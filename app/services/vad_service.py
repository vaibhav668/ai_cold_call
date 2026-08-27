import audioop
from typing import Optional
from app.core.config import settings
from app.core.logging import logger
from app.services.speech.vad.base import VoiceActivityDetector
from app.services.speech.vad.silero_provider import SileroVADProvider

def _rms_ulaw(audio_chunk: bytes) -> float:
    if not audio_chunk:
        return 0.0
    pcm_bytes = audioop.ulaw2lin(audio_chunk, 2)
    return audioop.rms(pcm_bytes, 2)

class LegacyRMSDetector(VoiceActivityDetector):
    """Fallback VAD using dynamic noise floor estimation."""
    def __init__(self) -> None:
        self._in_speech = False
        self._speech_frames = 0
        self._silence_frames = 0
        self._speech_confirmed = False
        self.noise_floor = None
        self._name_mode_frames = 35  # 35 * 20ms = 700ms base (extended to 45=900ms in name-collection mode)

    def process_frame(self, audio_chunk: bytes) -> Optional[str]:
        rms = _rms_ulaw(audio_chunk)
        if self.noise_floor is None:
            self.noise_floor = max(50.0, min(800.0, rms))

        if rms < self.noise_floor:
            self.noise_floor = 0.90 * self.noise_floor + 0.10 * rms
        else:
            if not self._in_speech:
                self.noise_floor = 0.98 * self.noise_floor + 0.02 * rms

        self.noise_floor = max(50.0, min(800.0, self.noise_floor))

        speech_threshold = max(180.0, self.noise_floor + 100.0)
        silence_threshold = max(120.0, self.noise_floor + 40.0)

        if not self._in_speech:
            if rms > speech_threshold:
                self._speech_frames += 1
                if self._speech_frames >= 2 and not self._speech_confirmed:
                    self._in_speech = True
                    self._speech_confirmed = True
                    self._silence_frames = 0
                    return 'speech_start'
            else:
                self._speech_frames = max(0, self._speech_frames - 1)
        else:
            if rms < silence_threshold:
                self._silence_frames += 1
                if self._silence_frames >= self._name_mode_frames:  # 30 frames * 20ms = ~600ms silence — prevents early name cutoffs
                    self._in_speech = False
                    self._speech_frames = 0
                    self._silence_frames = 0
                    self._speech_confirmed = False
                    return 'speech_end'
            else:
                self._silence_frames = 0
        return None

    def reset(self) -> None:
        self._in_speech = False
        self._speech_frames = 0
        self._silence_frames = 0
        self._speech_confirmed = False
        # Note: do NOT reset _name_mode_frames here — it's set by the caller
        # and should persist across VAD resets within the same utterance phase.

class EndOfSpeechDetector:
    def __init__(self) -> None:
        from app.core.config import check_low_memory
        self._fallback_provider = LegacyRMSDetector()

        if check_low_memory():
            logger.info("[VAD] Low memory deployment detected. Forcing lightweight Legacy RMS VAD to conserve CPU/RAM.")
            self.provider = self._fallback_provider
        else:
            provider_name = settings.VAD_PROVIDER.lower()
            if provider_name == "silero":
                self.provider = SileroVADProvider()
                if self.provider.model is None or self.provider.model == "FAILED":
                    logger.warning("[VAD] Silero VAD failed. Falling back to dynamic RMS VAD.")
                    self.provider = self._fallback_provider
            else:
                self.provider = self._fallback_provider

    def process_frame(self, audio_chunk: bytes) -> Optional[str]:
        return self.provider.process_frame(audio_chunk)

    def reset(self) -> None:
        self.provider.reset()

    def set_name_collection_mode(self, enabled: bool) -> None:
        """
        Extend VAD trailing-silence tolerance when collecting a customer name.
        In name mode: 900ms silence gate (avoids cutting 'Akash' at 600ms pause).
        In normal mode: restores to base 600ms silence gate.
        Barge-in detection is completely unaffected — it listens for speech_start,
        which is controlled by the speech threshold, not the silence duration.
        """
        from app.services.speech.vad.silero_provider import SileroVADProvider
        from silero_vad import VADIterator
        if isinstance(self.provider, SileroVADProvider) and self.provider.vad_iterator is not None:
            # Silero's VADIterator must be recreated to change min_silence_duration_ms
            try:
                target_ms = 900 if enabled else self.provider._base_silence_ms
                if self.provider._current_silence_ms != target_ms:
                    self.provider.vad_iterator = VADIterator(
                        self.provider.model,
                        threshold=0.4,
                        sampling_rate=16000,
                        min_silence_duration_ms=target_ms,
                        speech_pad_ms=60,
                    )
                    self.provider._current_silence_ms = target_ms
                    mode_label = "NAME-COLLECTION (900ms)" if enabled else f"NORMAL ({target_ms}ms)"
                    logger.info(f"[VAD] Silence tolerance switched to {mode_label}")
            except Exception as e:
                logger.warning(f"[VAD] set_name_collection_mode failed: {e}")
        elif isinstance(self.provider, LegacyRMSDetector):
            # LegacyRMS: adjust the silence frame count directly
            # 30 frames = 600ms (normal), 45 frames = 900ms (name mode)
            self.provider._name_mode_frames = 45 if enabled else 30

    @property
    def is_speaking(self) -> bool:
        if isinstance(self.provider, SileroVADProvider):
            return self.provider.is_speaking
        return self._fallback_provider._in_speech

class VADService:
    """Legacy single-frame check kept for backwards compatibility."""

    def __init__(self, threshold: float = 380.0) -> None:
        self.threshold = threshold

    def is_speech(self, audio_chunk: bytes) -> bool:
        return _rms_ulaw(audio_chunk) > self.threshold
