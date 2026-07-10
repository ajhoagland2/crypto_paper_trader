from datetime import datetime, timedelta, timezone

from src.data.database import ResearchDatabase
from src.risk.risk_manager import RiskManager
from src.strategy.control_stack import ControlStack
from src.strategy.momentum import MomentumConfig
from src.trading.execution import ExecutionResult
from src.trading.live_paper_runner import (
    LivePaperRunner,
    extract_best_bid_ask_prices,
    extract_quote_snapshots,
)
from src.trading.paper_trader import PaperTrader
from src.robinhood.client import RobinhoodAPIError


class FakeRobinhoodClient:
    def __init__(self, prices: list[float]) -> None:
        self.prices = prices
        self.index = 0
        self.requested_symbols: list[list[str]] = []

    def get_best_bid_ask(self, symbols: list[str]) -> dict:
        self.requested_symbols.append(symbols)
        price = self.prices[min(self.index, len(self.prices) - 1)]
        self.index += 1
        return {
            "results": [
                {
                    "symbol": symbols[0],
                    "bid_price": str(price - 0.05),
                    "ask_price": str(price + 0.05),
                }
            ]
        }


class RecordingSmsNotifier:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send(self, message: str) -> bool:
        self.messages.append(message)
        return True


class RecordingNotifier:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def notify(self, event_type: str, message: str, severity, **kwargs) -> None:
        self.events.append(
            {
                "event_type": event_type,
                "message": message,
                "severity": severity,
                **kwargs,
            }
        )

    def maybe_send_heartbeat(self, now=None) -> None:
        del now


class FailingRobinhoodClient:
    def get_best_bid_ask(self, symbols: list[str]) -> dict:
        del symbols
        raise RobinhoodAPIError("network blocked")


class RecordingOrderExecutor:
    mode = "LIVE"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.orders: list[dict] = []

    def submit_market_order(self, symbol: str, side: str, quantity: float) -> ExecutionResult:
        self.orders.append({"symbol": symbol, "side": side, "quantity": quantity})
        if self.fail:
            raise RobinhoodAPIError("order rejected", status_code=400)
        return ExecutionResult(
            submitted=True,
            mode="LIVE",
            order_id=f"order-{len(self.orders)}",
            client_order_id=f"client-{len(self.orders)}",
            raw_response={"id": f"order-{len(self.orders)}"},
        )


def test_extract_best_bid_ask_prices_uses_midpoint() -> None:
    payload = {
        "results": [
            {"symbol": "BTC-USD", "bid_price": "99", "ask_price": "101"},
            {"asset_code": "ETH", "mark_price": "2500"},
        ]
    }

    prices = extract_best_bid_ask_prices(payload)

    assert prices["BTC-USD"] == 100
    assert prices["ETH-USD"] == 2500


def test_extract_quote_snapshots_preserves_bid_ask_spread() -> None:
    payload = {
        "results": [
            {"symbol": "XLM-USD", "bid_price": "0.20146", "ask_price": "0.20550"},
        ]
    }

    snapshot = extract_quote_snapshots(payload)["XLM-USD"]

    assert snapshot.bid == 0.20146
    assert snapshot.ask == 0.20550
    assert snapshot.midpoint == 0.20348
    assert snapshot.spread_pct > 1.9


def test_live_paper_runner_executes_control_stack_paper_trade(tmp_path) -> None:
    database_path = tmp_path / "paper.sqlite3"
    client = FakeRobinhoodClient([100, 101, 102, 103])
    trader = PaperTrader(starting_cash=1000)
    risk = RiskManager(
        max_trade_size=200,
        max_daily_loss=100,
        max_trades_per_day=10,
        cooldown_after_loss_seconds=0,
    )
    control_stack = ControlStack(
        lookback_window=3,
        buy_dip_percent=5,
        rebound_percent=1,
        sell_above_dip_percent=5,
        stop_loss_percent=5,
        trailing_stop_percent=5,
    )

    with ResearchDatabase(str(database_path)) as database:
        runner = LivePaperRunner(
            client=client,  # type: ignore[arg-type]
            trader=trader,
            risk=risk,
            database=database,
            symbols=["BTC-USD"],
            control_stack=control_stack,
            momentum_config=MomentumConfig(
                interval_seconds=1,
                entry_threshold=45,
                exit_threshold=10,
                target_profit_pct=0.5,
                allocation_per_trade=200,
            ),
            sms_summary_interval_seconds=9999,
            api_min_request_interval_seconds=1,
        )
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        buy_result = runner.run_once(start)
        sell_result = runner.run_once(start + timedelta(seconds=1))

    assert buy_result["signals"]["BTC-USD"] == "BUY"
    assert sell_result["signals"]["BTC-USD"] == "SELL"
    assert len(trader.trade_history) == 2
    assert trader.realized_pl() > 0
    assert runner.price_history["BTC-USD"] == [100, 101]
    assert len(runner.candle_history["BTC-USD"]) == 2
    assert runner.candle_history["BTC-USD"][-1]["close"] == 101
    assert any(event["type"] == "trade" for event in runner.event_history)
    assert all(event["symbol"] == "BTC-USD" for event in runner.event_history)


def test_live_paper_runner_updates_controls_between_ticks(tmp_path) -> None:
    database_path = tmp_path / "paper.sqlite3"

    with ResearchDatabase(str(database_path)) as database:
        runner = LivePaperRunner(
            client=FakeRobinhoodClient([100, 101, 102]),  # type: ignore[arg-type]
            trader=PaperTrader(starting_cash=1000),
            risk=RiskManager(
                max_trade_size=200,
                max_daily_loss=100,
                max_trades_per_day=10,
                cooldown_after_loss_seconds=0,
            ),
            database=database,
            symbols=["BTC-USD"],
            control_stack=ControlStack(buy_dip_percent=50),
            momentum_config=MomentumConfig(interval_seconds=1, entry_threshold=100),
            api_min_request_interval_seconds=1,
        )
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        runner.run_once(start)
        runner.run_once(start + timedelta(seconds=1))
        runner.update_momentum_config(MomentumConfig(interval_seconds=1, entry_threshold=45))
        result = runner.run_once(start + timedelta(seconds=2))

    assert result["signals"]["BTC-USD"] == "BUY"


def test_live_paper_runner_sets_sell_target_from_buy_price(tmp_path) -> None:
    database_path = tmp_path / "paper.sqlite3"

    with ResearchDatabase(str(database_path)) as database:
        runner = LivePaperRunner(
            client=FakeRobinhoodClient([100]),  # type: ignore[arg-type]
            trader=PaperTrader(starting_cash=1000),
            risk=RiskManager(
                max_trade_size=200,
                max_daily_loss=100,
                max_trades_per_day=10,
                cooldown_after_loss_seconds=0,
            ),
            database=database,
            symbols=["BTC-USD"],
            control_stack=ControlStack(lookback_window=3),
            momentum_config=MomentumConfig(
                interval_seconds=1,
                entry_threshold=45,
                target_profit_pct=1.5,
                allocation_per_trade=200,
            ),
            api_min_request_interval_seconds=1,
        )
        runner.run_once(datetime(2026, 1, 1, tzinfo=timezone.utc))

    metadata = runner.open_trade_metadata["BTC-USD"][0]
    assert metadata["entry_price"] == 100
    assert abs(metadata["target_exit_price"] - 101.5) < 0.000001


def test_live_paper_runner_adds_second_lot_after_entry_interval(tmp_path) -> None:
    database_path = tmp_path / "paper.sqlite3"

    with ResearchDatabase(str(database_path)) as database:
        runner = LivePaperRunner(
            client=FakeRobinhoodClient([100, 100, 100, 100]),  # type: ignore[arg-type]
            trader=PaperTrader(starting_cash=1000),
            risk=RiskManager(
                max_trade_size=100,
                max_daily_loss=100,
                max_trades_per_day=10,
                cooldown_after_loss_seconds=0,
            ),
            database=database,
            symbols=["BTC-USD"],
            control_stack=ControlStack(),
            momentum_config=MomentumConfig(
                interval_seconds=30,
                entry_threshold=50,
                entry_interval_seconds=300,
                max_open_trades=2,
                target_profit_pct=10,
                allocation_per_trade=100,
            ),
            api_min_request_interval_seconds=1,
        )
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        first = runner.run_once(start)
        early = runner.run_once(start + timedelta(seconds=299))
        second = runner.run_once(start + timedelta(seconds=300))
        capped = runner.run_once(start + timedelta(seconds=600))

    assert first["signals"]["BTC-USD"] == "BUY"
    assert early["signals"]["BTC-USD"] == "HOLD"
    assert second["signals"]["BTC-USD"] == "BUY"
    assert capped["signals"]["BTC-USD"] == "HOLD"
    assert len(runner.open_trade_metadata["BTC-USD"]) == 2
    assert len([trade for trade in runner.trader.trade_history if trade.side == "BUY"]) == 2


def test_live_paper_runner_sells_only_lot_whose_target_is_reached(tmp_path) -> None:
    database_path = tmp_path / "paper.sqlite3"

    with ResearchDatabase(str(database_path)) as database:
        runner = LivePaperRunner(
            client=FakeRobinhoodClient([100, 99.9, 100.9]),  # type: ignore[arg-type]
            trader=PaperTrader(starting_cash=1000),
            risk=RiskManager(
                max_trade_size=100,
                max_daily_loss=100,
                max_trades_per_day=10,
                cooldown_after_loss_seconds=0,
            ),
            database=database,
            symbols=["BTC-USD"],
            control_stack=ControlStack(),
            momentum_config=MomentumConfig(
                interval_seconds=30,
                entry_threshold=0,
                entry_interval_seconds=300,
                max_open_trades=2,
                target_profit_pct=1,
                catastrophic_loss_pct=-50,
                allocation_per_trade=100,
            ),
            api_min_request_interval_seconds=1,
        )
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        runner.run_once(start)
        runner.run_once(start + timedelta(seconds=300))
        result = runner.run_once(start + timedelta(seconds=301))

    assert result["signals"]["BTC-USD"] == "SELL"
    assert len(result["trades"]) == 1
    assert result["trades"][0]["exit_reason"] == "TARGET_PROFIT"
    assert len(runner.open_trade_metadata["BTC-USD"]) == 1
    assert runner.open_trade_metadata["BTC-USD"][0]["entry_price"] == 100


def test_live_paper_runner_sends_hourly_summary(tmp_path) -> None:
    notifier = RecordingSmsNotifier()
    database_path = tmp_path / "paper.sqlite3"

    with ResearchDatabase(str(database_path)) as database:
        runner = LivePaperRunner(
            client=FakeRobinhoodClient([100]),  # type: ignore[arg-type]
            trader=PaperTrader(starting_cash=1000),
            risk=RiskManager(
                max_trade_size=200,
                max_daily_loss=100,
                max_trades_per_day=10,
                cooldown_after_loss_seconds=0,
            ),
            database=database,
            symbols=["BTC-USD"],
            control_stack=ControlStack(),
            sms_summary_interval_seconds=3600,
            sms_notifier=notifier,
        )
        runner.last_summary_at = datetime.now(timezone.utc) - timedelta(hours=1)
        runner.run_once()

    assert len(notifier.messages) == 1
    assert "Hourly paper trader summary" in notifier.messages[0]


def test_live_paper_runner_notifies_when_paper_trade_executes(tmp_path) -> None:
    notifier = RecordingNotifier()
    database_path = tmp_path / "paper.sqlite3"

    with ResearchDatabase(str(database_path)) as database:
        runner = LivePaperRunner(
            client=FakeRobinhoodClient([100, 101]),  # type: ignore[arg-type]
            trader=PaperTrader(starting_cash=1000),
            risk=RiskManager(
                max_trade_size=200,
                max_daily_loss=100,
                max_trades_per_day=10,
                cooldown_after_loss_seconds=0,
            ),
            database=database,
            symbols=["BTC-USD"],
            control_stack=ControlStack(),
            momentum_config=MomentumConfig(
                interval_seconds=1,
                entry_threshold=45,
                target_profit_pct=0.5,
                allocation_per_trade=200,
            ),
            api_min_request_interval_seconds=1,
            notifier=notifier,
        )
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        runner.run_once(start)
        runner.run_once(start + timedelta(seconds=1))

    executed = [
        event for event in notifier.events if event["event_type"] == "PAPER_TRADE_EXECUTED"
    ]
    assert [event["details"]["side"] for event in executed] == ["BUY", "SELL"]
    assert all(event["details"]["mode"] == "SHADOW" for event in executed)


def test_live_runner_submits_order_before_mirroring_trade(tmp_path) -> None:
    database_path = tmp_path / "paper.sqlite3"
    executor = RecordingOrderExecutor()

    with ResearchDatabase(str(database_path)) as database:
        runner = LivePaperRunner(
            client=FakeRobinhoodClient([100]),  # type: ignore[arg-type]
            trader=PaperTrader(starting_cash=1000),
            risk=RiskManager(
                max_trade_size=200,
                max_daily_loss=100,
                max_trades_per_day=10,
                cooldown_after_loss_seconds=0,
            ),
            database=database,
            symbols=["BTC-USD"],
            control_stack=ControlStack(),
            momentum_config=MomentumConfig(
                interval_seconds=1,
                entry_threshold=45,
                target_profit_pct=1,
                allocation_per_trade=200,
            ),
            api_min_request_interval_seconds=1,
            order_executor=executor,
        )
        result = runner.run_once(datetime(2026, 1, 1, tzinfo=timezone.utc))
        rows = database.recent_events(10)

    assert result["trades"][0]["execution"]["submitted"] is True
    assert result["trades"][0]["execution"]["order_id"] == "order-1"
    assert executor.orders == [{"symbol": "BTC-USD", "side": "BUY", "quantity": 1.99900049}]
    assert runner.trader.positions["BTC-USD"].quantity == 1.99900049
    assert runner.trader.trade_history[0].price == 100.05
    assert any(row["event_type"] == "live_order" for row in rows)


def test_live_runner_uses_ask_for_buy_and_bid_for_sell_accounting(tmp_path) -> None:
    database_path = tmp_path / "paper.sqlite3"
    executor = RecordingOrderExecutor()

    with ResearchDatabase(str(database_path)) as database:
        runner = LivePaperRunner(
            client=FakeRobinhoodClient([100, 101.2]),  # type: ignore[arg-type]
            trader=PaperTrader(starting_cash=1000),
            risk=RiskManager(
                max_trade_size=200,
                max_daily_loss=100,
                max_trades_per_day=10,
                cooldown_after_loss_seconds=0,
            ),
            database=database,
            symbols=["BTC-USD"],
            control_stack=ControlStack(),
            momentum_config=MomentumConfig(
                interval_seconds=1,
                entry_threshold=45,
                target_profit_pct=1,
                allocation_per_trade=200,
            ),
            api_min_request_interval_seconds=1,
            order_executor=executor,
        )
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        buy_result = runner.run_once(start)
        sell_result = runner.run_once(start + timedelta(seconds=1))

    buy_trade = buy_result["trades"][0]
    sell_trade = sell_result["trades"][0]
    assert buy_trade["price"] == 100.05
    assert buy_trade["quote"]["bid"] == 99.95
    assert buy_trade["quote"]["ask"] == 100.05
    assert sell_trade["price"] == 101.15
    assert sell_trade["realized_pl"] > 2
    assert [order["side"] for order in executor.orders] == ["BUY", "SELL"]


def test_live_runner_does_not_mirror_failed_live_order(tmp_path) -> None:
    database_path = tmp_path / "paper.sqlite3"
    executor = RecordingOrderExecutor(fail=True)

    with ResearchDatabase(str(database_path)) as database:
        runner = LivePaperRunner(
            client=FakeRobinhoodClient([100]),  # type: ignore[arg-type]
            trader=PaperTrader(starting_cash=1000),
            risk=RiskManager(
                max_trade_size=200,
                max_daily_loss=100,
                max_trades_per_day=10,
                cooldown_after_loss_seconds=0,
            ),
            database=database,
            symbols=["BTC-USD"],
            control_stack=ControlStack(),
            momentum_config=MomentumConfig(
                interval_seconds=1,
                entry_threshold=45,
                target_profit_pct=1,
                allocation_per_trade=200,
            ),
            api_min_request_interval_seconds=1,
            order_executor=executor,
        )
        result = runner.run_once(datetime(2026, 1, 1, tzinfo=timezone.utc))

    assert result["rejections"][0]["reason"] == "live order submission failed"
    assert runner.trader.trade_history == []
    assert runner.trader.positions == {}


def test_live_paper_runner_notifies_when_trade_is_not_going_well(tmp_path) -> None:
    notifier = RecordingNotifier()
    database_path = tmp_path / "paper.sqlite3"

    with ResearchDatabase(str(database_path)) as database:
        runner = LivePaperRunner(
            client=FakeRobinhoodClient([100, 98.9, 98.8]),  # type: ignore[arg-type]
            trader=PaperTrader(starting_cash=1000),
            risk=RiskManager(
                max_trade_size=200,
                max_daily_loss=100,
                max_trades_per_day=10,
                cooldown_after_loss_seconds=0,
            ),
            database=database,
            symbols=["BTC-USD"],
            control_stack=ControlStack(),
            momentum_config=MomentumConfig(
                interval_seconds=1,
                entry_threshold=45,
                max_open_trades=1,
                target_profit_pct=10,
                catastrophic_loss_pct=-50,
                soft_stop_loss_pct=-2,
                allocation_per_trade=200,
            ),
            api_min_request_interval_seconds=1,
            notifier=notifier,
        )
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        runner.run_once(start)
        runner.run_once(start + timedelta(seconds=1))
        runner.run_once(start + timedelta(seconds=2))

    struggling = [
        event for event in notifier.events if event["event_type"] == "TRADE_NOT_GOING_WELL"
    ]
    assert len(struggling) == 1
    assert struggling[0]["symbol"] == "BTC-USD"
    assert struggling[0]["details"]["unrealized_pl_pct"] <= -1.0


def test_live_paper_runner_sends_hourly_performance_notification(tmp_path) -> None:
    notifier = RecordingNotifier()
    database_path = tmp_path / "paper.sqlite3"

    with ResearchDatabase(str(database_path)) as database:
        runner = LivePaperRunner(
            client=FakeRobinhoodClient([100]),  # type: ignore[arg-type]
            trader=PaperTrader(starting_cash=1000),
            risk=RiskManager(
                max_trade_size=200,
                max_daily_loss=100,
                max_trades_per_day=10,
                cooldown_after_loss_seconds=0,
            ),
            database=database,
            symbols=["BTC-USD"],
            control_stack=ControlStack(),
            api_min_request_interval_seconds=1,
            notification_recap_interval_seconds=3600,
            notifier=notifier,
        )
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        runner.started_at = start
        runner.last_recap_card_at = start
        runner.run_once(start + timedelta(hours=1))

    summaries = [
        event for event in notifier.events if event["event_type"] == "HOURLY_RECAP_CARD"
    ]
    assert len(summaries) == 1
    assert summaries[0]["details"]["mode"] == "SHADOW"
    assert summaries[0]["details"]["window_minutes"] == 60
    assert summaries[0]["details"]["cadence"] == "hourly"


def test_live_paper_runner_sends_30_minute_sql_card_update(tmp_path) -> None:
    notifier = RecordingNotifier()
    database_path = tmp_path / "paper.sqlite3"

    with ResearchDatabase(str(database_path)) as database:
        runner = LivePaperRunner(
            client=FakeRobinhoodClient([100]),  # type: ignore[arg-type]
            trader=PaperTrader(starting_cash=1000),
            risk=RiskManager(
                max_trade_size=200,
                max_daily_loss=100,
                max_trades_per_day=10,
                cooldown_after_loss_seconds=0,
            ),
            database=database,
            symbols=["DOGE-USD"],
            control_stack=ControlStack(),
            notification_trade_update_interval_seconds=1800,
            notifier=notifier,
        )
        database.log_simulated_trade(
            "DOGE-USD",
            {"side": "BUY", "realized_pl": 0.0},
        )
        database.log_simulated_trade(
            "DOGE-USD",
            {"side": "SELL", "realized_pl": 3.25},
        )
        runner.last_trade_status_update_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        runner.run_once(datetime(2026, 1, 1, 0, 30, tzinfo=timezone.utc))

    updates = [
        event for event in notifier.events if event["event_type"] == "TRADE_STATUS_UPDATE"
    ]
    assert len(updates) == 1
    details = updates[0]["details"]
    assert details["window_minutes"] == 30
    assert details["cadence"] == "30 minutes"
    assert details["symbols"] == "DOGE-USD"
    assert details["realized_pl"] == 3.25
    assert details["trades"] == 1
    assert details["buys"] == 1


def test_live_paper_runner_records_api_error_for_browser(tmp_path) -> None:
    database_path = tmp_path / "paper.sqlite3"

    with ResearchDatabase(str(database_path)) as database:
        runner = LivePaperRunner(
            client=FailingRobinhoodClient(),  # type: ignore[arg-type]
            trader=PaperTrader(starting_cash=1000),
            risk=RiskManager(
                max_trade_size=200,
                max_daily_loss=100,
                max_trades_per_day=10,
                cooldown_after_loss_seconds=0,
            ),
            database=database,
            symbols=["BTC-USD"],
            control_stack=ControlStack(),
        )
        result = runner.run_once()

    assert result["error"] == "network blocked"
    assert runner.last_error == "network blocked"
    assert runner.event_history[-1]["type"] == "api_error"


def test_live_paper_runner_caps_long_run_browser_histories(tmp_path) -> None:
    database_path = tmp_path / "paper.sqlite3"

    with ResearchDatabase(str(database_path)) as database:
        runner = LivePaperRunner(
            client=FakeRobinhoodClient([100, 101, 102, 103, 104, 105]),  # type: ignore[arg-type]
            trader=PaperTrader(starting_cash=1000),
            risk=RiskManager(
                max_trade_size=100,
                max_daily_loss=100,
                max_trades_per_day=10,
                cooldown_after_loss_seconds=0,
            ),
            database=database,
            symbols=["BTC-USD"],
            control_stack=ControlStack(),
            momentum_config=MomentumConfig(interval_seconds=1, entry_threshold=100),
            api_min_request_interval_seconds=1,
            max_history_points=3,
            max_event_history=4,
        )
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for index in range(6):
            runner.run_once(start + timedelta(seconds=index))

    assert runner.tick_counts["BTC-USD"] == 6
    assert runner.price_history["BTC-USD"] == [103, 104, 105]
    assert len(runner.quote_history["BTC-USD"]) == 3
    assert len(runner.candle_history["BTC-USD"]) == 3
    assert runner.candle_history["BTC-USD"][-1]["step"] == 6
    assert len(runner.event_history) <= 4


def test_live_paper_runner_persists_unique_long_run_summary_windows(tmp_path) -> None:
    database_path = tmp_path / "paper.sqlite3"

    with ResearchDatabase(str(database_path)) as database:
        runner = LivePaperRunner(
            client=FakeRobinhoodClient([100, 101, 102]),  # type: ignore[arg-type]
            trader=PaperTrader(starting_cash=1000),
            risk=RiskManager(
                max_trade_size=100,
                max_daily_loss=100,
                max_trades_per_day=10,
                cooldown_after_loss_seconds=0,
            ),
            database=database,
            symbols=["BTC-USD"],
            control_stack=ControlStack(),
            momentum_config=MomentumConfig(interval_seconds=1, entry_threshold=100),
            api_min_request_interval_seconds=1,
            session_window_minutes=30,
        )
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        runner.started_at = start
        runner.run_once(start + timedelta(minutes=31))
        runner.run_once(start + timedelta(minutes=61))
        summaries = database.simulation_summaries(limit=10)

    assert len(summaries) == 2
    assert {summary["payload"]["window_index"] for summary in summaries} == {1, 2}
