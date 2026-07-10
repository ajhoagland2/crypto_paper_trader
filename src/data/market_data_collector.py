import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

from src.data.database import ResearchDatabase
from src.robinhood.client import RobinhoodAPIError
from src.trading.live_paper_runner import extract_best_bid_ask_prices


class MarketDataCollector:
    """Read-only background collector that keeps local SQL market data fresh."""

    def __init__(
        self,
        client: object,
        database_path: str,
        symbols: Iterable[str],
        interval_seconds: float,
    ) -> None:
        self.client = client
        self.database_path = database_path
        self.symbols = [symbol.upper() for symbol in symbols]
        self.interval_seconds = max(1.0, interval_seconds)
        self.latest_prices: Dict[str, float] = {}
        self.collected_ticks = 0
        self.last_collection_at: Optional[datetime] = None
        self.last_error = ""
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="market-data-collector",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def collect_once(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        try:
            payload = self.client.get_best_bid_ask(self.symbols)  # type: ignore[attr-defined]
            prices = extract_best_bid_ask_prices(payload)
        except RobinhoodAPIError as exc:
            self.last_error = str(exc)
            with ResearchDatabase(self.database_path) as database:
                database.log_api_error(
                    {
                        "error": self.last_error,
                        "status_code": exc.status_code,
                        "source": "background_market_data_collector",
                    }
                )
            return self.status()
        except Exception as exc:
            self.last_error = str(exc)
            with ResearchDatabase(self.database_path) as database:
                database.log_api_error(
                    {
                        "error": self.last_error,
                        "source": "background_market_data_collector",
                    }
                )
            return self.status()

        inserted = 0
        with ResearchDatabase(self.database_path) as database:
            for symbol in self.symbols:
                price = prices.get(symbol)
                if price is None:
                    continue
                database.log_market_data(
                    symbol,
                    {
                        "price": price,
                        "source": "background_market_data_collector",
                        "collected_at": now.isoformat(),
                    },
                )
                inserted += 1

        with self._lock:
            self.latest_prices.update(prices)
            self.collected_ticks += inserted
            self.last_collection_at = now
            self.last_error = ""
        return self.status()

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "running": self.running,
                "symbols": self.symbols,
                "intervalSeconds": self.interval_seconds,
                "collectedTicks": self.collected_ticks,
                "lastCollectionAt": (
                    self.last_collection_at.isoformat()
                    if self.last_collection_at
                    else ""
                ),
                "latestPrices": dict(self.latest_prices),
                "lastError": self.last_error,
            }

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            self.collect_once()
            self._stop_event.wait(self.interval_seconds)
