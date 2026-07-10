from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional


class NotificationSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class DeliveryStatus(str, Enum):
    DISABLED = "disabled"
    SENT = "sent"
    FAILED = "failed"
    DEDUPLICATED = "deduplicated"
    RATE_LIMITED = "rate_limited"
    LOGGED_ONLY = "logged_only"


@dataclass(frozen=True)
class NotificationEvent:
    event_type: str
    severity: NotificationSeverity
    message: str
    symbol: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class ProviderResponse:
    status: DeliveryStatus
    error_message: str = ""


class NotificationProvider:
    name = "base"

    @property
    def is_configured(self) -> bool:
        return False

    def format_message(self, event: NotificationEvent) -> str:
        raise NotImplementedError

    def build_payload(self, event: NotificationEvent) -> Any:
        raise NotImplementedError

    def send(self, event: NotificationEvent) -> ProviderResponse:
        raise NotImplementedError
