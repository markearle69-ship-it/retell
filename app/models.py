from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from .db import Base

DEFAULT_GLOBAL_PROMPT_RULES = (
    "If the caller directly asks whether you are a real person, a human, an AI, a bot, or "
    "anything similar, answer honestly and immediately: you are an AI assistant / virtual "
    "receptionist for the business, not a human. Never claim or imply you are human. After "
    "disclosing this, reassure them you can still fully help - gather their details and get a "
    "real technician on the team to call them back - and continue the call normally."
)


class GlobalPromptConfig(Base):
    """Single-row config: rules prepended to every niche's rendered prompt, so
    universal behavior (e.g. "never claim to be human") is maintained in one
    place instead of copy-pasted into every niche template."""

    __tablename__ = "global_prompt_config"

    id = Column(Integer, primary_key=True)
    shared_rules = Column(Text, nullable=False, default=DEFAULT_GLOBAL_PROMPT_RULES)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class NicheTemplate(Base):
    """A reusable Retell agent prompt for a niche (e.g. Auto AC Repair), with
    {{business_name}}/{{location}}/{{zip_codes}} placeholders filled in per site."""

    __tablename__ = "niche_templates"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    prompt_template = Column(Text, nullable=False)
    begin_message_template = Column(Text, nullable=True)
    voice_id = Column(String, nullable=False)
    model = Column(String, nullable=False, default="gpt-4.1")

    # Niche-level (same for every site in this niche) template variables, for
    # sharing one generic prompt structure across niches via {{service_description}}
    # / {{problem_domain}} / {{collect_list}} placeholders.
    service_description = Column(Text, nullable=True)
    problem_domain = Column(Text, nullable=True)
    collect_list = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    tenants = relationship("Tenant", back_populates="niche_template")


class Tenant(Base):
    """A local business (rank-and-rent site owner) that receives leads."""

    __tablename__ = "tenants"

    id = Column(Integer, primary_key=True)
    business_name = Column(String, nullable=False)
    # The Twilio/Retell number this tenant's site forwards calls to.
    # Used to route an inbound call's webhook to the right tenant.
    to_number = Column(String, unique=True, nullable=False, index=True)
    notify_email = Column(String, nullable=True)
    notify_sms_number = Column(String, nullable=True)

    # Auto-provisioning (Twilio SIP trunk + Retell agent/LLM/number import).
    niche_template_id = Column(Integer, ForeignKey("niche_templates.id"), nullable=True)
    location = Column(String, nullable=True)
    zip_codes = Column(String, nullable=True)
    retell_llm_id = Column(String, nullable=True)
    retell_agent_id = Column(String, nullable=True)
    twilio_number_sid = Column(String, nullable=True)
    provisioned_at = Column(DateTime, nullable=True)
    provisioning_error = Column(Text, nullable=True)

    # Rank tracking. This site's EMD (exact-match domain), e.g.
    # "socorroplumbingrepair.com" - matched against SERP result URLs.
    domain = Column(String, nullable=True)
    # Override for the search phrase to check (without location - that comes
    # from `location` above). Falls back to the niche's service_description,
    # then business_name, if left blank. See rank_checker.build_query().
    target_keyword = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    leads = relationship("Lead", back_populates="tenant")
    niche_template = relationship("NicheTemplate", back_populates="tenants")
    rank_checks = relationship(
        "RankCheck", back_populates="tenant", order_by="RankCheck.checked_at.desc()"
    )


class Lead(Base):
    """One inbound call, enriched with Retell's summary/analysis once available."""

    __tablename__ = "leads"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True, index=True)
    call_id = Column(String, unique=True, nullable=False, index=True)

    from_number = Column(String, nullable=True)
    to_number = Column(String, nullable=True)
    agent_id = Column(String, nullable=True)

    call_summary = Column(Text, nullable=True)
    transcript = Column(Text, nullable=True)
    user_sentiment = Column(String, nullable=True)
    call_successful = Column(Boolean, nullable=True)
    recording_url = Column(String, nullable=True)
    disconnection_reason = Column(String, nullable=True)

    start_timestamp = Column(BigInteger, nullable=True)
    end_timestamp = Column(BigInteger, nullable=True)
    duration_ms = Column(BigInteger, nullable=True)

    last_event = Column(String, nullable=True)
    notified_at = Column(DateTime, nullable=True)
    raw_payload = Column(JSON, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    tenant = relationship("Tenant", back_populates="leads")


class RankCheck(Base):
    """One organic-search ranking snapshot for a site, e.g. from a cron job
    that periodically runs scripts/check_rankings.py."""

    __tablename__ = "rank_checks"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)

    query = Column(String, nullable=False)
    search_engine = Column(String, nullable=False, default="google")

    # Null position means the domain wasn't found in the results scanned
    # (num_results, at query time) - not necessarily "not ranking at all".
    position = Column(Integer, nullable=True)
    matched_url = Column(String, nullable=True)
    num_results_checked = Column(Integer, nullable=True)

    # Set instead of position/matched_url if the provider call itself failed
    # (rate limit, bad API key, network error, etc.) so a failed check is
    # distinguishable from a genuine "not ranked" result.
    error = Column(Text, nullable=True)

    checked_at = Column(DateTime, default=datetime.utcnow, index=True)

    tenant = relationship("Tenant", back_populates="rank_checks")
