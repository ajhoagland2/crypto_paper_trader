from typing import List, Optional, Sequence


def simple_moving_average(values: Sequence[float], period: int) -> Optional[float]:
    if period <= 0:
        raise ValueError("period must be positive")
    if len(values) < period:
        return None
    window = values[-period:]
    return sum(window) / period


def rolling_sma(values: Sequence[float], period: int) -> List[Optional[float]]:
    return [simple_moving_average(values[: index + 1], period) for index in range(len(values))]
