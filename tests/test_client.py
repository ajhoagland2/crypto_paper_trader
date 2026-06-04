import json
from unittest.mock import Mock, patch

from src.robinhood.client import RetryConfig, RobinhoodClient


def test_get_market_quote_uses_official_best_bid_ask_path() -> None:
    client = RobinhoodClient(
        "key",
        "private",
        retry_config=RetryConfig(attempts=1, backoff_seconds=0),
    )
    client.signer.headers = Mock(return_value={"x-api-key": "key"})

    response = Mock()
    response.read.return_value = json.dumps({"results": [{"symbol": "BTC-USD"}]}).encode()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=None)

    with patch("src.robinhood.client.urlopen", return_value=response) as mocked_urlopen:
        payload = client.get_market_quote("btc-usd")

    request = mocked_urlopen.call_args.args[0]
    assert payload["results"][0]["symbol"] == "BTC-USD"
    assert request.full_url.endswith("/api/v1/crypto/marketdata/best_bid_ask/?symbol=BTC-USD")


def test_get_holdings_adds_asset_code_query() -> None:
    client = RobinhoodClient(
        "key",
        "private",
        retry_config=RetryConfig(attempts=1, backoff_seconds=0),
    )
    client.signer.headers = Mock(return_value={"x-api-key": "key"})

    response = Mock()
    response.read.return_value = b'{"results":[]}'
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=None)

    with patch("src.robinhood.client.urlopen", return_value=response) as mocked_urlopen:
        client.get_holdings(["btc"])

    request = mocked_urlopen.call_args.args[0]
    assert request.full_url.endswith("/api/v1/crypto/trading/holdings/?asset_code=BTC")
