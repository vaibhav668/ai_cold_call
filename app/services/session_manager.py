import json
from typing import List, Dict, Optional
from app.core.logging import logger

_in_memory_states: Dict[str, str] = {}
_in_memory_messages: Dict[str, List[Dict[str, str]]] = {}

class SessionManager:
    def __init__(self) -> None:
        pass

    async def get_session_state(self, call_id: str) -> Optional[str]:
        """Fetch call current node from local Python in-memory storage."""
        return _in_memory_states.get(call_id)

    async def update_session_state(self, call_id: str, state: str) -> None:
        """Set call current state in local Python in-memory storage."""
        _in_memory_states[call_id] = state

    async def get_message_history(self, call_id: str) -> List[Dict[str, str]]:
        """Fetch list of message dictionary exchanges from local Python in-memory storage."""
        return _in_memory_messages.get(call_id, [])

    async def append_message(self, call_id: str, message: Dict[str, str]) -> None:
        """Append message turn to session history in local Python in-memory storage."""
        if call_id not in _in_memory_messages:
            _in_memory_messages[call_id] = []
        _in_memory_messages[call_id].append(message)

    async def clear_session(self, call_id: str) -> None:
        """Remove call configuration records and purge local memory session cache."""
        _in_memory_states.pop(call_id, None)
        _in_memory_messages.pop(call_id, None)
