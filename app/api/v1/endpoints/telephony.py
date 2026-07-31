from fastapi import APIRouter, Depends, status, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import uuid
import json
import base64
from typing import Optional
from app.db.session import get_db_session
from app.api.deps import get_current_user, RoleChecker
from app.schemas.telephony import CallTriggerIn, CallTriggerOut
from app.services.telephony_service import TelephonyService
from app.services.vad_service import VADService
from app.services.stt_service import SpeechService
from app.services.tts_service import VoiceService
from app.services.conversation_engine import ConversationEngine
from app.models.call_log import CallLog
from app.core.logging import logger
from app.models.user import User

router = APIRouter()

@router.post("/telephony/dial", response_model=CallTriggerOut)
async def trigger_outbound_call(
    payload: CallTriggerIn,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(RoleChecker(["admin", "manager"]))
):
    """Initiates an outbound cold call using Plivo's REST API."""
    host = request.headers.get("host", "example.com")
    
    service = TelephonyService(db)
    request_uuid, call_status = await service.initiate_call(
        campaign_id=payload.campaign_id,
        customer_id=payload.customer_id,
        phone_number=payload.phone_number,
        callback_domain=host
    )
    
    if not request_uuid:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Outbound call dialer trigger failed."
        )
        
    return {
        "request_uuid": request_uuid,
        "status": call_status
    }

@router.post("/telephony/answer")
async def plivo_answer_webhook(
    request: Request,
    campaign_id: uuid.UUID,
    customer_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session)
):
    """Plivo answer callback webhook returning XML Stream instructions."""
    form_data = await request.form()
    call_uuid = form_data.get("CallUUID", f"unknown-call-{uuid.uuid4()}")
    host = request.headers.get("host", "example.com")
    
    # Dynamically link the CallUUID to the CallLog so the websocket and status callbacks can match it
    try:
        query = select(CallLog).where(
            CallLog.campaign_id == campaign_id,
            CallLog.customer_id == customer_id,
            CallLog.status.in_(["initiated", "ringing"])
        ).order_by(CallLog.created_at.desc())
        result = await db.execute(query)
        call_log = result.scalars().first()
        if call_log:
            call_log.plivo_call_uuid = call_uuid
            call_log.status = "ringing"
            await db.commit()
            logger.info(f"Answer Webhook: Linked CallLog ID {call_log.id} to CallUUID {call_uuid}")
    except Exception as e:
        logger.error(f"Error linking call_uuid in answer webhook: {e}")
    
    xml_content = f"""<Response>
    <Stream url="wss://{host}/api/v1/telephony/stream/{call_uuid}"/>
</Response>"""
    return Response(content=xml_content, media_type="application/xml")

@router.post("/telephony/inbound")
async def plivo_inbound_webhook(request: Request):
    """Handles incoming customer support calls, returning custom Speak & Stream instructions."""
    form_data = await request.form()
    call_uuid = form_data.get("CallUUID", f"inbound-call-{uuid.uuid4()}")
    host = request.headers.get("host", "example.com")
    
    xml_content = f"""<Response>
    <Speak>Thank you for calling Mercy Hospital. Please hold while we connect you to our voice coordinator.</Speak>
    <Stream url="wss://{host}/api/v1/telephony/stream/{call_uuid}"/>
</Response>"""
    return Response(content=xml_content, media_type="application/xml")

@router.post("/telephony/status")
async def plivo_status_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db_session)
):
    """Processes Plivo call lifecycle status callbacks (completed, failed, ringing)."""
    form_data = await request.form()
    call_uuid = form_data.get("CallUUID")
    request_uuid = form_data.get("RequestUUID")
    call_status = form_data.get("CallStatus")
    duration = int(form_data.get("Duration", 0))
    
    if call_uuid and call_status:
        service = TelephonyService(db)
        await service.process_status_update(call_uuid, call_status, duration, request_uuid)
        
    return Response(content="<Response></Response>", media_type="application/xml")

@router.websocket("/telephony/stream/{call_uuid}")
async def plivo_audio_stream_websocket(
    websocket: WebSocket,
    call_uuid: str
):
    """Bidirectional WebSocket handling incoming Plivo JSON frames and returning audio."""
    await websocket.accept()
    logger.info(f"Plivo Audio Stream WebSocket connection accepted: {call_uuid}")
    
    # Initialize pipeline checkers
    vad = VADService(threshold=1500.0)
    stt = SpeechService()
    tts = VoiceService()
    
    bot_is_speaking = False
    
    try:
        while True:
            message = await websocket.receive_text()
            data = json.loads(message)
            event = data.get("event")
            
            if event == "start":
                start_data = data.get("start", {})
                logger.info(f"Stream started for Call UUID {call_uuid}. Stream ID: {start_data.get('streamSid')}")
                
            elif event == "media":
                media_data = data.get("media", {})
                payload = media_data.get("payload", "")
                
                try:
                    raw_audio = base64.b64decode(payload)
                    
                    # 1. Voice Activity Detection Interruption Check
                    is_user_speaking = vad.is_speech(raw_audio)
                    if is_user_speaking and bot_is_speaking:
                        logger.info(f"User speech detected. Interrupting bot speech for Call {call_uuid}.")
                        bot_is_speaking = False
                        # Send clear command to Plivo to flush voice buffers
                        await websocket.send_text(json.dumps({"event": "clear"}))
                        
                    # 2. Feed to Streaming STT
                    transcript = await stt.transcribe_chunk(raw_audio)
                    if transcript:
                        logger.info(f"STT Transcript captured: '{transcript}'")
                        
                        # 3. Retrieve context and run Conversation Turn
                        campaign_id = None
                        customer_id = None
                        
                        async for db in get_db_session():
                            query = select(CallLog).where(CallLog.plivo_call_uuid == call_uuid)
                            result = await db.execute(query)
                            call_log = result.scalars().first()
                            if call_log:
                                campaign_id = call_log.campaign_id
                                customer_id = call_log.customer_id
                            break
                            
                        if not campaign_id or not customer_id:
                            campaign_id = uuid.uuid4()
                            customer_id = uuid.uuid4()
                            
                        response_text = ""
                        async for db in get_db_session():
                            engine = ConversationEngine(db)
                            response_text, should_hangup, should_transfer = await engine.process_turn(
                                call_id=call_uuid,
                                campaign_id=campaign_id,
                                customer_id=customer_id,
                                user_text=transcript
                            )
                            break
                            
                        # 4. Trigger TTS Audio Streaming back to Plivo
                        if response_text:
                            bot_is_speaking = True
                            async for audio_chunk in tts.stream_speech(response_text):
                                if not bot_is_speaking:
                                    # Interrupt mid-stream
                                    break
                                    
                                payload_out = base64.b64encode(audio_chunk).decode("utf-8")
                                reply_msg = {
                                    "event": "media",
                                    "media": {
                                        "payload": payload_out
                                    }
                                }
                                await websocket.send_text(json.dumps(reply_msg))
                                
                            bot_is_speaking = False
                            
                except Exception as e:
                    logger.warning(f"Error handling media frame: {e}")
                    
            elif event == "stop":
                logger.info(f"Stream stopped for Call UUID {call_uuid}")
                break
                
    except WebSocketDisconnect:
        logger.info(f"Stream WebSocket disconnected for Call UUID {call_uuid}")
    except Exception as e:
        logger.error(f"Error in stream WebSocket session: {e}")
        await websocket.close()
