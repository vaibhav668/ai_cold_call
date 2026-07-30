import uuid
from pydantic import BaseModel, Field

class CallTriggerIn(BaseModel):
    campaign_id: uuid.UUID
    customer_id: uuid.UUID
    phone_number: str = Field(..., description="E.164 phone number to dial")

class CallTriggerOut(BaseModel):
    request_uuid: str
    status: str
