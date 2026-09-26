from app import research
from app.sources import uk, us


def test_affluence_score_ranks_income_and_price():
    places = [
        {"income": 30000, "house_price": 150000},
        {"income": 60000, "house_price": 500000},
        {"income": 45000, "house_price": 300000},
    ]
    research.score_affluence(places)
    assert [p["affluence_score"] for p in places] == [0.0, 100.0, 50.0]


def test_affluence_score_uses_whichever_stat_exists():
    places = [
        {"income": None, "house_price": 100000},
        {"income": None, "house_price": 200000},
        {"income": None, "house_price": None},
    ]
    research.score_affluence(places)
    assert [p["affluence_score"] for p in places] == [0.0, 100.0, None]


def test_uk_enrich_averages_sampled_neighbourhoods(monkeypatch):
    # 5 sample points: two neighbourhoods, one point off the map (no postcode).
    codes = [
        {"msoa21": "A", "msoa11": "A", "admin_ward": "W1"},
        {"msoa21": "A", "msoa11": "A", "admin_ward": "W1"},
        {"msoa21": "B", "msoa11": "B", "admin_ward": "W1"},
        None,
        {"msoa21": "B", "msoa11": "B", "admin_ward": "W2"},
    ]
    monkeypatch.setattr(uk, "_reverse_geocode", lambda pts: codes)
    monkeypatch.setattr(uk, "income_by_msoa", lambda: {"A": 40000, "B": 60000})
    monkeypatch.setattr(uk, "house_price_by_msoa", lambda: {"A": 200000, "B": 400000})
    monkeypatch.setattr(uk, "population_by_ward", lambda: {"W1": 9000})
    monkeypatch.setattr(uk, "population_by_msoa", lambda: {})

    [place] = uk.enrich([{"name": "X", "lat": 53.0, "lng": -2.0, "population": None}])
    assert place["income"] == 50000
    assert place["house_price"] == 300000
    assert place["population"] == 9000
    assert place["population_source"] == "Census 2021 ward"


def test_uk_enrich_keeps_osm_population(monkeypatch):
    monkeypatch.setattr(uk, "_reverse_geocode", lambda pts: [None] * len(pts))
    [place] = uk.enrich([{"name": "X", "lat": 53.0, "lng": -2.0, "population": 12000}])
    assert place["population"] == 12000
    assert place["population_source"] == "OpenStreetMap"
    assert place["income"] is None


def test_uk_parse_population_tag():
    assert uk._parse_int("12,345") == 12345
    assert uk._parse_int("5000;6000") == 5000
    assert uk._parse_int("") is None


def test_find_places_excludes_centre_and_dedupes(monkeypatch):
    centre = (53.4794, -2.2453)
    monkeypatch.setattr(uk, "fetch_places", lambda lat, lng, r: [
        {"name": "Manchester", "place_type": "city", "lat": 53.4795, "lng": -2.2452, "population": 500000},
        {"name": "Altrincham", "place_type": "town", "lat": 53.3876, "lng": -2.3487, "population": 50000},
        {"name": "Altrincham", "place_type": "suburb", "lat": 53.30, "lng": -2.50, "population": None},
        {"name": "Nearby", "place_type": "suburb", "lat": 53.4800, "lng": -2.2460, "population": None},
    ])
    monkeypatch.setattr(uk, "enrich", lambda places: [{**p, "income": 1, "house_price": 1} for p in places])
    places = research.find_places("GB", *centre, 10, exclude_name="Manchester")
    assert [p["name"] for p in places] == ["Altrincham"]
    assert places[0]["sector"] == "SW"
    assert places[0]["place_type"] == "town"


def test_us_clean_name():
    assert us.clean_name("Aspen Hill CDP") == ("Aspen Hill", "CDP")
    assert us.clean_name("Pasadena city") == ("Pasadena", "city")


def test_us_fetch_and_enrich(monkeypatch):
    class FakeResp:
        status_code = 200
        text = "["

        def json(self):
            return [
                ["B01003_001E", "B19013_001E", "B25077_001E", "state", "place"],
                ["25000", "95000", "-666666666", "06", "12345"],
            ]

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params):
            assert params["in"] == "state:06"
            return FakeResp()

    monkeypatch.setattr(us, "_places", lambda: [
        {"state": "CA", "geoid": "0612345", "name": "Testville city", "lat": 34.10, "lng": -118.20},
        {"state": "CA", "geoid": "0699999", "name": "Faraway city", "lat": 36.00, "lng": -118.20},
    ])
    monkeypatch.setattr(us, "client", lambda: FakeClient())
    places = us.fetch_places(34.05, -118.25, 10)
    assert [p["name"] for p in places] == ["Testville, CA"]
    [p] = us.enrich(places)
    assert p["population"] == 25000
    assert p["income"] == 95000
    assert p["house_price"] is None


def test_geocode_us_from_gazetteer(monkeypatch):
    from app.sources import geocode

    monkeypatch.setattr(us, "_places", lambda: [
        {"state": "TX", "geoid": "1", "name": "Austin city", "lat": 30.3, "lng": -97.7},
        {"state": "MN", "geoid": "2", "name": "Austin city", "lat": 43.7, "lng": -92.9},
        {"state": "TX", "geoid": "3", "name": "Austin Colony CDP", "lat": 1, "lng": 1},
    ])
    assert geocode.geocode("austin, mn", "US") == {"name": "Austin, MN", "lat": 43.7, "lng": -92.9}


def test_geocode_uk_prefers_bigger_place_and_qualifier(monkeypatch):
    from app.sources import geocode

    rows = [
        {"name_1": "Hale", "local_type": "Village", "county_unitary": "Hampshire", "latitude": 1, "longitude": 1},
        {"name_1": "Hale", "local_type": "Town", "district_borough": "Trafford", "latitude": 2, "longitude": 2},
    ]

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"result": rows}

    class C:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **k):
            return Resp()

    monkeypatch.setattr(geocode, "client", lambda: C())
    assert geocode.geocode("Hale", "GB")["lat"] == 2
    assert geocode.geocode("Hale, Hampshire", "GB")["lat"] == 1
