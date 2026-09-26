"""UK towns/suburbs plus affluence data.

- Places: OpenStreetMap (Overpass API) city/town/village/suburb points.
- Affluence: each place is sampled at its centre and four points ~0.6 miles
  out. postcodes.io maps each point to its neighbourhood (MSOA) and ward, and
  bundled ONS files give:
    * average household income per MSOA (FYE 2023, England & Wales)
    * median house price paid per MSOA (year to Mar 2023, England & Wales)
    * Census 2021 ward population (fallback when OSM has no population), or
      failing that the summed population of the neighbourhoods sampled
      (an approximation, labelled as such)
  Scotland and Northern Ireland have no MSOA income/price data here, so those
  columns come back empty and ranking falls back to population.
"""

import csv
import statistics
from functools import lru_cache
from pathlib import Path
from typing import Optional

import httpx

from ..geo import offset_point
from .http import client

# Public Overpass servers are busy; fall through to a mirror if one fails.
OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
POSTCODES_URL = "https://api.postcodes.io/postcodes"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

PLACE_TYPES = ("city", "town", "village", "suburb")
SAMPLE_OFFSET_MILES = 0.6
POSTCODES_BATCH = 100  # postcodes.io bulk reverse-geocode limit


@lru_cache(maxsize=None)
def _load(filename: str, key: str, value: str) -> dict[str, int]:
    with open(DATA_DIR / filename, newline="") as f:
        return {row[key]: int(row[value]) for row in csv.DictReader(f)}


def income_by_msoa() -> dict[str, int]:
    return _load("uk_msoa_income.csv", "msoa21", "income")


def house_price_by_msoa() -> dict[str, int]:
    return _load("uk_msoa_house_price.csv", "msoa11", "median_price")


def population_by_ward() -> dict[str, int]:
    return _load("uk_ward_population.csv", "ward", "population")


def population_by_msoa() -> dict[str, int]:
    return _load("uk_msoa_population.csv", "msoa21", "population")


def fetch_places(lat: float, lng: float, radius_miles: float) -> list[dict]:
    radius_m = int(radius_miles * 1609.34)
    pattern = "|".join(PLACE_TYPES)
    query = (
        f'[out:json][timeout:90];node["place"~"^({pattern})$"]["name"]'
        f"(around:{radius_m},{lat},{lng});out body;"
    )
    elements = None
    last_error: Exception | None = None
    with client() as c:
        for url in OVERPASS_URLS:
            try:
                resp = c.post(url, data={"data": query})
                resp.raise_for_status()
                elements = resp.json().get("elements", [])
                break
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
    if elements is None:
        raise httpx.HTTPError(f"All Overpass servers failed: {last_error}")

    places = []
    for el in elements:
        tags = el.get("tags", {})
        places.append(
            {
                "name": tags["name"],
                "place_type": tags.get("place"),
                "lat": el["lat"],
                "lng": el["lon"],
                "population": _parse_int(tags.get("population")),
            }
        )
    return places


def _parse_int(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    digits = "".join(ch for ch in value.split(";")[0] if ch.isdigit())
    return int(digits) if digits else None


def _sample_points(lat: float, lng: float) -> list[tuple[float, float]]:
    d = SAMPLE_OFFSET_MILES
    return [
        (lat, lng),
        offset_point(lat, lng, d, 0),
        offset_point(lat, lng, -d, 0),
        offset_point(lat, lng, 0, d),
        offset_point(lat, lng, 0, -d),
    ]


def _reverse_geocode(points: list[tuple[float, float]]) -> list[Optional[dict]]:
    """Return postcodes.io 'codes' dict (or None) for each point, in order."""
    out: list[Optional[dict]] = []
    with client() as c:
        for i in range(0, len(points), POSTCODES_BATCH):
            batch = points[i : i + POSTCODES_BATCH]
            resp = c.post(
                POSTCODES_URL,
                json={
                    "geolocations": [
                        {"latitude": la, "longitude": lo, "radius": 2000, "limit": 1}
                        for la, lo in batch
                    ]
                },
            )
            resp.raise_for_status()
            for item in resp.json().get("result", []):
                results = item.get("result") or []
                out.append(results[0].get("codes") if results else None)
    return out


def enrich(places: list[dict]) -> list[dict]:
    """Add income, house_price and (if missing) population to each place."""
    if not places:
        return places
    points = [p for place in places for p in _sample_points(place["lat"], place["lng"])]
    codes = _reverse_geocode(points)
    incomes, prices = income_by_msoa(), house_price_by_msoa()
    ward_pops, msoa_pops = population_by_ward(), population_by_msoa()

    per_place = len(points) // len(places)
    for idx, place in enumerate(places):
        raw = codes[idx * per_place : (idx + 1) * per_place]
        place_codes = [c for c in raw if c]

        msoa21s = {c.get("msoa21") for c in place_codes} - {None}
        msoa11s = {c.get("msoa11") for c in place_codes} - {None}
        income_vals = [incomes[m] for m in msoa21s if m in incomes]
        price_vals = [prices[m] for m in msoa11s if m in prices]
        place["income"] = round(statistics.mean(income_vals)) if income_vals else None
        place["house_price"] = round(statistics.median(price_vals)) if price_vals else None

        if place.get("population"):
            place["population_source"] = "OpenStreetMap"
        else:
            centre_ward = raw[0].get("admin_ward") if raw and raw[0] else None
            place["population"] = ward_pops.get(centre_ward) if centre_ward else None
            if place["population"]:
                place["population_source"] = "Census 2021 ward"
            else:
                nearby = [msoa_pops[m] for m in msoa21s if m in msoa_pops]
                place["population"] = sum(nearby) or None
                place["population_source"] = "approx (nearby neighbourhoods)" if nearby else None
    return places
