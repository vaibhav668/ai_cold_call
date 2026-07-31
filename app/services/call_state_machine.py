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
    THINKING               — STT returned text; LLM request in flight
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
_VALID_TRANSITIONS: dict[CallState, set[CallState]] = {
    CallState.CONNECTED: {
        CallState.AI_SPEAKING,   # greeting
        CallState.WAITING_FOR_CUSTOMER,
        CallState.ERROR,
    },
    CallState.AI_SPEAKING: {
        CallState.WAITING_FOR_CUSTOMER,   # finished normally
        CallState.CUSTOMER_SPEAKING,      # barge-in
        CallState.CALL_COMPLETED,
        CallState.ERROR,
    },
    CallState.WAITING_FOR_CUSTOMER: {
        CallState.CUSTOMER_SPEAKING,
        CallState.CALL_COMPLETED,
        CallState.ERROR,
    },
    CallState.CUSTOMER_SPEAKING: {
        CallState.TRANSCRIBING,           # silence timeout → end-of-speech
        CallState.WAITING_FOR_CUSTOMER,   # very short noise / click
        CallState.ERROR,
    },
    CallState.TRANSCRIBING: {
        CallState.THINKING,
        CallState.WAITING_FOR_CUSTOMER,   # STT returned empty / silence token
        CallState.ERROR,
    },
    CallState.THINKING: {
        CallState.GENERATING_RESPONSE,
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
        logger.info(f"[STATE] {call_uuid} → CONNECTED")

    @property
    def state(self) -> CallState:
        return self._state

    async def transition(self, new_state: CallState) -> bool:
        """
        Attempt a state transition.

        Returns True if transition succeeded, False if it was invalid.
        Invalid transitions are logged but do NOT raise exceptions — the
        caller is responsible for handling a False return.
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
            logger.info(
                f"[STATE] {self.call_uuid} {old_state.name} → {new_state.name}"
            )
            return True

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
