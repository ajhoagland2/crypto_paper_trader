from src.strategy.signals import Signal, moving_average_crossover_signal, price_action_live_signal


def test_buy_signal_on_bullish_crossover() -> None:
    prices = [10, 10, 10, 10, 10, 12]

    assert moving_average_crossover_signal(prices, short_period=2, long_period=5) == Signal.BUY


def test_sell_signal_on_bearish_crossover() -> None:
    prices = [12, 12, 12, 12, 12, 10]

    assert moving_average_crossover_signal(prices, short_period=2, long_period=5) == Signal.SELL


def test_hold_when_not_enough_data() -> None:
    assert moving_average_crossover_signal([1, 2, 3]) == Signal.HOLD


def test_price_action_buys_after_rebound_confirmation() -> None:
    prices = [10, 10, 10, 10, 10, 12, 14, 12, 10, 8]

    assert price_action_live_signal(prices, 4, has_position=False) == Signal.HOLD
    assert price_action_live_signal(prices, 5, has_position=False) == Signal.BUY


def test_price_action_sells_when_sell_above_dip_target_is_reached() -> None:
    prices = [10, 10, 10, 10, 10, 12, 14, 12, 10, 8]

    assert (
        price_action_live_signal(
            prices,
            6,
            has_position=True,
            entry_price=12,
            highest_since_entry=14,
            dip_reference_price=10,
            sell_above_dip_percent=30,
        )
        == Signal.SELL
    )
