from fastapi.testclient import TestClient

from app import admin
from app.config import settings
from app.main import app


def _logged_in_client() -> TestClient:
    client = TestClient(app)
    resp = client.post("/admin/login", data={"password": settings.admin_api_key}, follow_redirects=False)
    assert resp.status_code == 303
    return client


def test_create_and_edit_niche_via_panel():
    client = _logged_in_client()

    resp = client.post(
        "/admin/niches",
        data={
            "name": "Panel Test Niche",
            "prompt_template": "You work for {{business_name}} in {{location}}.",
            "voice_id": "11labs-Adrian",
            "model": "gpt-4.1",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    resp = client.get("/admin/niches")
    assert resp.status_code == 200
    assert "Panel Test Niche" in resp.text
    assert "11labs-Adrian" in resp.text


def test_provisioning_form_calls_provision_site(monkeypatch):
    client = _logged_in_client()

    client.post(
        "/admin/niches",
        data={
            "name": "Auto Provision Niche",
            "prompt_template": "You work for {{business_name}} in {{location}}, covering {{zip_codes}}.",
            "voice_id": "11labs-Adrian",
            "model": "gpt-4.1",
        },
        follow_redirects=False,
    )
    niches_resp = client.get("/admin/niches")
    assert "Auto Provision Niche" in niches_resp.text

    # Find the niche id via the JSON admin API rather than scraping HTML.
    # (There's no JSON endpoint for niches, so fetch it straight from the DB.)
    from app.db import SessionLocal
    from app.models import NicheTemplate

    db = SessionLocal()
    niche = db.query(NicheTemplate).filter(NicheTemplate.name == "Auto Provision Niche").first()
    assert niche is not None
    niche_id = niche.id
    db.close()

    captured = {}

    def fake_provision_site(db, tenant, niche):
        captured["business_name"] = tenant.business_name
        captured["niche_name"] = niche.name
        tenant.retell_agent_id = "agent_fake"
        tenant.retell_llm_id = "llm_fake"
        tenant.twilio_number_sid = "PN_fake"
        from datetime import datetime

        tenant.provisioned_at = datetime.utcnow()
        db.commit()

    monkeypatch.setattr(admin, "provision_site", fake_provision_site)

    resp = client.post(
        "/admin/tenants",
        data={
            "business_name": "Auto Provisioned Site",
            "to_number": "+15556661111",
            "notify_email": "auto@example.com",
            "location": "Socorro, Texas",
            "zip_codes": "79927, 79928",
            "niche_id": str(niche_id),
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert captured == {"business_name": "Auto Provisioned Site", "niche_name": "Auto Provision Niche"}

    dashboard_resp = client.get("/admin")
    assert "Auto Provisioned Site" in dashboard_resp.text
    assert "Auto &#10003;" in dashboard_resp.text


def test_provisioning_form_shows_error_on_failure(monkeypatch):
    client = _logged_in_client()

    client.post(
        "/admin/niches",
        data={
            "name": "Failing Niche",
            "prompt_template": "Prompt for {{business_name}}",
            "voice_id": "11labs-Adrian",
        },
        follow_redirects=False,
    )

    from app.db import SessionLocal
    from app.models import NicheTemplate

    db = SessionLocal()
    niche = db.query(NicheTemplate).filter(NicheTemplate.name == "Failing Niche").first()
    niche_id = niche.id
    db.close()

    from app.provisioning import ProvisioningError

    def fake_provision_site_fails(db, tenant, niche):
        raise ProvisioningError("Twilio number not found — buy it first")

    monkeypatch.setattr(admin, "provision_site", fake_provision_site_fails)

    resp = client.post(
        "/admin/tenants",
        data={
            "business_name": "Broken Site",
            "to_number": "+15557772222",
            "niche_id": str(niche_id),
        },
    )
    assert resp.status_code == 400
    assert "Twilio number not found" in resp.text


def test_reverting_to_manual_clears_stale_provisioning_error(monkeypatch):
    client = _logged_in_client()

    client.post(
        "/admin/niches",
        data={
            "name": "Revert Niche",
            "prompt_template": "Prompt for {{business_name}}",
            "voice_id": "11labs-Adrian",
        },
        follow_redirects=False,
    )

    from app.db import SessionLocal
    from app.models import NicheTemplate
    from app.provisioning import ProvisioningError

    db = SessionLocal()
    niche_id = db.query(NicheTemplate).filter(NicheTemplate.name == "Revert Niche").first().id
    db.close()

    def fake_provision_site_fails(db, tenant, niche):
        raise ProvisioningError("Phone number already exists.")

    monkeypatch.setattr(admin, "provision_site", fake_provision_site_fails)

    resp = client.post(
        "/admin/tenants",
        data={
            "business_name": "Already Manual Site",
            "to_number": "+15551119999",
            "niche_id": str(niche_id),
        },
    )
    assert resp.status_code == 400
    assert "already exists" in resp.text

    # Reverting to manual (no niche selected) should clear the stale error,
    # not leave the site stuck showing "Error" forever.
    resp = client.post(
        "/admin/tenants",
        data={
            "business_name": "Already Manual Site",
            "to_number": "+15551119999",
            "niche_id": "",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    dashboard_resp = client.get("/admin")
    assert "Already Manual Site" in dashboard_resp.text
    row = dashboard_resp.text.split("Already Manual Site")[1].split("</tr>")[0]
    assert "Manual" in row
    assert "already exists" not in row


def test_force_reprovision_checkbox_shows_for_partial_state_not_just_full_success():
    client = _logged_in_client()

    resp = client.post(
        "/admin/tenants",
        data={"business_name": "Partial Provision Site", "to_number": "+15553334444"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    from app.db import SessionLocal
    from app.models import Tenant

    db = SessionLocal()
    tenant = db.query(Tenant).filter(Tenant.to_number == "+15553334444").first()
    tenant_id = tenant.id
    # Simulate a partially-completed provisioning attempt: agent created,
    # but the number import step never succeeded (provisioned_at stays None).
    tenant.retell_agent_id = "agent_stale_123"
    db.commit()
    db.close()

    resp = client.get(f"/admin?edit_id={tenant_id}")
    assert "force_reprovision" in resp.text
