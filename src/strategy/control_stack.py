from dataclasses import dataclass


@dataclass(frozen=True)
class ControlStack:
    """User-tunable paper trading parameters."""

    lookback_window: int = 4
    buy_dip_percent: float = 4.0
    rebound_percent: float = 1.0
    sell_above_dip_percent: float = 8.0
    stop_loss_percent: float = 6.0
    trailing_stop_percent: float = 5.0

    def validate(self) -> None:
        if self.lookback_window < 2:
            raise ValueError("lookback_window must be at least 2")
        for name, value in (
            ("buy_dip_percent", self.buy_dip_percent),
            ("rebound_percent", self.rebound_percent),
            ("sell_above_dip_percent", self.sell_above_dip_percent),
            ("stop_loss_percent", self.stop_loss_percent),
            ("trailing_stop_percent", self.trailing_stop_percent),
        ):
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
