from src.trading.execution import RobinhoodLiveOrderExecutor, quantize_asset_quantity


class RecordingClient:
    def __init__(self) -> None:
        self.orders: list[dict] = []

    def place_market_order(
        self,
        symbol: str,
        side: str,
        asset_quantity: float,
        client_order_id: str,
    ) -> dict:
        self.orders.append(
            {
                "symbol": symbol,
                "side": side,
                "asset_quantity": asset_quantity,
                "client_order_id": client_order_id,
            }
        )
        return {"id": "order-123"}


def test_quantize_asset_quantity_uses_xlm_step() -> None:
    assert quantize_asset_quantity("XLM-USD", 25.33595476011918) == 25.33


def test_quantize_asset_quantity_uses_doge_step() -> None:
    assert quantize_asset_quantity("DOGE-USD", 128.59217416) == 128.59


def test_live_executor_submits_quantized_quantity() -> None:
    client = RecordingClient()
    executor = RobinhoodLiveOrderExecutor(client)  # type: ignore[arg-type]

    result = executor.submit_market_order("XLM-USD", "BUY", 25.33595476011918)

    assert result.submitted is True
    assert result.order_id == "order-123"
    assert client.orders[0]["asset_quantity"] == 25.33
