import hashlib
import hmac
import json
import time

from fastapi.testclient import TestClient

from app import notify
from app.config import settings
from app.main import app

client = TestClient(app)
ADMIN_HEADERS = {"x-admin-key": settings.admin_api_key}


def _sign(body: bytes, timestamp_ms: int) -> str:
    digest = hmac.new(
        settings.retell_api_key.encode("utf-8"),
        body + str(timestamp_ms).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"v={timestamp_ms},d={digest}"


def _post_webhook(payload: dict):
    body = json.dumps(payload).encode("utf-8")
    header = _sign(body, int(time.time() * 1000))
    return client.post(
        "/webhooks/retell",
        content=body,
        headers={"content-type": "application/json", "x-retell-signature": header},
    )


def test_webhook_rejects_bad_signature():
    resp = client.post(
        "/webhooks/retell",
        content=b'{"event": "call_ended", "call": {"call_id": "abc"}}',
        headers={"content-type": "application/json", "x-retell-signature": "v=1,d=bogus"},
    )
    assert resp.status_code == 401


def test_webhook_stores_lead_and_notifies_tenant(monkeypatch):
    tenant_resp = client.post(
        "/tenants",
        json={
            "business_name": "Joe's Plumbing",
            "to_number": "+15550001111",
            "notify_email": "joe@example.com",
            "notify_sms_number": "+15559998888",
        },
        headers=ADMIN_HEADERS,
    )
    assert tenant_resp.status_code == 200, tenant_resp.text
    tenant_id = tenant_resp.json()["id"]

    notified = {}

    def fake_notify_tenant(tenant, lead):
        notified["tenant_id"] = tenant.id
        notified["call_summary"] = lead.call_summary

    monkeypatch.setattr(notify, "notify_tenant", fake_notify_tenant)

    payload = {
        "event": "call_analyzed",
        "call": {
            "call_id": "call_123",
            "from_number": "+15551234567",
            "to_number": "+15550001111",
            "agent_id": "agent_abc",
            "recording_url": "https://example.com/rec.wav",
            "disconnection_reason": "user_hangup",
            "call_analysis": {
                "call_summary": "Caller has a burst pipe, wants a quote today.",
                "user_sentiment": "Positive",
                "call_successful": True,
            },
        },
    }
    resp = _post_webhook(payload)
    assert resp.status_code == 200, resp.text

    leads_resp = client.get("/leads", params={"tenant_id": tenant_id}, headers=ADMIN_HEADERS)
    assert leads_resp.status_code == 200
    leads = leads_resp.json()
    assert len(leads) == 1
    assert leads[0]["call_id"] == "call_123"
    assert leads[0]["from_number"] == "+15551234567"
    assert leads[0]["call_summary"] == "Caller has a burst pipe, wants a quote today."
    assert leads[0]["user_sentiment"] == "Positive"

    assert notified == {
        "tenant_id": tenant_id,
        "call_summary": "Caller has a burst pipe, wants a quote today.",
    }


def test_webhook_without_matching_tenant_still_stores_lead():
    payload = {
        "event": "call_ended",
        "call": {
            "call_id": "call_no_tenant",
            "from_number": "+15551110000",
            "to_number": "+19998887777",
        },
    }
    resp = _post_webhook(payload)
    assert resp.status_code == 200

    resp = client.get("/leads", headers=ADMIN_HEADERS)
    call_ids = [lead["call_id"] for lead in resp.json()]
    assert "call_no_tenant" in call_ids
