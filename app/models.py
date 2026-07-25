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
    created_at = Column(DateTime, default=datetime.utcnow)

    leads = relationship("Lead", back_populates="tenant")


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
