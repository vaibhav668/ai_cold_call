from app.core.logging import logger


def decode_ulaw_sample(u_val: int) -> int:
    """Decodes G.711 mu-law byte sample back to a 16-bit linear PCM signed integer."""
    # ITU-T G.711 mu-law decoding algorithm
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
    Stateful VAD with hysteresis for end-of-speech detection.

    State machine:
        IDLE → SPEAKING (when RMS > speech_threshold for min_speech_frames)
        SPEAKING → TRAILING_SILENCE (when RMS < silence_threshold)
        TRAILING_SILENCE → IDLE / speech_ended (when silence_frames > silence_timeout_frames)
        TRAILING_SILENCE → SPEAKING (if voice resumes before timeout)

    Yields events: 'speech_start', 'speech_end', or None each frame.
    """

    # Tuned for 8kHz G.711 mu-law phone audio
    SPEECH_THRESHOLD = 900.0    # RMS above this = speech active
    SILENCE_THRESHOLD = 500.0   # RMS below this = silence
    MIN_SPEECH_FRAMES = 4       # ~80ms minimum utterance (4 × 20ms frames)
    SILENCE_TIMEOUT_FRAMES = 25 # ~500ms of silence = end-of-utterance

    def __init__(self) -> None:
        self._in_speech = False
        self._speech_frames = 0
        self._silence_frames = 0
        self._speech_confirmed = False  # True once MIN_SPEECH_FRAMES reached

    def process_frame(self, audio_chunk: bytes) -> str | None:
        """
        Process one 20ms audio frame.

        Returns:
            'speech_start'  — customer just started speaking (confirmed)
            'speech_end'    — customer finished speaking (silence timeout hit)
            None            — no event this frame
        """
        rms = _rms(audio_chunk)

        if not self._in_speech:
            if rms > self.SPEECH_THRESHOLD:
                self._speech_frames += 1
                if self._speech_frames >= self.MIN_SPEECH_FRAMES and not self._speech_confirmed:
                    self._in_speech = True
                    self._speech_confirmed = True
                    self._silence_frames = 0
                    return 'speech_start'
            else:
                self._speech_frames = 0
        else:
            # Currently in speech
            if rms < self.SILENCE_THRESHOLD:
                self._silence_frames += 1
                if self._silence_frames >= self.SILENCE_TIMEOUT_FRAMES:
                    # End of utterance
                    self._in_speech = False
                    self._speech_frames = 0
                    self._silence_frames = 0
                    self._speech_confirmed = False
                    return 'speech_end'
            else:
                # Voice resumed during silence window — reset silence counter
                self._silence_frames = 0

        return None

    def reset(self) -> None:
        """Reset all state (call when barge-in clears or new turn starts)."""
        self._in_speech = False
        self._speech_frames = 0
        self._silence_frames = 0
        self._speech_confirmed = False

    @property
    def is_speaking(self) -> bool:
        return self._in_speech


class VADService:
    """Legacy single-frame is_speech check preserved for backwards compatibility."""

    def __init__(self, threshold: float = 900.0) -> None:
        self.threshold = threshold

    def is_speech(self, audio_chunk: bytes) -> bool:
        """Determines if audio chunk volume exceeds threshold."""
        return _rms(audio_chunk) > self.threshold
