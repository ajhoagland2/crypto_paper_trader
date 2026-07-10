import base64
import hashlib
import hmac
import json
import mimetypes
import secrets
import smtplib
import time
from dataclasses import asdict
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional

from src.backtest.backtestEngine import run_backtest
from src.backtest.models import BacktestConfig
from src.backtest.serializers import (
    parse_datetime,
    parse_points,
    result_to_dict,
    walk_forward_to_dict,
)
from src.backtest.walkForwardEngine import run_walk_forward
from src.config import Settings, get_settings
from src.data.database import ResearchDatabase
from src.data.market_data_collector import MarketDataCollector
from src.notifications.discord_control import DiscordControlBot, DiscordControlConfig
from src.notifications.notifier import build_notifier
from src.notifications.sms import SmsConfig, SmsNotifier
from src.risk.risk_manager import RiskManager
from src.robinhood.client import RetryConfig, RobinhoodAPIError, RobinhoodClient
from src.strategy.control_stack import ControlStack
from src.strategy.momentum import MomentumConfig, MomentumWeights
from src.trading.execution import PaperOrderExecutor, RobinhoodLiveOrderExecutor
from src.trading.live_paper_runner import LivePaperRunner
from src.trading.paper_trader import PaperTrader


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


def _hash_password(password: str, salt: Optional[bytes] = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 260_000)
    return (
        "pbkdf2_sha256$260000$"
        f"{base64.urlsafe_b64encode(salt).decode('ascii')}$"
        f"{base64.urlsafe_b64encode(digest).decode('ascii')}"
    )


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations, raw_salt, raw_digest = password_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(raw_salt.encode("ascii"))
        expected = base64.urlsafe_b64decode(raw_digest.encode("ascii"))
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            int(iterations),
        )
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


class AuthManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self.reset_tokens: Dict[str, Dict[str, Any]] = {}
        self.store_path = self._resolve_store_path(settings.web_auth_store_path)

    def refresh_settings(self) -> None:
        self.settings = get_settings()
        self.store_path = self._resolve_store_path(self.settings.web_auth_store_path)

    def is_enabled(self) -> bool:
        return self.settings.web_auth_enabled

    def configured(self) -> bool:
        return bool(self._credentials().get("email") and self._credentials().get("password_hash"))

    def login(self, email: str, password: str) -> Optional[str]:
        credentials = self._credentials()
        configured_email = str(credentials.get("email", "")).strip().lower()
        password_hash = str(credentials.get("password_hash", ""))
        if not configured_email or not password_hash:
            return None
        if email.strip().lower() != configured_email:
            return None
        if not _verify_password(password, password_hash):
            return None
        token = secrets.token_urlsafe(32)
        self.sessions[token] = {
            "email": configured_email,
            "expires_at": time.time() + self.settings.web_auth_session_seconds,
        }
        return token

    def logout(self, token: str) -> None:
        self.sessions.pop(token, None)

    def user_for_session(self, token: str) -> Optional[str]:
        session = self.sessions.get(token)
        if not session:
            return None
        if float(session.get("expires_at", 0)) <= time.time():
            self.sessions.pop(token, None)
            return None
        return str(session.get("email", ""))

    def start_password_reset(self, email: str) -> Dict[str, Any]:
        credentials = self._credentials()
        configured_email = str(credentials.get("email", "")).strip().lower()
        if not configured_email or email.strip().lower() != configured_email:
            return {"ok": True, "sent": False}
        token = secrets.token_urlsafe(32)
        self.reset_tokens[token] = {
            "email": configured_email,
            "expires_at": time.time() + self.settings.web_auth_reset_token_seconds,
        }
        sent = self._send_recovery_email(configured_email, token)
        response = {"ok": True, "sent": sent}
        if not sent:
            response["resetToken"] = token
            response["message"] = "SMTP is not configured; use this local reset token."
        return response

    def reset_password(self, token: str, new_password: str) -> bool:
        reset = self.reset_tokens.get(token)
        if not reset or float(reset.get("expires_at", 0)) <= time.time():
            self.reset_tokens.pop(token, None)
            return False
        if len(new_password) < 8:
            return False
        email = str(reset.get("email", ""))
        self._save_credentials(email, _hash_password(new_password))
        self.reset_tokens.pop(token, None)
        self.sessions.clear()
        return True

    def _credentials(self) -> Dict[str, str]:
        store = self._read_store()
        email = store.get("email") or self.settings.web_auth_email
        password_hash = store.get("password_hash") or self.settings.web_auth_password_hash
        if not password_hash and self.settings.web_auth_password:
            password_hash = _hash_password(self.settings.web_auth_password)
        return {"email": email, "password_hash": password_hash}

    def _read_store(self) -> Dict[str, str]:
        if not self.store_path.exists():
            return {}
        try:
            payload = json.loads(self.store_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return {
            "email": str(payload.get("email", "")),
            "password_hash": str(payload.get("password_hash", "")),
        }

    def _save_credentials(self, email: str, password_hash: str) -> None:
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self.store_path.write_text(
            json.dumps({"email": email, "password_hash": password_hash}, indent=2),
            encoding="utf-8",
        )

    def _resolve_store_path(self, raw_path: str) -> Path:
        path = Path(raw_path)
        if path.is_absolute():
            return path.resolve()
        return (ROOT / path).resolve()

    def _send_recovery_email(self, email: str, token: str) -> bool:
        settings = self.settings
        if not settings.smtp_host or not settings.smtp_username:
            return False
        message = EmailMessage()
        message["Subject"] = "Crypto Research password reset"
        message["From"] = settings.smtp_username
        message["To"] = email
        message.set_content(
            "Use this reset token in the Crypto Research sign-in screen:\n\n"
            f"{token}\n\nThis token expires soon."
        )
        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
                smtp.starttls()
                if settings.smtp_password:
                    smtp.login(settings.smtp_username, settings.smtp_password)
                smtp.send_message(message)
            return True
        except Exception:
            return False


class WebAppState:
    def __init__(self) -> None:
        self.settings: Settings = get_settings()
        self.database: Optional[ResearchDatabase] = None
        self.runner: Optional[LivePaperRunner] = None
        self.collector: Optional[MarketDataCollector] = None
        self.discord_control: Optional[DiscordControlBot] = None
        self.auth = AuthManager(self.settings)

    def reset(self) -> None:
        if self.discord_control:
            self.discord_control.stop()
            self.discord_control = None
        if self.database:
            if self.runner:
                self.database.log_simulation_summary(
                    self.runner.simulation_run_id,
                    self.runner.simulation_summary(),
                )
            self.database.close()
        self.database = None
        self.runner = None
        self.settings = get_settings()
        self.auth.refresh_settings()

    def ensure_market_data_collector(self) -> Optional[MarketDataCollector]:
        self.settings = get_settings()
        if not self.settings.market_data_collector_enabled:
            if self.collector:
                self.collector.stop()
                self.collector = None
            return None
        if not self.settings.robinhood_api_key or not self.settings.robinhood_private_key:
            return None

        symbols = self.settings.symbol_list
        should_rebuild = (
            self.collector is None
            or self.collector.symbols != symbols
            or self.collector.database_path != self.settings.database_path
        )
        if should_rebuild:
            if self.collector:
                self.collector.stop()
            self.collector = MarketDataCollector(
                client=RobinhoodClient(
                    api_key=self.settings.robinhood_api_key,
                    private_key=self.settings.robinhood_private_key,
                    base_url=self.settings.robinhood_api_base_url,
                    retry_config=RetryConfig(attempts=1, backoff_seconds=0, timeout_seconds=6),
                ),
                database_path=self.settings.database_path,
                symbols=symbols,
                interval_seconds=self.settings.market_data_collector_interval_seconds,
            )
        self.collector.start()
        return self.collector

    def ensure_discord_control(self) -> Optional[DiscordControlBot]:
        self.settings = get_settings()
        if not self.settings.discord_control_enabled:
            if self.discord_control:
                self.discord_control.stop()
                self.discord_control = None
            return None
        config = DiscordControlConfig(
            enabled=self.settings.discord_control_enabled,
            bot_token=self.settings.discord_bot_token,
            channel_id=self.settings.discord_control_channel_id,
            poll_seconds=self.settings.discord_control_poll_seconds,
            prefix=self.settings.discord_control_prefix,
        )
        should_rebuild = (
            self.discord_control is None
            or self.discord_control.config != config
        )
        if should_rebuild:
            if self.discord_control:
                self.discord_control.stop()
            self.discord_control = DiscordControlBot(config, self.handle_discord_command)
        self.discord_control.start()
        return self.discord_control

    def get_runner(self, overrides: Optional[Dict[str, Any]] = None) -> LivePaperRunner:
        overrides = overrides or {}
        if self.runner is None:
            self.runner = self._build_runner(overrides)
        else:
            if self._symbols_from_overrides(overrides) != self.runner.symbols:
                self.reset()
                self.runner = self._build_runner(overrides)
                return self.runner
            if self._should_rebuild_empty_runner(self.runner, overrides):
                self.reset()
                self.runner = self._build_runner(overrides)
                return self.runner
            self._apply_overrides(self.runner, overrides)
        return self.runner

    def _should_rebuild_empty_runner(
        self, runner: LivePaperRunner, overrides: Dict[str, Any]
    ) -> bool:
        if runner.trader.trade_history or runner.trader.positions:
            return False
        desired_cash = float(overrides.get("startingCash", runner.trader.starting_cash))
        desired_reserve = float(overrides.get("gainReservePercent", runner.trader.gain_reserve_percent))
        return (
            desired_cash != runner.trader.starting_cash
            or desired_reserve != runner.trader.gain_reserve_percent
        )

    def _control_stack_from_overrides(self, overrides: Dict[str, Any]) -> ControlStack:
        return ControlStack(
            lookback_window=int(overrides.get("lookbackWindow", self.settings.lookback_window)),
            buy_dip_percent=float(overrides.get("buyDipPercent", self.settings.buy_dip_percent)),
            rebound_percent=float(overrides.get("reboundPercent", self.settings.rebound_percent)),
            sell_above_dip_percent=float(
                overrides.get("sellAboveDipPercent", self.settings.sell_above_dip_percent)
            ),
            stop_loss_percent=float(overrides.get("stopLossPercent", self.settings.stop_loss_percent)),
            trailing_stop_percent=float(
                overrides.get("trailingStopPercent", self.settings.trailing_stop_percent)
            ),
        )

    def _symbols_from_overrides(self, overrides: Dict[str, Any]) -> list[str]:
        raw_symbol = str(overrides.get("symbol", "")).strip().upper()
        if raw_symbol:
            if "-" not in raw_symbol:
                raw_symbol = f"{raw_symbol}-USD"
            return [raw_symbol]
        return [self.settings.symbol_list[0]]

    def _apply_overrides(self, runner: LivePaperRunner, overrides: Dict[str, Any]) -> None:
        self.settings = get_settings()
        runner.update_control_stack(self._control_stack_from_overrides(overrides))
        runner.update_momentum_config(self._momentum_config_from_overrides(overrides))
        runner.update_risk_controls(
            max_trade_size=self._effective_max_trade_size(overrides),
            max_daily_loss=float(overrides.get("maxDailyLoss", self.settings.max_daily_loss)),
            max_trades_per_day=int(overrides.get("maxTrades", self.settings.max_trades_per_day)),
            cooldown_after_loss_seconds=int(
                overrides.get("cooldownAfterLossSeconds", self.settings.cooldown_after_loss_seconds)
            ),
            kill_switch=bool(overrides.get("killSwitch", self.settings.kill_switch)),
        )

    def _build_runner(self, overrides: Dict[str, Any]) -> LivePaperRunner:
        self.settings = get_settings()
        if self.settings.live_trading_enabled and not self.settings.live_trading_acknowledged:
            raise RuntimeError(
                "Live order placement requires LIVE_TRADING_ACKNOWLEDGEMENT=I_UNDERSTAND_LIVE_ORDERS."
            )
        if not self.settings.symbol_list:
            raise RuntimeError("Set CRYPTO_SYMBOLS to at least one symbol.")

        control_stack = self._control_stack_from_overrides(overrides)
        momentum_config = self._momentum_config_from_overrides(overrides)
        symbols = self._symbols_from_overrides(overrides)
        self.database = ResearchDatabase(self.settings.database_path)
        client = RobinhoodClient(
            api_key=self.settings.robinhood_api_key,
            private_key=self.settings.robinhood_private_key,
            base_url=self.settings.robinhood_api_base_url,
            retry_config=RetryConfig(attempts=1, backoff_seconds=0, timeout_seconds=6),
        )
        max_trade_size = self._effective_max_trade_size(overrides)
        return LivePaperRunner(
            client=client,
            trader=PaperTrader(
                starting_cash=float(overrides.get("startingCash", self.settings.starting_cash)),
                gain_reserve_percent=float(
                    overrides.get("gainReservePercent", self.settings.gain_reserve_percent)
                ),
            ),
            risk=RiskManager(
                max_trade_size=max_trade_size,
                max_daily_loss=float(overrides.get("maxDailyLoss", self.settings.max_daily_loss)),
                max_trades_per_day=int(overrides.get("maxTrades", self.settings.max_trades_per_day)),
                cooldown_after_loss_seconds=int(
                    overrides.get("cooldownAfterLossSeconds", self.settings.cooldown_after_loss_seconds)
                ),
                kill_switch=bool(overrides.get("killSwitch", self.settings.kill_switch)),
            ),
            database=self.database,
            symbols=symbols,
            control_stack=control_stack,
            momentum_config=momentum_config,
            api_min_request_interval_seconds=self.settings.api_min_request_interval_seconds,
            poll_interval_seconds=self.settings.poll_interval_seconds,
            sms_summary_interval_seconds=self.settings.sms_summary_interval_seconds,
            sms_notifier=SmsNotifier(
                SmsConfig(
                    enabled=self.settings.sms_enabled,
                    account_sid=self.settings.twilio_account_sid,
                    auth_token=self.settings.twilio_auth_token,
                    from_number=self.settings.twilio_from_number,
                    to_number=self.settings.sms_to_number,
                )
            ),
            notifier=build_notifier(self.database),
            notification_summary_interval_seconds=(
                self.settings.notification_summary_interval_minutes * 60
            ),
            notification_trade_update_interval_seconds=(
                self.settings.notification_trade_update_interval_minutes * 60
            ),
            notification_recap_interval_seconds=(
                self.settings.notification_recap_interval_minutes * 60
            ),
            max_history_points=self.settings.max_live_history_points,
            max_event_history=self.settings.max_live_event_history,
            session_window_minutes=self.settings.session_window_minutes,
            order_executor=(
                RobinhoodLiveOrderExecutor(client)
                if self.settings.live_trading_enabled
                else PaperOrderExecutor()
            ),
        )

    def _effective_max_trade_size(self, overrides: Dict[str, Any]) -> float:
        configured = float(overrides.get("maxTradeSize", self.settings.max_trade_size))
        if self.settings.live_trading_enabled:
            return min(configured, self.settings.live_max_order_notional)
        return configured

    def _effective_target_profit_pct(self, overrides: Dict[str, Any]) -> float:
        configured = float(overrides.get("targetProfitPct", self.settings.target_profit_pct))
        if self.settings.live_trading_enabled:
            return max(configured, self.settings.live_min_target_profit_pct)
        return configured

    def _momentum_config_from_overrides(self, overrides: Dict[str, Any]) -> MomentumConfig:
        return MomentumConfig(
            lookback_minutes=float(
                overrides.get("momentumLookbackMinutes", self.settings.momentum_lookback_minutes)
            ),
            interval_seconds=int(
                overrides.get("momentumIntervalSeconds", self.settings.momentum_interval_seconds)
            ),
            entry_threshold=float(
                overrides.get("momentumEntryThreshold", self.settings.momentum_entry_threshold)
            ),
            exit_threshold=float(
                overrides.get("momentumExitThreshold", self.settings.momentum_exit_threshold)
            ),
            entry_interval_seconds=int(
                max(
                    1,
                    int(
                        overrides.get(
                            "momentumEntryIntervalSeconds",
                            self.settings.momentum_entry_interval_seconds,
                        )
                    ),
                )
            ),
            max_open_trades=max(
                1,
                int(overrides.get("maxOpenTrades", self.settings.max_open_trades)),
            ),
            target_profit_pct=self._effective_target_profit_pct(overrides),
            allocation_per_trade=float(
                overrides.get("paperAllocationPerTrade", self.settings.paper_allocation_per_trade)
            ),
            catastrophic_loss_pct=float(
                overrides.get("catastrophicLossPct", self.settings.catastrophic_loss_pct)
            ),
            soft_stop_loss_pct=float(
                overrides.get("softStopLossPct", self.settings.soft_stop_loss_pct)
            ),
            pyramiding_enabled=bool(overrides.get("pyramidingEnabled", self.settings.pyramiding_enabled)),
            weights=MomentumWeights(
                trend=float(overrides.get("momentumWeightTrend", self.settings.momentum_weight_trend)),
                acceleration=float(
                    overrides.get("momentumWeightAcceleration", self.settings.momentum_weight_acceleration)
                ),
                z_score=float(
                    overrides.get("momentumWeightZScore", self.settings.momentum_weight_z_score)
                ),
                moving_average_slope=float(
                    overrides.get("momentumWeightMaSlope", self.settings.momentum_weight_ma_slope)
                ),
                volatility_stability=float(
                    overrides.get("momentumWeightVolatility", self.settings.momentum_weight_volatility)
                ),
            ),
        )

    def public_status(self) -> Dict[str, Any]:
        settings = get_settings()
        runner = self.runner
        trader = runner.trader if runner else None
        latest_prices = runner.latest_prices if runner else {}
        price_history = runner.price_history if runner else {}
        quote_history = runner.quote_history if runner else {}
        candle_history = runner.candle_history if runner else {}
        event_history = runner.event_history if runner else []
        trade_history = [asdict(trade) for trade in trader.trade_history] if trader else []
        positions = [asdict(position) for position in trader.positions.values()] if trader else []
        return {
            "configured": bool(settings.robinhood_api_key and settings.robinhood_private_key),
            "mode": settings.trading_mode,
            "symbols": runner.symbols if runner else settings.symbol_list,
            "smsEnabled": settings.sms_enabled,
            "startingCash": trader.starting_cash if trader else settings.starting_cash,
            "liveMaxOrderNotional": settings.live_max_order_notional,
            "portfolioValue": trader.portfolio_value(latest_prices) if trader else settings.starting_cash,
            "cash": trader.cash if trader else settings.starting_cash,
            "availableCash": trader.available_cash if trader else settings.starting_cash,
            "realizedPl": trader.realized_pl() if trader else 0.0,
            "unrealizedPl": trader.unrealized_pl(latest_prices) if trader else 0.0,
            "gainReserve": trader.gain_reserve if trader else 0.0,
            "trades": len(trader.trade_history) if trader else 0,
            "latestPrices": latest_prices,
            "priceHistory": price_history,
            "quoteHistory": quote_history,
            "candleHistory": candle_history,
            "eventHistory": event_history,
            "tradeHistory": trade_history,
            "positions": positions,
            "lastError": runner.last_error if runner else "",
            "controlStack": asdict(runner.control_stack) if runner else {},
            "riskControls": {
                "maxTradeSize": runner.risk.max_trade_size if runner else settings.max_trade_size,
                "maxDailyLoss": runner.risk.max_daily_loss if runner else settings.max_daily_loss,
                "maxTrades": runner.risk.max_trades_per_day if runner else settings.max_trades_per_day,
                "cooldownAfterLossSeconds": (
                    runner.risk.cooldown_after_loss_seconds
                    if runner
                    else settings.cooldown_after_loss_seconds
                ),
                "killSwitch": runner.risk.kill_switch if runner else settings.kill_switch,
            },
            "momentum": runner.momentum_scores if runner else {},
            "momentumConfig": asdict(runner.momentum_config) if runner else {},
            "openTradeMetadata": runner.open_trade_metadata if runner else {},
            "simulationRunId": runner.simulation_run_id if runner else "",
            "marketDataCollector": (
                self.collector.status()
                if self.collector
                else {
                    "running": False,
                    "symbols": settings.symbol_list,
                    "intervalSeconds": settings.market_data_collector_interval_seconds,
                    "collectedTicks": 0,
                    "lastCollectionAt": "",
                    "latestPrices": {},
                    "lastError": "",
                }
            ),
            "manualControls": {
                "paused": runner.manual_pause if runner else False,
                "pauseReason": runner.manual_pause_reason if runner else "",
                "killSwitch": runner.risk.kill_switch if runner else settings.kill_switch,
            },
            "performanceAnalytics": (
                runner.performance_summary_details() if runner else {}
            ),
            "discordControl": (
                self.discord_control.status()
                if self.discord_control
                else {
                    "enabled": settings.discord_control_enabled,
                    "configured": False,
                    "running": False,
                    "channelId": settings.discord_control_channel_id,
                    "prefix": settings.discord_control_prefix,
                    "lastError": "",
                }
            ),
        }

    def execute_manual_control(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        runner = self.get_runner(payload.get("overrides") or {})
        action = str(payload.get("action", "")).strip().lower().replace("-", "_")
        reason = str(payload.get("reason", "manual control")).strip()
        if action == "pause":
            result = runner.pause(reason)
        elif action == "resume":
            result = runner.resume(reason)
        elif action in {"kill_on", "kill_switch_on"}:
            result = runner.set_kill_switch(True, reason)
        elif action in {"kill_off", "kill_switch_off"}:
            result = runner.set_kill_switch(False, reason)
        elif action in {"force_exit", "exit"}:
            symbol = str(payload.get("symbol", "")).strip().upper() or None
            if symbol == "ALL":
                symbol = None
            result = runner.force_exit(symbol=symbol, reason=reason)
        elif action in {"status", "analytics"}:
            result = {"ok": True, "action": action}
        else:
            return {"error": "unknown manual control action"}
        result["status"] = self.public_status()
        return result

    def handle_discord_command(self, command_text: str, message: Dict[str, Any]) -> Dict[str, Any]:
        del message
        parts = command_text.strip().split()
        if not parts:
            return {"error": "empty command"}
        command = parts[0].lower()
        if command == "status":
            status = self.public_status()
            controls = status.get("manualControls", {})
            summary = (
                f"Mode `{status.get('mode')}` | paused `{controls.get('paused')}` | "
                f"kill `{controls.get('killSwitch')}` | positions `{len(status.get('positions', []))}` | "
                f"P/L `{status.get('realizedPl', 0):.2f}` realized, `{status.get('unrealizedPl', 0):.2f}` unrealized."
            )
            return {"ok": True, "action": "status", "summary": summary}
        if command == "analytics":
            analytics = self.public_status().get("performanceAnalytics", {})
            summary = (
                f"Win `{analytics.get('win_rate_pct', 0)}%` | profit factor `{analytics.get('profit_factor', 0)}` | "
                f"return `{analytics.get('total_return_pct', 0)}%` | exposure `{analytics.get('open_exposure_pct', 0)}%`."
            )
            return {"ok": True, "action": "analytics", "summary": summary}
        if command == "pause":
            return self.execute_manual_control({"action": "pause", "reason": " ".join(parts[1:]) or "Discord pause"})
        if command == "resume":
            return self.execute_manual_control({"action": "resume", "reason": "Discord resume"})
        if command == "kill" and len(parts) > 1 and parts[1].lower() in {"on", "off"}:
            return self.execute_manual_control(
                {
                    "action": "kill_on" if parts[1].lower() == "on" else "kill_off",
                    "reason": "Discord kill switch command",
                }
            )
        if command == "exit":
            symbol = parts[1].upper() if len(parts) > 1 else "ALL"
            reason = " ".join(parts[2:]) or "Discord force exit"
            return self.execute_manual_control({"action": "force_exit", "symbol": symbol, "reason": reason})
        return {"error": "unknown Discord control command"}


STATE = WebAppState()


class CryptoResearchHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] == "/api/auth/status":
            self._send_auth_status()
            return
        if self._is_api_request() and not self._is_authenticated():
            self._send_json({"error": "authentication required"}, status=401)
            return
        if self.path == "/api/status":
            self._send_json(STATE.public_status())
            return
        if self.path == "/api/history":
            settings = get_settings()
            with ResearchDatabase(settings.database_path) as database:
                self._send_json(
                    {
                        "summaries": database.simulation_summaries(),
                        "recentEvents": database.recent_events(100),
                    }
                )
            return
        if self.path == "/api/saved-sessions":
            settings = get_settings()
            with ResearchDatabase(settings.database_path) as database:
                sessions = database.trade_sessions(
                    starting_cash=settings.starting_cash,
                    window_minutes=settings.session_window_minutes,
                    limit=100,
                )
                self._send_json(
                    {
                        "sessions": sessions,
                        "eventCounts": database.event_counts(),
                        "windowMinutes": settings.session_window_minutes,
                        "databasePath": settings.database_path,
                    }
                )
            return
        self._serve_static()

    def do_POST(self) -> None:
        try:
            route = self.path.split("?", 1)[0]
            if route == "/api/auth/login":
                self._handle_login()
                return
            if route == "/api/auth/logout":
                self._handle_logout()
                return
            if route == "/api/auth/forgot-password":
                self._handle_forgot_password()
                return
            if route == "/api/auth/reset-password":
                self._handle_reset_password()
                return
            if self._is_api_request() and not self._is_authenticated():
                self._send_json({"error": "authentication required"}, status=401)
                return
            if self.path == "/api/live-paper/step":
                overrides = self._read_json()
                runner = STATE.get_runner(overrides)
                result = runner.run_once()
                self._send_json({"result": result, "status": STATE.public_status()})
                return
            if self.path == "/api/live-paper/reset":
                STATE.reset()
                STATE.ensure_market_data_collector()
                STATE.ensure_discord_control()
                self._send_json({"status": STATE.public_status()})
                return
            if self.path == "/api/manual-control":
                result = STATE.execute_manual_control(self._read_json())
                self._send_json(result, status=400 if result.get("error") else 200)
                return
            if self.path == "/api/market-data/collect":
                collector = STATE.ensure_market_data_collector()
                if not collector:
                    self._send_json({"error": "market data collector is not configured"}, status=400)
                    return
                self._send_json({"collector": collector.collect_once(), "status": STATE.public_status()})
                return
            if self.path == "/api/backtest/run":
                self._send_json(run_backtest_request(self._read_json()))
                return
            self._send_json({"error": "unknown endpoint"}, status=404)
        except RobinhoodAPIError as exc:
            self._send_json({"error": str(exc), "statusCode": exc.status_code}, status=502)
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=400)

    def _send_auth_status(self) -> None:
        STATE.auth.refresh_settings()
        user = self._current_user()
        self._send_json(
            {
                "enabled": STATE.auth.is_enabled(),
                "configured": STATE.auth.configured(),
                "authenticated": bool(user) or not STATE.auth.is_enabled(),
                "email": user or "",
            }
        )

    def _handle_login(self) -> None:
        STATE.auth.refresh_settings()
        if not STATE.auth.is_enabled():
            self._send_json({"ok": True, "authenticated": True})
            return
        if not STATE.auth.configured():
            self._send_json({"error": "web authentication is not configured"}, status=503)
            return
        payload = self._read_json()
        token = STATE.auth.login(str(payload.get("email", "")), str(payload.get("password", "")))
        if not token:
            self._send_json({"error": "invalid email or password"}, status=401)
            return
        self._send_json(
            {"ok": True, "authenticated": True},
            extra_headers=[
                (
                    "Set-Cookie",
                    (
                        f"crypto_auth={token}; Path=/; HttpOnly; SameSite=Lax; "
                        f"Max-Age={STATE.auth.settings.web_auth_session_seconds}"
                    ),
                )
            ],
        )

    def _handle_logout(self) -> None:
        token = self._session_token()
        if token:
            STATE.auth.logout(token)
        self._send_json(
            {"ok": True},
            extra_headers=[("Set-Cookie", "crypto_auth=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0")],
        )

    def _handle_forgot_password(self) -> None:
        STATE.auth.refresh_settings()
        payload = self._read_json()
        self._send_json(STATE.auth.start_password_reset(str(payload.get("email", ""))))

    def _handle_reset_password(self) -> None:
        payload = self._read_json()
        ok = STATE.auth.reset_password(
            str(payload.get("token", "")),
            str(payload.get("password", "")),
        )
        if not ok:
            self._send_json({"error": "reset token is invalid, expired, or password is too short"}, status=400)
            return
        self._send_json({"ok": True})

    def _is_api_request(self) -> bool:
        return self.path.split("?", 1)[0].startswith("/api/")

    def _session_token(self) -> str:
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            name, _, value = part.strip().partition("=")
            if name == "crypto_auth":
                return value
        return ""

    def _current_user(self) -> Optional[str]:
        if not STATE.auth.is_enabled():
            return "auth-disabled"
        token = self._session_token()
        return STATE.auth.user_for_session(token) if token else None

    def _is_authenticated(self) -> bool:
        return bool(self._current_user())

    def _serve_static(self) -> None:
        route = self.path.split("?", 1)[0]
        route = "/index.html" if route == "/" else route
        path = (DOCS / route.lstrip("/")).resolve()
        if not str(path).startswith(str(DOCS.resolve())) or not path.exists():
            self._send_json({"error": "not found"}, status=404)
            return

        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw) if raw else {}

    def _send_json(
        self,
        payload: Dict[str, Any],
        status: int = 200,
        extra_headers: Optional[list[tuple[str, str]]] = None,
    ) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for name, value in extra_headers or []:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def run(host: str = "127.0.0.1", port: int = 8766) -> None:
    STATE.ensure_market_data_collector()
    STATE.ensure_discord_control()
    server = ThreadingHTTPServer((host, port), CryptoResearchHandler)
    print(f"Crypto Research web app running at http://{host}:{port}")
    print("Open the browser and use the Live Robinhood Paper panel.")
    print("Background market data collection is read-only and writes to SQLite.")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    finally:
        if STATE.collector:
            STATE.collector.stop()
        if STATE.discord_control:
            STATE.discord_control.stop()


def run_backtest_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    settings = get_settings()
    config = _backtest_config_from_payload(payload)
    with ResearchDatabase(settings.database_path) as database:
        rows = database.historical_market_data(config.symbol, config.start, config.end)
    points = parse_points(rows)
    result = run_backtest(config, points)
    response: Dict[str, Any] = {
        "result": result_to_dict(result),
        "dataPointCount": len(points),
        "source": "local_sqlite_market_data",
    }
    if bool(payload.get("walkForwardEnabled", False)):
        response["walkForward"] = walk_forward_to_dict(run_walk_forward(config, points))
    return response


def _backtest_config_from_payload(payload: Dict[str, Any]) -> BacktestConfig:
    symbol = str(payload.get("symbol", "")).strip().upper()
    if not symbol:
        raise ValueError("Symbol is required.")
    if "-" not in symbol:
        symbol = f"{symbol}-USD"

    start = parse_datetime(str(payload.get("start", "")))
    end = parse_datetime(str(payload.get("end", "")))
    if end <= start:
        raise ValueError("Date/time range must end after it starts.")

    starting_cash = _positive_float(payload, "startingCash", "Starting cash")
    max_trade_size = _positive_float(payload, "maxTradeSize", "Max trade size")
    if max_trade_size > starting_cash:
        raise ValueError("Max trade size cannot exceed starting cash.")
    entry_score = _bounded_float(payload, "entryScore", "Entry score", 0, 100)
    exit_score = _bounded_float(payload, "exitScore", "Exit score", 0, 100)
    target_profit_pct = _positive_float(payload, "targetProfitPct", "Target profit %")
    cooldown_seconds = int(_bounded_float(payload, "cooldownSeconds", "Cooldown seconds", 0, 86400))
    entry_interval_seconds = int(float(payload.get("entryIntervalSeconds", 300)))
    if entry_interval_seconds < 1 or entry_interval_seconds > 86400:
        raise ValueError("Entry interval seconds must be between 1 and 86400.")
    max_open_trades = int(float(payload.get("maxOpenTrades", 1)))
    if max_open_trades < 1 or max_open_trades > 100:
        raise ValueError("Max open trades must be between 1 and 100.")
    catastrophic_loss_pct = float(payload.get("catastrophicLossPct", -3))
    if catastrophic_loss_pct >= 0:
        raise ValueError("Catastrophic loss % must be negative.")

    return BacktestConfig(
        symbol=symbol,
        start=start,
        end=end,
        starting_cash=starting_cash,
        max_trade_size=max_trade_size,
        entry_score=entry_score,
        exit_score=exit_score,
        target_profit_pct=target_profit_pct,
        cooldown_seconds=cooldown_seconds,
        catastrophic_loss_pct=catastrophic_loss_pct,
        entry_interval_seconds=entry_interval_seconds,
        max_open_trades=max_open_trades,
    )


def _positive_float(payload: Dict[str, Any], key: str, label: str) -> float:
    value = float(payload.get(key, 0))
    if value <= 0:
        raise ValueError(f"{label} must be greater than zero.")
    return value


def _bounded_float(
    payload: Dict[str, Any],
    key: str,
    label: str,
    minimum: float,
    maximum: float,
) -> float:
    value = float(payload.get(key, minimum))
    if value < minimum or value > maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}.")
    return value


if __name__ == "__main__":
    run()
