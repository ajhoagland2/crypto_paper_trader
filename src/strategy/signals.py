from enum import Enum
from typing import Sequence

from src.strategy.indicators import simple_moving_average


class Signal(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


def moving_average_crossover_signal(
    prices: Sequence[float], short_period: int = 3, long_period: int = 5
) -> Signal:
    if short_period >= long_period:
        raise ValueError("short_period must be less than long_period")
    if len(prices) < long_period + 1:
        return Signal.HOLD

    previous_prices = prices[:-1]
    previous_short = simple_moving_average(previous_prices, short_period)
    previous_long = simple_moving_average(previous_prices, long_period)
    current_short = simple_moving_average(prices, short_period)
    current_long = simple_moving_average(prices, long_period)

    if None in (previous_short, previous_long, current_short, current_long):
        return Signal.HOLD
    if previous_short <= previous_long and current_short > current_long:
        return Signal.BUY
    if previous_short >= previous_long and current_short < current_long:
        return Signal.SELL
    return Signal.HOLD


def price_action_live_signal(
    prices: Sequence[float],
    index: int,
    has_position: bool,
    entry_price: float = 0.0,
    highest_since_entry: float = 0.0,
    lookback_window: int = 4,
    buy_dip_percent: float = 4.0,
    rebound_percent: float = 1.0,
    profit_target_percent: float = 8.0,
    stop_loss_percent: float = 6.0,
    trailing_stop_percent: float = 5.0,
) -> Signal:
    """Live-style paper signal that only uses current and prior prices."""
    if index < 0 or index >= len(prices):
        raise IndexError("index is outside the price series")

    price = prices[index]
    previous = prices[index - 1] if index > 0 else None
    prior_index = max(0, index - 1)
    prior_window_start = max(0, prior_index - lookback_window + 1)
    prior_window = prices[prior_window_start : prior_index + 1]
    current_window_start = max(0, index - lookback_window + 1)
    current_window = prices[current_window_start : index + 1]
    recent_low = min(prior_window)
    recent_high = max(prior_window)
    current_high = max(current_window)

    if not has_position:
        had_tradable_dip = (
            recent_high > 0 and _percent_move(recent_high, recent_low) <= -buy_dip_percent
        )
        rebounded_from_low = recent_low > 0 and _percent_move(recent_low, price) >= rebound_percent
        momentum_confirmed = previous is not None and price >= previous
        prior_tick_was_low = previous is not None and previous <= recent_low * 1.002
        flat_base_breakout = prior_tick_was_low and rebounded_from_low and momentum_confirmed

        if (had_tradable_dip and rebounded_from_low and momentum_confirmed) or flat_base_breakout:
            return Signal.BUY
        return Signal.HOLD

    peak = max(highest_since_entry, price)
    gain = _percent_move(entry_price, price)
    drawdown_from_peak = _percent_move(peak, price)
    momentum_rolled_over = previous is not None and price < previous
    extended_near_high = price >= current_high * 0.998

    if gain <= -stop_loss_percent:
        return Signal.SELL
    if gain >= profit_target_percent:
        return Signal.SELL
    if gain > 0 and drawdown_from_peak <= -trailing_stop_percent:
        return Signal.SELL
    if gain > 0 and extended_near_high and momentum_rolled_over:
        return Signal.SELL
    if index == len(prices) - 1 and gain != 0:
        return Signal.SELL
    return Signal.HOLD


def _percent_move(from_price: float, to_price: float) -> float:
    if from_price == 0:
        return 0.0
    return ((to_price - from_price) / from_price) * 100
