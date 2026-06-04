# robinhood-crypto-research-agent

A safety-first Python scaffold for researching the official Robinhood Crypto Trading API and testing crypto strategies in paper mode.

This project is intentionally read-only against Robinhood in milestone 1. It can collect market/account data, generate starter strategy signals, simulate trades, enforce risk controls, and log research events to SQLite. It does not place live orders.

The active paper simulation uses a live-style optimizer that only uses current and past prices. It waits for rebound confirmation before buying, then exits with profit target, stop loss, trailing stop, momentum rollover, or end-of-paper-session rules.

Profitable paper exits can move a configurable percentage of realized gains into a gain reserve. This is a planning bucket for a possible capital-gains safety net, not tax advice.

## Safety Architecture

- Official Robinhood Crypto Trading API docs are the source of truth.
- No unofficial Robinhood libraries are used.
- Credentials are loaded from environment variables or `.env`, never hardcoded.
- `TRADING_MODE=paper` is the default.
- Live trading raises an error in `src/main.py`.
- The API wrapper exposes read-only methods only: market quote, account, and holdings.
- Risk checks run before simulated trades.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .[dev]
```

Copy `.env.example` to `.env` and fill in your Robinhood Crypto API credentials:

```text
ROBINHOOD_API_KEY=your-api-key-here
ROBINHOOD_PRIVATE_KEY=your-base64-private-key-here
ROBINHOOD_API_BASE_URL=https://trading.robinhood.com
TRADING_MODE=paper
```

Robinhood's official documentation describes authenticated requests using `x-api-key`, `x-signature`, and `x-timestamp` headers. Real request signing needs PyNaCl:

```powershell
python -m pip install -e .[crypto-signing]
```

## Run

Run the built-in paper simulation:

```powershell
python -m src.main
```

Open the static web interface:

```powershell
python -m http.server 8766 --directory docs
```

Then visit `http://localhost:8766`.

Run tests:

```powershell
python -m pytest
```

## Project Layout

```text
src/config.py                  Environment and settings
src/robinhood/auth.py          Auth header and signature structure
src/robinhood/client.py        Read-only Robinhood Crypto API wrapper
src/robinhood/market_data.py   Market data service
src/robinhood/account.py       Account and holdings service
src/strategy/indicators.py     Starter indicators
src/strategy/signals.py        BUY/SELL/HOLD signal generation
src/trading/paper_trader.py    Paper trading engine
src/risk/risk_manager.py       Risk controls and trade rejection
src/data/database.py           SQLite event logging
src/main.py                    Basic paper-trading simulation
docs/                          GitHub Pages web interface
tests/                         Unit tests
```

## GitHub Pages

The web interface lives in `docs/` so it can be published with GitHub Pages by choosing the `docs` folder as the Pages source in the repository settings. It is a static browser app, so it does not send credentials anywhere and does not call the Robinhood API directly.

## Why Live Trading Is Disabled

Live order placement is intentionally excluded from milestone 1. Before live trading could be considered, this project would need deeper API conformance tests, secure key handling, production-grade monitoring, stronger risk governance, audit logs, manual approval flows, and a separate review of Robinhood's current API terms and account requirements. Nothing here is financial advice, and no strategy is expected or guaranteed to be profitable.
