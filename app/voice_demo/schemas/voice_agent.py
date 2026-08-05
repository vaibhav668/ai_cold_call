import uuid
from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, ConfigDict

class VoiceProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: Optional[str] = None
    avatar: Optional[str] = None
    gender: str
    supported_languages: str
    voice_provider: str
    preview_audio: Optional[str] = None
    status: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

class SessionSetupIn(BaseModel):
    voice_profile_id: uuid.UUID
    industry: str  # "hospital" or "real_estate"
    language: str  # "English", "Hindi", or "Telugu"

class SessionSetupOut(BaseModel):
    session_id: str
    campaign_id: uuid.UUID
    customer_id: uuid.UUID
    voice_profile: VoiceProfileOut

class MessageExchange(BaseModel):
    sender: str  # "user" or "agent"
    text: str
    timestamp: datetime

class SummaryOut(BaseModel):
    summary: str
    intent: str
    sentiment: str
    duration_seconds: int
    extracted_information: Dict[str, Any]
    lead_qualification: str
    appointment_status: str
    knowledge_retrieved: List[str]
    recommended_next_action: str
    transcript: List[Dict[str, Any]]
    # New metrics requested
    language: str
    voice_used: str
    industry: str
    lead_score: Optional[int] = None
    site_visit_status: str
    extracted_variables: Dict[str, Any]

