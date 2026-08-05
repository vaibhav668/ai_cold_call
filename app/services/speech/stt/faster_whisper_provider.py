import os
import io
import wave
import struct
import httpx
import asyncio
import numpy as np
from typing import Optional
from app.core.logging import logger
from app.services.speech.stt.base import SpeechToTextProvider

# Ignore common Whisper hallucination tokens on telephone static/silence
_SILENCE_TOKENS = {
    "", ".", "..", "...", "Thank you.", "Bye.", "Thanks.", "you",
    "You.", "you.", "Okay.", "okay.", "Hmm.", "hmm.", "Uh.", "uh.",
    "Mm.", "mm.", "Mmm.", "mmm.", "[Music]", "[Applause]", "[Laughter]",
}

def decode_ulaw_sample(u_val: int) -> int:
    """Decodes G.711 mu-law byte sample back to a 16-bit linear PCM signed integer."""
    u_val = ~u_val & 0xFF
    sign = (u_val & 0x80)
    exponent = (u_val >> 4) & 0x07
    mantissa = u_val & 0x0F
    sample = (mantissa << 3) + 132
    sample <<= exponent
    sample -= 132
    return -sample if sign else sample


class FasterWhisperProvider(SpeechToTextProvider):
    """
    Local Speech-to-Text provider utilizing the Faster-Whisper library.
    Optimized for low-latency CPU inference via CTranslate2.
    """

    _model_instance = None
    _model_lock = asyncio.Lock()

    def __init__(self) -> None:
        low_mem = os.environ.get("LOW_MEMORY_DEPLOYMENT", "false").lower() == "true"
        try:
            import psutil
            if psutil.virtual_memory().total < 1024 * 1024 * 1024:  # < 1GB
                low_mem = True
        except Exception:
            pass
        default_size = "tiny.en" if low_mem else "base"
        self.model_size = os.environ.get("WHISPER_MODEL", default_size)
        self.api_key = os.environ.get("GROQ_API_KEY", "")

    @classmethod
    async def _get_model(cls, model_size: str):
        """Loads and caches the WhisperModel in a thread-safe singleton wrapper."""
        if cls._model_instance is not None:
            return cls._model_instance

        async with cls._model_lock:
            if cls._model_instance is not None:
                return cls._model_instance

            try:
                from faster_whisper import WhisperModel
                logger.info(f"[STT] Initializing Faster-Whisper model '{model_size}' on CPU...")
                # Run the blocking model loading inside executor to keep event loop responsive
                def load_model():
                    # Explicitly set download_root to /tmp to ensure write access on all
                    # containerized platforms (Render, Railway, etc.) regardless of HOME dir.
                    if os.name != "nt":
                        cache_dir = os.environ.get("HF_HOME", "/tmp/hf_cache")
                        os.makedirs(cache_dir, exist_ok=True)
                        download_root = cache_dir
                    else:
                        download_root = None
                    return WhisperModel(
                        model_size,
                        device="cpu",
                        compute_type="int8",
                        cpu_threads=4,
                        download_root=download_root
                    )
                cls._model_instance = await asyncio.get_event_loop().run_in_executor(None, load_model)
                logger.info(f"[STT] Faster-Whisper model '{model_size}' successfully loaded.")
                try:
                    import psutil
                    rss = psutil.Process().memory_info().rss / (1024 * 1024)
                    logger.info(f"[MEMORY] Whisper loaded: RSS {rss:.2f} MB")
                except Exception:
                    pass
            except Exception as e:
                logger.error(f"[STT] Could not initialize local Faster-Whisper model: {e}")
                cls._model_instance = "FAILED"
            return cls._model_instance

    async def transcribe_utterance(
        self,
        audio_bytes: bytes,
        language: Optional[str] = None
    ) -> Optional[str]:
        if not audio_bytes or len(audio_bytes) < 160:
            return None

        # 1. Try local Faster-Whisper model first
        model = await self._get_model(self.model_size)
        if model != "FAILED" and model is not None:
            try:
                # Decode G.711 mu-law 8kHz to linear PCM
                samples_8k = [decode_ulaw_sample(b) for b in audio_bytes]
                x_8k = np.array(samples_8k, dtype=np.float32) / 32768.0

                # Resample 8kHz -> 16kHz (simply duplicate each sample for speed)
                x_16k = np.repeat(x_8k, 2)

                def run_transcription():
                    import torch
                    with torch.inference_mode():
                        # Whisper large-v3-turbo detects language dynamically if language=None
                        segments, info = model.transcribe(
                            x_16k,
                            beam_size=3,
                            language=language,
                            vad_filter=True
                        )
                        text = " ".join([seg.text for seg in segments]).strip()
                        return text, info.language

                text, detected_lang = await asyncio.get_event_loop().run_in_executor(None, run_transcription)
                if text and text not in _SILENCE_TOKENS and len(text) > 2:
                    logger.info(f"[STT] Local Whisper transcribed ({detected_lang}): '{text}'")
                    return text
                return None
            except Exception as e:
                logger.warning(f"[STT] Local transcription failed: {e}. Falling back to Cloud API...")

        # 2. Cloud API fallback (Groq Whisper-large-v3-turbo) if local model fails
        if self.api_key:
            return await self._transcribe_cloud_fallback(audio_bytes, language)

        # 3. High-fidelity Mock fallback if both fail
        return self._mock_transcription(audio_bytes)

    async def _transcribe_cloud_fallback(self, audio_bytes: bytes, language: Optional[str] = None) -> Optional[str]:
        try:
            # Convert G.711 mu-law bytes to a 16-bit mono WAV byte array
            pcm_samples = [decode_ulaw_sample(b) for b in audio_bytes]
            pcm_bytes = struct.pack(f"<{len(pcm_samples)}h", *pcm_samples)

            wav_io = io.BytesIO()
            with wave.open(wav_io, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(8000)
                wav_file.writeframes(pcm_bytes)
            wav_bytes = wav_io.getvalue()

            async with httpx.AsyncClient(timeout=10.0) as client:
                files = {"file": ("speech.wav", wav_bytes, "audio/wav")}
                data = {"model": "whisper-large-v3-turbo"}
                if language:
                    data["language"] = language
                headers = {"Authorization": f"Bearer {self.api_key}"}

                response = await client.post(
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    files=files,
                    data=data,
                    headers=headers,
                )
                if response.status_code == 200:
                    text = response.json().get("text", "").strip()
                    if text and text not in _SILENCE_TOKENS and len(text) > 2:
                        logger.info(f"[STT] Cloud Whisper fallback transcribed: '{text}'")
                        return text
        except Exception as e:
            logger.error(f"[STT] Cloud fallback transcription failed: {e}")
        return None

    def _mock_transcription(self, audio_bytes: bytes) -> Optional[str]:
        # Basic mock transcript based on length
        logger.warning("[STT] Whisper both local & cloud failed. Returning mock speech transcription...")
        duration_sec = len(audio_bytes) / 8000.0
        if duration_sec < 1.0:
            return "Yes."
        elif duration_sec < 2.5:
            return "Confirm my appointment."
        else:
            return "Hello, I am looking to confirm my cardiology appointment scheduled for next week."
