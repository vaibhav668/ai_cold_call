"""
Telephony API Endpoints
=======================
Handles:
  - POST /telephony/dial         — trigger outbound call via Plivo REST
  - POST /telephony/answer       — Plivo answer webhook → return XML stream instructions
  - POST /telephony/inbound      — inbound call webhook
  - POST /telephony/status       — Plivo call lifecycle status callbacks
  - WS   /telephony/stream/{id}  — bidirectional Plivo media stream WebSocket

WebSocket Architecture
----------------------
The WebSocket handler uses TWO concurrent asyncio Tasks:

  _recv_loop(...)   Reads every incoming Plivo JSON frame.
                    Routes audio bytes through VAD → utterance buffer.
                    On speech_start → signals barge-in if AI is speaking.
                    On speech_end   → hands utterance buffer to STT pipeline.

  _pipeline(...)    STT → LLM → TTS.
                    Protected by an asyncio.Lock to prevent parallel requests.
                    Each TTS frame is pushed into an asyncio.Queue.

  _send_loop(...)   Drains the asyncio.Queue and sends playAudio frames to Plivo.
                    Checks a cancel_event per-utterance to abort mid-stream.

This ensures incoming audio is ALWAYS being read regardless of what TTS is doing,
which is the fix for the fundamental "bot cannot hear while speaking" bug.
"""

from fastapi import APIRouter, Depends, status, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import uuid
import json
import base64
import asyncio
from typing import Optional

from app.db.session import get_db_session
from app.api.deps import get_current_user, RoleChecker
from app.schemas.telephony import CallTriggerIn, CallTriggerOut
from app.services.telephony_service import TelephonyService
from app.services.vad_service import EndOfSpeechDetector
from app.services.stt_service import SpeechService
from app.services.tts_service import VoiceService
from app.services.conversation_engine import ConversationEngine
from app.services.call_state_machine import CallStateMachine, CallState
from app.models.call_log import CallLog
from app.core.logging import logger
from app.models.user import User

router = APIRouter()

# Sentinel value pushed into the audio queue to signal "stop current playback"
_STOP_SENTINEL = b"__STOP__"


# ─────────────────────────────────────────────────────────────────────────────
# REST endpoints
# ─────────────────────────────────────────────────────────────────────────────

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

    return {"request_uuid": request_uuid, "status": call_status}


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
    <Stream bidirectional="true" keepCallAlive="true">wss://{host}/api/v1/telephony/stream/{call_uuid}</Stream>
</Response>"""
    return Response(content=xml_content, media_type="application/xml")


@router.post("/telephony/inbound")
async def plivo_inbound_webhook(request: Request):
    """Handles incoming customer support calls."""
    form_data = await request.form()
    call_uuid = form_data.get("CallUUID", f"inbound-call-{uuid.uuid4()}")
    host = request.headers.get("host", "example.com")

    xml_content = f"""<Response>
    <Speak>Thank you for calling. Please hold while we connect you to our voice assistant.</Speak>
    <Stream bidirectional="true" keepCallAlive="true">wss://{host}/api/v1/telephony/stream/{call_uuid}</Stream>
</Response>"""
    return Response(content=xml_content, media_type="application/xml")


@router.post("/telephony/status")
async def plivo_status_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db_session)
):
    """Processes Plivo call lifecycle status callbacks."""
    form_data = await request.form()
    call_uuid = form_data.get("CallUUID")
    request_uuid = form_data.get("RequestUUID")
    call_status = form_data.get("CallStatus")
    duration = int(form_data.get("Duration", 0))

    if call_uuid and call_status:
        service = TelephonyService(db)
        await service.process_status_update(call_uuid, call_status, duration, request_uuid)

    return Response(content="<Response></Response>", media_type="application/xml")


# ─────────────────────────────────────────────────────────────────────────────
# Context resolution helper
# ─────────────────────────────────────────────────────────────────────────────

async def _resolve_call_context(call_uuid: str) -> tuple[Optional[uuid.UUID], Optional[uuid.UUID]]:
    """Resolves campaign_id and customer_id with multi-tier database fallback."""
    async for db in get_db_session():
        # Tier 1: Exact match
        query = select(CallLog).where(CallLog.plivo_call_uuid == call_uuid)
        result = await db.execute(query)
        call_log = result.scalars().first()
        if call_log:
            return call_log.campaign_id, call_log.customer_id

        # Tier 2: Recent initiated/ringing — link UUID and return
        query = select(CallLog).where(
            CallLog.status.in_(["initiated", "ringing"])
        ).order_by(CallLog.created_at.desc())
        result = await db.execute(query)
        call_log = result.scalars().first()
        if call_log:
            call_log.plivo_call_uuid = call_uuid
            await db.commit()
            logger.info(f"WebSocket: Linked CallLog ID {call_log.id} to CallUUID {call_uuid}")
            return call_log.campaign_id, call_log.customer_id

        # Tier 3: Absolute fallback
        query = select(CallLog).order_by(CallLog.created_at.desc())
        result = await db.execute(query)
        call_log = result.scalars().first()
        if call_log:
            call_log.plivo_call_uuid = call_uuid
            await db.commit()
            logger.info(f"WebSocket: Fallback linked latest CallLog ID {call_log.id} to CallUUID {call_uuid}")
            return call_log.campaign_id, call_log.customer_id

        break

    return None, None


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket inner tasks
# ─────────────────────────────────────────────────────────────────────────────

async def _send_loop(
    websocket: WebSocket,
    audio_queue: asyncio.Queue,
    call_uuid: str,
    sm: CallStateMachine,
) -> None:
    """
    Drains audio_queue and sends playAudio frames to Plivo.
    Runs for the entire call duration — does NOT exit on idle timeout.

    FIX: Previously, asyncio.TimeoutError was caught outside the while loop,
    causing the send loop to exit after 30s of no TTS activity. All subsequent
    TTS frames would queue silently with nobody reading them. The call would
    appear to work (states transition) but the customer hears nothing.
    """
    try:
        while not sm.is_terminal():
            # FIX: Inner try-except so TimeoutError continues the loop rather
            # than breaking out of it. The send loop must stay alive for the
            # entire call, not just during active TTS playback.
            try:
                item = await asyncio.wait_for(audio_queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                # Normal — no TTS for 30s (customer turn, silence, etc.)
                logger.debug(f"[SEND] Queue idle for 30s on {call_uuid}, continuing.")
                continue

            if item is _STOP_SENTINEL:
                # Discard everything else currently queued (mid-stream frames)
                drained = 0
                while not audio_queue.empty():
                    audio_queue.get_nowait()
                    drained += 1
                if drained:
                    logger.debug(f"[SEND] Drained {drained} queued frames on barge-in.")

                # Tell Plivo to clear its audio buffer too
                try:
                    await websocket.send_text(json.dumps({"event": "clearAudio"}))
                    logger.info(f"[SEND] clearAudio sent to Plivo for {call_uuid}")
                except Exception:
                    pass
                continue

            if item is None:
                # None = graceful shutdown signal
                break

            payload_b64 = base64.b64encode(item).decode("utf-8")
            msg = {
                "event": "playAudio",
                "media": {
                    "contentType": "audio/x-mulaw",
                    "sampleRate": 8000,
                    "payload": payload_b64,
                },
            }
            try:
                await websocket.send_text(json.dumps(msg))
            except Exception as e:
                logger.warning(f"[SEND] WebSocket send failed: {e}")
                break

    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"[SEND] Unexpected error in send loop: {e}")


async def _run_pipeline(
    call_uuid: str,
    user_text: str,
    campaign_id: uuid.UUID,
    customer_id: uuid.UUID,
    audio_queue: asyncio.Queue,
    cancel_event: asyncio.Event,
    sm: CallStateMachine,
    llm_lock: asyncio.Lock,
) -> None:
    """
    STT result → LLM → TTS.
    Guarded by llm_lock to prevent concurrent requests.
    TTS frames are pushed into audio_queue for _send_loop to deliver.
    """

    # ── THINKING ──────────────────────────────────────────────────────────
    await sm.transition(CallState.THINKING)

    response_text: str = ""
    should_hangup: bool = False

    async with llm_lock:
        try:
            async for db in get_db_session():
                engine = ConversationEngine(db)
                response_text, should_hangup, _ = await engine.process_turn(
                    call_id=call_uuid,
                    campaign_id=campaign_id,
                    customer_id=customer_id,
                    user_text=user_text,
                )
                break
        except Exception as e:
            logger.error(f"[PIPELINE] LLM error for {call_uuid}: {e}")
            response_text = "I'm sorry, I had trouble understanding that. Could you say it again?"

    logger.info(f"[PIPELINE] LLM response: '{response_text[:120]}...'")

    if not response_text:
        logger.warning(f"[PIPELINE] Empty LLM response for {call_uuid}")
        await sm.transition(CallState.WAITING_FOR_CUSTOMER)
        return

    # ── GENERATING_RESPONSE ───────────────────────────────────────────────
    await sm.transition(CallState.GENERATING_RESPONSE)

    # ── AI_SPEAKING ───────────────────────────────────────────────────────
    tts = VoiceService()
    cancel_event.clear()  # Fresh cancellation token for this utterance
    await sm.transition(CallState.AI_SPEAKING)

    try:
        async for audio_chunk in tts.stream_speech(response_text, cancel_event=cancel_event):
            if cancel_event.is_set():
                logger.info(f"[PIPELINE] TTS cancelled mid-stream for {call_uuid}")
                break
            await audio_queue.put(audio_chunk)
    except Exception as e:
        logger.error(f"[PIPELINE] TTS streaming error: {e}")

    if not cancel_event.is_set():
        # Finished naturally — transition to waiting
        if should_hangup:
            logger.info(f"[PIPELINE] Conversation complete for {call_uuid}. Transitioning to COMPLETED.")
            await sm.transition(CallState.CALL_COMPLETED)
        else:
            await sm.transition(CallState.WAITING_FOR_CUSTOMER)
    # If cancelled, _recv_loop already transitioned to CUSTOMER_SPEAKING


# ─────────────────────────────────────────────────────────────────────────────
# Main WebSocket handler
# ─────────────────────────────────────────────────────────────────────────────

@router.websocket("/telephony/stream/{call_uuid}")
async def plivo_audio_stream_websocket(
    websocket: WebSocket,
    call_uuid: str,
) -> None:
    """
    Bidirectional Plivo media stream WebSocket.

    Runs three concurrent tasks:
      - _recv_loop: reads all incoming frames, manages VAD + utterance buffering
      - _send_loop: writes queued TTS audio frames to Plivo
      - pipeline task (spawned per utterance): STT → LLM → TTS
    """
    await websocket.accept()
    logger.info(f"[WS] WebSocket accepted for {call_uuid}")

    # ── Shared state ──────────────────────────────────────────────────────
    sm = CallStateMachine(call_uuid)
    audio_queue: asyncio.Queue = asyncio.Queue()
    llm_lock = asyncio.Lock()

    # cancel_event is set to abort current TTS stream on barge-in
    cancel_event = asyncio.Event()

    # Per-utterance audio accumulation buffer (reset on each utterance)
    utterance_buffer = bytearray()

    # VAD
    vad = EndOfSpeechDetector()
    stt = SpeechService()

    # Active pipeline task (STT→LLM→TTS)
    pipeline_task: Optional[asyncio.Task] = None

    # campaign / customer IDs resolved once on stream start
    campaign_id: Optional[uuid.UUID] = None
    customer_id: Optional[uuid.UUID] = None

    async def _barge_in() -> None:
        """Stop current AI speech and transition to CUSTOMER_SPEAKING."""
        nonlocal pipeline_task
        logger.info(f"[BARGE-IN] Customer interrupted AI speech for {call_uuid}")
        cancel_event.set()                     # Abort TTS stream
        audio_queue.put_nowait(_STOP_SENTINEL) # Clear Plivo buffer
        vad.reset()
        utterance_buffer.clear()
        if pipeline_task and not pipeline_task.done():
            pipeline_task.cancel()
            try:
                await asyncio.shield(pipeline_task)
            except (asyncio.CancelledError, Exception):
                pass
            pipeline_task = None
        await sm.transition(CallState.CUSTOMER_SPEAKING)

    async def _fire_greeting() -> None:
        """Fire the initial AI greeting on stream start."""
        nonlocal campaign_id, customer_id, pipeline_task
        campaign_id, customer_id = await _resolve_call_context(call_uuid)
        if not campaign_id or not customer_id:
            logger.error(f"[WS] Could not resolve context for {call_uuid}. Aborting.")
            await sm.transition(CallState.ERROR)
            return

        logger.info(f"[WS] Firing greeting for {call_uuid} (campaign={campaign_id})")
        pipeline_task = asyncio.create_task(
            _run_pipeline(
                call_uuid=call_uuid,
                user_text="[CALL_START]",
                campaign_id=campaign_id,
                customer_id=customer_id,
                audio_queue=audio_queue,
                cancel_event=cancel_event,
                sm=sm,
                llm_lock=llm_lock,
            )
        )

    # ── Start the send loop as a background task ──────────────────────────
    send_task = asyncio.create_task(
        _send_loop(websocket, audio_queue, call_uuid, sm)
    )

    # ── Main receive loop (runs in the foreground) ────────────────────────
    try:
        while not sm.is_terminal():
            try:
                raw_message = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=60.0  # Drop call if Plivo goes silent for 60s
                )
            except asyncio.TimeoutError:
                logger.warning(f"[WS] No data from Plivo for 60s on {call_uuid} — closing.")
                break

            data = json.loads(raw_message)
            event = data.get("event")

            # ── stream start ─────────────────────────────────────────────
            if event == "start":
                stream_info = data.get("start", {})
                logger.info(
                    f"[WS] Stream started for {call_uuid}. "
                    f"StreamID: {stream_info.get('streamId') or stream_info.get('streamSid')}"
                )
                await _fire_greeting()

            # ── incoming audio frame ──────────────────────────────────────
            elif event == "media":
                media_data = data.get("media", {})
                payload_b64 = media_data.get("payload", "")
                if not payload_b64:
                    continue

                try:
                    raw_audio = base64.b64decode(payload_b64)
                except Exception:
                    continue

                # ── If AI is currently speaking, only check for barge-in ──
                if sm.is_ai_speaking():
                    # FIX: Blanking window (1.2s) to ignore initial G.711 echo of the bot's own voice
                    loop_time = asyncio.get_event_loop().time()
                    if loop_time - sm.ai_speech_start_time > 1.2:
                        vad_event = vad.process_frame(raw_audio)
                        if vad_event == "speech_start":
                            await _barge_in()
                    else:
                        vad.reset()
                    # Do NOT accumulate utterance buffer while bot is speaking
                    continue

                # ── If in a terminal/non-listening state, ignore audio ────
                if sm.state in (
                    CallState.TRANSCRIBING,
                    CallState.THINKING,
                    CallState.GENERATING_RESPONSE,
                    CallState.CALL_COMPLETED,
                    CallState.ERROR,
                ):
                    continue

                # ── VAD processing for WAITING / CUSTOMER_SPEAKING ────────
                # FIX: Post-speech blanking window (0.6s) to ignore residual G.711 echo
                # of the bot's voice right after it stops speaking.
                loop_time = asyncio.get_event_loop().time()
                if sm.is_waiting() and (loop_time - sm.waiting_start_time < 0.6):
                    vad.reset()
                    continue

                vad_event = vad.process_frame(raw_audio)

                # FIX: Only accumulate utterance audio when customer is actively
                # speaking. Previously the buffer grew during CONNECTED and
                # WAITING_FOR_CUSTOMER states, filling it with silence + bot echo
                # before the customer said anything. Groq Whisper would then
                # transcribe that noise as empty, and the turn was dropped.
                if sm.state == CallState.CUSTOMER_SPEAKING:
                    utterance_buffer.extend(raw_audio)

                if vad_event == "speech_start":
                    if sm.is_waiting():
                        logger.info(f"[VAD] Speech start detected for {call_uuid}")
                        utterance_buffer.clear()  # Clean slate for this utterance
                        vad.reset()               # Reset VAD state cleanly
                        # Re-prime: we know we're in speech now
                        vad._in_speech = True
                        vad._speech_confirmed = True
                        await sm.transition(CallState.CUSTOMER_SPEAKING)

                elif vad_event == "speech_end":
                    if sm.state == CallState.CUSTOMER_SPEAKING:
                        logger.info(f"[VAD] End of speech — firing STT for {call_uuid}")
                        await sm.transition(CallState.TRANSCRIBING)

                        utterance_bytes = bytes(utterance_buffer)
                        utterance_buffer.clear()
                        vad.reset()

                        # FIX: Guard against duplicate pipeline tasks.
                        # If a prior task is still running (shouldn't happen but
                        # defensive), cancel it before starting a new one.
                        if pipeline_task and not pipeline_task.done():
                            logger.warning(f"[WS] Prior pipeline still running for {call_uuid} — cancelling.")
                            pipeline_task.cancel()
                            try:
                                await asyncio.shield(pipeline_task)
                            except (asyncio.CancelledError, Exception):
                                pass

                        # STT → LLM → TTS
                        async def _transcribe_and_pipeline(
                            audio: bytes,
                            _campaign_id: uuid.UUID = campaign_id,
                            _customer_id: uuid.UUID = customer_id,
                        ) -> None:
                            nonlocal pipeline_task
                            # 1. STT
                            transcript = await stt.transcribe_utterance(audio)
                            if not transcript:
                                logger.info(f"[STT] Empty transcript for {call_uuid} — returning to WAITING")
                                await sm.transition(CallState.WAITING_FOR_CUSTOMER)
                                return

                            logger.info(f"[STT] Transcript: '{transcript}' for {call_uuid}")

                            # 2. Resolve context if still missing
                            _cid = _campaign_id
                            _uid = _customer_id
                            if not _cid or not _uid:
                                _cid, _uid = await _resolve_call_context(call_uuid)
                            if not _cid or not _uid:
                                logger.error(f"[WS] Context unresolved for {call_uuid}")
                                await sm.transition(CallState.WAITING_FOR_CUSTOMER)
                                return

                            # 3. LLM → TTS
                            await _run_pipeline(
                                call_uuid=call_uuid,
                                user_text=transcript,
                                campaign_id=_cid,
                                customer_id=_uid,
                                audio_queue=audio_queue,
                                cancel_event=cancel_event,
                                sm=sm,
                                llm_lock=llm_lock,
                            )

                        pipeline_task = asyncio.create_task(
                            _transcribe_and_pipeline(utterance_bytes)
                        )

            # ── stream stop ───────────────────────────────────────────────
            elif event == "stop":
                logger.info(f"[WS] Stream stopped by Plivo for {call_uuid}")
                break

    except WebSocketDisconnect:
        logger.info(f"[WS] WebSocket disconnected for {call_uuid}")
    except Exception as e:
        logger.error(f"[WS] Unhandled exception in receive loop for {call_uuid}: {e}", exc_info=True)
    finally:
        # ── Cleanup ───────────────────────────────────────────────────────
        logger.info(f"[WS] Cleaning up tasks for {call_uuid} (final state: {sm.state.name})")

        # Cancel any in-flight pipeline task
        if pipeline_task and not pipeline_task.done():
            pipeline_task.cancel()
            try:
                await asyncio.shield(pipeline_task)
            except (asyncio.CancelledError, Exception):
                pass

        # Signal send loop to stop
        audio_queue.put_nowait(None)
        cancel_event.set()
        send_task.cancel()
        try:
            await asyncio.shield(send_task)
        except (asyncio.CancelledError, Exception):
            pass

        # Close WebSocket if still open
        try:
            await websocket.close()
        except Exception:
            pass

        logger.info(f"[WS] Session closed for {call_uuid}")
