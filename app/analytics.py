"""Self-hosted, cookie-free page-view analytics for the microsite portfolio.

Deliberately doesn't use Google Analytics / Search Console (or any
third-party script) so there's no shared footprint across sites - each site
just loads its own copy of `/t.js` from this service and posts a beacon to
`/collect`. No cookies, no localStorage, no raw IP or full user-agent is
ever written to the database - see `visitor_hash()`.

"What query got them here" has two real sources, and it matters to be
straight about the difference:

- Organic search referrers (Google, Bing, DuckDuckGo, ...): Google has
  stripped the search query from its outbound referrer for organic results
  since 2013 ("(not provided)" - this is why GA shows that too). We still
  parse the referrer for engines that do pass it (Bing, DuckDuckGo, Yahoo,
  Yandex, Baidu, ...), but Google organic will show up as
  search_engine="google", search_query=None.
- UTM parameters on the landing URL (utm_term especially) - fully reliable,
  because you control them. If these sites get paid traffic, set
  utm_term={keyword} (Google Ads ValueTrack) or the Bing Ads equivalent in
  the destination URL and the exact keyword shows up here every time.
"""

import hashlib
import hmac
import re
import secrets
from collections import Counter
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import parse_qs, urlparse

from sqlalchemy.orm import Session

from . import models
from .config import settings

# --- site key generation --------------------------------------------------


def generate_site_key() -> str:
    return secrets.token_urlsafe(16)


# --- pseudonymous visitor hashing ------------------------------------------
#
# No IP address or user-agent string is ever stored. We only store an HMAC
# of (ip, user-agent, site key, and the calendar day) - keyed with a server
# secret. The day component means the hash is different tomorrow for the
# same visitor, so it can't be used to build a cross-day profile or re-
# identify anyone; it exists purely so "3 views from 1 person today" doesn't
# get counted as "3 visitors".


def visitor_hash(ip: str, user_agent: str, site_key: str, day: str) -> str:
    key = settings.analytics_salt.encode("utf-8")
    msg = f"{ip}|{user_agent}|{site_key}|{day}".encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def client_ip(request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


# --- bot filtering -----------------------------------------------------

_BOT_UA_RE = re.compile(
    r"bot|crawl|spider|slurp|headless|preview|facebookexternalhit|"
    r"pingdom|uptimerobot|monitor|curl|wget|python-requests|axios|"
    r"go-http-client|scrapy",
    re.IGNORECASE,
)


def is_bot_ua(user_agent: str) -> bool:
    return bool(_BOT_UA_RE.search(user_agent or ""))


# --- coarse device/browser classification (no third-party UA library) ---


def device_type(user_agent: str) -> str:
    ua = user_agent or ""
    if re.search(r"iPad|Tablet", ua, re.IGNORECASE):
        return "tablet"
    if re.search(r"Mobi|Android(?!.*Tablet)|iPhone", ua, re.IGNORECASE):
        return "mobile"
    return "desktop"


_BROWSER_PATTERNS = [
    ("edge", r"Edg/"),
    ("opera", r"OPR/|Opera"),
    ("chrome", r"Chrome/"),
    ("firefox", r"Firefox/"),
    ("safari", r"Version/.*Safari/"),
]


def browser_name(user_agent: str) -> str:
    ua = user_agent or ""
    for name, pattern in _BROWSER_PATTERNS:
        if re.search(pattern, ua):
            return name
    return "other"


# --- referrer / search-query parsing ----------------------------------

# domain substring -> query-string param that carries the search term
_SEARCH_ENGINES = {
    "google.": "q",
    "bing.com": "q",
    "search.yahoo.": "p",
    "duckduckgo.com": "q",
    "yandex.": "text",
    "baidu.com": "wd",
    "ecosia.org": "q",
    "startpage.com": "query",
    "search.brave.com": "q",
    "ask.com": "q",
    "aol.com": "q",
}


def parse_referrer(referrer: Optional[str], own_host: Optional[str] = None) -> dict:
    """Returns {"referrer_host", "search_engine", "search_query"} - any of
    which may be None. `own_host` (the site's own domain) is excluded from
    referrer_host so on-site navigation doesn't get counted as a referral."""

    result = {"referrer_host": None, "search_engine": None, "search_query": None}
    if not referrer:
        return result

    try:
        parsed = urlparse(referrer)
    except ValueError:
        return result

    host = (parsed.netloc or "").lower()
    if not host or (own_host and host == own_host.lower()):
        return result

    result["referrer_host"] = host

    for engine_substr, param in _SEARCH_ENGINES.items():
        if engine_substr in host:
            engine_name = engine_substr.rstrip(".").split(".")[0]
            result["search_engine"] = engine_name
            query_params = parse_qs(parsed.query)
            values = query_params.get(param)
            if values:
                result["search_query"] = values[0]
            break

    return result


def path_from_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    return (parsed.path or "/")[:1024]


_UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content")


def parse_landing_url(url: Optional[str]) -> dict:
    """Extracts UTM params (and a couple of ad-click-id fallbacks) from the
    page URL the visitor actually landed on."""

    result = {key: None for key in _UTM_KEYS}
    if not url:
        return result

    try:
        parsed = urlparse(url)
    except ValueError:
        return result

    query_params = parse_qs(parsed.query)
    for key in _UTM_KEYS:
        values = query_params.get(key)
        if values:
            result[key] = values[0][:255]

    if not result["utm_source"]:
        if query_params.get("gclid"):
            result["utm_source"] = "google (gclid)"
        elif query_params.get("msclkid"):
            result["utm_source"] = "bing (msclkid)"

    return result


# --- stats aggregation for the admin dashboard --------------------------


def site_stats(db: Session, site: models.AnalyticsSite, now: Optional[datetime] = None) -> dict:
    now = now or datetime.utcnow()
    since_30d = now - timedelta(days=30)

    views = (
        db.query(models.PageView)
        .filter(
            models.PageView.site_id == site.id,
            models.PageView.occurred_at >= since_30d,
            models.PageView.is_bot.is_(False),
        )
        .all()
    )

    def in_window(hours: int) -> list:
        cutoff = now - timedelta(hours=hours)
        return [v for v in views if v.occurred_at >= cutoff]

    def unique_count(rows: list) -> int:
        return len({v.visitor_hash for v in rows})

    v24, v7d, v30d = in_window(24), in_window(24 * 7), views

    daily: dict = {}
    for v in views:
        day = v.occurred_at.strftime("%Y-%m-%d")
        bucket = daily.setdefault(day, {"views": 0, "visitors": set()})
        bucket["views"] += 1
        bucket["visitors"].add(v.visitor_hash)
    daily_series = [
        {"date": day, "views": b["views"], "visitors": len(b["visitors"])}
        for day, b in sorted(daily.items())
    ]

    top_pages = Counter(v.path for v in v30d if v.path).most_common(10)
    top_referrers = Counter(
        v.referrer_host for v in v30d if v.referrer_host and not v.search_engine
    ).most_common(10)
    search_engines = Counter(v.search_engine for v in v30d if v.search_engine).most_common(10)
    search_queries = Counter(v.search_query for v in v30d if v.search_query).most_common(20)
    campaigns = Counter(
        (v.utm_source, v.utm_campaign, v.utm_term)
        for v in v30d
        if v.utm_source or v.utm_campaign or v.utm_term
    ).most_common(15)

    recent = (
        db.query(models.PageView)
        .filter(models.PageView.site_id == site.id)
        .order_by(models.PageView.occurred_at.desc())
        .limit(50)
        .all()
    )

    return {
        "views_24h": len(v24),
        "visitors_24h": unique_count(v24),
        "views_7d": len(v7d),
        "visitors_7d": unique_count(v7d),
        "views_30d": len(v30d),
        "visitors_30d": unique_count(v30d),
        "daily_series": daily_series,
        "top_pages": top_pages,
        "top_referrers": top_referrers,
        "search_engines": search_engines,
        "search_queries": search_queries,
        "campaigns": campaigns,
        "recent": recent,
    }
