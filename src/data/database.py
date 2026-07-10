import json
import sqlite3
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


class ResearchDatabase:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._lock = threading.Lock()
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
            CREATE TABLE IF NOT EXISTS simulation_summaries (
                simulation_run_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notification_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                provider TEXT NOT NULL,
                event_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                message TEXT NOT NULL,
                delivery_status TEXT NOT NULL,
                error_message TEXT
            );
            """
        )
        self.connection.commit()

    def log_event(
        self, event_type: str, payload: Dict[str, Any], symbol: Optional[str] = None
    ) -> None:
        with self._lock:
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

    def log_momentum_calculation(self, symbol: str, payload: Dict[str, Any]) -> None:
        self.log_event("momentum_calculation", payload, symbol)

    def log_simulation_summary(self, simulation_run_id: str, payload: Dict[str, Any]) -> None:
        with self._lock:
            self.connection.execute(
                """
                INSERT OR REPLACE INTO simulation_summaries
                    (simulation_run_id, payload, created_at)
                VALUES (?, ?, ?)
                """,
                (
                    simulation_run_id,
                    json.dumps(payload, sort_keys=True),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            self.connection.commit()

    def log_notification_event(
        self,
        provider: str,
        event_type: str,
        severity: str,
        message: str,
        delivery_status: str,
        error_message: str = "",
        timestamp: Optional[datetime] = None,
    ) -> None:
        with self._lock:
            self.connection.execute(
                """
                INSERT INTO notification_events
                    (timestamp, provider, event_type, severity, message, delivery_status, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (timestamp or datetime.now(timezone.utc)).isoformat(),
                    provider,
                    event_type,
                    severity,
                    message,
                    delivery_status,
                    error_message,
                ),
            )
            self.connection.commit()

    def recent_notification_events(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self.connection.execute(
                """
                SELECT timestamp, provider, event_type, severity, message, delivery_status, error_message
                FROM notification_events
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "timestamp": row["timestamp"],
                "provider": row["provider"],
                "event_type": row["event_type"],
                "severity": row["severity"],
                "message": row["message"],
                "delivery_status": row["delivery_status"],
                "error_message": row["error_message"] or "",
            }
            for row in rows
        ]

    def recent_events(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self.connection.execute(
                "SELECT event_type, symbol, payload, created_at FROM events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "event_type": row["event_type"],
                "symbol": row["symbol"],
                "payload": json.loads(row["payload"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def event_counts(self) -> Dict[str, int]:
        with self._lock:
            rows = self.connection.execute(
                """
                SELECT event_type, COUNT(*) AS event_count
                FROM events
                GROUP BY event_type
                """
            ).fetchall()
        return {row["event_type"]: int(row["event_count"]) for row in rows}

    def simulation_summaries(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self.connection.execute(
                """
                SELECT simulation_run_id, payload, created_at
                FROM simulation_summaries
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "simulation_run_id": row["simulation_run_id"],
                "payload": json.loads(row["payload"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def trade_sessions(
        self,
        starting_cash: float,
        window_minutes: int = 30,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Aggregate saved SQLite activity into fixed UTC session windows."""
        with self._lock:
            rows = self.connection.execute(
                """
                SELECT event_type, symbol, payload, created_at
                FROM events
                WHERE event_type IN ('market_data', 'simulated_trade', 'rejected_trade')
                ORDER BY created_at ASC
                """
            ).fetchall()

        window_seconds = max(1, window_minutes) * 60
        sessions: Dict[int, Dict[str, Any]] = {}
        for row in rows:
            created_at = _parse_timestamp(row["created_at"])
            bucket = int(created_at.timestamp()) // window_seconds * window_seconds
            session = sessions.setdefault(
                bucket,
                {
                    "events": [],
                    "symbols": set(),
                    "rejected": Counter(),
                    "buys": 0,
                    "sells": 0,
                },
            )
            payload = json.loads(row["payload"])
            session["events"].append(
                {
                    "event_type": row["event_type"],
                    "payload": payload,
                    "created_at": created_at,
                }
            )
            if row["symbol"]:
                session["symbols"].add(row["symbol"])
            if row["event_type"] == "simulated_trade":
                side = str(payload.get("side", "")).upper()
                if side == "BUY":
                    session["buys"] += 1
                elif side == "SELL":
                    session["sells"] += 1
            elif row["event_type"] == "rejected_trade":
                session["rejected"][str(payload.get("reason", "unknown"))] += 1

        now = datetime.now(timezone.utc)
        results: List[Dict[str, Any]] = []
        for bucket, session in sessions.items():
            start = datetime.fromtimestamp(bucket, tz=timezone.utc)
            end = datetime.fromtimestamp(bucket + window_seconds, tz=timezone.utc)
            closed = [
                event["payload"]
                for event in session["events"]
                if event["event_type"] == "simulated_trade"
                and str(event["payload"].get("side", "")).upper() == "SELL"
            ]
            realized_values = [float(trade.get("realized_pl", 0.0) or 0.0) for trade in closed]
            wins = [value for value in realized_values if value > 0]
            losses = [value for value in realized_values if value < 0]
            realized_pl = sum(realized_values)
            cumulative = 0.0
            peak_equity = starting_cash
            max_drawdown = 0.0
            for value in realized_values:
                cumulative += value
                equity = starting_cash + cumulative
                peak_equity = max(peak_equity, equity)
                if peak_equity > 0:
                    max_drawdown = min(
                        max_drawdown,
                        ((equity - peak_equity) / peak_equity) * 100,
                    )

            results.append(
                {
                    "sessionId": f"{bucket}-{window_minutes}m",
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "isComplete": end <= now,
                    "symbols": sorted(session["symbols"]),
                    "totalReturn": (
                        (realized_pl / starting_cash) * 100 if starting_cash > 0 else 0.0
                    ),
                    "realizedPl": realized_pl,
                    "maxDrawdown": max_drawdown,
                    "winRate": (len(wins) / len(closed) * 100) if closed else 0.0,
                    "averageWin": sum(wins) / len(wins) if wins else 0.0,
                    "averageLoss": sum(losses) / len(losses) if losses else 0.0,
                    "numberOfTrades": len(closed),
                    "rejectedTradesByReason": dict(session["rejected"]),
                    "bestTrade": max(realized_values, default=0.0),
                    "worstTrade": min(realized_values, default=0.0),
                    "buyCount": session["buys"],
                    "openLots": max(0, session["buys"] - session["sells"]),
                    "outcome": "winning" if realized_pl > 0 else "non-winning",
                }
            )
        return sorted(results, key=lambda item: item["start"], reverse=True)[:limit]

    def historical_market_data(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> List[Dict[str, Any]]:
        symbol = symbol.upper()
        with self._lock:
            rows = self.connection.execute(
                """
                SELECT payload, created_at
                FROM events
                WHERE event_type = 'market_data'
                    AND UPPER(symbol) = ?
                    AND created_at >= ?
                    AND created_at <= ?
                ORDER BY created_at ASC
                """,
                (symbol, start.isoformat(), end.isoformat()),
            ).fetchall()
        points: List[Dict[str, Any]] = []
        for row in rows:
            payload = json.loads(row["payload"])
            price = payload.get("price")
            if not isinstance(price, (int, float)):
                continue
            points.append(
                {
                    "timestamp": row["created_at"],
                    "price": float(price),
                    "volume": float(payload.get("volume", 0.0) or 0.0),
                }
            )
        return points

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "ResearchDatabase":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
