import time

from fastapi.testclient import TestClient

from app import analytics
from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import AnalyticsSite


def _make_site(domain_suffix: str = "") -> AnalyticsSite:
    unique = int(time.time() * 1_000_000)
    db = SessionLocal()
    site = AnalyticsSite(
        name=f"Test Site {unique}",
        domain=f"example{unique}{domain_suffix}.com",
        site_key=analytics.generate_site_key(),
    )
    db.add(site)
    db.commit()
    db.refresh(site)
    db.close()
    return site


# --- parsing helpers -----------------------------------------------------


def test_parse_referrer_extracts_bing_query():
    result = analytics.parse_referrer("https://www.bing.com/search?q=emergency+ac+repair")
    assert result["search_engine"] == "bing"
    assert result["search_query"] == "emergency ac repair"
    assert result["referrer_host"] == "www.bing.com"


def test_parse_referrer_google_organic_has_no_query():
    # Google strips the query from organic referrers - this must not be
    # invented/guessed, the field should just be None.
    result = analytics.parse_referrer("https://www.google.com/")
    assert result["search_engine"] == "google"
    assert result["search_query"] is None


def test_parse_referrer_non_search_site():
    result = analytics.parse_referrer("https://www.facebook.com/somepage")
    assert result["search_engine"] is None
    assert result["referrer_host"] == "www.facebook.com"


def test_parse_referrer_excludes_own_domain():
    result = analytics.parse_referrer("https://mysite.com/other-page", own_host="mysite.com")
    assert result["referrer_host"] is None


def test_parse_referrer_none():
    result = analytics.parse_referrer(None)
    assert result == {"referrer_host": None, "search_engine": None, "search_query": None}


def test_parse_landing_url_utm_params():
    result = analytics.parse_landing_url(
        "https://mysite.com/?utm_source=google&utm_medium=cpc&utm_term=ac+repair"
    )
    assert result["utm_source"] == "google"
    assert result["utm_medium"] == "cpc"
    assert result["utm_term"] == "ac repair"


def test_parse_landing_url_gclid_fallback():
    result = analytics.parse_landing_url("https://mysite.com/?gclid=abc123")
    assert result["utm_source"] == "google (gclid)"


def test_path_from_url():
    assert analytics.path_from_url("https://mysite.com/services/ac-repair?x=1") == "/services/ac-repair"
    assert analytics.path_from_url(None) is None


def test_is_bot_ua():
    assert analytics.is_bot_ua("Mozilla/5.0 (compatible; Googlebot/2.1)")
    assert not analytics.is_bot_ua(
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"
    )


def test_visitor_hash_rotates_daily_and_never_reversible():
    h1 = analytics.visitor_hash("1.2.3.4", "some-ua", "site-key", "2026-08-06")
    h2 = analytics.visitor_hash("1.2.3.4", "some-ua", "site-key", "2026-08-07")
    assert h1 != h2
    assert "1.2.3.4" not in h1


# --- /collect ingestion ----------------------------------------------------


def test_collect_records_pageview_with_search_query():
    site = _make_site()
    client = TestClient(app)

    resp = client.post(
        "/collect",
        content='{"site_key": "%s", "url": "https://%s/landing", "referrer": "https://www.bing.com/search?q=ac+repair"}'
        % (site.site_key, site.domain),
        headers={"Content-Type": "text/plain", "User-Agent": "Mozilla/5.0 (Macintosh)"},
    )
    assert resp.status_code == 204

    db = SessionLocal()
    stats = analytics.site_stats(db, db.query(AnalyticsSite).filter(AnalyticsSite.id == site.id).first())
    db.close()

    assert stats["views_30d"] == 1
    assert stats["search_queries"] == [("ac repair", 1)]
    assert stats["search_engines"] == [("bing", 1)]


def test_collect_unknown_site_key_is_silent_204():
    client = TestClient(app)
    resp = client.post(
        "/collect",
        content='{"site_key": "does-not-exist", "url": "https://x.com/"}',
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status_code == 204


def test_collect_missing_body_is_silent_204():
    client = TestClient(app)
    resp = client.post("/collect", content=b"", headers={"Content-Type": "text/plain"})
    assert resp.status_code == 204


def test_collect_does_not_store_raw_ip_or_user_agent():
    site = _make_site()
    client = TestClient(app)
    client.post(
        "/collect",
        content='{"site_key": "%s", "url": "https://%s/"}' % (site.site_key, site.domain),
        headers={"Content-Type": "text/plain", "User-Agent": "some very specific browser string"},
    )

    from app.models import PageView

    db = SessionLocal()
    row = db.query(PageView).filter(PageView.site_id == site.id).order_by(PageView.id.desc()).first()
    db.close()
    assert row is not None
    # No column on the model even carries a raw UA or IP - this is really a
    # schema assertion, but exercise it via the actual persisted row too.
    assert "some very specific browser string" not in str(row.__dict__)


def test_tracker_script_served():
    client = TestClient(app)
    resp = client.get("/t.js")
    assert resp.status_code == 200
    assert "sendBeacon" in resp.text
    assert resp.headers["content-type"].startswith("application/javascript")


# --- admin panel -----------------------------------------------------------


def test_admin_can_add_and_view_analytics_site():
    client = TestClient(app)
    client.post("/admin/login", data={"password": settings.admin_api_key}, follow_redirects=False)

    unique = int(time.time() * 1_000_000)
    domain = f"admintest{unique}.com"
    resp = client.post(
        "/admin/analytics/sites",
        data={"name": "Admin Test Site", "domain": domain},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    resp = client.get("/admin/analytics")
    assert "Admin Test Site" in resp.text
    assert domain in resp.text
    assert "data-site" in resp.text
    assert "/t.js" in resp.text

    db = SessionLocal()
    site = db.query(AnalyticsSite).filter(AnalyticsSite.domain == domain).first()
    db.close()
    assert site is not None
    assert site.site_key in resp.text

    resp = client.get(f"/admin/analytics/sites/{site.id}")
    assert resp.status_code == 200
    assert "Admin Test Site" in resp.text


def test_admin_cannot_add_duplicate_domain():
    client = TestClient(app)
    client.post("/admin/login", data={"password": settings.admin_api_key}, follow_redirects=False)

    unique = int(time.time() * 1_000_000)
    domain = f"dupe{unique}.com"
    client.post("/admin/analytics/sites", data={"name": "Site A", "domain": domain})
    resp = client.post("/admin/analytics/sites", data={"name": "Site B", "domain": domain})
    assert resp.status_code == 400
    assert "already tracked" in resp.text


def test_admin_regenerate_key_invalidates_old_key():
    site = _make_site()
    old_key = site.site_key

    client = TestClient(app)
    client.post("/admin/login", data={"password": settings.admin_api_key}, follow_redirects=False)
    resp = client.post(f"/admin/analytics/sites/{site.id}/regenerate-key", follow_redirects=False)
    assert resp.status_code == 303

    # old key no longer records anything
    resp = client.post(
        "/collect",
        content='{"site_key": "%s", "url": "https://%s/"}' % (old_key, site.domain),
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status_code == 204

    db = SessionLocal()
    from app.models import PageView

    count = db.query(PageView).filter(PageView.site_id == site.id).count()
    db.close()
    assert count == 0


def test_admin_delete_site_cascades_page_views():
    site = _make_site()
    client = TestClient(app)
    client.post(
        "/collect",
        content='{"site_key": "%s", "url": "https://%s/"}' % (site.site_key, site.domain),
        headers={"Content-Type": "text/plain"},
    )

    client.post("/admin/login", data={"password": settings.admin_api_key}, follow_redirects=False)
    resp = client.post(f"/admin/analytics/sites/{site.id}/delete", follow_redirects=False)
    assert resp.status_code == 303

    db = SessionLocal()
    from app.models import PageView

    assert db.query(AnalyticsSite).filter(AnalyticsSite.id == site.id).first() is None
    assert db.query(PageView).filter(PageView.site_id == site.id).count() == 0
    db.close()


def test_analytics_requires_login():
    anon = TestClient(app)
    resp = anon.get("/admin/analytics", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/login"
