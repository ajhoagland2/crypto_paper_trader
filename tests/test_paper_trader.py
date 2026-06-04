import pytest

from src.trading.paper_trader import PaperTrader


def test_buy_sell_tracks_cash_positions_and_pl() -> None:
    trader = PaperTrader(starting_cash=1_000)

    trader.buy("BTC-USD", quantity=2, price=100)
    trade = trader.sell("BTC-USD", quantity=1, price=125)

    assert trader.cash == 925
    assert trader.positions["BTC-USD"].quantity == 1
    assert trade.realized_pl == 25
    assert trader.realized_pl() == 25
    assert trader.gain_reserve == 3.75
    assert trader.available_cash == 921.25
    assert trader.unrealized_pl({"BTC-USD": 150}) == 50


def test_gain_reserve_only_applies_to_profitable_sells() -> None:
    trader = PaperTrader(starting_cash=1_000, gain_reserve_percent=15)

    trader.buy("BTC-USD", quantity=1, price=100)
    trader.sell("BTC-USD", quantity=1, price=80)

    assert trader.realized_pl() == -20
    assert trader.gain_reserve == 0


def test_rejects_oversized_sell() -> None:
    trader = PaperTrader(starting_cash=1_000)

    with pytest.raises(ValueError, match="insufficient paper position"):
        trader.sell("BTC-USD", quantity=1, price=100)
