import json
import time
from datetime import datetime

import httpx
import pytest

from app import models, social_provisioning
from app.config import settings
from app.db import SessionLocal
from app.provisioning import ProvisioningError


class _FakeResponse:
    def __init__(self, status_code: int, json_data=None, text: str = ""):
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}
        self.text = text or json.dumps(self._json_data)

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://example.test")
            raise httpx.HTTPStatusError("error", request=request, response=self)

    def json(self):
        return self._json_data


class _NicheStub:
    id = 1
    name = "Test Niche"
    service_description = "AC repair"
    social_content_pillars = "tip one\ntip two"
    social_caption_style = "friendly"
    social_platforms = None
    social_template_uid = None


class _TenantStub:
    id = 1
    business_name = "Test Business"
    location = "Testville"
    logo_url = None
    ayrshare_profile_key = None


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "test-anthropic-key")
    monkeypatch.setattr(settings, "ayrshare_api_key", "test-ayrshare-key")
    monkeypatch.setattr(settings, "bannerbear_api_key", "")
    monkeypatch.setattr(settings, "social_default_platforms", "facebook,instagram")
    monkeypatch.setattr(settings, "social_posts_per_batch", 3)
    monkeypatch.setattr(settings, "social_post_interval_hours", 48)
    monkeypatch.setattr(social_provisioning.time, "sleep", lambda *_: None)


# --- generate_caption_batch ---


def _anthropic_client(text):
    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, headers=None, json=None):
            return _FakeResponse(200, {"content": [{"type": "text", "text": text}]})

    return _Client


def test_generate_caption_batch_parses_json_array(monkeypatch):
    payload = json.dumps([{"pillar": "tip one", "caption": "Caption A"}, {"pillar": "tip two", "caption": "Caption B"}])
    monkeypatch.setattr(social_provisioning.httpx, "Client", lambda *a, **kw: _anthropic_client(payload)())

    captions = social_provisioning.generate_caption_batch(_NicheStub(), _TenantStub(), 2)

    assert captions == [{"pillar": "tip one", "caption": "Caption A"}, {"pillar": "tip two", "caption": "Caption B"}]


def test_generate_caption_batch_rejects_non_json_output(monkeypatch):
    monkeypatch.setattr(social_provisioning.httpx, "Client", lambda *a, **kw: _anthropic_client("not json")())

    with pytest.raises(ProvisioningError) as exc_info:
        social_provisioning.generate_caption_batch(_NicheStub(), _TenantStub(), 2)

    assert "wasn't valid JSON" in str(exc_info.value)


def test_generate_caption_batch_requires_api_key(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    with pytest.raises(ProvisioningError) as exc_info:
        social_provisioning.generate_caption_batch(_NicheStub(), _TenantStub(), 2)
    assert "ANTHROPIC_API_KEY" in str(exc_info.value)


def test_generate_caption_batch_zero_count_short_circuits():
    assert social_provisioning.generate_caption_batch(_NicheStub(), _TenantStub(), 0) == []


# --- generate_creative ---


def test_generate_creative_skips_when_bannerbear_not_configured():
    assert social_provisioning.generate_creative(_TenantStub(), _NicheStub(), "caption") is None


def test_generate_creative_skips_when_no_template_uid(monkeypatch):
    monkeypatch.setattr(settings, "bannerbear_api_key", "bb-key")
    monkeypatch.setattr(settings, "bannerbear_template_uid", "")
    assert social_provisioning.generate_creative(_TenantStub(), _NicheStub(), "caption") is None


def test_generate_creative_polls_until_completed(monkeypatch):
    monkeypatch.setattr(settings, "bannerbear_api_key", "bb-key")
    monkeypatch.setattr(settings, "bannerbear_template_uid", "tmpl_1")

    calls = {"get": 0}

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, headers=None, json=None):
            return _FakeResponse(200, {"uid": "img_1", "status": "pending", "image_url": None})

        def get(self, url, headers=None):
            calls["get"] += 1
            if calls["get"] < 2:
                return _FakeResponse(200, {"uid": "img_1", "status": "pending", "image_url": None})
            return _FakeResponse(200, {"uid": "img_1", "status": "completed", "image_url": "https://img/1.png"})

    monkeypatch.setattr(social_provisioning.httpx, "Client", lambda *a, **kw: _Client())

    url = social_provisioning.generate_creative(_TenantStub(), _NicheStub(), "caption")
    assert url == "https://img/1.png"
    assert calls["get"] == 2


def test_generate_creative_raises_on_render_failure(monkeypatch):
    monkeypatch.setattr(settings, "bannerbear_api_key", "bb-key")
    monkeypatch.setattr(settings, "bannerbear_template_uid", "tmpl_1")

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, headers=None, json=None):
            return _FakeResponse(200, {"uid": "img_1", "status": "pending", "image_url": None})

        def get(self, url, headers=None):
            return _FakeResponse(200, {"uid": "img_1", "status": "failed", "image_url": None})

    monkeypatch.setattr(social_provisioning.httpx, "Client", lambda *a, **kw: _Client())

    with pytest.raises(ProvisioningError) as exc_info:
        social_provisioning.generate_creative(_TenantStub(), _NicheStub(), "caption")
    assert "failed to render" in str(exc_info.value)


# --- create_ayrshare_profile ---


def test_create_ayrshare_profile_success(monkeypatch):
    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, headers=None, json=None):
            assert headers["Authorization"] == "Bearer test-ayrshare-key"
            return _FakeResponse(200, {"profileKey": "pk_123", "refId": "ref_123"})

    monkeypatch.setattr(social_provisioning.httpx, "Client", lambda *a, **kw: _Client())

    profile_key, ref_id = social_provisioning.create_ayrshare_profile(_TenantStub())
    assert profile_key == "pk_123"
    assert ref_id == "ref_123"


def test_create_ayrshare_profile_requires_api_key(monkeypatch):
    monkeypatch.setattr(settings, "ayrshare_api_key", "")
    with pytest.raises(ProvisioningError) as exc_info:
        social_provisioning.create_ayrshare_profile(_TenantStub())
    assert "AYRSHARE_API_KEY" in str(exc_info.value)


def test_create_ayrshare_profile_missing_profile_key_in_response(monkeypatch):
    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, headers=None, json=None):
            return _FakeResponse(200, {"status": "success"})

    monkeypatch.setattr(social_provisioning.httpx, "Client", lambda *a, **kw: _Client())

    with pytest.raises(ProvisioningError) as exc_info:
        social_provisioning.create_ayrshare_profile(_TenantStub())
    assert "didn't return a profileKey" in str(exc_info.value)


# --- schedule_post_on_ayrshare ---


class _PostStub:
    caption = "Hello world"
    platforms = "facebook,instagram"
    scheduled_for = datetime(2026, 1, 1, 12, 0, 0)
    image_url = None


def test_schedule_post_on_ayrshare_success(monkeypatch):
    class _TenantWithProfile(_TenantStub):
        ayrshare_profile_key = "pk_123"

    captured = {}

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, headers=None, json=None):
            captured["headers"] = headers
            captured["json"] = json
            return _FakeResponse(200, {"id": "post_123"})

    monkeypatch.setattr(social_provisioning.httpx, "Client", lambda *a, **kw: _Client())

    post_id = social_provisioning.schedule_post_on_ayrshare(_TenantWithProfile(), _PostStub())
    assert post_id == "post_123"
    assert captured["headers"]["Profile-Key"] == "pk_123"
    assert captured["json"]["platforms"] == ["facebook", "instagram"]


def test_schedule_post_on_ayrshare_requires_profile():
    with pytest.raises(ProvisioningError) as exc_info:
        social_provisioning.schedule_post_on_ayrshare(_TenantStub(), _PostStub())
    assert "no Ayrshare profile" in str(exc_info.value)


# --- ensure_content_queue / process_post_queue / provision_social (DB-backed) ---


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _make_niche(db, **overrides):
    kwargs = dict(
        name=f"Social Niche {time.time()}",
        prompt_template="x",
        voice_id="voice-1",
        social_content_pillars="tip one\ntip two",
    )
    kwargs.update(overrides)
    niche = models.NicheTemplate(**kwargs)
    db.add(niche)
    db.commit()
    db.refresh(niche)
    return niche


def _make_tenant(db, **overrides):
    kwargs = dict(
        business_name="Social Test Site",
        to_number=f"+1555{int(time.time() * 1000) % 10_000_000:07d}",
    )
    kwargs.update(overrides)
    tenant = models.Tenant(**kwargs)
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return tenant


def test_ensure_content_queue_tops_up_to_batch_size(db, monkeypatch):
    niche = _make_niche(db)
    tenant = _make_tenant(db)

    monkeypatch.setattr(
        social_provisioning,
        "generate_caption_batch",
        lambda n, t, count: [{"pillar": "tip one", "caption": f"Caption {i}"} for i in range(count)],
    )

    social_provisioning.ensure_content_queue(db, tenant, niche)

    posts = db.query(models.SocialPost).filter(models.SocialPost.tenant_id == tenant.id).order_by(
        models.SocialPost.scheduled_for
    ).all()
    assert len(posts) == settings.social_posts_per_batch
    assert all(p.status == "draft" for p in posts)
    # Spaced apart by the configured interval.
    gap = posts[1].scheduled_for - posts[0].scheduled_for
    assert gap.total_seconds() == settings.social_post_interval_hours * 3600

    # Already full - resubmitting is a no-op.
    social_provisioning.ensure_content_queue(db, tenant, niche)
    posts_after = db.query(models.SocialPost).filter(models.SocialPost.tenant_id == tenant.id).all()
    assert len(posts_after) == settings.social_posts_per_batch


def test_ensure_content_queue_requires_platforms(db, monkeypatch):
    niche = _make_niche(db, social_platforms=None)
    tenant = _make_tenant(db)
    monkeypatch.setattr(settings, "social_default_platforms", "")

    with pytest.raises(ProvisioningError) as exc_info:
        social_provisioning.ensure_content_queue(db, tenant, niche)
    assert "No social platforms configured" in str(exc_info.value)


def test_process_post_queue_schedules_drafts_and_marks_failures(db, monkeypatch):
    niche = _make_niche(db)
    tenant = _make_tenant(db, ayrshare_profile_key="pk_123")

    ok_post = models.SocialPost(
        tenant_id=tenant.id, niche_template_id=niche.id, caption="Good post",
        platforms="facebook", scheduled_for=datetime.utcnow(), status="draft",
    )
    bad_post = models.SocialPost(
        tenant_id=tenant.id, niche_template_id=niche.id, caption="Bad post",
        platforms="facebook", scheduled_for=datetime.utcnow(), status="draft",
    )
    db.add_all([ok_post, bad_post])
    db.commit()

    monkeypatch.setattr(social_provisioning, "generate_creative", lambda t, n, c: None)

    def fake_schedule(t, post):
        if post.caption == "Bad post":
            raise ProvisioningError("ayrshare boom")
        return "ayrshare_post_1"

    monkeypatch.setattr(social_provisioning, "schedule_post_on_ayrshare", fake_schedule)

    errors = social_provisioning.process_post_queue(db, tenant, niche)

    db.refresh(ok_post)
    db.refresh(bad_post)
    assert ok_post.status == "scheduled"
    assert ok_post.ayrshare_post_id == "ayrshare_post_1"
    assert bad_post.status == "failed"
    assert "ayrshare boom" in bad_post.error
    assert len(errors) == 1


def test_provision_social_runs_all_steps_and_is_idempotent(db, monkeypatch):
    niche = _make_niche(db)
    tenant = _make_tenant(db)

    calls = {"profile": 0, "queue": 0, "process": 0}

    def fake_create_profile(t):
        calls["profile"] += 1
        return "pk_new", "ref_new"

    def fake_ensure_queue(d, t, n):
        calls["queue"] += 1

    def fake_process(d, t, n):
        calls["process"] += 1
        return []

    monkeypatch.setattr(social_provisioning, "create_ayrshare_profile", fake_create_profile)
    monkeypatch.setattr(social_provisioning, "ensure_content_queue", fake_ensure_queue)
    monkeypatch.setattr(social_provisioning, "process_post_queue", fake_process)

    social_provisioning.provision_social(db, tenant, niche)

    assert tenant.ayrshare_profile_key == "pk_new"
    assert tenant.ayrshare_ref_id == "ref_new"
    assert tenant.social_provisioned_at is not None
    assert tenant.social_provisioning_error is None
    assert calls == {"profile": 1, "queue": 1, "process": 1}

    # Resubmitting skips profile creation (already has a key) but still tops up/processes.
    social_provisioning.provision_social(db, tenant, niche)
    assert calls == {"profile": 1, "queue": 2, "process": 2}


def test_provision_social_raises_with_post_errors_but_keeps_profile(db, monkeypatch):
    niche = _make_niche(db)
    tenant = _make_tenant(db)

    monkeypatch.setattr(social_provisioning, "create_ayrshare_profile", lambda t: ("pk_x", "ref_x"))
    monkeypatch.setattr(social_provisioning, "ensure_content_queue", lambda d, t, n: None)
    monkeypatch.setattr(
        social_provisioning, "process_post_queue", lambda d, t, n: ["'Bad caption': boom"]
    )

    with pytest.raises(ProvisioningError) as exc_info:
        social_provisioning.provision_social(db, tenant, niche)

    assert "1 post(s) failed" in str(exc_info.value)
    assert tenant.ayrshare_profile_key == "pk_x"  # kept despite the failure
    assert tenant.social_provisioning_error is not None
