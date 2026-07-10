from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from src.notifications.providers.base import (
    DeliveryStatus,
    NotificationEvent,
    NotificationProvider,
    ProviderResponse,
)


@dataclass(frozen=True)
class TwilioSmsConfig:
    account_sid: str = ""
    auth_token: str = ""
    from_number: str = ""
    to_number: str = ""


class TwilioSmsProvider(NotificationProvider):
    name = "twilio_sms"

    def __init__(
        self,
        config: TwilioSmsConfig,
        opener: Callable[..., object] = urlopen,
    ) -> None:
        self.config = config
        self.opener = opener

    @property
    def is_configured(self) -> bool:
        return bool(
            self.config.account_sid
            and self.config.auth_token
            and self.config.from_number
            and self.config.to_number
        )

    def format_message(self, event: NotificationEvent) -> str:
        parts = [f"[Robinhood Paper Trader] {event.event_type} {event.severity.value}"]
        if event.symbol:
            parts.append(event.symbol)
        if event.message:
            parts.append(event.message)
        for key, value in (event.details or {}).items():
            parts.append(f"{key}={value}")
        return " | ".join(parts)[:1500]

    def build_payload(self, event: NotificationEvent) -> dict[str, str]:
        return {
            "From": self.config.from_number,
            "To": self.config.to_number,
            "Body": self.format_message(event),
        }

    def send(self, event: NotificationEvent) -> ProviderResponse:
        if not self.is_configured:
            return ProviderResponse(DeliveryStatus.FAILED, "Twilio SMS is not configured")
        endpoint = (
            "https://api.twilio.com/2010-04-01/Accounts/"
            f"{self.config.account_sid}/Messages.json"
        )
        auth = f"{self.config.account_sid}:{self.config.auth_token}".encode("utf-8")
        request = Request(
            endpoint,
            data=urlencode(self.build_payload(event)).encode("utf-8"),
            headers={
                "Authorization": f"Basic {base64.b64encode(auth).decode('utf-8')}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        try:
            with self.opener(request, timeout=10) as response:
                response.read()
            return ProviderResponse(DeliveryStatus.SENT)
        except Exception as exc:  # pragma: no cover
            return ProviderResponse(DeliveryStatus.FAILED, str(exc))
