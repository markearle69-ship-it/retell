import time

import httpx
import pytest

from app import models, provisioning
from app.db import SessionLocal


class _FakeResponse:
    def __init__(self, status_code: int, text: str, json_data: dict | list | None = None):
        self.status_code = status_code
        self.text = text
        self._json_data = json_data if json_data is not None else {}

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://api.retellai.com/import-phone-number")
            raise httpx.HTTPStatusError("error", request=request, response=self)

    def json(self):
        return self._json_data


# Voice list used by every fake client's default .get() - includes the voice_id
# every _NicheStub in this file uses, so the pre-flight validation in
# create_retell_agent passes and tests can exercise the steps after it.
_FAKE_VOICE_LIST = [{"voice_id": "bad-voice-id"}, {"voice_id": "11labs-Marissa"}]


class _FakeRetellClient:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, headers=None):
        return _FakeResponse(200, "ok", json_data=_FAKE_VOICE_LIST)

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
    id = 1
    name = "Test Niche"
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


def test_create_retell_agent_rejects_unknown_voice_id_before_creating_anything(monkeypatch):
    class _UnknownVoiceNiche(_NicheStub):
        name = "Concrete Contractor"
        voice_id = "11Labs-Marissa"  # wrong case - not in _FAKE_VOICE_LIST

    posted = []

    class _TrackingClient(_FakeRetellClient):
        def post(self, url, headers=None, json=None):
            posted.append(url)
            return _FakeResponse(200, "ok", json_data={"llm_id": "should_not_happen"})

    monkeypatch.setattr(provisioning.httpx, "Client", _TrackingClient)

    with pytest.raises(provisioning.ProvisioningError) as exc_info:
        provisioning.create_retell_agent(_UnknownVoiceNiche(), _TenantStub())

    message = str(exc_info.value)
    assert "11Labs-Marissa" in message
    assert "Concrete Contractor" in message
    assert "case mismatch" in message
    assert posted == []  # no LLM or agent creation attempted


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

    def fake_create_agent(n, t, shared_rules=""):
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

    def fake_create_agent(n, t, shared_rules=""):
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

    assert calls == {"agent": 1, "trunk": 2, "import": 1}
    assert tenant.provisioned_at is not None


def test_get_global_prompt_config_creates_default_row_once(db):
    from app.models import DEFAULT_GLOBAL_PROMPT_RULES, GlobalPromptConfig

    db.query(GlobalPromptConfig).delete()
    db.commit()

    config = provisioning.get_global_prompt_config(db)
    assert config.shared_rules == DEFAULT_GLOBAL_PROMPT_RULES

    config.shared_rules = "Custom rule text."
    db.commit()

    # Second call returns the same row, not a fresh default.
    config_again = provisioning.get_global_prompt_config(db)
    assert config_again.shared_rules == "Custom rule text."
    assert db.query(GlobalPromptConfig).count() == 1


def test_create_retell_agent_prepends_shared_rules(monkeypatch):
    captured = {}

    class _CapturingClient(_FakeRetellClient):
        def post(self, url, headers=None, json=None):
            if url.endswith("/create-retell-llm"):
                captured["llm_payload"] = json
                return _FakeResponse(200, "ok", json_data={"llm_id": "llm_captured"})
            return _FakeResponse(200, "ok", json_data={"agent_id": "agent_captured"})

    monkeypatch.setattr(provisioning.httpx, "Client", _CapturingClient)

    provisioning.create_retell_agent(
        _NicheStub(), _TenantStub(), shared_rules="Never claim to be human."
    )

    prompt = captured["llm_payload"]["general_prompt"]
    assert prompt.startswith("Never claim to be human.")
    assert "Test Business" in prompt  # niche prompt still present after it


def test_create_retell_agent_without_shared_rules_is_unchanged(monkeypatch):
    captured = {}

    class _CapturingClient(_FakeRetellClient):
        def post(self, url, headers=None, json=None):
            if url.endswith("/create-retell-llm"):
                captured["llm_payload"] = json
                return _FakeResponse(200, "ok", json_data={"llm_id": "llm_captured"})
            return _FakeResponse(200, "ok", json_data={"agent_id": "agent_captured"})

    monkeypatch.setattr(provisioning.httpx, "Client", _CapturingClient)

    provisioning.create_retell_agent(_NicheStub(), _TenantStub())

    prompt = captured["llm_payload"]["general_prompt"]
    assert prompt.startswith("Prompt for Test Business")


class _TenantWithIdsStub:
    def __init__(
        self,
        retell_llm_id="llm_existing",
        retell_agent_id="agent_existing",
        niche_template=None,
        business_name="Test Business",
        location="Testville",
        zip_codes="00000",
    ):
        self.retell_llm_id = retell_llm_id
        self.retell_agent_id = retell_agent_id
        self.niche_template = niche_template
        self.niche_template_id = niche_template.id if niche_template is not None else None
        self.business_name = business_name
        self.location = location
        self.zip_codes = zip_codes


def test_sync_existing_agent_skips_non_provisioned_site():
    tenant = _TenantWithIdsStub(retell_llm_id=None, retell_agent_id=None)
    assert provisioning.sync_existing_agent(tenant, "Some rule.") == "skipped (not auto-provisioned)"


def test_sync_existing_agent_already_up_to_date_without_niche(monkeypatch):
    # No niche assigned - falls back to the narrow prefix/tool-presence check.
    class _Client(_FakeRetellClient):
        def get(self, url, headers=None):
            return _FakeResponse(
                200,
                "ok",
                json_data={
                    "general_prompt": "Some rule.\n\nNiche prompt here.",
                    "general_tools": [provisioning.END_CALL_TOOL],
                },
            )

    calls = {"patch": 0, "post": 0}
    client = _Client()
    monkeypatch.setattr(
        client,
        "patch",
        lambda *a, **kw: calls.__setitem__("patch", calls["patch"] + 1),
        raising=False,
    )
    monkeypatch.setattr(provisioning.httpx, "Client", lambda *a, **kw: client)

    status = provisioning.sync_existing_agent(_TenantWithIdsStub(), "Some rule.")

    assert status == "already up to date"
    assert calls["patch"] == 0


def test_sync_existing_agent_already_up_to_date_with_niche(monkeypatch):
    # Niche assigned, live prompt already matches a fresh render exactly -
    # must not update just because it's re-checking every time.
    niche = _NicheStub()
    tenant = _TenantWithIdsStub(niche_template=niche)
    variables = provisioning._template_variables(tenant, niche)
    current_prompt = f"Some rule.\n\n{provisioning.render_template(niche.prompt_template, variables)}"

    class _Client(_FakeRetellClient):
        def get(self, url, headers=None):
            return _FakeResponse(
                200,
                "ok",
                json_data={"general_prompt": current_prompt, "general_tools": [provisioning.END_CALL_TOOL]},
            )

    calls = {"create": 0}

    def _post(self, url, headers=None, json=None):
        calls["create"] += 1
        return _FakeResponse(200, "ok", json_data={"llm_id": "should_not_happen"})

    monkeypatch.setattr(_Client, "post", _post, raising=False)
    monkeypatch.setattr(provisioning.httpx, "Client", _Client)

    status = provisioning.sync_existing_agent(tenant, "Some rule.")

    assert status == "already up to date"
    assert calls["create"] == 0


def test_sync_existing_agent_detects_niche_content_drift_and_updates(monkeypatch):
    # This is the scenario that motivated this: global rules + end_call are
    # already present (so the old narrow check would say "up to date"), but
    # the niche's OWN prompt content (e.g. a rewritten COVERAGE section) has
    # since changed - that drift must still be detected and pushed live.
    niche = _NicheStub()
    niche.prompt_template = "Updated prompt for {{business_name}}: no longer rejects any zip code."
    tenant = _TenantWithIdsStub(niche_template=niche)

    captured = {}

    class _Client(_FakeRetellClient):
        def get(self, url, headers=None):
            return _FakeResponse(
                200,
                "ok",
                json_data={
                    # Stale: old niche wording, but already has rules+tool.
                    "general_prompt": "Some rule.\n\nOLD prompt: we don't cover certain zip codes.",
                    "general_tools": [provisioning.END_CALL_TOOL],
                },
            )

        def post(self, url, headers=None, json=None):
            if url.endswith("/create-retell-llm"):
                captured["new_prompt"] = json["general_prompt"]
                return _FakeResponse(200, "ok", json_data={"llm_id": "llm_refreshed"})
            return _FakeResponse(200, "ok")

        def patch(self, url, headers=None, json=None):
            captured["repoint_llm_id"] = json["response_engine"]["llm_id"]
            return _FakeResponse(200, "ok")

    monkeypatch.setattr(provisioning.httpx, "Client", _Client)

    status = provisioning.sync_existing_agent(tenant, "Some rule.")

    assert status == "updated"
    assert "no longer rejects any zip code" in captured["new_prompt"]
    assert "we don't cover certain zip codes" not in captured["new_prompt"]
    assert captured["new_prompt"].startswith("Some rule.")
    assert captured["repoint_llm_id"] == "llm_refreshed"
    assert tenant.retell_llm_id == "llm_refreshed"


def test_sync_existing_agent_creates_new_llm_and_repoints_agent_when_missing(monkeypatch):
    captured = {}

    class _Client(_FakeRetellClient):
        def get(self, url, headers=None):
            return _FakeResponse(
                200,
                "ok",
                json_data={
                    "general_prompt": "Niche prompt here.",
                    "general_tools": [],
                    "model": "gpt-4.1",
                    "start_speaker": "agent",
                    "begin_message": "Hi there!",
                },
            )

        def post(self, url, headers=None, json=None):
            if url.endswith("/create-retell-llm"):
                captured["create_llm_payload"] = json
                return _FakeResponse(200, "ok", json_data={"llm_id": "llm_new"})
            captured["publish_url"] = url
            return _FakeResponse(200, "ok")

        def patch(self, url, headers=None, json=None):
            captured["repoint_url"] = url
            captured["repoint_payload"] = json
            return _FakeResponse(200, "ok")

    monkeypatch.setattr(provisioning.httpx, "Client", _Client)

    tenant = _TenantWithIdsStub(retell_llm_id="llm_x", retell_agent_id="agent_x")
    status = provisioning.sync_existing_agent(tenant, "Some rule.")

    assert status == "updated"
    assert captured["create_llm_payload"]["general_prompt"].startswith("Some rule.")
    assert "Niche prompt here." in captured["create_llm_payload"]["general_prompt"]
    assert provisioning.END_CALL_TOOL in captured["create_llm_payload"]["general_tools"]
    assert captured["create_llm_payload"]["begin_message"] == "Hi there!"

    assert captured["repoint_url"] == f"{provisioning.RETELL_API_BASE}/update-agent/agent_x"
    assert captured["repoint_payload"]["response_engine"] == {"type": "retell-llm", "llm_id": "llm_new"}

    assert captured["publish_url"] == f"{provisioning.RETELL_API_BASE}/publish-agent/agent_x"

    # The tenant's llm_id is updated in place so the DB record and Retell agree.
    assert tenant.retell_llm_id == "llm_new"


def test_sync_existing_agent_reports_create_llm_failure(monkeypatch):
    class _Client(_FakeRetellClient):
        def get(self, url, headers=None):
            return _FakeResponse(
                200, "ok", json_data={"general_prompt": "Niche prompt here.", "general_tools": []}
            )

        def post(self, url, headers=None, json=None):
            return _FakeResponse(500, "server error")

    monkeypatch.setattr(provisioning.httpx, "Client", _Client)

    with pytest.raises(provisioning.ProvisioningError) as exc_info:
        provisioning.sync_existing_agent(_TenantWithIdsStub(), "Some rule.")

    assert "Failed to create replacement LLM" in str(exc_info.value)


def test_sync_existing_agent_reports_repoint_failure_with_new_llm_id(monkeypatch):
    class _Client(_FakeRetellClient):
        def get(self, url, headers=None):
            return _FakeResponse(
                200, "ok", json_data={"general_prompt": "Niche prompt here.", "general_tools": []}
            )

        def post(self, url, headers=None, json=None):
            return _FakeResponse(200, "ok", json_data={"llm_id": "llm_new"})

        def patch(self, url, headers=None, json=None):
            return _FakeResponse(500, "server error")

    monkeypatch.setattr(provisioning.httpx, "Client", _Client)

    with pytest.raises(provisioning.ProvisioningError) as exc_info:
        provisioning.sync_existing_agent(_TenantWithIdsStub(), "Some rule.")

    message = str(exc_info.value)
    assert "llm_new" in message
    assert "safe to delete if this doesn't get resolved" in message


def test_sync_existing_agent_reports_publish_failure_after_repoint(monkeypatch):
    class _Client(_FakeRetellClient):
        def get(self, url, headers=None):
            return _FakeResponse(
                200, "ok", json_data={"general_prompt": "Niche prompt here.", "general_tools": []}
            )

        def post(self, url, headers=None, json=None):
            if url.endswith("/create-retell-llm"):
                return _FakeResponse(200, "ok", json_data={"llm_id": "llm_new"})
            return _FakeResponse(500, "server error")

        def patch(self, url, headers=None, json=None):
            return _FakeResponse(200, "ok")

    monkeypatch.setattr(provisioning.httpx, "Client", _Client)

    with pytest.raises(provisioning.ProvisioningError) as exc_info:
        provisioning.sync_existing_agent(_TenantWithIdsStub(), "Some rule.")

    assert "saved as a draft, not live yet" in str(exc_info.value)


def test_sync_existing_agent_treats_already_published_as_success(monkeypatch):
    class _Client(_FakeRetellClient):
        def get(self, url, headers=None):
            return _FakeResponse(
                200, "ok", json_data={"general_prompt": "Niche prompt here.", "general_tools": []}
            )

        def post(self, url, headers=None, json=None):
            if url.endswith("/create-retell-llm"):
                return _FakeResponse(200, "ok", json_data={"llm_id": "llm_new"})
            return _FakeResponse(400, '{"status":"error","message":"Agent already published."}')

        def patch(self, url, headers=None, json=None):
            return _FakeResponse(200, "ok")

    monkeypatch.setattr(provisioning.httpx, "Client", _Client)

    tenant = _TenantWithIdsStub()
    status = provisioning.sync_existing_agent(tenant, "Some rule.")
    assert status == "updated"
    assert tenant.retell_llm_id == "llm_new"
