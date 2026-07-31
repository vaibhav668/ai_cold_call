from app.core.logging import logger


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


class EndOfSpeechDetector:
    """
    Stateful VAD with dynamic noise floor estimation and hysteresis.

    Designed for real-world phone lines with varying noise/static levels.
    """

    def __init__(self) -> None:
        self._in_speech = False
        self._speech_frames = 0
        self._silence_frames = 0
        self._speech_confirmed = False

        # Dynamic noise floor tracking (adapts to line hum/static)
        self.noise_floor = 150.0

    def process_frame(self, audio_chunk: bytes) -> str | None:
        """
        Process one 20ms audio frame.

        Returns:
            'speech_start'  — customer confirmed speaking
            'speech_end'    — customer finished utterance
            None            — no event this frame
        """
        rms = _rms(audio_chunk)

        # 1. Update dynamic noise floor
        if rms < self.noise_floor:
            # Adapt quickly to lower energy levels (silence/drops)
            self.noise_floor = 0.95 * self.noise_floor + 0.05 * rms
        else:
            # Adapt very slowly to higher background noise if not speaking
            if not self._in_speech:
                self.noise_floor = 0.999 * self.noise_floor + 0.001 * rms

        # Clamp noise floor to safe phone line limits (50 to 800 RMS)
        self.noise_floor = max(50.0, min(800.0, self.noise_floor))

        # 2. Derive dynamic thresholds relative to current noise floor
        # Minimum thresholds protect against random silence/clicks
        speech_threshold = max(380.0, self.noise_floor + 250.0)
        silence_threshold = max(200.0, self.noise_floor + 100.0)

        # 3. State machine
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
                if self._silence_frames >= 20:  # ~400ms silence timeout
                    self._in_speech = False
                    self._speech_frames = 0
                    self._silence_frames = 0
                    self._speech_confirmed = False
                    return 'speech_end'
            else:
                # Speech continued — reset silence counter
                self._silence_frames = 0

        return None

    def reset(self) -> None:
        """Reset state tracking (preserves noise floor history)."""
        self._in_speech = False
        self._speech_frames = 0
        self._silence_frames = 0
        self._speech_confirmed = False

    @property
    def is_speaking(self) -> bool:
        return self._in_speech

    @property
    def speech_threshold(self) -> float:
        return max(380.0, self.noise_floor + 250.0)

    @property
    def silence_threshold(self) -> float:
        return max(200.0, self.noise_floor + 100.0)


class VADService:
    """Legacy single-frame is_speech check preserved for backward compatibility."""

    def __init__(self, threshold: float = 380.0) -> None:
        self.threshold = threshold

    def is_speech(self, audio_chunk: bytes) -> bool:
        return _rms(audio_chunk) > self.threshold
