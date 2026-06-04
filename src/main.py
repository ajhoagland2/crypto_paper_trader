from src.config import get_settings
from src.data.database import ResearchDatabase
from src.risk.risk_manager import RiskManager
from src.strategy.signals import Signal, price_action_live_signal
from src.trading.paper_trader import PaperTrader


def run_simulation() -> None:
    settings = get_settings()
    if settings.live_trading_enabled:
        raise RuntimeError("Live trading is intentionally disabled in milestone 1.")

    symbol = "BTC-USD"
    prices = [10.0, 10.0, 10.0, 10.0, 10.0, 12.0, 14.0, 12.0, 10.0, 8.0]
    trader = PaperTrader(starting_cash=settings.starting_cash)
    risk = RiskManager(
        max_trade_size=settings.max_trade_size,
        max_daily_loss=settings.max_daily_loss,
        max_trades_per_day=settings.max_trades_per_day,
        cooldown_after_loss_seconds=settings.cooldown_after_loss_seconds,
        kill_switch=settings.kill_switch,
    )

    with ResearchDatabase(settings.database_path) as database:
        observed_prices = []
        for price in prices:
            index = len(observed_prices)
            observed_prices.append(price)
            database.log_market_data(symbol, {"price": price})
            position = trader.positions.get(symbol)
            signal = price_action_live_signal(
                prices,
                index,
                has_position=position is not None,
                entry_price=position.average_price if position else 0.0,
                highest_since_entry=max(observed_prices) if position else 0.0,
            )
            database.log_strategy_signal(symbol, {"signal": signal.value, "price": price})

            if signal == Signal.BUY:
                quantity = min(settings.max_trade_size, trader.available_cash) / price
                decision = risk.evaluate_trade("BUY", symbol, quantity, price)
                if decision.approved:
                    trade = trader.buy(symbol, quantity, price)
                    risk.record_trade_result(trade.realized_pl)
                    database.log_simulated_trade(symbol, trade.__dict__)
                else:
                    database.log_rejected_trade(symbol, {"reason": decision.reason})
            elif signal == Signal.SELL and symbol in trader.positions:
                quantity = trader.positions[symbol].quantity
                decision = risk.evaluate_trade("SELL", symbol, quantity, price)
                if decision.approved:
                    trade = trader.sell(symbol, quantity, price)
                    risk.record_trade_result(trade.realized_pl)
                    database.log_simulated_trade(symbol, trade.__dict__)
                else:
                    database.log_rejected_trade(symbol, {"reason": decision.reason})

    print("Paper simulation complete")
    print(f"Cash: ${trader.cash:.2f}")
    print(f"Portfolio value: ${trader.portfolio_value({symbol: prices[-1]}):.2f}")
    print(f"Realized P/L: ${trader.realized_pl():.2f}")
    print(f"Gain reserve: ${trader.gain_reserve:.2f}")
    print(f"Unrealized P/L: ${trader.unrealized_pl({symbol: prices[-1]}):.2f}")
    print(f"Trades: {len(trader.trade_history)}")


if __name__ == "__main__":
    run_simulation()
