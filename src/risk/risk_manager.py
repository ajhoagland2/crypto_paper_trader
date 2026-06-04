from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reason: str = ""


@dataclass
class RiskManager:
    max_trade_size: float
    max_daily_loss: float
    max_trades_per_day: int
    cooldown_after_loss_seconds: int
    kill_switch: bool = False
    daily_realized_pl: float = 0.0
    trades_today: int = 0
    last_loss_at: Optional[datetime] = None

    def evaluate_trade(
        self,
        side: str,
        symbol: str,
        quantity: float,
        price: float,
        now: Optional[datetime] = None,
    ) -> RiskDecision:
        del symbol
        now = now or datetime.now(timezone.utc)
        notional = quantity * price
        is_entry = side.upper() == "BUY"

        if self.kill_switch:
            return RiskDecision(False, "global kill switch is enabled")
        if is_entry and notional > self.max_trade_size:
            return RiskDecision(False, "trade size exceeds max_trade_size")
        if is_entry and self.daily_realized_pl <= -abs(self.max_daily_loss):
            return RiskDecision(False, "max daily loss reached")
        if is_entry and self.trades_today >= self.max_trades_per_day:
            return RiskDecision(False, "max trades per day reached")
        if is_entry and self.last_loss_at:
            cooldown_until = self.last_loss_at + timedelta(seconds=self.cooldown_after_loss_seconds)
            if now < cooldown_until:
                return RiskDecision(False, "cooldown after loss is active")
        return RiskDecision(True)

    def record_trade_result(self, realized_pl: float) -> None:
        self.trades_today += 1
        self.daily_realized_pl += realized_pl
        if realized_pl < 0:
            self.last_loss_at = datetime.now(timezone.utc)
