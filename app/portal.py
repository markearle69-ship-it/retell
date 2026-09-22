"""Read-only, password-protected view scoped to one Portfolio (a bundle of
sites rented to one local business owner) - completely separate from the
admin session/login, and exposes none of the admin controls (niches,
global rules, sync, add/edit/delete sites)."""

import os
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import models
from .db import get_db
from .passwords import verify_password
from .session import (
    PORTFOLIO_SESSION_COOKIE_NAME,
    PORTFOLIO_SESSION_MAX_AGE,
    create_portfolio_session_cookie_value,
    verify_portfolio_session_cookie,
)

router = APIRouter(prefix="/portal", tags=["portal"])

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=_TEMPLATES_DIR)


class PortfolioNotAuthenticated(Exception):
    """Raised by require_portfolio_session; an app-level handler redirects to that portfolio's login."""

    def __init__(self, slug: str):
        self.slug = slug


def _get_portfolio_or_404(slug: str, db: Session) -> models.Portfolio:
    portfolio = db.query(models.Portfolio).filter(models.Portfolio.slug == slug).first()
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Not found")
    return portfolio


def require_portfolio_session(slug: str, request: Request, db: Session = Depends(get_db)) -> models.Portfolio:
    portfolio = _get_portfolio_or_404(slug, db)
    token = request.cookies.get(PORTFOLIO_SESSION_COOKIE_NAME)
    session_portfolio_id = verify_portfolio_session_cookie(token) if token else None
    # Bound to this specific portfolio - a valid session for one portfolio
    # must not grant access to a different one via its own slug/URL.
    if session_portfolio_id != portfolio.id:
        raise PortfolioNotAuthenticated(slug)
    return portfolio


@router.get("/{slug}/login", response_class=HTMLResponse)
def portal_login_form(slug: str, request: Request, db: Session = Depends(get_db)):
    portfolio = _get_portfolio_or_404(slug, db)
    return templates.TemplateResponse(
        request, "portal_login.html", {"portfolio": portfolio, "error": None}
    )


@router.post("/{slug}/login")
def portal_login_submit(slug: str, request: Request, password: str = Form(...), db: Session = Depends(get_db)):
    portfolio = _get_portfolio_or_404(slug, db)
    if not verify_password(password, portfolio.password_hash):
        return templates.TemplateResponse(
            request,
            "portal_login.html",
            {"portfolio": portfolio, "error": "Incorrect password"},
            status_code=401,
        )
    response = RedirectResponse(url=f"/portal/{slug}", status_code=303)
    response.set_cookie(
        PORTFOLIO_SESSION_COOKIE_NAME,
        create_portfolio_session_cookie_value(portfolio.id),
        httponly=True,
        samesite="lax",
        max_age=PORTFOLIO_SESSION_MAX_AGE,
    )
    return response


@router.post("/{slug}/logout")
def portal_logout(slug: str):
    response = RedirectResponse(url=f"/portal/{slug}/login", status_code=303)
    response.delete_cookie(PORTFOLIO_SESSION_COOKIE_NAME)
    return response


@router.get("/{slug}", response_class=HTMLResponse)
def portal_dashboard(slug: str, request: Request, db: Session = Depends(get_db)):
    portfolio = require_portfolio_session(slug, request, db)

    tenants = (
        db.query(models.Tenant)
        .filter(models.Tenant.portfolio_id == portfolio.id)
        .order_by(models.Tenant.business_name)
        .all()
    )
    tenant_ids = [t.id for t in tenants]

    lead_counts: dict = {}
    recent_leads: list = []
    lead_volume = {"last_24h": 0, "last_7d": 0, "last_30d": 0}

    if tenant_ids:
        count_rows = (
            db.query(models.Lead.tenant_id, func.count(models.Lead.id))
            .filter(models.Lead.tenant_id.in_(tenant_ids))
            .group_by(models.Lead.tenant_id)
            .all()
        )
        lead_counts = {tenant_id: count for tenant_id, count in count_rows}

        recent_leads = (
            db.query(models.Lead)
            .filter(models.Lead.tenant_id.in_(tenant_ids))
            .order_by(models.Lead.created_at.desc())
            .limit(25)
            .all()
        )

        now = datetime.utcnow()
        lead_volume = {
            "last_24h": db.query(models.Lead)
            .filter(models.Lead.tenant_id.in_(tenant_ids), models.Lead.created_at >= now - timedelta(hours=24))
            .count(),
            "last_7d": db.query(models.Lead)
            .filter(models.Lead.tenant_id.in_(tenant_ids), models.Lead.created_at >= now - timedelta(days=7))
            .count(),
            "last_30d": db.query(models.Lead)
            .filter(models.Lead.tenant_id.in_(tenant_ids), models.Lead.created_at >= now - timedelta(days=30))
            .count(),
        }

    return templates.TemplateResponse(
        request,
        "portal_dashboard.html",
        {
            "portfolio": portfolio,
            "tenants": tenants,
            "lead_counts": lead_counts,
            "recent_leads": recent_leads,
            "lead_volume": lead_volume,
        },
    )


@router.get("/{slug}/leads/{lead_id}", response_class=HTMLResponse)
def portal_lead_detail(slug: str, lead_id: int, request: Request, db: Session = Depends(get_db)):
    portfolio = require_portfolio_session(slug, request, db)

    lead = db.query(models.Lead).filter(models.Lead.id == lead_id).first()
    if lead is None or lead.tenant is None or lead.tenant.portfolio_id != portfolio.id:
        raise HTTPException(status_code=404, detail="Lead not found")

    return templates.TemplateResponse(
        request, "portal_lead_detail.html", {"lead": lead, "portfolio": portfolio}
    )
