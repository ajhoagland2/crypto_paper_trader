# Personal-Device Notifications

This project can send paper/shadow trading alerts to a personal device. Telegram is the default provider because it is free, reliable, and does not require paid SMS infrastructure.

Notifications are for monitoring only. They do not enable live trading, do not place orders, and should never include Robinhood credentials, API keys, private keys, account numbers, or sensitive balances.

## Recommended Free Setup: Telegram

1. In Telegram, message `@BotFather`.
2. Create a bot with `/newbot`.
3. Copy the bot token into `TELEGRAM_BOT_TOKEN`.
4. Send any message to your new bot.
5. Open this URL in a browser, replacing the token:

```text
https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/getUpdates
```

6. Copy your chat id into `TELEGRAM_CHAT_ID`.
7. Configure `.env`:

```text
NOTIFICATION_ENABLED=true
NOTIFICATION_PROVIDER=telegram
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
TELEGRAM_CHAT_ID=your-chat-id
```

## Discord Webhook Fallback

1. In a private Discord server/channel, create an incoming webhook.
2. Copy the webhook URL.
3. Configure `.env`:

```text
NOTIFICATION_ENABLED=true
NOTIFICATION_PROVIDER=discord
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

## Pushover Setup

Pushover is a reliable paid fallback with a one-time app fee.

```text
NOTIFICATION_ENABLED=true
NOTIFICATION_PROVIDER=pushover
PUSHOVER_USER_KEY=your-user-key
PUSHOVER_API_TOKEN=your-application-token
```

## Optional SMS Setup

Email-to-SMS can work if your carrier gateway is reliable:

```text
NOTIFICATION_ENABLED=true
NOTIFICATION_PROVIDER=email_sms
EMAIL_SMS_ADDRESS=15551234567@carrier-sms-gateway.example
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=sender@example.com
SMTP_PASSWORD=your-smtp-password
```

Twilio is available as a future paid SMS provider:

```text
NOTIFICATION_ENABLED=true
NOTIFICATION_PROVIDER=twilio
TWILIO_ACCOUNT_SID=your-account-sid
TWILIO_AUTH_TOKEN=your-auth-token
TWILIO_FROM_NUMBER=+15551234567
TWILIO_TO_NUMBER=+15557654321
```

## Environment Variables

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

## Safety Notes

- Keep `.env` local and never commit real credentials.
- Notifications summarize paper/shadow events only.
- Messages are sanitized before sending and before database logging.
- Keys, tokens, passwords, account identifiers, and balance-like fields are redacted.
- Rate limiting prevents bursts of alerts.
- Deduplication prevents repeated identical alerts from being sent continuously.
- Every notification attempt is logged in SQLite table `notification_events`.
- The live-data paper loop sends alerts for paper trade executions, struggling open paper trades, 30-minute trade status cards, and hourly recap cards.
- The 30-minute and hourly cards use the same SQLite session-card fields shown in the Saved Sessions tab: symbols, status, outcome, total return, realized P/L, max drawdown, win rate, average win/loss, trade count, best/worst trade, buys, open lots, and rejected reasons.

## Test Notification

After configuring `.env`, run:

```powershell
python -m src.notifications.notifier test
```

If `NOTIFICATION_ENABLED=false`, the test notification is logged but not sent. If enabled, it uses the configured provider.
