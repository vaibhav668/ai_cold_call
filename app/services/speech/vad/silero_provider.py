import audioop
import asyncio
import numpy as np
import collections
from typing import Optional
from app.core.logging import logger
from app.services.speech.vad.base import VoiceActivityDetector


def _ulaw_chunk_to_float32_16k(audio_chunk: bytes) -> np.ndarray:
    """
    Convert a G.711 mu-law 8kHz chunk → float32 numpy array at 16kHz.
    Uses C-level audioop — no Python loops, minimal GIL hold.
    """
    pcm_8k = audioop.ulaw2lin(audio_chunk, 2)
    pcm_16k, _ = audioop.ratecv(pcm_8k, 2, 1, 8000, 16000, None)
    return np.frombuffer(pcm_16k, dtype=np.int16).astype(np.float32) / 32768.0


class SileroVADProvider(VoiceActivityDetector):
    """
    Production-grade VAD provider utilizing Silero VAD.
    Uses frame accumulation to handle G.711 20ms chunks (320 samples at 16kHz)
    against Silero's 512-sample inference block requirement.

    IMPORTANT: process_frame() is a pure synchronous method intended to be run
    inside asyncio.get_event_loop().run_in_executor() — it must never be called
    directly from an async coroutine running on the event loop thread.
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
        """
        Process one 160-byte G.711 mu-law frame.
        CPU-bound: must be called via run_in_executor from async code.
        Returns: 'speech_start', 'speech_end', or None.
        """
        if not audio_chunk or self.model is None or self.vad_iterator is None:
            return None

        # 1. Decode G.711 mu-law (8kHz) → float32 16kHz using C audioop (no loops)
        x_16k = _ulaw_chunk_to_float32_16k(audio_chunk)

        # 2. Append to accumulator
        self._accumulator.extend(x_16k.tolist())

        event = None

        # 3. Process all available 512-sample blocks
        while len(self._accumulator) >= 512:
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
