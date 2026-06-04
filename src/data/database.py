import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


class ResearchDatabase:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.initialize()

    def initialize(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                symbol TEXT,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    def log_event(
        self, event_type: str, payload: Dict[str, Any], symbol: Optional[str] = None
    ) -> None:
        self.connection.execute(
            "INSERT INTO events (event_type, symbol, payload, created_at) VALUES (?, ?, ?, ?)",
            (
                event_type,
                symbol,
                json.dumps(payload, sort_keys=True),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.connection.commit()

    def log_market_data(self, symbol: str, payload: Dict[str, Any]) -> None:
        self.log_event("market_data", payload, symbol)

    def log_strategy_signal(self, symbol: str, payload: Dict[str, Any]) -> None:
        self.log_event("strategy_signal", payload, symbol)

    def log_simulated_trade(self, symbol: str, payload: Dict[str, Any]) -> None:
        self.log_event("simulated_trade", payload, symbol)

    def log_rejected_trade(self, symbol: str, payload: Dict[str, Any]) -> None:
        self.log_event("rejected_trade", payload, symbol)

    def log_api_error(self, payload: Dict[str, Any], symbol: Optional[str] = None) -> None:
        self.log_event("api_error", payload, symbol)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "ResearchDatabase":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
