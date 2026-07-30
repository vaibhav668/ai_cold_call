import uuid
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict, field_validator

class CampaignBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    is_active: bool = True
    workflow_type: str = Field("hospital", description="'hospital' or 'real_estate'")
    status: str = Field("draft", description="'draft', 'scheduled', 'active', 'paused', 'completed'")
    scheduled_start: Optional[datetime] = None
    scheduled_end: Optional[datetime] = None
    max_retries: int = Field(3, ge=0)
    retry_interval_minutes: int = Field(60, ge=1)

class CampaignCreate(CampaignBase):
    pass

class CampaignUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    is_active: Optional[bool] = None
    workflow_type: Optional[str] = None
    status: Optional[str] = None
    scheduled_start: Optional[datetime] = None
    scheduled_end: Optional[datetime] = None
    max_retries: Optional[int] = Field(None, ge=0)
    retry_interval_minutes: Optional[int] = Field(None, ge=1)

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        allowed = ["draft", "scheduled", "active", "paused", "completed"]
        if v not in allowed:
            raise ValueError(f"Status must be one of: {', '.join(allowed)}")
        return v

class CampaignOut(CampaignBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime

class CampaignPaginated(BaseModel):
    total: int
    items: List[CampaignOut]
    skip: int
    limit: int

class CampaignLeadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    campaign_id: uuid.UUID
    customer_id: uuid.UUID
    status: str
    retry_count: int
    last_attempt_at: Optional[datetime]
    next_attempt_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

class CampaignLeadAssign(BaseModel):
    customer_ids: List[uuid.UUID] = Field(..., min_length=1)
