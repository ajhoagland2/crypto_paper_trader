from typing import Dict, List

from src.backtest.models import BacktestTrade, EquityPoint


def calculate_max_drawdown(equity_curve: List[EquityPoint]) -> float:
    peak = None
    max_drawdown = 0.0
    for point in equity_curve:
        value = point.value
        if peak is None or value > peak:
            peak = value
        if peak and peak > 0:
            drawdown = ((value - peak) / peak) * 100
            max_drawdown = min(max_drawdown, drawdown)
    return max_drawdown


def summarize_trades(
    starting_cash: float,
    final_equity: float,
    trades: List[BacktestTrade],
    equity_curve: List[EquityPoint],
) -> Dict[str, object]:
    closed = [trade for trade in trades if trade.status == "CLOSED"]
    wins = [trade.realized_pl for trade in closed if trade.realized_pl > 0]
    losses = [trade.realized_pl for trade in closed if trade.realized_pl < 0]
    realized_pl = sum(trade.realized_pl for trade in closed)
    return {
        "totalReturn": _percent_return(starting_cash, final_equity),
        "realizedPl": realized_pl,
        "maxDrawdown": calculate_max_drawdown(equity_curve),
        "winRate": (len(wins) / len(closed) * 100) if closed else 0.0,
        "averageWin": sum(wins) / len(wins) if wins else 0.0,
        "averageLoss": sum(losses) / len(losses) if losses else 0.0,
        "numberOfTrades": len(closed),
        "bestTrade": max((trade.realized_pl for trade in closed), default=0.0),
        "worstTrade": min((trade.realized_pl for trade in closed), default=0.0),
        "openTrades": len([trade for trade in trades if trade.status == "OPEN"]),
    }


def _percent_return(start: float, end: float) -> float:
    if start <= 0:
        return 0.0
    return ((end - start) / start) * 100
