from datetime import datetime, timedelta, timezone

import pytest

from src.backtest.backtestEngine import run_backtest
from src.backtest.models import BacktestConfig, HistoricalMarketDataPoint
from src.backtest.walkForwardEngine import run_walk_forward
from src.data.database import ResearchDatabase
from src.web_server import run_backtest_request


def _config() -> BacktestConfig:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return BacktestConfig(
        symbol="XLM-USD",
        start=start,
        end=start + timedelta(minutes=10),
        starting_cash=1000,
        max_trade_size=100,
        entry_score=50,
        exit_score=40,
        target_profit_pct=0.2,
        cooldown_seconds=30,
        catastrophic_loss_pct=-3,
    )


def _points(prices: list[float]) -> list[HistoricalMarketDataPoint]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        HistoricalMarketDataPoint(start + timedelta(seconds=index * 30), price)
        for index, price in enumerate(prices)
    ]


def test_backtest_runs_momentum_strategy_and_records_metrics() -> None:
    result = run_backtest(_config(), _points([1, 1.01, 1.02, 1.03, 1.01, 0.99, 1.0, 1.02]))

    assert result.metrics["numberOfTrades"] >= 1
    assert "rejectedTradesByReason" in result.metrics
    assert result.equity_curve
    assert result.price_series
    assert any(marker.type == "BUY" for marker in result.markers)


def test_backtest_rejects_tiny_dataset_as_invalid_market_data() -> None:
    result = run_backtest(_config(), _points([1, 1.01]))

    assert result.rejected_reasons["missing or invalid market data"] == 1
    assert result.warnings


def test_backtest_respects_entry_interval_and_open_trade_cap() -> None:
    config = _config()
    config = BacktestConfig(
        **{
            **config.__dict__,
            "entry_score": 0,
            "target_profit_pct": 100,
            "entry_interval_seconds": 60,
            "max_open_trades": 2,
        }
    )

    result = run_backtest(config, _points([1, 1, 1, 1, 1, 1]))

    assert result.metrics["openTrades"] == 2
    assert len([marker for marker in result.markers if marker.type == "BUY"]) == 2
    assert result.rejected_reasons["entry interval active"] >= 1
    assert result.rejected_reasons["max concurrent trades reached"] >= 1


def test_walk_forward_returns_training_and_unseen_test_metrics() -> None:
    result = run_walk_forward(
        _config(),
        _points([1, 1.01, 1.02, 1.03, 1.01, 0.99, 1.0, 1.01, 1.03, 1.04, 1.02, 1.01]),
    )

    assert result.enabled
    assert result.best_parameters
    assert "totalReturn" in result.training_metrics
    assert "totalReturn" in result.test_metrics
    assert "totalReturnDelta" in result.degradation


def test_backtest_api_loads_local_market_data_without_live_state(monkeypatch, tmp_path) -> None:
    database_path = tmp_path / "research.sqlite3"
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with ResearchDatabase(str(database_path)) as database:
        for index, price in enumerate([1, 1.01, 1.02, 1.03, 1.02, 1.01, 1.0]):
            database.log_market_data("XLM-USD", {"price": price})
            database.connection.execute(
                "UPDATE events SET created_at = ? WHERE id = (SELECT MAX(id) FROM events)",
                ((start + timedelta(seconds=index * 30)).isoformat(),),
            )
            database.connection.commit()

    import src.web_server as web_server

    settings = web_server.get_settings()
    monkeypatch.setattr(
        web_server,
        "get_settings",
        lambda: settings.__class__(**{**settings.__dict__, "database_path": str(database_path)}),
    )

    response = run_backtest_request(
        {
            "symbol": "XLM",
            "start": start.isoformat(),
            "end": (start + timedelta(minutes=5)).isoformat(),
            "startingCash": 1000,
            "maxTradeSize": 100,
            "entryScore": 50,
            "exitScore": 40,
            "targetProfitPct": 0.2,
            "cooldownSeconds": 30,
            "catastrophicLossPct": -3,
            "walkForwardEnabled": True,
        }
    )

    assert response["dataPointCount"] == 7
    assert response["source"] == "local_sqlite_market_data"
    assert response["result"]["config"]["symbol"] == "XLM-USD"
    assert response["walkForward"]["enabled"]


def test_backtest_api_rejects_impossible_inputs() -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        run_backtest_request(
            {
                "symbol": "XLM",
                "start": start.isoformat(),
                "end": start.isoformat(),
                "startingCash": -1,
                "maxTradeSize": 100,
                "entryScore": 110,
                "exitScore": 40,
                "targetProfitPct": 0.2,
                "cooldownSeconds": 30,
                "catastrophicLossPct": -3,
            }
        )
