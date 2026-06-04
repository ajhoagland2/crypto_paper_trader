import base64
import time
from dataclasses import dataclass
from typing import Dict, Optional


class AuthenticationError(RuntimeError):
    """Raised when signed API authentication cannot be prepared."""


@dataclass(frozen=True)
class RobinhoodCredentials:
    api_key: str
    private_key: str

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.private_key)


class RequestSigner:
    """Build Robinhood Crypto API auth headers.

    Official docs require x-api-key, x-signature, and x-timestamp. Signature
    creation uses an Ed25519 private key; PyNaCl is optional until real API
    access is configured.
    """

    def __init__(self, credentials: RobinhoodCredentials) -> None:
        self.credentials = credentials

    def headers(self, method: str, path: str, body: Optional[str] = None) -> Dict[str, str]:
        if not self.credentials.is_configured:
            raise AuthenticationError("Robinhood API credentials are not configured.")

        timestamp = str(int(time.time()))
        message = self._message(method, path, timestamp, body)
        signature = self._sign(message)
        return {
            "x-api-key": self.credentials.api_key,
            "x-signature": signature,
            "x-timestamp": timestamp,
            "Content-Type": "application/json; charset=utf-8",
        }

    def _message(
        self, method: str, path: str, timestamp: str, body: Optional[str] = None
    ) -> str:
        body_text = body or ""
        return f"{self.credentials.api_key}{timestamp}{path}{method.upper()}{body_text}"

    def _sign(self, message: str) -> str:
        try:
            import nacl.signing  # type: ignore
        except ImportError as exc:
            raise AuthenticationError(
                "Install PyNaCl to sign real Robinhood API requests: "
                "pip install .[crypto-signing]"
            ) from exc

        private_key_seed = base64.b64decode(self.credentials.private_key)
        signing_key = nacl.signing.SigningKey(private_key_seed)
        signed = signing_key.sign(message.encode("utf-8"))
        return base64.b64encode(signed.signature).decode("utf-8")
