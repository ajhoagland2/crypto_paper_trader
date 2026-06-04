from typing import Any, Dict

from src.robinhood.client import RobinhoodClient


class MarketDataService:
    def __init__(self, client: RobinhoodClient) -> None:
        self.client = client

    def quote(self, symbol: str) -> Dict[str, Any]:
        return self.client.get_market_quote(symbol)
