import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib import request


def normalize_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if "-" not in normalized:
        normalized = f"{normalized}-USD"
    return normalized


def post_tick(body: Dict[str, Any]) -> Dict[str, Any]:
    payload = json.dumps(body).encode("utf-8")
    req = request.Request(
        "http://localhost:8766/api/live-paper/step",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def latest_symbol_event(status: Dict[str, Any], symbol: str) -> Optional[Dict[str, Any]]:
    events = status.get("eventHistory") or []
    for event in reversed(events):
        if event.get("symbol") == symbol:
            return event
    return None


def symbol_position(status: Dict[str, Any], symbol: str) -> Optional[Dict[str, Any]]:
    for position in status.get("positions") or []:
        if position.get("symbol") == symbol:
            return position
    return None


def collect(args: argparse.Namespace) -> None:
    symbol = normalize_symbol(args.symbol)
    output_path = Path(args.output)
    done_path = Path(args.done)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)
    done_path.unlink(missing_ok=True)

    body = {
        "symbol": symbol,
        "momentumEntryThreshold": args.entry_threshold,
        "momentumExitThreshold": args.exit_threshold,
        "targetProfitPct": args.target_profit_pct,
        "paperAllocationPerTrade": args.paper_allocation,
        "catastrophicLossPct": args.catastrophic_loss_pct,
    }
    end = datetime.now(timezone.utc) + timedelta(minutes=args.minutes)

    with output_path.open("a", encoding="utf-8") as stream:
        while datetime.now(timezone.utc) < end:
            try:
                payload = post_tick(body)
                status = payload.get("status") or {}
                momentum = (status.get("momentum") or {}).get(symbol) or {}
                stats = momentum.get("stats") or {}
                event = latest_symbol_event(status, symbol) or {}
                position = symbol_position(status, symbol) or {}
                row = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "runId": status.get("simulationRunId"),
                    "symbol": symbol,
                    "price": (status.get("latestPrices") or {}).get(symbol),
                    "signal": event.get("signal"),
                    "eventType": event.get("type"),
                    "details": event.get("details"),
                    "score": momentum.get("score"),
                    "currentReturn": stats.get("current_return"),
                    "zScore": stats.get("z_score"),
                    "trades": status.get("trades"),
                    "positions": len(status.get("positions") or []),
                    "hasPosition": bool(position),
                    "quantity": position.get("quantity", 0),
                    "averagePrice": position.get("average_price", 0),
                    "cash": status.get("cash"),
                    "availableCash": status.get("availableCash"),
                    "portfolioValue": status.get("portfolioValue"),
                    "realizedPl": status.get("realizedPl"),
                    "unrealizedPl": status.get("unrealizedPl"),
                    "gainReserve": status.get("gainReserve"),
                    "latestError": status.get("lastError"),
                }
            except Exception as exc:
                row = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "symbol": symbol,
                    "error": str(exc),
                }
            stream.write(json.dumps(row, separators=(",", ":")) + "\n")
            stream.flush()
            time.sleep(args.interval_seconds)

    done_path.write_text("DONE\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTC-USD")
    parser.add_argument("--minutes", type=float, default=30)
    parser.add_argument("--interval-seconds", type=float, default=1)
    parser.add_argument("--output", required=True)
    parser.add_argument("--done", required=True)
    parser.add_argument("--entry-threshold", type=float, default=50)
    parser.add_argument("--exit-threshold", type=float, default=40)
    parser.add_argument("--target-profit-pct", type=float, default=3)
    parser.add_argument("--paper-allocation", type=float, default=1)
    parser.add_argument("--catastrophic-loss-pct", type=float, default=-3)
    collect(parser.parse_args())


if __name__ == "__main__":
    main()
