import sys

from src.config import get_settings
from src.data.database import ResearchDatabase
from src.notifications.notifier import build_notifier
from src.notifications.providers.base import NotificationSeverity
from src.notifications.sms import SmsConfig, SmsNotifier
from src.risk.risk_manager import RiskManager
from src.robinhood.client import RobinhoodClient
from src.strategy.control_stack import ControlStack
from src.strategy.momentum import MomentumConfig, MomentumWeights
from src.strategy.signals import Signal, price_action_live_signal
from src.trading.execution import PaperOrderExecutor, RobinhoodLiveOrderExecutor
from src.trading.live_paper_runner import LivePaperRunner
from src.trading.paper_trader import PaperTrader


def run_simulation() -> None:
    settings = get_settings()
    if settings.live_trading_enabled:
        raise RuntimeError("Built-in simulation requires TRADING_MODE=paper.")

    symbol = "BTC-USD"
    prices = [10.0, 10.0, 10.0, 10.0, 10.0, 12.0, 14.0, 12.0, 10.0, 8.0]
    trader = PaperTrader(
        starting_cash=settings.starting_cash,
        gain_reserve_percent=settings.gain_reserve_percent,
    )
    risk = RiskManager(
        max_trade_size=settings.max_trade_size,
        max_daily_loss=settings.max_daily_loss,
        max_trades_per_day=settings.max_trades_per_day,
        cooldown_after_loss_seconds=settings.cooldown_after_loss_seconds,
        kill_switch=settings.kill_switch,
    )

    with ResearchDatabase(settings.database_path) as database:
        notifier = build_notifier(database)
        notifier.notify(
            "SIMULATION_STARTED",
            "Built-in paper simulation started.",
            NotificationSeverity.INFO,
            details={"mode": "SHADOW", "status": "No live order submitted"},
        )
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
        notifier.notify(
            "SIMULATION_STOPPED",
            "Built-in paper simulation stopped.",
            NotificationSeverity.INFO,
            details={"mode": "SHADOW", "status": "No live order submitted"},
        )

    print("Paper simulation complete")
    print(f"Cash: ${trader.cash:.2f}")
    print(f"Portfolio value: ${trader.portfolio_value({symbol: prices[-1]}):.2f}")
    print(f"Realized P/L: ${trader.realized_pl():.2f}")
    print(f"Gain reserve: ${trader.gain_reserve:.2f}")
    print(f"Unrealized P/L: ${trader.unrealized_pl({symbol: prices[-1]}):.2f}")
    print(f"Trades: {len(trader.trade_history)}")


def run_live_paper() -> None:
    settings = get_settings()
    if settings.live_trading_enabled and not settings.live_trading_acknowledged:
        raise RuntimeError(
            "Live order placement requires LIVE_TRADING_ACKNOWLEDGEMENT=I_UNDERSTAND_LIVE_ORDERS."
        )
    if not settings.symbol_list:
        raise RuntimeError("Set CRYPTO_SYMBOLS to at least one symbol, like BTC-USD.")

    client = RobinhoodClient(
        api_key=settings.robinhood_api_key,
        private_key=settings.robinhood_private_key,
        base_url=settings.robinhood_api_base_url,
    )
    trader = PaperTrader(
        starting_cash=settings.starting_cash,
        gain_reserve_percent=settings.gain_reserve_percent,
    )
    risk = RiskManager(
        max_trade_size=(
            min(settings.max_trade_size, settings.live_max_order_notional)
            if settings.live_trading_enabled
            else settings.max_trade_size
        ),
        max_daily_loss=settings.max_daily_loss,
        max_trades_per_day=settings.max_trades_per_day,
        cooldown_after_loss_seconds=settings.cooldown_after_loss_seconds,
        kill_switch=settings.kill_switch,
    )
    control_stack = ControlStack(
        lookback_window=settings.lookback_window,
        buy_dip_percent=settings.buy_dip_percent,
        rebound_percent=settings.rebound_percent,
        sell_above_dip_percent=settings.sell_above_dip_percent,
        stop_loss_percent=settings.stop_loss_percent,
        trailing_stop_percent=settings.trailing_stop_percent,
    )
    sms_notifier = SmsNotifier(
        SmsConfig(
            enabled=settings.sms_enabled,
            account_sid=settings.twilio_account_sid,
            auth_token=settings.twilio_auth_token,
            from_number=settings.twilio_from_number,
            to_number=settings.sms_to_number,
        )
    )

    with ResearchDatabase(settings.database_path) as database:
        notifier = build_notifier(database)
        runner = LivePaperRunner(
            client=client,
            trader=trader,
            risk=risk,
            database=database,
            symbols=settings.symbol_list,
            control_stack=control_stack,
            momentum_config=MomentumConfig(
                lookback_minutes=settings.momentum_lookback_minutes,
                interval_seconds=settings.momentum_interval_seconds,
                entry_threshold=settings.momentum_entry_threshold,
                exit_threshold=settings.momentum_exit_threshold,
                entry_interval_seconds=settings.momentum_entry_interval_seconds,
                max_open_trades=settings.max_open_trades,
                target_profit_pct=settings.target_profit_pct,
                allocation_per_trade=settings.paper_allocation_per_trade,
                catastrophic_loss_pct=settings.catastrophic_loss_pct,
                soft_stop_loss_pct=settings.soft_stop_loss_pct,
                pyramiding_enabled=settings.pyramiding_enabled,
                weights=MomentumWeights(
                    trend=settings.momentum_weight_trend,
                    acceleration=settings.momentum_weight_acceleration,
                    z_score=settings.momentum_weight_z_score,
                    moving_average_slope=settings.momentum_weight_ma_slope,
                    volatility_stability=settings.momentum_weight_volatility,
                ),
            ),
            poll_interval_seconds=settings.poll_interval_seconds,
            sms_summary_interval_seconds=settings.sms_summary_interval_seconds,
            sms_notifier=sms_notifier,
            notifier=notifier,
            notification_summary_interval_seconds=settings.notification_summary_interval_minutes * 60,
            notification_trade_update_interval_seconds=(
                settings.notification_trade_update_interval_minutes * 60
            ),
            notification_recap_interval_seconds=settings.notification_recap_interval_minutes * 60,
            max_history_points=settings.max_live_history_points,
            max_event_history=settings.max_live_event_history,
            session_window_minutes=settings.session_window_minutes,
            order_executor=(
                RobinhoodLiveOrderExecutor(client)
                if settings.live_trading_enabled
                else PaperOrderExecutor()
            ),
        )
        mode_label = "LIVE trading" if settings.live_trading_enabled else "live-data paper trading"
        print(f"Starting Robinhood {mode_label} loop.")
        if settings.live_trading_enabled:
            print(f"Live orders are enabled. Per-order notional cap: ${settings.live_max_order_notional:.2f}.")
        else:
            print("No live orders will be placed. Press Ctrl+C to stop.")
        runner.run_forever()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "live-paper":
        run_live_paper()
    else:
        run_simulation()
