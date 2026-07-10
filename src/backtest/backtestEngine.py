from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, Iterable, List

from src.backtest.backtestMetrics import summarize_trades
from src.backtest.models import (
    BacktestConfig,
    BacktestMarker,
    BacktestResult,
    EquityPoint,
    HistoricalMarketDataPoint,
)
from src.backtest.tradeSimulator import BacktestTradeSimulator
from src.strategy.momentum import MomentumConfig, MomentumStrategy, RollingMarketDataBuffer


MIN_BACKTEST_POINTS = 6


def run_backtest(
    config: BacktestConfig,
    data: Iterable[HistoricalMarketDataPoint],
) -> BacktestResult:
    points = sorted(data, key=lambda point: point.timestamp)
    rejected: Dict[str, int] = defaultdict(int)
    warnings: List[str] = []
    equity_curve: List[EquityPoint] = []
    price_series: List[Dict[str, object]] = []
    markers: List[BacktestMarker] = []
    trades = []

    if len(points) < MIN_BACKTEST_POINTS:
        rejected["missing or invalid market data"] += 1
        warnings.append("Not enough historical market data for a reliable backtest.")
        return _empty_result(config, rejected, warnings)

    strategy_config = MomentumConfig(
        entry_threshold=config.entry_score,
        exit_threshold=config.exit_score,
        entry_interval_seconds=config.entry_interval_seconds,
        max_open_trades=config.max_open_trades,
        target_profit_pct=config.target_profit_pct,
        allocation_per_trade=config.max_trade_size,
        catastrophic_loss_pct=config.catastrophic_loss_pct,
    )
    strategy = MomentumStrategy(strategy_config)
    buffer = RollingMarketDataBuffer(strategy_config.lookback_minutes * 60)
    simulator = BacktestTradeSimulator(config.starting_cash)
    cooldown_until = None
    last_entry_at = None

    for point in points:
        if point.price <= 0:
            rejected["missing or invalid market data"] += 1
            continue

        buffer.add(point.timestamp, point.price)
        momentum = strategy.score(buffer)
        price_series.append(
            {
                "timestamp": point.timestamp.isoformat(),
                "price": point.price,
                "volume": point.volume,
                "score": momentum.score,
            }
        )

        for open_trade in list(simulator.open_trades):
            target = open_trade.entry_price * (1 + config.target_profit_pct / 100)
            reason = strategy.exit_reason(
                momentum,
                open_trade.entry_price,
                point.price,
                target,
            )
            if reason:
                closed = simulator.sell(
                    open_trade,
                    point.timestamp,
                    point.price,
                    momentum.score,
                    reason,
                )
                trades.append(closed)
                cooldown_until = point.timestamp + timedelta(seconds=config.cooldown_seconds)
                markers.append(
                    BacktestMarker(
                        type="SELL",
                        timestamp=point.timestamp.isoformat(),
                        price=point.price,
                        score=momentum.score,
                        realized_pl=closed.realized_pl,
                        exit_reason=reason,
                    )
                )

        if cooldown_until and point.timestamp < cooldown_until:
            rejected["cooldown active"] += 1
        elif momentum.score < config.entry_score:
            rejected["score below entry threshold"] += 1
        elif len(simulator.open_trades) >= config.max_open_trades:
            rejected["max concurrent trades reached"] += 1
        elif (
            last_entry_at
            and (point.timestamp - last_entry_at).total_seconds()
            < config.entry_interval_seconds
        ):
            rejected["entry interval active"] += 1
        elif config.max_trade_size <= 0:
            rejected["trade exceeds max size"] += 1
        elif simulator.cash < config.max_trade_size:
            rejected["insufficient cash"] += 1
        else:
            reason = simulator.can_buy(config.max_trade_size, config.max_open_trades)
            if reason:
                rejected[reason] += 1
            else:
                trade = simulator.buy(
                    config.symbol,
                    point.timestamp,
                    point.price,
                    momentum.score,
                    config.max_trade_size,
                )
                last_entry_at = point.timestamp
                markers.append(
                    BacktestMarker(
                        type="BUY",
                        timestamp=point.timestamp.isoformat(),
                        price=point.price,
                        score=momentum.score,
                        trade_size=trade.trade_size,
                    )
                )

        equity_curve.append(
            EquityPoint(
                timestamp=point.timestamp.isoformat(),
                value=simulator.equity({config.symbol: point.price}),
            )
        )

    trades.extend(simulator.open_trades)

    final_equity = equity_curve[-1].value if equity_curve else config.starting_cash
    metrics = summarize_trades(config.starting_cash, final_equity, trades, equity_curve)
    metrics["rejectedTradesByReason"] = dict(rejected)
    return BacktestResult(
        config=config,
        metrics=metrics,
        trades=trades,
        rejected_reasons=dict(rejected),
        equity_curve=equity_curve,
        price_series=price_series,
        markers=markers,
        warnings=warnings,
    )


def _empty_result(
    config: BacktestConfig,
    rejected: Dict[str, int],
    warnings: List[str],
) -> BacktestResult:
    metrics = summarize_trades(
        config.starting_cash,
        config.starting_cash,
        [],
        [EquityPoint(timestamp=config.start.isoformat(), value=config.starting_cash)],
    )
    metrics["rejectedTradesByReason"] = dict(rejected)
    return BacktestResult(
        config=config,
        metrics=metrics,
        trades=[],
        rejected_reasons=dict(rejected),
        equity_curve=[EquityPoint(timestamp=config.start.isoformat(), value=config.starting_cash)],
        price_series=[],
        markers=[],
        warnings=warnings,
    )
