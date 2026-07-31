import io
import wave
import struct
import os
import asyncio
from abc import ABC, abstractmethod
from typing import Optional
import httpx
import numpy as np
from app.core.logging import logger


# ─────────────────────────────────────────────
# Audio conversion utilities
# ─────────────────────────────────────────────

def ulaw2linear(ulaw_byte: int) -> int:
    """Converts an 8-bit G.711 u-law sample to 16-bit signed linear PCM."""
    ulaw_byte = ~ulaw_byte & 0xFF
    sign = ulaw_byte & 0x80
    exponent = (ulaw_byte >> 4) & 0x07
    mantissa = ulaw_byte & 0x0F
    sample = ((mantissa << 3) + 0x84) << exponent
    sample -= 0x84
    return -sample if sign else sample


def mulaw_to_wav(mulaw_bytes: bytes, sample_rate: int = 8000) -> bytes:
    """Converts raw G.711 mu-law bytes into a 16-bit mono WAV byte array."""
    pcm_samples = [ulaw2linear(b) for b in mulaw_bytes]
    pcm_bytes = struct.pack(f"<{len(pcm_samples)}h", *pcm_samples)

    wav_io = io.BytesIO()
    with wave.open(wav_io, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)  # 16-bit
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_bytes)
    return wav_io.getvalue()


def ulaw_to_pcm_array(audio_chunk: bytes) -> np.ndarray:
    """Decodes mu-law to float32 [-1.0, 1.0] array."""
    from app.services.vad_service import decode_ulaw_sample
    samples = [decode_ulaw_sample(b) for b in audio_chunk]
    return np.array(samples, dtype=np.float32) / 32768.0


# ─────────────────────────────────────────────
# Abstract provider
# ─────────────────────────────────────────────

class SpeechToTextProvider(ABC):
    @abstractmethod
    async def transcribe_utterance(self, audio_bytes: bytes) -> Optional[str]:
        """Transcribe a complete utterance (called once after end-of-speech)."""
        pass

    def clear_buffer(self) -> None:
        """Optional: reset any internal audio buffer (e.g., on barge-in)."""
        pass


# ─────────────────────────────────────────────
# Groq Whisper (primary cloud STT)
# ─────────────────────────────────────────────

# Tokens that Whisper returns for silence / background noise — ignore these
_SILENCE_TOKENS = {
    "", ".", "..", "...", "Thank you.", "Bye.", "Thanks.", "you",
    "You.", "you.", "Okay.", "okay.", "Hmm.", "hmm.", "Uh.", "uh.",
    "Mm.", "mm.", "Mmm.", "mmm.", "[Music]", "[Applause]", "[Laughter]",
}


class GroqWhisperProvider(SpeechToTextProvider):
    """
    Cloud STT using Groq's Whisper-large-v3-turbo.

    Receives a complete captured utterance (collected by the VAD end-of-speech
    detector in the telephony handler) and returns a transcription.
    """

    def __init__(self) -> None:
        self.api_key = os.environ.get("GROQ_API_KEY", "")

    async def transcribe_utterance(self, audio_bytes: bytes) -> Optional[str]:
        """Convert a complete utterance (bytes of G.711 mu-law) to text."""
        if not audio_bytes or len(audio_bytes) < 160:  # < 20ms — ignore
            return None

        try:
            wav_bytes = mulaw_to_wav(audio_bytes)
            async with httpx.AsyncClient(timeout=12.0) as client:
                files = {"file": ("speech.wav", wav_bytes, "audio/wav")}
                data = {"model": "whisper-large-v3-turbo", "language": "en"}
                headers = {"Authorization": f"Bearer {self.api_key}"}

                response = await client.post(
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    files=files,
                    data=data,
                    headers=headers,
                )

                if response.status_code == 429:
                    retry_after = int(response.headers.get("retry-after", 2))
                    logger.warning(
                        f"Groq STT rate-limited. Waiting {retry_after}s before retry..."
                    )
                    await asyncio.sleep(retry_after)
                    # Retry once
                    response = await client.post(
                        "https://api.groq.com/openai/v1/audio/transcriptions",
                        files=files,
                        data=data,
                        headers=headers,
                    )

                if response.status_code == 200:
                    text = response.json().get("text", "").strip()
                    if text and text not in _SILENCE_TOKENS and len(text) > 2:
                        logger.info(f"[STT] Groq Whisper transcribed: '{text}'")
                        return text
                    else:
                        logger.debug(f"[STT] Ignoring silence token: '{text}'")
                        return None
                else:
                    logger.error(
                        f"[STT] Groq Whisper HTTP {response.status_code}: {response.text[:200]}"
                    )
                    return None

        except Exception as e:
            logger.error(f"[STT] Groq Whisper transcription failed: {e}")
            return None


# ─────────────────────────────────────────────
# Public service facade
# ─────────────────────────────────────────────

class SpeechService:
    """
    Facade over the STT provider.

    Usage in telephony handler:
        1. Feed each 20ms audio frame to the VAD end-of-speech detector.
        2. When VAD fires 'speech_end', collect all buffered utterance bytes.
        3. Call: text = await stt.transcribe_utterance(utterance_bytes)
    """

    def __init__(self) -> None:
        self.provider: SpeechToTextProvider = GroqWhisperProvider()

    async def transcribe_utterance(self, audio_bytes: bytes) -> Optional[str]:
        return await self.provider.transcribe_utterance(audio_bytes)
