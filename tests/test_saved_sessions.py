from datetime import datetime, timedelta, timezone

from src.data.database import ResearchDatabase


def _set_latest_timestamp(database: ResearchDatabase, timestamp: datetime) -> None:
    database.connection.execute(
        "UPDATE events SET created_at = ? WHERE id = (SELECT MAX(id) FROM events)",
        (timestamp.isoformat(),),
    )
    database.connection.commit()


def test_trade_sessions_group_saved_events_into_thirty_minute_cards(tmp_path) -> None:
    path = tmp_path / "sessions.sqlite3"
    start = datetime(2026, 6, 18, 12, 0, tzinfo=timezone.utc)

    with ResearchDatabase(str(path)) as database:
        database.log_simulated_trade(
            "BTC-USD",
            {"side": "BUY", "notional": 100, "realized_pl": 0},
        )
        _set_latest_timestamp(database, start + timedelta(minutes=2))
        database.log_simulated_trade(
            "BTC-USD",
            {"side": "SELL", "notional": 102, "realized_pl": 2},
        )
        _set_latest_timestamp(database, start + timedelta(minutes=12))
        database.log_rejected_trade("BTC-USD", {"reason": "entry interval active"})
        _set_latest_timestamp(database, start + timedelta(minutes=15))

        database.log_simulated_trade(
            "XLM-USD",
            {"side": "BUY", "notional": 100, "realized_pl": 0},
        )
        _set_latest_timestamp(database, start + timedelta(minutes=32))
        database.log_simulated_trade(
            "XLM-USD",
            {"side": "SELL", "notional": 99, "realized_pl": -1},
        )
        _set_latest_timestamp(database, start + timedelta(minutes=45))

        sessions = database.trade_sessions(starting_cash=1000)

    assert len(sessions) == 2
    assert sessions[0]["outcome"] == "non-winning"
    assert sessions[0]["realizedPl"] == -1
    assert sessions[1]["outcome"] == "winning"
    assert sessions[1]["realizedPl"] == 2
    assert sessions[1]["winRate"] == 100
    assert sessions[1]["rejectedTradesByReason"]["entry interval active"] == 1


def test_event_counts_report_saved_sqlite_rows(tmp_path) -> None:
    with ResearchDatabase(str(tmp_path / "counts.sqlite3")) as database:
        database.log_market_data("BTC-USD", {"price": 100})
        database.log_simulated_trade("BTC-USD", {"side": "BUY"})
        database.log_rejected_trade("BTC-USD", {"reason": "test"})

        counts = database.event_counts()

    assert counts["market_data"] == 1
    assert counts["simulated_trade"] == 1
    assert counts["rejected_trade"] == 1


def test_trade_sessions_include_market_data_only_windows(tmp_path) -> None:
    path = tmp_path / "sessions.sqlite3"
    start = datetime(2026, 6, 18, 12, 0, tzinfo=timezone.utc)

    with ResearchDatabase(str(path)) as database:
        database.log_market_data("DOGE-USD", {"price": 0.12})
        _set_latest_timestamp(database, start + timedelta(minutes=4))
        database.log_market_data("DOGE-USD", {"price": 0.13})
        _set_latest_timestamp(database, start + timedelta(minutes=39))

        sessions = database.trade_sessions(starting_cash=1000)

    assert len(sessions) == 2
    assert sessions[0]["symbols"] == ["DOGE-USD"]
    assert sessions[0]["numberOfTrades"] == 0
    assert sessions[0]["realizedPl"] == 0
    assert sessions[0]["outcome"] == "non-winning"
