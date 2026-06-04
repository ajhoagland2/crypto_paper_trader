from datetime import datetime, timedelta, timezone

from src.risk.risk_manager import RiskManager


def test_rejects_trade_above_max_size() -> None:
    risk = RiskManager(100, 500, 10, 60)

    decision = risk.evaluate_trade("BUY", "BTC-USD", quantity=2, price=100)

    assert not decision.approved
    assert "trade size" in decision.reason


def test_rejects_during_loss_cooldown() -> None:
    risk = RiskManager(1_000, 500, 10, 60)
    now = datetime.now(timezone.utc)
    risk.last_loss_at = now - timedelta(seconds=10)

    decision = risk.evaluate_trade("BUY", "BTC-USD", quantity=1, price=100, now=now)

    assert not decision.approved
    assert "cooldown" in decision.reason


def test_kill_switch_rejects_everything() -> None:
    risk = RiskManager(1_000, 500, 10, 60, kill_switch=True)

    decision = risk.evaluate_trade("BUY", "BTC-USD", quantity=1, price=100)

    assert not decision.approved
    assert "kill switch" in decision.reason
