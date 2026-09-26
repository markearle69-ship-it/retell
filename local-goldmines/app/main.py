import logging
from urllib.parse import quote
from pathlib import Path
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from . import models, research
from .auth import (
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE,
    NotAuthenticated,
    create_session_cookie_value,
    password_ok,
    require_login,
)
from .db import Base, engine, get_db
from .geo import SECTORS
from .sources.geocode import GeocodeError, geocode
from .sources.us import CensusError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Local Goldmines")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

HUB_RADIUS_MILES = 10.0
DRILL_RADIUS_MILES = 5.0
COUNTRIES = {"GB": "United Kingdom", "US": "United States"}
auth = [Depends(require_login)]


@app.exception_handler(NotAuthenticated)
async def not_authenticated_handler(request: Request, exc: NotAuthenticated) -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=303)


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login")
def login_submit(request: Request, password: str = Form(...)):
    if not password_ok(password):
        return templates.TemplateResponse(
            request, "login.html", {"error": "Incorrect password"}, status_code=401
        )
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        create_session_cookie_value(),
        httponly=True,
        samesite="lax",
        max_age=SESSION_MAX_AGE,
    )
    return response


@app.post("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


def _get_project(db: Session, project_id: int) -> models.Project:
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _get_area(db: Session, area_id: int) -> models.Area:
    area = db.get(models.Area, area_id)
    if not area:
        raise HTTPException(status_code=404, detail="Area not found")
    return area


def _run_research(db: Session, area: models.Area) -> None:
    """Research an area, storing a readable error on it instead of crashing the page."""
    try:
        research.research_area(db, area)
    except (CensusError, httpx.HTTPError) as exc:
        db.rollback()
        logger.exception("Research failed for area %s", area.id)
        area.research_error = (
            str(exc) if isinstance(exc, CensusError)
            else "A data source didn't respond. Wait a minute and press Re-run."
        )
        db.commit()


@app.get("/", response_class=HTMLResponse, dependencies=auth)
def projects_page(request: Request, db: Session = Depends(get_db), error: Optional[str] = None):
    projects = db.query(models.Project).order_by(models.Project.created_at.desc()).all()
    return templates.TemplateResponse(
        request,
        "projects.html",
        {"projects": projects, "countries": COUNTRIES, "error": error, "hub_radius": HUB_RADIUS_MILES},
    )


@app.post("/projects", dependencies=auth)
def create_project(
    request: Request,
    niche: str = Form(...),
    city: str = Form(...),
    country: str = Form(...),
    radius_miles: float = Form(HUB_RADIUS_MILES),
    min_population: int = Form(0),
    db: Session = Depends(get_db),
):
    if country not in COUNTRIES:
        raise HTTPException(status_code=400, detail="Unsupported country")
    try:
        centre = geocode(city, country)
    except GeocodeError as exc:
        return RedirectResponse(url=f"/?error={quote(str(exc))}", status_code=303)
    except httpx.HTTPError:
        return RedirectResponse(
            url="/?error=" + quote("The map lookup service didn't respond. Try again."), status_code=303
        )

    project = models.Project(
        name=f"{niche.strip().title()} – {centre['name']}", niche=niche.strip(), country=country
    )
    hub = models.Area(
        level=0,
        name=centre["name"],
        lat=centre["lat"],
        lng=centre["lng"],
        radius_miles=radius_miles,
        min_population=max(min_population, 0),
    )
    project.areas.append(hub)
    db.add(project)
    db.commit()
    _run_research(db, hub)
    return RedirectResponse(url=f"/areas/{hub.id}", status_code=303)


@app.post("/projects/{project_id}/delete", dependencies=auth)
def delete_project(project_id: int, db: Session = Depends(get_db)):
    project = _get_project(db, project_id)
    db.query(models.SelectionLog).filter(
        models.SelectionLog.area_id.in_([a.id for a in project.areas])
    ).delete(synchronize_session=False)
    db.delete(project)
    db.commit()
    return RedirectResponse(url="/", status_code=303)


def _area_payload(area: models.Area) -> dict:
    """What the map needs, as plain JSON."""
    return {
        "centre": {"name": area.name, "lat": area.lat, "lng": area.lng},
        "radius_miles": area.radius_miles,
        "candidates": [
            {
                "id": c.id,
                "name": c.name,
                "lat": c.lat,
                "lng": c.lng,
                "sector": c.sector,
                "score": c.affluence_score,
                "selected": c.selected,
            }
            for c in area.candidates
        ],
    }


@app.get("/areas/{area_id}", response_class=HTMLResponse, dependencies=auth)
def area_page(request: Request, area_id: int, db: Session = Depends(get_db), saved: bool = False):
    area = _get_area(db, area_id)
    drilled = {child.name: child for child in area.children}
    groups = research.ranked_by_sector(area)
    return templates.TemplateResponse(
        request,
        "area.html",
        {
            "area": area,
            "project": area.project,
            "groups": groups,
            "sectors": SECTORS,
            "drilled": drilled,
            "saved": saved,
            "payload": _area_payload(area),
            "currency": "£" if area.project.country == "GB" else "$",
            "drill_radius": DRILL_RADIUS_MILES,
        },
    )


@app.post("/areas/{area_id}/research", dependencies=auth)
def rerun_research(
    area_id: int,
    radius_miles: float = Form(...),
    min_population: int = Form(0),
    db: Session = Depends(get_db),
):
    area = _get_area(db, area_id)
    area.radius_miles = radius_miles
    area.min_population = max(min_population, 0)
    db.commit()
    _run_research(db, area)
    return RedirectResponse(url=f"/areas/{area.id}", status_code=303)


@app.post("/areas/{area_id}/selection", dependencies=auth)
async def save_selection(request: Request, area_id: int, db: Session = Depends(get_db)):
    area = _get_area(db, area_id)
    form = await request.form()
    chosen = {int(v) for v in form.getlist("candidate_ids")}
    for c in area.candidates:
        c.selected = c.id in chosen
    research.log_selection(db, area)
    db.commit()
    return RedirectResponse(url=f"/areas/{area.id}?saved=1", status_code=303)


@app.post("/candidates/{candidate_id}/drill", dependencies=auth)
async def drill_down(request: Request, candidate_id: int, db: Session = Depends(get_db)):
    candidate = db.get(models.Candidate, candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Place not found")
    parent = candidate.area

    # The drill button lives inside the picks form, so save the ticks on screen
    # too (plus this town - drilling into it means you're building it) rather
    # than silently dropping them.
    form = await request.form()
    chosen = {int(v) for v in form.getlist("candidate_ids")} | {candidate.id}
    for c in parent.candidates:
        c.selected = c.id in chosen
    research.log_selection(db, parent)
    db.commit()
    existing = next((a for a in parent.children if a.name == candidate.name), None)
    if existing:
        return RedirectResponse(url=f"/areas/{existing.id}", status_code=303)
    child = models.Area(
        project_id=parent.project_id,
        parent_id=parent.id,
        level=parent.level + 1,
        name=candidate.name,
        lat=candidate.lat,
        lng=candidate.lng,
        radius_miles=DRILL_RADIUS_MILES,
        min_population=0,
    )
    db.add(child)
    db.commit()
    _run_research(db, child)
    return RedirectResponse(url=f"/areas/{child.id}", status_code=303)


@app.get("/projects/{project_id}/coverage", response_class=HTMLResponse, dependencies=auth)
def coverage_page(request: Request, project_id: int, db: Session = Depends(get_db)):
    """Prospecting view: every site (hub + chosen towns) and the ground each one covers."""
    project = _get_project(db, project_id)
    hub = project.hub
    sites = []
    if hub:
        sites.append({"name": hub.name, "lat": hub.lat, "lng": hub.lng,
                      "radius": hub.radius_miles, "kind": "hub", "suburbs": [c.name for c in hub.selected]})
        drilled = {a.name: a for a in hub.children}
        for town in hub.selected:
            child = drilled.get(town.name)
            sites.append({
                "name": town.name, "lat": town.lat, "lng": town.lng,
                "radius": child.radius_miles if child else DRILL_RADIUS_MILES,
                "kind": "town",
                "suburbs": [c.name for c in child.selected] if child else [],
            })
    total_pages_locations = sum(1 + len(s["suburbs"]) for s in sites)
    reach = (hub.radius_miles + DRILL_RADIUS_MILES) if hub else 0
    return templates.TemplateResponse(
        request,
        "coverage.html",
        {"project": project, "sites": sites, "reach": reach, "locations": total_pages_locations},
    )
