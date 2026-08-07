from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import RankCheck, Tenant
from app.rank_checker import RankCheckResult


def _logged_in_client() -> TestClient:
    client = TestClient(app)
    client.post("/admin/login", data={"password": settings.admin_api_key}, follow_redirects=False)
    return client


def test_rankings_requires_login():
    anon = TestClient(app)
    resp = anon.get("/admin/rankings", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/login"


def test_rankings_page_lists_sites_with_domain_and_latest_check():
    client = _logged_in_client()
    db = SessionLocal()
    try:
        tenant = Tenant(
            business_name="Rankings Test Plumbing",
            to_number="+15557778001",
            domain="rankingstestplumbing.com",
            location="Reno, Nevada",
        )
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        db.add(
            RankCheck(
                tenant_id=tenant.id,
                query="plumber Reno, Nevada",
                position=4,
                matched_url="https://rankingstestplumbing.com/",
                num_results_checked=100,
            )
        )
        db.commit()
        tenant_id = tenant.id
    finally:
        db.close()

    resp = client.get("/admin/rankings")
    assert resp.status_code == 200
    assert "Rankings Test Plumbing" in resp.text
    assert "rankingstestplumbing.com" in resp.text
    assert "#4" in resp.text

    db = SessionLocal()
    try:
        db.query(RankCheck).filter(RankCheck.tenant_id == tenant_id).delete()
        db.query(Tenant).filter(Tenant.id == tenant_id).delete()
        db.commit()
    finally:
        db.close()


def test_check_now_runs_a_check_and_saves_it():
    client = _logged_in_client()
    db = SessionLocal()
    try:
        tenant = Tenant(
            business_name="Check Now Plumbing",
            to_number="+15557778002",
            domain="checknowplumbing.com",
            location="Reno, Nevada",
        )
        db.add(tenant)
        db.commit()
        db.refresh(tenant)
        tenant_id = tenant.id
    finally:
        db.close()

    fake_result = RankCheckResult(query="plumber Reno, Nevada", position=7, matched_url="https://checknowplumbing.com/")
    with patch("app.admin.check_ranking", return_value=fake_result) as mock_check:
        resp = client.post(f"/admin/rankings/{tenant_id}/check", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/admin/rankings"
        mock_check.assert_called_once()

    db = SessionLocal()
    try:
        saved = db.query(RankCheck).filter(RankCheck.tenant_id == tenant_id).first()
        assert saved is not None
        assert saved.position == 7

        db.query(RankCheck).filter(RankCheck.tenant_id == tenant_id).delete()
        db.query(Tenant).filter(Tenant.id == tenant_id).delete()
        db.commit()
    finally:
        db.close()
