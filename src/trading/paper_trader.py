from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional
from uuid import uuid4


@dataclass
class Position:
    symbol: str
    quantity: float = 0.0
    average_price: float = 0.0


@dataclass
class Trade:
    trade_id: str
    symbol: str
    side: str
    quantity: float
    price: float
    notional: float
    realized_pl: float
    timestamp: str


@dataclass
class PaperTrader:
    starting_cash: float = 10_000.0
    gain_reserve_percent: float = 15.0
    cash: float = field(init=False)
    gain_reserve: float = 0.0
    positions: Dict[str, Position] = field(default_factory=dict)
    trade_history: List[Trade] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.cash = self.starting_cash

    def buy(self, symbol: str, quantity: float, price: float) -> Trade:
        self._validate_order(quantity, price)
        notional = quantity * price
        if notional > self.available_cash:
            raise ValueError("insufficient paper cash")

        position = self.positions.get(symbol, Position(symbol=symbol))
        total_cost = (position.quantity * position.average_price) + notional
        position.quantity += quantity
        position.average_price = total_cost / position.quantity
        self.positions[symbol] = position
        self.cash -= notional
        return self._record_trade(symbol, "BUY", quantity, price, realized_pl=0.0)

    def sell(self, symbol: str, quantity: float, price: float) -> Trade:
        self._validate_order(quantity, price)
        position = self.positions.get(symbol)
        if position is None or quantity > position.quantity:
            raise ValueError("insufficient paper position")

        realized_pl = (price - position.average_price) * quantity
        self.reserve_from_gain(realized_pl)
        position.quantity -= quantity
        self.cash += quantity * price
        if position.quantity == 0:
            del self.positions[symbol]
        return self._record_trade(symbol, "SELL", quantity, price, realized_pl=realized_pl)

    def portfolio_value(self, latest_prices: Optional[Dict[str, float]] = None) -> float:
        latest_prices = latest_prices or {}
        value = self.cash
        for symbol, position in self.positions.items():
            value += position.quantity * latest_prices.get(symbol, position.average_price)
        return value

    def unrealized_pl(self, latest_prices: Dict[str, float]) -> float:
        total = 0.0
        for symbol, position in self.positions.items():
            mark = latest_prices.get(symbol, position.average_price)
            total += (mark - position.average_price) * position.quantity
        return total

    def realized_pl(self) -> float:
        return sum(trade.realized_pl for trade in self.trade_history)

    @property
    def available_cash(self) -> float:
        return self.cash - self.gain_reserve

    def reserve_from_gain(self, realized_pl: float) -> float:
        if realized_pl <= 0:
            return 0.0
        reserved_gain = realized_pl * (self.gain_reserve_percent / 100)
        self.gain_reserve += reserved_gain
        return reserved_gain

    def _record_trade(
        self, symbol: str, side: str, quantity: float, price: float, realized_pl: float
    ) -> Trade:
        trade = Trade(
            trade_id=str(uuid4()),
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            notional=quantity * price,
            realized_pl=realized_pl,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self.trade_history.append(trade)
        return trade

    @staticmethod
    def _validate_order(quantity: float, price: float) -> None:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        if price <= 0:
            raise ValueError("price must be positive")
