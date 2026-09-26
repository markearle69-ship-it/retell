"""Resolve a hub city/town name to a point.

UK: postcodes.io place names (Ordnance Survey Open Names). US: the bundled
Census Gazetteer. Both avoid OpenStreetMap's public Nominatim server, which
rate-limits shared cloud IPs hard; Nominatim is only the last resort.

Add ", <county/borough/state>" to disambiguate, e.g. "Hale, Trafford" or
"Springfield, IL".
"""

from typing import Optional

import httpx

from . import us
from .http import client

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
POSTCODES_PLACES_URL = "https://api.postcodes.io/places"

# Prefer the bigger settlement when several share a name.
_UK_TYPE_RANK = {"City": 0, "Town": 1, "Suburban Area": 2, "Village": 3, "Hamlet": 4}


class GeocodeError(Exception):
    pass


def _split(query: str) -> tuple[str, str]:
    name, _, qualifier = query.partition(",")
    return name.strip(), qualifier.strip()


def _geocode_uk(query: str) -> Optional[dict]:
    name, qualifier = _split(query)
    with client() as c:
        resp = c.get(POSTCODES_PLACES_URL, params={"q": name, "limit": 50})
        resp.raise_for_status()
        results = resp.json().get("result") or []

    matches = [r for r in results if (r.get("name_1") or "").lower() == name.lower()]
    if qualifier:
        q = qualifier.lower()
        matches = [
            r for r in matches
            if any(q in (r.get(k) or "").lower() for k in ("county_unitary", "district_borough", "region"))
        ]
    if not matches:
        return None
    best = min(matches, key=lambda r: _UK_TYPE_RANK.get(r.get("local_type"), 9))
    return {"name": best["name_1"], "lat": best["latitude"], "lng": best["longitude"]}


def _geocode_us(query: str) -> Optional[dict]:
    name, state = _split(query)
    matches = []
    for p in us._places():
        clean, place_type = us.clean_name(p["name"])
        if clean.lower() != name.lower():
            continue
        if state and p["state"].lower() != state.lower():
            continue
        matches.append((place_type != "city", p, clean))
    if not matches:
        return None
    _, best, clean = min(matches, key=lambda m: m[0])
    return {"name": f"{clean}, {best['state']}", "lat": best["lat"], "lng": best["lng"]}


def _geocode_nominatim(query: str, country: str) -> Optional[dict]:
    with client() as c:
        resp = c.get(
            NOMINATIM_URL,
            params={"q": query, "format": "json", "limit": 1, "countrycodes": country.lower()},
        )
        resp.raise_for_status()
        results = resp.json()
    if not results:
        return None
    top = results[0]
    return {
        "name": (top.get("display_name") or query).split(",")[0].strip(),
        "lat": float(top["lat"]),
        "lng": float(top["lon"]),
    }


def geocode(query: str, country: str) -> dict:
    """Return {name, lat, lng} or raise GeocodeError."""
    try:
        found = _geocode_uk(query) if country == "GB" else _geocode_us(query)
    except httpx.HTTPError:
        found = None
    if not found:
        try:
            found = _geocode_nominatim(query, country)
        except httpx.HTTPError:
            found = None
    if not found:
        hint = "county or borough" if country == "GB" else "state, e.g. 'Austin, TX'"
        raise GeocodeError(f"Couldn't find '{query}'. Check the spelling or add the {hint}.")
    return found
