import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict

class PromptTemplateBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    system_prompt: str = Field(..., min_length=1)
    language_prompt: Optional[str] = None
    conversation_goals: Optional[str] = None
    is_active: bool = True

class PromptTemplateCreate(PromptTemplateBase):
    pass

class PromptTemplateUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    system_prompt: Optional[str] = Field(None, min_length=1)
    language_prompt: Optional[str] = None
    conversation_goals: Optional[str] = None
    is_active: Optional[bool] = None

class PromptTemplateOut(PromptTemplateBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    campaign_id: uuid.UUID
    created_at: datetime
    updated_at: datetime

class PromptTemplateCompileOut(BaseModel):
    campaign_id: uuid.UUID
    customer_id: uuid.UUID
    resolved_variables: dict
    compiled_prompt: str
