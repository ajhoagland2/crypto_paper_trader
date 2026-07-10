from __future__ import annotations

import os
import re
import sys
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Deque, Dict, Optional, Union

from src.config import load_dotenv
from src.data.database import ResearchDatabase
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


EVENT_TYPES = {
    "SYSTEM_STARTED",
    "SYSTEM_STOPPED",
    "SIMULATION_STARTED",
    "SIMULATION_STOPPED",
    "PAPER_TRADE_EXECUTED",
    "SHADOW_TRADE_PROPOSED",
    "SHADOW_TRADE_REJECTED",
    "TRADE_NOT_GOING_WELL",
    "TRADE_STATUS_UPDATE",
    "HOURLY_PERFORMANCE_UPDATE",
    "HOURLY_RECAP_CARD",
    "HIGH_CONFIDENCE_TRADE",
    "TARGET_PROFIT_REACHED",
    "MOMENTUM_WEAKENED",
    "RISK_MANAGER_FORCED_EXIT",
    "CATASTROPHIC_LOSS_THRESHOLD_REACHED",
    "KILL_SWITCH_ACTIVATED",
    "API_ERROR",
    "ACCOUNT_READ_ERROR",
    "HEARTBEAT",
    "MANUAL_CONTROL",
}

SENSITIVE_KEY_PATTERN = re.compile(
    r"(api|auth|token|secret|password|private|credential|account|sid|key|balance)",
    re.IGNORECASE,
)
SENSITIVE_VALUE_PATTERN = re.compile(
    r"(?i)(api[_ -]?key|auth[_ -]?token|private[_ -]?key|password|secret|account[_ -]?sid)"
    r"\s*[:=]\s*[^,\s|]+"
)


class NullNotificationProvider(NotificationProvider):
    name = "none"

    def format_message(self, event: NotificationEvent) -> str:
        return event.message

    def build_payload(self, event: NotificationEvent) -> dict[str, str]:
        return {"message": self.format_message(event)}

    def send(self, event: NotificationEvent) -> ProviderResponse:
        del event
        return ProviderResponse(DeliveryStatus.LOGGED_ONLY)


class Notifier:
    def __init__(
        self,
        database: ResearchDatabase,
        provider: NotificationProvider,
        enabled: bool = False,
        rate_limit_per_minute: int = 10,
        deduplication_seconds: int = 300,
        heartbeat_interval_minutes: int = 60,
    ) -> None:
        self.database = database
        self.provider = provider
        self.enabled = enabled
        self.rate_limit_per_minute = max(1, int(rate_limit_per_minute))
        self.deduplication_window = timedelta(seconds=max(0, int(deduplication_seconds)))
        self.heartbeat_interval = timedelta(minutes=max(1, int(heartbeat_interval_minutes)))
        self._recent_send_times: Deque[datetime] = deque()
        self._last_fingerprints: Dict[str, datetime] = {}
        self._last_heartbeat_at: Optional[datetime] = None

    def notify(
        self,
        event_type: str,
        message: str,
        severity: Union[NotificationSeverity, str] = NotificationSeverity.INFO,
        symbol: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        now: Optional[datetime] = None,
    ) -> ProviderResponse:
        now = now or datetime.now(timezone.utc)
        normalized_event_type = event_type.strip().upper()
        normalized_severity = (
            severity if isinstance(severity, NotificationSeverity) else NotificationSeverity(str(severity).upper())
        )
        event = NotificationEvent(
            event_type=normalized_event_type,
            severity=normalized_severity,
            message=redact_sensitive_text(message),
            symbol=symbol.upper() if symbol else None,
            details=redact_sensitive_data(details or {}),
        )
        formatted_message = self.provider.format_message(event)

        if not self.enabled:
            response = ProviderResponse(DeliveryStatus.DISABLED)
            self._log_attempt(now, event, formatted_message, response)
            return response

        fingerprint = self._fingerprint(event)
        last_sent_at = self._last_fingerprints.get(fingerprint)
        if last_sent_at and now - last_sent_at < self.deduplication_window:
            response = ProviderResponse(DeliveryStatus.DEDUPLICATED)
            self._log_attempt(now, event, formatted_message, response)
            return response

        if self._is_rate_limited(now):
            response = ProviderResponse(DeliveryStatus.RATE_LIMITED)
            self._log_attempt(now, event, formatted_message, response)
            return response

        response = self.provider.send(event)
        if response.status == DeliveryStatus.SENT:
            self._recent_send_times.append(now)
            self._last_fingerprints[fingerprint] = now
        self._log_attempt(now, event, formatted_message, response)
        return response

    def heartbeat_due(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(timezone.utc)
        return (
            self._last_heartbeat_at is None
            or now - self._last_heartbeat_at >= self.heartbeat_interval
        )

    def send_heartbeat(self, now: Optional[datetime] = None) -> ProviderResponse:
        now = now or datetime.now(timezone.utc)
        self._last_heartbeat_at = now
        return self.notify(
            "HEARTBEAT",
            "Paper/shadow trading monitor is still running.",
            NotificationSeverity.INFO,
            details={"mode": "SHADOW", "status": "No live order submitted"},
            now=now,
        )

    def maybe_send_heartbeat(self, now: Optional[datetime] = None) -> Optional[ProviderResponse]:
        now = now or datetime.now(timezone.utc)
        if not self.heartbeat_due(now):
            return None
        return self.send_heartbeat(now)

    def _is_rate_limited(self, now: datetime) -> bool:
        window_start = now - timedelta(minutes=1)
        while self._recent_send_times and self._recent_send_times[0] <= window_start:
            self._recent_send_times.popleft()
        return len(self._recent_send_times) >= self.rate_limit_per_minute

    def _fingerprint(self, event: NotificationEvent) -> str:
        detail_items = tuple(sorted((event.details or {}).items()))
        return repr((event.event_type, event.severity.value, event.message, event.symbol, detail_items))

    def _log_attempt(
        self,
        timestamp: datetime,
        event: NotificationEvent,
        message: str,
        response: ProviderResponse,
    ) -> None:
        self.database.log_notification_event(
            provider=self.provider.name,
            event_type=event.event_type,
            severity=event.severity.value,
            message=message,
            delivery_status=response.status.value,
            error_message=redact_sensitive_text(response.error_message),
            timestamp=timestamp,
        )


def redact_sensitive_data(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: Dict[str, Any] = {}
        for key, item in value.items():
            if SENSITIVE_KEY_PATTERN.search(str(key)):
                cleaned[key] = "[REDACTED]"
            else:
                cleaned[key] = redact_sensitive_data(item)
        return cleaned
    if isinstance(value, list):
        return [redact_sensitive_data(item) for item in value]
    if isinstance(value, str):
        return redact_sensitive_text(value)
    return value


def redact_sensitive_text(value: str) -> str:
    return SENSITIVE_VALUE_PATTERN.sub(
        lambda match: match.group(0).split("=", 1)[0].split(":", 1)[0] + "=[REDACTED]",
        value,
    )


def build_provider_from_env(provider_name: str) -> NotificationProvider:
    normalized = provider_name.strip().lower()
    if normalized == "telegram":
        return TelegramProvider(
            TelegramConfig(
                bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
                chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            )
        )
    if normalized == "discord":
        return DiscordProvider(DiscordConfig(webhook_url=os.getenv("DISCORD_WEBHOOK_URL", "")))
    if normalized == "pushover":
        return PushoverProvider(
            PushoverConfig(
                user_key=os.getenv("PUSHOVER_USER_KEY", ""),
                api_token=os.getenv("PUSHOVER_API_TOKEN", ""),
            )
        )
    if normalized in {"email_sms", "email-to-sms", "email"}:
        return EmailSmsProvider(
            EmailSmsConfig(
                email_sms_address=os.getenv("EMAIL_SMS_ADDRESS", ""),
                smtp_host=os.getenv("SMTP_HOST", ""),
                smtp_port=int(os.getenv("SMTP_PORT", "587")),
                smtp_username=os.getenv("SMTP_USERNAME", ""),
                smtp_password=os.getenv("SMTP_PASSWORD", ""),
            )
        )
    if normalized in {"twilio", "twilio_sms", "sms"}:
        return TwilioSmsProvider(
            TwilioSmsConfig(
                account_sid=os.getenv("TWILIO_ACCOUNT_SID", ""),
                auth_token=os.getenv("TWILIO_AUTH_TOKEN", ""),
                from_number=os.getenv("TWILIO_FROM_NUMBER", ""),
                to_number=os.getenv("TWILIO_TO_NUMBER", os.getenv("SMS_TO_NUMBER", "")),
            )
        )
    return NullNotificationProvider()


def build_notifier(database: ResearchDatabase) -> Notifier:
    load_dotenv()
    provider = build_provider_from_env(os.getenv("NOTIFICATION_PROVIDER", "telegram"))
    return Notifier(
        database=database,
        provider=provider,
        enabled=os.getenv("NOTIFICATION_ENABLED", "false").lower() == "true",
        rate_limit_per_minute=int(os.getenv("NOTIFICATION_RATE_LIMIT_PER_MINUTE", "10")),
        deduplication_seconds=int(os.getenv("NOTIFICATION_DEDUP_SECONDS", "300")),
        heartbeat_interval_minutes=int(os.getenv("HEARTBEAT_INTERVAL_MINUTES", "60")),
    )


def _send_test_notification() -> int:
    load_dotenv()
    database_path = os.getenv("DATABASE_PATH", "research_agent.sqlite3")
    with ResearchDatabase(database_path) as database:
        notifier = build_notifier(database)
        response = notifier.notify(
            "HEARTBEAT",
            "Test notification from Robinhood Paper Trader.",
            NotificationSeverity.INFO,
            details={"mode": "SHADOW", "status": "Logged only - no live order submitted"},
        )
    print(f"Notification provider: {notifier.provider.name}")
    print(f"Delivery status: {response.status.value}")
    if response.error_message:
        print(f"Error: {response.error_message}")
    return 0 if response.status in {DeliveryStatus.SENT, DeliveryStatus.DISABLED} else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        raise SystemExit(_send_test_notification())
    print("Usage: python -m src.notifications.notifier test")
    raise SystemExit(2)
