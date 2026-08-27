import uuid
import json
import re
import asyncio
from typing import Tuple, List, Dict, Any, Optional, AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.session_manager import SessionManager
from app.services.llm_service import LLMService
from app.services.prompt_service import PromptService
from app.services.rag_service import RAGService
HAS_DB = True
try:
    from app.repositories.call_log import CallLogRepository
    from app.models.call_log import CallLog
except ImportError:
    HAS_DB = False
    CallLogRepository = None
    CallLog = None

from app.core.logging import logger

class ConversationEngine:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.session_manager = SessionManager()
        self.llm_service = LLMService()
        self.prompt_service = PromptService(db)
        self.rag_service = RAGService()
        self.call_log_repo = CallLogRepository(db) if CallLogRepository is not None else None

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
            },
            {
                "type": "function",
                "function": {
                    "name": "confirm_appointment",
                    "description": "Confirm the customer is attending the scheduled appointment.",
                    "parameters": {
                        "type": "object",
                        "properties": {}
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "reschedule_appointment",
                    "description": "Trigger the rescheduling workflow when a customer explicitly requests a change.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "new_date": {"type": "string", "description": "Proposed new date (YYYY-MM-DD)"},
                            "new_time": {"type": "string", "description": "Proposed new time (HH:MM)"}
                        },
                        "required": ["new_date", "new_time"]
                    }
                }
            }
        ]

    async def process_turn_stream(
        self,
        call_id: str,
        campaign_id: uuid.UUID,
        customer_id: uuid.UUID,
        user_text: str
    ) -> AsyncGenerator[Tuple[Optional[str], bool, bool], None]:
        """
        Streaming turn execution loop.
        Yields (text_token, should_hangup, should_transfer) progressively.
        """
        history = await self.session_manager.get_message_history(call_id)
        state = await self.session_manager.get_session_state(call_id) or "greeting"

        # 1. Initialize session if empty
        if not history:
            compiled_prompt, _ = await self.prompt_service.build_prompt(
                campaign_id=campaign_id,
                customer_id=customer_id,
                rag_query=user_text,
                session_id=call_id
            )
            metadata = await self.session_manager.get_session_metadata(call_id)
            if metadata and "language" in metadata:
                lang = metadata["language"]
                if lang == "Hindi":
                    compiled_prompt += (
                        "\n\n### LANGUAGE GUIDELINE\n"
                        "IMPORTANT: Speak only in Hindi. Translate all concepts, questions, and responses to Hindi naturally. "
                        "Do NOT use English or Roman script. Use Devanagari script for output."
                    )
                elif lang == "Telugu":
                    compiled_prompt += (
                        "\n\n### LANGUAGE GUIDELINE\n"
                        "IMPORTANT: Speak only in Telugu. Translate all concepts, questions, and responses to Telugu naturally. "
                        "Do NOT use English or Roman script. Use Telugu script for output."
                    )
            history.append({"role": "system", "content": compiled_prompt})
            await self.session_manager.append_message(call_id, history[-1])

        # 2. Append user input
        is_greeting = (user_text == "[CALL_START]")
        if is_greeting:
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

        # 3. Agentic tool-call loop
        should_hangup = False
        should_transfer = False
        loop_limit = 3
        full_content_accumulator = []
        active_tools = None if is_greeting else self._get_tools_schema()

        while loop_limit > 0:
            tool_calls_detected = None

            async for text_chunk, t_calls in self.llm_service.generate_completion_stream(history, active_tools):
                if t_calls:
                    tool_calls_detected = t_calls
                    break
                if text_chunk:
                    full_content_accumulator.append(text_chunk)
                    yield text_chunk, False, False

            if not tool_calls_detected:
                # Normal text response complete
                break

            # LLM requested tool execution
            tool_calls_message = {
                "role": "assistant",
                "content": None,
                "tool_calls": tool_calls_detected
            }
            history.append(tool_calls_message)
            await self.session_manager.append_message(call_id, tool_calls_message)

            for tool_call in tool_calls_detected:
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
                elif func_name == "confirm_appointment":
                    state = "appointment_confirmed"
                    await self.session_manager.update_session_state(call_id, state)
                    tool_result_content = "Appointment successfully confirmed in the database."
                elif func_name == "reschedule_appointment":
                    state = "appointment_rescheduled"
                    await self.session_manager.update_session_state(call_id, state)
                    tool_result_content = f"Appointment rescheduled successfully for {args.get('new_date')} at {args.get('new_time')}."
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

        full_text = "".join(full_content_accumulator).strip()
        if full_text:
            bot_turn = {"role": "assistant", "content": full_text}
            history.append(bot_turn)
            await self.session_manager.append_message(call_id, bot_turn)

        # Evaluate hangup condition
        low_content = full_text.lower()
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

        yield None, should_hangup, should_transfer

    async def process_turn(
        self,
        call_id: str,
        campaign_id: uuid.UUID,
        customer_id: uuid.UUID,
        user_text: str
    ) -> Tuple[str, bool, bool]:
        """Legacy turn execution helper that collects stream output into a single string."""
        accumulated_text = []
        final_hangup = False
        final_transfer = False
        async for chunk, h, t in self.process_turn_stream(call_id, campaign_id, customer_id, user_text):
            if chunk:
                accumulated_text.append(chunk)
            if h:
                final_hangup = True
            if t:
                final_transfer = True
        return "".join(accumulated_text), final_hangup, final_transfer

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
        
        exchanges = []
        for msg in history:
            role = msg.get("role")
            content = msg.get("content")
            if role in ["user", "assistant"] and content:
                exchanges.append({
                    "sender": "customer" if role == "user" else "agent",
                    "text": content
                })
                
        status_val = "completed"
        if state == "escalated":
            status_val = "completed"
        elif not exchanges:
            status_val = "failed"
            
        existing_log = await self.call_log_repo.get_by_plivo_uuid(call_id)
        if existing_log:
            updated_log = await self.call_log_repo.update(existing_log, {
                "status": status_val,
                "duration_seconds": duration_seconds,
                "transcript": exchanges
            })
            await self.db.commit()
            await self.session_manager.clear_session(call_id)
            return updated_log
            
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
        await self.session_manager.clear_session(call_id)
        return created_log


# ─────────────────────────────────────────────────────────────────────────────
# VOICE DEMO WEBSOCKET PIPELINE SUPPORT STRUCTURES
# ─────────────────────────────────────────────────────────────────────────────

TEMPLATES = {
    "hospital": {
        "HOSPITAL_GREETING": {
            "en": "Hi! This is {agent_name} calling from CityCare Hospital... May I know whom I'm speaking with?",
            "hi": "नमस्ते, मैं सिटीकेयर हॉस्पिटल से {agent_name} बात कर रही हूँ। क्या मैं आपका नाम जान सकती हूँ?",
            "te": "నమస్కారం, నేను సిటీకేర్ హాస్పిటల్ నుండి {agent_name} మాట్లాడుతున్నాను. మీ పేరు తెలుసుకోవచ్చా?"
        },
        "HOSPITAL_PURPOSE": {
            "en": "Hello {customer_name}! I'm calling to confirm your doctor's appointment scheduled for tomorrow... Would you like to confirm, reschedule, or cancel it?",
            "hi": "नमस्ते {customer_name}। मैं कल के लिए निर्धारित आपके डॉक्टर के अपॉइंटमेंट की पुष्टि करने के लिए कॉल कर रही हूँ। क्या आप इसकी पुष्टि करना चाहते हैं, इसे रीशेड्यूल करना चाहते हैं या कैंसिल करना चाहते हैं?",
            "te": "నమస్కారం {customer_name}. రేపటి మీ డాక్టర్ అపాయింట్‌మెంట్‌ను కన్ఫర్మ్ చేయడానికి నేను కాల్ చేస్తున్నాను. మీరు దానిని కన్ఫర్మ్ చేయాలనుకుంటున్నారా, రీషెడ్యూల్ చేయాలనుకుంటున్నారా లేదా రద్దు చేయాలనుకుంటున్నారా?"
        },
        "CONFIRM": {
            "en": "Thank you, {customer_name}! Your appointment is confirmed... We look forward to seeing you tomorrow. Goodbye!",
            "hi": "धन्यवाद {customer_name}। आपका अपॉइंटमेंट कन्फर्म हो गया है। हम कल आपसे मिलने की उम्मीद करते हैं। नमस्ते!",
            "te": "ధన్యవాదాలు {customer_name}. మీ అపాయింట్‌మెంట్ కన్ఫర్మ్ చేయబడింది. రేపు మిమ్మల్ని కలవడానికి మేము ఎదురుచూస్తున్నాము. సెలవు!"
        },
        "CANCEL": {
            "en": "No problem, {customer_name}. Your appointment has been cancelled... If you need to book again, feel free to call us. Goodbye!",
            "hi": "कोई बात नहीं {customer_name}। आपका अपॉइंटमेंट कैंसिल कर दिया गया है। अगर आपको फिर से बुक करना हो, तो हमें कॉल कर सकते हैं। नमस्ते!",
            "te": "పర్వాలేదండి {customer_name}. మీ అపాయింట్‌మెంట్ రద్దు చేయబడింది. మీరు మళ్లీ బుక్ చేసుకోవాలనుకుంటే, నిస్సంకోచంగా మాకు కాల్ చేయండి. సెలవు!"
        },
        "RESCHEDULE": {
            "en": "Sure. What slot or day would you prefer for your rescheduled appointment?",
            "hi": "ज़रूर। आप अपने रीशेड्यूल किए गए अपॉइंटमेंट के लिए कौन सा समय या दिन पसंद करेंगे?",
            "te": "సరే. మీ రీషెడ్యూల్డ్ అపాయింట్‌మెంట్ కోసం ఏ సమయం లేదా రోజు అనుకూలంగా ఉంటుంది?"
        },
        "RESCHEDULE_CONFIRM": {
            "en": "Perfect! Your appointment has been rescheduled to {slot}... We will send you the details. Goodbye!",
            "hi": "बढ़िया! आपका अपॉइंटमेंट {slot} के लिए रीशेड्यूल कर दिया गया है। हम आपको सारी details भेज देंगे। नमस्ते!",
            "te": "చాలా సంతోషం! మీ అపాయింట్‌మెంట్ {slot} కి రీషెడ్యూల్ చేయబడింది. మేము మీకు పూర్తి వివరాలు పంపిస్తాము. సెలవు!"
        },
        "UNCLEAR": {
            "en": "Just regarding your appointment, would you like to confirm, reschedule, or cancel?",
            "hi": "बस आपके अपॉइंटमेंट के बारे में, क्या आप इसकी पुष्टि करना चाहते हैं, इसे रीशेड्यूल करना चाहते हैं या कैंसिल करना चाहते हैं?",
            "te": "మీ అపాయింట్‌మెంట్ గురించి, మీరు దానిని కన్ఫర్మ్ చేయాలనుకుంటున్నారా, రీషెడ్యూల్ చేయాలనుకుంటున్నారా లేదా రద్దు చేయాలనుకుంటున్నారా?"
        },
        "CLOSING": {
            "en": "Thank you for your time. Goodbye!",
            "hi": "समय देने के लिए धन्यवाद। नमस्ते!",
            "te": "ధన్యవాదాలు. సెలవు!"
        },
        "REDIRECT_SMALL_TALK": {
            "en": "I'm doing well, thank you! Just regarding your doctor's appointment scheduled for tomorrow, would you like to confirm, reschedule, or cancel it?",
            "hi": "मैं ठीक हूँ, धन्यवाद! बस कल के आपके डॉक्टर के अपॉइंटमेंट के संबंध में, क्या आप इसकी पुष्टि करना चाहते हैं, इसे रीशगेड्यूल करना चाहते हैं या कैंसिल करना चाहते हैं?",
            "te": "నేను బాగున్నాను, ధన్యవాదాలు! రేపటి మీ డాక్టర్ అపాయింట్‌మెంట్‌కు సంబంధించి, మీరు దానిని కన్ఫర్మ్ చేయాలనుకుంటున్నారా, రీషెడ్యూల్ చేయాలనుకుంటున్నారా లేదా రద్దు చేయాలనుకుంటున్నారా?"
        },
        "RECOVERY": {
            "en": "Sorry, I didn't catch your name. Could you please repeat your name?",
            "hi": "क्षमा करें, मुझे आपका नाम समझ नहीं आया। क्या आप अपना नाम दोहरा सकते हैं?",
            "te": "క్షమించండి, మీ పేరు నాకు స్పష్టంగా వినపడలేదు. మరోసారి మీ పేరు చెప్పగలరా?"
        }
    },
    "real_estate": {
        "RE_GREETING": {
            "en": "Hello... May I know whom I am speaking with?",
            "hi": "नमस्ते! क्या मैं जान सकती हूँ कि मैं किससे बात कर रही हूँ?",
            "te": "నమస్కారం! నేను ఎవరితో మాట్లాడుతున్నానో తెలుసుకోవచ్చా?"
        },
        "RE_PURPOSE_INTRO": {
            "en": "Hi {customer_name}! I'm calling from Skyline Developers... We have a 2 BHK at 80 Lakhs, a 3 BHK at 1.2 Crores, and a penthouse at 2.5 Crores... Which interests you?",
            "hi": "नमस्ते {customer_name}, बात करने के लिए धन्यवाद। मैं Skyline Developers से कॉल कर रही हूँ। हमारे पास वर्तमान में तीन प्रीमियम विकल्प उपलब्ध हैं: पहला, 80 लाख में Skyline Heights पर एक लग्जरी 2 BHK; दूसरा, 1.2 करोड़ में Skyline Residency पर एक प्रीमियम 3 BHK; और तीसरा, 2.5 करोड़ में Skyline Towers पर एक डुप्लेक्स पेंटहाउस। आप इनमें से कौन सा विकल्प पसंद करेंगे?",
            "te": "నమస్కారం {customer_name}, మాట్లాడినందుకు ధన్యవాదాలు. నేను Skyline Developers నుండి కాల్ చేస్తున్నాను. మా వద్ద ప్రస్తుతం మూడు ప్రీమియం ఆప్షన్‌లు ఉన్నాయి: మొదటిది, 80 లక్షలకు Skyline Heights వద్ద లగ్జరీ 2 BHK; రెండవది, 1.2 కోట్లకు Skyline Residency వద్ద ప్రీమియం 3 BHK; మరియు మూడవది, 2.5 కోట్లకు Skyline Towers వద్ద డ్యూప్లెక్స్ పెంట్‌హౌస్. మీరు వీటిలో ఏది ఎంచుకుంటారు?"
        },
        "RE_PROPERTY_PITCH": {
            "en": "Great! Skyline Residency features premium luxury spaces with state-of-the-art amenities. Are you currently looking to buy or invest in a property?",
            "hi": "बढ़िया! Gachibowli में Skyline Residency project में 2 और 3 BHK flats 80 लाख से शुरू हैं। क्या आप अभी कोई flat खरीदने या invest करने की सोच रहे हैं?",
            "te": "చాలా మంచిది! గచ్చిబౌలిలో Skyline Residency ప్రాజెక్ట్‌లో 2 & 3 BHK అపార్ట్‌మెంట్‌లు 80 లక్షల నుండి అందుబాటులో ఉన్నాయి. మీరు ఇల్లు కొనడానికి ఆసక్తి చూపుతున్నారా?"
        },
        "RE_INTEREST_QUESTION": {
            "en": "Awesome! What kind of property type and budget range are you considering?",
            "hi": "बढ़िया! आप किस तरह की property और budget range की सोच रहे हैं?",
            "te": "చాలా సంతోషం! మీ బడ్జెట్ మరియు ఎలాంటి ఇల్లు కావాలనుకుంటున్నారో చెప్పగలరా?"
        },
        "RE_RECOMMENDATION": {
            "en": "Based on your requirements, I would highly recommend our premium 2 BHK apartment in Skyline Residency. Would you be interested in booking a site visit to check it out?",
            "hi": "आपकी requirement के हिसाब से, मैं आपको Skyline Residency में हमारा 2 BHK flat recommend करूँगी। क्या आप इसे देखने के लिए एक site visit book करना चाहेंगे?",
            "te": "మీ రిక్వైర్‌మెంట్ ప్రకారం, నేను Skyline Residency లోని మా 2 BHK అపార్ట్‌మెంట్‌ను రికమండ్ చేస్తాను. దానిని చూడటానికి సైట్ విజిట్ బుక్ చేయాలనుకుంటున్నారా?"
        },
        "RE_SITE_VISIT_CONFIRM": {
            "en": "Perfect! Your site visit is confirmed... We will send you the details soon. Goodbye!",
            "hi": "परफेक्ट! हमने आपकी पसंद की प्रॉपर्टी देखने के लिए एक साइट विजिट बुक कर दी है। हम आपको जल्द ही सारी डिटेल्स भेज देंगे। समय देने के लिए धन्यवाद। नमस्ते!",
            "te": "చాలా సంతోషం! మీరు కోరుకున్న ప్రాపర్టీని చూడటానికి సైట్ విజిట్ బుక్ చేయబడింది. మేము మీకు త్వరలోనే పూర్తి వివరాలు పంపిస్తాము. మీ సమయానికి ధన్యవాదాలు. సెలవు!"
        },
        "RE_SITE_VISIT_DECLINE": {
            "en": "No problem. Thanks for your time, {customer_name}. Have a great day. Goodbye!",
            "hi": "कोई बात नहीं। समय देने के लिए धन्यवाद, {customer_name}। आपका दिन शुभ हो। नमस्ते!",
            "te": "పర్వాలేదండి. మీ సమయానికి ధన్యవాదాలు, {customer_name}. మంచి రోజు అవ్వాలని కోరుకుంటున్నాను. సెలవు!"
        },
        "NOT_INTERESTED": {
            "en": "No problem at all. Thanks for your time, {customer_name}. Have a great day. Goodbye!",
            "hi": "कोई बात नहीं। समय देने के लिए धन्यवाद, {customer_name}। आपका दिन शुभ हो। नमस्ते!",
            "te": "పర్వాలేదండి. మీ సమయానికి ధన్యవాదాలు, {customer_name}. మంచి రోజు అవ్వాలని కోరుకుంటున్నాను. సెలవు!"
        },
        "BUSY": {
            "en": "No problem. Thanks for your time. Goodbye!",
            "hi": "कोई बात नहीं। समय देने के लिए धन्यवाद। नमस्ते!",
            "te": "పర్వాలేదండి. మీ సమయానికి ధన్యవాదాలు. సెలవు!"
        },
        "RECOVERY": {
            "en": "Sorry, I didn't catch your name. Could you please repeat your name?",
            "hi": "क्षमा करें, मुझे आपका नाम समझ नहीं आया। क्या आप अपना नाम दोहरा सकते हैं?",
            "te": "क्षमించండి, మీ పేరు నాకు స్పష్టంగా వినపడలేదు. మరోసారి మీ పేరు చెప్పగలరా?"
        }
    }
}


def clean_speech_text(text: str) -> str:
    """Sanitize output text to ensure pure direct speech for TTS, preserving spaces."""
    if not text:
        return ""
    text = text.replace("**", "").replace("*", "")
    text = re.sub(r'#+\s+', '', text)
    text = text.replace("`", "")
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\[[^\]]+\]', '', text)
    return text


def normalize_name_transcript(text: str) -> str:
    """Clean and normalize transcript text for name slot extraction."""
    if not text:
        return ""
    return text.strip().rstrip(".,!?।")


def extract_customer_name_from_text(text: str, language: str = "en") -> Optional[str]:
    """
    Consolidated, language-aware customer name extractor.
    Supports English, Hindi, Hinglish, and Telugu patterns.
    """
    if not text:
        return None
        
    raw = text.strip().rstrip(".,!?।")
    
    INVALID_WORDS = {
        "unknown", "none", "null", "undefined", "n/a", "user", "customer", 
        "my gosh", "in the car", "my car", "gosh", "yes", "no", "hello", "hi", "ok", "okay",
        "go", "let's", "lets", "let", "come", "start", "see", "look", "show", "tell", "give", "speak", "speaking", "talk", "hear", "listen",
        "sophia", "maya", "ananya", "arjun", "david", "sharma", "sharma's", "please", "today", "tomorrow",
        "mera", "meri", "mere", "naam", "name", "hai", "hoon", "hu", "haan", "nahi",
        "appointment", "reschedule", "confirm", "cancel", "hospital", "doctor",
        "i", "i'm", "my", "this", "it's", "it", "myself", "am", "called", "here", "hey",
        "a", "an", "the", "and", "or", "is", "are", "me", "we", "you", "he", "she", "they",
        "మీరు", "నా", "పేరు", "నమస్కారం", "అవును", "సరే", "ధన్యవాదాలు", "మాట్లాడుతున్నాను", "నేను",
        "మరియు", "లేదా", "ఉంది", "ఉన్నారు"
    }

    words_in_text = re.findall(r"[A-Za-z\u0900-\u097F\u0C00-\u0C7F]+", raw)
    
    candidate_words = []
    for w in words_in_text:
        w_clean = w.strip().rstrip(".,!?।")
        if w_clean.lower() not in INVALID_WORDS and len(w_clean) >= 2:
            candidate_words.append(w_clean)
            
    if len(set(w.lower() for w in candidate_words)) > 2:
        logger.warning(f"[NAME-EXTRACTION] Rejected: Multiple distinct name candidates found {set(candidate_words)}")
        return None

    def clean_name(name_str: str) -> Optional[str]:
        words = name_str.strip().split()
        cleaned_words = []
        for w in words:
            w_clean = w.strip().rstrip(".,!?।")
            if w_clean.lower() not in INVALID_WORDS and len(w_clean) >= 2:
                cleaned_words.append(w_clean)
        if cleaned_words:
            return " ".join(w.title() for w in cleaned_words[:2])
        return None

    # Regex patterns
    en_patterns = [
        r"\b(?:my name is|i'm|i am|this is|myself|call me)\s+([A-Za-z\s]+)",
        r"\b([A-Za-z\s]+)\s+(?:speaking|here|this side)",
        r"\b(?:mera name|mera naam|main|naam|naam hai)\s+([A-Za-z\s]+)"
    ]
    
    for pat in en_patterns:
        match = re.search(pat, raw, re.IGNORECASE)
        if match:
            cand = clean_name(match.group(1))
            if cand:
                return cand

    hi_patterns = [
        r"(?:मेरा नाम|नाम)\s+([\u0900-\u097F\sA-Za-z]+)",
        r"(?:मैं|मै)\s+([\u0900-\u097F\sA-Za-z]+)\s+(?:बोल\s+रहा|बोल\s+रही|बात\s+कर)",
        r"([\u0900-\u097F\sA-Za-z]+)\s+(?:बोल\s+रहा|बोल\s+रही|बात\s+कर|बोलता|बोलती)"
    ]
    for pat in hi_patterns:
        match = re.search(pat, raw, re.IGNORECASE)
        if match:
            cand_raw = match.group(1)
            for stop in ["है", "हूँ", "जी", "बात", "रहा", "रही", "बोल", "hoon", "hai"]:
                cand_raw = re.sub(rf"\s+{stop}\b", "", cand_raw, flags=re.IGNORECASE).strip()
            cand = clean_name(cand_raw)
            if cand:
                return cand

    te_patterns = [
        r"(?:నా పేరు|పేరు)\s+([\u0C00-\u0C7F\sA-Za-z]+)",
        r"(?:నేను)\s+([\u0C00-\u0C7F\sA-Za-z]+)\s+(?:మాట్లాడుతున్నాను)",
        r"([\u0C00-\u0C7F\sA-Za-z]+)\s+(?:మాట్లాడుతున్నాను)"
    ]
    for pat in te_patterns:
        match = re.search(pat, raw)
        if match:
            cand_raw = match.group(1)
            for stop in ["మాట్లాడుతున్నాను", "మాట్లాడుతున్నా", "మాట్లాడు", "నేను"]:
                cand_raw = re.sub(rf"\s+{stop}\b", "", cand_raw).strip()
            cand = clean_name(cand_raw)
            if cand:
                return cand

    # Single or double word fallback
    words = raw.split()
    if 1 <= len(words) <= 2:
        cleaned = clean_name(raw)
        if cleaned:
            if re.search(r'[A-Za-z\u0900-\u097F\u0C00-\u0C7F]', cleaned):
                return cleaned

    return None


def validate_tts_speech(text: str) -> bool:
    """Strict TTS input validation: checks for internal leaks (brackets, json, variables)."""
    if not text:
        return True
    if "[" in text or "]" in text or "{" in text or "}" in text:
        return False
    if "customer_name=" in text or "intent=" in text or "state=" in text or "tool=" in text:
        return False
    return True




def get_deterministic_fallback(
    industry: str,
    state: str,
    lang_key: str,
    customer_name: Optional[str] = None,
    collected_info: Optional[dict] = None
) -> str:
    """Returns a completely safe, hardcoded response for the given state and language."""
    template_map = {
        "HOSPITAL_GREETING": "HOSPITAL_GREETING",
        "HOSPITAL_WAITING_FOR_NAME": "RECOVERY",
        "HOSPITAL_PURPOSE": "HOSPITAL_PURPOSE",
        "HOSPITAL_WAITING_FOR_DECISION": "UNCLEAR",
        "HOSPITAL_WAITING_FOR_RESCHEDULE_SLOT": "RESCHEDULE",
        "HOSPITAL_GOODBYE": "CLOSING",
        "RE_GREETING": "RE_GREETING",
        "RE_WAITING_FOR_NAME": "RECOVERY",
        "RE_INTEREST_DECISION": "RE_PURPOSE_INTRO",
        "RE_REQUIREMENT_COLLECTION": "RE_INTEREST_QUESTION",
        "RE_SITE_VISIT_OFFER": "RE_RECOMMENDATION",
        "RE_CALL_ENDED": "NOT_INTERESTED"
    }
    
    tpl_name = template_map.get(state, "RECOVERY")
    try:
        tpl_text = TEMPLATES[industry][tpl_name][lang_key]
    except KeyError:
        try:
            tpl_text = TEMPLATES[industry]["RECOVERY"][lang_key]
        except KeyError:
            tpl_text = "Hello, can you hear me?"
        
    res = tpl_text.format(
        agent_name="Sophia",
        customer_name=customer_name or "",
        slot=(collected_info or {}).get("reschedule_slot", "tomorrow")
    )
    res = res.replace("Hi ,", "Hi,").replace("Hello ,", "Hello,").replace("  ", " ").strip()
    return res


async def extract_speech_from_json_stream(token_stream) -> AsyncGenerator[str, None]:
    """Parses LLM token stream yielding structured JSON on the fly, and extracts ONLY the 'speech' string field."""
    buffer = ""
    in_speech_value = False
    escaped = False
    speech_found = False
    quote_char = None
    all_tokens = []
    yielded_any = False
    
    async for token, _ in token_stream:
        if token:
            all_tokens.append(token)
            buffer += token
            
            if not speech_found:
                speech_key_idx = buffer.find('"speech"')
                if speech_key_idx == -1:
                    speech_key_idx = buffer.find("'speech'")
                
                if speech_key_idx != -1:
                    sub_buf = buffer[speech_key_idx:]
                    colon_idx = sub_buf.find(':')
                    if colon_idx != -1:
                        quote_match = re.search(r'["\']', sub_buf[colon_idx:])
                        if quote_match:
                            quote_char = quote_match.group(0)
                            quote_pos = colon_idx + quote_match.start()
                            in_speech_value = True
                            speech_found = True
                            buffer = sub_buf[quote_pos + 1:]
            
            if in_speech_value:
                i = 0
                yield_buf = ""
                while i < len(buffer):
                    char = buffer[i]
                    if escaped:
                        yield_buf += char
                        escaped = False
                    elif char == '\\':
                        escaped = True
                    elif char == quote_char:
                        in_speech_value = False
                        buffer = buffer[i+1:]
                        break
                    else:
                        yield_buf += char
                    i += 1
                
                if yield_buf:
                    clean_chunk = clean_speech_text(yield_buf)
                    if clean_chunk:
                        yielded_any = True
                        yield clean_chunk
                
                if not in_speech_value:
                    buffer = ""
                else:
                    buffer = ""
                    
    if not yielded_any:
        full_raw = "".join(all_tokens).strip()
        if full_raw:
            if full_raw.startswith("{") and "speech" in full_raw:
                try:
                    data = json.loads(full_raw)
                    speech = data.get("speech", "")
                    if speech:
                        yield clean_speech_text(speech)
                        return
                except Exception:
                    pass
            yield clean_speech_text(full_raw)


def validate_tool_call(
    industry: str,
    state: str,
    detected_intent: str,
    requested_tool: str,
    slots: Optional[dict] = None
) -> bool:
    allowed = False
    if industry == "hospital":
        if state == "HOSPITAL_WAITING_FOR_DECISION":
            if detected_intent == "CONFIRM_APPOINTMENT" and requested_tool == "confirm_appointment":
                allowed = True
            elif detected_intent == "CANCEL_APPOINTMENT" and requested_tool == "cancel_appointment":
                allowed = True
            elif detected_intent == "RESCHEDULE_APPOINTMENT" and requested_tool == "reschedule_appointment":
                allowed = True
        elif state == "HOSPITAL_WAITING_FOR_RESCHEDULE_SLOT":
            if requested_tool == "reschedule_appointment":
                allowed = True
    elif industry == "real_estate":
        if state in ("RE_SITE_VISIT_OFFER", "RE_INTEREST_DECISION"):
            if detected_intent == "CONFIRM_SITE_VISIT" and requested_tool == "book_site_visit":
                allowed = True

    logger.info(
        f"[TOOL-GUARD]\n"
        f"industry={industry}\n"
        f"state={state}\n"
        f"intent={detected_intent}\n"
        f"requested_tool={requested_tool}\n"
        f"allowed={str(allowed).lower()}\n"
        f"slots={slots}\n"
        f"allowed_statuses={['Success'] if allowed else ['Failed: tool_not_allowed_in_current_state']}"
    )
    return allowed


def check_and_reject_tool_calls(industry: str, state: str, requested_tool: str) -> None:
    forbidden = ["HOSPITAL_GREETING", "HOSPITAL_WAITING_FOR_NAME", "HOSPITAL_NAME_CAPTURED", "HOSPITAL_PURPOSE"]
    if industry == "hospital" and state in forbidden:
        logger.warning(
            f"[TOOL-GUARD]\n"
            f"industry={industry}\n"
            f"state={state}\n"
            f"intent=FORBIDDEN_STATE\n"
            f"requested_tool={requested_tool}\n"
            f"allowed=false\n"
            f"slots=None\n"
            f"allowed_statuses=['Failed: tool_not_allowed_in_current_state']"
        )


def validate_response_against_state(
    response: str,
    state: str,
    industry: str,
    customer_name: Optional[str] = None
) -> Tuple[bool, str]:
    # Placeholder validation intercept
    return True, response


# ── EXTENDED METHODS FOR CLASS ───────────────────────────────────────────────
# We add process_voice_demo_turn_stream to the class dynamically by matching the target signature:

async def _process_voice_demo_turn_stream_impl(
    self,
    call_id: str,
    campaign_id: uuid.UUID,
    *args,
    **kwargs
) -> AsyncGenerator[Tuple[Optional[str], bool, bool], None]:
    """Dedicated voice demo turn stream with strict state machine templates and JSON parsing."""
    industry = kwargs.get("industry", "hospital")
    language = kwargs.get("language", "English")
    agent_name = kwargs.get("agent_name", "Sophia")
    user_text = kwargs.get("user_text", "")

    if args:
        if len(args) == 4:
            industry, language, agent_name, user_text = args

    meta = await self.session_manager.get_session_metadata(call_id)
    if not meta:
        meta = await self.session_manager.initialize_session_context(
            call_id, industry=industry, language=language, agent_name=agent_name
        )

    agent_name = meta.get("agent_name", agent_name)
    lang_input = meta.get("language", "en").lower().strip()
    if lang_input in ("english", "en"):
        lang_code = "en"
        lang_str = "English"
    elif lang_input in ("hindi", "hi"):
        lang_code = "hi"
        lang_str = "Hindi"
    elif lang_input in ("telugu", "te"):
        lang_code = "te"
        lang_str = "Telugu"
    else:
        lang_code = "en"
        lang_str = "English"
    lang_key = lang_code
    industry = meta.get("industry", industry)
    current_state = await self.session_manager.get_session_state(call_id) or "CALL_STARTED"
    if current_state == "WAIT_FOR_NAME":
        if industry == "hospital":
            current_state = "HOSPITAL_WAITING_FOR_NAME"
        elif industry == "real_estate":
            current_state = "RE_WAITING_FOR_NAME"
    
    from app.services.session_manager import VoiceSession
    session_store = VoiceSession(call_id)
    customer_name = session_store.customer_name
    collected_info = meta.get("collected_info", {})

    history = await self.session_manager.get_message_history(call_id)
    assistant_msgs = [m for m in history if m["role"] == "assistant"]
    last_agent_message = assistant_msgs[-1]["content"] if assistant_msgs else ""

    should_hangup = False
    should_transfer = False
    tool_executed = None
    tool_result = None

    if current_state in ("HOSPITAL_GOODBYE", "HOSPITAL_CALL_ENDED", "RE_CALL_ENDED", "CALL_ENDED", "CALL_COMPLETED") or meta.get("should_hangup"):
        logger.info(f"[CONV-ENGINE] Session {call_id} is in terminal state. Ending.")
        yield None, True, False
        return

    target_template_name = None
    detected_intent = "UNKNOWN"
    allowed_intents = []
    validated_intent = "UNKNOWN"
    tool_allowed = False
    requested_tool = None
    next_state = current_state

    if industry == "hospital":
        if user_text == "[CALL_START]":
            current_state = "HOSPITAL_GREETING"
            target_template_name = "HOSPITAL_GREETING"
            next_state = "HOSPITAL_WAITING_FOR_NAME"
            detected_intent = "CALL_START"
            validated_intent = "CALL_START"
            allowed_intents = ["CALL_START"]
            await self.session_manager.update_session_state(call_id, next_state)

        elif current_state == "HOSPITAL_WAITING_FOR_NAME":
            allowed_intents = ["NAME", "UNKNOWN"]
            
            if not customer_name:
                extracted = extract_customer_name_from_text(user_text, lang_code)
                if extracted:
                    customer_name = extracted
                    session_store.customer_name = extracted
                    meta["customer_name"] = customer_name
                    collected_info["customer_name"] = customer_name
                    await self.session_manager.update_session_metadata(
                        call_id, {"customer_name": customer_name, "collected_info": collected_info}
                    )

            if customer_name:
                detected_intent = "NAME"
                validated_intent = "NAME"
                target_template_name = "HOSPITAL_PURPOSE"
                next_state = "HOSPITAL_WAITING_FOR_DECISION"
            else:
                detected_intent = "UNKNOWN"
                validated_intent = "UNKNOWN"
                target_template_name = "RECOVERY"
                next_state = "HOSPITAL_WAITING_FOR_NAME"

            await self.session_manager.update_session_state(call_id, next_state)

        elif current_state == "HOSPITAL_WAITING_FOR_DECISION":
            allowed_intents = ["CONFIRM_APPOINTMENT", "CANCEL_APPOINTMENT", "RESCHEDULE_APPOINTMENT", "UNCLEAR", "REPEAT"]
            
            t_lower = user_text.lower().strip().rstrip(".,!?।")
            confirm_words = ["confirm", "yes", "yeah", "yep", "sure", "okay", "ok", "haan", "ha", "कन्फर्म", "हाँ", "जी हाँ", "కన్ఫర్మ్", "అవును", "సరే", "yes please"]
            cancel_words = ["cancel", "no", "nope", "don't", "dont", "nahi", "na", "कैंसिल", "नहीं", "कंसल", "రద్దు", "వద్దు"]
            reschedule_words = ["reschedule", "change", "postpone", "move", "later", "tomorrow", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "रीशेड्यूल", "बदल", "రీషెడ్యూల్", "schedule", "rescind", "receive", "reduce", "point", "reshedule"]
            repeat_words = ["repeat", "say again", "pardon", "what did you say", "dubara", "phir se", "fir se", "kya bola", "दोहराएं", "మరోసారి", "మళ్లీ"]
            
            is_repeat = any(w in t_lower for w in repeat_words)
            is_ambiguous_yes = (t_lower in confirm_words)
            
            if is_repeat:
                detected_intent = "REPEAT"
                validated_intent = "REPEAT"
                target_template_name = "REPEAT"
                next_state = current_state
            elif is_ambiguous_yes:
                is_confirmation_question = "confirm your appointment" in last_agent_message.lower() or "confirm it" in last_agent_message.lower()
                if is_confirmation_question:
                    detected_intent = "CONFIRM_APPOINTMENT"
                    validated_intent = "CONFIRM_APPOINTMENT"
                    requested_tool = "confirm_appointment"
                else:
                    detected_intent = "CONFIRM"
                    validated_intent = "UNCLEAR"
                    target_template_name = "UNCLEAR"
                    next_state = "HOSPITAL_WAITING_FOR_DECISION"
            else:
                if any(w in t_lower for w in reschedule_words):
                    detected_intent = "RESCHEDULE_APPOINTMENT"
                    validated_intent = "RESCHEDULE_APPOINTMENT"
                    requested_tool = "reschedule_appointment"
                elif any(w in t_lower for w in cancel_words):
                    detected_intent = "CANCEL_APPOINTMENT"
                    validated_intent = "CANCEL_APPOINTMENT"
                    requested_tool = "cancel_appointment"
                elif any(w in t_lower for w in confirm_words) or "confirm" in t_lower or "keep" in t_lower:
                    detected_intent = "CONFIRM_APPOINTMENT"
                    validated_intent = "CONFIRM_APPOINTMENT"
                    requested_tool = "confirm_appointment"
                else:
                    detected_intent = "UNKNOWN"
                    validated_intent = "UNCLEAR"
                    target_template_name = "UNCLEAR"
                    next_state = "HOSPITAL_WAITING_FOR_DECISION"

            if validated_intent == "CONFIRM_APPOINTMENT":
                tool_allowed = validate_tool_call(industry, current_state, validated_intent, requested_tool)
                if tool_allowed:
                    tool_executed = "confirm_appointment"
                    tool_result = "Success: Appointment confirmed."
                    target_template_name = "CONFIRM"
                    next_state = "HOSPITAL_GOODBYE"
                    should_hangup = True
            elif validated_intent == "CANCEL_APPOINTMENT":
                tool_allowed = validate_tool_call(industry, current_state, validated_intent, requested_tool)
                if tool_allowed:
                    tool_executed = "cancel_appointment"
                    tool_result = "Success: Appointment cancelled."
                    target_template_name = "CANCEL"
                    next_state = "HOSPITAL_GOODBYE"
                    should_hangup = True
            elif validated_intent == "RESCHEDULE_APPOINTMENT":
                tool_allowed = validate_tool_call(industry, current_state, validated_intent, requested_tool, slots={"slot": "next Monday at 10:00 AM"})
                if tool_allowed:
                    tool_executed = "reschedule_appointment"
                    tool_result = "Success: Rescheduled to next Monday at 10:00 AM"
                    collected_info["reschedule_slot"] = "next Monday at 10:00 AM"
                    target_template_name = "RESCHEDULE_CONFIRM"
                    next_state = "HOSPITAL_GOODBYE"
                    should_hangup = True

            await self.session_manager.update_session_state(call_id, next_state)

        elif current_state == "HOSPITAL_WAITING_FOR_RESCHEDULE_SLOT":
            allowed_intents = ["PROVIDE_SLOT"]
            detected_intent = "PROVIDE_SLOT"
            validated_intent = "PROVIDE_SLOT"
            requested_tool = "reschedule_appointment"
            
            collected_info["reschedule_slot"] = user_text
            await self.session_manager.update_session_metadata(call_id, {"collected_info": collected_info})

            tool_allowed = validate_tool_call(industry, current_state, validated_intent, requested_tool, slots={"slot": user_text})
            if tool_allowed:
                tool_executed = "reschedule_appointment"
                tool_result = f"Success: Rescheduled to {user_text}"
                target_template_name = "RESCHEDULE_CONFIRM"
                next_state = "HOSPITAL_GOODBYE"
                should_hangup = True

            await self.session_manager.update_session_state(call_id, next_state)

        if requested_tool and not tool_allowed:
            check_and_reject_tool_calls(industry, current_state, requested_tool)

    elif industry == "real_estate":
        if user_text == "[CALL_START]":
            current_state = "RE_GREETING"
            target_template_name = "RE_GREETING"
            next_state = "RE_WAITING_FOR_NAME"
            detected_intent = "CALL_START"
            validated_intent = "CALL_START"
            allowed_intents = ["CALL_START"]
            await self.session_manager.update_session_state(call_id, next_state)

        elif current_state == "RE_WAITING_FOR_NAME":
            allowed_intents = ["NAME", "UNKNOWN"]
            
            if not customer_name:
                extracted = extract_customer_name_from_text(user_text, lang_code)
                if extracted:
                    customer_name = extracted
                    session_store.customer_name = extracted
                    meta["customer_name"] = customer_name
                    collected_info["customer_name"] = customer_name
                    await self.session_manager.update_session_metadata(
                        call_id, {"customer_name": customer_name, "collected_info": collected_info}
                    )

            if customer_name:
                detected_intent = "NAME"
                validated_intent = "NAME"
                target_template_name = "RE_PURPOSE_INTRO"
                next_state = "RE_INTEREST_DECISION"
            else:
                detected_intent = "UNKNOWN"
                validated_intent = "UNKNOWN"
                target_template_name = "RECOVERY"
                next_state = "RE_WAITING_FOR_NAME"

            await self.session_manager.update_session_state(call_id, next_state)

        elif current_state == "RE_INTEREST_DECISION":
            allowed_intents = ["CONFIRM_SITE_VISIT", "DECLINE_SITE_VISIT"]
            t_lower = user_text.lower()
            decline_words = ["no", "nope", "not interested", "dont", "don't", "nahi", "na", "nahi chahiye", "వద్దు", "లేదు", "नहीं"]
            
            is_decline = any(w in t_lower for w in decline_words)
            
            if is_decline:
                detected_intent = "DECLINE_SITE_VISIT"
                validated_intent = "DECLINE_SITE_VISIT"
                target_template_name = "RE_SITE_VISIT_DECLINE"
                next_state = "RE_CALL_ENDED"
                should_hangup = True
            else:
                detected_intent = "CONFIRM_SITE_VISIT"
                validated_intent = "CONFIRM_SITE_VISIT"
                requested_tool = "book_site_visit"
                tool_allowed = validate_tool_call(industry, current_state, validated_intent, requested_tool, slots={"visit": user_text})
                if tool_allowed:
                    tool_executed = "book_site_visit"
                    tool_result = f"Success: Site visit booked for choice: {user_text}."
                    target_template_name = "RE_SITE_VISIT_CONFIRM"
                    next_state = "RE_CALL_ENDED"
                    should_hangup = True
                else:
                    target_template_name = "RE_SITE_VISIT_CONFIRM"
                    next_state = "RE_CALL_ENDED"
                    should_hangup = True

            await self.session_manager.update_session_state(call_id, next_state)

        elif current_state == "RE_REQUIREMENT_COLLECTION":
            allowed_intents = ["PROVIDE_REQUIREMENT"]
            detected_intent = "PROVIDE_REQUIREMENT"
            validated_intent = "PROVIDE_REQUIREMENT"
            collected_info["requirements"] = user_text
            await self.session_manager.update_session_metadata(call_id, {"collected_info": collected_info})

            target_template_name = "RE_RECOMMENDATION"
            next_state = "RE_SITE_VISIT_OFFER"
            await self.session_manager.update_session_state(call_id, next_state)

        elif current_state == "RE_SITE_VISIT_OFFER":
            allowed_intents = ["CONFIRM_SITE_VISIT", "DECLINE_SITE_VISIT"]
            t_lower = user_text.lower()
            requested_tool = "book_site_visit"
            confirm_words = ["confirm", "yes", "yeah", "yep", "sure", "okay", "ok", "haan", "ha", "हाँ", "అవును"]
            
            if any(w in t_lower for w in confirm_words) or "visit" in t_lower or "book" in t_lower or "show" in t_lower:
                detected_intent = "CONFIRM_SITE_VISIT"
                validated_intent = "CONFIRM_SITE_VISIT"
                tool_allowed = validate_tool_call(industry, current_state, validated_intent, requested_tool, slots={"visit": "Skyline Residency"})
                if tool_allowed:
                    tool_executed = "book_site_visit"
                    tool_result = "Success: Site visit booked."
                    target_template_name = "RE_SITE_VISIT_CONFIRM"
            else:
                detected_intent = "DECLINE_SITE_VISIT"
                validated_intent = "DECLINE_SITE_VISIT"
                target_template_name = "RE_SITE_VISIT_DECLINE"

            next_state = "RE_CALL_ENDED"
            should_hangup = True
            await self.session_manager.update_session_state(call_id, next_state)

    meta["turn_detected_intent"] = detected_intent
    meta["turn_validated_intent"] = validated_intent
    meta["turn_response_policy"] = "template" if target_template_name else "llm_fallback"
    meta["turn_tool_executed"] = tool_executed or "None"
    meta["turn_tool_allowed"] = tool_allowed
    await self.session_manager.update_session_metadata(call_id, meta)

    try:
        logger.info(
            f"[CONVERSATION-GUARD]\n"
            f"session_id={call_id}\n"
            f"industry={industry}\n"
            f"current_state={current_state}\n"
            f"customer_name={customer_name or 'UNKNOWN'}\n"
            f"last_agent_message='{last_agent_message}'\n"
            f"customer_transcript='{user_text}'\n"
            f"detected_intent={detected_intent}\n"
            f"allowed_intents={allowed_intents}\n"
            f"validated_intent={validated_intent}\n"
            f"next_state={next_state}\n"
            f"tool_allowed={str(tool_allowed).lower()}\n"
            f"requested_tool={requested_tool or 'None'}"
        )
    except Exception as ge:
        logger.warning(f"Conversation guard logging warning: {ge}")

    if target_template_name:
        if target_template_name == "REPEAT":
            target_text = last_agent_message or "Could you please confirm, reschedule, or cancel your appointment?"
        else:
            template_text = TEMPLATES[industry][target_template_name][lang_key]
            target_text = template_text.format(
                agent_name=agent_name,
                customer_name=customer_name or "",
                slot=collected_info.get("reschedule_slot", "tomorrow")
            )
        target_text = target_text.replace("Hi ,", "Hi,").replace("Hello ,", "Hello,").replace("  ", " ").strip()

        # --- ZERO-LLM FAST PATH ---
        # The template text is already fully resolved above. Sending it through the LLM
        # just to echo it back as JSON costs ~1000ms TTFT every turn. Skip the LLM entirely
        # and stream the text word-by-word directly to TTS for instant response.
        logger.info(f"[TEMPLATE-FAST-PATH] Bypassing LLM for template={target_template_name} text_chars={len(target_text)}")

        words = target_text.split(" ")
        full_text = target_text

        # Sanity check — no metadata bleed
        if "[" in full_text or "]" in full_text or "customer_name=" in full_text:
            logger.error(f"[TTS-SANITIZER-REJECT] Metadata in template text: '{full_text}'")
            full_text = get_deterministic_fallback(industry, current_state, lang_key, customer_name, collected_info)
            yield full_text, False, False
        else:
            is_valid, fallback_val = validate_response_against_state(full_text, next_state, industry, customer_name)
            if not is_valid:
                logger.warning(f"[RESPONSE-VALIDATION] State contract violation. Replacing with fallback '{fallback_val}'")
                full_text = fallback_val
                yield fallback_val, False, False
            else:
                # Pre-split at sentence boundaries so TTS can dispatch each sentence
                # to synthesis immediately rather than accumulating all words first.
                # e.g. "Hello Krish." (12 chars) becomes sentence-1 instantly (~250ms synth)
                # while the longer follow-up synthesizes in parallel.
                # Split on sentence terminators while keeping the terminator attached
                raw_sentences = re.split(r'(?<=[.!?।])\s+', target_text)
                for i, sent in enumerate(raw_sentences):
                    sent = sent.strip()
                    if not sent:
                        continue
                    # Yield sentence with a trailing space so TTS splitter sees a word boundary
                    chunk = sent if i == 0 else " " + sent
                    yield chunk, False, False
                    # Tiny yield point lets the TTS producer task run and dispatch synthesis
                    # for this sentence before we push the next one
                    await asyncio.sleep(0)

        if full_text:
            bot_turn = {"role": "assistant", "content": full_text}
            await self.session_manager.append_message(call_id, bot_turn)

    else:
        compiled_prompt, _ = await self.prompt_service.build_prompt(
            campaign_id,
            industry=industry,
            language=lang_str,
            agent_name=agent_name,
            current_state=current_state,
            collected_info=collected_info,
            user_text=user_text
        )

        history_dialogue = [m for m in history if m["role"] in ("user", "assistant")]
        if user_text != "[CALL_START]":
            user_turn = {"role": "user", "content": user_text}
            history_dialogue.append(user_turn)
            await self.session_manager.append_message(call_id, user_turn)

        messages_to_send = [{"role": "system", "content": compiled_prompt}] + history_dialogue
        llm_stream = self.llm_service.generate_completion_stream(messages_to_send, None)
        speech_stream = extract_speech_from_json_stream(llm_stream)

        full_text_acc = []
        async for chunk in speech_stream:
            if chunk:
                if "[" in chunk or "]" in chunk or "{" in chunk or "}" in chunk or "customer_name=" in chunk:
                    logger.error(f"[TTS-SANITIZER-REJECT] Metadata leak in chunk: '{chunk}'")
                    full_text_acc = [get_deterministic_fallback(industry, current_state, lang_key, customer_name, collected_info)]
                    break
                full_text_acc.append(chunk)
                yield chunk, False, False

        full_text = "".join(full_text_acc).strip()
        
        is_valid, fallback_val = validate_response_against_state(full_text, next_state, industry, customer_name)
        if not is_valid:
            logger.warning(f"[RESPONSE-VALIDATION] State contract violation. Replacing '{full_text}' with fallback '{fallback_val}'")
            full_text = fallback_val
            yield fallback_val, False, False

        if full_text:
            bot_turn = {"role": "assistant", "content": full_text}
            await self.session_manager.append_message(call_id, bot_turn)

    logger.info(
        f"[TURN] session={call_id} state={next_state} agent={agent_name} "
        f"language={lang_code} customer_name={customer_name or 'UNKNOWN'} "
        f"tool={tool_executed or 'None'} tool_result={tool_result or 'None'} "
        f"should_hangup={should_hangup}"
    )

    yield None, should_hangup, should_transfer


ConversationEngine.process_voice_demo_turn_stream = _process_voice_demo_turn_stream_impl

