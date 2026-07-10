from datetime import datetime, timedelta, timezone

from src.data.database import ResearchDatabase
from src.strategy.momentum import (
    MomentumConfig,
    MomentumIntervalStats,
    MomentumScore,
    MomentumStrategy,
    RollingMarketDataBuffer,
)


def _buffer(prices: list[float], spacing_seconds: int = 30) -> RollingMarketDataBuffer:
    buffer = RollingMarketDataBuffer(lookback_seconds=300)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for index, price in enumerate(prices):
        buffer.add(start + timedelta(seconds=index * spacing_seconds), price)
    return buffer


def test_rolling_buffer_keeps_five_minutes() -> None:
    buffer = RollingMarketDataBuffer(lookback_seconds=300)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    for index in range(8):
        buffer.add(start + timedelta(minutes=index), 100 + index)

    assert buffer.points[0].timestamp == start + timedelta(minutes=2)
    assert buffer.points[-1].price == 107


def test_interval_returns_mean_stddev_and_z_score() -> None:
    strategy = MomentumStrategy(MomentumConfig(interval_seconds=30))
    stats = strategy.calculate_stats(_buffer([100, 101, 103, 104]))

    assert len(stats.interval_returns) == 3
    assert stats.mean_return > 0
    assert stats.standard_deviation > 0
    assert stats.current_return > 0
    assert isinstance(stats.z_score, float)


def test_z_score_is_safe_when_standard_deviation_is_zero() -> None:
    strategy = MomentumStrategy(MomentumConfig(interval_seconds=30))
    stats = strategy.calculate_stats(_buffer([100, 100, 100, 100]))

    assert stats.standard_deviation == 0
    assert stats.z_score == 0


def test_confidence_score_and_entry_threshold_logic() -> None:
    strategy = MomentumStrategy(MomentumConfig(interval_seconds=30, entry_threshold=55))
    score = strategy.score(_buffer([100, 101, 102, 104]))

    assert 0 <= score.score <= 100
    assert strategy.should_enter(score, has_position=False)
    assert not strategy.should_enter(score, has_position=True)


def test_entry_gate_relies_on_score_not_extra_momentum_filters() -> None:
    strategy = MomentumStrategy(MomentumConfig(entry_threshold=60))
    score = MomentumScore(
        score=80,
        trend_return=-0.5,
        acceleration=-0.2,
        moving_average_slope=-0.1,
        volatility_stability=0.9,
        stats=MomentumIntervalStats(
            interval_returns=[-0.2, -0.1],
            mean_return=-0.15,
            standard_deviation=0.05,
            current_return=-0.1,
            z_score=-1.0,
        ),
    )

    assert strategy.should_enter(score, has_position=False)


def test_target_exit_and_hold_behavior_after_interval_expires() -> None:
    strategy = MomentumStrategy(MomentumConfig(interval_seconds=30, target_profit_pct=0.25))
    score = strategy.score(_buffer([100, 100.1, 100.15, 100.2]))

    assert strategy.exit_reason(score, 100, 100.1, 100.25) is None
    assert strategy.exit_reason(score, 100, 100.26, 100.25) == "TARGET_PROFIT"


def test_exit_logic_ignores_weak_momentum_and_keeps_catastrophic_loss() -> None:
    strategy = MomentumStrategy(
        MomentumConfig(interval_seconds=30, exit_threshold=90, catastrophic_loss_pct=-3)
    )
    score = strategy.score(_buffer([100, 99.5, 99.0, 98.8]))

    assert strategy.exit_reason(score, 100, 99, 101) is None
    assert strategy.exit_reason(score, 100, 96.5, 101) == "CATASTROPHIC_LOSS"


def test_simulation_history_persistence(tmp_path) -> None:
    database = ResearchDatabase(str(tmp_path / "history.sqlite3"))
    database.log_simulation_summary("run-1", {"final_value": 101})

    summaries = database.simulation_summaries()
    database.close()

    assert summaries[0]["simulation_run_id"] == "run-1"
    assert summaries[0]["payload"]["final_value"] == 101


def test_rejected_trade_logging(tmp_path) -> None:
    database = ResearchDatabase(str(tmp_path / "history.sqlite3"))
    database.log_rejected_trade("BTC-USD", {"reason": "risk rule triggered"})

    events = database.recent_events()
    database.close()

    assert events[0]["event_type"] == "rejected_trade"
    assert events[0]["payload"]["reason"] == "risk rule triggered"
