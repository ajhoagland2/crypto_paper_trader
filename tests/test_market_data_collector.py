from datetime import datetime, timezone
from pathlib import Path

from src.config import Settings
from src.data.database import ResearchDatabase
from src.data.market_data_collector import MarketDataCollector
from src.web_server import WebAppState


class FakeRobinhoodClient:
    requested_symbols: list[list[str]] = []

    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs

    def get_best_bid_ask(self, symbols: list[str]) -> dict:
        self.requested_symbols.append(symbols)
        return {
            "results": [
                {"symbol": symbol, "bid_price": "99", "ask_price": "101"}
                for symbol in symbols
            ]
        }


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        robinhood_api_key="test-key",
        robinhood_private_key="test-private-key",
        trading_mode="paper",
        crypto_symbols="BTC-USD,ETH-USD",
        database_path=str(tmp_path / "research.sqlite3"),
        market_data_collector_interval_seconds=999,
    )


def test_market_data_collector_logs_quotes_without_trades(tmp_path) -> None:
    database_path = tmp_path / "collector.sqlite3"
    collector = MarketDataCollector(
        client=FakeRobinhoodClient(),
        database_path=str(database_path),
        symbols=["BTC-USD", "ETH-USD"],
        interval_seconds=999,
    )

    status = collector.collect_once(datetime(2026, 1, 1, tzinfo=timezone.utc))

    assert status["collectedTicks"] == 2
    with ResearchDatabase(str(database_path)) as database:
        rows = database.recent_events(10)

    assert [row["event_type"] for row in rows] == ["market_data", "market_data"]
    assert {row["symbol"] for row in rows} == {"BTC-USD", "ETH-USD"}
    assert all(row["payload"]["source"] == "background_market_data_collector" for row in rows)


def test_web_state_collector_starts_without_paper_runner(monkeypatch, tmp_path) -> None:
    import src.web_server as web_server

    FakeRobinhoodClient.requested_symbols = []
    monkeypatch.setattr(web_server, "get_settings", lambda: _settings(tmp_path))
    monkeypatch.setattr(web_server, "RobinhoodClient", FakeRobinhoodClient)

    state = WebAppState()
    collector = state.ensure_market_data_collector()

    assert collector is not None
    assert state.runner is None
    assert collector.symbols == ["BTC-USD", "ETH-USD"]

    collector.stop()
