import time

import httpx
import pytest

from app import models, provisioning
from app.db import SessionLocal


class _FakeResponse:
    def __init__(self, status_code: int, text: str, json_data: dict | None = None):
        self.status_code = status_code
        self.text = text
        self._json_data = json_data or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://api.retellai.com/import-phone-number")
            raise httpx.HTTPStatusError("error", request=request, response=self)

    def json(self):
        return self._json_data


class _FakeRetellClient:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, headers=None, json=None):
        return _FakeResponse(400, '{"status":"error","message":"Phone number already exists."}')


def test_import_number_already_exists_gives_actionable_message(monkeypatch):
    monkeypatch.setattr(provisioning.httpx, "Client", _FakeRetellClient)
    monkeypatch.setattr(provisioning, "get_trunk_termination_uri", lambda: "test.pstn.twilio.com")

    with pytest.raises(provisioning.ProvisioningError) as exc_info:
        provisioning.import_number_to_retell("+15551234567", "agent_123")

    assert "already has a phone number import" in str(exc_info.value)
    assert "leave this site's niche unset" in str(exc_info.value)


class _NicheStub:
    prompt_template = "Prompt for {{business_name}}, covering {{service_description}}: {{collect_list}}"
    begin_message_template = None
    model = "gpt-4.1"
    voice_id = "bad-voice-id"
    service_description = "air conditioning repair"
    problem_domain = "vehicle AC issues"
    collect_list = "- make/model\n- issue description"


class _TenantStub:
    business_name = "Test Business"
    location = "Testville"
    zip_codes = "00000"


def test_create_retell_agent_reports_llm_step_failure(monkeypatch):
    class _FailLlmClient(_FakeRetellClient):
        def post(self, url, headers=None, json=None):
            return _FakeResponse(404, '{"status":"error","message":"Not Found"}')

    monkeypatch.setattr(provisioning.httpx, "Client", _FailLlmClient)

    with pytest.raises(provisioning.ProvisioningError) as exc_info:
        provisioning.create_retell_agent(_NicheStub(), _TenantStub())

    assert "Retell LLM creation failed" in str(exc_info.value)


def test_create_retell_agent_reports_agent_step_failure_and_llm_id(monkeypatch):
    class _FailAgentClient(_FakeRetellClient):
        def __init__(self, *a, **kw):
            self.calls = 0

        def post(self, url, headers=None, json=None):
            self.calls += 1
            if self.calls == 1:
                return _FakeResponse(200, "ok", json_data={"llm_id": "llm_abc123"})
            return _FakeResponse(404, '{"status":"error","message":"Not Found"}')

    monkeypatch.setattr(provisioning.httpx, "Client", _FailAgentClient)

    with pytest.raises(provisioning.ProvisioningError) as exc_info:
        provisioning.create_retell_agent(_NicheStub(), _TenantStub())

    message = str(exc_info.value)
    assert "Retell agent creation failed" in message
    assert "llm_abc123" in message
    assert "orphaned" in message


def test_create_retell_agent_substitutes_niche_level_variables(monkeypatch):
    captured = {}

    class _CapturingClient(_FakeRetellClient):
        def post(self, url, headers=None, json=None):
            if url.endswith("/create-retell-llm"):
                captured["llm_payload"] = json
                return _FakeResponse(200, "ok", json_data={"llm_id": "llm_captured"})
            captured["agent_payload"] = json
            return _FakeResponse(200, "ok", json_data={"agent_id": "agent_captured"})

    monkeypatch.setattr(provisioning.httpx, "Client", _CapturingClient)

    provisioning.create_retell_agent(_NicheStub(), _TenantStub())

    prompt = captured["llm_payload"]["general_prompt"]
    assert "Test Business" in prompt  # per-site
    assert "air conditioning repair" in prompt  # per-niche
    assert "make/model" in prompt  # per-niche

    tool_types = {t["type"] for t in captured["llm_payload"]["general_tools"]}
    assert "end_call" in tool_types


def test_webhook_url_blank_when_not_configured():
    assert provisioning.settings.public_base_url == ""
    assert provisioning.webhook_url() is None


def test_create_retell_agent_sets_webhook_url_when_configured(monkeypatch):
    monkeypatch.setattr(provisioning.settings, "public_base_url", "https://example.up.railway.app")
    captured = {}

    class _CapturingClient(_FakeRetellClient):
        def post(self, url, headers=None, json=None):
            if url.endswith("/create-retell-llm"):
                return _FakeResponse(200, "ok", json_data={"llm_id": "llm_captured"})
            captured["agent_payload"] = json
            return _FakeResponse(200, "ok", json_data={"agent_id": "agent_captured"})

    monkeypatch.setattr(provisioning.httpx, "Client", _CapturingClient)

    provisioning.create_retell_agent(_NicheStub(), _TenantStub())

    assert captured["agent_payload"]["webhook_url"] == "https://example.up.railway.app/webhooks/retell"


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
