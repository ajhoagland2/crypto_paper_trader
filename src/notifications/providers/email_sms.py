from __future__ import annotations

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Callable

from src.notifications.providers.base import (
    DeliveryStatus,
    NotificationEvent,
    NotificationProvider,
    ProviderResponse,
)


@dataclass(frozen=True)
class EmailSmsConfig:
    email_sms_address: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""


class EmailSmsProvider(NotificationProvider):
    name = "email_sms"

    def __init__(
        self,
        config: EmailSmsConfig,
        smtp_factory: Callable[..., smtplib.SMTP] = smtplib.SMTP,
    ) -> None:
        self.config = config
        self.smtp_factory = smtp_factory

    @property
    def is_configured(self) -> bool:
        return bool(
            self.config.email_sms_address
            and self.config.smtp_host
            and self.config.smtp_username
            and self.config.smtp_password
        )

    def format_message(self, event: NotificationEvent) -> str:
        parts = [f"{event.event_type} {event.severity.value}"]
        if event.symbol:
            parts.append(event.symbol)
        if event.message:
            parts.append(event.message)
        for key, value in (event.details or {}).items():
            parts.append(f"{key}={value}")
        return " | ".join(parts)[:1500]

    def build_payload(self, event: NotificationEvent) -> EmailMessage:
        message = EmailMessage()
        message["Subject"] = f"Robinhood Paper Trader {event.severity.value}"
        message["From"] = self.config.smtp_username
        message["To"] = self.config.email_sms_address
        message.set_content(self.format_message(event))
        return message

    def send(self, event: NotificationEvent) -> ProviderResponse:
        if not self.is_configured:
            return ProviderResponse(DeliveryStatus.FAILED, "Email-to-SMS is not configured")
        try:
            with self.smtp_factory(self.config.smtp_host, self.config.smtp_port, timeout=10) as smtp:
                smtp.starttls()
                smtp.login(self.config.smtp_username, self.config.smtp_password)
                smtp.send_message(self.build_payload(event))
            return ProviderResponse(DeliveryStatus.SENT)
        except Exception as exc:  # pragma: no cover
            return ProviderResponse(DeliveryStatus.FAILED, str(exc))
