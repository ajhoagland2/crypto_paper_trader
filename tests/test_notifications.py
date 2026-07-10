from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.data.database import ResearchDatabase
from src.notifications.notifier import Notifier, redact_sensitive_data
from src.notifications.providers.base import (
    DeliveryStatus,
    NotificationEvent,
    NotificationProvider,
    NotificationSeverity,
    ProviderResponse,
)
from src.notifications.providers.discord import DiscordConfig, DiscordProvider
from src.notifications.providers.email_sms import EmailSmsConfig, EmailSmsProvider
from src.notifications.providers.pushover import PushoverConfig, PushoverProvider
from src.notifications.providers.telegram import TelegramConfig, TelegramProvider
from src.notifications.providers.twilio_sms import TwilioSmsConfig, TwilioSmsProvider


class FakeProvider(NotificationProvider):
    name = "fake"

    def __init__(self) -> None:
        self.sent = 0

    @property
    def is_configured(self) -> bool:
        return True

    def format_message(self, event: NotificationEvent) -> str:
        details = event.details or {}
        detail_text = " ".join(f"{key}={value}" for key, value in details.items())
        return f"{event.event_type} {event.severity.value} {event.message} {detail_text}".strip()

    def build_payload(self, event: NotificationEvent) -> dict[str, str]:
        return {"message": self.format_message(event)}

    def send(self, event: NotificationEvent) -> ProviderResponse:
        self.sent += 1
        return ProviderResponse(DeliveryStatus.SENT)


def event() -> NotificationEvent:
    return NotificationEvent(
        event_type="HIGH_CONFIDENCE_TRADE",
        severity=NotificationSeverity.INFO,
        message="High-confidence paper buy candidate detected.",
        symbol="DOGE",
        details={
            "action": "BUY_CANDIDATE",
            "momentum_score": 87,
            "mode": "SHADOW",
            "status": "Logged only - no live order submitted",
        },
    )


def test_disabled_notifications_are_logged_without_sending(tmp_path) -> None:
    provider = FakeProvider()
    with ResearchDatabase(str(tmp_path / "events.sqlite3")) as database:
        notifier = Notifier(database, provider, enabled=False)

        response = notifier.notify("HEARTBEAT", "still running")

        rows = database.recent_notification_events()

    assert response.status == DeliveryStatus.DISABLED
    assert provider.sent == 0
    assert rows[0]["delivery_status"] == "disabled"
    assert rows[0]["event_type"] == "HEARTBEAT"


def test_telegram_message_formatting() -> None:
    provider = TelegramProvider(TelegramConfig(bot_token="token", chat_id="chat"))

    payload = provider.build_payload(event())

    assert payload["chat_id"] == "chat"
    assert "[Robinhood Paper Trader]" in payload["text"]
    assert "Event: HIGH_CONFIDENCE_TRADE" in payload["text"]
    assert "Symbol: DOGE" in payload["text"]
    assert "Momentum Score: 87" in payload["text"]


def test_discord_webhook_payload_formatting() -> None:
    provider = DiscordProvider(DiscordConfig(webhook_url="https://discord.test/webhook"))

    payload = provider.build_payload(event())
    request = provider.build_request(event())

    assert "content" in payload
    assert "Event: `HIGH_CONFIDENCE_TRADE`" in payload["content"]
    assert "Symbol: `DOGE`" in payload["content"]
    assert request.get_header("User-agent") == provider.user_agent
    assert request.get_header("Accept") == "application/json"
    assert request.get_header("Content-type") == "application/json; charset=utf-8"


def test_pushover_payload_formatting() -> None:
    provider = PushoverProvider(PushoverConfig(user_key="user", api_token="token"))

    payload = provider.build_payload(event())

    assert payload["token"] == "token"
    assert payload["user"] == "user"
    assert payload["title"] == "Robinhood Paper Trader"
    assert payload["priority"] == 0
    assert "HIGH_CONFIDENCE_TRADE" in str(payload["message"])


def test_email_to_sms_formatting() -> None:
    provider = EmailSmsProvider(
        EmailSmsConfig(
            email_sms_address="15551234567@carrier.example",
            smtp_host="smtp.example",
            smtp_username="sender@example.com",
            smtp_password="password",
        )
    )

    payload = provider.build_payload(event())

    assert payload["To"] == "15551234567@carrier.example"
    assert payload["From"] == "sender@example.com"
    assert "HIGH_CONFIDENCE_TRADE INFO" in payload.get_content()


def test_twilio_sms_formatting() -> None:
    provider = TwilioSmsProvider(
        TwilioSmsConfig(
            account_sid="sid",
            auth_token="token",
            from_number="+15551234567",
            to_number="+15557654321",
        )
    )

    payload = provider.build_payload(event())

    assert payload["From"] == "+15551234567"
    assert payload["To"] == "+15557654321"
    assert "HIGH_CONFIDENCE_TRADE" in payload["Body"]
    assert "DOGE" in payload["Body"]


def test_notification_deduplication(tmp_path) -> None:
    provider = FakeProvider()
    now = datetime(2026, 6, 30, tzinfo=timezone.utc)
    with ResearchDatabase(str(tmp_path / "events.sqlite3")) as database:
        notifier = Notifier(database, provider, enabled=True, deduplication_seconds=300)

        first = notifier.notify("API_ERROR", "same alert", NotificationSeverity.WARNING, now=now)
        second = notifier.notify(
            "API_ERROR",
            "same alert",
            NotificationSeverity.WARNING,
            now=now + timedelta(seconds=30),
        )

    assert first.status == DeliveryStatus.SENT
    assert second.status == DeliveryStatus.DEDUPLICATED
    assert provider.sent == 1


def test_notification_rate_limiting(tmp_path) -> None:
    provider = FakeProvider()
    now = datetime(2026, 6, 30, tzinfo=timezone.utc)
    with ResearchDatabase(str(tmp_path / "events.sqlite3")) as database:
        notifier = Notifier(
            database,
            provider,
            enabled=True,
            rate_limit_per_minute=1,
            deduplication_seconds=0,
        )

        first = notifier.notify("API_ERROR", "first", NotificationSeverity.WARNING, now=now)
        second = notifier.notify(
            "ACCOUNT_READ_ERROR",
            "second",
            NotificationSeverity.WARNING,
            now=now + timedelta(seconds=10),
        )

    assert first.status == DeliveryStatus.SENT
    assert second.status == DeliveryStatus.RATE_LIMITED
    assert provider.sent == 1


def test_database_logging(tmp_path) -> None:
    with ResearchDatabase(str(tmp_path / "events.sqlite3")) as database:
        notifier = Notifier(database, FakeProvider(), enabled=True)

        notifier.notify("KILL_SWITCH_ACTIVATED", "Kill switch enabled", NotificationSeverity.CRITICAL)
        rows = database.recent_notification_events()

    assert rows[0]["provider"] == "fake"
    assert rows[0]["event_type"] == "KILL_SWITCH_ACTIVATED"
    assert rows[0]["severity"] == "CRITICAL"
    assert rows[0]["delivery_status"] == "sent"


def test_secret_redaction(tmp_path) -> None:
    with ResearchDatabase(str(tmp_path / "events.sqlite3")) as database:
        notifier = Notifier(database, FakeProvider(), enabled=True)

        notifier.notify(
            "API_ERROR",
            "api_key=abc123 failed",
            NotificationSeverity.WARNING,
            details={
                "api_token": "super-secret",
                "account_number": "123456",
                "safe": "visible",
            },
        )
        row = database.recent_notification_events()[0]

    assert "abc123" not in row["message"]
    assert "super-secret" not in row["message"]
    assert "123456" not in row["message"]
    assert "visible" in row["message"]
    assert redact_sensitive_data({"balance": "999", "symbol": "DOGE"})["balance"] == "[REDACTED]"
