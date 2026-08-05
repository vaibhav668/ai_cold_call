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
from app.services.llm_service import LLMService

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
        camp_query_fb = select(Campaign).where(Campaign.workflow_type == setup.industry)
        camp_res_fb = await db.execute(camp_query_fb)
        campaign = camp_res_fb.scalars().first()
        if not campaign:
            raise HTTPException(status_code=404, detail=f"No campaign configured for industry '{setup.industry}'.")

    # 3. Resolve, Create or Update Customer
    cust_query = select(Customer).where(Customer.phone_number == "+15551234567")
    cust_res = await db.execute(cust_query)
    customer = cust_res.scalars().first()

    custom_vars = {
        "preferred_language": setup.language,
    }
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
    """Return lightweight session metadata — transcript/summary generation is disabled for performance."""
    meta = _demo_sessions.get(session_id)
    if not meta:
        logger.warning(f"[SUMMARY] Session ID '{session_id}' not found in memory. Returning fallback summary.")
        return SummaryOut(
            summary="The conversation session metadata was lost or the server restarted.",
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
            failure_reason="Session ID not found in server memory (process restart or invalid session)",
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
        summary="Call summary and transcripts are disabled for performance optimization.",
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
    """
    Safely cancel an asyncio Task and wait for it to finish.
    Uses asyncio.wait_for instead of asyncio.shield to avoid the shield/cancel deadlock.
    """
    if task is None or task.done():
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError, Exception):
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)


@router.websocket("/stream/{session_id}")
async def voice_agent_websocket(websocket: WebSocket, session_id: str):
    """
    Bidirectional WebSocket for browser streaming.
    Accepts raw audio, handles VAD detection, triggers Conversation Engine,
    and streams TTS synthesized audio back.
    """
    await websocket.accept()
    logger.info(f"[DEMO-WS] Connected session: {session_id}")
    logger.info("[AUDIO-CODEC] WebSocket established. Browser input: 16-bit linear PCM, 8kHz downsampled. Server output: 8-bit G.711 mu-law, 8kHz mono (20ms frames, 160 bytes each).")

    meta = _demo_sessions.get(session_id)
    if not meta:
        logger.error(f"[DEMO-WS] Session meta not found for {session_id}. Terminating.")
        await websocket.close(code=1008)
        return

    meta["start_time"] = time.time()

    campaign_id = uuid.UUID(str(meta["campaign_id"]))
    customer_id = uuid.UUID(str(meta["customer_id"]))
    language = meta["language"]
    voice_config = meta.get("voice_config", {})
    language_code = {"English": "en", "Hindi": "hi", "Telugu": "te"}.get(language, "en")

    # Shared active state
    sm = CallStateMachine(session_id)
    audio_queue: asyncio.Queue = asyncio.Queue()
    llm_lock = asyncio.Lock()

    # cancel_event: set when current TTS stream must abort (barge-in or new pipeline)
    cancel_event = asyncio.Event()

    utterance_buffer = bytearray()
    vad = EndOfSpeechDetector()
    stt = SpeechService()

    pipeline_task: Optional[asyncio.Task] = None
    # Monotonically increasing nonce: pipeline only sends audio if its nonce matches current
    _pipeline_nonce = 0

    loop = asyncio.get_event_loop()

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
        nonlocal pipeline_task, _pipeline_nonce, cancel_event
        logger.info(f"[BARGE-IN] Customer interrupted AI speech for session {session_id}")

        # 1. Advance nonce — any running pipeline will self-cancel when it checks
        _pipeline_nonce += 1

        # 2. Signal the TTS stream to stop
        cancel_event.set()

        # 3. Flush the audio send queue with a sentinel
        audio_queue.put_nowait(_STOP_SENTINEL)

        # 4. Reset VAD state
        vad.reset()
        utterance_buffer.clear()

        # 5. Cancel the pipeline task
        await _safe_cancel_task(pipeline_task)
        pipeline_task = None

        # 6. Transition state
        await _send_state_change(CallState.CUSTOMER_SPEAKING)

    async def _fire_pipeline(user_text: str) -> None:
        """Launch a new pipeline task with the current nonce."""
        nonlocal pipeline_task, _pipeline_nonce, cancel_event

        # Advance nonce and reset cancel_event BEFORE starting pipeline
        _pipeline_nonce += 1
        my_nonce = _pipeline_nonce
        cancel_event.clear()

        # Cancel any existing pipeline
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
            )
        )

    # ── Audio send loop ──────────────────────────────────────────────────────
    async def _send_loop() -> None:
        chunks_sent = 0
        try:
            while not sm.is_terminal():
                item = await audio_queue.get()

                if item is _STOP_SENTINEL:
                    # Drain remaining stale audio chunks from queue
                    drained = 0
                    while not audio_queue.empty():
                        try:
                            audio_queue.get_nowait()
                            drained += 1
                        except asyncio.QueueEmpty:
                            break
                    if drained:
                        logger.info(f"[WS-SEND] Barge-in: drained {drained} stale audio chunks.")
                    # Notify client to clear its playback buffer immediately
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
                    if chunks_sent % 50 == 0:
                        logger.info(f"[WS-SEND] Streamed {chunks_sent} audio chunks to browser.")
                except Exception as e:
                    logger.error(f"[WS-SEND] Connection lost during audio stream: {e}")
                    break
                # No artificial sleep — WebSocket backpressure handles flow control naturally

        except Exception as e:
            logger.error(f"[WS-SEND] Send loop error: {e}")
        logger.info(f"[WS-SEND] Stream loop terminated. Total chunks sent: {chunks_sent}")

    send_task = asyncio.create_task(_send_loop())

    # ── Main receive loop ────────────────────────────────────────────────────
    try:
        # Fire initial warm greeting
        logger.info(f"[DEMO-WS] Firing greeting for session {session_id}")
        await _fire_pipeline("[CALL_START]")

        while not sm.is_terminal():
            data = await websocket.receive()

            if data.get("type") == "websocket.disconnect":
                logger.info(f"[DEMO-WS] Browser disconnected for session {session_id}")
                break

            # ── Control messages (JSON) ──────────────────────────────────────
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

            # ── Binary audio stream ──────────────────────────────────────────
            elif "bytes" in data:
                binary_data = data["bytes"]

                # Transcode Int16 PCM → G.711 mu-law (half the bytes)
                mu_law_audio = pcm16_to_ulaw(binary_data)

                # ── VAD during AI speech: barge-in detection ─────────────────
                if sm.is_ai_speaking():
                    loop_time = loop.time()
                    # Skip barge-in checks for the first 1.2s to avoid echo detection
                    if loop_time - sm.ai_speech_start_time > 1.2:
                        # VAD is CPU-bound (Silero Torch): run in executor, non-blocking
                        vad_event = await loop.run_in_executor(None, vad.process_frame, mu_law_audio)
                        if vad_event == "speech_start":
                            await _barge_in()
                    else:
                        vad.reset()
                    continue

                # ── Skip frames during processing states ─────────────────────
                if sm.state in (
                    CallState.TRANSCRIBING,
                    CallState.THINKING,
                    CallState.GENERATING_RESPONSE,
                    CallState.CALL_COMPLETED,
                    CallState.ERROR,
                ):
                    continue

                # ── Ignore audio during the echo blanking window ─────────────
                loop_time = loop.time()
                if sm.is_waiting() and (loop_time - sm.waiting_start_time < 0.6):
                    vad.reset()
                    continue

                # ── Normal VAD processing (executor — non-blocking) ──────────
                vad_event = await loop.run_in_executor(None, vad.process_frame, mu_law_audio)

                if sm.state == CallState.CUSTOMER_SPEAKING:
                    utterance_buffer.extend(mu_law_audio)

                if vad_event == "speech_start":
                    if sm.is_waiting():
                        logger.info(f"[DEMO-WS] Speech start detected for session {session_id}")
                        utterance_buffer.clear()
                        vad.reset()
                        vad.provider._in_speech = True
                        if hasattr(vad.provider, '_speech_confirmed'):
                            vad.provider._speech_confirmed = True
                        await _send_state_change(CallState.CUSTOMER_SPEAKING)

                elif vad_event == "speech_end":
                    if sm.state == CallState.CUSTOMER_SPEAKING:
                        logger.info(f"[DEMO-WS] Speech end detected — firing STT for session {session_id}")
                        await _send_state_change(CallState.TRANSCRIBING)

                        utterance_bytes = bytes(utterance_buffer)
                        utterance_buffer.clear()
                        vad.reset()

                        async def _transcribe_and_run(audio: bytes) -> None:
                            import time as _time
                            _stt_start = _time.perf_counter()
                            transcript = await stt.transcribe_utterance(audio, language=language_code)
                            _stt_latency = _time.perf_counter() - _stt_start
                            logger.info(f"[METRICS] STT Latency: {_stt_latency:.3f}s | audio_len={len(audio)} bytes")

                            if not transcript:
                                logger.info(f"[DEMO-WS] Empty transcript. Returning to WAITING.")
                                await _send_state_change(CallState.WAITING_FOR_CUSTOMER)
                                return

                            logger.info(f"[DEMO-WS] User Transcript: '{transcript}'")
                            await _fire_pipeline(transcript)

                        # Cancel any previous pipeline before starting STT→pipeline chain
                        await _safe_cancel_task(pipeline_task)
                        pipeline_task = asyncio.create_task(_transcribe_and_run(utterance_bytes))

    except WebSocketDisconnect as e:
        logger.info(f"[DEMO-WS] WebSocket disconnect event for session {session_id} (code={e.code}, reason={e.reason or 'None'})")
        if e.code == 1006:
            logger.warning(f"[DEMO-WS] Code 1006: abnormal closure — likely OOM crash, network timeout, or Render 30s idle timeout.")
        if meta:
            meta["failure_reason"] = f"WebSocket disconnected: code={e.code}, reason={e.reason or 'None'}"
            meta["current_state"] = sm.state.name
    except Exception as e:
        import traceback
        stack = traceback.format_exc()
        logger.error(f"[DEMO-WS] WebSocket exception for session {session_id} in state {sm.state.name}: {e}", exc_info=True)
        if meta:
            meta["failure_reason"] = f"WebSocket exception: {e}"
            meta["current_state"] = sm.state.name
            meta["error_stack"] = stack
    finally:
        meta["end_time"] = time.time()
        logger.info(f"[DEMO-WS] Cleaning up session {session_id}")

        # Signal all running pipelines to stop
        cancel_event.set()

        # Cancel pipeline task
        await _safe_cancel_task(pipeline_task)

        # Flush audio queue
        audio_queue.put_nowait(None)
        while not audio_queue.empty():
            with contextlib.suppress(asyncio.QueueEmpty):
                audio_queue.get_nowait()

        # Stop send loop
        send_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await asyncio.wait_for(send_task, timeout=2.0)

        # Close WebSocket cleanly
        with contextlib.suppress(Exception):
            await websocket.close()

        # Clear utterance buffer
        utterance_buffer.clear()

        # Clear session state from session manager
        with contextlib.suppress(Exception):
            await SessionManager().clear_session(session_id)

        import gc
        gc.collect()
        with contextlib.suppress(ImportError):
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

        try:
            import psutil
            rss = psutil.Process().memory_info().rss / (1024 * 1024)
            logger.info(f"[MEMORY] Session {session_id} cleaned. Current RSS: {rss:.2f} MB")
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
    state_callback=None,
    nonce: int = 0,
    get_nonce=None,
) -> None:
    """
    Core turn-taking pipeline: ConversationEngine → TTS synthesis → audio queue.

    nonce/get_nonce: Pipeline self-cancels if a newer pipeline has started (barge-in safety).
    """
    import time as _time
    _pipeline_start = _time.perf_counter()
    logger.info(f"[DEMO-PIPELINE] Pipeline started for {call_uuid} | nonce={nonce} | user_text='{user_text[:60]}'")

    def _is_superseded() -> bool:
        """Returns True if a newer pipeline has started — this one should abort."""
        return get_nonce is not None and get_nonce() != nonce

    # 1. Transition to THINKING
    if state_callback:
        await state_callback(CallState.THINKING)
    else:
        await sm.transition(CallState.THINKING)

    if _is_superseded():
        logger.info(f"[DEMO-PIPELINE] Pipeline nonce={nonce} superseded before LLM. Aborting.")
        return

    response_text = ""
    should_hangup = False

    _llm_start = _time.perf_counter()
    async with llm_lock:
        if _is_superseded():
            logger.info(f"[DEMO-PIPELINE] Pipeline nonce={nonce} superseded while waiting for LLM lock. Aborting.")
            return
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
        except asyncio.CancelledError:
            logger.info(f"[DEMO-PIPELINE] LLM call cancelled for nonce={nonce}")
            raise
        except Exception as e:
            logger.error(f"[DEMO-PIPELINE] Engine execution failed: {e}")
            response_text = "I am having trouble connecting right now. Could you repeat that?"

    _llm_latency = _time.perf_counter() - _llm_start
    logger.info(f"[METRICS] LLM+DB Latency: {_llm_latency:.3f}s | response_len={len(response_text)} chars")

    if _is_superseded() or cancel_event.is_set():
        logger.info(f"[DEMO-PIPELINE] Pipeline nonce={nonce} superseded after LLM. Aborting.")
        return

    if not response_text:
        if state_callback:
            await state_callback(CallState.WAITING_FOR_CUSTOMER)
        else:
            await sm.transition(CallState.WAITING_FOR_CUSTOMER)
        return

    logger.info(f"[DEMO-PIPELINE] Agent Response: '{response_text[:80]}...' (nonce={nonce})")

    # 2. Transition to GENERATING_RESPONSE
    if state_callback:
        await state_callback(CallState.GENERATING_RESPONSE)
    else:
        await sm.transition(CallState.GENERATING_RESPONSE)

    if _is_superseded() or cancel_event.is_set():
        return

    # 3. Transition to AI_SPEAKING & stream audio chunks
    tts = VoiceService()

    if state_callback:
        await state_callback(CallState.AI_SPEAKING)
    else:
        await sm.transition(CallState.AI_SPEAKING)

    try:
        chunks_count = 0
        _tts_start = _time.perf_counter()
        _tts_ttfb = None

        async for audio_chunk in tts.stream_speech(
            response_text,
            cancel_event=cancel_event,
            language=language_code,
            voice_config=voice_config
        ):
            # Abort immediately if superseded by a new pipeline (barge-in)
            if _is_superseded() or cancel_event.is_set():
                logger.info(f"[DEMO-PIPELINE] TTS stream interrupted at chunk {chunks_count} (nonce={nonce} superseded).")
                break

            if _tts_ttfb is None:
                _tts_ttfb = _time.perf_counter() - _tts_start
                logger.info(f"[METRICS] TTS TTFB: {_tts_ttfb:.3f}s")

            await audio_queue.put(audio_chunk)
            chunks_count += 1

        _tts_total = _time.perf_counter() - _tts_start
        logger.info(f"[METRICS] TTS Total: {_tts_total:.3f}s | chunks={chunks_count} | nonce={nonce}")

    except asyncio.CancelledError:
        logger.info(f"[DEMO-PIPELINE] TTS cancelled for nonce={nonce}")
        raise
    except Exception as e:
        import traceback
        stack = traceback.format_exc()
        logger.error(f"[DEMO-PIPELINE] TTS generation error: {e}")
        if session_meta:
            session_meta["failure_reason"] = f"TTS generation error: {e}"
            session_meta["current_state"] = sm.state.name
            session_meta["error_stack"] = stack

    _pipeline_total = _time.perf_counter() - _pipeline_start
    logger.info(f"[METRICS] Pipeline Total: {_pipeline_total:.3f}s | LLM={_llm_latency:.3f}s | nonce={nonce}")

    # Only transition state if this pipeline is still the active one
    if not _is_superseded() and not cancel_event.is_set():
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
