from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Any, Dict, Optional, Protocol
from uuid import uuid4

from src.robinhood.client import RobinhoodAPIError, RobinhoodClient

DEFAULT_ASSET_QUANTITY_STEP = Decimal("0.00000001")
ASSET_QUANTITY_STEPS = {
    "DOGE-USD": Decimal("0.01"),
    "XLM-USD": Decimal("0.01"),
}


@dataclass(frozen=True)
class ExecutionResult:
    submitted: bool
    mode: str
    order_id: str = ""
    client_order_id: str = ""
    raw_response: Optional[Dict[str, Any]] = None


class OrderExecutor(Protocol):
    mode: str

    def submit_market_order(self, symbol: str, side: str, quantity: float) -> ExecutionResult:
        ...


class PaperOrderExecutor:
    mode = "SHADOW"

    def submit_market_order(self, symbol: str, side: str, quantity: float) -> ExecutionResult:
        del symbol, side, quantity
        return ExecutionResult(submitted=False, mode=self.mode)


class RobinhoodLiveOrderExecutor:
    mode = "LIVE"

    def __init__(self, client: RobinhoodClient) -> None:
        self.client = client

    def submit_market_order(self, symbol: str, side: str, quantity: float) -> ExecutionResult:
        client_order_id = str(uuid4())
        api_quantity = quantize_asset_quantity(symbol, quantity)
        try:
            response = self.client.place_market_order(
                symbol=symbol,
                side=side,
                asset_quantity=api_quantity,
                client_order_id=client_order_id,
            )
        except RobinhoodAPIError:
            raise
        order_id = _first_string(response, ("id", "order_id", "client_order_id"))
        return ExecutionResult(
            submitted=True,
            mode=self.mode,
            order_id=order_id,
            client_order_id=client_order_id,
            raw_response=response,
        )


def quantize_asset_quantity(symbol: str, quantity: float) -> float:
    step = ASSET_QUANTITY_STEPS.get(symbol.upper(), DEFAULT_ASSET_QUANTITY_STEP)
    decimal_quantity = Decimal(str(quantity))
    rounded_quantity = (decimal_quantity / step).to_integral_value(rounding=ROUND_DOWN) * step
    if rounded_quantity <= 0:
        raise ValueError("asset quantity is below Robinhood's minimum precision")
    return float(rounded_quantity)


def _first_string(payload: Dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return ""
