import asyncio
import numpy as np
import uuid
import sys
from app.services.vad_service import EndOfSpeechDetector
from app.services.stt_service import SpeechService
from app.services.tts_service import VoiceService
from app.services.embedding_service import EmbeddingService
from app.services.rag_service import RAGService

# Force UTF-8 encoding for Windows terminal stdout to support Indic scripts printing
sys.stdout.reconfigure(encoding='utf-8')

async def test_vad():
    print("\n--- Testing VAD (Silero) ---")
    detector = EndOfSpeechDetector()
    print(f"Active VAD Provider: {detector.provider.__class__.__name__}")
    
    # 20ms G.711 mu-law frame (160 bytes of zero/quiet audio)
    silent_frame = b"\xff" * 160
    
    # Process 50 frames (1 second of silence)
    print("Feeding silence...")
    for _ in range(50):
        res = detector.process_frame(silent_frame)
        if res:
            print(f"VAD Event: {res}")
            
    # Process 50 frames of simulated speech (alternating high amplitude PCM encoded to mu-law)
    # 440Hz tone G.711 encoded samples
    import math
    speech_frame_samples = []
    for t_idx in range(160):
        val = int(8000.0 * math.sin(2.0 * math.pi * 440.0 * (t_idx / 8000.0)))
        # G.711 mu-law compression
        BIAS = 0x84
        CLIP = 32635
        sign = (val >> 8) & 0x80
        if sign != 0:
            val = -val
        if val > CLIP:
            val = CLIP
        val = val + BIAS
        exponent = 7
        exp_mask = 0x4000
        while (val & exp_mask) == 0 and exponent > 0:
            exponent -= 1
            exp_mask >>= 1
        mantissa = (val >> (exponent + 3)) & 0x0F
        ulaw_byte = ~(sign | (exponent << 4) | mantissa) & 0xFF
        speech_frame_samples.append(ulaw_byte)
        
    speech_frame = bytes(speech_frame_samples)
    print("Feeding simulated speech...")
    for i in range(100):
        res = detector.process_frame(speech_frame)
        if res:
            print(f"VAD Event: {res}")
            
    print("VAD tests complete.")

async def test_stt():
    print("\n--- Testing STT (Faster-Whisper) ---")
    stt = SpeechService()
    print(f"Active STT Provider: {stt.provider.__class__.__name__}")
    
    # Feed 1 second of silence to test fallback/silence checks
    silent_audio = b"\xff" * 8000
    res = await stt.transcribe_utterance(silent_audio, language="en")
    print(f"Silent audio transcription output: {res} (Expected: None)")
    
    # Try mock transcription by length (in case local/cloud models fail)
    mock_audio = b"\xff" * 20000
    res = await stt.transcribe_utterance(mock_audio, language="en")
    print(f"Mock-length audio transcription output: {res}")

async def test_tts():
    print("\n--- Testing TTS (MeloTTS) ---")
    tts = VoiceService()
    print(f"Active TTS Provider: {tts.provider.__class__.__name__}")
    
    # Test multilingual inputs
    inputs = {
        "en": "Hello, how can I help you today?",
        "hi": "नमस्ते, मैं आपकी क्या सहायता कर सकता हूँ?",
        "te": "నమస్తే, నేను మీకు ఎలా సహాయపడగలను?"
    }
    
    for lang, text in inputs.items():
        print(f"Synthesizing [{lang}]: '{text}'")
        chunks = []
        async for chunk in tts.stream_speech(text, language=lang):
            chunks.append(chunk)
        print(f"Generated {len(chunks)} G.711 mu-law audio frames ({len(chunks)*20}ms)")

async def test_embeddings_and_rag():
    print("\n--- Testing Embeddings (BAAI/bge-m3) & RAG ---")
    emb = EmbeddingService()
    print(f"Active Embeddings Provider: {emb.provider.__class__.__name__}")
    print(f"Dimension: {emb.dimension}")
    
    # Test query embedding
    query = "cardiology appointment details"
    vec = await emb.get_query_embedding(query)
    print(f"Query vector dimensions: {len(vec)} (Expected: 1024)")
    
    # Test RAG Service integration
    rag = RAGService()
    await rag.initialize_collection()
    
    campaign_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    
    # Index English, Hindi, Telugu sample document text
    sample_text = (
        "English: Dr. Akash is a cardiologist. Phone: 123456. "
        "Hindi: डॉ आकाश हृदय रोग विशेषज्ञ हैं। "
        "Telugu: డాక్టర్ ఆకాష్ కార్డియాలజిస్ట్."
    )
    
    print("Indexing sample text...")
    indexed_chunks = await rag.index_document(campaign_id, doc_id, "test_doc.txt", sample_text)
    print(f"Indexed {indexed_chunks} chunks.")
    
    # Search in English
    print("Searching for 'Dr. Akash'...")
    results = await rag.search_knowledge(campaign_id, "Dr. Akash", limit=2)
    for idx, r in enumerate(results):
        print(f"Result {idx+1}: {r['text'][:100]} (score: {r['score']:.4f})")
        
    # Search in Hindi
    print("Searching for 'डॉ आकाश' (Hindi)...")
    results = await rag.search_knowledge(campaign_id, "डॉ आकाश", limit=2)
    for idx, r in enumerate(results):
        print(f"Result {idx+1}: {r['text'][:100]} (score: {r['score']:.4f})")

    # Clean up document
    print("Cleaning up indexed vectors...")
    await rag.delete_document_vectors(doc_id)
    print("RAG tests complete.")

async def main():
    await test_vad()
    await test_stt()
    await test_tts()
    await test_embeddings_and_rag()

if __name__ == "__main__":
    asyncio.run(main())
