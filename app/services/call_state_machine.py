"""
Call State Machine
==================
Explicit state management for one active phone call.
Every transition is logged with structured context.

States:
    CONNECTED              — WebSocket established, resolving context
    AI_SPEAKING            — TTS frames are being sent to Plivo
    WAITING_FOR_CUSTOMER   — AI finished, listening for customer to start speaking
    CUSTOMER_SPEAKING      — VAD has detected active customer speech
    TRANSCRIBING           — VAD silence timeout hit; sending utterance to STT
    THINKING               — LLM request in flight (STT result OR greeting "Hello")
    GENERATING_RESPONSE    — LLM returned text; TTS synthesis in progress
    CALL_COMPLETED         — Conversation naturally ended; ready to hangup
    ERROR                  — Unrecoverable error; call should be terminated
"""

import asyncio
from enum import Enum, auto
from app.core.logging import logger


class CallState(Enum):
    CONNECTED = auto()
    AI_SPEAKING = auto()
    WAITING_FOR_CUSTOMER = auto()
    CUSTOMER_SPEAKING = auto()
    TRANSCRIBING = auto()
    THINKING = auto()
    GENERATING_RESPONSE = auto()
    CALL_COMPLETED = auto()
    ERROR = auto()


# Valid transitions: state → set of allowed next states
#
# FIX: CONNECTED must allow THINKING and GENERATING_RESPONSE because
# _run_pipeline is shared between the greeting and regular turns.
# The greeting pipeline enters: CONNECTED → THINKING → GENERATING_RESPONSE → AI_SPEAKING.
_VALID_TRANSITIONS: dict[CallState, set[CallState]] = {
    CallState.CONNECTED: {
        CallState.THINKING,             # greeting — LLM call
        CallState.GENERATING_RESPONSE,  # greeting — TTS synthesis
        CallState.AI_SPEAKING,          # greeting — start speaking
        CallState.WAITING_FOR_CUSTOMER, # context resolution failed gracefully
        CallState.ERROR,
    },
    CallState.AI_SPEAKING: {
        CallState.WAITING_FOR_CUSTOMER,   # finished naturally
        CallState.CUSTOMER_SPEAKING,      # barge-in detected
        CallState.CALL_COMPLETED,
        CallState.ERROR,
    },
    CallState.WAITING_FOR_CUSTOMER: {
        CallState.CUSTOMER_SPEAKING,
        CallState.THINKING,              # pipeline re-entry when rapid speech follows
        CallState.CALL_COMPLETED,
        CallState.ERROR,
    },
    CallState.CUSTOMER_SPEAKING: {
        CallState.TRANSCRIBING,           # silence timeout → end-of-speech
        CallState.WAITING_FOR_CUSTOMER,   # very short noise / click (spurious)
        CallState.ERROR,
    },
    CallState.TRANSCRIBING: {
        CallState.THINKING,
        CallState.WAITING_FOR_CUSTOMER,   # STT returned empty / silence token
        CallState.ERROR,
    },
    CallState.THINKING: {
        CallState.GENERATING_RESPONSE,
        CallState.THINKING,               # rapid re-entry: new utterance while already thinking
        CallState.WAITING_FOR_CUSTOMER,   # LLM returned empty
        CallState.ERROR,
    },
    CallState.GENERATING_RESPONSE: {
        CallState.AI_SPEAKING,
        CallState.WAITING_FOR_CUSTOMER,   # TTS returned no audio
        CallState.CALL_COMPLETED,
        CallState.ERROR,
    },
    CallState.CALL_COMPLETED: set(),  # terminal
    CallState.ERROR: set(),           # terminal
}


class CallStateMachine:
    """
    Thread-safe call state machine.

    Usage:
        sm = CallStateMachine(call_uuid)
        await sm.transition(CallState.AI_SPEAKING)
    """

    def __init__(self, call_uuid: str) -> None:
        self.call_uuid = call_uuid
        self._state = CallState.CONNECTED
        self._lock = asyncio.Lock()
        self.ai_speech_start_time = 0.0
        self.waiting_start_time = 0.0
        logger.info(f"[STATE] {call_uuid} → CONNECTED")

    @property
    def state(self) -> CallState:
        return self._state

    async def transition(self, new_state: CallState) -> bool:
        """
        Attempt a state transition.

        Returns True if transition succeeded, False if it was invalid.
        Invalid transitions are logged but do NOT raise — caller handles False.
        """
        async with self._lock:
            allowed = _VALID_TRANSITIONS.get(self._state, set())
            if new_state not in allowed:
                logger.warning(
                    f"[STATE] {self.call_uuid} INVALID transition "
                    f"{self._state.name} → {new_state.name} (ignored)"
                )
                return False

            old_state = self._state
            self._state = new_state
            
            # Track timestamps for echo blanking windows
            loop_time = asyncio.get_event_loop().time()
            if new_state == CallState.AI_SPEAKING:
                self.ai_speech_start_time = 999999999.0  # Prime with safe placeholder: send loop sets actual play time
            elif new_state == CallState.WAITING_FOR_CUSTOMER:
                self.waiting_start_time = loop_time

            logger.info(
                f"[STATE] {self.call_uuid} {old_state.name} → {new_state.name}"
            )
            return True

    def force(self, new_state: CallState) -> None:
        """
        Unconditionally set state (no lock, no validation).
        Use only in cleanup/error paths where we cannot await.
        """
        self._state = new_state

    def is_terminal(self) -> bool:
        return self._state in (CallState.CALL_COMPLETED, CallState.ERROR)

    def is_ai_speaking(self) -> bool:
        return self._state == CallState.AI_SPEAKING

    def is_waiting(self) -> bool:
        return self._state == CallState.WAITING_FOR_CUSTOMER

    def is_customer_turn(self) -> bool:
        return self._state in (
            CallState.CUSTOMER_SPEAKING,
            CallState.TRANSCRIBING,
            CallState.THINKING,
            CallState.GENERATING_RESPONSE,
        )
