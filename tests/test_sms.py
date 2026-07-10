from unittest.mock import Mock, patch

from src.notifications.sms import SmsConfig, SmsNotifier


def test_sms_notifier_skips_when_disabled() -> None:
    notifier = SmsNotifier(SmsConfig(enabled=False))

    assert notifier.send("hello") is False


def test_sms_notifier_posts_to_twilio_when_configured() -> None:
    response = Mock()
    response.read.return_value = b"{}"
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=None)
    notifier = SmsNotifier(
        SmsConfig(
            enabled=True,
            account_sid="sid",
            auth_token="token",
            from_number="+15551234567",
            to_number="+15557654321",
        )
    )

    with patch("src.notifications.sms.urlopen", return_value=response) as mocked_urlopen:
        sent = notifier.send("summary")

    request = mocked_urlopen.call_args.args[0]
    assert sent is True
    assert request.full_url.endswith("/Accounts/sid/Messages.json")
