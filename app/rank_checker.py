"""Checks a site's organic Google ranking for its target keyword + location.

Deliberately does NOT drive a real (or "incognito") browser to google.com:
that's against Google's Terms of Service, gets your IP/residential network
rate-limited or CAPTCHA'd within a handful of queries, and personalizes
results based on the machine's location/history/cookies - all wrong for
"where does this EMD actually rank for anyone searching it". Checking ~70
sites on a schedule needs a stable, high-volume way to fetch results, which
is exactly what SERP APIs (SerpApi, DataForSEO, ValueSerp, ...) exist for:
they run the search server-side against a clean, geo-targeted browser
session and hand back structured JSON. This module is written against
SerpApi (https://serpapi.com) since it has a usable free tier for testing;
swap `_fetch_serp` for another provider's client if you'd rather use one -
everything else (matching, storage, alerting) stays the same.
"""

from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import httpx

from .config import settings
from .models import NicheTemplate, Tenant

SERPAPI_URL = "https://serpapi.com/search.json"


@dataclass
class RankCheckResult:
    query: str
    position: Optional[int] = None
    matched_url: Optional[str] = None
    num_results_checked: Optional[int] = None
    error: Optional[str] = None


def build_query(tenant: Tenant, niche: Optional[NicheTemplate] = None) -> str:
    """The search phrase to check: an explicit override, else the niche's
    service description, else just the business name - plus location."""
    keyword = (
        (tenant.target_keyword or "").strip()
        or (niche.service_description if niche else "")
        or ""
    ).strip() or tenant.business_name
    location = (tenant.location or "").strip()
    return f"{keyword} {location}".strip() if location else keyword


def _normalize_domain(value: str) -> str:
    value = value.strip().lower()
    if "//" not in value:
        value = f"//{value}"
    host = urlparse(value).netloc or value
    return host[4:] if host.startswith("www.") else host


def _fetch_serp(query: str, location: Optional[str], num_results: int) -> dict:
    if not settings.serpapi_key:
        raise RuntimeError("SERPAPI_KEY is not configured")

    params = {
        "engine": "google",
        "q": query,
        "google_domain": "google.com",
        "num": num_results,
        "api_key": settings.serpapi_key,
    }
    if location:
        params["location"] = location

    resp = httpx.get(SERPAPI_URL, params=params, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("error"):
        raise RuntimeError(payload["error"])
    return payload


def check_ranking(
    tenant: Tenant, niche: Optional[NicheTemplate] = None, num_results: Optional[int] = None
) -> RankCheckResult:
    """Runs one ranking check for `tenant` and returns the result - does not
    touch the database. Caller decides whether/how to persist it."""
    query = build_query(tenant, niche)
    num_results = num_results or settings.rank_check_num_results

    if not tenant.domain:
        return RankCheckResult(query=query, error="Tenant has no domain configured")

    try:
        payload = _fetch_serp(query, tenant.location, num_results)
    except Exception as exc:  # noqa: BLE001 - report any provider/network failure as a failed check
        return RankCheckResult(query=query, error=str(exc))

    target = _normalize_domain(tenant.domain)
    organic = payload.get("organic_results") or []

    for entry in organic:
        link = entry.get("link") or ""
        if not link:
            continue
        if _normalize_domain(link) == target:
            position = entry.get("position") or (organic.index(entry) + 1)
            return RankCheckResult(
                query=query,
                position=position,
                matched_url=link,
                num_results_checked=len(organic),
            )

    return RankCheckResult(query=query, num_results_checked=len(organic))
