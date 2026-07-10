# robinhood-crypto-research-agent

A safety-first Python scaffold for researching the official Robinhood Crypto Trading API and testing crypto strategies in paper mode, with explicitly gated live crypto order submission for Epic_02.

This project can collect market/account data, generate starter strategy signals, simulate trades, enforce risk controls, and log research events to SQLite. Paper mode is still the default. Live orders require `TRADING_MODE=live` and an explicit acknowledgement flag.

The active paper simulation uses a live-style 30-second momentum optimizer that only uses current and past prices. It keeps a rolling 5-minute market-data window, scores each symbol from 0 to 100, buys only when confidence and risk rules agree, then exits on target profit, weakening momentum, risk controls, or catastrophic loss.

Profitable paper exits can move a configurable percentage of realized gains into a gain reserve. This is a planning bucket for a possible capital-gains safety net, not tax advice.

The local Python runner can also poll real Robinhood Crypto market data, evaluate the control stack, execute paper trades or explicitly gated live trades, persist simulation history, and optionally send personal-device notifications. Telegram is the default free notification provider.

## Safety Architecture

- Official Robinhood Crypto Trading API docs are the source of truth.
- No unofficial Robinhood libraries are used.
- Credentials are loaded from environment variables or `.env`, never hardcoded.
- `TRADING_MODE=paper` is the default.
- Live trading also requires `LIVE_TRADING_ACKNOWLEDGEMENT=I_UNDERSTAND_LIVE_ORDERS`.
- Live order size is capped by `LIVE_MAX_ORDER_NOTIONAL` in addition to normal strategy and risk limits.
- The API wrapper exposes market quote, account, holdings, and market order placement.
- Risk checks run before paper or live-mirrored trades.

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
LIVE_TRADING_ACKNOWLEDGEMENT=
LIVE_MAX_ORDER_NOTIONAL=25
CRYPTO_SYMBOLS=BTC-USD,ETH-USD,SOL-USD,DOGE-USD,XLM-USD,ATOM-USD
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

Run the real-data paper trading loop:

```powershell
python -m src.main live-paper
```

That mode signs Robinhood API requests, pulls best bid/ask market data, runs the control stack, and records mirrored trades to SQLite. With `TRADING_MODE=paper`, no live orders are placed. With `TRADING_MODE=live` and the acknowledgement flag set, the runner submits Robinhood market orders before updating the local mirrored ledger.

Enable live trading only after reviewing the strategy and risk settings:

```text
TRADING_MODE=live
LIVE_TRADING_ACKNOWLEDGEMENT=I_UNDERSTAND_LIVE_ORDERS
LIVE_MAX_ORDER_NOTIONAL=25
```

The live runner uses Robinhood market orders with asset quantity, then records the accepted order response as a `live_order` event in SQLite. If live order submission fails, the local position mirror is not updated.

Open the static web interface:

```powershell
python -m http.server 8766 --directory docs
```

Then visit `http://localhost:8766`.

Run the API-backed web interface:

```powershell
python -m src.web_server
```

You can also double-click `launch_web_app.bat` from the project folder. Then visit `http://localhost:8766`. This is the recommended local workflow once `.env` is configured. The page can check local configuration, pull real Robinhood market data, run control-stack ticks, reset the local session, and start a browser-driven auto loop. PowerShell or the launcher is only hosting the local app; the trader controls are in the browser.

Configure web-console sign-in before using the API-backed page:

```text
WEB_AUTH_ENABLED=true
WEB_AUTH_EMAIL=operator@example.com
WEB_AUTH_PASSWORD=use-a-long-local-password
WEB_AUTH_STORE_PATH=web_auth_store.json
```

The sign-in session uses an HTTP-only local cookie. The forgot-password flow sends a recovery token through the existing SMTP settings when `SMTP_HOST`, `SMTP_USERNAME`, and related fields are configured. If SMTP is not configured, the local page shows the reset token so you can recover during development. Successful password resets write a hashed password to `WEB_AUTH_STORE_PATH`.

Run tests:

```powershell
python -m pytest
```

## Control Stack

The real-data paper loop reads these values from `.env`:

```text
STARTING_CASH=10000
MAX_TRADE_SIZE=1000
MAX_DAILY_LOSS=500
MAX_TRADES_PER_DAY=10
COOLDOWN_AFTER_LOSS_SECONDS=60
KILL_SWITCH=false
GAIN_RESERVE_PERCENT=15
POLL_INTERVAL_SECONDS=60
API_REQUESTS_PER_MINUTE=60
API_MIN_REQUEST_INTERVAL_SECONDS=1.0
MOMENTUM_LOOKBACK_MINUTES=5
MOMENTUM_INTERVAL_SECONDS=30
MOMENTUM_ENTRY_THRESHOLD=80
MOMENTUM_EXIT_THRESHOLD=40
TARGET_PROFIT_PCT=0.25
PAPER_ALLOCATION_PER_TRADE=100
CATASTROPHIC_LOSS_PCT=-3
SOFT_STOP_LOSS_PCT=-1
MOMENTUM_WEIGHT_TREND=0.25
MOMENTUM_WEIGHT_ACCELERATION=0.25
MOMENTUM_WEIGHT_Z_SCORE=0.25
MOMENTUM_WEIGHT_MA_SLOPE=0.15
MOMENTUM_WEIGHT_VOLATILITY=0.10
PYRAMIDING_ENABLED=false
```

The trader only opens a paper position when the momentum score clears the entry threshold and the risk manager approves the order. Trend, acceleration, 30-second return, z-score, moving-average slope, and volatility stability influence the score instead of acting as separate buy gates. It exits when price reaches the configured target above the entry reference, when the score falls below the exit threshold, or when risk and catastrophic-loss rules trigger. Risk limits are evaluated before every simulated trade.

## Momentum Strategy

Each symbol has its own 5-minute rolling price buffer. Inside that buffer, the strategy samples 30-second intervals and computes the current 30-second return, mean interval return, standard deviation, z-score, 5-minute trend, acceleration, moving-average slope, and volatility stability.

Those inputs become a 0-100 Momentum Confidence Score:

```text
trend                 25%
30-second acceleration 25%
z-score               25%
moving-average slope  15%
volatility stability  10%
```

The weights are configurable with the `MOMENTUM_WEIGHT_*` environment variables. The default entry threshold is 80 and the default exit threshold is 40.

The 30-second interval is not a forced trade timer. It is the measurement period for momentum. If the score stays strong after an interval expires, the paper position can keep holding. A new buy only happens when the configured entry conditions are met against live market data.

## Saved Simulation History

SQLite stores market data, strategy signals, simulated trades, rejected trades, API errors, momentum calculations, and long-running simulation summaries. Runs longer than 30 minutes persist summary snapshots, and the local API exposes recent summaries at:

```text
http://localhost:8766/api/history
```

## Personal-Device Notifications

Important paper/shadow trading alerts can be sent to a personal device. Telegram is the recommended free option, with Discord, Pushover, email-to-SMS, and Twilio SMS available as fallbacks.

```text
NOTIFICATION_ENABLED=false
NOTIFICATION_PROVIDER=telegram
NOTIFICATION_RATE_LIMIT_PER_MINUTE=10
NOTIFICATION_DEDUP_SECONDS=300
NOTIFICATION_SUMMARY_INTERVAL_MINUTES=60
NOTIFICATION_TRADE_UPDATE_INTERVAL_MINUTES=30
NOTIFICATION_RECAP_INTERVAL_MINUTES=60
HEARTBEAT_INTERVAL_MINUTES=60
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
DISCORD_WEBHOOK_URL=
DISCORD_CONTROL_ENABLED=false
DISCORD_BOT_TOKEN=
DISCORD_CONTROL_CHANNEL_ID=
DISCORD_CONTROL_POLL_SECONDS=5
DISCORD_CONTROL_PREFIX=!trader
PUSHOVER_USER_KEY=
PUSHOVER_API_TOKEN=
EMAIL_SMS_ADDRESS=
SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_FROM_NUMBER=
TWILIO_TO_NUMBER=
```

Keep `NOTIFICATION_ENABLED=false` until the chosen provider is configured. Every notification attempt is recorded in SQLite table `notification_events`, including provider, event type, severity, redacted message, delivery status, and any redacted error message.

The live-data paper loop sends notifications when a paper trade executes, when an open paper trade starts moving into soft-stop territory, every 30 minutes with the same SQLite session-card fields shown in the Saved Sessions tab, and once per hour with a recap card. The 30-minute and hourly cards include symbols, session status, outcome, total return, realized P/L, max drawdown, win rate, average win/loss, trade count, best/worst trade, buys, open lots, and rejected reasons.

## Manual Controls And Analytics

The Paper Trader view includes risk/performance analytics for win rate, profit factor, total return, open exposure, average win/loss, best/worst trade, and closed-trade count. The same view also exposes manual controls for the active runner:

```text
Pause Bot
Resume Bot
Kill Switch On
Kill Switch Off
Force Exit Symbol
```

Discord control is optional and separate from the notification webhook. Create a Discord bot, add it to a private control channel, set `DISCORD_CONTROL_ENABLED=true`, `DISCORD_BOT_TOKEN`, and `DISCORD_CONTROL_CHANNEL_ID`, then start the web server. Supported commands:

```text
!trader status
!trader analytics
!trader pause optional reason
!trader resume
!trader kill on
!trader kill off
!trader exit BTC-USD optional reason
!trader exit all optional reason
```

These commands only affect the paper/shadow runner. They do not place live orders.

Recommended Telegram setup:

1. Create a bot with Telegram `@BotFather`.
2. Send the bot a message.
3. Visit `https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/getUpdates` and copy your chat id.
4. Set `NOTIFICATION_ENABLED=true`, `NOTIFICATION_PROVIDER=telegram`, `TELEGRAM_BOT_TOKEN`, and `TELEGRAM_CHAT_ID`.

Send a test notification:

```powershell
python -m src.notifications.notifier test
```

More setup details are in `docs/notifications.md`. Notifications summarize paper/shadow events only and redact secrets, credentials, account identifiers, and balance-like fields.

## Project Layout

```text
src/config.py                  Environment and settings
src/robinhood/auth.py          Auth header and signature structure
src/robinhood/client.py        Read-only Robinhood Crypto API wrapper
src/robinhood/market_data.py   Market data service
src/robinhood/account.py       Account and holdings service
src/strategy/indicators.py     Starter indicators
src/strategy/signals.py        BUY/SELL/HOLD signal generation
src/strategy/control_stack.py  User-tunable strategy parameters
src/strategy/momentum.py       30-second momentum confidence strategy
src/trading/paper_trader.py    Paper trading engine
src/trading/live_paper_runner.py Robinhood data paper trading loop
src/risk/risk_manager.py       Risk controls and trade rejection
src/data/database.py           SQLite event logging
src/notifications/notifier.py  Provider-agnostic alert orchestration
src/notifications/providers/   Telegram, Discord, Pushover, email-SMS, and Twilio providers
src/notifications/sms.py       Legacy optional hourly SMS summaries
src/main.py                    Basic paper-trading simulation
docs/                          GitHub Pages web interface
tests/                         Unit tests
```

## GitHub Pages

The web interface lives in `docs/` so it can be published with GitHub Pages by choosing the `docs` folder as the Pages source in the repository settings. It is a static browser app, so it does not send credentials anywhere and does not call the Robinhood API directly.

## Why Live Trading Is Disabled

Live order placement is now available only through explicit Epic_02 gates. Before using it with meaningful size, this project still needs deeper API conformance testing, secure key handling, production-grade monitoring, stronger risk governance, richer audit logs, manual approval flows, and a separate review of Robinhood's current API terms and account requirements. Nothing here is financial advice, and no strategy is expected or guaranteed to be profitable.
