from unittest.mock import MagicMock, patch

from app import notify
from app.models import Lead, Tenant


def _tenant(**overrides) -> Tenant:
    t = Tenant(business_name="Joe's Plumbing", to_number="+15550001111")
    for key, value in overrides.items():
        setattr(t, key, value)
    return t


def _lead(**overrides) -> Lead:
    lead = Lead(call_id="call_123", from_number="+15559998888", call_summary="Burst pipe, needs a quote.")
    for key, value in overrides.items():
        setattr(lead, key, value)
    return lead


def test_send_email_noop_without_notify_email(monkeypatch):
    monkeypatch.setattr(notify.settings, "postmark_api_token", "pm-token")
    with patch("app.notify.httpx.post") as mock_post:
        notify.send_email(_tenant(notify_email=None), _lead())
        mock_post.assert_not_called()


def test_send_email_uses_postmark_api_when_token_set(monkeypatch):
    monkeypatch.setattr(notify.settings, "postmark_api_token", "pm-token")
    monkeypatch.setattr(notify.settings, "smtp_from", "notifications@notify.example.com")
    monkeypatch.setattr(notify.settings, "smtp_host", "should-be-ignored.example.com")

    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None

    with patch("app.notify.httpx.post", return_value=mock_response) as mock_post, patch(
        "app.notify.smtplib.SMTP"
    ) as mock_smtp:
        notify.send_email(_tenant(notify_email="joe@example.com"), _lead())

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == notify.POSTMARK_API_URL
        assert kwargs["headers"]["X-Postmark-Server-Token"] == "pm-token"
        assert kwargs["json"]["To"] == "joe@example.com"
        assert kwargs["json"]["From"] == "notifications@notify.example.com"
        mock_smtp.assert_not_called()


def test_send_email_falls_back_to_smtp_without_postmark_token(monkeypatch):
    monkeypatch.setattr(notify.settings, "postmark_api_token", "")
    monkeypatch.setattr(notify.settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(notify.settings, "smtp_from", "notifications@example.com")

    mock_server = MagicMock()
    with patch("app.notify.httpx.post") as mock_post, patch("app.notify.smtplib.SMTP") as mock_smtp:
        mock_smtp.return_value.__enter__.return_value = mock_server
        notify.send_email(_tenant(notify_email="joe@example.com"), _lead())

        mock_smtp.assert_called_once_with("smtp.example.com", notify.settings.smtp_port)
        mock_server.sendmail.assert_called_once()
        mock_post.assert_not_called()


def test_send_email_noop_when_nothing_configured(monkeypatch):
    monkeypatch.setattr(notify.settings, "postmark_api_token", "")
    monkeypatch.setattr(notify.settings, "smtp_host", "")

    with patch("app.notify.httpx.post") as mock_post, patch("app.notify.smtplib.SMTP") as mock_smtp:
        notify.send_email(_tenant(notify_email="joe@example.com"), _lead())
        mock_post.assert_not_called()
        mock_smtp.assert_not_called()
