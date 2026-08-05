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
    from sqlalchemy.orm import Session

    from .models import NicheTemplate, Tenant

RETELL_API_BASE = "https://api.retellai.com"

_VAR_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")

# Without this, a prompt instructing the agent to "invoke end_call" (e.g. for
# hanging up on robocalls/spam) has no actual tool to call - it's not
# available by default and has to be registered explicitly. Shared between
# create_retell_agent (new sites) and sync_existing_agent (patching sites
# that predate this) so both stay identical.
END_CALL_TOOL = {
    "type": "end_call",
    "name": "end_call",
    "description": (
        "End the call immediately. Use this both at the natural close of a normal "
        "call (after saying goodbye), and the instant you detect the caller is a "
        "robocall, scam, or automated spam system rather than a real customer - in "
        "the spam case, call this tool right away with no spoken reply at all."
    ),
    "speak_during_execution": False,
}


class ProvisioningError(Exception):
    """Raised when a step fails; the message is shown directly in the admin panel."""


def render_template(text: str, variables: dict) -> str:
    def _sub(match: "re.Match[str]") -> str:
        return str(variables.get(match.group(1), match.group(0)))

    return _VAR_RE.sub(_sub, text)


def _template_variables(tenant: "Tenant", niche: "NicheTemplate") -> dict:
    return {
        # Per-site.
        "business_name": tenant.business_name,
        "location": tenant.location or "",
        "zip_codes": tenant.zip_codes or "",
        # Per-niche (same for every site using this niche).
        "service_description": niche.service_description or "",
        "problem_domain": niche.problem_domain or "",
        "collect_list": niche.collect_list or "",
    }


def _retell_headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.retell_api_key}",
        "Content-Type": "application/json",
    }


def webhook_url() -> str | None:
    if not settings.public_base_url:
        return None
    return settings.public_base_url.rstrip("/") + "/webhooks/retell"


def get_global_prompt_config(db: "Session"):
    """The single row of rules prepended to every niche's prompt (e.g. "never
    claim to be human") - maintained once here instead of copy-pasted into
    every niche template. Created with the default text on first access."""
    from .models import DEFAULT_GLOBAL_PROMPT_RULES, GlobalPromptConfig

    config = db.query(GlobalPromptConfig).first()
    if config is None:
        config = GlobalPromptConfig(shared_rules=DEFAULT_GLOBAL_PROMPT_RULES)
        db.add(config)
        db.commit()
        db.refresh(config)
    return config


def list_retell_voice_ids() -> set[str]:
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(f"{RETELL_API_BASE}/list-voices", headers=_retell_headers())
            resp.raise_for_status()
            return {v["voice_id"] for v in resp.json()}
    except httpx.HTTPError as exc:
        raise ProvisioningError(f"Failed to fetch Retell's voice list: {exc}") from exc


def create_retell_agent(niche: "NicheTemplate", tenant: "Tenant", shared_rules: str = "") -> tuple[str, str]:
    """Create a Retell LLM (rendered prompt) + Agent using it. Returns (llm_id, agent_id)."""
    # Checked up front, before creating anything: a bad voice_id (wrong case,
    # typo, or copy-pasted from the wrong place) has repeatedly only surfaced
    # as a generic 404 partway through, after an LLM was already created and
    # orphaned. voice_id lookups on Retell's side are case-sensitive even
    # though that's not documented, so an exact-match check here catches it
    # immediately and for free, with a message that says exactly what's wrong.
    available_voices = list_retell_voice_ids()
    if niche.voice_id not in available_voices:
        raise ProvisioningError(
            f"Voice ID '{niche.voice_id}' (niche '{niche.name}') was not found in your Retell "
            "account's voice list - check /admin/niches for a typo or case mismatch (voice IDs "
            "are case-sensitive). No LLM or agent was created for this attempt."
        )

    variables = _template_variables(tenant, niche)
    rendered_niche_prompt = render_template(niche.prompt_template, variables)
    general_prompt = (
        f"{shared_rules.strip()}\n\n{rendered_niche_prompt}" if shared_rules.strip() else rendered_niche_prompt
    )
    llm_payload = {
        "general_prompt": general_prompt,
        "model": niche.model or "gpt-4.1",
        "start_speaker": "agent",
        "general_tools": [END_CALL_TOOL],
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
        hook_url = webhook_url()
        if hook_url:
            # Set explicitly rather than relying on the account-level webhook
            # inheriting to API-created agents - that inheritance not reliably
            # applying was the likely cause of leads silently going missing.
            agent_payload["webhook_url"] = hook_url
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


def sync_existing_agent(tenant: "Tenant", shared_rules: str) -> str:
    """Patch an already-provisioned site's existing LLM in place - add the
    end_call tool and the current global rules if either is missing - then
    publish the agent so the change actually goes live. No new agent/LLM is
    created and no phone routing is touched, unlike Force re-provision.

    Returns a short human-readable status: "updated", "already up to date",
    or "skipped (not auto-provisioned)".
    """
    if not tenant.retell_llm_id or not tenant.retell_agent_id:
        return "skipped (not auto-provisioned)"

    with httpx.Client(timeout=30) as client:
        try:
            resp = client.get(
                f"{RETELL_API_BASE}/get-retell-llm/{tenant.retell_llm_id}", headers=_retell_headers()
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProvisioningError(
                f"Failed to fetch LLM {tenant.retell_llm_id}: {exc.response.status_code} {exc.response.text}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProvisioningError(f"Failed to fetch LLM {tenant.retell_llm_id}: {exc}") from exc
        current = resp.json()

        current_prompt = current.get("general_prompt") or ""
        current_tools = current.get("general_tools") or []

        needs_rules = bool(shared_rules.strip()) and not current_prompt.strip().startswith(shared_rules.strip())
        needs_end_call = not any(t.get("type") == "end_call" for t in current_tools)

        if not needs_rules and not needs_end_call:
            return "already up to date"

        new_prompt = f"{shared_rules.strip()}\n\n{current_prompt}" if needs_rules else current_prompt
        new_tools = current_tools + [END_CALL_TOOL] if needs_end_call else current_tools

        llm_update_payload = {"general_prompt": new_prompt, "general_tools": new_tools}
        try:
            resp = client.patch(
                f"{RETELL_API_BASE}/update-retell-llm/{tenant.retell_llm_id}",
                headers=_retell_headers(),
                json=llm_update_payload,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if "cannot update published" not in exc.response.text.lower():
                raise ProvisioningError(
                    f"Failed to update LLM {tenant.retell_llm_id}: {exc.response.status_code} {exc.response.text}"
                ) from exc
            # The LLM's latest version is fully published - only a draft can be
            # edited. Per Retell's docs, publishing again creates a fresh draft
            # as a side effect even when there's nothing new to publish, so
            # retry the patch once after that.
            try:
                pub_resp = client.post(
                    f"{RETELL_API_BASE}/publish-agent/{tenant.retell_agent_id}", headers=_retell_headers()
                )
                pub_resp.raise_for_status()
            except httpx.HTTPError:
                pass  # best effort - if this didn't help, the retry below reports a clear error anyway
            try:
                resp = client.patch(
                    f"{RETELL_API_BASE}/update-retell-llm/{tenant.retell_llm_id}",
                    headers=_retell_headers(),
                    json=llm_update_payload,
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc2:
                raise ProvisioningError(
                    f"Failed to update LLM {tenant.retell_llm_id} even after creating a fresh draft: "
                    f"{exc2.response.status_code} {exc2.response.text}"
                ) from exc2
            except httpx.HTTPError as exc2:
                raise ProvisioningError(
                    f"Failed to update LLM {tenant.retell_llm_id} even after creating a fresh draft: {exc2}"
                ) from exc2
        except httpx.HTTPError as exc:
            raise ProvisioningError(f"Failed to update LLM {tenant.retell_llm_id}: {exc}") from exc

        try:
            resp = client.post(
                f"{RETELL_API_BASE}/publish-agent/{tenant.retell_agent_id}", headers=_retell_headers()
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # Updating the LLM directly (as opposed to editing in Retell's
            # dashboard UI, which creates an explicit draft) takes effect
            # immediately with nothing left to publish - "already published"
            # means the change is already live, not that anything failed.
            if "already published" in exc.response.text.lower():
                pass
            else:
                raise ProvisioningError(
                    f"LLM updated but publishing agent {tenant.retell_agent_id} failed - the change "
                    f"was saved as a draft, not live yet: {exc.response.status_code} {exc.response.text}"
                ) from exc
        except httpx.HTTPError as exc:
            raise ProvisioningError(
                f"LLM updated but publishing agent {tenant.retell_agent_id} failed - the change "
                f"was saved as a draft, not live yet: {exc}"
            ) from exc

    return "updated"


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
        shared_rules = get_global_prompt_config(db).shared_rules
        llm_id, agent_id = create_retell_agent(niche, tenant, shared_rules)
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
