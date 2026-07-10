from datetime import datetime
from typing import Dict, List, Optional
from uuid import uuid4

from src.backtest.models import BacktestTrade


class BacktestTradeSimulator:
    def __init__(self, starting_cash: float) -> None:
        self.starting_cash = starting_cash
        self.cash = starting_cash
        self.open_trades: List[BacktestTrade] = []

    @property
    def open_trade(self) -> Optional[BacktestTrade]:
        return self.open_trades[0] if self.open_trades else None

    def can_buy(self, notional: float, max_open_trades: int) -> Optional[str]:
        if notional <= 0:
            return "missing or invalid market data"
        if len(self.open_trades) >= max_open_trades:
            return "max concurrent trades reached"
        if self.cash < notional:
            return "insufficient cash"
        return None

    def buy(
        self,
        symbol: str,
        timestamp: datetime,
        price: float,
        score: float,
        notional: float,
    ) -> BacktestTrade:
        quantity = notional / price
        self.cash -= notional
        trade = BacktestTrade(
            trade_id=str(uuid4()),
            symbol=symbol,
            entry_timestamp=timestamp.isoformat(),
            entry_price=price,
            entry_score=score,
            trade_size=notional,
        )
        setattr(trade, "_quantity", quantity)
        self.open_trades.append(trade)
        return trade

    def sell(
        self,
        trade: BacktestTrade,
        timestamp: datetime,
        price: float,
        score: float,
        reason: str,
    ) -> BacktestTrade:
        if trade not in self.open_trades:
            raise ValueError("no open backtest position")
        quantity = float(getattr(trade, "_quantity"))
        proceeds = quantity * price
        realized_pl = proceeds - trade.trade_size
        self.cash += proceeds
        trade.exit_timestamp = timestamp.isoformat()
        trade.exit_price = price
        trade.exit_score = score
        trade.realized_pl = realized_pl
        trade.return_pct = _percent_return(trade.entry_price, price)
        trade.exit_reason = reason
        trade.duration_seconds = (
            datetime.fromisoformat(trade.exit_timestamp)
            - datetime.fromisoformat(trade.entry_timestamp)
        ).total_seconds()
        trade.status = "CLOSED"
        self.open_trades.remove(trade)
        return trade

    def equity(self, latest_prices: Dict[str, float]) -> float:
        value = self.cash
        for trade in self.open_trades:
            quantity = float(getattr(trade, "_quantity"))
            price = latest_prices.get(trade.symbol, trade.entry_price)
            value += quantity * price
        return value


def _percent_return(start: float, end: float) -> float:
    if start <= 0:
        return 0.0
    return ((end - start) / start) * 100
