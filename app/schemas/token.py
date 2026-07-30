from pydantic import BaseModel, EmailStr
from typing import Optional

class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"

class TokenPayload(BaseModel):
    sub: str  # User ID
    role: str
    exp: Optional[int] = None

class LoginPayload(BaseModel):
    email: EmailStr
    password: str

class RefreshPayload(BaseModel):
    refresh_token: str
