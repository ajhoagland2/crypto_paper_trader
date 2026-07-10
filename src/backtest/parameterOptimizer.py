from dataclasses import replace
from typing import Iterable, List

from src.backtest.backtestEngine import run_backtest
from src.backtest.models import BacktestConfig, BacktestResult, HistoricalMarketDataPoint


def optimize_parameters(
    base_config: BacktestConfig,
    data: Iterable[HistoricalMarketDataPoint],
) -> BacktestResult:
    points = list(data)
    candidates: List[BacktestResult] = []
    entry_scores = _nearby_values(base_config.entry_score, [base_config.entry_score - 10, base_config.entry_score, base_config.entry_score + 10], 0, 100)
    targets = _nearby_values(base_config.target_profit_pct, [base_config.target_profit_pct / 2, base_config.target_profit_pct, base_config.target_profit_pct * 1.5], 0.01, 20)
    cooldowns = sorted({0, base_config.cooldown_seconds, max(0, base_config.cooldown_seconds * 2)})

    for entry in entry_scores:
        for target in targets:
            for cooldown in cooldowns:
                result = run_backtest(
                    replace(
                        base_config,
                        entry_score=entry,
                        target_profit_pct=target,
                        cooldown_seconds=int(cooldown),
                    ),
                    points,
                )
                candidates.append(result)

    return max(candidates, key=_objective_score) if candidates else run_backtest(base_config, points)


def best_parameter_summary(result: BacktestResult) -> dict:
    return {
        "entryScore": result.config.entry_score,
        "exitScore": result.config.exit_score,
        "targetProfitPct": result.config.target_profit_pct,
        "cooldownSeconds": result.config.cooldown_seconds,
        "entryIntervalSeconds": result.config.entry_interval_seconds,
        "maxOpenTrades": result.config.max_open_trades,
    }


def _objective_score(result: BacktestResult) -> float:
    total_return = float(result.metrics.get("totalReturn", 0.0))
    drawdown = abs(float(result.metrics.get("maxDrawdown", 0.0)))
    trades = int(result.metrics.get("numberOfTrades", 0))
    trade_penalty = 3.0 if trades < 3 else 0.0
    return total_return - drawdown * 0.5 - trade_penalty


def _nearby_values(base: float, values: List[float], minimum: float, maximum: float) -> List[float]:
    cleaned = {round(max(minimum, min(maximum, value)), 4) for value in values}
    cleaned.add(round(max(minimum, min(maximum, base)), 4))
    return sorted(cleaned)
