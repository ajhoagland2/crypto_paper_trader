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
        os.environ.setdefault(key, value)
        loaded[key] = value
    return loaded


@dataclass(frozen=True)
class Settings:
    robinhood_api_key: str = ""
    robinhood_private_key: str = ""
    robinhood_api_base_url: str = "https://trading.robinhood.com"
    trading_mode: str = "paper"
    database_path: str = "research_agent.sqlite3"
    starting_cash: float = 10_000.0
    max_trade_size: float = 1_000.0
    max_daily_loss: float = 500.0
    max_trades_per_day: int = 10
    cooldown_after_loss_seconds: int = 300
    kill_switch: bool = False

    @property
    def live_trading_enabled(self) -> bool:
        return self.trading_mode.lower() == "live"


def get_settings() -> Settings:
    load_dotenv()
    return Settings(
        robinhood_api_key=os.getenv("ROBINHOOD_API_KEY", ""),
        robinhood_private_key=os.getenv("ROBINHOOD_PRIVATE_KEY", ""),
        robinhood_api_base_url=os.getenv(
            "ROBINHOOD_API_BASE_URL", "https://trading.robinhood.com"
        ).rstrip("/"),
        trading_mode=os.getenv("TRADING_MODE", "paper"),
        database_path=os.getenv("DATABASE_PATH", "research_agent.sqlite3"),
        starting_cash=float(os.getenv("STARTING_CASH", "10000")),
        max_trade_size=float(os.getenv("MAX_TRADE_SIZE", "1000")),
        max_daily_loss=float(os.getenv("MAX_DAILY_LOSS", "500")),
        max_trades_per_day=int(os.getenv("MAX_TRADES_PER_DAY", "10")),
        cooldown_after_loss_seconds=int(os.getenv("COOLDOWN_AFTER_LOSS_SECONDS", "300")),
        kill_switch=os.getenv("KILL_SWITCH", "false").lower() == "true",
    )
