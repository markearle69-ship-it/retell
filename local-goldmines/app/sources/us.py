"""US places plus affluence data.

- Places: bundled Census Gazetteer (every incorporated place and CDP with its
  centre point), so finding what's inside the radius needs no API call.
- Stats: American Community Survey 5-year estimates via the Census API, one
  call per state touched: total population, median household income, and
  median home value.
"""

import csv
from functools import lru_cache
from pathlib import Path

from ..config import settings
from ..geo import distance_miles
from .http import client

ACS_URL = "https://api.census.gov/data/2023/acs/acs5"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# ACS uses large negative sentinels (e.g. -666666666) for "no estimate".
_ACS_MISSING_BELOW = 0

# Gazetteer names end in the legal type ("Pasadena city", "Aspen Hill CDP").
_NAME_SUFFIXES = (
    " city and borough", " consolidated government", " metropolitan government",
    " unified government", " urban county", " municipality", " CDP", " city", " town",
    " village", " borough", " township", " comunidad", " zona urbana",
)


class CensusError(Exception):
    pass


@lru_cache(maxsize=1)
def _places() -> list[dict]:
    with open(DATA_DIR / "us_places.csv", newline="") as f:
        return [
            {**row, "lat": float(row["lat"]), "lng": float(row["lng"])}
            for row in csv.DictReader(f)
        ]


def clean_name(name: str) -> tuple[str, str]:
    for suffix in _NAME_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)], suffix.strip()
    return name, "place"


def fetch_places(lat: float, lng: float, radius_miles: float) -> list[dict]:
    out = []
    for p in _places():
        if distance_miles(lat, lng, p["lat"], p["lng"]) > radius_miles:
            continue
        name, place_type = clean_name(p["name"])
        out.append(
            {
                "name": f"{name}, {p['state']}",
                "place_type": place_type,
                "lat": p["lat"],
                "lng": p["lng"],
                "geoid": p["geoid"],
            }
        )
    return out


def _acs_value(raw) -> int | None:
    try:
        v = int(float(raw))
    except (TypeError, ValueError):
        return None
    return v if v >= _ACS_MISSING_BELOW else None


def enrich(places: list[dict]) -> list[dict]:
    if not places:
        return places
    if not settings.census_api_key:
        raise CensusError("CENSUS_API_KEY isn't set, so US population and income can't be looked up.")

    states = sorted({p["geoid"][:2] for p in places})
    stats: dict[str, dict] = {}
    with client() as c:
        for state in states:
            resp = c.get(
                ACS_URL,
                params={
                    "get": "B01003_001E,B19013_001E,B25077_001E",
                    "for": "place:*",
                    "in": f"state:{state}",
                    "key": settings.census_api_key,
                },
            )
            if resp.status_code != 200 or not resp.text.startswith("["):
                raise CensusError(f"Census API error ({resp.status_code}). Check CENSUS_API_KEY.")
            rows = resp.json()
            header = rows[0]
            for row in rows[1:]:
                r = dict(zip(header, row))
                stats[r["state"] + r["place"]] = {
                    "population": _acs_value(r["B01003_001E"]),
                    "income": _acs_value(r["B19013_001E"]),
                    "house_price": _acs_value(r["B25077_001E"]),
                }

    for place in places:
        s = stats.get(place["geoid"], {})
        place["population"] = s.get("population")
        place["population_source"] = "ACS 2023" if place["population"] else None
        place["income"] = s.get("income")
        place["house_price"] = s.get("house_price")
    return places
