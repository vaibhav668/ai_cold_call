import asyncio
from typing import AsyncGenerator, Optional
from app.core.config import settings
from app.services.speech.tts.base import TextToSpeechProvider
from app.services.speech.tts.melotts_provider import MeloTTSProvider

class VoiceService:
    """
    Service Facade representing the Text-to-Speech (TTS) interface.
    Conversation Engine and Telephony systems use this facade to synthesize audio.
    """

    def __init__(self) -> None:
        provider_name = settings.TTS_PROVIDER.lower()
        if provider_name == "melotts":
            self.provider: TextToSpeechProvider = MeloTTSProvider()
        else:
            self.provider: TextToSpeechProvider = MeloTTSProvider()

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
