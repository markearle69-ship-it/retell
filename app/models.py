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

    created_at = Column(DateTime, default=datetime.utcnow)

    leads = relationship("Lead", back_populates="tenant")
    niche_template = relationship("NicheTemplate", back_populates="tenants")


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
