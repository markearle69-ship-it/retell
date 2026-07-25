from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


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
