import uuid
import json
from typing import Tuple, List, Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.session_manager import SessionManager
from app.services.llm_service import LLMService
from app.services.prompt_service import PromptService
from app.services.rag_service import RAGService
from app.repositories.call_log import CallLogRepository
from app.models.call_log import CallLog
from app.core.logging import logger

class ConversationEngine:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.session_manager = SessionManager()
        self.llm_service = LLMService()
        self.prompt_service = PromptService(db)
        self.rag_service = RAGService()
        self.call_log_repo = CallLogRepository(db)

    def _get_tools_schema(self) -> List[Dict[str, Any]]:
        """Define schemas for conversational tools available to LLaMA models."""
        return [
            {
                "type": "function",
                "function": {
                    "name": "book_appointment",
                    "description": "Schedule a customer appointment or reservation.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "date": {"type": "string", "description": "ISO date string (YYYY-MM-DD)"},
                            "time": {"type": "string", "description": "Time string (HH:MM)"}
                        },
                        "required": ["date", "time"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "transfer_to_human",
                    "description": "Transfer the call to a human operator or support representative.",
                    "parameters": {
                        "type": "object",
                        "properties": {}
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "lookup_knowledge",
                    "description": "Query the campaign knowledge database for specific business details or answers.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Specific query term"}
                        },
                        "required": ["query"]
                    }
                }
            }
        ]

    async def process_turn(
        self,
        call_id: str,
        campaign_id: uuid.UUID,
        customer_id: uuid.UUID,
        user_text: str
    ) -> Tuple[str, bool, bool]:
        """Runs the turn execution loop, resolving tool calls and outputting bot response flags."""
        history = await self.session_manager.get_message_history(call_id)
        state = await self.session_manager.get_session_state(call_id) or "greeting"
        
        # 1. Initialize session if empty
        if not history:
            # Dynamically pull prompt template and run initial RAG lookup on user text
            compiled_prompt, _ = await self.prompt_service.build_prompt(
                campaign_id=campaign_id,
                customer_id=customer_id,
                rag_query=user_text
            )
            history.append({"role": "system", "content": compiled_prompt})
            await self.session_manager.append_message(call_id, history[-1])
            
        # 2. Append user input
        # FIX: Use CALL_START marker for the greeting so the LLM greets the
        # customer instead of immediately calling tools. When user_text is "Hello"
        # the LLM tends to call book_appointment / lookup_knowledge before speaking,
        # exhausting the tool loop and returning empty content → no audio played.
        is_greeting = (user_text == "[CALL_START]")
        if is_greeting:
            # Inject a system note so the LLM knows to greet rather than act
            history.append({
                "role": "system",
                "content": (
                    "[CALL_START] The call just connected. Greet the customer warmly "
                    "by name if available, introduce yourself as the AI assistant, "
                    "and state the purpose of the call concisely. "
                    "Do NOT call any tools yet. Speak naturally as if starting a phone call."
                )
            })
            user_text_for_llm = "[Please begin with your greeting now.]"
        else:
            user_text_for_llm = user_text

        user_turn = {"role": "user", "content": user_text_for_llm}
        history.append(user_turn)
        await self.session_manager.append_message(call_id, user_turn)


        # 3. Agentic tool-call loop (max 3 iterations)
        # FIX: The old loop ran while loop_limit > 0 and decremented at the bottom.
        # When loop_limit hit 0, the while exited WITHOUT entering the `if not tool_calls`
        # block, leaving `content = None` from the last tool-call response.
        # Result: return content or "" → "", no audio, complete silence.
        #
        # New approach:
        # - If the LLM returns tool_calls, execute them and loop.
        # - If it returns text content, break immediately.
        # - If the loop exhausts (LLM kept calling tools for 3 rounds with no text),
        #   make ONE final forced completion call WITHOUT tools to guarantee text output.
        should_hangup = False
        should_transfer = False
        loop_limit = 3
        content: Optional[str] = None
        made_tool_calls = False
        # Greeting must never call tools — forces plain spoken text immediately
        active_tools = None if is_greeting else self._get_tools_schema()


        while loop_limit > 0:
            content, tool_calls = await self.llm_service.generate_completion(history, active_tools)


            if not tool_calls:
                # LLM returned plain text — store and break
                if content:
                    bot_turn = {"role": "assistant", "content": content}
                    history.append(bot_turn)
                    await self.session_manager.append_message(call_id, bot_turn)
                break

            # LLM returned tool calls — execute them
            made_tool_calls = True
            tool_calls_message = {
                "role": "assistant",
                "content": None,
                "tool_calls": tool_calls
            }
            history.append(tool_calls_message)
            await self.session_manager.append_message(call_id, tool_calls_message)

            for tool_call in tool_calls:
                tool_id = tool_call.get("id")
                func_data = tool_call.get("function", {})
                func_name = func_data.get("name")
                args = {}
                try:
                    args = json.loads(func_data.get("arguments", "{}"))
                except Exception:
                    pass

                tool_result_content = ""

                if func_name == "book_appointment":
                    state = "appointment_booked"
                    await self.session_manager.update_session_state(call_id, state)
                    tool_result_content = f"Appointment successfully scheduled for {args.get('date')} at {args.get('time')}."

                elif func_name == "transfer_to_human":
                    state = "escalated"
                    await self.session_manager.update_session_state(call_id, state)
                    should_transfer = True
                    tool_result_content = "Call transfer successfully initiated."

                elif func_name == "lookup_knowledge":
                    query = args.get("query", "")
                    facts = await self.rag_service.search_knowledge(campaign_id, query, limit=2)
                    facts_list = [f["text"] for f in facts]
                    tool_result_content = json.dumps({"facts": facts_list})

                else:
                    tool_result_content = f"Error: Tool '{func_name}' not implemented."

                tool_response = {
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "name": func_name,
                    "content": tool_result_content
                }
                history.append(tool_response)
                await self.session_manager.append_message(call_id, tool_response)

            loop_limit -= 1

        # FIX: If the tool loop exhausted and content is still empty, make one
        # final forced completion WITHOUT tools. This guarantees the bot always
        # produces spoken text after executing tools.
        if made_tool_calls and not content:
            logger.warning(f"[ENGINE] Tool loop exhausted without text for {call_id}. Forcing final completion.")
            content, _ = await self.llm_service.generate_completion(history, tools=None)
            if content:
                bot_turn = {"role": "assistant", "content": content}
                history.append(bot_turn)
                await self.session_manager.append_message(call_id, bot_turn)
            else:
                content = "I've taken care of that for you. Is there anything else I can help you with?"

            
        # Hangup detection — strict conditions to prevent premature termination:
        # 1. Require at least 6 messages in history (system + 2+ exchanges) before considering hangup
        # 2. Use unambiguous, complete farewell phrases only
        # 3. State must be explicitly "completed" by a tool call
        import re
        low_content = (content or "").lower()
        assistant_turns = sum(1 for m in history if m.get("role") == "assistant")
        FAREWELL_RE = re.compile(
            r'\b(goodbye for now|have a great day|take care, goodbye|'
            r'thanks for calling, goodbye|thank you for calling, goodbye|'
            r'have a wonderful day|is there anything else before we go)\b'
        )
        if FAREWELL_RE.search(low_content) and assistant_turns >= 2:
            should_hangup = True
        elif state == "completed":
            should_hangup = True

        if state == "escalated":
            should_transfer = True

        return content or "", should_hangup, should_transfer

    async def end_call(
        self,
        call_id: str,
        campaign_id: uuid.UUID,
        customer_id: uuid.UUID,
        phone_number: str,
        duration_seconds: int
    ) -> CallLog:
        """Purge active Redis memory keys and flush completed conversation transcripts to PostgreSQL."""
        history = await self.session_manager.get_message_history(call_id)
        state = await self.session_manager.get_session_state(call_id) or "completed"
        
        # Convert message list format to DB-friendly JSON list
        exchanges = []
        for msg in history:
            role = msg.get("role")
            content = msg.get("content")
            if role in ["user", "assistant"] and content:
                exchanges.append({
                    "sender": "customer" if role == "user" else "agent",
                    "text": content
                })
                
        # Status calculation
        status_val = "completed"
        if state == "escalated":
            status_val = "completed"  # Escalated is treated as completed handoff
        elif not exchanges:
            status_val = "failed"
            
        call_log = CallLog(
            campaign_id=campaign_id,
            customer_id=customer_id,
            plivo_call_uuid=call_id,
            phone_number=phone_number,
            status=status_val,
            duration_seconds=duration_seconds,
            transcript=exchanges
        )
        
        created_log = await self.call_log_repo.create(call_log)
        await self.db.commit()
        
        # Purge Redis keys
        await self.session_manager.clear_session(call_id)
        return created_log
