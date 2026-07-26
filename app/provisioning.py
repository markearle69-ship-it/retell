"""Automates the manual Twilio SIP trunk + Retell agent setup:

1. Render the niche's prompt template with this site's business_name/location/zip_codes.
2. Create a Retell LLM (the rendered prompt) and an Agent that uses it.
3. Attach the already-purchased Twilio number to the one shared SIP trunk.
4. Import that number into Retell via SIP, pointed at the new agent.

Each step is idempotent (skipped if already done for this tenant), so
resubmitting the provisioning form after a partial failure only retries what
didn't complete.
"""

import re
from datetime import datetime
from typing import TYPE_CHECKING

import httpx
from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client as TwilioClient

from .config import settings

if TYPE_CHECKING:
    from .models import NicheTemplate, Tenant

RETELL_API_BASE = "https://api.retellai.com"

_VAR_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


class ProvisioningError(Exception):
    """Raised when a step fails; the message is shown directly in the admin panel."""


def render_template(text: str, variables: dict) -> str:
    def _sub(match: "re.Match[str]") -> str:
        return str(variables.get(match.group(1), match.group(0)))

    return _VAR_RE.sub(_sub, text)


def _template_variables(tenant: "Tenant") -> dict:
    return {
        "business_name": tenant.business_name,
        "location": tenant.location or "",
        "zip_codes": tenant.zip_codes or "",
    }


def _retell_headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.retell_api_key}",
        "Content-Type": "application/json",
    }


def create_retell_agent(niche: "NicheTemplate", tenant: "Tenant") -> tuple[str, str]:
    """Create a Retell LLM (rendered prompt) + Agent using it. Returns (llm_id, agent_id)."""
    variables = _template_variables(tenant)
    llm_payload = {
        "general_prompt": render_template(niche.prompt_template, variables),
        "model": niche.model or "gpt-4.1",
        "start_speaker": "agent",
    }
    if niche.begin_message_template:
        llm_payload["begin_message"] = render_template(niche.begin_message_template, variables)

    with httpx.Client(timeout=30) as client:
        try:
            resp = client.post(
                f"{RETELL_API_BASE}/create-retell-llm", headers=_retell_headers(), json=llm_payload
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProvisioningError(
                f"Retell LLM creation failed: {exc.response.status_code} {exc.response.text}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProvisioningError(f"Retell LLM creation failed: {exc}") from exc
        llm_id = resp.json()["llm_id"]

        agent_payload = {
            "agent_name": tenant.business_name,
            "voice_id": niche.voice_id,
            "response_engine": {"type": "retell-llm", "llm_id": llm_id},
        }
        try:
            resp = client.post(
                f"{RETELL_API_BASE}/create-agent", headers=_retell_headers(), json=agent_payload
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProvisioningError(
                f"Retell agent creation failed (LLM {llm_id} was created and is now orphaned - "
                f"safe to delete in Retell's dashboard): {exc.response.status_code} {exc.response.text}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProvisioningError(
                f"Retell agent creation failed (LLM {llm_id} was created and is now orphaned - "
                f"safe to delete in Retell's dashboard): {exc}"
            ) from exc
        agent_id = resp.json()["agent_id"]

    return llm_id, agent_id


def _twilio_client() -> TwilioClient:
    return TwilioClient(settings.twilio_account_sid, settings.twilio_auth_token)


def attach_number_to_trunk(phone_number: str) -> str:
    """Attach an already-purchased Twilio number to the shared trunk. Returns its PhoneNumberSid."""
    if not settings.twilio_trunk_sid:
        raise ProvisioningError("TWILIO_TRUNK_SID is not configured")

    client = _twilio_client()
    try:
        matches = client.incoming_phone_numbers.list(phone_number=phone_number, limit=1)
    except TwilioRestException as exc:
        raise ProvisioningError(f"Failed to look up Twilio number {phone_number}: {exc}") from exc

    if not matches:
        raise ProvisioningError(
            f"Twilio number {phone_number} was not found in your account — "
            "buy it in Twilio first, then retry."
        )
    number_sid = matches[0].sid

    try:
        client.trunking.v1.trunks(settings.twilio_trunk_sid).phone_numbers.create(
            phone_number_sid=number_sid
        )
    except TwilioRestException as exc:
        if exc.status != 409 and "already" not in str(exc).lower():
            raise ProvisioningError(f"Failed to attach {phone_number} to the SIP trunk: {exc}") from exc

    return number_sid


def get_trunk_termination_uri() -> str:
    if not settings.twilio_trunk_sid:
        raise ProvisioningError("TWILIO_TRUNK_SID is not configured")

    client = _twilio_client()
    try:
        trunk = client.trunking.v1.trunks(settings.twilio_trunk_sid).fetch()
    except TwilioRestException as exc:
        raise ProvisioningError(f"Failed to fetch trunk {settings.twilio_trunk_sid}: {exc}") from exc

    if not trunk.domain_name:
        raise ProvisioningError("Trunk has no termination URI (domain_name) configured")
    return trunk.domain_name


def import_number_to_retell(phone_number: str, agent_id: str) -> None:
    termination_uri = get_trunk_termination_uri()
    payload = {
        "phone_number": phone_number,
        "termination_uri": termination_uri,
        "sip_trunk_auth_username": settings.twilio_sip_username,
        "sip_trunk_auth_password": settings.twilio_sip_password,
        "inbound_agents": [{"agent_id": agent_id, "weight": 1.0}],
        "allowed_inbound_country_list": [
            c.strip() for c in settings.retell_allowed_inbound_countries.split(",") if c.strip()
        ],
        "nickname": phone_number,
    }
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                f"{RETELL_API_BASE}/import-phone-number", headers=_retell_headers(), json=payload
            )
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if "already exists" in exc.response.text.lower():
            raise ProvisioningError(
                f"Retell already has a phone number import for {phone_number} from before this "
                "site used auto-provisioning (likely set up manually). Auto-provisioning can't "
                "take over an existing import — either leave this site's niche unset (manual), "
                "or delete its existing phone number entry in Retell's dashboard first, then retry."
            ) from exc
        raise ProvisioningError(
            f"Retell phone number import failed: {exc.response.status_code} {exc.response.text}"
        ) from exc
    except httpx.HTTPError as exc:
        raise ProvisioningError(f"Retell phone number import failed: {exc}") from exc


def provision_site(db, tenant: "Tenant", niche: "NicheTemplate") -> None:
    """Mutates and commits `tenant` step by step. Whatever succeeds is kept even if
    a later step raises, so a resubmit only retries what's left."""
    if not tenant.retell_llm_id or not tenant.retell_agent_id:
        llm_id, agent_id = create_retell_agent(niche, tenant)
        tenant.retell_llm_id = llm_id
        tenant.retell_agent_id = agent_id
        db.commit()

    if not tenant.twilio_number_sid:
        tenant.twilio_number_sid = attach_number_to_trunk(tenant.to_number)
        db.commit()

    if not tenant.provisioned_at:
        import_number_to_retell(tenant.to_number, tenant.retell_agent_id)
        tenant.provisioned_at = datetime.utcnow()
        tenant.provisioning_error = None
        db.commit()
