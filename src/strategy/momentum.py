from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import sqrt
from typing import List, Optional, Sequence


@dataclass(frozen=True)
class MarketDataPoint:
    timestamp: datetime
    price: float


@dataclass(frozen=True)
class MomentumWeights:
    trend: float = 0.25
    acceleration: float = 0.25
    z_score: float = 0.25
    moving_average_slope: float = 0.15
    volatility_stability: float = 0.10

    def normalized(self) -> "MomentumWeights":
        total = (
            self.trend
            + self.acceleration
            + self.z_score
            + self.moving_average_slope
            + self.volatility_stability
        )
        if total <= 0:
            return MomentumWeights()
        return MomentumWeights(
            trend=self.trend / total,
            acceleration=self.acceleration / total,
            z_score=self.z_score / total,
            moving_average_slope=self.moving_average_slope / total,
            volatility_stability=self.volatility_stability / total,
        )


@dataclass(frozen=True)
class MomentumConfig:
    lookback_minutes: float = 5.0
    interval_seconds: int = 30
    entry_threshold: float = 80.0
    exit_threshold: float = 40.0
    entry_interval_seconds: int = 300
    max_open_trades: int = 3
    target_profit_pct: float = 0.25
    allocation_per_trade: float = 100.0
    catastrophic_loss_pct: float = -3.0
    soft_stop_loss_pct: float = -1.0
    catastrophic_loss_enabled: bool = True
    pyramiding_enabled: bool = False
    weights: MomentumWeights = MomentumWeights()


@dataclass(frozen=True)
class MomentumIntervalStats:
    interval_returns: List[float]
    mean_return: float
    standard_deviation: float
    current_return: float
    z_score: float


@dataclass(frozen=True)
class MomentumScore:
    score: float
    trend_return: float
    acceleration: float
    moving_average_slope: float
    volatility_stability: float
    stats: MomentumIntervalStats


class RollingMarketDataBuffer:
    def __init__(self, lookback_seconds: float) -> None:
        self.lookback = timedelta(seconds=lookback_seconds)
        self.points: List[MarketDataPoint] = []

    def add(self, timestamp: datetime, price: float) -> None:
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        self.points.append(MarketDataPoint(timestamp=timestamp, price=price))
        self.points.sort(key=lambda point: point.timestamp)
        cutoff = timestamp - self.lookback
        self.points = [point for point in self.points if point.timestamp >= cutoff]

    @property
    def latest(self) -> Optional[MarketDataPoint]:
        return self.points[-1] if self.points else None

    def interval_returns(self, interval_seconds: int) -> List[float]:
        if len(self.points) < 2:
            return []
        latest = self.points[-1].timestamp
        earliest = max(self.points[0].timestamp, latest - self.lookback)
        boundaries: List[datetime] = []
        cursor = earliest
        while cursor <= latest:
            boundaries.append(cursor)
            cursor += timedelta(seconds=interval_seconds)
        if not boundaries or boundaries[-1] < latest:
            boundaries.append(latest)

        returns: List[float] = []
        for start, end in zip(boundaries, boundaries[1:]):
            start_point = self._point_at_or_after(start)
            end_point = self._point_at_or_before(end)
            if start_point and end_point and end_point.timestamp > start_point.timestamp:
                returns.append(_percent_return(start_point.price, end_point.price))
        return returns

    def trend_return(self) -> float:
        if len(self.points) < 2:
            return 0.0
        return _percent_return(self.points[0].price, self.points[-1].price)

    def moving_average_slope(self) -> float:
        if len(self.points) < 4:
            return 0.0
        midpoint = len(self.points) // 2
        first_avg = _mean([point.price for point in self.points[:midpoint]])
        second_avg = _mean([point.price for point in self.points[midpoint:]])
        return _percent_return(first_avg, second_avg)

    def _point_at_or_after(self, timestamp: datetime) -> Optional[MarketDataPoint]:
        for point in self.points:
            if point.timestamp >= timestamp:
                return point
        return None

    def _point_at_or_before(self, timestamp: datetime) -> Optional[MarketDataPoint]:
        selected: Optional[MarketDataPoint] = None
        for point in self.points:
            if point.timestamp <= timestamp:
                selected = point
            else:
                break
        return selected


class MomentumStrategy:
    def __init__(self, config: MomentumConfig) -> None:
        self.config = config

    def calculate_stats(self, buffer: RollingMarketDataBuffer) -> MomentumIntervalStats:
        returns = buffer.interval_returns(self.config.interval_seconds)
        mean_return = _mean(returns)
        standard_deviation = _standard_deviation(returns)
        current_return = returns[-1] if returns else 0.0
        z_score = 0.0
        if standard_deviation > 1e-9:
            z_score = (current_return - mean_return) / standard_deviation
        return MomentumIntervalStats(
            interval_returns=returns,
            mean_return=mean_return,
            standard_deviation=standard_deviation,
            current_return=current_return,
            z_score=z_score,
        )

    def score(self, buffer: RollingMarketDataBuffer) -> MomentumScore:
        stats = self.calculate_stats(buffer)
        trend_return = buffer.trend_return()
        acceleration = 0.0
        if len(stats.interval_returns) >= 2:
            acceleration = stats.interval_returns[-1] - stats.interval_returns[-2]
        moving_average_slope = buffer.moving_average_slope()
        volatility_stability = _clamp(1 - (stats.standard_deviation / 2), 0, 1)
        weights = self.config.weights.normalized()

        trend_component = _component_from_signed_value(trend_return, scale=1.0)
        acceleration_component = _component_from_signed_value(acceleration, scale=0.5)
        z_component = _component_from_signed_value(stats.z_score, scale=2.0)
        slope_component = _component_from_signed_value(moving_average_slope, scale=1.0)

        score = 100 * (
            trend_component * weights.trend
            + acceleration_component * weights.acceleration
            + z_component * weights.z_score
            + slope_component * weights.moving_average_slope
            + volatility_stability * weights.volatility_stability
        )
        return MomentumScore(
            score=_clamp(score, 0, 100),
            trend_return=trend_return,
            acceleration=acceleration,
            moving_average_slope=moving_average_slope,
            volatility_stability=volatility_stability,
            stats=stats,
        )

    def should_enter(self, momentum: MomentumScore, has_position: bool) -> bool:
        if has_position and not self.config.pyramiding_enabled:
            return False
        return momentum.score >= self.config.entry_threshold

    def exit_reason(
        self,
        momentum: MomentumScore,
        entry_price: float,
        current_price: float,
        target_exit_price: float,
    ) -> Optional[str]:
        del momentum
        trade_return = _percent_return(entry_price, current_price)
        if current_price >= target_exit_price:
            return "TARGET_PROFIT"
        if self.config.catastrophic_loss_enabled and trade_return <= self.config.catastrophic_loss_pct:
            return "CATASTROPHIC_LOSS"
        return None


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _standard_deviation(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = _mean(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return sqrt(variance)


def _percent_return(start: float, end: float) -> float:
    if start == 0:
        return 0.0
    return ((end - start) / start) * 100


def _component_from_signed_value(value: float, scale: float) -> float:
    if scale <= 0:
        return 0.5
    return _clamp(0.5 + (value / (2 * scale)), 0, 1)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))
