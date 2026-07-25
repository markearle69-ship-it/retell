import logging
from datetime import datetime
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from sqlalchemy.orm import Session

from . import models, notify, schemas
from .config import settings
from .db import Base, engine, get_db
from .signature import verify_retell_signature

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Retell Lead Router")


def require_admin(x_admin_key: str = Header(default="")) -> None:
    if not settings.admin_api_key or x_admin_key != settings.admin_api_key:
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/webhooks/retell")
async def retell_webhook(
    request: Request,
    x_retell_signature: str = Header(default=""),
    db: Session = Depends(get_db),
) -> dict:
    raw_body = await request.body()

    if not verify_retell_signature(raw_body, x_retell_signature, settings.retell_api_key):
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = await request.json()
    event = payload.get("event")
    call = payload.get("call") or {}
    call_id = call.get("call_id")

    if not call_id:
        raise HTTPException(status_code=400, detail="Missing call.call_id")

    to_number = call.get("to_number")

    tenant = None
    if to_number:
        tenant = (
            db.query(models.Tenant)
            .filter(models.Tenant.to_number == to_number)
            .first()
        )
        if tenant is None:
            logger.warning("No tenant configured for to_number=%s (call_id=%s)", to_number, call_id)

    lead = db.query(models.Lead).filter(models.Lead.call_id == call_id).first()
    if lead is None:
        lead = models.Lead(call_id=call_id)
        db.add(lead)

    analysis = call.get("call_analysis") or {}

    if tenant is not None:
        lead.tenant_id = tenant.id
    lead.from_number = call.get("from_number") or lead.from_number
    lead.to_number = to_number or lead.to_number
    lead.agent_id = call.get("agent_id") or lead.agent_id
    lead.call_summary = analysis.get("call_summary") or lead.call_summary
    lead.transcript = call.get("transcript") or lead.transcript
    lead.user_sentiment = analysis.get("user_sentiment") or lead.user_sentiment
    if "call_successful" in analysis:
        lead.call_successful = analysis.get("call_successful")
    lead.recording_url = call.get("recording_url") or lead.recording_url
    lead.disconnection_reason = call.get("disconnection_reason") or lead.disconnection_reason
    lead.start_timestamp = call.get("start_timestamp") or lead.start_timestamp
    lead.end_timestamp = call.get("end_timestamp") or lead.end_timestamp
    lead.duration_ms = call.get("duration_ms") or lead.duration_ms
    lead.last_event = event
    lead.raw_payload = payload

    db.commit()
    db.refresh(lead)

    # Notify once the call has been analyzed (summary is ready) - this is the
    # event that carries call_summary/user_sentiment/call_successful.
    if event == "call_analyzed" and tenant is not None and lead.notified_at is None:
        notify.notify_tenant(tenant, lead)
        lead.notified_at = datetime.utcnow()
        db.commit()

    return {"status": "ok"}


@app.post("/tenants", response_model=schemas.TenantOut, dependencies=[Depends(require_admin)])
def create_tenant(tenant_in: schemas.TenantCreate, db: Session = Depends(get_db)):
    existing = (
        db.query(models.Tenant)
        .filter(models.Tenant.to_number == tenant_in.to_number)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="A tenant with this to_number already exists")

    tenant = models.Tenant(**tenant_in.model_dump())
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return tenant


@app.get("/tenants", response_model=list[schemas.TenantOut], dependencies=[Depends(require_admin)])
def list_tenants(db: Session = Depends(get_db)):
    return db.query(models.Tenant).order_by(models.Tenant.id).all()


@app.get("/leads", response_model=list[schemas.LeadOut], dependencies=[Depends(require_admin)])
def list_leads(tenant_id: Optional[int] = None, db: Session = Depends(get_db)):
    query = db.query(models.Lead)
    if tenant_id is not None:
        query = query.filter(models.Lead.tenant_id == tenant_id)
    return query.order_by(models.Lead.created_at.desc()).all()


@app.get("/leads/{lead_id}", response_model=schemas.LeadDetail, dependencies=[Depends(require_admin)])
def get_lead(lead_id: int, db: Session = Depends(get_db)):
    lead = db.query(models.Lead).filter(models.Lead.id == lead_id).first()
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead
