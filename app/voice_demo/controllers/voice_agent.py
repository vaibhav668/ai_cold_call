import uuid
import json
import asyncio
import time
from datetime import datetime, timezone
import numpy as np
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.voice_demo.models.voice_profile import VoiceProfile
from app.voice_demo.schemas.voice_agent import VoiceProfileOut, SessionSetupIn, SessionSetupOut, SummaryOut
from app.voice_demo.repositories.voice_profile import VoiceProfileRepository
from app.services.conversation_engine import ConversationEngine
from app.services.tts_service import VoiceService
from app.services.stt_service import SpeechService
from app.services.vad_service import EndOfSpeechDetector
from app.services.session_manager import SessionManager
from app.services.call_state_machine import CallStateMachine, CallState
from app.core.logging import logger
from app.models.campaign import Campaign
from app.models.customer import Customer
from app.services.llm_service import LLMService

router = APIRouter()

# In-memory dictionary to track active browser sessions
_demo_sessions: Dict[str, Dict[str, Any]] = {}
_STOP_SENTINEL = object()

def linear2ulaw(sample: int) -> int:
    """Converts a 16-bit signed linear PCM sample to 8-bit G.711 u-law."""
    BIAS = 0x84
    CLIP = 32635
    sign = (sample >> 8) & 0x80
    if sign != 0:
        sample = -sample
    if sample > CLIP:
        sample = CLIP
    sample = sample + BIAS
    exponent = 7
    exp_mask = 0x4000
    while (sample & exp_mask) == 0 and exponent > 0:
        exponent -= 1
        exp_mask >>= 1
    mantissa = (sample >> (exponent + 3)) & 0x0F
    ulaw_byte = ~(sign | (exponent << 4) | mantissa) & 0xFF
    return ulaw_byte

def pcm16_to_ulaw(pcm_bytes: bytes) -> bytes:
    """Convert raw 16-bit linear PCM bytes to G.711 mu-law."""
    samples = np.frombuffer(pcm_bytes, dtype=np.int16)
    return bytes(linear2ulaw(int(s)) for s in samples)

@router.get("/voices", response_model=List[VoiceProfileOut])
async def get_voices(db: AsyncSession = Depends(get_db_session)):
    """Fetch all active voice profiles available for the browser demo."""
    repo = VoiceProfileRepository(db)
    return await repo.get_active()

@router.get("/industries")
async def get_industries():
    """Retrieve supported demo industries and their structural setups."""
    return [
        {
            "id": "hospital",
            "name": "Hospital Receptionist",
            "description": "Engage with Sarah at Mercy Hospital. Confirm details of your appointment, ask about visiting hours, parking fees, or cancellation terms."
        },
        {
            "id": "real_estate",
            "name": "Real Estate Consultant",
            "description": "Speak with James at Premium Realty to qualify for Orchard Heights luxury apartments, ask about amenities, pricing, or book a site visit."
        }
    ]

@router.post("/sessions", response_model=SessionSetupOut)
async def create_session(setup: SessionSetupIn, db: AsyncSession = Depends(get_db_session)):
    """
    Initialize a browser voice demo session.
    Automatically resolves corresponding Campaign & Customer, and performs
    voice-to-language adaptation if needed.
    """
    # 1. Resolve Voice Profile
    repo = VoiceProfileRepository(db)
    selected_voice = await repo.get(setup.voice_profile_id)
    if not selected_voice or selected_voice.status != "active":
        raise HTTPException(status_code=404, detail="Selected voice profile not found or inactive.")

    resolved_voice = selected_voice
    supported_langs = [l.strip() for l in selected_voice.supported_languages.split(",")]
    
    # Adaptive Voice Switching: If voice does not support requested language, switch to closest compatible
    if setup.language not in supported_langs:
        logger.info(f"[VOICE ADAPT] Selected voice {selected_voice.name} does not support {setup.language}. Finding compatible voice...")
        all_voices = await repo.get_active()
        compatible_voice = None
        
        # 1. Try matching by gender and target language
        for v in all_voices:
            v_langs = [l.strip() for l in v.supported_languages.split(",")]
            if v.gender == selected_voice.gender and setup.language in v_langs:
                compatible_voice = v
                break
        
        # 2. Try matching by target language only
        if not compatible_voice:
            for v in all_voices:
                v_langs = [l.strip() for l in v.supported_languages.split(",")]
                if setup.language in v_langs:
                    compatible_voice = v
                    break
        
        if compatible_voice:
            resolved_voice = compatible_voice
            logger.info(f"[VOICE ADAPT] Switched session voice to {resolved_voice.name} ({resolved_voice.gender})")
        else:
            logger.warning(f"[VOICE ADAPT] No compatible voice found for language {setup.language}. Keeping {selected_voice.name}.")

    # 2. Resolve Campaign ID by industry
    camp_query = select(Campaign).where(Campaign.workflow_type == setup.industry, Campaign.is_active == True)
    camp_res = await db.execute(camp_query)
    campaign = camp_res.scalars().first()
    
    if not campaign:
        # Fallback to inactive campaign or any matching campaign
        camp_query_fb = select(Campaign).where(Campaign.workflow_type == setup.industry)
        camp_res_fb = await db.execute(camp_query_fb)
        campaign = camp_res_fb.scalars().first()
        if not campaign:
            raise HTTPException(status_code=404, detail=f"No campaign configured for industry '{setup.industry}'.")

    # 3. Resolve or Create default Customer by preferred language
    cust_query = select(Customer)
    cust_res = await db.execute(cust_query)
    all_customers = cust_res.scalars().all()
    
    customer = None
    for c in all_customers:
        pref_lang = c.custom_variables.get("preferred_language") if c.custom_variables else None
        if pref_lang == setup.language:
            if setup.industry == "hospital" and "doctor_name" in c.custom_variables:
                customer = c
                break
            elif setup.industry == "real_estate" and "property_interest" in c.custom_variables:
                customer = c
                break

    if not customer:
        # Create a matching demo customer
        logger.info(f"[SESSION] Customer for {setup.language} / {setup.industry} not found. Creating default mock lead...")
        mock_phone = f"+1555{uuid.uuid4().int % 10000000:07d}"
        custom_vars = {
            "preferred_language": setup.language,
        }
        if setup.industry == "hospital":
            custom_vars.update({
                "doctor_name": "Dr. Emily Vance",
                "department": "Cardiology",
                "appointment_date": "2026-08-05",
                "appointment_time": "10:00 AM"
            })
        else:
            custom_vars.update({
                "property_interest": "Orchard Heights",
                "budget": "$150,000",
                "location": "Gachibowli",
                "lead_status": "New Lead"
            })
            
        customer = Customer(
            id=uuid.uuid4(),
            first_name="Demo",
            last_name="User",
            phone_number=mock_phone,
            email="demo.user@example.com",
            custom_variables=custom_vars,
            is_active=True
        )
        db.add(customer)
        await db.flush()
        await db.commit()

    # 4. Initialize session configuration
    session_id = str(uuid.uuid4())
    voice_config_dict = json.loads(resolved_voice.voice_configuration or "{}")

    # Update in SessionManager for engine availability
    sm_manager = SessionManager()
    await sm_manager.update_session_metadata(session_id, {
        "session_id": session_id,
        "campaign_id": str(campaign.id),
        "customer_id": str(customer.id),
        "language": setup.language,
        "voice_config": voice_config_dict
    })

    _demo_sessions[session_id] = {
        "session_id": session_id,
        "campaign_id": campaign.id,
        "customer_id": customer.id,
        "voice_profile": resolved_voice,
        "voice_config": voice_config_dict,        # CRITICAL: required by WS handler
        "language": setup.language,
        "industry": setup.industry,
        "created_at": datetime.now(timezone.utc),
        "start_time": None,
        "end_time": None,
        "transcript": []
    }

    return SessionSetupOut(
        session_id=session_id,
        campaign_id=campaign.id,
        customer_id=customer.id,
        voice_profile=resolved_voice
    )

@router.post("/summary/{session_id}", response_model=SummaryOut)
async def get_session_summary(session_id: str):
    """
    Generate the final conversation summary, intent, sentiment, duration, and
    extracted metrics using LLM analysis on the conversation transcript.
    """
    meta = _demo_sessions.get(session_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Demo session not found.")

    # Calculate duration
    start = meta.get("start_time")
    end = meta.get("end_time") or time.time()
    duration = int(end - start) if start else 0

    # Retrieve transcript
    exchanges = meta.get("transcript", [])
    transcript_str = "\n".join([f"{'Customer' if msg['sender'] == 'user' else 'Agent'}: {msg['text']}" for msg in exchanges])

    if not exchanges:
        return SummaryOut(
            summary="The conversation was empty.",
            intent="None",
            sentiment="Neutral",
            duration_seconds=duration,
            extracted_information={},
            lead_qualification="Not Applicable",
            appointment_status="None",
            knowledge_retrieved=[],
            recommended_next_action="No action needed.",
            transcript=[]
        )

    # Invoke LLM to perform structural summarization
    prompt = f"""
You are an expert conversational analyst. Analyze the following conversation transcript between our AI voice agent and a customer.
Generate a JSON output summarizing the call metrics and intent details. Do not return any introductory or trailing text. ONLY return the valid JSON block.

TRANSCRIPT:
{transcript_str}

JSON SCHEMA:
{{
  "summary": "Brief 1-2 sentence summary of the call",
  "intent": "Primary customer intent (e.g., confirm appointment, reschedule, inquire about price, generic query)",
  "sentiment": "Overall customer sentiment (Positive, Neutral, Negative, Frustrated)",
  "extracted_information": {{
     "first_name": "Customer first name if found",
     "last_name": "Customer last name if found",
     "phone_number": "Phone number if found",
     "appointment_date": "Date if scheduled/mentioned",
     "appointment_time": "Time if scheduled/mentioned",
     "budget": "Budget if mentioned",
     "property_interest": "Property of interest if mentioned"
  }},
  "lead_qualification": "Cold / Warm / Hot / Qualified / Not Applicable",
  "appointment_status": "Scheduled / Confirmed / Rescheduled / Cancelled / None",
  "knowledge_retrieved": ["list of specific facts or policies discussed/retrieved from RAG"],
  "recommended_next_action": "Recommended next action for sales/support team"
}}
"""
    llm = LLMService()
    try:
        content, _ = await llm.generate_completion([{"role": "user", "content": prompt}], tools=None)
        # Parse JSON
        summary_data = json.loads(content.strip().replace("```json", "").replace("```", ""))
    except Exception as e:
        logger.error(f"[SUMMARY] LLM summarization failed: {e}")
        # High quality fallback
        summary_data = {
            "summary": "AI voice calling demo session completed.",
            "intent": "Inquire about services",
            "sentiment": "Neutral",
            "extracted_information": {},
            "lead_qualification": "Warm",
            "appointment_status": "None",
            "knowledge_retrieved": ["General information"],
            "recommended_next_action": "Follow up via email."
        }

    return SummaryOut(
        summary=summary_data.get("summary", ""),
        intent=summary_data.get("intent", ""),
        sentiment=summary_data.get("sentiment", ""),
        duration_seconds=duration,
        extracted_information=summary_data.get("extracted_information", {}),
        lead_qualification=summary_data.get("lead_qualification", "Not Applicable"),
        appointment_status=summary_data.get("appointment_status", "None"),
        knowledge_retrieved=summary_data.get("knowledge_retrieved", []),
        recommended_next_action=summary_data.get("recommended_next_action", ""),
        transcript=exchanges
    )

@router.websocket("/stream/{session_id}")
async def voice_agent_websocket(websocket: WebSocket, session_id: str):
    """
    Bidirectional WebSocket for browser streaming.
    Accepts raw audio, handles VAD detection, triggers Conversation Engine,
    and streams Melotts synthesized audio back.
    """
    await websocket.accept()
    logger.info(f"[DEMO-WS] Connected session: {session_id}")

    meta = _demo_sessions.get(session_id)
    if not meta:
        logger.error(f"[DEMO-WS] Session meta not found for {session_id}. Terminating.")
        await websocket.close(code=1008)
        return

    # Track start time
    meta["start_time"] = time.time()

    campaign_id = uuid.UUID(str(meta["campaign_id"]))
    customer_id = uuid.UUID(str(meta["customer_id"]))
    language = meta["language"]
    voice_config = meta.get("voice_config", {})
    language_code = {"English": "en", "Hindi": "hi", "Telugu": "te"}.get(language, "en")

    # Shared active states
    sm = CallStateMachine(session_id)
    audio_queue: asyncio.Queue = asyncio.Queue()
    llm_lock = asyncio.Lock()
    cancel_event = asyncio.Event()
    utterance_buffer = bytearray()
    
    vad = EndOfSpeechDetector()
    stt = SpeechService()

    pipeline_task: Optional[asyncio.Task] = None

    async def _send_state_change(new_state: CallState):
        """Transition the call state machine and notify the browser of the state change."""
        await sm.transition(new_state)
        try:
            await websocket.send_json({
                "event": "state_change",
                "state": new_state.name
            })
        except Exception:
            pass

    async def _barge_in() -> None:
        """Stop current AI speech and transition immediately to customer speaking."""
        nonlocal pipeline_task
        logger.info(f"[DEMO-WS] Barge-in! Interrupted voice for session {session_id}")
        cancel_event.set()
        audio_queue.put_nowait(_STOP_SENTINEL)
        vad.reset()
        utterance_buffer.clear()
        
        if pipeline_task and not pipeline_task.done():
            pipeline_task.cancel()
            try:
                await asyncio.shield(pipeline_task)
            except (asyncio.CancelledError, Exception):
                pass
            pipeline_task = None
            
        await _send_state_change(CallState.CUSTOMER_SPEAKING)

    async def _fire_greeting() -> None:
        """Fire initial greeting response."""
        nonlocal pipeline_task
        logger.info(f"[DEMO-WS] Firing greeting for session {session_id}")
        pipeline_task = asyncio.create_task(
            _run_pipeline(
                call_uuid=session_id,
                user_text="[CALL_START]",
                campaign_id=campaign_id,
                customer_id=customer_id,
                audio_queue=audio_queue,
                cancel_event=cancel_event,
                sm=sm,
                llm_lock=llm_lock,
                voice_config=voice_config,
                language_code=language_code,
                websocket=websocket,
                session_meta=meta,
                state_callback=_send_state_change
            )
        )

    # 1. Start send loop in background
    async def _send_loop():
        try:
            while not sm.is_terminal():
                item = await audio_queue.get()
                if item is _STOP_SENTINEL:
                    # Clear out buffer on interrupt
                    while not audio_queue.empty():
                        audio_queue.get_nowait()
                    try:
                        await websocket.send_json({"event": "clear_audio"})
                    except Exception:
                        pass
                    continue
                if item is None:
                    break
                
                # Send raw bytes to browser
                try:
                    await websocket.send_bytes(item)
                except Exception:
                    break
                await asyncio.sleep(0.02)
        except Exception as e:
            logger.error(f"[DEMO-WS] Send loop error: {e}")

    send_task = asyncio.create_task(_send_loop())

    # 2. Main receive loop
    try:
        # Fire initial warm greeting
        await _fire_greeting()
        
        while not sm.is_terminal():
            data = await websocket.receive()
            if data.get("type") == "websocket.disconnect":
                logger.info(f"[DEMO-WS] Browser disconnected for session {session_id}")
                break

            # Handle control messages (JSON)
            if "text" in data:
                try:
                    msg = json.loads(data["text"])
                    event = msg.get("event")
                    if event == "ping":
                        await websocket.send_json({"event": "pong"})
                    elif event == "stop":
                        logger.info(f"[DEMO-WS] Stop event received for session {session_id}")
                        break
                except Exception:
                    pass

            # Handle binary audio stream
            elif "bytes" in data:
                binary_data = data["bytes"]
                
                # Transcode Int16 PCM (320 bytes per 20ms) to G.711 mu-law (160 bytes)
                if len(binary_data) == 320:
                    mu_law_audio = pcm16_to_ulaw(binary_data)
                else:
                    mu_law_audio = binary_data

                # VAD Interruption Checks when AI is speaking
                if sm.is_ai_speaking():
                    loop_time = asyncio.get_event_loop().time()
                    if loop_time - sm.ai_speech_start_time > 1.2:
                        vad_event = vad.process_frame(mu_law_audio)
                        if vad_event == "speech_start":
                            await _barge_in()
                    else:
                        vad.reset()
                    continue

                if sm.state in (
                    CallState.TRANSCRIBING,
                    CallState.THINKING,
                    CallState.GENERATING_RESPONSE,
                    CallState.CALL_COMPLETED,
                    CallState.ERROR,
                ):
                    continue

                # Normal speech buffering
                loop_time = asyncio.get_event_loop().time()
                if sm.is_waiting() and (loop_time - sm.waiting_start_time < 0.6):
                    vad.reset()
                    continue

                vad_event = vad.process_frame(mu_law_audio)
                if sm.state == CallState.CUSTOMER_SPEAKING:
                    utterance_buffer.extend(mu_law_audio)

                if vad_event == "speech_start":
                    if sm.is_waiting():
                        logger.info(f"[DEMO-WS] Speech start detected for session {session_id}")
                        utterance_buffer.clear()
                        vad.reset()
                        vad._in_speech = True
                        vad._speech_confirmed = True
                        await _send_state_change(CallState.CUSTOMER_SPEAKING)

                elif vad_event == "speech_end":
                    if sm.state == CallState.CUSTOMER_SPEAKING:
                        logger.info(f"[DEMO-WS] Speech end detected — firing STT for session {session_id}")
                        await _send_state_change(CallState.TRANSCRIBING)

                        utterance_bytes = bytes(utterance_buffer)
                        utterance_buffer.clear()
                        vad.reset()

                        # Guard duplicate pipeline
                        if pipeline_task and not pipeline_task.done():
                            pipeline_task.cancel()
                            try:
                                await asyncio.shield(pipeline_task)
                            except (asyncio.CancelledError, Exception):
                                pass

                        async def _transcribe_and_run(audio: bytes):
                            # Transcribe user text in target language
                            transcript = await stt.transcribe_utterance(audio, language=language_code)
                            if not transcript:
                                logger.info(f"[DEMO-WS] Empty transcript. Returning to WAITING.")
                                await _send_state_change(CallState.WAITING_FOR_CUSTOMER)
                                return

                            logger.info(f"[DEMO-WS] User Transcript: '{transcript}'")
                            
                            # Send live user transcript event
                            try:
                                await websocket.send_json({
                                    "event": "transcript",
                                    "sender": "user",
                                    "text": transcript,
                                    "timestamp": datetime.utcnow().isoformat()
                                })
                            except Exception:
                                pass
                            
                            # Save in transcript
                            meta["transcript"].append({
                                "sender": "user",
                                "text": transcript,
                                "timestamp": datetime.utcnow().isoformat()
                            })

                            # Run pipeline
                            await _run_pipeline(
                                call_uuid=session_id,
                                user_text=transcript,
                                campaign_id=campaign_id,
                                customer_id=customer_id,
                                audio_queue=audio_queue,
                                cancel_event=cancel_event,
                                sm=sm,
                                llm_lock=llm_lock,
                                voice_config=voice_config,
                                language_code=language_code,
                                websocket=websocket,
                                session_meta=meta,
                                state_callback=_send_state_change
                            )

                        pipeline_task = asyncio.create_task(_transcribe_and_run(utterance_bytes))

    except WebSocketDisconnect:
        logger.info(f"[DEMO-WS] WebSocket disconnected for session {session_id}")
    except Exception as e:
        logger.error(f"[DEMO-WS] WebSocket exception: {e}", exc_info=True)
    finally:
        # Save end time
        meta["end_time"] = time.time()
        logger.info(f"[DEMO-WS] Cleaning up active tasks for session {session_id}")
        
        # Cleanup
        if pipeline_task and not pipeline_task.done():
            pipeline_task.cancel()
            try:
                await asyncio.shield(pipeline_task)
            except (asyncio.CancelledError, Exception):
                pass

        audio_queue.put_nowait(None)
        cancel_event.set()
        send_task.cancel()
        try:
            await asyncio.shield(send_task)
        except (asyncio.CancelledError, Exception):
            pass

        try:
            await websocket.close()
        except Exception:
            pass

async def _run_pipeline(
    call_uuid: str,
    user_text: str,
    campaign_id: uuid.UUID,
    customer_id: uuid.UUID,
    audio_queue: asyncio.Queue,
    cancel_event: asyncio.Event,
    sm: CallStateMachine,
    llm_lock: asyncio.Lock,
    voice_config: Optional[dict] = None,
    language_code: Optional[str] = None,
    websocket: Optional[WebSocket] = None,
    session_meta: Optional[dict] = None,
    state_callback = None
) -> None:
    """Core turn-taking pipeline: ConversationEngine → MeloTTS synthesis."""
    logger.info(f"[DEMO-PIPELINE] Pipeline started for {call_uuid}")

    # 1. Transition to THINKING
    if state_callback:
        await state_callback(CallState.THINKING)
    else:
        await sm.transition(CallState.THINKING)

    response_text = ""
    should_hangup = False

    async with llm_lock:
        try:
            async for db in get_db_session():
                engine = ConversationEngine(db)
                response_text, should_hangup, _ = await engine.process_turn(
                    call_id=call_uuid,
                    campaign_id=campaign_id,
                    customer_id=customer_id,
                    user_text=user_text
                )
                break
        except Exception as e:
            logger.error(f"[DEMO-PIPELINE] Engine execution failed: {e}")
            response_text = "I am having trouble connecting to my network right now. Could you repeat that?"

    logger.info(f"[DEMO-PIPELINE] Agent Response: '{response_text}'")

    if not response_text:
        if state_callback:
            await state_callback(CallState.WAITING_FOR_CUSTOMER)
        else:
            await sm.transition(CallState.WAITING_FOR_CUSTOMER)
        return

    # Send transcript event
    try:
        await websocket.send_json({
            "event": "transcript",
            "sender": "agent",
            "text": response_text,
            "timestamp": datetime.utcnow().isoformat()
        })
    except Exception:
        pass

    # Save to session meta
    if session_meta:
        session_meta["transcript"].append({
            "sender": "agent",
            "text": response_text,
            "timestamp": datetime.utcnow().isoformat()
        })

    # 2. Transition to GENERATING_RESPONSE
    if state_callback:
        await state_callback(CallState.GENERATING_RESPONSE)
    else:
        await sm.transition(CallState.GENERATING_RESPONSE)

    # 3. Transition to AI_SPEAKING & stream audio chunks
    tts = VoiceService()
    cancel_event.clear()
    
    if state_callback:
        await state_callback(CallState.AI_SPEAKING)
    else:
        await sm.transition(CallState.AI_SPEAKING)

    try:
        chunks_count = 0
        async for audio_chunk in tts.stream_speech(response_text, cancel_event=cancel_event, language=language_code, voice_config=voice_config):
            if cancel_event.is_set():
                break
            await audio_queue.put(audio_chunk)
            chunks_count += 1
        logger.info(f"[DEMO-PIPELINE] Synthesized and queued {chunks_count} chunks.")
    except Exception as e:
        logger.error(f"[DEMO-PIPELINE] TTS generation error: {e}")

    if not cancel_event.is_set():
        if should_hangup:
            if state_callback:
                await state_callback(CallState.CALL_COMPLETED)
            else:
                await sm.transition(CallState.CALL_COMPLETED)
        else:
            if state_callback:
                await state_callback(CallState.WAITING_FOR_CUSTOMER)
            else:
                await sm.transition(CallState.WAITING_FOR_CUSTOMER)
