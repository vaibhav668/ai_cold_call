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

def linear2ulaw(sample: int) -> int:
    """Converts a 16-bit signed linear PCM sample (-32768 to 32767) to 8-bit G.711 u-law."""
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

class EdgeTTSProvider(TextToSpeechProvider):
    def __init__(self, voice: str = "en-US-AriaNeural") -> None:
        self.voice = voice

    async def stream_speech(self, text: str) -> AsyncGenerator[bytes, None]:
        try:
            import edge_tts
            import miniaudio
            
            communicate = edge_tts.Communicate(text, self.voice)
            mp3_bytes = b""
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    mp3_bytes += chunk["data"]
                    
            if not mp3_bytes:
                logger.warning("Edge TTS returned empty audio. Falling back to mock TTS.")
                async for chunk in MockTTSProvider().stream_speech(text):
                    yield chunk
                return
                
            decoded = miniaudio.decode(mp3_bytes, sample_rate=8000, nchannels=1, output_format=miniaudio.SampleFormat.SIGNED16)
            mulaw_bytes = bytes(linear2ulaw(s) for s in decoded.samples)
            
            # Slice into 160-byte (20ms) frames for Plivo streaming
            for i in range(0, len(mulaw_bytes), 160):
                yield mulaw_bytes[i:i+160]
                
        except Exception as e:
            logger.warning(f"Edge TTS synthesis failed: {e}. Falling back to mock TTS.")
            async for chunk in MockTTSProvider().stream_speech(text):
                yield chunk

class CoquiXTTSProvider(TextToSpeechProvider):
    def __init__(self) -> None:
        self.tts = None

    def _init_tts(self) -> None:
        if not self.tts:
            try:
                from TTS.api import TTS
                logger.info("Initializing local Coqui XTTS v2 model on CPU...")
                self.tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to("cpu")
            except Exception as e:
                logger.warning(f"Could not load local Coqui XTTS model: {e}. Falling back to Edge TTS.")
                self.tts = None

    async def stream_speech(self, text: str) -> AsyncGenerator[bytes, None]:
        self._init_tts()
        if not self.tts:
            async for chunk in EdgeTTSProvider().stream_speech(text):
                yield chunk
            return

        try:
            temp_wav = os.path.join(tempfile.gettempdir(), "xtts_output.wav")
            ref_speaker = "static/samples/speaker.wav"
            if not os.path.exists(ref_speaker):
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
            async for chunk in EdgeTTSProvider().stream_speech(text):
                yield chunk

class MockTTSProvider(TextToSpeechProvider):
    async def stream_speech(self, text: str) -> AsyncGenerator[bytes, None]:
        # Generate 50 frames (~1 second) of audible 440Hz tone in G.711 mu-law PCMU
        tone_chunk = bytes([0x1E, 0x0B, 0x02, 0x02, 0x0B, 0x1E, 0x9E, 0x8B, 0x82, 0x82, 0x8B, 0x9E] * 13 + [0x1E, 0x0B, 0x02, 0x02])
        for _ in range(50):
            yield tone_chunk

class VoiceService:
    def __init__(self) -> None:
        self.provider: TextToSpeechProvider = EdgeTTSProvider()

    async def stream_speech(self, text: str) -> AsyncGenerator[bytes, None]:
        async for chunk in self.provider.stream_speech(text):
            yield chunk
