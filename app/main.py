import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from . import admin, analytics, models, notify, schemas
from .admin import NotAuthenticated
from .config import settings
from .db import Base, engine, get_db
from .migrations import run_additive_migrations
from .signature import verify_retell_signature

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

Base.metadata.create_all(bind=engine)
run_additive_migrations(engine)

app = FastAPI(title="Retell Lead Router")
app.include_router(admin.router)

# /collect and /t.js are hit cross-origin from every tracked microsite's own
# domain, unauthenticated (site_key is a public identifier, not a secret -
# see AnalyticsSite in app/models.py), so this needs to allow any origin.
# No credentials/cookies are involved on these calls, so allow_origins="*"
# doesn't expose the admin session cookie to anyone.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


_TRACKER_JS = """
(function () {
  var script = document.currentScript;
  if (!script) return;
  var site = script.getAttribute("data-site");
  if (!site) return;
  var endpoint = script.src.replace(/\\/t\\.js.*$/, "/collect");

  function send() {
    var payload = JSON.stringify({
      site_key: site,
      url: location.href,
      referrer: document.referrer || null
    });
    try {
      if (navigator.sendBeacon) {
        navigator.sendBeacon(endpoint, new Blob([payload], { type: "text/plain" }));
        return;
      }
    } catch (e) {}
    fetch(endpoint, {
      method: "POST",
      body: payload,
      headers: { "Content-Type": "text/plain" },
      keepalive: true
    }).catch(function () {});
  }

  send();
})();
""".strip()


@app.exception_handler(NotAuthenticated)
async def not_authenticated_handler(request: Request, exc: NotAuthenticated) -> RedirectResponse:
    return RedirectResponse(url="/admin/login", status_code=303)


def require_admin(x_admin_key: str = Header(default="")) -> None:
    if not settings.admin_api_key or x_admin_key != settings.admin_api_key:
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/t.js")
def tracker_script() -> Response:
    """The self-hosted analytics snippet. Embed once per microsite:

    <script defer data-site="SITE_KEY" src="https://<this-service>/t.js"></script>

    Same file for every site - it reads its own site key from the data-site
    attribute on its own <script> tag, Plausible-style. No cookies, no
    localStorage, no third-party requests."""
    return Response(content=_TRACKER_JS, media_type="application/javascript")


@app.post("/collect")
async def collect(request: Request, db: Session = Depends(get_db)) -> Response:
    """Records one page-view beacon. Always responds 204 regardless of
    whether site_key is recognized, so this can't be used to enumerate
    valid site keys by watching the response."""
    try:
        raw = await request.body()
        payload = json.loads(raw) if raw else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = {}

    site_key = (payload.get("site_key") or "").strip()
    if not site_key:
        return Response(status_code=204)

    site = (
        db.query(models.AnalyticsSite)
        .filter(models.AnalyticsSite.site_key == site_key)
        .first()
    )
    if site is None:
        return Response(status_code=204)

    user_agent = request.headers.get("user-agent", "")
    ip = analytics.client_ip(request)
    today = datetime.utcnow().strftime("%Y-%m-%d")

    landing_url = payload.get("url") or ""
    path = analytics.path_from_url(landing_url)
    referrer_info = analytics.parse_referrer(payload.get("referrer"), own_host=site.domain)
    utm_info = analytics.parse_landing_url(landing_url)

    view = models.PageView(
        site_id=site.id,
        path=path,
        referrer_host=referrer_info["referrer_host"],
        search_engine=referrer_info["search_engine"],
        search_query=referrer_info["search_query"],
        utm_source=utm_info["utm_source"],
        utm_medium=utm_info["utm_medium"],
        utm_campaign=utm_info["utm_campaign"],
        utm_term=utm_info["utm_term"],
        utm_content=utm_info["utm_content"],
        visitor_hash=analytics.visitor_hash(ip, user_agent, site_key, today),
        device_type=analytics.device_type(user_agent),
        browser=analytics.browser_name(user_agent),
        is_bot=analytics.is_bot_ua(user_agent),
    )
    db.add(view)
    db.commit()

    return Response(status_code=204)


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
