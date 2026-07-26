import os
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import models
from .config import settings
from .db import get_db
from .provisioning import ProvisioningError, provision_site
from .session import (
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE,
    create_session_cookie_value,
    verify_session_cookie,
)

router = APIRouter(prefix="/admin", tags=["admin"])

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=_TEMPLATES_DIR)


class NotAuthenticated(Exception):
    """Raised by require_admin_session; caught by an app-level handler that redirects to /admin/login."""


def require_admin_session(request: Request) -> None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token or not verify_session_cookie(token):
        raise NotAuthenticated()


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
def login_submit(request: Request, password: str = Form(...)):
    if not settings.admin_api_key or password != settings.admin_api_key:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Incorrect password"},
            status_code=401,
        )
    response = RedirectResponse(url="/admin", status_code=303)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        create_session_cookie_value(),
        httponly=True,
        samesite="lax",
        max_age=SESSION_MAX_AGE,
    )
    return response


@router.post("/logout")
def logout():
    response = RedirectResponse(url="/admin/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


def _dashboard_context(request: Request, db: Session, edit_id: Optional[int], provision_error: Optional[str]) -> dict:
    tenants = db.query(models.Tenant).order_by(models.Tenant.business_name).all()

    count_rows = (
        db.query(models.Lead.tenant_id, func.count(models.Lead.id))
        .group_by(models.Lead.tenant_id)
        .all()
    )
    lead_counts = {tenant_id: count for tenant_id, count in count_rows if tenant_id is not None}

    recent_leads = db.query(models.Lead).order_by(models.Lead.created_at.desc()).limit(25).all()

    edit_tenant = None
    if edit_id is not None:
        edit_tenant = db.query(models.Tenant).filter(models.Tenant.id == edit_id).first()

    niches = db.query(models.NicheTemplate).order_by(models.NicheTemplate.name).all()

    return {
        "tenants": tenants,
        "lead_counts": lead_counts,
        "recent_leads": recent_leads,
        "edit_tenant": edit_tenant,
        "niches": niches,
        "webhook_url": str(request.base_url).rstrip("/") + "/webhooks/retell",
        "provision_error": provision_error,
    }


@router.get("", response_class=HTMLResponse, dependencies=[Depends(require_admin_session)])
def dashboard(request: Request, edit_id: Optional[int] = None, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request, "dashboard.html", _dashboard_context(request, db, edit_id, provision_error=None)
    )


@router.post("/tenants", dependencies=[Depends(require_admin_session)])
def upsert_tenant(
    request: Request,
    tenant_id: Optional[int] = Form(None),
    business_name: str = Form(...),
    to_number: str = Form(...),
    notify_email: Optional[str] = Form(None),
    notify_sms_number: Optional[str] = Form(None),
    location: Optional[str] = Form(None),
    zip_codes: Optional[str] = Form(None),
    niche_id: Optional[str] = Form(None),
    force_reprovision: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    to_number = to_number.strip()
    niche_id_int = int(niche_id) if niche_id else None

    tenant = None
    if tenant_id:
        tenant = db.query(models.Tenant).filter(models.Tenant.id == tenant_id).first()
    if tenant is None:
        tenant = db.query(models.Tenant).filter(models.Tenant.to_number == to_number).first()
    if tenant is None:
        tenant = models.Tenant(to_number=to_number)
        db.add(tenant)

    tenant.business_name = business_name.strip()
    tenant.to_number = to_number
    tenant.notify_email = (notify_email or "").strip() or None
    tenant.notify_sms_number = (notify_sms_number or "").strip() or None
    tenant.location = (location or "").strip() or None
    tenant.zip_codes = (zip_codes or "").strip() or None

    if niche_id_int:
        niche = db.query(models.NicheTemplate).filter(models.NicheTemplate.id == niche_id_int).first()
        if niche is None:
            db.commit()
            return templates.TemplateResponse(
                request,
                "dashboard.html",
                _dashboard_context(request, db, edit_id=tenant.id, provision_error="Selected niche template not found."),
                status_code=400,
            )
        tenant.niche_template_id = niche.id

        if force_reprovision:
            tenant.retell_llm_id = None
            tenant.retell_agent_id = None
            tenant.twilio_number_sid = None
            tenant.provisioned_at = None
            tenant.provisioning_error = None

        db.commit()

        try:
            provision_site(db, tenant, niche)
        except ProvisioningError as exc:
            tenant.provisioning_error = str(exc)
            db.commit()
            return templates.TemplateResponse(
                request,
                "dashboard.html",
                _dashboard_context(request, db, edit_id=tenant.id, provision_error=str(exc)),
                status_code=400,
            )
    else:
        tenant.niche_template_id = None
        tenant.provisioning_error = None
        db.commit()

    return RedirectResponse(url="/admin", status_code=303)


@router.post("/tenants/{tenant_id}/delete", dependencies=[Depends(require_admin_session)])
def delete_tenant(tenant_id: int, db: Session = Depends(get_db)):
    tenant = db.query(models.Tenant).filter(models.Tenant.id == tenant_id).first()
    if tenant is not None:
        db.query(models.Lead).filter(models.Lead.tenant_id == tenant_id).update(
            {"tenant_id": None}
        )
        db.delete(tenant)
        db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@router.get(
    "/leads/{lead_id}",
    response_class=HTMLResponse,
    dependencies=[Depends(require_admin_session)],
)
def lead_detail(request: Request, lead_id: int, db: Session = Depends(get_db)):
    lead = db.query(models.Lead).filter(models.Lead.id == lead_id).first()
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    return templates.TemplateResponse(request, "lead_detail.html", {"lead": lead})


@router.get("/niches", response_class=HTMLResponse, dependencies=[Depends(require_admin_session)])
def list_niches(request: Request, edit_id: Optional[int] = None, db: Session = Depends(get_db)):
    niches = db.query(models.NicheTemplate).order_by(models.NicheTemplate.name).all()
    edit_niche = None
    if edit_id is not None:
        edit_niche = db.query(models.NicheTemplate).filter(models.NicheTemplate.id == edit_id).first()
    return templates.TemplateResponse(
        request, "niches.html", {"niches": niches, "edit_niche": edit_niche}
    )


@router.post("/niches", dependencies=[Depends(require_admin_session)])
def upsert_niche(
    niche_id: Optional[int] = Form(None),
    name: str = Form(...),
    prompt_template: str = Form(...),
    begin_message_template: Optional[str] = Form(None),
    voice_id: str = Form(...),
    model: str = Form("gpt-4.1"),
    db: Session = Depends(get_db),
):
    niche = None
    if niche_id:
        niche = db.query(models.NicheTemplate).filter(models.NicheTemplate.id == niche_id).first()
    if niche is None:
        niche = models.NicheTemplate(name=name.strip())
        db.add(niche)

    niche.name = name.strip()
    niche.prompt_template = prompt_template
    niche.begin_message_template = (begin_message_template or "").strip() or None
    niche.voice_id = voice_id.strip()
    niche.model = (model or "gpt-4.1").strip()

    db.commit()
    return RedirectResponse(url="/admin/niches", status_code=303)


@router.post("/niches/{niche_id}/delete", dependencies=[Depends(require_admin_session)])
def delete_niche(niche_id: int, db: Session = Depends(get_db)):
    niche = db.query(models.NicheTemplate).filter(models.NicheTemplate.id == niche_id).first()
    if niche is not None:
        db.query(models.Tenant).filter(models.Tenant.niche_template_id == niche_id).update(
            {"niche_template_id": None}
        )
        db.delete(niche)
        db.commit()
    return RedirectResponse(url="/admin/niches", status_code=303)
