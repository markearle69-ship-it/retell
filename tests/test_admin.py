import time
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import Lead, Portfolio, Tenant


def test_dashboard_requires_login():
    anon = TestClient(app)
    resp = anon.get("/admin", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/login"


def test_login_wrong_password():
    anon = TestClient(app)
    resp = anon.post("/admin/login", data={"password": "definitely-wrong"})
    assert resp.status_code == 401
    assert "Incorrect password" in resp.text


def test_login_fails_closed_when_admin_api_key_unset(monkeypatch):
    # ADMIN_API_KEY must default to "" - if it's ever left unset in the real
    # deployment, every check must reject rather than fall through to a
    # known guessable default.
    monkeypatch.setattr(settings, "admin_api_key", "")
    anon = TestClient(app)
    for attempted_password in ("", "change-me", "admin", "password"):
        resp = anon.post("/admin/login", data={"password": attempted_password})
        assert resp.status_code == 401


def test_dashboard_prefers_configured_public_base_url(monkeypatch):
    monkeypatch.setattr(settings, "public_base_url", "https://example.up.railway.app")
    client = TestClient(app)
    client.post("/admin/login", data={"password": settings.admin_api_key}, follow_redirects=False)

    resp = client.get("/admin")
    assert "https://example.up.railway.app/webhooks/retell" in resp.text


def test_login_and_manage_tenant_via_panel():
    client = TestClient(app)

    resp = client.post(
        "/admin/login", data={"password": settings.admin_api_key}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert client.cookies.get("admin_session")

    resp = client.get("/admin")
    assert resp.status_code == 200
    assert "/webhooks/retell" in resp.text
    # TestClient's default host is "testserver" over plain http, same as how
    # requests actually arrive at this app behind Railway's proxy - the
    # displayed URL must still say https, not echo the scheme it arrived as.
    assert "https://testserver/webhooks/retell" in resp.text

    resp = client.post(
        "/admin/tenants",
        data={
            "business_name": "Admin Panel Test Site",
            "to_number": "+15552223333",
            "notify_email": "panel@example.com",
            "notify_sms_number": "+15554445555",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    resp = client.get("/admin")
    assert "Admin Panel Test Site" in resp.text
    assert "+15552223333" in resp.text

    # Editing pre-fills the form
    tenants_resp = client.get("/tenants", headers={"x-admin-key": settings.admin_api_key})
    tenant_id = next(
        t["id"] for t in tenants_resp.json() if t["to_number"] == "+15552223333"
    )
    resp = client.get(f"/admin?edit_id={tenant_id}")
    assert 'value="Admin Panel Test Site"' in resp.text

    # Renaming via the same to_number updates rather than duplicating
    resp = client.post(
        "/admin/tenants",
        data={
            "tenant_id": str(tenant_id),
            "business_name": "Renamed Test Site",
            "to_number": "+15552223333",
            "notify_email": "panel@example.com",
            "notify_sms_number": "+15554445555",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    tenants_resp = client.get("/tenants", headers={"x-admin-key": settings.admin_api_key})
    matching = [t for t in tenants_resp.json() if t["to_number"] == "+15552223333"]
    assert len(matching) == 1
    assert matching[0]["business_name"] == "Renamed Test Site"

    # Delete it
    resp = client.post(f"/admin/tenants/{tenant_id}/delete", follow_redirects=False)
    assert resp.status_code == 303

    resp = client.get("/admin")
    assert "Renamed Test Site" not in resp.text


def test_add_site_with_manual_niche_select_does_not_500():
    # A real browser <select> always submits a value, including "" for the
    # "manual, no auto-provisioning" placeholder option — this must be treated
    # as "no niche", not fail form validation.
    client = TestClient(app)
    client.post("/admin/login", data={"password": settings.admin_api_key}, follow_redirects=False)

    resp = client.post(
        "/admin/tenants",
        data={
            "business_name": "Manual Select Site",
            "to_number": "+15558889999",
            "notify_email": "",
            "notify_sms_number": "",
            "location": "",
            "zip_codes": "",
            "niche_id": "",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    resp = client.get("/admin")
    assert "Manual Select Site" in resp.text


def test_lead_volume_stat_tiles():
    now = datetime.utcnow()
    unique = int(time.time() * 1000)
    db = SessionLocal()
    db.add(Lead(call_id=f"vol_within_24h_{unique}", created_at=now - timedelta(hours=1)))
    db.add(Lead(call_id=f"vol_within_7d_{unique}", created_at=now - timedelta(days=2)))
    db.add(Lead(call_id=f"vol_within_30d_{unique}", created_at=now - timedelta(days=20)))
    db.add(Lead(call_id=f"vol_too_old_{unique}", created_at=now - timedelta(days=40)))
    db.commit()

    before = {
        "last_24h": SessionLocal().query(Lead).filter(Lead.created_at >= now - timedelta(hours=24)).count(),
        "last_7d": SessionLocal().query(Lead).filter(Lead.created_at >= now - timedelta(days=7)).count(),
        "last_30d": SessionLocal().query(Lead).filter(Lead.created_at >= now - timedelta(days=30)).count(),
    }
    db.close()

    client = TestClient(app)
    client.post("/admin/login", data={"password": settings.admin_api_key}, follow_redirects=False)
    resp = client.get("/admin")
    assert resp.status_code == 200

    # These three counts include whatever other tests already inserted in the
    # shared DB, so assert against a freshly computed expectation rather than
    # small fixed numbers.
    assert f'<div class="stat-value">{before["last_24h"]}</div>' in resp.text
    assert f'<div class="stat-value">{before["last_7d"]}</div>' in resp.text
    assert f'<div class="stat-value">{before["last_30d"]}</div>' in resp.text


def test_create_portfolio_via_panel_generates_slug_and_password():
    client = TestClient(app)
    client.post("/admin/login", data={"password": settings.admin_api_key}, follow_redirects=False)

    resp = client.post(
        "/admin/portfolios",
        data={"name": "Austin Admin Test Portfolio"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    resp = client.get("/admin/portfolios")
    assert resp.status_code == 200
    assert "Austin Admin Test Portfolio" in resp.text

    db = SessionLocal()
    portfolio = db.query(Portfolio).filter(Portfolio.name == "Austin Admin Test Portfolio").first()
    assert portfolio is not None
    assert portfolio.slug  # auto-generated, non-empty
    assert portfolio.password_hash  # auto-generated password was hashed and stored
    db.close()


def test_create_portfolio_with_duplicate_slug_gets_suffixed():
    client = TestClient(app)
    client.post("/admin/login", data={"password": settings.admin_api_key}, follow_redirects=False)

    client.post(
        "/admin/portfolios",
        data={"name": "Dup Slug One", "slug": "dup-slug-test", "password": "hunter2"},
        follow_redirects=False,
    )
    client.post(
        "/admin/portfolios",
        data={"name": "Dup Slug Two", "slug": "dup-slug-test", "password": "hunter3"},
        follow_redirects=False,
    )

    db = SessionLocal()
    slugs = [
        p.slug
        for p in db.query(Portfolio).filter(Portfolio.name.in_(["Dup Slug One", "Dup Slug Two"])).all()
    ]
    db.close()
    assert len(slugs) == 2
    assert len(set(slugs)) == 2  # both rows got distinct slugs, no collision


def test_edit_portfolio_rename_and_password_change():
    client = TestClient(app)
    client.post("/admin/login", data={"password": settings.admin_api_key}, follow_redirects=False)

    client.post(
        "/admin/portfolios",
        data={"name": "Edit Me Portfolio", "slug": "edit-me-test", "password": "original-pw"},
        follow_redirects=False,
    )
    db = SessionLocal()
    portfolio = db.query(Portfolio).filter(Portfolio.slug == "edit-me-test").first()
    portfolio_id = portfolio.id
    original_hash = portfolio.password_hash
    db.close()

    resp = client.post(
        "/admin/portfolios",
        data={"portfolio_id": portfolio_id, "name": "Renamed Portfolio", "slug": "edit-me-test"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    db = SessionLocal()
    portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
    assert portfolio.name == "Renamed Portfolio"
    assert portfolio.password_hash == original_hash  # blank password left it unchanged
    db.close()


def test_delete_portfolio_unassigns_its_sites_without_deleting_them():
    client = TestClient(app)
    client.post("/admin/login", data={"password": settings.admin_api_key}, follow_redirects=False)

    client.post(
        "/admin/portfolios",
        data={"name": "Deletable Portfolio", "slug": "deletable-test", "password": "hunter2"},
        follow_redirects=False,
    )
    db = SessionLocal()
    portfolio = db.query(Portfolio).filter(Portfolio.slug == "deletable-test").first()
    portfolio_id = portfolio.id
    tenant = Tenant(business_name="Site In Deletable Portfolio", to_number="+15558880001", portfolio_id=portfolio_id)
    db.add(tenant)
    db.commit()
    tenant_id = tenant.id
    db.close()

    resp = client.post(f"/admin/portfolios/{portfolio_id}/delete", follow_redirects=False)
    assert resp.status_code == 303

    db = SessionLocal()
    assert db.query(Portfolio).filter(Portfolio.id == portfolio_id).first() is None
    surviving_tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    assert surviving_tenant is not None
    assert surviving_tenant.portfolio_id is None
    db.close()


def test_assign_tenant_to_portfolio_via_dashboard_form():
    client = TestClient(app)
    client.post("/admin/login", data={"password": settings.admin_api_key}, follow_redirects=False)

    client.post(
        "/admin/portfolios",
        data={"name": "Assignment Test Portfolio", "slug": "assignment-test", "password": "hunter2"},
        follow_redirects=False,
    )
    db = SessionLocal()
    portfolio_id = db.query(Portfolio).filter(Portfolio.slug == "assignment-test").first().id
    db.close()

    resp = client.post(
        "/admin/tenants",
        data={
            "business_name": "Portfolio Assigned Site",
            "to_number": "+15558880002",
            "notify_email": "",
            "notify_sms_number": "",
            "location": "",
            "zip_codes": "",
            "niche_id": "",
            "portfolio_id": str(portfolio_id),
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    db = SessionLocal()
    tenant = db.query(Tenant).filter(Tenant.business_name == "Portfolio Assigned Site").first()
    assert tenant.portfolio_id == portfolio_id
    db.close()

    resp = client.get("/admin")
    assert "Assignment Test Portfolio" in resp.text
