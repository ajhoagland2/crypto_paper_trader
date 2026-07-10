import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional
from uuid import uuid4

from src.data.database import ResearchDatabase
from src.notifications.sms import NullSmsNotifier
from src.notifications.providers.base import NotificationSeverity
from src.risk.risk_manager import RiskManager
from src.robinhood.client import RobinhoodAPIError, RobinhoodClient
from src.strategy.control_stack import ControlStack
from src.strategy.momentum import MomentumConfig, MomentumScore, MomentumStrategy, RollingMarketDataBuffer
from src.strategy.signals import Signal
from src.trading.execution import ExecutionResult, OrderExecutor, PaperOrderExecutor, quantize_asset_quantity
from src.trading.paper_trader import PaperTrader


@dataclass(frozen=True)
class QuoteSnapshot:
    symbol: str
    midpoint: float
    bid: Optional[float] = None
    ask: Optional[float] = None

    @property
    def spread_pct(self) -> float:
        if not self.bid or not self.ask or self.midpoint <= 0:
            return 0.0
        return ((self.ask - self.bid) / self.midpoint) * 100


class LivePaperRunner:
    """Poll Robinhood market data and execute control-stack trades."""

    def __init__(
        self,
        client: RobinhoodClient,
        trader: PaperTrader,
        risk: RiskManager,
        database: ResearchDatabase,
        symbols: Iterable[str],
        control_stack: ControlStack,
        momentum_config: Optional[MomentumConfig] = None,
        api_min_request_interval_seconds: float = 1.0,
        poll_interval_seconds: int = 60,
        sms_summary_interval_seconds: int = 3600,
        sms_notifier: Optional[object] = None,
        notifier: Optional[object] = None,
        notification_summary_interval_seconds: int = 3600,
        notification_trade_update_interval_seconds: int = 1800,
        notification_recap_interval_seconds: int = 3600,
        max_history_points: int = 3000,
        max_event_history: int = 500,
        session_window_minutes: int = 30,
        order_executor: Optional[OrderExecutor] = None,
    ) -> None:
        control_stack.validate()
        self.client = client
        self.trader = trader
        self.risk = risk
        self.database = database
        self.symbols = [symbol.upper() for symbol in symbols]
        self.control_stack = control_stack
        self.momentum_config = momentum_config or MomentumConfig()
        self.momentum_strategy = MomentumStrategy(self.momentum_config)
        self.api_min_request_interval_seconds = api_min_request_interval_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.sms_summary_interval_seconds = sms_summary_interval_seconds
        self.sms_notifier = sms_notifier or NullSmsNotifier()
        self.notifier = notifier
        self.notification_summary_interval_seconds = max(60, int(notification_summary_interval_seconds))
        self.notification_trade_update_interval_seconds = max(
            60,
            int(notification_trade_update_interval_seconds),
        )
        self.notification_recap_interval_seconds = max(60, int(notification_recap_interval_seconds))
        self.max_history_points = max(1, int(max_history_points))
        self.max_event_history = max(1, int(max_event_history))
        self.session_window_minutes = max(1, int(session_window_minutes))
        self.price_history: Dict[str, List[float]] = {symbol: [] for symbol in self.symbols}
        self.quote_history: Dict[str, List[Dict[str, Any]]] = {symbol: [] for symbol in self.symbols}
        self.candle_history: Dict[str, List[Dict[str, Any]]] = {symbol: [] for symbol in self.symbols}
        self.tick_counts: Dict[str, int] = {symbol: 0 for symbol in self.symbols}
        self.latest_prices: Dict[str, float] = {}
        self.highest_since_entry: Dict[str, float] = {}
        self.dip_reference_by_symbol: Dict[str, float] = {}
        self.event_history: List[Dict[str, Any]] = []
        self.last_error: str = ""
        self.last_summary_at = datetime.now(timezone.utc)
        self.last_notification_summary_at = datetime.now(timezone.utc)
        self.last_trade_status_update_at = datetime.now(timezone.utc)
        self.last_recap_card_at = datetime.now(timezone.utc)
        self.last_persisted_summary_at: Optional[datetime] = None
        self.persisted_summary_windows: set[int] = set()
        self.simulation_run_id = str(uuid4())
        self.started_at = datetime.now(timezone.utc)
        self.last_api_request_at: Optional[datetime] = None
        self.cached_quotes: Dict[str, float] = {}
        self.cached_quote_snapshots: Dict[str, QuoteSnapshot] = {}
        self.market_buffers: Dict[str, RollingMarketDataBuffer] = {
            symbol: RollingMarketDataBuffer(self.momentum_config.lookback_minutes * 60)
            for symbol in self.symbols
        }
        self.momentum_scores: Dict[str, Dict[str, Any]] = {}
        self.open_trade_metadata: Dict[str, List[Dict[str, Any]]] = {}
        self.order_executor = order_executor or PaperOrderExecutor()
        self._kill_switch_alerted = False
        self._not_going_well_alerted_lots: set[str] = set()
        self.manual_pause = False
        self.manual_pause_reason = ""

    def run_once(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        if self.manual_pause:
            return {
                "paused": True,
                "reason": self.manual_pause_reason or "manual pause is active",
                "signals": {},
                "trades": [],
                "rejections": [],
            }
        if self.risk.kill_switch and not self._kill_switch_alerted:
            self._notify(
                "KILL_SWITCH_ACTIVATED",
                "Risk kill switch is active. Paper entries will be blocked.",
                NotificationSeverity.CRITICAL,
                details={"mode": self.order_executor.mode, "status": self._order_status(False)},
                now=now,
            )
            self._kill_switch_alerted = True
        quotes, error = self._get_rate_limited_quotes(now)
        if error:
            return {"error": error, "signals": {}}
        self.last_error = ""
        signals: Dict[str, str] = {}
        trades = []
        rejections = []

        for symbol in self.symbols:
            price = quotes.get(symbol)
            if price is None:
                self.database.log_api_error({"error": "missing quote", "quotes": quotes}, symbol)
                continue

            self.latest_prices[symbol] = price
            self.market_buffers[symbol].add(now, price)
            self.tick_counts[symbol] = self.tick_counts.get(symbol, 0) + 1
            step = self.tick_counts[symbol]
            history = self.price_history.setdefault(symbol, [])
            history.append(price)
            quote_payload = self._quote_payload(symbol, price)
            self.quote_history.setdefault(symbol, []).append(
                {"step": step, "price": price, "timestamp": now.isoformat(), **quote_payload}
            )
            self._record_quote_candle(symbol, price, now, step)
            self._trim_symbol_history(symbol)
            self.database.log_market_data(symbol, {"price": price, "source": "robinhood"})

            position = self.trader.positions.get(symbol)
            if position:
                previous_high = self.highest_since_entry.get(symbol, position.average_price)
                self.highest_since_entry[symbol] = max(previous_high, price)

            momentum = self.momentum_strategy.score(self.market_buffers[symbol])
            self.momentum_scores[symbol] = self._momentum_payload(momentum)
            self.database.log_momentum_calculation(
                symbol,
                {
                    "simulation_run_id": self.simulation_run_id,
                    "score": momentum.score,
                    "stats": asdict(momentum.stats),
                    "trend_return": momentum.trend_return,
                    "acceleration": momentum.acceleration,
                    "moving_average_slope": momentum.moving_average_slope,
                    "volatility_stability": momentum.volatility_stability,
                },
            )

            executable_exit_price = self._execution_price(symbol, "SELL", price)
            signal = self._momentum_signal(symbol, momentum, executable_exit_price)
            signal_details = self._momentum_details(symbol, momentum, executable_exit_price)
            signals[symbol] = signal.value
            signal_event = {
                "step": step,
                "type": "signal",
                "symbol": symbol,
                "signal": signal.value,
                "price": executable_exit_price if signal == Signal.SELL else price,
                "details": signal_details,
                "momentum_score": momentum.score,
                "timestamp": now.isoformat(),
            }
            self._append_event(signal_event)
            self.database.log_strategy_signal(
                symbol,
                {
                    "simulation_run_id": self.simulation_run_id,
                    "signal": signal.value,
                    "price": price,
                    "control_stack": asdict(self.control_stack),
                    "momentum": self._momentum_payload(momentum),
                },
            )

            self._notify_struggling_lots(symbol, momentum, executable_exit_price, now)
            trade_results = self._execute_signal(symbol, signal, price, now)
            for trade in trade_results:
                if trade.get("approved"):
                    trades.append(trade)
                else:
                    rejections.append(trade)

        if (now - self.last_summary_at).total_seconds() >= self.sms_summary_interval_seconds:
            self.sms_notifier.send(self.summary())
            self.last_summary_at = now
        if self.notifier:
            if (
                now - self.last_trade_status_update_at
            ).total_seconds() >= self.notification_trade_update_interval_seconds:
                self._notify_trade_status_card(now)
                self.last_trade_status_update_at = now
            if (
                now - self.last_recap_card_at
            ).total_seconds() >= self.notification_recap_interval_seconds:
                self._notify_hourly_recap_card(now)
                self.last_recap_card_at = now
            self.notifier.maybe_send_heartbeat(now)
        self._persist_long_run_summary(now)

        return {"signals": signals, "trades": trades, "rejections": rejections}

    def _get_rate_limited_quotes(self, now: datetime) -> tuple[Dict[str, float], Optional[str]]:
        should_request = (
            self.last_api_request_at is None
            or (now - self.last_api_request_at).total_seconds() >= self.api_min_request_interval_seconds
            or not self.cached_quotes
        )
        if not should_request:
            return self.cached_quotes, None
        try:
            payload = self.client.get_best_bid_ask(self.symbols)
        except RobinhoodAPIError as exc:
            self.last_error = str(exc)
            self.database.log_api_error({"error": self.last_error, "status_code": exc.status_code})
            self._notify(
                "API_ERROR",
                "Robinhood read-only API request failed.",
                NotificationSeverity.WARNING,
                details={"status_code": exc.status_code, "mode": self.order_executor.mode},
                now=now,
            )
            self._append_event(
                {
                    "step": 0,
                    "type": "api_error",
                    "symbol": "API",
                    "signal": "ERROR",
                    "price": 0,
                    "details": self.last_error,
                    "timestamp": now.isoformat(),
                }
            )
            return self.cached_quotes, str(exc) if not self.cached_quotes else None

        quote_snapshots = extract_quote_snapshots(payload)
        quotes = {symbol: snapshot.midpoint for symbol, snapshot in quote_snapshots.items()}
        if quotes:
            self.cached_quotes = quotes
            self.cached_quote_snapshots = quote_snapshots
            self.last_api_request_at = now
        return self.cached_quotes, None

    def _momentum_signal(self, symbol: str, momentum: MomentumScore, price: float) -> Signal:
        lots = self.open_trade_metadata.get(symbol, [])
        if any(self._lot_exit_reason(lot, momentum, price) for lot in lots):
            return Signal.SELL
        return Signal.BUY if self._can_open_lot(symbol, momentum) else Signal.HOLD

    def _momentum_details(self, symbol: str, momentum: MomentumScore, price: float) -> str:
        lots = self.open_trade_metadata.get(symbol, [])
        if lots:
            target_exit_price = min(float(lot["target_exit_price"]) for lot in lots)
            exit_reasons = [
                self._lot_exit_reason(lot, momentum, price)
                for lot in lots
            ]
            reason = next((value for value in exit_reasons if value), None)
            return (
                f"momentum score {momentum.score:.1f}; {len(lots)}/"
                f"{self.momentum_config.max_open_trades} open lots; "
                f"next target {target_exit_price:.4f}; exit reason {reason or 'HOLD'}"
            )
        return (
            f"momentum score {momentum.score:.1f}/{self.momentum_config.entry_threshold:.1f}; "
            f"trend {momentum.trend_return:.4f}%; current 30s return "
            f"{momentum.stats.current_return:.4f}%; z {momentum.stats.z_score:.3f}"
        )

    @staticmethod
    def _momentum_payload(momentum: MomentumScore) -> Dict[str, Any]:
        return {
            "score": momentum.score,
            "trend_return": momentum.trend_return,
            "acceleration": momentum.acceleration,
            "moving_average_slope": momentum.moving_average_slope,
            "volatility_stability": momentum.volatility_stability,
            "stats": asdict(momentum.stats),
        }

    def run_forever(self, max_cycles: Optional[int] = None) -> None:
        cycles = 0
        self._notify(
            "SYSTEM_STARTED",
            "Live-data paper trading loop started.",
            NotificationSeverity.INFO,
            details={"mode": self.order_executor.mode, "status": self._order_status(False)},
        )
        try:
            while max_cycles is None or cycles < max_cycles:
                result = self.run_once()
                print(self.summary(result["signals"]))
                cycles += 1
                if max_cycles is None or cycles < max_cycles:
                    time.sleep(self.poll_interval_seconds)
        finally:
            self._notify(
                "SYSTEM_STOPPED",
                "Live-data paper trading loop stopped.",
                NotificationSeverity.INFO,
                details={"mode": self.order_executor.mode, "status": self._order_status(False)},
            )

    def summary(self, signals: Optional[Dict[str, str]] = None) -> str:
        signal_text = ", ".join(
            f"{symbol}:{signal}" for symbol, signal in (signals or {}).items()
        )
        if signal_text:
            signal_text = f" | Signals: {signal_text}"
        open_symbols = sorted(self.trader.positions.keys())
        open_position_text = ",".join(open_symbols) if open_symbols else "none"
        return (
            "Hourly paper trader summary: "
            f"mode={self.order_executor.mode}, "
            f"symbols={len(self.symbols)}, "
            f"open_positions={open_position_text}, "
            f"trades_logged={len(self.trader.trade_history)}, "
            f"{self._order_status(False).lower()}"
            f"{signal_text}"
        )

    def _execute_signal(
        self, symbol: str, signal: Signal, price: float, now: datetime
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        if signal == Signal.BUY:
            entry_price = self._execution_price(symbol, "BUY", price)
            notional = min(
                self.risk.max_trade_size,
                self.momentum_config.allocation_per_trade,
                self.trader.available_cash,
            )
            quantity = notional / entry_price if entry_price > 0 else 0.0
            if self.order_executor.mode == "LIVE":
                quantity = quantize_asset_quantity(symbol, quantity)
            decision = self.risk.evaluate_trade("BUY", symbol, quantity, entry_price, now=now)
            if not decision.approved:
                payload = {"approved": False, "side": "BUY", "symbol": symbol, "reason": decision.reason}
                self.database.log_rejected_trade(symbol, payload)
                self._record_live_event(symbol, "rejected", "BUY", entry_price, decision.reason, now)
                self._notify(
                    "TRADE_REJECTED",
                    "Buy candidate was rejected by risk controls.",
                    NotificationSeverity.WARNING,
                    symbol=symbol,
                    details={
                        "action": "BUY_CANDIDATE",
                        "reason": decision.reason,
                        "mode": self.order_executor.mode,
                    },
                    now=now,
                )
                return [payload]
            execution = self._submit_order(symbol, "BUY", quantity, entry_price, now)
            if not execution.submitted and self.order_executor.mode == "LIVE":
                payload = {
                    "approved": False,
                    "side": "BUY",
                    "symbol": symbol,
                    "reason": "live order submission failed",
                }
                self.database.log_rejected_trade(symbol, payload)
                return [payload]
            trade = self.trader.buy(symbol, quantity, entry_price)
            self.highest_since_entry[symbol] = entry_price
            dip_reference = self._recent_low_before_entry(symbol)
            self.dip_reference_by_symbol[symbol] = dip_reference
            momentum_payload = self.momentum_scores.get(symbol, {})
            target_exit_price = entry_price * (1 + self.momentum_config.target_profit_pct / 100)
            metadata = {
                "lot_id": trade.trade_id,
                "simulation_run_id": self.simulation_run_id,
                "symbol": symbol,
                "entry_time": now.isoformat(),
                "entry_price": entry_price,
                "quantity": trade.quantity,
                "signal_price": price,
                "quote": self._quote_payload(symbol, price),
                "dip_reference_price": dip_reference,
                "entry_score": momentum_payload.get("score", 0.0),
                "peak_score": momentum_payload.get("score", 0.0),
                "target_profit_pct": self.momentum_config.target_profit_pct,
                "target_exit_price": target_exit_price,
                "position_size": trade.notional,
                "strategy_name": "30s_momentum_confidence",
                "risk_parameters": {
                    "max_trade_size": self.risk.max_trade_size,
                    "max_daily_loss": self.risk.max_daily_loss,
                    "max_trades_per_day": self.risk.max_trades_per_day,
                    "catastrophic_loss_pct": self.momentum_config.catastrophic_loss_pct,
                    "soft_stop_loss_pct": self.momentum_config.soft_stop_loss_pct,
                    "spread_pct": self._quote_payload(symbol, price).get("spread_pct", 0.0),
                },
            }
            self.open_trade_metadata.setdefault(symbol, []).append(metadata)
            self.risk.record_trade_result(trade.realized_pl)
            payload = {"approved": True, **asdict(trade), **metadata}
            payload["execution"] = asdict(execution)
            self.database.log_simulated_trade(symbol, payload)
            self._notify(
                "HIGH_CONFIDENCE_TRADE",
                "High-confidence paper buy candidate detected.",
                NotificationSeverity.INFO,
                symbol=symbol,
                details={
                    "action": "BUY_CANDIDATE",
                    "momentum_score": round(float(momentum_payload.get("score", 0.0)), 2),
                    "mode": self.order_executor.mode,
                    "status": self._order_status(execution.submitted),
                },
                now=now,
            )
            self._notify(
                self._trade_event_type(),
                self._trade_message("buy"),
                NotificationSeverity.INFO,
                symbol=symbol,
                details={
                    "side": "BUY",
                    "lot_id": trade.trade_id,
                    "momentum_score": round(float(momentum_payload.get("score", 0.0)), 2),
                    "target_profit_pct": round(float(self.momentum_config.target_profit_pct), 4),
                    "mode": self.order_executor.mode,
                    "status": self._order_status(execution.submitted),
                    "order_id": execution.order_id,
                    "client_order_id": execution.client_order_id,
                },
                now=now,
            )
            self._record_live_event(
                symbol,
                "trade",
                "BUY",
                entry_price,
                f"{self._trade_prefix()} lot {len(self.open_trade_metadata[symbol])} buy "
                f"{trade.quantity:.8f} units; momentum score "
                f"{momentum_payload.get('score', 0.0):.1f}; target {target_exit_price:.6f}",
                now,
            )
            return [payload]

        if signal == Signal.SELL and symbol in self.trader.positions:
            exit_price = self._execution_price(symbol, "SELL", price)
            momentum_payload = self.momentum_scores.get(symbol, {})
            remaining_lots: List[Dict[str, Any]] = []
            for metadata in self.open_trade_metadata.get(symbol, []):
                exit_reason = self._lot_exit_reason(
                    metadata,
                    self.momentum_scores_to_score(symbol),
                    exit_price,
                )
                if not exit_reason:
                    remaining_lots.append(metadata)
                    continue
                quantity = float(metadata["quantity"])
                if self.order_executor.mode == "LIVE":
                    quantity = quantize_asset_quantity(symbol, quantity)
                decision = self.risk.evaluate_trade("SELL", symbol, quantity, exit_price, now=now)
                if not decision.approved:
                    payload = {
                        "approved": False,
                        "side": "SELL",
                        "symbol": symbol,
                        "lot_id": metadata["lot_id"],
                        "reason": decision.reason,
                    }
                    self.database.log_rejected_trade(symbol, payload)
                    self._record_live_event(symbol, "rejected", "SELL", exit_price, decision.reason, now)
                    self._notify(
                        "TRADE_REJECTED",
                        "Sell candidate was rejected by risk controls.",
                        NotificationSeverity.WARNING,
                        symbol=symbol,
                        details={
                            "action": "SELL_CANDIDATE",
                            "reason": decision.reason,
                            "mode": self.order_executor.mode,
                        },
                        now=now,
                    )
                    results.append(payload)
                    remaining_lots.append(metadata)
                    continue
                execution = self._submit_order(symbol, "SELL", quantity, exit_price, now)
                if not execution.submitted and self.order_executor.mode == "LIVE":
                    payload = {
                        "approved": False,
                        "side": "SELL",
                        "symbol": symbol,
                        "lot_id": metadata["lot_id"],
                        "reason": "live order submission failed",
                    }
                    self.database.log_rejected_trade(symbol, payload)
                    results.append(payload)
                    remaining_lots.append(metadata)
                    continue
                trade = self.trader.sell_lot(
                    symbol,
                    quantity,
                    exit_price,
                    float(metadata["entry_price"]),
                )
                hold_time_seconds = (
                    now - datetime.fromisoformat(str(metadata["entry_time"]))
                ).total_seconds()
                self.risk.record_trade_result(trade.realized_pl)
                payload = {
                    "approved": True,
                    **asdict(trade),
                    "lot_id": metadata["lot_id"],
                    "simulation_run_id": self.simulation_run_id,
                    "entry_score": metadata.get("entry_score"),
                    "peak_score": metadata.get("peak_score"),
                    "exit_score": momentum_payload.get("score"),
                    "target_exit_price": metadata.get("target_exit_price"),
                    "target_reached": exit_reason == "TARGET_PROFIT",
                    "exit_reason": exit_reason,
                    "signal_price": price,
                    "quote": self._quote_payload(symbol, price),
                    "hold_time_seconds": hold_time_seconds,
                    "execution": asdict(execution),
                }
                self.database.log_simulated_trade(symbol, payload)
                self._not_going_well_alerted_lots.discard(str(metadata["lot_id"]))
                self._notify(
                    self._trade_event_type(),
                    self._trade_message("sell"),
                    NotificationSeverity.INFO,
                    symbol=symbol,
                    details={
                        "side": "SELL",
                        "lot_id": metadata["lot_id"],
                        "exit_reason": exit_reason,
                        "realized_pl_pct": round(
                            ((exit_price - float(metadata["entry_price"])) / float(metadata["entry_price"])) * 100,
                            4,
                        ),
                        "mode": self.order_executor.mode,
                        "status": self._order_status(execution.submitted),
                        "order_id": execution.order_id,
                        "client_order_id": execution.client_order_id,
                    },
                    now=now,
                )
                self._notify_exit(symbol, exit_reason, momentum_payload, now)
                self._record_live_event(
                    symbol,
                    "trade",
                    "SELL",
                    exit_price,
                    f"{self._trade_prefix()} lot sell realized ${trade.realized_pl:.2f}; {exit_reason}",
                    now,
                )
                results.append(payload)
            if remaining_lots:
                self.open_trade_metadata[symbol] = remaining_lots
            else:
                self.open_trade_metadata.pop(symbol, None)
                self.highest_since_entry.pop(symbol, None)
                self.dip_reference_by_symbol.pop(symbol, None)
            return results

        return results

    def _submit_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        now: datetime,
    ) -> ExecutionResult:
        try:
            execution = self.order_executor.submit_market_order(symbol, side, quantity)
        except RobinhoodAPIError as exc:
            self.last_error = str(exc)
            payload = {
                "side": side,
                "quantity": quantity,
                "price": price,
                "error": self.last_error,
                "status_code": exc.status_code,
                "mode": self.order_executor.mode,
            }
            self.database.log_api_error(payload, symbol)
            self._record_live_event(symbol, "api_error", side, price, self.last_error, now)
            self._notify(
                "LIVE_ORDER_FAILED",
                "Live order submission failed before local position state changed.",
                NotificationSeverity.CRITICAL,
                symbol=symbol,
                details=payload,
                now=now,
            )
            return ExecutionResult(submitted=False, mode=self.order_executor.mode)
        if execution.submitted:
            self.database.log_event(
                "live_order",
                {
                    "side": side,
                    "quantity": quantity,
                    "price": price,
                    "order_id": execution.order_id,
                    "client_order_id": execution.client_order_id,
                    "response": execution.raw_response or {},
                    "mode": execution.mode,
                },
                symbol,
            )
            self._record_live_event(
                symbol,
                "live_order",
                side,
                price,
                f"submitted live {side.lower()} order {execution.order_id or execution.client_order_id}",
                now,
            )
        return execution

    def _trade_event_type(self) -> str:
        return "LIVE_TRADE_SUBMITTED" if self.order_executor.mode == "LIVE" else "PAPER_TRADE_EXECUTED"

    def _trade_message(self, side: str) -> str:
        if self.order_executor.mode == "LIVE":
            return f"Live {side} order submitted; local ledger mirrored after acceptance."
        return f"Paper {side} executed in shadow mode."

    def _trade_prefix(self) -> str:
        return "live-mirrored" if self.order_executor.mode == "LIVE" else "paper"

    def _order_status(self, submitted: bool) -> str:
        if self.order_executor.mode == "LIVE":
            return "Live order submitted" if submitted else "Live trading armed"
        return "Paper trade only - no live order submitted"

    def _execution_price(self, symbol: str, side: str, fallback_price: float) -> float:
        if self.order_executor.mode != "LIVE":
            return fallback_price
        snapshot = self.cached_quote_snapshots.get(symbol)
        if not snapshot:
            return fallback_price
        if side.upper() == "BUY" and snapshot.ask:
            return snapshot.ask
        if side.upper() == "SELL" and snapshot.bid:
            return snapshot.bid
        return fallback_price

    def _quote_payload(self, symbol: str, fallback_price: float) -> Dict[str, Any]:
        snapshot = self.cached_quote_snapshots.get(symbol)
        if not snapshot:
            return {
                "midpoint": fallback_price,
                "bid": None,
                "ask": None,
                "spread_pct": 0.0,
            }
        return {
            "midpoint": snapshot.midpoint,
            "bid": snapshot.bid,
            "ask": snapshot.ask,
            "spread_pct": round(snapshot.spread_pct, 6),
        }

    def _can_open_lot(
        self,
        symbol: str,
        momentum: MomentumScore,
        now: Optional[datetime] = None,
    ) -> bool:
        if momentum.score < self.momentum_config.entry_threshold:
            return False
        lots = self.open_trade_metadata.get(symbol, [])
        if len(lots) >= self.momentum_config.max_open_trades:
            return False
        if not lots:
            return True
        now = now or self.market_buffers[symbol].latest.timestamp
        latest_entry = max(datetime.fromisoformat(str(lot["entry_time"])) for lot in lots)
        return (now - latest_entry).total_seconds() >= self.momentum_config.entry_interval_seconds

    def _lot_exit_reason(
        self,
        lot: Dict[str, Any],
        momentum: MomentumScore,
        price: float,
    ) -> Optional[str]:
        return self.momentum_strategy.exit_reason(
            momentum,
            float(lot["entry_price"]),
            price,
            float(lot["target_exit_price"]),
        )

    def update_control_stack(self, control_stack: ControlStack) -> None:
        control_stack.validate()
        self.control_stack = control_stack

    def update_momentum_config(self, momentum_config: MomentumConfig) -> None:
        self.momentum_config = momentum_config
        self.momentum_strategy = MomentumStrategy(momentum_config)

    def update_risk_controls(
        self,
        max_trade_size: float,
        max_daily_loss: float,
        max_trades_per_day: int,
        cooldown_after_loss_seconds: int,
        kill_switch: bool,
    ) -> None:
        self.risk.max_trade_size = max_trade_size
        self.risk.max_daily_loss = max_daily_loss
        self.risk.max_trades_per_day = max_trades_per_day
        self.risk.cooldown_after_loss_seconds = cooldown_after_loss_seconds
        self.risk.kill_switch = kill_switch

    def pause(self, reason: str = "manual pause") -> Dict[str, Any]:
        self.manual_pause = True
        self.manual_pause_reason = reason.strip() or "manual pause"
        self._record_manual_event("PAUSE", self.manual_pause_reason)
        self._notify(
            "MANUAL_CONTROL",
            "Trading loop paused by manual control.",
            NotificationSeverity.WARNING,
            details={
                "action": "PAUSE",
                "reason": self.manual_pause_reason,
                "mode": self.order_executor.mode,
            },
        )
        return {"ok": True, "action": "pause", "paused": True, "reason": self.manual_pause_reason}

    def resume(self, reason: str = "manual resume") -> Dict[str, Any]:
        self.manual_pause = False
        self.manual_pause_reason = ""
        self._record_manual_event("RESUME", reason.strip() or "manual resume")
        self._notify(
            "MANUAL_CONTROL",
            "Trading loop resumed by manual control.",
            NotificationSeverity.INFO,
            details={"action": "RESUME", "mode": self.order_executor.mode},
        )
        return {"ok": True, "action": "resume", "paused": False}

    def set_kill_switch(self, enabled: bool, reason: str = "manual control") -> Dict[str, Any]:
        self.risk.kill_switch = bool(enabled)
        if not enabled:
            self._kill_switch_alerted = False
        action = "KILL_ON" if enabled else "KILL_OFF"
        self._record_manual_event(action, reason.strip() or "manual control")
        self._notify(
            "MANUAL_CONTROL",
            f"Risk kill switch {'enabled' if enabled else 'disabled'} by manual control.",
            NotificationSeverity.CRITICAL if enabled else NotificationSeverity.INFO,
            details={"action": action, "mode": self.order_executor.mode},
        )
        return {"ok": True, "action": "kill_switch", "killSwitch": self.risk.kill_switch}

    def force_exit(
        self,
        symbol: Optional[str] = None,
        reason: str = "manual force exit",
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        symbols = [symbol.upper()] if symbol else sorted(self.trader.positions.keys())
        trades: List[Dict[str, Any]] = []
        errors: List[Dict[str, str]] = []
        for target_symbol in symbols:
            if "-" not in target_symbol:
                target_symbol = f"{target_symbol}-USD"
            position = self.trader.positions.get(target_symbol)
            if not position:
                errors.append({"symbol": target_symbol, "error": "no open position"})
                continue
            price = self.latest_prices.get(target_symbol, position.average_price)
            lots = self.open_trade_metadata.get(target_symbol, [])
            if lots:
                remaining_lots: List[Dict[str, Any]] = []
                for metadata in list(lots):
                    quantity = float(metadata.get("quantity", 0.0))
                    if quantity <= 0:
                        remaining_lots.append(metadata)
                        continue
                    execution = self._submit_order(target_symbol, "SELL", quantity, price, now)
                    if not execution.submitted and self.order_executor.mode == "LIVE":
                        errors.append({"symbol": target_symbol, "error": "live order submission failed"})
                        remaining_lots.append(metadata)
                        continue
                    trade = self.trader.sell_lot(
                        target_symbol,
                        quantity,
                        price,
                        float(metadata["entry_price"]),
                    )
                    self.risk.record_trade_result(trade.realized_pl)
                    payload = {
                        "approved": True,
                        **asdict(trade),
                        "lot_id": metadata.get("lot_id"),
                        "exit_reason": "MANUAL_FORCE_EXIT",
                        "manual_reason": reason,
                        "execution": asdict(execution),
                    }
                    self.database.log_simulated_trade(target_symbol, payload)
                    trades.append(payload)
                if remaining_lots:
                    self.open_trade_metadata[target_symbol] = remaining_lots
                else:
                    self.open_trade_metadata.pop(target_symbol, None)
            else:
                execution = self._submit_order(target_symbol, "SELL", position.quantity, price, now)
                if not execution.submitted and self.order_executor.mode == "LIVE":
                    errors.append({"symbol": target_symbol, "error": "live order submission failed"})
                    continue
                trade = self.trader.sell(target_symbol, position.quantity, price)
                self.risk.record_trade_result(trade.realized_pl)
                payload = {
                    "approved": True,
                    **asdict(trade),
                    "exit_reason": "MANUAL_FORCE_EXIT",
                    "manual_reason": reason,
                    "execution": asdict(execution),
                }
                self.database.log_simulated_trade(target_symbol, payload)
                trades.append(payload)
                self.open_trade_metadata.pop(target_symbol, None)
            if target_symbol not in self.open_trade_metadata:
                self.highest_since_entry.pop(target_symbol, None)
                self.dip_reference_by_symbol.pop(target_symbol, None)
            self._record_live_event(
                target_symbol,
                "manual",
                "SELL",
                price,
                f"manual force exit; {reason}",
                now,
            )
        severity = NotificationSeverity.WARNING if trades else NotificationSeverity.INFO
        self._notify(
            "MANUAL_CONTROL",
            "Manual force-exit command processed.",
            severity,
            symbol=symbol,
            details={
                "action": "FORCE_EXIT",
                "closed_trades": len(trades),
                "errors": len(errors),
                "mode": self.order_executor.mode,
            },
            now=now,
        )
        return {"ok": not errors or bool(trades), "action": "force_exit", "trades": trades, "errors": errors}

    def _record_manual_event(self, action: str, details: str) -> None:
        now = datetime.now(timezone.utc)
        self._append_event(
            {
                "step": 0,
                "type": "manual",
                "symbol": "SYSTEM",
                "signal": action,
                "price": 0,
                "details": details,
                "timestamp": now.isoformat(),
            }
        )

    def momentum_scores_to_score(self, symbol: str) -> MomentumScore:
        return self.momentum_strategy.score(self.market_buffers[symbol])

    def simulation_summary(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        return {
            "simulation_run_id": self.simulation_run_id,
            "started_at": self.started_at.isoformat(),
            "ended_at": now.isoformat(),
            "duration_seconds": (now - self.started_at).total_seconds(),
            "portfolio_value": self.trader.portfolio_value(self.latest_prices),
            "cash": self.trader.cash,
            "realized_pl": self.trader.realized_pl(),
            "unrealized_pl": self.trader.unrealized_pl(self.latest_prices),
            "gain_reserve": self.trader.gain_reserve,
            "trade_count": len(self.trader.trade_history),
            "symbols": self.symbols,
        }

    def _persist_long_run_summary(self, now: datetime) -> None:
        duration = (now - self.started_at).total_seconds()
        window_seconds = self.session_window_minutes * 60
        window_index = int(duration // window_seconds)
        if duration >= window_seconds and window_index not in self.persisted_summary_windows:
            summary = self.simulation_summary(now)
            summary["window_index"] = window_index
            summary["session_window_minutes"] = self.session_window_minutes
            summary["session_window_start"] = (
                self.started_at + timedelta(seconds=(window_index - 1) * window_seconds)
            ).isoformat()
            summary["session_window_end"] = (
                self.started_at + timedelta(seconds=window_index * window_seconds)
            ).isoformat()
            self.database.log_simulation_summary(
                f"{self.simulation_run_id}:{window_index}",
                summary,
            )
            self.last_persisted_summary_at = now
            self.persisted_summary_windows.add(window_index)

    def _recent_low_before_entry(self, symbol: str) -> float:
        history = self.price_history.get(symbol, [])
        if not history:
            return 0.0
        window = history[-self.control_stack.lookback_window :]
        return min(window) if window else history[-1]

    def _record_quote_candle(self, symbol: str, price: float, now: datetime, step: int) -> None:
        candles = self.candle_history.setdefault(symbol, [])
        previous_close = candles[-1]["close"] if candles else price
        open_price = previous_close
        candle = {
            "step": step,
            "timestamp": now.isoformat(),
            "open": open_price,
            "high": max(open_price, price),
            "low": min(open_price, price),
            "close": price,
        }
        candles.append(candle)

    def _record_live_event(
        self,
        symbol: str,
        event_type: str,
        signal: str,
        price: float,
        details: str,
        now: datetime,
    ) -> None:
        self._append_event(
            {
                "step": self.tick_counts.get(symbol, len(self.price_history.get(symbol, []))),
                "type": event_type,
                "symbol": symbol,
                "signal": signal,
                "price": price,
                "details": details,
                "timestamp": now.isoformat(),
            }
        )

    def _append_event(self, event: Dict[str, Any]) -> None:
        self.event_history.append(event)
        if len(self.event_history) > self.max_event_history:
            del self.event_history[: len(self.event_history) - self.max_event_history]

    def _notify(
        self,
        event_type: str,
        message: str,
        severity: NotificationSeverity,
        symbol: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        now: Optional[datetime] = None,
    ) -> None:
        if not self.notifier:
            return
        self.notifier.notify(
            event_type,
            message,
            severity,
            symbol=symbol,
            details=details or {},
            now=now,
        )

    def _notify_exit(
        self,
        symbol: str,
        exit_reason: str,
        momentum_payload: Dict[str, Any],
        now: datetime,
    ) -> None:
        event_type = {
            "TARGET_PROFIT": "TARGET_PROFIT_REACHED",
            "MOMENTUM_WEAKENED": "MOMENTUM_WEAKENED",
            "CATASTROPHIC_LOSS": "CATASTROPHIC_LOSS_THRESHOLD_REACHED",
            "SOFT_STOP_LOSS": "RISK_MANAGER_FORCED_EXIT",
        }.get(exit_reason, "RISK_MANAGER_FORCED_EXIT")
        severity = (
            NotificationSeverity.CRITICAL
            if event_type in {"CATASTROPHIC_LOSS_THRESHOLD_REACHED", "RISK_MANAGER_FORCED_EXIT"}
            else NotificationSeverity.WARNING
        )
        self._notify(
            event_type,
            "Position exit condition was reached.",
            severity,
            symbol=symbol,
            details={
                "exit_reason": exit_reason,
                "momentum_score": round(float(momentum_payload.get("score", 0.0) or 0.0), 2),
                "mode": self.order_executor.mode,
                "status": self._order_status(self.order_executor.mode == "LIVE"),
            },
            now=now,
        )

    def _notify_struggling_lots(
        self,
        symbol: str,
        momentum: MomentumScore,
        price: float,
        now: datetime,
    ) -> None:
        lots = self.open_trade_metadata.get(symbol, [])
        if not lots:
            return
        soft_stop_pct = float(self.momentum_config.soft_stop_loss_pct)
        warning_threshold = soft_stop_pct / 2 if soft_stop_pct < 0 else -0.5
        warning_threshold = min(-0.25, warning_threshold)
        for lot in lots:
            lot_id = str(lot["lot_id"])
            if lot_id in self._not_going_well_alerted_lots:
                continue
            entry_price = float(lot["entry_price"])
            if entry_price <= 0:
                continue
            unrealized_pct = ((price - entry_price) / entry_price) * 100
            momentum_weak = momentum.score <= self.momentum_config.exit_threshold
            price_weak = unrealized_pct <= warning_threshold
            if not (price_weak or momentum_weak):
                continue
            reasons = []
            if price_weak:
                reasons.append("unrealized return below warning threshold")
            if momentum_weak:
                reasons.append("momentum below exit threshold")
            self._notify(
                "TRADE_NOT_GOING_WELL",
                "Position needs attention.",
                NotificationSeverity.WARNING,
                symbol=symbol,
                details={
                    "lot_id": lot_id,
                    "unrealized_pl_pct": round(unrealized_pct, 4),
                    "momentum_score": round(float(momentum.score), 2),
                    "reason": "; ".join(reasons),
                    "mode": self.order_executor.mode,
                    "status": "Monitoring open position",
                },
                now=now,
            )
            self._not_going_well_alerted_lots.add(lot_id)

    def _notify_hourly_performance(self, now: datetime) -> None:
        self._notify(
            "HOURLY_PERFORMANCE_UPDATE",
            "Hourly paper trading performance update.",
            NotificationSeverity.INFO,
            details=self.performance_summary_details(now),
            now=now,
        )

    def _notify_trade_status_card(self, now: datetime) -> None:
        details = self._latest_sql_card_details(window_minutes=30)
        details["cadence"] = "30 minutes"
        self._notify(
            "TRADE_STATUS_UPDATE",
            "30-minute paper trade status card.",
            NotificationSeverity.INFO,
            details=details,
            now=now,
        )

    def _notify_hourly_recap_card(self, now: datetime) -> None:
        details = self._latest_sql_card_details(window_minutes=60)
        details["cadence"] = "hourly"
        self._notify(
            "HOURLY_RECAP_CARD",
            "Hourly paper trading recap card.",
            NotificationSeverity.INFO,
            details=details,
            now=now,
        )

    def _latest_sql_card_details(self, window_minutes: int) -> Dict[str, Any]:
        sessions = self.database.trade_sessions(
            starting_cash=self.trader.starting_cash,
            window_minutes=window_minutes,
            limit=1,
        )
        if not sessions:
            return {
                "mode": self.order_executor.mode,
                "window_minutes": window_minutes,
                "status": "No saved trade session yet",
                "symbols": ",".join(self.symbols),
                "open_positions": ",".join(sorted(self.trader.positions.keys())) or "none",
                "trades": len(self.trader.trade_history),
            }
        session = sessions[0]
        rejected = session.get("rejectedTradesByReason", {}) or {}
        rejected_text = (
            ", ".join(f"{reason}: {count}" for reason, count in rejected.items())
            if rejected
            else "None"
        )
        status = "Complete" if session.get("isComplete") else "In progress"
        outcome = "Winning Session" if session.get("outcome") == "winning" else "Non-Winning Session"
        return {
            "mode": self.order_executor.mode,
            "window_minutes": window_minutes,
            "session_range": f"{session.get('start')} to {session.get('end')}",
            "symbols": ",".join(session.get("symbols", [])) or "No symbol",
            "status": status,
            "outcome": outcome,
            "total_return_pct": round(float(session.get("totalReturn", 0.0) or 0.0), 4),
            "realized_pl": round(float(session.get("realizedPl", 0.0) or 0.0), 4),
            "max_drawdown_pct": round(float(session.get("maxDrawdown", 0.0) or 0.0), 4),
            "win_rate_pct": round(float(session.get("winRate", 0.0) or 0.0), 2),
            "average_win": round(float(session.get("averageWin", 0.0) or 0.0), 4),
            "average_loss": round(float(session.get("averageLoss", 0.0) or 0.0), 4),
            "trades": int(session.get("numberOfTrades", 0) or 0),
            "best_trade": round(float(session.get("bestTrade", 0.0) or 0.0), 4),
            "worst_trade": round(float(session.get("worstTrade", 0.0) or 0.0), 4),
            "buys": int(session.get("buyCount", 0) or 0),
            "open_lots": int(session.get("openLots", 0) or 0),
            "rejected": rejected_text,
            "order_mode": self.order_executor.mode,
        }

    def performance_summary_details(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        closed_trades = [trade for trade in self.trader.trade_history if trade.side.upper() == "SELL"]
        buy_count = len([trade for trade in self.trader.trade_history if trade.side.upper() == "BUY"])
        wins = [trade for trade in closed_trades if trade.realized_pl > 0]
        losses = [trade for trade in closed_trades if trade.realized_pl < 0]
        realized_pl = self.trader.realized_pl()
        unrealized_pl = self.trader.unrealized_pl(self.latest_prices)
        starting_cash = self.trader.starting_cash if self.trader.starting_cash > 0 else 1.0
        open_symbols = sorted(self.trader.positions.keys())
        realized_values = [trade.realized_pl for trade in closed_trades]
        gross_profit = sum(value for value in realized_values if value > 0)
        gross_loss = abs(sum(value for value in realized_values if value < 0))
        portfolio_value = self.trader.portfolio_value(self.latest_prices)
        open_exposure = sum(
            position.quantity * self.latest_prices.get(symbol, position.average_price)
            for symbol, position in self.trader.positions.items()
        )
        total_return_pct = ((portfolio_value - self.trader.starting_cash) / starting_cash) * 100
        best_trade = max(realized_values) if realized_values else 0.0
        worst_trade = min(realized_values) if realized_values else 0.0
        return {
            "mode": self.order_executor.mode,
            "runtime_minutes": round((now - self.started_at).total_seconds() / 60, 2),
            "symbols_watched": len(self.symbols),
            "open_positions": ",".join(open_symbols) if open_symbols else "none",
            "open_exposure": round(open_exposure, 4),
            "open_exposure_pct": round((open_exposure / starting_cash) * 100, 4),
            "buy_count": buy_count,
            "closed_trade_count": len(closed_trades),
            "win_count": len(wins),
            "loss_count": len(losses),
            "win_rate_pct": round((len(wins) / len(closed_trades) * 100) if closed_trades else 0.0, 2),
            "average_win": round((gross_profit / len(wins)) if wins else 0.0, 4),
            "average_loss": round((-gross_loss / len(losses)) if losses else 0.0, 4),
            "profit_factor": round((gross_profit / gross_loss) if gross_loss else (gross_profit if gross_profit else 0.0), 4),
            "best_trade": round(best_trade, 4),
            "worst_trade": round(worst_trade, 4),
            "total_return_pct": round(total_return_pct, 4),
            "realized_pl_pct": round((realized_pl / starting_cash) * 100, 4),
            "unrealized_pl_pct": round((unrealized_pl / starting_cash) * 100, 4),
            "realized_pl": round(realized_pl, 4),
            "unrealized_pl": round(unrealized_pl, 4),
            "portfolio_value": round(portfolio_value, 4),
            "status": self._order_status(False),
        }

    def _trim_symbol_history(self, symbol: str) -> None:
        for history_by_symbol in (
            self.price_history,
            self.quote_history,
            self.candle_history,
        ):
            history = history_by_symbol.get(symbol)
            if history and len(history) > self.max_history_points:
                del history[: len(history) - self.max_history_points]


def extract_best_bid_ask_prices(payload: Dict[str, Any]) -> Dict[str, float]:
    return {
        symbol: snapshot.midpoint
        for symbol, snapshot in extract_quote_snapshots(payload).items()
    }


def extract_quote_snapshots(payload: Dict[str, Any]) -> Dict[str, QuoteSnapshot]:
    if isinstance(payload, list):
        results = payload
    else:
        results = payload.get("results", [])
    snapshots: Dict[str, QuoteSnapshot] = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        symbol = _symbol_from_quote(item)
        if not symbol:
            continue
        bid = _first_number(
            item,
            ("bid_price", "bid", "best_bid", "bid_inclusive_of_sell_spread"),
        )
        ask = _first_number(
            item,
            ("ask_price", "ask", "best_ask", "ask_inclusive_of_buy_spread"),
        )
        price = (bid + ask) / 2 if bid and ask else _first_number(
            item,
            ("price", "mark_price", "mid_price", "last_trade_price"),
        )
        if price and price > 0:
            snapshots[symbol] = QuoteSnapshot(
                symbol=symbol,
                midpoint=price,
                bid=bid,
                ask=ask,
            )
    return snapshots


def _symbol_from_quote(item: Dict[str, Any]) -> str:
    for key in ("symbol", "trading_pair", "pair"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
    asset_code = item.get("asset_code") or item.get("asset")
    if isinstance(asset_code, str) and asset_code.strip():
        code = asset_code.strip().upper()
        return code if "-" in code else f"{code}-USD"
    return ""


def _first_number(item: Dict[str, Any], keys: Iterable[str]) -> Optional[float]:
    for key in keys:
        value = item.get(key)
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            return number
    return None
