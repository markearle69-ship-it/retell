import time

import pytest

from app import models, provisioning
from app.db import SessionLocal


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def test_render_template_substitutes_known_variables():
    text = "Hello {{business_name}} in {{location}}, zips: {{zip_codes}}."
    result = provisioning.render_template(
        text, {"business_name": "Joe's Plumbing", "location": "Austin, TX", "zip_codes": "78701"}
    )
    assert result == "Hello Joe's Plumbing in Austin, TX, zips: 78701."


def test_render_template_leaves_unknown_variables_untouched():
    result = provisioning.render_template("Hi {{unknown}}", {"business_name": "x"})
    assert result == "Hi {{unknown}}"


def test_provision_site_runs_all_steps_and_is_idempotent(db, monkeypatch):
    niche = models.NicheTemplate(
        name=f"Test Niche {time.time()}",
        prompt_template="You work for {{business_name}} in {{location}}.",
        voice_id="voice-1",
        model="gpt-4.1",
    )
    db.add(niche)
    db.commit()
    db.refresh(niche)

    tenant = models.Tenant(business_name="Test Site", to_number=f"+1555{int(time.time()) % 10_000_000:07d}")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)

    calls = {"agent": 0, "trunk": 0, "import": 0}

    def fake_create_agent(n, t):
        calls["agent"] += 1
        return "llm_123", "agent_123"

    def fake_attach(phone_number):
        calls["trunk"] += 1
        return "PN123"

    def fake_import(phone_number, agent_id):
        calls["import"] += 1

    monkeypatch.setattr(provisioning, "create_retell_agent", fake_create_agent)
    monkeypatch.setattr(provisioning, "attach_number_to_trunk", fake_attach)
    monkeypatch.setattr(provisioning, "import_number_to_retell", fake_import)

    provisioning.provision_site(db, tenant, niche)

    assert tenant.retell_llm_id == "llm_123"
    assert tenant.retell_agent_id == "agent_123"
    assert tenant.twilio_number_sid == "PN123"
    assert tenant.provisioned_at is not None
    assert calls == {"agent": 1, "trunk": 1, "import": 1}

    # Resubmitting should skip every already-completed step.
    provisioning.provision_site(db, tenant, niche)
    assert calls == {"agent": 1, "trunk": 1, "import": 1}


def test_provision_site_retries_only_failed_step(db, monkeypatch):
    niche = models.NicheTemplate(
        name=f"Retry Niche {time.time()}",
        prompt_template="Prompt for {{business_name}}",
        voice_id="voice-1",
    )
    db.add(niche)
    db.commit()
    db.refresh(niche)

    tenant = models.Tenant(business_name="Retry Site", to_number=f"+1555{int(time.time() * 7) % 10_000_000:07d}")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)

    calls = {"agent": 0, "trunk": 0, "import": 0}

    def fake_create_agent(n, t):
        calls["agent"] += 1
        return "llm_1", "agent_1"

    def fake_attach_fail(phone_number):
        calls["trunk"] += 1
        raise provisioning.ProvisioningError("twilio boom")

    monkeypatch.setattr(provisioning, "create_retell_agent", fake_create_agent)
    monkeypatch.setattr(provisioning, "attach_number_to_trunk", fake_attach_fail)

    with pytest.raises(provisioning.ProvisioningError):
        provisioning.provision_site(db, tenant, niche)

    assert tenant.retell_agent_id == "agent_1"
    assert tenant.twilio_number_sid is None
    assert calls == {"agent": 1, "trunk": 1, "import": 0}

    def fake_attach_ok(phone_number):
        calls["trunk"] += 1
        return "PN999"

    def fake_import(phone_number, agent_id):
        calls["import"] += 1

    monkeypatch.setattr(provisioning, "attach_number_to_trunk", fake_attach_ok)
    monkeypatch.setattr(provisioning, "import_number_to_retell", fake_import)

    provisioning.provision_site(db, tenant, niche)

    # The already-succeeded agent-creation step is NOT repeated.
    assert calls == {"agent": 1, "trunk": 2, "import": 1}
    assert tenant.provisioned_at is not None
