from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from src.notifications.providers.base import (
    DeliveryStatus,
    NotificationEvent,
    NotificationProvider,
    NotificationSeverity,
    ProviderResponse,
)


@dataclass(frozen=True)
class PushoverConfig:
    user_key: str = ""
    api_token: str = ""


class PushoverProvider(NotificationProvider):
    name = "pushover"

    def __init__(
        self,
        config: PushoverConfig,
        opener: Callable[..., object] = urlopen,
    ) -> None:
        self.config = config
        self.opener = opener

    @property
    def is_configured(self) -> bool:
        return bool(self.config.user_key and self.config.api_token)

    def format_message(self, event: NotificationEvent) -> str:
        lines = [f"Event: {event.event_type}", f"Severity: {event.severity.value}"]
        if event.symbol:
            lines.append(f"Symbol: {event.symbol}")
        if event.message:
            lines.append(event.message)
        for key, value in (event.details or {}).items():
            lines.append(f"{key.replace('_', ' ').title()}: {value}")
        return "\n".join(lines)

    def build_payload(self, event: NotificationEvent) -> dict[str, object]:
        priority = 1 if event.severity == NotificationSeverity.CRITICAL else 0
        return {
            "token": self.config.api_token,
            "user": self.config.user_key,
            "title": "Robinhood Paper Trader",
            "message": self.format_message(event),
            "priority": priority,
        }

    def send(self, event: NotificationEvent) -> ProviderResponse:
        if not self.is_configured:
            return ProviderResponse(DeliveryStatus.FAILED, "Pushover is not configured")
        request = Request(
            "https://api.pushover.net/1/messages.json",
            data=urlencode(self.build_payload(event)).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with self.opener(request, timeout=10) as response:
                response.read()
            return ProviderResponse(DeliveryStatus.SENT)
        except Exception as exc:  # pragma: no cover
            return ProviderResponse(DeliveryStatus.FAILED, str(exc))
