from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


@dataclass(frozen=True)
class HistoricalMarketDataPoint:
    timestamp: datetime
    price: float
    volume: float = 0.0


@dataclass(frozen=True)
class BacktestConfig:
    symbol: str
    start: datetime
    end: datetime
    starting_cash: float
    max_trade_size: float
    entry_score: float
    exit_score: float
    target_profit_pct: float
    cooldown_seconds: int
    catastrophic_loss_pct: float
    entry_interval_seconds: int = 300
    max_open_trades: int = 1


@dataclass
class BacktestTrade:
    trade_id: str
    symbol: str
    entry_timestamp: str
    entry_price: float
    entry_score: float
    exit_timestamp: str = ""
    exit_price: float = 0.0
    exit_score: float = 0.0
    trade_size: float = 0.0
    realized_pl: float = 0.0
    return_pct: float = 0.0
    exit_reason: str = ""
    duration_seconds: float = 0.0
    status: str = "OPEN"


@dataclass(frozen=True)
class BacktestMarker:
    type: str
    timestamp: str
    price: float
    score: float
    trade_size: float = 0.0
    realized_pl: float = 0.0
    exit_reason: str = ""


@dataclass(frozen=True)
class EquityPoint:
    timestamp: str
    value: float


@dataclass
class BacktestResult:
    config: BacktestConfig
    metrics: Dict[str, object]
    trades: List[BacktestTrade]
    rejected_reasons: Dict[str, int]
    equity_curve: List[EquityPoint]
    price_series: List[Dict[str, object]]
    markers: List[BacktestMarker]
    warnings: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class WalkForwardResult:
    enabled: bool
    best_parameters: Dict[str, float]
    training_metrics: Dict[str, object]
    test_metrics: Dict[str, object]
    degradation: Dict[str, float]
    overfit_warning: str
    passed: bool
    training_result: Optional[BacktestResult] = None
    test_result: Optional[BacktestResult] = None
