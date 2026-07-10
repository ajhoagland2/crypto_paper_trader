import base64
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class SmsConfig:
    enabled: bool = False
    account_sid: str = ""
    auth_token: str = ""
    from_number: str = ""
    to_number: str = ""

    @property
    def is_configured(self) -> bool:
        return bool(
            self.enabled
            and self.account_sid
            and self.auth_token
            and self.from_number
            and self.to_number
        )


class SmsNotifier:
    """Optional Twilio SMS notifier using only the Python standard library."""

    def __init__(self, config: SmsConfig) -> None:
        self.config = config

    def send(self, message: str) -> bool:
        if not self.config.is_configured:
            return False

        endpoint = (
            "https://api.twilio.com/2010-04-01/Accounts/"
            f"{self.config.account_sid}/Messages.json"
        )
        payload = urlencode(
            {
                "From": self.config.from_number,
                "To": self.config.to_number,
                "Body": message,
            }
        ).encode("utf-8")
        auth = f"{self.config.account_sid}:{self.config.auth_token}".encode("utf-8")
        headers = {
            "Authorization": f"Basic {base64.b64encode(auth).decode('utf-8')}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        request = Request(endpoint, data=payload, headers=headers, method="POST")
        with urlopen(request, timeout=10) as response:
            response.read()
        return True


class NullSmsNotifier:
    def send(self, message: str) -> bool:
        del message
        return False
