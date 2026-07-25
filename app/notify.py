import logging
import smtplib
from email.mime.text import MIMEText

from .config import settings
from .models import Lead, Tenant

logger = logging.getLogger(__name__)


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


def send_email(tenant: Tenant, lead: Lead) -> None:
    if not tenant.notify_email or not settings.smtp_host:
        return

    msg = MIMEText(_format_message(tenant, lead))
    msg["Subject"] = f"New lead call from {lead.from_number or 'unknown number'}"
    msg["From"] = settings.smtp_from or settings.smtp_user
    msg["To"] = tenant.notify_email

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.starttls()
            if settings.smtp_user:
                server.login(settings.smtp_user, settings.smtp_password)
            server.sendmail(msg["From"], [tenant.notify_email], msg.as_string())
    except Exception:
        logger.exception("Failed to send lead email to %s", tenant.notify_email)


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
