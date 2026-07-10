from pathlib import Path

from src.config import Settings
from src.web_server import AuthManager, WebAppState, _hash_password


class FakeRobinhoodClient:
    requested_symbols: list[list[str]] = []

    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs

    def get_best_bid_ask(self, symbols: list[str]) -> dict:
        self.requested_symbols.append(symbols)
        return {
            "results": [
                {
                    "symbol": symbol,
                    "bid_price": "99",
                    "ask_price": "101",
                }
                for symbol in symbols
            ]
        }


class FakeSmsNotifier:
    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs

    def send(self, message: str) -> None:
        del message


class FakeNotifier:
    def maybe_send_heartbeat(self, now=None) -> None:
        del now

    def notify(self, event_type: str, message: str, severity, **kwargs) -> None:
        del event_type, message, severity, kwargs


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        robinhood_api_key="test-key",
        robinhood_private_key="test-private-key",
        trading_mode="paper",
        crypto_symbols="BTC-USD,ETH-USD,SOL-USD,DOGE-USD,XLM-USD,ATOM-USD",
        database_path=str(tmp_path / "research.sqlite3"),
    )


def _auth_settings(tmp_path: Path) -> Settings:
    return Settings(
        web_auth_email="operator@example.com",
        web_auth_password="old-password-123",
        web_auth_store_path=str(tmp_path / "auth_store.json"),
        smtp_host="",
        smtp_username="",
    )


def test_auth_manager_login_creates_and_expires_session(tmp_path) -> None:
    auth = AuthManager(_auth_settings(tmp_path))

    token = auth.login("operator@example.com", "old-password-123")

    assert token
    assert auth.user_for_session(token) == "operator@example.com"

    auth.logout(token)

    assert auth.user_for_session(token) is None


def test_auth_manager_rejects_wrong_password(tmp_path) -> None:
    auth = AuthManager(_auth_settings(tmp_path))

    assert auth.login("operator@example.com", "wrong-password") is None
    assert auth.login("other@example.com", "old-password-123") is None


def test_auth_manager_password_reset_updates_stored_hash(tmp_path) -> None:
    auth = AuthManager(_auth_settings(tmp_path))

    recovery = auth.start_password_reset("operator@example.com")
    assert recovery["ok"] is True
    assert recovery["sent"] is False
    token = recovery["resetToken"]

    assert auth.reset_password(token, "new-password-123") is True
    assert auth.login("operator@example.com", "old-password-123") is None
    assert auth.login("operator@example.com", "new-password-123")
    assert (tmp_path / "auth_store.json").exists()


def test_auth_manager_accepts_prehashed_password(tmp_path) -> None:
    settings = Settings(
        web_auth_email="operator@example.com",
        web_auth_password_hash=_hash_password("stored-password-123"),
        web_auth_store_path=str(tmp_path / "auth_store.json"),
    )
    auth = AuthManager(settings)

    assert auth.login("operator@example.com", "stored-password-123")


def test_web_runner_uses_selected_symbol_only(monkeypatch, tmp_path) -> None:
    import src.web_server as web_server

    FakeRobinhoodClient.requested_symbols = []
    monkeypatch.setattr(web_server, "get_settings", lambda: _settings(tmp_path))
    monkeypatch.setattr(web_server, "RobinhoodClient", FakeRobinhoodClient)
    monkeypatch.setattr(web_server, "SmsNotifier", FakeSmsNotifier)
    monkeypatch.setattr(web_server, "build_notifier", lambda database: FakeNotifier())

    state = WebAppState()
    runner = state.get_runner({"symbol": "DOGE-USD"})
    result = runner.run_once()

    assert runner.symbols == ["DOGE-USD"]
    assert result["signals"].keys() == {"DOGE-USD"}
    assert FakeRobinhoodClient.requested_symbols == [["DOGE-USD"]]

    state.reset()


def test_changing_selected_symbol_starts_single_symbol_session(monkeypatch, tmp_path) -> None:
    import src.web_server as web_server

    FakeRobinhoodClient.requested_symbols = []
    monkeypatch.setattr(web_server, "get_settings", lambda: _settings(tmp_path))
    monkeypatch.setattr(web_server, "RobinhoodClient", FakeRobinhoodClient)
    monkeypatch.setattr(web_server, "SmsNotifier", FakeSmsNotifier)
    monkeypatch.setattr(web_server, "build_notifier", lambda database: FakeNotifier())

    state = WebAppState()
    first_runner = state.get_runner({"symbol": "DOGE"})
    first_runner.run_once()

    next_runner = state.get_runner({"symbol": "XLM"})
    next_runner.run_once()

    assert first_runner.symbols == ["DOGE-USD"]
    assert next_runner.symbols == ["XLM-USD"]
    assert first_runner is not next_runner
    assert FakeRobinhoodClient.requested_symbols == [["DOGE-USD"], ["XLM-USD"]]

    state.reset()


def test_web_runner_attaches_notification_notifier(monkeypatch, tmp_path) -> None:
    import src.web_server as web_server

    fake_notifier = FakeNotifier()
    monkeypatch.setattr(web_server, "get_settings", lambda: _settings(tmp_path))
    monkeypatch.setattr(web_server, "RobinhoodClient", FakeRobinhoodClient)
    monkeypatch.setattr(web_server, "SmsNotifier", FakeSmsNotifier)
    monkeypatch.setattr(web_server, "build_notifier", lambda database: fake_notifier)

    state = WebAppState()
    runner = state.get_runner({"symbol": "DOGE-USD"})

    assert runner.notifier is fake_notifier
    assert runner.notification_trade_update_interval_seconds == 1800
    assert runner.notification_recap_interval_seconds == 3600

    state.reset()


def test_manual_control_pause_resume_and_kill_switch(monkeypatch, tmp_path) -> None:
    import src.web_server as web_server

    monkeypatch.setattr(web_server, "get_settings", lambda: _settings(tmp_path))
    monkeypatch.setattr(web_server, "RobinhoodClient", FakeRobinhoodClient)
    monkeypatch.setattr(web_server, "SmsNotifier", FakeSmsNotifier)
    monkeypatch.setattr(web_server, "build_notifier", lambda database: FakeNotifier())

    state = WebAppState()

    paused = state.execute_manual_control({"action": "pause", "reason": "operator check"})
    assert paused["ok"] is True
    assert paused["status"]["manualControls"]["paused"] is True

    killed = state.execute_manual_control({"action": "kill_on"})
    assert killed["status"]["manualControls"]["killSwitch"] is True

    resumed = state.execute_manual_control({"action": "resume"})
    assert resumed["status"]["manualControls"]["paused"] is False

    state.reset()


def test_discord_command_parser_uses_manual_controls(monkeypatch, tmp_path) -> None:
    import src.web_server as web_server

    monkeypatch.setattr(web_server, "get_settings", lambda: _settings(tmp_path))
    monkeypatch.setattr(web_server, "RobinhoodClient", FakeRobinhoodClient)
    monkeypatch.setattr(web_server, "SmsNotifier", FakeSmsNotifier)
    monkeypatch.setattr(web_server, "build_notifier", lambda database: FakeNotifier())

    state = WebAppState()

    result = state.handle_discord_command("pause checking entries", {})
    assert result["ok"] is True
    assert result["status"]["manualControls"]["paused"] is True

    status = state.handle_discord_command("status", {})
    assert status["action"] == "status"
    assert "paused" in status["summary"]

    state.reset()
