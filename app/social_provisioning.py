"""Automates setting up and running each site's socials, the same
idempotent/resumable way provisioning.py automates the Twilio + Retell setup:

1. Create the site's own Ayrshare sub-profile (Business Plan "profiles"
   feature) so every site's socials are managed under one Ayrshare account
   without cross-posting to each other. NOTE: Ayrshare still requires a
   one-time manual step per site - actually linking that profile's real
   Facebook/Instagram/Google Business Profile accounts via OAuth in
   Ayrshare's own dashboard. There is no API workaround for that OAuth step;
   this pipeline automates everything before and after it.
2. Generate a batch of on-brand, niche+location-varied captions (Claude) so
   sites sharing a niche don't read as duplicated content.
3. Generate a branded creative per caption (Bannerbear), if configured -
   posts go out text-only if it isn't.
4. Schedule each post on the site's Ayrshare profile.

Each step is idempotent (skipped/topped-up rather than redone), so
resubmitting "Provision social" after a partial failure - e.g. Bannerbear
rendered fine but Ayrshare scheduling failed for one post - only retries
what didn't complete, exactly like provision_site().
"""

import json
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import httpx

from . import models
from .config import settings
from .provisioning import ProvisioningError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from .models import NicheTemplate, SocialPost, Tenant

ANTHROPIC_API_BASE = "https://api.anthropic.com/v1/messages"

# Bannerbear renders asynchronously - poll its image endpoint until the
# render finishes rather than assuming image_url is populated immediately.
_BANNERBEAR_POLL_ATTEMPTS = 8
_BANNERBEAR_POLL_DELAY_SECONDS = 1.5

_DEFAULT_CONTENT_PILLARS = [
    "a quick maintenance tip",
    "a common warning sign customers ignore",
    "why choose a local, licensed pro over a DIY fix",
    "a recent job / customer story (kept generic, no real customer details)",
]


def _anthropic_headers() -> dict:
    return {
        "x-api-key": settings.anthropic_api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }


def generate_caption_batch(niche: "NicheTemplate", tenant: "Tenant", count: int) -> list[dict]:
    """Returns up to `count` dicts of {"pillar": ..., "caption": ...}."""
    if not settings.anthropic_api_key:
        raise ProvisioningError("ANTHROPIC_API_KEY is not configured")
    if count <= 0:
        return []

    pillars = [p.strip() for p in (niche.social_content_pillars or "").splitlines() if p.strip()]
    if not pillars:
        pillars = _DEFAULT_CONTENT_PILLARS
    pillar_cycle = [pillars[i % len(pillars)] for i in range(count)]

    system = (
        "You write short, platform-ready social media captions for local service "
        "businesses that run near-identical marketing sites across many towns. Always "
        "vary the wording, opening hook, and specific local detail across captions so "
        "sibling sites never read as duplicated content - this matters for their SEO. "
        "Reply with ONLY a JSON array, no prose, no markdown code fences - one object "
        'per caption: {"pillar": <the angle used, echoed back>, "caption": <the '
        "caption text, under 280 characters, natural call to action, no hashtag "
        "spam>}."
    )
    user = (
        f"Business: {tenant.business_name}\n"
        f"Location: {getattr(tenant, 'location', None) or 'unspecified'}\n"
        f"Niche/service: {niche.service_description or niche.name}\n"
        f"Voice/style: {niche.social_caption_style or 'friendly, plain-spoken, trustworthy local tradesperson'}\n"
        f"Write exactly {count} captions, one for each of these angles in order: "
        + "; ".join(pillar_cycle)
    )

    payload = {
        "model": settings.anthropic_model,
        "max_tokens": 1536,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    try:
        with httpx.Client(timeout=60) as client:
            resp = client.post(ANTHROPIC_API_BASE, headers=_anthropic_headers(), json=payload)
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise ProvisioningError(
            f"Caption generation failed: {exc.response.status_code} {exc.response.text}"
        ) from exc
    except httpx.HTTPError as exc:
        raise ProvisioningError(f"Caption generation failed: {exc}") from exc

    data = resp.json()
    text = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
    try:
        captions = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ProvisioningError(
            f"Caption generation returned output that wasn't valid JSON: {text[:300]!r}"
        ) from exc
    if not isinstance(captions, list) or not captions:
        raise ProvisioningError(f"Caption generation returned no usable captions: {text[:300]!r}")

    return captions[:count]


def _bannerbear_headers() -> dict:
    return {"Authorization": f"Bearer {settings.bannerbear_api_key}", "Content-Type": "application/json"}


def generate_creative(tenant: "Tenant", niche: "NicheTemplate", caption_text: str) -> "str | None":
    """Renders a branded image for this caption via Bannerbear. Returns None
    (post goes out text-only) if Bannerbear isn't configured at all - that's
    a deliberate opt-in, not an error, since plenty of setups start
    text-only. The template must define text layers named "caption" and
    "business_name", and optionally an image layer named "logo"."""
    if not settings.bannerbear_api_key:
        return None
    template_uid = (niche.social_template_uid or settings.bannerbear_template_uid or "").strip()
    if not template_uid:
        return None

    modifications = [{"name": "caption", "text": caption_text[:200]}]
    if tenant.business_name:
        modifications.append({"name": "business_name", "text": tenant.business_name})
    if getattr(tenant, "logo_url", None):
        modifications.append({"name": "logo", "image_url": tenant.logo_url})

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                f"{settings.bannerbear_api_base}/images",
                headers=_bannerbear_headers(),
                json={"template": template_uid, "modifications": modifications},
            )
            resp.raise_for_status()
            created = resp.json()
            uid = created.get("uid")
            status = created.get("status")
            image_url = created.get("image_url")

            for _ in range(_BANNERBEAR_POLL_ATTEMPTS):
                if status == "completed" and image_url:
                    return image_url
                if status == "failed":
                    raise ProvisioningError(f"Bannerbear image {uid} failed to render")
                time.sleep(_BANNERBEAR_POLL_DELAY_SECONDS)
                resp = client.get(f"{settings.bannerbear_api_base}/images/{uid}", headers=_bannerbear_headers())
                resp.raise_for_status()
                created = resp.json()
                status = created.get("status")
                image_url = created.get("image_url")
    except httpx.HTTPStatusError as exc:
        raise ProvisioningError(
            f"Creative generation failed: {exc.response.status_code} {exc.response.text}"
        ) from exc
    except httpx.HTTPError as exc:
        raise ProvisioningError(f"Creative generation failed: {exc}") from exc

    raise ProvisioningError(f"Bannerbear image {uid} did not finish rendering in time (still '{status}')")


def _ayrshare_headers(profile_key: "str | None" = None) -> dict:
    headers = {"Authorization": f"Bearer {settings.ayrshare_api_key}", "Content-Type": "application/json"}
    if profile_key:
        headers["Profile-Key"] = profile_key
    return headers


def create_ayrshare_profile(tenant: "Tenant") -> "tuple[str, str | None]":
    """Creates this site's Ayrshare sub-profile. Returns (profile_key, ref_id).
    Linking the profile's actual social accounts is a separate, manual,
    one-time OAuth step per site in Ayrshare's dashboard - see module
    docstring."""
    if not settings.ayrshare_api_key:
        raise ProvisioningError("AYRSHARE_API_KEY is not configured")

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                f"{settings.ayrshare_api_base}/profiles",
                headers=_ayrshare_headers(),
                json={"title": tenant.business_name},
            )
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise ProvisioningError(
            f"Ayrshare profile creation failed: {exc.response.status_code} {exc.response.text}"
        ) from exc
    except httpx.HTTPError as exc:
        raise ProvisioningError(f"Ayrshare profile creation failed: {exc}") from exc

    data = resp.json()
    profile_key = data.get("profileKey")
    if not profile_key:
        raise ProvisioningError(f"Ayrshare profile creation didn't return a profileKey: {data}")
    return profile_key, data.get("refId")


def schedule_post_on_ayrshare(tenant: "Tenant", post: "SocialPost") -> str:
    if not tenant.ayrshare_profile_key:
        raise ProvisioningError("Site has no Ayrshare profile yet")

    payload = {
        "post": post.caption,
        "platforms": [p.strip() for p in post.platforms.split(",") if p.strip()],
        "scheduleDate": post.scheduled_for.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if post.image_url:
        payload["mediaUrls"] = [post.image_url]

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                f"{settings.ayrshare_api_base}/post",
                headers=_ayrshare_headers(tenant.ayrshare_profile_key),
                json=payload,
            )
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise ProvisioningError(
            f"Scheduling post on Ayrshare failed: {exc.response.status_code} {exc.response.text}"
        ) from exc
    except httpx.HTTPError as exc:
        raise ProvisioningError(f"Scheduling post on Ayrshare failed: {exc}") from exc

    data = resp.json()
    post_id = data.get("id")
    if not post_id:
        raise ProvisioningError(f"Ayrshare accepted the post but returned no id: {data}")
    return post_id


def _platforms_for(niche: "NicheTemplate") -> str:
    return (niche.social_platforms or settings.social_default_platforms or "").strip()


def _next_scheduled_slot(db: "Session", tenant: "Tenant") -> datetime:
    latest = (
        db.query(models.SocialPost)
        .filter(models.SocialPost.tenant_id == tenant.id)
        .order_by(models.SocialPost.scheduled_for.desc())
        .first()
    )
    base = latest.scheduled_for if latest else datetime.utcnow()
    return base + timedelta(hours=settings.social_post_interval_hours)


def ensure_content_queue(db: "Session", tenant: "Tenant", niche: "NicheTemplate") -> None:
    """Tops the tenant's queued (draft/scheduled) post count up to
    settings.social_posts_per_batch by generating new captions. No-op if the
    queue is already full."""
    pending = (
        db.query(models.SocialPost)
        .filter(models.SocialPost.tenant_id == tenant.id, models.SocialPost.status.in_(["draft", "scheduled"]))
        .count()
    )
    shortfall = settings.social_posts_per_batch - pending
    if shortfall <= 0:
        return

    platforms = _platforms_for(niche)
    if not platforms:
        raise ProvisioningError(
            "No social platforms configured - set 'Platforms' on the niche or "
            "SOCIAL_DEFAULT_PLATFORMS in settings."
        )

    captions = generate_caption_batch(niche, tenant, shortfall)
    scheduled_for = _next_scheduled_slot(db, tenant)
    for item in captions:
        caption_text = (item.get("caption") or "").strip()
        if not caption_text:
            continue
        db.add(
            models.SocialPost(
                tenant_id=tenant.id,
                niche_template_id=niche.id,
                content_pillar=item.get("pillar"),
                caption=caption_text,
                platforms=platforms,
                scheduled_for=scheduled_for,
                status="draft",
            )
        )
        db.commit()
        scheduled_for = scheduled_for + timedelta(hours=settings.social_post_interval_hours)


def process_post_queue(db: "Session", tenant: "Tenant", niche: "NicheTemplate") -> list[str]:
    """Generates a creative for (if configured) and schedules every draft
    post for this tenant. Returns per-post error strings (empty if every
    post succeeded) - a post that fails is marked "failed" with its error
    saved, and left for the next run to retry; it doesn't block its
    siblings."""
    drafts = (
        db.query(models.SocialPost)
        .filter(models.SocialPost.tenant_id == tenant.id, models.SocialPost.status == "draft")
        .order_by(models.SocialPost.scheduled_for)
        .all()
    )

    errors = []
    for post in drafts:
        try:
            if post.image_url is None:
                post.image_url = generate_creative(tenant, niche, post.caption)
                db.commit()

            post.ayrshare_post_id = schedule_post_on_ayrshare(tenant, post)
            post.status = "scheduled"
            post.error = None
            db.commit()
        except ProvisioningError as exc:
            post.status = "failed"
            post.error = str(exc)
            db.commit()
            errors.append(f"{post.caption[:40]!r}: {exc}")

    return errors


def provision_social(db: "Session", tenant: "Tenant", niche: "NicheTemplate") -> None:
    """Idempotent and resumable like provision_site(): create the tenant's
    Ayrshare profile if missing, top up its content queue, then generate
    creatives + schedule whatever's still in draft. Raises ProvisioningError
    (with per-post detail, capped) if anything failed - whatever succeeded is
    still committed, so resubmitting only retries what's left."""
    if not tenant.ayrshare_profile_key:
        profile_key, ref_id = create_ayrshare_profile(tenant)
        tenant.ayrshare_profile_key = profile_key
        tenant.ayrshare_ref_id = ref_id
        db.commit()

    ensure_content_queue(db, tenant, niche)
    errors = process_post_queue(db, tenant, niche)

    tenant.social_provisioned_at = datetime.utcnow()
    if errors:
        tenant.social_provisioning_error = f"{len(errors)} post(s) failed: " + "; ".join(errors[:5])
        db.commit()
        raise ProvisioningError(tenant.social_provisioning_error)

    tenant.social_provisioning_error = None
    db.commit()
