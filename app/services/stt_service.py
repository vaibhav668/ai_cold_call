import os
from typing import Optional
from app.core.config import settings
from app.services.speech.stt.base import SpeechToTextProvider
from app.services.speech.stt.faster_whisper_provider import FasterWhisperProvider

class SpeechService:
    """
    Service Facade representing the Speech-to-Text (STT) interface.
    Conversation Engine and Telephony systems use this facade to transcribe voice.
    """

    def __init__(self) -> None:
        provider_name = settings.STT_PROVIDER.lower()
        if provider_name == "faster_whisper":
            self.provider: SpeechToTextProvider = FasterWhisperProvider()
        else:
            self.provider: SpeechToTextProvider = FasterWhisperProvider()

    @classmethod
    async def warmup(cls) -> float:
        """Warms up the STT singleton during application boot."""
        provider = FasterWhisperProvider()
        return await FasterWhisperProvider.warmup(provider.model_size)

    async def transcribe_utterance(
        self,
        audio_bytes: bytes,
        language: Optional[str] = None
    ) -> Optional[str]:
        """Transcribe raw G.711 mu-law audio bytes using the configured STT provider."""
        return await self.provider.transcribe_utterance(audio_bytes, language=language)
