import time
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import Lead


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
