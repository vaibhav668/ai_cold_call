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

class VADService:
    def __init__(self, threshold: float = 1500.0) -> None:
        self.threshold = threshold

    def is_speech(self, audio_chunk: bytes) -> bool:
        """Determines if the audio chunk volume exceeds the speech threshold (speech active)."""
        if not audio_chunk:
            return False
            
        total_squares = 0
        for byte in audio_chunk:
            sample = decode_ulaw_sample(byte)
            total_squares += sample * sample
            
        rms = (total_squares / len(audio_chunk)) ** 0.5
        return rms > self.threshold
