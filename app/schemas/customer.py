import uuid
import re
from datetime import datetime
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, EmailStr, Field, ConfigDict, field_validator

class CustomerBase(BaseModel):
    first_name: str = Field(..., min_length=1, max_length=255)
    last_name: Optional[str] = Field(None, max_length=255)
    phone_number: str = Field(..., description="E.164 phone format, e.g., +1234567890")
    email: Optional[EmailStr] = None
    custom_variables: Dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True

    @field_validator("phone_number")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        pattern = re.compile(r"^\+?[1-9]\d{1,14}$")
        v_clean = "".join(v.split())
        if not pattern.match(v_clean):
            raise ValueError("Phone number must match standard international E.164 format (e.g. +1234567890).")
        return v_clean

class CustomerCreate(CustomerBase):
    pass

class CustomerUpdate(BaseModel):
    first_name: Optional[str] = Field(None, min_length=1, max_length=255)
    last_name: Optional[str] = Field(None, max_length=255)
    phone_number: Optional[str] = None
    email: Optional[EmailStr] = None
    custom_variables: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None

    @field_validator("phone_number")
    @classmethod
    def validate_phone(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        pattern = re.compile(r"^\+?[1-9]\d{1,14}$")
        v_clean = "".join(v.split())
        if not pattern.match(v_clean):
            raise ValueError("Phone number must match standard international E.164 format.")
        return v_clean

class CustomerOut(CustomerBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime

class CustomerPaginated(BaseModel):
    total: int
    items: List[CustomerOut]
    skip: int
    limit: int
