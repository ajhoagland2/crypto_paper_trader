from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, List

from src.backtest.models import (
    BacktestConfig,
    BacktestResult,
    HistoricalMarketDataPoint,
    WalkForwardResult,
)


def parse_datetime(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    normalized = _trim_fractional_seconds(normalized)
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _trim_fractional_seconds(value: str) -> str:
    for separator in ("+", "-"):
        if separator in value[10:]:
            head, tail = value.rsplit(separator, 1)
            sign = separator
            break
    else:
        head, tail, sign = value, "", ""
    if "." not in head:
        return value
    before, fraction = head.split(".", 1)
    if len(fraction) <= 6:
        return value
    return f"{before}.{fraction[:6]}{sign}{tail}"


def parse_points(rows: List[Dict[str, Any]]) -> List[HistoricalMarketDataPoint]:
    return [
        HistoricalMarketDataPoint(
            timestamp=parse_datetime(str(row["timestamp"])),
            price=float(row["price"]),
            volume=float(row.get("volume", 0.0) or 0.0),
        )
        for row in rows
    ]


def result_to_dict(result: BacktestResult) -> Dict[str, Any]:
    return {
        "config": {
            "symbol": result.config.symbol,
            "start": result.config.start.isoformat(),
            "end": result.config.end.isoformat(),
            "startingCash": result.config.starting_cash,
            "maxTradeSize": result.config.max_trade_size,
            "entryScore": result.config.entry_score,
            "exitScore": result.config.exit_score,
            "targetProfitPct": result.config.target_profit_pct,
            "cooldownSeconds": result.config.cooldown_seconds,
            "catastrophicLossPct": result.config.catastrophic_loss_pct,
            "entryIntervalSeconds": result.config.entry_interval_seconds,
            "maxOpenTrades": result.config.max_open_trades,
        },
        "metrics": result.metrics,
        "trades": [asdict(trade) for trade in result.trades],
        "rejectedReasons": result.rejected_reasons,
        "equityCurve": [asdict(point) for point in result.equity_curve],
        "priceSeries": result.price_series,
        "markers": [asdict(marker) for marker in result.markers],
        "warnings": result.warnings,
    }


def walk_forward_to_dict(result: WalkForwardResult) -> Dict[str, Any]:
    return {
        "enabled": result.enabled,
        "bestParameters": result.best_parameters,
        "trainingMetrics": result.training_metrics,
        "testMetrics": result.test_metrics,
        "degradation": result.degradation,
        "overfitWarning": result.overfit_warning,
        "passed": result.passed,
        "trainingResult": result_to_dict(result.training_result) if result.training_result else None,
        "testResult": result_to_dict(result.test_result) if result.test_result else None,
    }
