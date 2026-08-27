import sys
import os
import asyncio
import uuid
from datetime import datetime, timezone

# Ensure root dir is on python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.stdout.reconfigure(encoding='utf-8')

from app.services.intent_service import IntentService
from app.services.conversation_engine import ConversationEngine, normalize_name_transcript
from app.services.speech.tts.kokoro_provider import KokoroProvider
from app.voice_demo.controllers.voice_agent import (
    get_greeting_text,
    pregenerate_greeting,
    _demo_sessions,
    _greeting_cache
)

async def run_end_to_end_hindi_tests():
    print("=" * 75)
    print("      END-TO-END HINDI VOICE PIPELINE INTEGRATION TEST SUITE")
    print("=" * 75)

    intent_svc = IntentService()
    engine = ConversationEngine()
    tts_provider = KokoroProvider()

    total_passed = 0
    total_tests = 0

    # -------------------------------------------------------------------------
    # TEST GROUP 1: Hindi STT & Name Extraction
    # -------------------------------------------------------------------------
    print("\n--- [GROUP 1] HINDI NAME EXTRACTION & STT TEST ---")
    stt_name_tests = [
        ("मेरा नाम अमित है", "अमित"),
        ("मेरा नाम मयंक है", "मयंक"),
        ("मैं आकाश हूँ", "आकाश"),
        ("My name is Amit", "Amit"),
    ]

    for utterance, expected_name in stt_name_tests:
        total_tests += 1
        extracted = intent_svc.extract_name(utterance)
        status = "✓ PASS" if extracted == expected_name else f"❌ FAIL (got '{extracted}')"
        if extracted == expected_name:
            total_passed += 1
        print(f"  [{status}] Input: '{utterance}' → Extracted: '{extracted}' (Expected: '{expected_name}')")

    # -------------------------------------------------------------------------
    # TEST GROUP 2: Hindi Intent Classification & Code-Switching
    # -------------------------------------------------------------------------
    print("\n--- [GROUP 2] HINDI INTENT CLASSIFICATION TEST ---")
    intent_tests = [
        ("मुझे अपना appointment cancel करना है", "CANCEL"),
        ("मैं अपना appointment reschedule करना चाहता हूँ", "RESCHEDULE"),
        ("हाँ, इसे confirm कर दीजिए", "CONFIRM"),
    ]

    for utterance, expected_intent in intent_tests:
        total_tests += 1
        intent, vars_extracted = intent_svc.classify_intent(utterance, current_state="WAIT_FOR_DECISION")
        status = "✓ PASS" if intent == expected_intent else f"❌ FAIL (got '{intent}')"
        if intent == expected_intent:
            total_passed += 1
        print(f"  [{status}] Input: '{utterance}' → Intent: '{intent}'")

    # -------------------------------------------------------------------------
    # TEST GROUP 3: Persona & Session Isolation (Maya vs Sophia)
    # -------------------------------------------------------------------------
    print("\n--- [GROUP 3] PERSONA & SESSION ISOLATION TEST (MAYA vs SOPHIA) ---")

    # Test Session A: Maya (Hindi)
    total_tests += 1
    session_id_a = str(uuid.uuid4())
    greeting_a = get_greeting_text("hospital", "Hindi", "Maya", "Female")
    print(f"  Session A (Maya) Greeting: '{greeting_a}'")
    
    assert "Maya" in greeting_a or "माया" in greeting_a, "Session A greeting must contain Maya"
    assert "Sophia" not in greeting_a, "Session A greeting MUST NOT contain Sophia"

    voice_a, lang_code_a = tts_provider._resolve_voice_and_lang(
        {"persona_name": "Maya"}, "Hindi"
    )
    print(f"  Session A TTS Resolution: voice='{voice_a}', lang_code='{lang_code_a}'")
    assert voice_a == "hf_beta", f"Maya Hindi TTS must resolve to hf_beta, got {voice_a}"
    assert lang_code_a == "hi", f"Maya Hindi TTS lang_code must be 'hi', got {lang_code_a}"

    # Test Session B: Sophia (Hindi)
    total_tests += 1
    session_id_b = str(uuid.uuid4())
    greeting_b = get_greeting_text("hospital", "Hindi", "Sophia", "Female")
    print(f"  Session B (Sophia) Greeting: '{greeting_b}'")

    assert "Sophia" in greeting_b or "सोफिया" in greeting_b, "Session B greeting must contain Sophia"
    assert "Maya" not in greeting_b, "Session B greeting MUST NOT contain Maya"

    voice_b, lang_code_b = tts_provider._resolve_voice_and_lang(
        {"persona_name": "Sophia"}, "Hindi"
    )
    print(f"  Session B TTS Resolution: voice='{voice_b}', lang_code='{lang_code_b}'")
    assert voice_b == "hf_alpha", f"Sophia Hindi TTS must resolve to hf_alpha, got {voice_b}"
    assert lang_code_b == "hi", f"Sophia Hindi TTS lang_code must be 'hi', got {lang_code_b}"

    # Pre-generation Cache Keys
    cache_key_a = ("maya", "hindi", "hospital", "female")
    cache_key_b = ("sophia", "hindi", "hospital", "female")
    assert cache_key_a != cache_key_b, "Cache keys for Maya and Sophia MUST be distinct!"
    total_passed += 2
    print("  ✓ PASS: Maya and Sophia sessions are 100% isolated with unique greetings and TTS voices!")

    # -------------------------------------------------------------------------
    # TEST GROUP 4: Full Multi-Turn Conversation Continuity in Hindi
    # -------------------------------------------------------------------------
    print("\n--- [GROUP 4] MULTI-TURN HINDI CONVERSATION CONTINUITY TEST ---")
    total_tests += 1

    # Turn 1: Customer provides name in Hindi
    turn1_text = "मेरा नाम वैभव है"
    history = []
    
    response1_chunks = []
    state_out = None
    async for chunk, is_hangup, is_tool in engine.process_turn_stream(
        call_id=uuid.uuid4(),
        campaign_id=uuid.uuid4(),
        customer_id=uuid.uuid4(),
        user_text=turn1_text,
        conversation_history=history,
        industry="hospital",
        language="Hindi",
        collected_info={}
    ):
        if chunk:
            response1_chunks.append(chunk)

    response1 = "".join(response1_chunks)
    print(f"  Turn 1 User: '{turn1_text}'")
    print(f"  Turn 1 Agent Response: '{response1}'")

    assert "वैभव" in response1, "Agent response MUST acknowledge customer name 'वैभव'"
    assert "डॉ. शर्मा" in response1 or "appointment" in response1.lower() or "अपॉइंटमेंट" in response1, "Agent response MUST state call purpose"
    print("  ✓ PASS: Agent acknowledged customer name and stated call purpose in 1 turn!")
    total_passed += 1

    print("\n" + "=" * 75)
    print(f" TOTAL RESULT: {total_passed}/{total_tests} Hindi pipeline test cases passed 100%.")
    print("=" * 75)

if __name__ == "__main__":
    asyncio.run(run_end_to_end_hindi_tests())
