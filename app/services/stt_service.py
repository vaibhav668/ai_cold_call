from abc import ABC, abstractmethod
from typing import Optional, List
import numpy as np
from app.services.vad_service import decode_ulaw_sample
from app.core.logging import logger

def ulaw_to_pcm_array(audio_chunk: bytes) -> np.ndarray:
    """Decodes mu-law audio chunk to 16-bit linear PCM and normalizes to float32 [-1.0, 1.0]."""
    samples = [decode_ulaw_sample(b) for b in audio_chunk]
    return np.array(samples, dtype=np.float32) / 32768.0

class SpeechToTextProvider(ABC):
    @abstractmethod
    async def transcribe_chunk(self, audio_chunk: bytes) -> Optional[str]:
        """Convert incoming raw audio chunk into transcription text."""
        pass

class FasterWhisperProvider(SpeechToTextProvider):
    def __init__(self) -> None:
        self.model = None
        self.accumulated_audio: List[float] = []
        self.mock_provider = MockSTTProvider()

    def _init_model(self) -> None:
        if not self.model:
            try:
                from faster_whisper import WhisperModel
                logger.info("Loading local Faster-Whisper 'tiny' model on CPU...")
                self.model = WhisperModel("tiny", device="cpu", compute_type="int8")
            except Exception as e:
                logger.warning(f"Could not initialize Faster-Whisper local model: {e}. Falling back to mock transcribers.")
                self.model = None

    async def transcribe_chunk(self, audio_chunk: bytes) -> Optional[str]:
        self._init_model()
        if not self.model:
            return await self.mock_provider.transcribe_chunk(audio_chunk)

        # Append float32 samples
        pcm_samples = ulaw_to_pcm_array(audio_chunk)
        self.accumulated_audio.extend(pcm_samples.tolist())

        # Process transcription block when sufficient audio frames are buffered (e.g. 5 seconds at 8kHz = 40000 samples)
        if len(self.accumulated_audio) >= 40000:
            audio_data = np.array(self.accumulated_audio, dtype=np.float32)
            self.accumulated_audio = [] # Flush buffer
            
            try:
                segments, info = self.model.transcribe(audio_data, beam_size=1)
                text = " ".join([segment.text for segment in segments]).strip()
                return text if text else None
            except Exception as e:
                logger.error(f"Faster-Whisper local transcription failed: {e}")
                return None
        return None

class MockSTTProvider(SpeechToTextProvider):
    def __init__(self) -> None:
        self.chunks_received = 0

    async def transcribe_chunk(self, audio_chunk: bytes) -> Optional[str]:
        self.chunks_received += 1
        if self.chunks_received >= 10:
            self.chunks_received = 0
            return "hello i would like to reschedule my appointment"
        return None

class SpeechService:
    def __init__(self) -> None:
        # Default to FasterWhisperProvider for local speech processing
        self.provider: SpeechToTextProvider = FasterWhisperProvider()

    async def transcribe_chunk(self, audio_chunk: bytes) -> Optional[str]:
        return await self.provider.transcribe_chunk(audio_chunk)
