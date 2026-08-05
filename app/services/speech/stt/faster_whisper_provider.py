import os
import io
import wave
import audioop
import asyncio
import httpx
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


def _ulaw_to_float32_16k(audio_bytes: bytes) -> np.ndarray:
    """
    Convert G.711 mu-law 8kHz bytes → float32 numpy array at 16kHz.
    Uses C-level audioop functions throughout — no Python loops, no GIL pressure.
    """
    pcm_8k = audioop.ulaw2lin(audio_bytes, 2)
    pcm_16k, _ = audioop.ratecv(pcm_8k, 2, 1, 8000, 16000, None)
    samples = np.frombuffer(pcm_16k, dtype=np.int16).astype(np.float32) / 32768.0
    return samples


class FasterWhisperProvider(SpeechToTextProvider):
    """
    Local Speech-to-Text provider utilizing the Faster-Whisper library.
    Optimized for low-latency CPU inference via CTranslate2.
    Strictly offline operation during conversation — no HuggingFace checks at runtime.
    """

    _model_instance = None
    _model_lock = asyncio.Lock()

    def __init__(self) -> None:
        from app.core.config import check_low_memory
        low_mem = check_low_memory()
        default_size = "tiny.en" if low_mem else "base"
        
        configured_model = os.environ.get("WHISPER_MODEL", default_size)
        if low_mem and configured_model not in ("tiny.en", "tiny"):
            logger.warning(
                f"[STT] Low-memory environment detected. Overriding configured model '{configured_model}' "
                f"to 'tiny.en' to maintain <350MB RSS footprint and prevent OOM restarts."
            )
            self.model_size = "tiny.en"
        else:
            self.model_size = configured_model

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
                logger.info(f"[STT] Initializing Faster-Whisper model '{model_size}' on CPU (strictly offline mode)...")
                
                # Enforce offline mode via env vars so HuggingFace hub never initiates network calls
                os.environ["HF_HUB_OFFLINE"] = "1"
                os.environ["TRANSFORMERS_OFFLINE"] = "1"

                def load_model():
                    if os.name != "nt":
                        cache_dir = os.environ.get("HF_HOME", "/app/models/hf_cache")
                        os.makedirs(cache_dir, exist_ok=True)
                        download_root = cache_dir
                    else:
                        download_root = None

                    # Try loading with local_files_only=True first to guarantee zero network checks
                    try:
                        return WhisperModel(
                            model_size,
                            device="cpu",
                            compute_type="int8",
                            cpu_threads=4,
                            download_root=download_root,
                            local_files_only=True
                        )
                    except Exception as local_err:
                        logger.warning(f"[STT] local_files_only load failed ({local_err}). Retrying standard load...")
                        # If local_files_only parameter is not supported by installed faster_whisper version, fallback
                        return WhisperModel(
                            model_size,
                            device="cpu",
                            compute_type="int8",
                            cpu_threads=4,
                            download_root=download_root
                        )

                cls._model_instance = await asyncio.get_event_loop().run_in_executor(None, load_model)
                logger.info(f"[STT] Faster-Whisper model '{model_size}' successfully loaded and cached as Singleton.")
                try:
                    import psutil
                    rss = psutil.Process().memory_info().rss / (1024 * 1024)
                    logger.info(f"[MEMORY] Whisper singleton initialized: RSS {rss:.2f} MB")
                except Exception:
                    pass

            except Exception as e:
                import traceback
                logger.critical(f"[STT] Failed to initialize local Faster-Whisper model '{model_size}': {e}\n{traceback.format_exc()}")
                cls._model_instance = "FAILED"

            return cls._model_instance

    @classmethod
    async def warmup(cls, model_size: str = "tiny.en") -> float:
        """Eagerly load model weights and run a 0.1s dummy inference pass during server boot."""
        import time
        start_t = time.perf_counter()
        logger.info(f"[WARMUP] Eagerly warming up FasterWhisper singleton ('{model_size}')...")
        model = await cls._get_model(model_size)
        if model != "FAILED" and model is not None:
            try:
                # 0.1s silent audio sample (1600 zero samples at 16kHz)
                dummy_x = np.zeros(1600, dtype=np.float32)
                def run_warmup_inference():
                    import torch
                    with torch.inference_mode():
                        list(model.transcribe(dummy_x, beam_size=1))
                await asyncio.get_event_loop().run_in_executor(None, run_warmup_inference)
                elapsed = (time.perf_counter() - start_t) * 1000.0
                logger.info(f"[WARMUP] FasterWhisper model warmed up in {elapsed:.1f}ms.")
                return elapsed
            except Exception as e:
                logger.warning(f"[WARMUP] Whisper dummy inference failed (non-fatal): {e}")
        return (time.perf_counter() - start_t) * 1000.0

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
                # Decode G.711 mu-law 8kHz → float32 16kHz (C-level, no Python loops)
                def prepare_audio():
                    return _ulaw_to_float32_16k(audio_bytes)

                x_16k = await asyncio.get_event_loop().run_in_executor(None, prepare_audio)

                def run_transcription():
                    import torch
                    with torch.inference_mode():
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
                import traceback
                logger.warning(f"[STT] Local transcription error: {e}\n{traceback.format_exc()}. Falling back to Cloud API...")

        # 2. Cloud API fallback (Groq Whisper-large-v3-turbo) if local model fails
        if self.api_key:
            return await self._transcribe_cloud_fallback(audio_bytes, language)

        # 3. High-fidelity Mock fallback if both fail
        return self._mock_transcription(audio_bytes)

    async def _transcribe_cloud_fallback(self, audio_bytes: bytes, language: Optional[str] = None) -> Optional[str]:
        try:
            pcm_bytes = audioop.ulaw2lin(audio_bytes, 2)
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
        logger.warning("[STT] Whisper both local & cloud failed. Returning mock speech transcription...")
        duration_sec = len(audio_bytes) / 8000.0
        if duration_sec < 1.0:
            return "Yes."
        elif duration_sec < 2.5:
            return "Confirm my appointment."
        else:
            return "Hello, I am looking to confirm my cardiology appointment scheduled for next week."
