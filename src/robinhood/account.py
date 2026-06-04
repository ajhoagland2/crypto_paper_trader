from typing import Any, Dict, Iterable, Optional

from src.robinhood.client import RobinhoodClient


class AccountService:
    def __init__(self, client: RobinhoodClient) -> None:
        self.client = client

    def account(self) -> Dict[str, Any]:
        return self.client.get_account()

    def holdings(self, asset_codes: Optional[Iterable[str]] = None) -> Dict[str, Any]:
        return self.client.get_holdings(asset_codes)
