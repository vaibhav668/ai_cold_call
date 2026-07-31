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

    Calibrated for 8kHz G.711 mu-law phone audio (Indian mobile carriers).

    Typical decoded PCM RMS ranges observed on Plivo streams:
        Line static / background: 50 – 200
        Quiet speech:            200 – 600
        Normal speech:           400 – 2000
        Loud speech:            1500 – 5000

    State machine:
        IDLE → SPEAKING        (RMS > SPEECH_THRESHOLD for MIN_SPEECH_FRAMES consecutive)
        SPEAKING → TRAILING    (RMS < SILENCE_THRESHOLD)
        TRAILING → IDLE/END    (silence_frames > SILENCE_TIMEOUT_FRAMES)
        TRAILING → SPEAKING    (voice resumes before timeout)

    Events returned: 'speech_start', 'speech_end', or None.
    """

    # FIX: Lowered from 900→450 and 500→220 to detect quieter phone speech.
    # Requiring 4 consecutive frames above 900 was too strict — any dip reset
    # the counter, so speech on Indian mobile carriers was never confirmed.
    SPEECH_THRESHOLD = 450.0    # RMS above this = speech active
    SILENCE_THRESHOLD = 220.0   # RMS below this = silence
    MIN_SPEECH_FRAMES = 3       # ~60ms minimum utterance (3 × 20ms)
    SILENCE_TIMEOUT_FRAMES = 20 # ~400ms silence = end-of-utterance

    def __init__(self) -> None:
        self._in_speech = False
        self._speech_frames = 0
        self._silence_frames = 0
        self._speech_confirmed = False

    def process_frame(self, audio_chunk: bytes) -> str | None:
        """
        Process one 20ms audio frame.

        Returns:
            'speech_start'  — customer confirmed speaking
            'speech_end'    — customer finished utterance
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
                # FIX: Don't fully reset on a single sub-threshold frame.
                # Decrement gradually so occasional dips don't cancel build-up.
                self._speech_frames = max(0, self._speech_frames - 1)
        else:
            if rms < self.SILENCE_THRESHOLD:
                self._silence_frames += 1
                if self._silence_frames >= self.SILENCE_TIMEOUT_FRAMES:
                    self._in_speech = False
                    self._speech_frames = 0
                    self._silence_frames = 0
                    self._speech_confirmed = False
                    return 'speech_end'
            else:
                # Voice resumed during silence window
                self._silence_frames = 0

        return None

    def reset(self) -> None:
        """Reset all state (call on barge-in or new turn start)."""
        self._in_speech = False
        self._speech_frames = 0
        self._silence_frames = 0
        self._speech_confirmed = False

    @property
    def is_speaking(self) -> bool:
        return self._in_speech


class VADService:
    """Legacy single-frame is_speech check preserved for backward compatibility."""

    def __init__(self, threshold: float = 450.0) -> None:
        self.threshold = threshold

    def is_speech(self, audio_chunk: bytes) -> bool:
        return _rms(audio_chunk) > self.threshold
