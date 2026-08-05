from typing import Optional
from app.core.config import settings
from app.core.logging import logger
from app.services.speech.vad.base import VoiceActivityDetector
from app.services.speech.vad.silero_provider import SileroVADProvider

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


def _rms(audio_chunk: bytes) -> float:
    """Compute RMS energy of a G.711 mu-law audio chunk."""
    if not audio_chunk:
        return 0.0
    total = sum(decode_ulaw_sample(b) ** 2 for b in audio_chunk)
    return (total / len(audio_chunk)) ** 0.5


class LegacyRMSDetector(VoiceActivityDetector):
    """Fallback VAD using dynamic noise floor estimation and energy thresholds."""

    def __init__(self) -> None:
        self._in_speech = False
        self._speech_frames = 0
        self._silence_frames = 0
        self._speech_confirmed = False
        self.noise_floor = None  # Lazy initialized on first frame

    def process_frame(self, audio_chunk: bytes) -> Optional[str]:
        rms = _rms(audio_chunk)

        # Lazy initialize noise floor to first frame's energy level
        if self.noise_floor is None:
            self.noise_floor = max(50.0, min(800.0, rms))

        # Update dynamic noise floor with faster adaptation rate for background hums
        if rms < self.noise_floor:
            self.noise_floor = 0.90 * self.noise_floor + 0.10 * rms
        else:
            if not self._in_speech:
                self.noise_floor = 0.98 * self.noise_floor + 0.02 * rms

        self.noise_floor = max(50.0, min(800.0, self.noise_floor))

        speech_threshold = max(380.0, self.noise_floor + 250.0)
        silence_threshold = max(200.0, self.noise_floor + 100.0)

        if not self._in_speech:
            if rms > speech_threshold:
                self._speech_frames += 1
                if self._speech_frames >= 3 and not self._speech_confirmed:
                    self._in_speech = True
                    self._speech_confirmed = True
                    self._silence_frames = 0
                    return 'speech_start'
            else:
                self._speech_frames = max(0, self._speech_frames - 1)
        else:
            if rms < silence_threshold:
                self._silence_frames += 1
                if self._silence_frames >= 20:
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

    @property
    def speech_threshold(self) -> float:
        floor = self.noise_floor if self.noise_floor is not None else 150.0
        return max(380.0, floor + 250.0)

    @property
    def silence_threshold(self) -> float:
        floor = self.noise_floor if self.noise_floor is not None else 150.0
        return max(200.0, floor + 100.0)


class EndOfSpeechDetector:
    """
    Facade class acting as the primary VAD service.
    Encapsulates Silero VAD with local RMS fallback for high availability.
    """

    def __init__(self) -> None:
        provider_name = settings.VAD_PROVIDER.lower()
        self.provider: VoiceActivityDetector = None
        self._fallback_provider = LegacyRMSDetector()

        if provider_name == "silero":
            self.provider = SileroVADProvider()
            if self.provider.model is None:
                logger.warning("[VAD] Silero load failed. Falling back to dynamic RMS VAD.")
                self.provider = self._fallback_provider
        else:
            self.provider = self._fallback_provider

    def process_frame(self, audio_chunk: bytes) -> Optional[str]:
        return self.provider.process_frame(audio_chunk)

    def reset(self) -> None:
        self.provider.reset()

    @property
    def is_speaking(self) -> bool:
        if isinstance(self.provider, SileroVADProvider):
            return self.provider.is_speaking
        return self._fallback_provider._in_speech

    @property
    def noise_floor(self) -> float:
        return self._fallback_provider.noise_floor

    @property
    def speech_threshold(self) -> float:
        return self._fallback_provider.speech_threshold

    @property
    def silence_threshold(self) -> float:
        return self._fallback_provider.silence_threshold


class VADService:
    """Legacy single-frame check kept for backwards compatibility."""

    def __init__(self, threshold: float = 380.0) -> None:
        self.threshold = threshold

    def is_speech(self, audio_chunk: bytes) -> bool:
        return _rms(audio_chunk) > self.threshold
