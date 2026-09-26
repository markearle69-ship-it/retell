import pytest
from fastapi.testclient import TestClient

from app import main, models, research
from app.db import Base, SessionLocal, engine


FAKE_PLACES = [
    {"name": "Altrincham", "place_type": "town", "lat": 53.3876, "lng": -2.3487, "population": 50000,
     "population_source": "OpenStreetMap", "income": 61000, "house_price": 455000,
     "distance_miles": 7.8, "bearing": 216.0, "sector": "SW", "affluence_score": 100.0},
    {"name": "Moss Side", "place_type": "suburb", "lat": 53.458, "lng": -2.245, "population": 21000,
     "population_source": "Census 2021 ward", "income": 40000, "house_price": 195000,
     "distance_miles": 1.5, "bearing": 180.0, "sector": "S", "affluence_score": 0.0},
    {"name": "Tiny", "place_type": "village", "lat": 53.6, "lng": -2.2, "population": 300,
     "population_source": "OpenStreetMap", "income": None, "house_price": None,
     "distance_miles": 8.5, "bearing": 10.0, "sector": "N", "affluence_score": None},
]


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(main, "geocode", lambda q, c: {"name": q.title(), "lat": 53.4794, "lng": -2.2453})
    monkeypatch.setattr(research, "find_places", lambda *a, **k: [dict(p) for p in FAKE_PLACES])
    yield


@pytest.fixture
def client():
    c = TestClient(main.app)
    r = c.post("/login", data={"password": "test-password"}, follow_redirects=False)
    assert r.status_code == 303
    return c


def test_requires_login():
    r = TestClient(main.app).get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_wrong_password_rejected():
    r = TestClient(main.app).post("/login", data={"password": "nope"}, follow_redirects=False)
    assert r.status_code == 401


def test_create_project_researches_hub(client):
    r = client.post("/projects", data={"niche": "electrician", "city": "manchester", "country": "GB",
                                       "radius_miles": 10, "min_population": 1000}, follow_redirects=False)
    assert r.status_code == 303
    page = client.get(r.headers["location"])
    assert page.status_code == 200
    assert "Altrincham" in page.text and "Moss Side" in page.text
    assert "Tiny" not in page.text  # under min population

    db = SessionLocal()
    area = db.query(models.Area).one()
    assert area.level == 0 and area.radius_miles == 10
    assert len(area.candidates) == 2
    db.close()


def test_save_selection_logs_training_data(client):
    r = client.post("/projects", data={"niche": "electrician", "city": "manchester", "country": "GB"},
                    follow_redirects=False)
    area_url = r.headers["location"]
    db = SessionLocal()
    alt = db.query(models.Candidate).filter_by(name="Altrincham").one()
    db.close()

    client.post(f"{area_url}/selection", data={"candidate_ids": [str(alt.id)]}, follow_redirects=False)

    db = SessionLocal()
    picks = {c.name: c.selected for c in db.query(models.Candidate)}
    assert picks["Altrincham"] is True and picks["Moss Side"] is False
    log = db.query(models.SelectionLog).one()
    assert log.context["niche"] == "electrician"
    assert {c["name"]: c["selected"] for c in log.candidates}["Altrincham"] is True
    assert "income" in log.candidates[0]
    db.close()


def test_rerun_keeps_existing_picks(client):
    r = client.post("/projects", data={"niche": "electrician", "city": "manchester", "country": "GB"},
                    follow_redirects=False)
    area_url = r.headers["location"]
    db = SessionLocal()
    alt_id = db.query(models.Candidate).filter_by(name="Altrincham").one().id
    db.close()
    client.post(f"{area_url}/selection", data={"candidate_ids": [str(alt_id)]})
    client.post(f"{area_url}/research", data={"radius_miles": 12, "min_population": 0})

    db = SessionLocal()
    assert db.query(models.Candidate).filter_by(name="Altrincham").one().selected is True
    assert db.query(models.Candidate).count() == 3
    db.close()


def test_drill_down_creates_5_mile_child_and_selects_town(client):
    r = client.post("/projects", data={"niche": "electrician", "city": "manchester", "country": "GB"},
                    follow_redirects=False)
    db = SessionLocal()
    alt = db.query(models.Candidate).filter_by(name="Altrincham").one()
    moss = db.query(models.Candidate).filter_by(name="Moss Side").one()
    db.close()

    # Moss Side ticked on screen but not saved yet - drilling must keep it.
    r = client.post(f"/candidates/{alt.id}/drill", data={"candidate_ids": [str(moss.id)]}, follow_redirects=False)
    assert r.status_code == 303

    db = SessionLocal()
    child = db.query(models.Area).filter_by(level=1).one()
    assert child.name == "Altrincham" and child.radius_miles == 5
    hub = db.query(models.Area).filter_by(level=0).one()
    assert {c.name for c in hub.selected} == {"Altrincham", "Moss Side"}
    db.close()

    # Drilling again goes to the same child instead of duplicating it.
    r2 = client.post(f"/candidates/{alt.id}/drill", follow_redirects=False)
    assert r2.headers["location"] == r.headers["location"]

    cov = client.get(f"/projects/{child.project_id}/coverage")
    assert cov.status_code == 200 and "Altrincham" in cov.text


def test_geocode_failure_shows_error(client, monkeypatch):
    from app.sources.geocode import GeocodeError

    def boom(q, c):
        raise GeocodeError("Couldn't find 'Nowhere' in GB.")

    monkeypatch.setattr(main, "geocode", boom)
    r = client.post("/projects", data={"niche": "x", "city": "Nowhere", "country": "GB"})
    assert "find &#39;Nowhere&#39;" in r.text or "find 'Nowhere'" in r.text


def test_delete_project(client):
    r = client.post("/projects", data={"niche": "electrician", "city": "manchester", "country": "GB"},
                    follow_redirects=False)
    client.post(r.headers["location"] + "/selection", data={})
    db = SessionLocal()
    pid = db.query(models.Project).one().id
    db.close()
    client.post(f"/projects/{pid}/delete")
    db = SessionLocal()
    assert db.query(models.Project).count() == 0
    assert db.query(models.Candidate).count() == 0
    assert db.query(models.SelectionLog).count() == 0
    db.close()
