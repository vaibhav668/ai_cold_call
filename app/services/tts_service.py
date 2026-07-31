from abc import ABC, abstractmethod
from typing import AsyncGenerator
import os
import tempfile
from app.core.logging import logger

class TextToSpeechProvider(ABC):
    @abstractmethod
    async def stream_speech(self, text: str) -> AsyncGenerator[bytes, None]:
        """Convert text into streamed G.711 mu-law raw audio bytes."""
        yield b""

class CoquiXTTSProvider(TextToSpeechProvider):
    def __init__(self) -> None:
        self.tts = None

    def _init_tts(self) -> None:
        if not self.tts:
            try:
                from TTS.api import TTS
                logger.info("Initializing local Coqui XTTS v2 model on CPU...")
                # Lazy loading Coqui XTTS model
                self.tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to("cpu")
            except Exception as e:
                logger.warning(f"Could not load local Coqui XTTS model: {e}. Falling back to mock TTS.")
                self.tts = None

    async def stream_speech(self, text: str) -> AsyncGenerator[bytes, None]:
        self._init_tts()
        if not self.tts:
            mock = MockTTSProvider()
            async for chunk in mock.stream_speech(text):
                yield chunk
            return

        try:
            # XTTS synthesizes audio to file.
            # We clone the voice of a default speaker from static assets if available.
            temp_wav = os.path.join(tempfile.gettempdir(), "xtts_output.wav")
            
            # Simple reference speaker wav path (uses a placeholder or dummy sample)
            ref_speaker = "static/samples/speaker.wav"
            if not os.path.exists(ref_speaker):
                # Fallback to creating a dummy reference speaker file or write placeholder
                os.makedirs("static/samples", exist_ok=True)
                with open(ref_speaker, "wb") as f:
                    f.write(b"")
                    
            self.tts.tts_to_file(
                text=text,
                file_path=temp_wav,
                speaker_wav=ref_speaker,
                language="en"
            )
            
            if os.path.exists(temp_wav):
                with open(temp_wav, "rb") as f:
                    while True:
                        chunk = f.read(1024)
                        if not chunk:
                            break
                        yield chunk
                os.remove(temp_wav)
        except Exception as e:
            logger.error(f"Local Coqui XTTS synthesis failed: {e}")
            yield b""

class MockTTSProvider(TextToSpeechProvider):
    async def stream_speech(self, text: str) -> AsyncGenerator[bytes, None]:
        # Generate 50 frames (~1 second) of audible 440Hz tone in G.711 mu-law PCMU
        tone_chunk = bytes([0x1E, 0x0B, 0x02, 0x02, 0x0B, 0x1E, 0x9E, 0x8B, 0x82, 0x82, 0x8B, 0x9E] * 13 + [0x1E, 0x0B, 0x02, 0x02])
        for _ in range(50):
            yield tone_chunk

class VoiceService:
    def __init__(self) -> None:
        self.provider: TextToSpeechProvider = CoquiXTTSProvider()

    async def stream_speech(self, text: str) -> AsyncGenerator[bytes, None]:
        async for chunk in self.provider.stream_speech(text):
            yield chunk
