from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import Lead, Portfolio, Tenant
from app.passwords import hash_password


def _make_portfolio(name: str, slug: str, password: str) -> int:
    db = SessionLocal()
    portfolio = Portfolio(name=name, slug=slug, password_hash=hash_password(password))
    db.add(portfolio)
    db.commit()
    db.refresh(portfolio)
    pid = portfolio.id
    db.close()
    return pid


def _make_tenant(business_name: str, to_number: str, portfolio_id: int | None = None) -> int:
    db = SessionLocal()
    tenant = Tenant(business_name=business_name, to_number=to_number, portfolio_id=portfolio_id)
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    tid = tenant.id
    db.close()
    return tid


def _make_lead(call_id: str, tenant_id: int | None, summary: str = "A summary") -> int:
    db = SessionLocal()
    lead = Lead(call_id=call_id, tenant_id=tenant_id, from_number="+15550001111", call_summary=summary)
    db.add(lead)
    db.commit()
    db.refresh(lead)
    lid = lead.id
    db.close()
    return lid


def test_portal_dashboard_requires_login():
    _make_portfolio("Austin Portfolio", "austin-test-1", "hunter2")
    anon = TestClient(app)
    resp = anon.get("/portal/austin-test-1", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/portal/austin-test-1/login"


def test_portal_login_wrong_password():
    _make_portfolio("Austin Portfolio", "austin-test-2", "hunter2")
    anon = TestClient(app)
    resp = anon.post("/portal/austin-test-2/login", data={"password": "wrong"})
    assert resp.status_code == 401
    assert "Incorrect password" in resp.text


def test_portal_login_unknown_slug_404s():
    anon = TestClient(app)
    resp = anon.get("/portal/does-not-exist/login")
    assert resp.status_code == 404


def test_portal_dashboard_shows_only_assigned_sites_and_leads():
    pid = _make_portfolio("Austin Portfolio", "austin-test-3", "hunter2")
    other_pid = _make_portfolio("Dallas Portfolio", "dallas-test-3", "hunter3")

    in_portfolio = _make_tenant("Austin AC Repair", "+15557770001", portfolio_id=pid)
    other_portfolio = _make_tenant("Dallas AC Repair", "+15557770002", portfolio_id=other_pid)
    unassigned = _make_tenant("Unassigned Site", "+15557770003", portfolio_id=None)

    _make_lead("portal_test_call_1", in_portfolio, summary="Leaky AC in Austin")
    _make_lead("portal_test_call_2", other_portfolio, summary="Should not be visible - Dallas")
    _make_lead("portal_test_call_3", unassigned, summary="Should not be visible - unassigned")

    client = TestClient(app)
    client.post("/portal/austin-test-3/login", data={"password": "hunter2"}, follow_redirects=False)

    resp = client.get("/portal/austin-test-3")
    assert resp.status_code == 200
    assert "Austin AC Repair" in resp.text
    assert "Leaky AC in Austin" in resp.text
    assert "Dallas AC Repair" not in resp.text
    assert "Should not be visible - Dallas" not in resp.text
    assert "Unassigned Site" not in resp.text
    assert "Should not be visible - unassigned" not in resp.text


def test_portal_session_does_not_grant_access_to_a_different_portfolio():
    _make_portfolio("Austin Portfolio", "austin-test-4", "hunter2")
    _make_portfolio("Dallas Portfolio", "dallas-test-4", "hunter3")

    client = TestClient(app)
    client.post("/portal/austin-test-4/login", data={"password": "hunter2"}, follow_redirects=False)

    # A valid session for austin-test-4 must not unlock dallas-test-4.
    resp = client.get("/portal/dallas-test-4", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/portal/dallas-test-4/login"


def test_portal_lead_detail_blocks_leads_outside_the_portfolio():
    pid = _make_portfolio("Austin Portfolio", "austin-test-5", "hunter2")
    other_pid = _make_portfolio("Dallas Portfolio", "dallas-test-5", "hunter3")

    in_portfolio = _make_tenant("Austin Site 5", "+15557770005", portfolio_id=pid)
    other_portfolio = _make_tenant("Dallas Site 5", "+15557770006", portfolio_id=other_pid)

    own_lead_id = _make_lead("portal_test_call_5a", in_portfolio, summary="Own lead")
    foreign_lead_id = _make_lead("portal_test_call_5b", other_portfolio, summary="Foreign lead")

    client = TestClient(app)
    client.post("/portal/austin-test-5/login", data={"password": "hunter2"}, follow_redirects=False)

    resp = client.get(f"/portal/austin-test-5/leads/{own_lead_id}")
    assert resp.status_code == 200
    assert "Own lead" in resp.text

    resp = client.get(f"/portal/austin-test-5/leads/{foreign_lead_id}")
    assert resp.status_code == 404


def test_portal_dashboard_has_no_admin_controls():
    pid = _make_portfolio("Austin Portfolio", "austin-test-6", "hunter2")
    _make_tenant("Austin Site 6", "+15557770007", portfolio_id=pid)

    client = TestClient(app)
    client.post("/portal/austin-test-6/login", data={"password": "hunter2"}, follow_redirects=False)
    resp = client.get("/portal/austin-test-6")

    assert "Niche templates" not in resp.text
    assert "Add a site" not in resp.text
    assert "action=\"/admin" not in resp.text
