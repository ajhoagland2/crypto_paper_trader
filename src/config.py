from dataclasses import dataclass
import os
from pathlib import Path
from typing import Dict


def load_dotenv(path: str = ".env") -> Dict[str, str]:
    """Load simple KEY=VALUE pairs into the environment without extra dependencies."""
    env_path = Path(path)
    loaded: Dict[str, str] = {}
    if not env_path.exists():
        return loaded

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ[key] = value
        loaded[key] = value
    return loaded


@dataclass(frozen=True)
class Settings:
    robinhood_api_key: str = ""
    robinhood_private_key: str = ""
    robinhood_api_base_url: str = "https://trading.robinhood.com"
    trading_mode: str = "paper"
    live_trading_acknowledgement: str = ""
    live_max_order_notional: float = 25.0
    database_path: str = "research_agent.sqlite3"
    starting_cash: float = 10_000.0
    max_trade_size: float = 1_000.0
    max_daily_loss: float = 500.0
    max_trades_per_day: int = 10
    cooldown_after_loss_seconds: int = 60
    kill_switch: bool = False
    crypto_symbols: str = "BTC-USD,ETH-USD,SOL-USD,DOGE-USD,XLM-USD,ATOM-USD"
    poll_interval_seconds: int = 60
    lookback_window: int = 4
    buy_dip_percent: float = 4.0
    rebound_percent: float = 1.0
    sell_above_dip_percent: float = 8.0
    stop_loss_percent: float = 6.0
    trailing_stop_percent: float = 5.0
    gain_reserve_percent: float = 15.0
    api_requests_per_minute: int = 60
    api_min_request_interval_seconds: float = 1.0
    market_data_collector_enabled: bool = True
    market_data_collector_interval_seconds: float = 30.0
    max_live_history_points: int = 3000
    max_live_event_history: int = 500
    session_window_minutes: int = 30
    momentum_lookback_minutes: float = 5.0
    momentum_interval_seconds: int = 30
    momentum_entry_threshold: float = 80.0
    momentum_exit_threshold: float = 40.0
    momentum_entry_interval_seconds: int = 300
    max_open_trades: int = 3
    target_profit_pct: float = 0.25
    live_min_target_profit_pct: float = 3.0
    paper_allocation_per_trade: float = 100.0
    catastrophic_loss_pct: float = -3.0
    soft_stop_loss_pct: float = -1.0
    momentum_weight_trend: float = 0.25
    momentum_weight_acceleration: float = 0.25
    momentum_weight_z_score: float = 0.25
    momentum_weight_ma_slope: float = 0.15
    momentum_weight_volatility: float = 0.10
    pyramiding_enabled: bool = False
    notification_enabled: bool = False
    notification_provider: str = "telegram"
    notification_rate_limit_per_minute: int = 10
    notification_dedup_seconds: int = 300
    notification_summary_interval_minutes: int = 60
    notification_trade_update_interval_minutes: int = 30
    notification_recap_interval_minutes: int = 60
    heartbeat_interval_minutes: int = 60
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    discord_webhook_url: str = ""
    discord_control_enabled: bool = False
    discord_bot_token: str = ""
    discord_control_channel_id: str = ""
    discord_control_poll_seconds: int = 5
    discord_control_prefix: str = "!trader"
    pushover_user_key: str = ""
    pushover_api_token: str = ""
    email_sms_address: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    sms_enabled: bool = False
    sms_summary_interval_seconds: int = 3600
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""
    twilio_to_number: str = ""
    sms_to_number: str = ""
    web_auth_enabled: bool = True
    web_auth_email: str = ""
    web_auth_password: str = ""
    web_auth_password_hash: str = ""
    web_auth_store_path: str = "web_auth_store.json"
    web_auth_session_seconds: int = 86400
    web_auth_reset_token_seconds: int = 1800

    @property
    def live_trading_enabled(self) -> bool:
        return self.trading_mode.lower() == "live"

    @property
    def live_trading_acknowledged(self) -> bool:
        return self.live_trading_acknowledgement == "I_UNDERSTAND_LIVE_ORDERS"

    @property
    def symbol_list(self) -> list[str]:
        return [
            symbol.strip().upper()
            for symbol in self.crypto_symbols.split(",")
            if symbol.strip()
        ]


def get_settings() -> Settings:
    load_dotenv()
    return Settings(
        robinhood_api_key=os.getenv("ROBINHOOD_API_KEY", ""),
        robinhood_private_key=os.getenv("ROBINHOOD_PRIVATE_KEY", ""),
        robinhood_api_base_url=os.getenv(
            "ROBINHOOD_API_BASE_URL", "https://trading.robinhood.com"
        ).rstrip("/"),
        trading_mode=os.getenv("TRADING_MODE", "paper"),
        live_trading_acknowledgement=os.getenv("LIVE_TRADING_ACKNOWLEDGEMENT", ""),
        live_max_order_notional=float(os.getenv("LIVE_MAX_ORDER_NOTIONAL", "25")),
        database_path=os.getenv("DATABASE_PATH", "research_agent.sqlite3"),
        starting_cash=float(os.getenv("STARTING_CASH", "10000")),
        max_trade_size=float(os.getenv("MAX_TRADE_SIZE", "1000")),
        max_daily_loss=float(os.getenv("MAX_DAILY_LOSS", "500")),
        max_trades_per_day=int(os.getenv("MAX_TRADES_PER_DAY", "10")),
        cooldown_after_loss_seconds=int(os.getenv("COOLDOWN_AFTER_LOSS_SECONDS", "60")),
        kill_switch=os.getenv("KILL_SWITCH", "false").lower() == "true",
        crypto_symbols=os.getenv("CRYPTO_SYMBOLS", "BTC-USD,ETH-USD,SOL-USD,DOGE-USD,XLM-USD,ATOM-USD"),
        poll_interval_seconds=int(os.getenv("POLL_INTERVAL_SECONDS", "60")),
        lookback_window=int(os.getenv("LOOKBACK_WINDOW", "4")),
        buy_dip_percent=float(os.getenv("BUY_DIP_PERCENT", "4")),
        rebound_percent=float(os.getenv("REBOUND_PERCENT", "1")),
        sell_above_dip_percent=float(
            os.getenv("SELL_ABOVE_DIP_PERCENT", os.getenv("PROFIT_TARGET_PERCENT", "8"))
        ),
        stop_loss_percent=float(os.getenv("STOP_LOSS_PERCENT", "6")),
        trailing_stop_percent=float(os.getenv("TRAILING_STOP_PERCENT", "5")),
        gain_reserve_percent=float(os.getenv("GAIN_RESERVE_PERCENT", "15")),
        api_requests_per_minute=int(os.getenv("API_REQUESTS_PER_MINUTE", "60")),
        api_min_request_interval_seconds=float(os.getenv("API_MIN_REQUEST_INTERVAL_SECONDS", "1.0")),
        market_data_collector_enabled=os.getenv("MARKET_DATA_COLLECTOR_ENABLED", "true").lower() == "true",
        market_data_collector_interval_seconds=float(
            os.getenv("MARKET_DATA_COLLECTOR_INTERVAL_SECONDS", "30")
        ),
        max_live_history_points=int(os.getenv("MAX_LIVE_HISTORY_POINTS", "3000")),
        max_live_event_history=int(os.getenv("MAX_LIVE_EVENT_HISTORY", "500")),
        session_window_minutes=int(os.getenv("SESSION_WINDOW_MINUTES", "30")),
        momentum_lookback_minutes=float(os.getenv("MOMENTUM_LOOKBACK_MINUTES", "5")),
        momentum_interval_seconds=int(os.getenv("MOMENTUM_INTERVAL_SECONDS", "30")),
        momentum_entry_threshold=float(os.getenv("MOMENTUM_ENTRY_THRESHOLD", "80")),
        momentum_exit_threshold=float(os.getenv("MOMENTUM_EXIT_THRESHOLD", "40")),
        momentum_entry_interval_seconds=int(os.getenv("MOMENTUM_ENTRY_INTERVAL_SECONDS", "300")),
        max_open_trades=int(os.getenv("MAX_OPEN_TRADES", "3")),
        target_profit_pct=float(os.getenv("TARGET_PROFIT_PCT", "0.25")),
        live_min_target_profit_pct=float(os.getenv("LIVE_MIN_TARGET_PROFIT_PCT", "3.0")),
        paper_allocation_per_trade=float(os.getenv("PAPER_ALLOCATION_PER_TRADE", "100")),
        catastrophic_loss_pct=float(os.getenv("CATASTROPHIC_LOSS_PCT", "-3")),
        soft_stop_loss_pct=float(os.getenv("SOFT_STOP_LOSS_PCT", "-1")),
        momentum_weight_trend=float(os.getenv("MOMENTUM_WEIGHT_TREND", "0.25")),
        momentum_weight_acceleration=float(os.getenv("MOMENTUM_WEIGHT_ACCELERATION", "0.25")),
        momentum_weight_z_score=float(os.getenv("MOMENTUM_WEIGHT_Z_SCORE", "0.25")),
        momentum_weight_ma_slope=float(os.getenv("MOMENTUM_WEIGHT_MA_SLOPE", "0.15")),
        momentum_weight_volatility=float(os.getenv("MOMENTUM_WEIGHT_VOLATILITY", "0.10")),
        pyramiding_enabled=os.getenv("PYRAMIDING_ENABLED", "false").lower() == "true",
        notification_enabled=os.getenv("NOTIFICATION_ENABLED", "false").lower() == "true",
        notification_provider=os.getenv("NOTIFICATION_PROVIDER", "telegram"),
        notification_rate_limit_per_minute=int(os.getenv("NOTIFICATION_RATE_LIMIT_PER_MINUTE", "10")),
        notification_dedup_seconds=int(os.getenv("NOTIFICATION_DEDUP_SECONDS", "300")),
        notification_summary_interval_minutes=int(os.getenv("NOTIFICATION_SUMMARY_INTERVAL_MINUTES", "60")),
        notification_trade_update_interval_minutes=int(
            os.getenv("NOTIFICATION_TRADE_UPDATE_INTERVAL_MINUTES", "30")
        ),
        notification_recap_interval_minutes=int(os.getenv("NOTIFICATION_RECAP_INTERVAL_MINUTES", "60")),
        heartbeat_interval_minutes=int(os.getenv("HEARTBEAT_INTERVAL_MINUTES", "60")),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL", ""),
        discord_control_enabled=os.getenv("DISCORD_CONTROL_ENABLED", "false").lower() == "true",
        discord_bot_token=os.getenv("DISCORD_BOT_TOKEN", ""),
        discord_control_channel_id=os.getenv("DISCORD_CONTROL_CHANNEL_ID", ""),
        discord_control_poll_seconds=int(os.getenv("DISCORD_CONTROL_POLL_SECONDS", "5")),
        discord_control_prefix=os.getenv("DISCORD_CONTROL_PREFIX", "!trader"),
        pushover_user_key=os.getenv("PUSHOVER_USER_KEY", ""),
        pushover_api_token=os.getenv("PUSHOVER_API_TOKEN", ""),
        email_sms_address=os.getenv("EMAIL_SMS_ADDRESS", ""),
        smtp_host=os.getenv("SMTP_HOST", ""),
        smtp_port=int(os.getenv("SMTP_PORT", "587")),
        smtp_username=os.getenv("SMTP_USERNAME", ""),
        smtp_password=os.getenv("SMTP_PASSWORD", ""),
        sms_enabled=os.getenv("SMS_ENABLED", "false").lower() == "true",
        sms_summary_interval_seconds=int(os.getenv("SMS_SUMMARY_INTERVAL_SECONDS", "3600")),
        twilio_account_sid=os.getenv("TWILIO_ACCOUNT_SID", ""),
        twilio_auth_token=os.getenv("TWILIO_AUTH_TOKEN", ""),
        twilio_from_number=os.getenv("TWILIO_FROM_NUMBER", ""),
        twilio_to_number=os.getenv("TWILIO_TO_NUMBER", ""),
        sms_to_number=os.getenv("SMS_TO_NUMBER", ""),
        web_auth_enabled=os.getenv("WEB_AUTH_ENABLED", "true").lower() == "true",
        web_auth_email=os.getenv("WEB_AUTH_EMAIL", ""),
        web_auth_password=os.getenv("WEB_AUTH_PASSWORD", ""),
        web_auth_password_hash=os.getenv("WEB_AUTH_PASSWORD_HASH", ""),
        web_auth_store_path=os.getenv("WEB_AUTH_STORE_PATH", "web_auth_store.json"),
        web_auth_session_seconds=int(os.getenv("WEB_AUTH_SESSION_SECONDS", "86400")),
        web_auth_reset_token_seconds=int(os.getenv("WEB_AUTH_RESET_TOKEN_SECONDS", "1800")),
    )
