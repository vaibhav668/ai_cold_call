import asyncio
from typing import AsyncGenerator, Optional
from app.core.config import settings
from app.services.speech.tts.base import TextToSpeechProvider
from app.services.speech.tts.melotts_provider import MeloTTSProvider
from app.services.speech.tts.edge_tts_provider import EdgeTTSProvider
from app.core.logging import logger

class VoiceService:
    """
    Service Facade representing the Text-to-Speech (TTS) interface.
    Conversation Engine and Telephony systems use this facade to synthesize audio.

    Provider selection order:
      1. TTS_PROVIDER env var ("melotts" or "edge_tts")
      2. If MeloTTS is configured but unavailable, auto-fallback to EdgeTTS.
    """

    def __init__(self) -> None:
        provider_name = settings.TTS_PROVIDER.lower()
        if provider_name == "edge_tts":
            self.provider: TextToSpeechProvider = EdgeTTSProvider()
            logger.info("[VoiceService] Using EdgeTTSProvider (Microsoft Edge TTS).")
        else:
            # Try MeloTTS; if it fails at init time, fall back to EdgeTTS
            try:
                candidate = MeloTTSProvider()
                # Quick health check: if the underlying model is a mock, switch to EdgeTTS
                if getattr(candidate, '_is_mock', False):
                    raise RuntimeError("MeloTTS is in mock mode.")
                self.provider = candidate
                logger.info("[VoiceService] Using MeloTTSProvider.")
            except Exception as e:
                logger.warning(f"[VoiceService] MeloTTS unavailable ({e}). Falling back to EdgeTTSProvider.")
                self.provider = EdgeTTSProvider()


    async def stream_speech(
        self,
        text: str,
        cancel_event: Optional[asyncio.Event] = None,
        language: Optional[str] = None,
        voice_config: Optional[dict] = None
    ) -> AsyncGenerator[bytes, None]:
        """Synthesize text into G.711 mu-law audio chunks using the configured TTS provider."""
        import inspect
        sig = inspect.signature(self.provider.stream_speech)
        if "voice_config" in sig.parameters:
            async for chunk in self.provider.stream_speech(text, cancel_event=cancel_event, language=language, voice_config=voice_config):
                yield chunk
        else:
            async for chunk in self.provider.stream_speech(text, cancel_event=cancel_event, language=language):
                yield chunk
