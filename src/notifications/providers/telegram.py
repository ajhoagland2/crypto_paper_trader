from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable
from urllib.request import Request, urlopen

from src.notifications.providers.base import (
    DeliveryStatus,
    NotificationEvent,
    NotificationProvider,
    ProviderResponse,
)


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str = ""
    chat_id: str = ""


class TelegramProvider(NotificationProvider):
    name = "telegram"

    def __init__(
        self,
        config: TelegramConfig,
        opener: Callable[..., object] = urlopen,
    ) -> None:
        self.config = config
        self.opener = opener

    @property
    def is_configured(self) -> bool:
        return bool(self.config.bot_token and self.config.chat_id)

    def format_message(self, event: NotificationEvent) -> str:
        lines = [
            "[Robinhood Paper Trader]",
            f"Event: {event.event_type}",
        ]
        if event.symbol:
            lines.append(f"Symbol: {event.symbol}")
        lines.append(f"Severity: {event.severity.value}")
        if event.message:
            lines.append(event.message)
        for key, value in (event.details or {}).items():
            label = key.replace("_", " ").title()
            lines.append(f"{label}: {value}")
        return "\n".join(lines)

    def build_payload(self, event: NotificationEvent) -> dict[str, object]:
        return {
            "chat_id": self.config.chat_id,
            "text": self.format_message(event),
            "disable_web_page_preview": True,
        }

    def send(self, event: NotificationEvent) -> ProviderResponse:
        if not self.is_configured:
            return ProviderResponse(DeliveryStatus.FAILED, "Telegram is not configured")
        endpoint = f"https://api.telegram.org/bot{self.config.bot_token}/sendMessage"
        request = Request(
            endpoint,
            data=json.dumps(self.build_payload(event)).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self.opener(request, timeout=10) as response:
                response.read()
            return ProviderResponse(DeliveryStatus.SENT)
        except Exception as exc:  # pragma: no cover - exercised through Notifier tests
            return ProviderResponse(DeliveryStatus.FAILED, str(exc))
