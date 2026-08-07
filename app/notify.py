import logging
import smtplib
from email.mime.text import MIMEText

import httpx

from .config import settings
from .models import Lead, Tenant

logger = logging.getLogger(__name__)

POSTMARK_API_URL = "https://api.postmarkapp.com/email"


def _format_message(tenant: Tenant, lead: Lead) -> str:
    lines = [
        f"New call for {tenant.business_name}",
        f"From: {lead.from_number or 'unknown number'}",
    ]
    if lead.user_sentiment:
        lines.append(f"Sentiment: {lead.user_sentiment}")
    if lead.call_successful is not None:
        lines.append(f"Outcome: {'Resolved by agent' if lead.call_successful else 'Needs follow-up'}")
    if lead.call_summary:
        lines.append(f"\nSummary:\n{lead.call_summary}")
    if lead.recording_url:
        lines.append(f"\nRecording: {lead.recording_url}")
    return "\n".join(lines)


def _send_via_postmark_api(to_email: str, subject: str, text_body: str) -> None:
    payload = {
        "From": settings.smtp_from,
        "To": to_email,
        "Subject": subject,
        "TextBody": text_body,
        "MessageStream": "outbound",
    }
    try:
        resp = httpx.post(
            POSTMARK_API_URL,
            headers={
                "X-Postmark-Server-Token": settings.postmark_api_token,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Failed to send email via Postmark API to %s: %s %s",
            to_email,
            exc.response.status_code,
            exc.response.text,
        )
    except httpx.HTTPError:
        logger.exception("Failed to send email via Postmark API to %s", to_email)


def _send_via_smtp(to_email: str, subject: str, text_body: str) -> None:
    msg = MIMEText(text_body)
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from or settings.smtp_user
    msg["To"] = to_email

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.starttls()
            if settings.smtp_user:
                server.login(settings.smtp_user, settings.smtp_password)
            server.sendmail(msg["From"], [to_email], msg.as_string())
    except Exception:
        logger.exception("Failed to send email via SMTP to %s", to_email)


def _send_generic_email(to_email: str, subject: str, text_body: str) -> None:
    if settings.postmark_api_token:
        _send_via_postmark_api(to_email, subject, text_body)
    elif settings.smtp_host:
        _send_via_smtp(to_email, subject, text_body)


def send_email(tenant: Tenant, lead: Lead) -> None:
    if not tenant.notify_email:
        return
    subject = f"New lead call from {lead.from_number or 'unknown number'}"
    _send_generic_email(tenant.notify_email, subject, _format_message(tenant, lead))


def send_rank_alert(to_email: str, subject: str, text_body: str) -> None:
    """Same email paths as send_email, but for a ranking alert rather than a
    lead - recipient/subject/body are given directly since there's no Lead
    to format from."""
    if not to_email:
        return
    _send_generic_email(to_email, subject, text_body)


def send_sms(tenant: Tenant, lead: Lead) -> None:
    if not tenant.notify_sms_number or not settings.twilio_account_sid:
        return

    try:
        from twilio.rest import Client

        client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
        summary = (lead.call_summary or "New call - summary not ready yet")[:300]
        client.messages.create(
            to=tenant.notify_sms_number,
            from_=settings.twilio_from_number,
            body=f"New lead from {lead.from_number or 'unknown'}: {summary}",
        )
    except Exception:
        logger.exception("Failed to send lead SMS to %s", tenant.notify_sms_number)


def notify_tenant(tenant: Tenant, lead: Lead) -> None:
    send_email(tenant, lead)
    send_sms(tenant, lead)
