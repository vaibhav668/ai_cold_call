import sys
import os
import time
import asyncio
import psutil
import numpy as np

# Ensure root dir is on python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.stdout.reconfigure(encoding='utf-8')

from app.services.speech.tts.kokoro_provider import KokoroProvider
from app.core.logging import logger

HINDI_TEST_SUITE = [
    ("Greeting", "नमस्ते! मैं सिटीकेयर हॉस्पिटल से सोफिया बात कर रही हूँ। क्या मैं आपका नाम जान सकती हूँ?"),
    ("Name Response", "धन्यवाद वैभव। मैं कल डॉ. शर्मा के साथ आपके अपॉइंटमेंट के सिलसिले में कॉल कर रही हूँ।"),
    ("Appointment Confirmation", "बहुत बढ़िया वैभव। कल सुबह 11 बजे डॉ. शर्मा के साथ आपका अपॉइंटमेंट कन्फर्म कर दिया गया है। धन्यवाद!"),
    ("Appointment Cancellation", "मैं समझती हूँ वैभव। आपके अपॉइंटमेंट रद्दीकरण का अनुरोध दर्ज कर लिया गया है। आपका दिन शुभ हो!"),
    ("Appointment Reschedule", "बिल्कुल वैभव। आपका अपॉइंटमेंट रीशेड्यूल करने के लिए चिह्नित कर दिया गया है। हमारी टीम जल्द आपसे संपर्क करेगी।"),
    ("Code-Mixed", "आपका appointment कल morning 11 AM को schedule किया गया है। confirm कर दीजिए।"),
    ("Indian Names", "मेरा नाम वैभव, मयंक, आकाश, राहुल, रोहित, अनन्या, प्रियंका या स्नेहा है।"),
    ("Numbers and Time", "कल सुबह 11 बजे या सोमवार दोपहर 3 बजे अपॉइंटमेंट तय करें।"),
]

async def run_hindi_tests():
    print("=" * 70)
    print("      KOKORO NATIVE HINDI TTS BENCHMARK & TEST SUITE")
    print("=" * 70)

    # 1. Warmup
    t0 = time.perf_counter()
    warmup_time = await KokoroProvider.warmup()
    print(f"✓ Model Warmup Completed in {warmup_time:.1f} ms")

    provider = KokoroProvider()
    voice_config = {"persona_name": "sophia"}

    # Process Memory
    process = psutil.Process()
    ram_mb_before = process.memory_info().rss / (1024 * 1024)

    total_tests = len(HINDI_TEST_SUITE)
    passed_tests = 0

    print("\n" + "-" * 70)
    for idx, (label, text) in enumerate(HINDI_TEST_SUITE, 1):
        print(f"\n[{idx}/{total_tests}] Testing: {label}")
        print(f"   Text: '{text}'")

        t_start = time.perf_counter()
        first_chunk_t = None
        total_chunks = 0
        total_bytes = 0

        try:
            async for chunk in provider.stream_speech(
                text,
                language="hi",
                voice_config=voice_config
            ):
                if first_chunk_t is None:
                    first_chunk_t = time.perf_counter()
                total_chunks += 1
                total_bytes += len(chunk)

            t_end = time.perf_counter()

            ttfb_ms = ((first_chunk_t - t_start) * 1000.0) if first_chunk_t else 0.0
            total_ms = (t_end - t_start) * 1000.0

            # G.711 mu-law at 8000Hz = 8000 bytes/sec
            audio_duration_sec = total_bytes / 8000.0
            rtf = (total_ms / 1000.0) / audio_duration_sec if audio_duration_sec > 0 else 0.0

            print(f"   ✓ TTFB (Time to First Byte): {ttfb_ms:.1f} ms")
            print(f"   ✓ Total Synthesis Time:       {total_ms:.1f} ms")
            print(f"   ✓ Audio Duration:             {audio_duration_sec:.2f} s")
            print(f"   ✓ Real Time Factor (RTF):     {rtf:.2f}")
            print(f"   ✓ Generated Audio Bytes:      {total_bytes} bytes ({total_chunks} chunks)")

            passed_tests += 1

        except Exception as e:
            print(f"   ❌ FAILED: {e}")

    ram_mb_after = process.memory_info().rss / (1024 * 1024)
    print("\n" + "=" * 70)
    print(f" SUMMARY: {passed_tests}/{total_tests} Hindi test cases passed successfully.")
    print(f" Memory Usage: RSS {ram_mb_after:.1f} MB (Delta: +{ram_mb_after - ram_mb_before:.1f} MB)")
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(run_hindi_tests())
