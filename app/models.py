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

    # Social auto-provisioning: one topic/angle per line, rotated through when
    # generating a site's post queue so sites sharing a niche don't all post
    # the same idea in the same week (e.g. "seasonal maintenance tip",
    # "common warning signs", "customer testimonial", "emergency call-out").
    social_content_pillars = Column(Text, nullable=True)
    # Tone/voice guidance folded into the caption-generation prompt (e.g.
    # "friendly, plain-spoken, no jargon, always end with a call to action").
    social_caption_style = Column(Text, nullable=True)
    # Comma-separated Ayrshare platform names; blank falls back to
    # settings.social_default_platforms.
    social_platforms = Column(String, nullable=True)
    # Bannerbear template UID; blank falls back to settings.bannerbear_template_uid.
    social_template_uid = Column(String, nullable=True)

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

    # Social auto-provisioning (Ayrshare profile + generated/scheduled posts).
    logo_url = Column(String, nullable=True)
    brand_color = Column(String, nullable=True)
    ayrshare_profile_key = Column(String, nullable=True)
    ayrshare_ref_id = Column(String, nullable=True)
    social_provisioned_at = Column(DateTime, nullable=True)
    social_provisioning_error = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    leads = relationship("Lead", back_populates="tenant")
    niche_template = relationship("NicheTemplate", back_populates="tenants")
    social_posts = relationship(
        "SocialPost", back_populates="tenant", order_by="SocialPost.scheduled_for"
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


class SocialPost(Base):
    """One generated+scheduled social post for a site. Rows move through
    status draft -> scheduled -> posted, or -> failed (with `error` set) if a
    step failed - resubmitted "Provision social" runs only retry what a given
    post is still missing (image, then the Ayrshare schedule call)."""

    __tablename__ = "social_posts"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    niche_template_id = Column(Integer, ForeignKey("niche_templates.id"), nullable=True)

    content_pillar = Column(String, nullable=True)
    caption = Column(Text, nullable=False)
    image_url = Column(String, nullable=True)
    platforms = Column(String, nullable=False)
    scheduled_for = Column(DateTime, nullable=False)

    status = Column(String, nullable=False, default="draft")  # draft, scheduled, posted, failed
    ayrshare_post_id = Column(String, nullable=True)
    error = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    tenant = relationship("Tenant", back_populates="social_posts")
