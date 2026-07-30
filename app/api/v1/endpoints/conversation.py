from fastapi import APIRouter, Depends, status, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
import uuid
from app.db.session import get_db_session
from app.api.deps import get_current_user
from app.services.conversation_engine import ConversationEngine
from app.models.user import User

router = APIRouter()

class ConversationTurnIn(BaseModel):
    campaign_id: uuid.UUID
    customer_id: uuid.UUID
    user_text: str = Field(..., min_length=1)

class ConversationTurnOut(BaseModel):
    response_text: str
    should_hangup: bool
    should_transfer: bool

class ConversationEndIn(BaseModel):
    campaign_id: uuid.UUID
    customer_id: uuid.UUID
    phone_number: str
    duration_seconds: int

@router.post("/conversation/{call_id}/turn", response_model=ConversationTurnOut)
async def process_conversation_turn(
    call_id: str,
    payload: ConversationTurnIn,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user)
):
    """Process an active call turn: loads message memory context and runs LLM tools loop."""
    engine = ConversationEngine(db)
    response_text, should_hangup, should_transfer = await engine.process_turn(
        call_id=call_id,
        campaign_id=payload.campaign_id,
        customer_id=payload.customer_id,
        user_text=payload.user_text
    )
    return {
        "response_text": response_text,
        "should_hangup": should_hangup,
        "should_transfer": should_transfer
    }

@router.post("/conversation/{call_id}/end", status_code=status.HTTP_200_OK)
async def end_conversation_session(
    call_id: str,
    payload: ConversationEndIn,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user)
):
    """End the call: saves transcript to PostgreSQL call logs and purges Redis keys."""
    engine = ConversationEngine(db)
    call_log = await engine.end_call(
        call_id=call_id,
        campaign_id=payload.campaign_id,
        customer_id=payload.customer_id,
        phone_number=payload.phone_number,
        duration_seconds=payload.duration_seconds
    )
    return {
        "status": "session_terminated",
        "call_log_id": str(call_log.id)
    }
