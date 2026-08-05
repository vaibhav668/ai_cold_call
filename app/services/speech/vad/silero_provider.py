import numpy as np
import collections
from typing import Optional
from app.core.logging import logger
from app.services.speech.vad.base import VoiceActivityDetector

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


class SileroVADProvider(VoiceActivityDetector):
    """
    Production-grade VAD provider utilizing Silero VAD.
    Uses frame accumulation to handle G.711 20ms chunks (320 samples at 16kHz)
    against Silero's 512-sample inference block requirement.
    """

    _model_instance = None

    def __init__(self) -> None:
        self.model = None
        self.vad_iterator = None
        self._accumulator = collections.deque()
        self._in_speech = False

        try:
            from silero_vad import load_silero_vad, VADIterator
            if SileroVADProvider._model_instance is None:
                logger.info("[VAD] Loading Silero VAD model...")
                SileroVADProvider._model_instance = load_silero_vad()
                try:
                    import psutil
                    rss = psutil.Process().memory_info().rss / (1024 * 1024)
                    logger.info(f"[MEMORY] Silero loaded: RSS {rss:.2f} MB")
                except Exception:
                    pass
            
            self.model = SileroVADProvider._model_instance
            if self.model is not None and self.model != "FAILED":
                # min_silence_duration_ms=400 (20 frames of 20ms) matches our previous VAD timing
                self.vad_iterator = VADIterator(
                    self.model,
                    threshold=0.5,
                    sampling_rate=16000,
                    min_silence_duration_ms=400,
                    speech_pad_ms=30
                )
                logger.info("[VAD] Silero VAD iterator initialized.")
        except Exception as e:
            logger.error(f"[VAD] Failed to initialize Silero VAD model: {e}. Fallback to RMS VAD enabled.")
            SileroVADProvider._model_instance = "FAILED"

    def process_frame(self, audio_chunk: bytes) -> Optional[str]:
        if not audio_chunk or self.model is None or self.vad_iterator is None:
            return None

        # 1. Decode G.711 mu-law (8kHz) to float32 linear PCM [-1.0, 1.0]
        samples_8k = [decode_ulaw_sample(b) for b in audio_chunk]
        x_8k = np.array(samples_8k, dtype=np.float32) / 32768.0

        # 2. Resample to 16kHz by duplicating samples
        x_16k = np.repeat(x_8k, 2)

        # 3. Append to accumulator
        self._accumulator.extend(x_16k.tolist())

        event = None

        # 4. Process all available 512-sample blocks
        while len(self._accumulator) >= 512:
            # Extract first 512 samples
            block = []
            for _ in range(512):
                block.append(self._accumulator.popleft())
            
            # Feed block to Silero VADIterator
            try:
                import torch
                block_tensor = torch.tensor(block, dtype=torch.float32)
                with torch.inference_mode():
                    result = self.vad_iterator(block_tensor)
                
                if result:
                    if "start" in result:
                        self._in_speech = True
                        event = "speech_start"
                    elif "end" in result:
                        self._in_speech = False
                        event = "speech_end"
            except Exception as e:
                logger.warning(f"[VAD] Silero frame iteration error: {e}")

        return event

    def reset(self) -> None:
        self._accumulator.clear()
        self._in_speech = False
        if self.vad_iterator is not None:
            try:
                self.vad_iterator.reset()
            except Exception:
                pass

    @property
    def is_speaking(self) -> bool:
        return self._in_speech
