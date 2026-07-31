import asyncio
from abc import ABC, abstractmethod
from typing import AsyncGenerator, Optional
import os
from app.core.logging import logger


# ─────────────────────────────────────────────
# Audio conversion
# ─────────────────────────────────────────────

def linear2ulaw(sample: int) -> int:
    """Converts a 16-bit signed linear PCM sample to 8-bit G.711 u-law."""
    BIAS = 0x84
    CLIP = 32635
    sign = (sample >> 8) & 0x80
    if sign != 0:
        sample = -sample
    if sample > CLIP:
        sample = CLIP
    sample = sample + BIAS
    exponent = 7
    exp_mask = 0x4000
    while (sample & exp_mask) == 0 and exponent > 0:
        exponent -= 1
        exp_mask >>= 1
    mantissa = (sample >> (exponent + 3)) & 0x0F
    ulaw_byte = ~(sign | (exponent << 4) | mantissa) & 0xFF
    return ulaw_byte


# ─────────────────────────────────────────────
# Abstract provider
# ─────────────────────────────────────────────

class TextToSpeechProvider(ABC):
    @abstractmethod
    async def stream_speech(
        self,
        text: str,
        cancel_event: Optional[asyncio.Event] = None,
    ) -> AsyncGenerator[bytes, None]:
        """
        Yield G.711 mu-law 160-byte (20ms) frames.
        Stop immediately if cancel_event is set.
        """
        yield b""


# ─────────────────────────────────────────────
# Edge TTS (primary — uses Microsoft neural voices, no API key)
# ─────────────────────────────────────────────

class EdgeTTSProvider(TextToSpeechProvider):
    def __init__(self, voice: str = "en-US-AriaNeural") -> None:
        self.voice = voice

    async def stream_speech(
        self,
        text: str,
        cancel_event: Optional[asyncio.Event] = None,
    ) -> AsyncGenerator[bytes, None]:
        try:
            import edge_tts
            import miniaudio

            communicate = edge_tts.Communicate(text, self.voice)
            mp3_bytes = b""
            async for chunk in communicate.stream():
                if cancel_event and cancel_event.is_set():
                    logger.info("[TTS] Cancelled during synthesis — discarding.")
                    return
                if chunk["type"] == "audio":
                    mp3_bytes += chunk["data"]

            if not mp3_bytes:
                logger.warning("[TTS] Edge TTS returned empty audio.")
                return

            decoded = miniaudio.decode(
                mp3_bytes,
                sample_rate=8000,
                nchannels=1,
                output_format=miniaudio.SampleFormat.SIGNED16,
            )
            mulaw_bytes = bytes(linear2ulaw(s) for s in decoded.samples)

            # Yield 160-byte (20ms at 8kHz) frames
            for i in range(0, len(mulaw_bytes), 160):
                if cancel_event and cancel_event.is_set():
                    logger.info("[TTS] Cancelled during playback streaming.")
                    return
                yield mulaw_bytes[i : i + 160]

        except Exception as e:
            logger.error(f"[TTS] Edge TTS synthesis failed: {e}")
            # No silent fallback — caller will handle missing audio
            return


# ─────────────────────────────────────────────
# Public service facade
# ─────────────────────────────────────────────

class VoiceService:
    def __init__(self) -> None:
        self.provider: TextToSpeechProvider = EdgeTTSProvider()

    async def stream_speech(
        self,
        text: str,
        cancel_event: Optional[asyncio.Event] = None,
    ) -> AsyncGenerator[bytes, None]:
        async for chunk in self.provider.stream_speech(text, cancel_event=cancel_event):
            yield chunk
