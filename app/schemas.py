from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr


class TenantCreate(BaseModel):
    business_name: str
    to_number: str
    notify_email: Optional[EmailStr] = None
    notify_sms_number: Optional[str] = None
    domain: Optional[str] = None
    target_keyword: Optional[str] = None


class TenantOut(TenantCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int


class LeadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: Optional[int]
    call_id: str
    from_number: Optional[str]
    to_number: Optional[str]
    agent_id: Optional[str]
    call_summary: Optional[str]
    user_sentiment: Optional[str]
    call_successful: Optional[bool]
    recording_url: Optional[str]
    disconnection_reason: Optional[str]
    start_timestamp: Optional[int]
    end_timestamp: Optional[int]
    duration_ms: Optional[int]
    last_event: Optional[str]
    notified_at: Optional[datetime]
    created_at: datetime


class LeadDetail(LeadOut):
    transcript: Optional[str]
