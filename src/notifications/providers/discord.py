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
class DiscordConfig:
    webhook_url: str = ""


class DiscordProvider(NotificationProvider):
    name = "discord"
    user_agent = "RobinhoodPaperTrader/0.1 (+https://discord.com)"

    def __init__(
        self,
        config: DiscordConfig,
        opener: Callable[..., object] = urlopen,
    ) -> None:
        self.config = config
        self.opener = opener

    @property
    def is_configured(self) -> bool:
        return bool(self.config.webhook_url)

    def format_message(self, event: NotificationEvent) -> str:
        parts = [
            "**[Robinhood Paper Trader]**",
            f"Event: `{event.event_type}`",
            f"Severity: `{event.severity.value}`",
        ]
        if event.symbol:
            parts.append(f"Symbol: `{event.symbol}`")
        if event.message:
            parts.append(event.message)
        for key, value in (event.details or {}).items():
            parts.append(f"{key.replace('_', ' ').title()}: `{value}`")
        return "\n".join(parts)

    def build_payload(self, event: NotificationEvent) -> dict[str, str]:
        return {"content": self.format_message(event)}

    def build_request(self, event: NotificationEvent) -> Request:
        return Request(
            self.config.webhook_url,
            data=json.dumps(self.build_payload(event)).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": self.user_agent,
            },
            method="POST",
        )

    def send(self, event: NotificationEvent) -> ProviderResponse:
        if not self.is_configured:
            return ProviderResponse(DeliveryStatus.FAILED, "Discord webhook is not configured")
        request = self.build_request(event)
        try:
            with self.opener(request, timeout=10) as response:
                response.read()
            return ProviderResponse(DeliveryStatus.SENT)
        except Exception as exc:  # pragma: no cover
            return ProviderResponse(DeliveryStatus.FAILED, str(exc))
