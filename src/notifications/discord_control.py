from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class DiscordControlConfig:
    enabled: bool = False
    bot_token: str = ""
    channel_id: str = ""
    poll_seconds: int = 5
    prefix: str = "!trader"


class DiscordControlBot:
    """Small Discord channel poller for paper-trading manual controls."""

    api_base = "https://discord.com/api/v10"

    def __init__(
        self,
        config: DiscordControlConfig,
        command_handler: Callable[[str, Dict[str, Any]], Dict[str, Any]],
        opener: Callable[..., object] = urlopen,
    ) -> None:
        self.config = config
        self.command_handler = command_handler
        self.opener = opener
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._last_message_id = ""
        self.last_error = ""

    @property
    def is_configured(self) -> bool:
        return bool(
            self.config.enabled
            and self.config.bot_token
            and self.config.channel_id
            and self.config.prefix
        )

    def start(self) -> None:
        if not self.is_configured or self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="discord-control", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": self.config.enabled,
            "configured": self.is_configured,
            "running": bool(self._thread and self._thread.is_alive()),
            "channelId": self.config.channel_id,
            "prefix": self.config.prefix,
            "lastError": self.last_error,
        }

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
                self.last_error = ""
            except Exception as exc:  # pragma: no cover
                self.last_error = str(exc)
            self._stop.wait(max(1, int(self.config.poll_seconds)))

    def poll_once(self) -> None:
        messages = self._fetch_messages()
        if not self._last_message_id and messages:
            self._last_message_id = max(
                (str(message.get("id", "")) for message in messages),
                key=lambda value: int(value or 0),
            )
            return
        for message in reversed(messages):
            message_id = str(message.get("id", ""))
            if not message_id or (self._last_message_id and int(message_id) <= int(self._last_message_id)):
                continue
            self._last_message_id = message_id
            content = str(message.get("content", "")).strip()
            if not content.startswith(self.config.prefix):
                continue
            response = self._handle_content(content, message)
            self._send_channel_message(response)

    def _handle_content(self, content: str, message: Dict[str, Any]) -> str:
        command_text = content[len(self.config.prefix) :].strip()
        if not command_text or command_text.lower() in {"help", "commands"}:
            return self._help_text()
        result = self.command_handler(command_text, message)
        return self._format_result(result)

    def _fetch_messages(self) -> list[Dict[str, Any]]:
        params = urlencode({"limit": 10})
        request = Request(
            f"{self.api_base}/channels/{self.config.channel_id}/messages?{params}",
            headers=self._headers(),
            method="GET",
        )
        with self.opener(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    def _send_channel_message(self, content: str) -> None:
        body = json.dumps({"content": content[:1900]}).encode("utf-8")
        request = Request(
            f"{self.api_base}/channels/{self.config.channel_id}/messages",
            data=body,
            headers={**self._headers(), "Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with self.opener(request, timeout=10) as response:
            response.read()

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bot {self.config.bot_token}",
            "Accept": "application/json",
            "User-Agent": "RobinhoodPaperTrader/0.1 (+https://discord.com)",
        }

    def _help_text(self) -> str:
        return (
            "Paper trader commands: `status`, `analytics`, `pause [reason]`, `resume`, "
            "`kill on`, `kill off`, `exit <SYMBOL|all> [reason]`."
        )

    def _format_result(self, result: Dict[str, Any]) -> str:
        if result.get("error"):
            return f"Manual control rejected: {result['error']}"
        action = result.get("action", "status")
        if action == "status":
            return result.get("summary", "Paper trader status unavailable.")
        if action == "analytics":
            return result.get("summary", "Paper trader analytics unavailable.")
        if action == "force_exit":
            return (
                f"Force exit processed: {len(result.get('trades', []))} paper sells, "
                f"{len(result.get('errors', []))} errors."
            )
        return f"Manual control `{action}` applied."
