import uuid
import json
import asyncio
import time
import re
import contextlib
from datetime import datetime, timezone
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
from app.main import STARTUP_METRICS

router = APIRouter()

# In-memory dictionary to track active browser sessions
_demo_sessions: Dict[str, Dict[str, Any]] = {}
_STOP_SENTINEL = object()


def pcm16_to_ulaw(pcm_bytes: bytes) -> bytes:
    """Convert raw 16-bit linear PCM bytes to G.711 mu-law via audioop (C-level)."""
    import audioop
    try:
        return audioop.lin2ulaw(pcm_bytes, 2)
    except Exception as e:
        logger.error(f"[AUDIO-TRANSCODE] Failed to convert PCM to mu-law: {e}")
        return b""


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
    repo = VoiceProfileRepository(db)
    selected_voice = await repo.get(setup.voice_profile_id)
    if not selected_voice or selected_voice.status != "active":
        raise HTTPException(status_code=404, detail="Selected voice profile not found or inactive.")

    resolved_voice = selected_voice
    supported_langs = [l.strip() for l in selected_voice.supported_languages.split(",")]

    if setup.language not in supported_langs:
        logger.info(f"[VOICE ADAPT] Selected voice {selected_voice.name} does not support {setup.language}. Finding compatible voice...")
        all_voices = await repo.get_active()
        compatible_voice = None

        for v in all_voices:
            v_langs = [l.strip() for l in v.supported_languages.split(",")]
            if v.gender == selected_voice.gender and setup.language in v_langs:
                compatible_voice = v
                break

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

    camp_query = select(Campaign).where(Campaign.workflow_type == setup.industry, Campaign.is_active == True)
    camp_res = await db.execute(camp_query)
    campaign = camp_res.scalars().first()

    if not campaign:
        camp_query_fb = select(Campaign).where(Campaign.workflow_type == setup.industry)
        camp_res_fb = await db.execute(camp_query_fb)
        campaign = camp_res_fb.scalars().first()
        if not campaign:
            raise HTTPException(status_code=404, detail=f"No campaign configured for industry '{setup.industry}'.")

    cust_query = select(Customer).where(Customer.phone_number == "+15551234567")
    cust_res = await db.execute(cust_query)
    customer = cust_res.scalars().first()

    custom_vars = {"preferred_language": setup.language}
    if setup.industry == "hospital":
        custom_vars.update({
            "doctor_name": "Dr. Sharma",
            "department": "Orthopedics",
            "appointment_date": "tomorrow",
            "appointment_time": "11:00 AM",
            "hospital_name": "CityCare Hospital",
            "purpose": "Routine Consultation"
        })
    else:
        custom_vars.update({
            "property_name": "3 BHK Apartment",
            "property_interest": "3 BHK Apartment",
            "location": "Hyderabad",
            "price": "80 Lakhs",
            "budget": "80 Lakhs",
            "builder": "Skyline Developers"
        })

    if not customer:
        logger.info("[SESSION] Customer Vaibhav not found by phone number. Creating customer...")
        customer = Customer(
            id=uuid.uuid4(),
            first_name="Vaibhav",
            last_name="",
            phone_number="+15551234567",
            email="vaibhav.demo@example.com",
            custom_variables=custom_vars,
            is_active=True
        )
        db.add(customer)
    else:
        logger.info("[SESSION] Customer Vaibhav found by phone number. Updating variables and name...")
        customer.first_name = "Vaibhav"
        customer.last_name = ""
        customer.email = "vaibhav.demo@example.com"
        customer.custom_variables = custom_vars
        customer.is_active = True

    await db.flush()
    await db.commit()

    session_id = str(uuid.uuid4())
    voice_config_dict = json.loads(resolved_voice.voice_configuration or "{}")

    sm_manager = SessionManager()
    await sm_manager.update_session_metadata(session_id, {
        "session_id": session_id,
        "campaign_id": str(campaign.id),
        "customer_id": str(customer.id),
        "language": setup.language,
        "agent_name": resolved_voice.name,
        "voice_config": voice_config_dict
    })

    _demo_sessions[session_id] = {
        "session_id": session_id,
        "campaign_id": campaign.id,
        "customer_id": customer.id,
        "voice_profile": resolved_voice,
        "voice_config": voice_config_dict,
        "language": setup.language,
        "industry": setup.industry,
        "created_at": datetime.now(timezone.utc),
        "start_time": None,
        "end_time": None,
    }

    return SessionSetupOut(
        session_id=session_id,
        campaign_id=campaign.id,
        customer_id=customer.id,
        voice_profile=resolved_voice
    )


@router.api_route("/summary/{session_id}", methods=["GET", "POST"], response_model=SummaryOut)
async def get_session_summary(session_id: str):
    """Return session metadata and status information."""
    meta = _demo_sessions.get(session_id)
    if not meta:
        return SummaryOut(
            summary="Session metadata lost or process restarted.",
            intent="None",
            sentiment="Neutral",
            duration_seconds=0,
            extracted_information={},
            lead_qualification="Not Applicable",
            appointment_status="None",
            knowledge_retrieved=[],
            recommended_next_action="Please restart the conversation.",
            transcript=[],
            language="English",
            voice_used="Sophia",
            industry="hospital",
            lead_score=0,
            site_visit_status="None",
            extracted_variables={},
            session_id=session_id,
            current_state="UNKNOWN (Session lost)",
            failure_reason="Session ID not found in memory",
            error_stack=None
        )

    start = meta.get("start_time")
    end = meta.get("end_time") or time.time()
    duration = int(end - start) if start else 0

    voice_used = meta.get("voice_profile").name if meta.get("voice_profile") else "Sophia"
    language = meta.get("language", "English")
    industry = meta.get("industry", "hospital")
    failure_reason = meta.get("failure_reason")
    error_stack = meta.get("error_stack")
    current_state = meta.get("current_state", "COMPLETED" if not failure_reason else "FAILED")

    return SummaryOut(
        summary="Call session complete.",
        intent="None",
        sentiment="Neutral",
        duration_seconds=duration,
        extracted_information={},
        lead_qualification="Not Applicable",
        appointment_status="None",
        knowledge_retrieved=[],
        recommended_next_action="None",
        transcript=[],
        language=language,
        voice_used=voice_used,
        industry=industry,
        lead_score=0,
        site_visit_status="None",
        extracted_variables={},
        session_id=session_id,
        current_state=current_state,
        failure_reason=failure_reason,
        error_stack=error_stack
    )


async def _safe_cancel_task(task: asyncio.Task, timeout: float = 2.0) -> None:
    """Safely cancel an asyncio Task and wait for it to finish."""
    if task is None or task.done():
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError, Exception):
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)


@router.websocket("/stream/{session_id}")
async def voice_agent_websocket(websocket: WebSocket, session_id: str):
    """
    Bidirectional WebSocket for browser voice agent.
    Streams continuous audio, performs streaming STT during user speech,
    runs Progressive LLM → Sentence TTS pipeline, and transmits detailed telemetry.
    """
    await websocket.accept()
    logger.info(f"[DEMO-WS] Connected session: {session_id}")

    meta = _demo_sessions.get(session_id)
    if not meta:
        logger.error(f"[DEMO-WS] Session meta not found for {session_id}. Terminating.")
        await websocket.close(code=1008)
        return

    meta["start_time"] = time.time()

    # Transmit startup telemetry to client upon connection
    try:
        await websocket.send_json({
            "event": "startup_metrics",
            "metrics": STARTUP_METRICS
        })
    except Exception:
        pass

    campaign_id = uuid.UUID(str(meta["campaign_id"]))
    customer_id = uuid.UUID(str(meta["customer_id"]))
    language = meta["language"]
    voice_config = meta.get("voice_config", {})
    language_code = {"English": "en", "Hindi": "hi", "Telugu": "te"}.get(language, "en")

    sm = CallStateMachine(session_id)
    audio_queue: asyncio.Queue = asyncio.Queue()
    llm_lock = asyncio.Lock()
    cancel_event = asyncio.Event()

    utterance_buffer = bytearray()
    last_intermediate_stt_len = 0
    intermediate_stt_task: Optional[asyncio.Task] = None

    vad = EndOfSpeechDetector()
    stt = SpeechService()

    pipeline_task: Optional[asyncio.Task] = None
    _pipeline_nonce = 0
    loop = asyncio.get_event_loop()

    # VAD timing tracker
    vad_timings = []

    async def _send_state_change(new_state: CallState) -> None:
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
        """Stop current AI speech immediately and transition to customer speaking."""
        nonlocal pipeline_task, _pipeline_nonce, cancel_event, intermediate_stt_task
        logger.info(f"[BARGE-IN] Customer interrupted AI speech for session {session_id}")

        _pipeline_nonce += 1
        cancel_event.set()
        audio_queue.put_nowait(_STOP_SENTINEL)
        vad.reset()
        utterance_buffer.clear()

        await _safe_cancel_task(intermediate_stt_task)
        intermediate_stt_task = None

        await _safe_cancel_task(pipeline_task)
        pipeline_task = None

        await _send_state_change(CallState.CUSTOMER_SPEAKING)

    async def _fire_pipeline(user_text: str, user_speech_end_t: float = 0.0) -> None:
        """Launch a new progressive streaming pipeline task with the current nonce."""
        nonlocal pipeline_task, _pipeline_nonce, cancel_event

        _pipeline_nonce += 1
        my_nonce = _pipeline_nonce
        cancel_event.clear()

        await _safe_cancel_task(pipeline_task)

        pipeline_task = asyncio.create_task(
            _run_pipeline(
                call_uuid=session_id,
                user_text=user_text,
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
                state_callback=_send_state_change,
                nonce=my_nonce,
                get_nonce=lambda: _pipeline_nonce,
                user_speech_end_t=user_speech_end_t,
                vad_timings=vad_timings,
            )
        )

    # ── Audio send loop ──────────────────────────────────────────────────────
    async def _send_loop() -> None:
        chunks_sent = 0
        try:
            while not sm.is_terminal():
                item = await audio_queue.get()

                if item is _STOP_SENTINEL:
                    drained = 0
                    while not audio_queue.empty():
                        try:
                            audio_queue.get_nowait()
                            drained += 1
                        except asyncio.QueueEmpty:
                            break
                    try:
                        await websocket.send_json({"event": "clear_audio"})
                    except Exception:
                        pass
                    continue

                if item is None:
                    break

                try:
                    await websocket.send_bytes(item)
                    chunks_sent += 1
                except Exception as e:
                    logger.error(f"[WS-SEND] Connection lost during audio stream: {e}")
                    break

        except Exception as e:
            logger.error(f"[WS-SEND] Send loop error: {e}")

    send_task = asyncio.create_task(_send_loop())

    # ── Main receive loop ────────────────────────────────────────────────────
    try:
        logger.info(f"[DEMO-WS] Firing sub-second greeting pipeline for session {session_id}")
        await _fire_pipeline("[CALL_START]")

        while not sm.is_terminal():
            data = await websocket.receive()

            if data.get("type") == "websocket.disconnect":
                logger.info(f"[DEMO-WS] Browser disconnected for session {session_id}")
                break

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

            elif "bytes" in data:
                binary_data = data["bytes"]
                mu_law_audio = pcm16_to_ulaw(binary_data)

                # Measure VAD latency
                v_start = time.perf_counter()

                # VAD during AI speech: barge-in detection
                if sm.is_ai_speaking():
                    loop_time = loop.time()
                    if loop_time - sm.ai_speech_start_time > 1.2:
                        vad_event = await loop.run_in_executor(None, vad.process_frame, mu_law_audio)
                        v_elapsed = (time.perf_counter() - v_start) * 1000.0
                        vad_timings.append(v_elapsed)

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

                loop_time = loop.time()
                if sm.is_waiting() and (loop_time - sm.waiting_start_time < 0.6):
                    vad.reset()
                    continue

                # Normal VAD processing
                vad_event = await loop.run_in_executor(None, vad.process_frame, mu_law_audio)
                v_elapsed = (time.perf_counter() - v_start) * 1000.0
                vad_timings.append(v_elapsed)

                if sm.state == CallState.CUSTOMER_SPEAKING:
                    utterance_buffer.extend(mu_law_audio)

                    # Streaming STT: periodically run intermediate transcription every 8000 bytes (~1.0s audio)
                    if len(utterance_buffer) - last_intermediate_stt_len >= 8000:
                        last_intermediate_stt_len = len(utterance_buffer)

                        async def _run_intermediate_stt(audio_snapshot: bytes):
                            try:
                                inter_transcript = await stt.transcribe_utterance(audio_snapshot, language=language_code)
                                if inter_transcript:
                                    await websocket.send_json({
                                        "event": "transcript",
                                        "sender": "user",
                                        "text": inter_transcript,
                                        "intermediate": True
                                    })
                            except Exception:
                                pass

                        # Launch intermediate STT in background without blocking
                        if intermediate_stt_task is None or intermediate_stt_task.done():
                            intermediate_stt_task = asyncio.create_task(_run_intermediate_stt(bytes(utterance_buffer)))

                if vad_event == "speech_start":
                    if sm.is_waiting():
                        logger.info(f"[DEMO-WS] Speech start detected for session {session_id}")
                        utterance_buffer.clear()
                        last_intermediate_stt_len = 0
                        vad.reset()
                        vad.provider._in_speech = True
                        if hasattr(vad.provider, '_speech_confirmed'):
                            vad.provider._speech_confirmed = True
                        await _send_state_change(CallState.CUSTOMER_SPEAKING)

                elif vad_event == "speech_end":
                    if sm.state == CallState.CUSTOMER_SPEAKING:
                        user_speech_end_t = time.perf_counter()
                        logger.info(f"[DEMO-WS] Speech end detected — firing STT for session {session_id}")
                        await _send_state_change(CallState.TRANSCRIBING)

                        utterance_bytes = bytes(utterance_buffer)
                        utterance_buffer.clear()
                        last_intermediate_stt_len = 0
                        vad.reset()

                        await _safe_cancel_task(intermediate_stt_task)
                        intermediate_stt_task = None

                        async def _transcribe_and_run(audio: bytes, speech_end_t: float) -> None:
                            _stt_start = time.perf_counter()
                            transcript = await stt.transcribe_utterance(audio, language=language_code)
                            stt_latency_ms = (time.perf_counter() - _stt_start) * 1000.0

                            if not transcript:
                                logger.info(f"[DEMO-WS] Empty transcript. Returning to WAITING.")
                                await _send_state_change(CallState.WAITING_FOR_CUSTOMER)
                                return

                            logger.info(f"[METRICS] STT Latency: {stt_latency_ms:.1f}ms | Transcript: '{transcript}'")

                            # Send final user transcript to browser
                            try:
                                await websocket.send_json({
                                    "event": "transcript",
                                    "sender": "user",
                                    "text": transcript,
                                    "intermediate": False
                                })
                            except Exception:
                                pass

                            await _fire_pipeline(transcript, user_speech_end_t=speech_end_t)

                        await _safe_cancel_task(pipeline_task)
                        pipeline_task = asyncio.create_task(_transcribe_and_run(utterance_bytes, user_speech_end_t))

    except WebSocketDisconnect as e:
        logger.info(f"[DEMO-WS] WebSocket disconnect event for session {session_id} (code={e.code}, reason={e.reason or 'None'})")
        if meta:
            meta["failure_reason"] = f"WebSocket disconnected: code={e.code}, reason={e.reason or 'None'}"
            meta["current_state"] = sm.state.name
    except Exception as e:
        import traceback
        stack = traceback.format_exc()
        logger.error(f"[DEMO-WS] WebSocket exception for session {session_id}: {e}", exc_info=True)
        if meta:
            meta["failure_reason"] = f"WebSocket exception: {e}"
            meta["current_state"] = sm.state.name
            meta["error_stack"] = stack
    finally:
        meta["end_time"] = time.time()
        logger.info(f"[DEMO-WS] Cleaning up session {session_id}")

        cancel_event.set()
        await _safe_cancel_task(intermediate_stt_task)
        await _safe_cancel_task(pipeline_task)

        audio_queue.put_nowait(None)
        while not audio_queue.empty():
            with contextlib.suppress(asyncio.QueueEmpty):
                audio_queue.get_nowait()

        send_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await asyncio.wait_for(send_task, timeout=2.0)

        with contextlib.suppress(Exception):
            await websocket.close()

        utterance_buffer.clear()
        with contextlib.suppress(Exception):
            await SessionManager().clear_session(session_id)


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
    state_callback=None,
    nonce: int = 0,
    get_nonce=None,
    user_speech_end_t: float = 0.0,
    vad_timings: Optional[List[float]] = None,
) -> None:
    """
    End-to-End Progressive Pipeline:
    Stream LLM Tokens → Sentence Splitter → Progressive TTS Synthesis → Client Audio Queue
    Calculates detailed telemetry metrics and pushes them to the browser.
    """
    _pipeline_start = time.perf_counter()

    def _is_superseded() -> bool:
        return get_nonce is not None and get_nonce() != nonce

    if state_callback:
        await state_callback(CallState.THINKING)

    if _is_superseded():
        return

    should_hangup = False
    should_transfer = False
    llm_first_token_ms = 0.0
    tts_first_byte_ms = 0.0
    total_round_trip_ms = 0.0
    chunks_count = 0
    full_agent_response = []

    _llm_start = time.perf_counter()

    async with llm_lock:
        if _is_superseded():
            return

        try:
            async for db in get_db_session():
                engine = ConversationEngine(db)
                tts = VoiceService()

                # Stream LLM token generator
                token_stream = engine.process_turn_stream(
                    call_id=call_uuid,
                    campaign_id=campaign_id,
                    customer_id=customer_id,
                    user_text=user_text
                )

                # Generator yielding raw text chunks for TTS
                async def _text_chunk_extractor():
                    nonlocal llm_first_token_ms, should_hangup, should_transfer
                    async for chunk, h, tr in token_stream:
                        if _is_superseded() or cancel_event.is_set():
                            break
                        if h:
                            should_hangup = True
                        if tr:
                            should_transfer = True
                        if chunk:
                            if llm_first_token_ms == 0.0:
                                llm_first_token_ms = (time.perf_counter() - _llm_start) * 1000.0
                            full_agent_response.append(chunk)
                            yield chunk

                # Pass text generator into progressive sentence-level TTS streamer
                _tts_start = time.perf_counter()
                audio_stream = tts.stream_text_stream_progressive(
                    _text_chunk_extractor(),
                    cancel_event=cancel_event,
                    language=language_code,
                    voice_config=voice_config
                )

                # Transition state to GENERATING_RESPONSE / AI_SPEAKING as soon as audio starts
                if state_callback:
                    await state_callback(CallState.GENERATING_RESPONSE)

                first_chunk_sent = False

                async for audio_chunk in audio_stream:
                    if _is_superseded() or cancel_event.is_set():
                        break

                    if not first_chunk_sent:
                        first_chunk_sent = True
                        tts_first_byte_ms = (time.perf_counter() - _tts_start) * 1000.0
                        if user_speech_end_t > 0.0:
                            total_round_trip_ms = (time.perf_counter() - user_speech_end_t) * 1000.0
                        else:
                            total_round_trip_ms = (time.perf_counter() - _pipeline_start) * 1000.0

                        if state_callback:
                            await state_callback(CallState.AI_SPEAKING)

                        # Transmit real-time telemetry metrics to browser
                        avg_vad_ms = round(sum(vad_timings) / len(vad_timings), 2) if vad_timings else 0.0
                        try:
                            if websocket:
                                await websocket.send_json({
                                    "event": "metrics",
                                    "metrics": {
                                        "llm_latency_ms": round(llm_first_token_ms, 1),
                                        "tts_first_byte_ms": round(tts_first_byte_ms, 1),
                                        "total_round_trip_ms": round(total_round_trip_ms, 1),
                                        "vad_latency_ms": avg_vad_ms,
                                    }
                                })
                        except Exception:
                            pass

                        logger.info(
                            f"[TELEMETRY] Round-Trip={total_round_trip_ms:.1f}ms | "
                            f"LLM TTFT={llm_first_token_ms:.1f}ms | TTS TTFB={tts_first_byte_ms:.1f}ms | VAD avg={avg_vad_ms}ms"
                        )

                    await audio_queue.put(audio_chunk)
                    chunks_count += 1

                break

        except asyncio.CancelledError:
            raise
        except Exception as e:
            import traceback
            stack = traceback.format_exc()
            logger.error(f"[DEMO-PIPELINE] Pipeline error: {e}\n{stack}")

    _pipeline_total = (time.perf_counter() - _pipeline_start) * 1000.0
    full_text_str = "".join(full_agent_response).strip()

    if full_text_str and websocket:
        try:
            await websocket.send_json({
                "event": "transcript",
                "sender": "agent",
                "text": full_text_str,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
        except Exception:
            pass

    logger.info(f"[METRICS] Pipeline Complete: total={_pipeline_total:.1f}ms | chunks={chunks_count}")

    if not _is_superseded() and not cancel_event.is_set():
        if should_hangup:
            if state_callback:
                await state_callback(CallState.CALL_COMPLETED)
        else:
            if state_callback:
                await state_callback(CallState.WAITING_FOR_CUSTOMER)
