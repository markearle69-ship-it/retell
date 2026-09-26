"""Step 1: find and rank the towns/suburbs around an Area's centre."""

from datetime import datetime

from sqlalchemy.orm import Session

from . import models
from .geo import bearing_degrees, distance_miles, sector_for
from .sources import uk, us

# Anything this close to the centre is the centre itself (or part of it).
MIN_DISTANCE_MILES = 1.0

# How much each stat counts towards the affluence score.
INCOME_WEIGHT = 0.6
HOUSE_PRICE_WEIGHT = 0.4


def _source(country: str):
    return uk if country == "GB" else us


def _percentiles(values: list) -> list:
    """Percentile rank (0-100) of each value among the non-missing values."""
    present = sorted(v for v in values if v is not None)
    if not present:
        return [None] * len(values)
    n = len(present)
    out = []
    for v in values:
        if v is None:
            out.append(None)
        elif n == 1:
            out.append(100.0)
        else:
            below = sum(1 for x in present if x < v)
            equal = sum(1 for x in present if x == v)
            out.append(100.0 * (below + (equal - 1) / 2) / (n - 1))
    return out


def score_affluence(places: list[dict]) -> None:
    """Set place['affluence_score']: 0-100, relative to the other places found."""
    income_pct = _percentiles([p.get("income") for p in places])
    price_pct = _percentiles([p.get("house_price") for p in places])
    for place, ip, hp in zip(places, income_pct, price_pct):
        if ip is not None and hp is not None:
            place["affluence_score"] = round(INCOME_WEIGHT * ip + HOUSE_PRICE_WEIGHT * hp, 1)
        elif ip is not None or hp is not None:
            place["affluence_score"] = round(ip if ip is not None else hp, 1)
        else:
            place["affluence_score"] = None


def _dedupe(places: list[dict]) -> list[dict]:
    """OSM sometimes has several nodes with one name; keep the one nearest the centre."""
    best: dict[str, dict] = {}
    for p in places:
        key = p["name"].lower()
        if key not in best or p["distance_miles"] < best[key]["distance_miles"]:
            best[key] = p
    return list(best.values())


def find_places(country: str, lat: float, lng: float, radius_miles: float, exclude_name: str = "") -> list[dict]:
    src = _source(country)
    places = src.fetch_places(lat, lng, radius_miles)
    for p in places:
        p["distance_miles"] = round(distance_miles(lat, lng, p["lat"], p["lng"]), 1)
        p["bearing"] = round(bearing_degrees(lat, lng, p["lat"], p["lng"]), 1)
        p["sector"] = sector_for(p["bearing"])
    centre = exclude_name.split(",")[0].strip().lower()
    places = [
        p for p in _dedupe(places)
        if MIN_DISTANCE_MILES <= p["distance_miles"] <= radius_miles
        and p["name"].split(",")[0].strip().lower() != centre
    ]
    places = src.enrich(places)
    score_affluence(places)
    return places


def research_area(db: Session, area: models.Area) -> None:
    """(Re)build an Area's candidate list, keeping earlier ticks for places still found."""
    previously_selected = {c.name for c in area.candidates if c.selected}
    places = find_places(
        area.project.country, area.lat, area.lng, area.radius_miles, exclude_name=area.name
    )
    area.candidates.clear()
    for p in places:
        if area.min_population and p.get("population") is not None and p["population"] < area.min_population:
            continue
        area.candidates.append(
            models.Candidate(
                name=p["name"],
                place_type=p.get("place_type"),
                lat=p["lat"],
                lng=p["lng"],
                distance_miles=p["distance_miles"],
                bearing=p["bearing"],
                sector=p["sector"],
                population=p.get("population"),
                population_source=p.get("population_source"),
                income=p.get("income"),
                house_price=p.get("house_price"),
                affluence_score=p.get("affluence_score"),
                selected=p["name"] in previously_selected,
            )
        )
    area.researched_at = datetime.utcnow()
    area.research_error = None
    db.commit()


def ranked_by_sector(area: models.Area) -> dict[str, list[models.Candidate]]:
    from .geo import SECTORS

    groups: dict[str, list[models.Candidate]] = {s: [] for s in SECTORS}
    for c in area.candidates:
        groups[c.sector].append(c)
    for s in groups:
        groups[s].sort(key=lambda c: (-(c.affluence_score or -1), -(c.population or 0)))
    return groups


def log_selection(db: Session, area: models.Area) -> None:
    db.add(
        models.SelectionLog(
            area_id=area.id,
            context={
                "niche": area.project.niche,
                "country": area.project.country,
                "level": area.level,
                "centre": area.name,
                "radius_miles": area.radius_miles,
                "min_population": area.min_population,
            },
            candidates=[{**c.features(), "selected": c.selected} for c in area.candidates],
        )
    )
