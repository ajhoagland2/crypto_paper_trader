from dataclasses import replace
from datetime import timedelta
from typing import Iterable, List

from src.backtest.backtestMetrics import calculate_max_drawdown
from src.backtest.models import (
    BacktestConfig,
    HistoricalMarketDataPoint,
    WalkForwardResult,
)
from src.backtest.parameterOptimizer import optimize_parameters
from src.backtest.backtestEngine import run_backtest

MAX_OPTIMIZATION_POINTS = 1200


def run_walk_forward(
    config: BacktestConfig,
    data: Iterable[HistoricalMarketDataPoint],
    training_ratio: float = 0.6,
) -> WalkForwardResult:
    points = sorted(data, key=lambda point: point.timestamp)
    if len(points) < 12:
        empty = run_backtest(config, points)
        return WalkForwardResult(
            enabled=True,
            best_parameters={},
            training_metrics=empty.metrics,
            test_metrics=empty.metrics,
            degradation={},
            overfit_warning="Not enough historical data for walk-forward testing.",
            passed=False,
            training_result=empty,
            test_result=empty,
        )

    split_index = max(1, min(len(points) - 1, int(len(points) * training_ratio)))
    training_points = points[:split_index]
    test_points = points[split_index:]

    training_config = replace(
        config,
        start=training_points[0].timestamp,
        end=training_points[-1].timestamp,
    )
    # Later refinement: replace this simple deterministic sample with a
    # time-bucketed optimizer once we store true OHLCV candles locally.
    optimization_points = _sample_for_optimization(training_points)
    optimized_sample = optimize_parameters(training_config, optimization_points)
    optimized_training = run_backtest(optimized_sample.config, training_points)

    test_config = replace(
        optimized_training.config,
        start=test_points[0].timestamp,
        end=test_points[-1].timestamp,
    )
    test_result = run_backtest(test_config, test_points)
    degradation = _degradation(optimized_training.metrics, test_result.metrics)
    warning = _overfit_warning(optimized_training.metrics, test_result.metrics)
    return WalkForwardResult(
        enabled=True,
        best_parameters={
            "entryScore": optimized_sample.config.entry_score,
            "exitScore": optimized_sample.config.exit_score,
            "targetProfitPct": optimized_sample.config.target_profit_pct,
            "cooldownSeconds": optimized_sample.config.cooldown_seconds,
            "entryIntervalSeconds": optimized_sample.config.entry_interval_seconds,
            "maxOpenTrades": optimized_sample.config.max_open_trades,
            "optimizationPoints": len(optimization_points),
            "trainingPoints": len(training_points),
        },
        training_metrics=optimized_training.metrics,
        test_metrics=test_result.metrics,
        degradation=degradation,
        overfit_warning=warning,
        passed=warning == "",
        training_result=optimized_training,
        test_result=test_result,
    )


def _sample_for_optimization(
    points: List[HistoricalMarketDataPoint],
) -> List[HistoricalMarketDataPoint]:
    if len(points) <= MAX_OPTIMIZATION_POINTS:
        return points
    step = max(1, len(points) // MAX_OPTIMIZATION_POINTS)
    sampled = points[::step]
    if sampled[-1] != points[-1]:
        sampled.append(points[-1])
    return sampled


def _degradation(training: dict, test: dict) -> dict:
    return {
        "totalReturnDelta": float(test.get("totalReturn", 0.0)) - float(training.get("totalReturn", 0.0)),
        "maxDrawdownDelta": float(test.get("maxDrawdown", 0.0)) - float(training.get("maxDrawdown", 0.0)),
        "winRateDelta": float(test.get("winRate", 0.0)) - float(training.get("winRate", 0.0)),
    }


def _overfit_warning(training: dict, test: dict) -> str:
    reasons: List[str] = []
    training_return = float(training.get("totalReturn", 0.0))
    test_return = float(test.get("totalReturn", 0.0))
    training_drawdown = abs(float(training.get("maxDrawdown", 0.0)))
    test_drawdown = abs(float(test.get("maxDrawdown", 0.0)))
    training_win_rate = float(training.get("winRate", 0.0))
    test_win_rate = float(test.get("winRate", 0.0))
    test_trades = int(test.get("numberOfTrades", 0))

    if training_return > 0 and test_return < 0:
        reasons.append("total return turns negative in test")
    if training_return > 0 and test_return < training_return * 0.35:
        reasons.append("test return degrades sharply")
    if test_drawdown > max(training_drawdown * 1.75, training_drawdown + 2):
        reasons.append("drawdown increases significantly in test")
    if training_win_rate >= 50 and test_win_rate < training_win_rate * 0.6:
        reasons.append("win rate collapses in test")
    if test_trades < 3:
        reasons.append("number of trades is too low to be statistically useful")

    if reasons:
        return (
            "High training performance with weak unseen-period performance may indicate curve-fitting. "
            + "; ".join(reasons)
            + "."
        )
    return ""
