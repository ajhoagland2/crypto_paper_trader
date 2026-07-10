import json
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, Iterable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from src.robinhood.auth import RequestSigner, RobinhoodCredentials


class RobinhoodAPIError(RuntimeError):
    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class RetryConfig:
    attempts: int = 3
    backoff_seconds: float = 0.5
    timeout_seconds: float = 10.0
    retry_statuses: tuple = (429, 500, 502, 503, 504)


class RobinhoodClient:
    """Small wrapper around the official Robinhood Crypto API."""

    def __init__(
        self,
        api_key: str,
        private_key: str,
        base_url: str = "https://trading.robinhood.com",
        retry_config: Optional[RetryConfig] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.signer = RequestSigner(RobinhoodCredentials(api_key, private_key))
        self.retry_config = retry_config or RetryConfig()

    def get_market_quote(self, symbol: str) -> Dict[str, Any]:
        return self.get_best_bid_ask([symbol])

    def get_best_bid_ask(self, symbols: Iterable[str]) -> Dict[str, Any]:
        path = "/api/v1/crypto/marketdata/best_bid_ask/"
        params = [("symbol", symbol.upper()) for symbol in symbols]
        return self._get(path, params=params)

    def get_account(self) -> Dict[str, Any]:
        return self._get("/api/v1/crypto/trading/accounts/")

    def get_holdings(self, asset_codes: Optional[Iterable[str]] = None) -> Dict[str, Any]:
        path = "/api/v1/crypto/trading/holdings/"
        params = [("asset_code", code.upper()) for code in asset_codes or []]
        return self._get(path, params=params)

    def place_market_order(
        self,
        symbol: str,
        side: str,
        asset_quantity: float,
        client_order_id: str,
    ) -> Dict[str, Any]:
        side = side.lower()
        if side not in {"buy", "sell"}:
            raise ValueError("side must be buy or sell")
        if asset_quantity <= 0:
            raise ValueError("asset_quantity must be positive")
        body = {
            "client_order_id": client_order_id,
            "side": side,
            "type": "market",
            "symbol": symbol.upper(),
            "market_order_config": {
                "asset_quantity": _decimal_string(asset_quantity),
            },
        }
        return self._post("/api/v1/crypto/trading/orders/", body)

    def _get(self, path: str, params: Optional[Iterable[tuple]] = None) -> Dict[str, Any]:
        query = urlencode(list(params or []))
        request_path = f"{path}?{query}" if query else path
        return self._request("GET", request_path)

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", path, body=json.dumps(payload, separators=(",", ":")))

    def _request(self, method: str, path: str, body: Optional[str] = None) -> Dict[str, Any]:
        headers = self.signer.headers(method, path, body)
        data = body.encode("utf-8") if body else None
        request = Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)

        last_error: Optional[Exception] = None
        for attempt in range(1, self.retry_config.attempts + 1):
            try:
                with urlopen(request, timeout=self.retry_config.timeout_seconds) as response:
                    payload = response.read().decode("utf-8")
                    return json.loads(payload) if payload else {}
            except HTTPError as exc:
                last_error = exc
                if exc.code not in self.retry_config.retry_statuses:
                    detail = exc.read().decode("utf-8", errors="replace")
                    raise RobinhoodAPIError(
                        f"Robinhood API returned HTTP {exc.code}: {detail}", exc.code
                    ) from exc
            except URLError as exc:
                last_error = exc

            if attempt < self.retry_config.attempts:
                time.sleep(self.retry_config.backoff_seconds * attempt)

        raise RobinhoodAPIError(f"Robinhood API request failed: {last_error}") from last_error


def _decimal_string(value: float) -> str:
    decimal = Decimal(str(value)).normalize()
    return format(decimal, "f")
