import os
import asyncio
import tempfile
from typing import AsyncGenerator, Optional
from app.core.logging import logger
from app.services.speech.tts.base import TextToSpeechProvider

# Simple Devanagari (Hindi) mapping to English phonetics
_HINDI_MAP = {
    'अ': 'a', 'आ': 'aa', 'इ': 'i', 'ई': 'ee', 'उ': 'u', 'ऊ': 'oo', 'ऋ': 'ri',
    'ए': 'e', 'ऐ': 'ai', 'ओ': 'o', 'औ': 'au', 'अं': 'an', 'अः': 'ah',
    'ा': 'aa', 'ि': 'i', 'ी': 'ee', 'ु': 'u', 'ू': 'oo', 'ृ': 'ri',
    'े': 'e', 'ै': 'ai', 'ो': 'o', 'ौ': 'au', 'ं': 'n', 'ः': 'h',
    'क': 'ka', 'ख': 'kha', 'ग': 'ga', 'घ': 'gha', 'ङ': 'nga',
    'च': 'cha', 'छ': 'chha', 'ज': 'ja', 'झ': 'jha', 'ञ': 'nya',
    'ट': 'ta', 'ठ': 'tha', 'ड': 'da', 'ढ': 'dha', 'ण': 'na',
    'त': 'ta', 'थ': 'tha', 'द': 'da', 'ध': 'dha', 'न': 'na',
    'प': 'pa', 'फ': 'pha', 'ब': 'ba', 'भ': 'bha', 'म': 'ma',
    'य': 'ya', 'र': 'ra', 'ल': 'la', 'व': 'va', 'श': 'sha',
    'ष': 'sha', 'स': 'sa', 'ह': 'ha', 'क्ष': 'ksha', 'त्र': 'tra', 'ज्ञ': 'gya',
    '०': '0', '१': '1', '२': '2', '३': '3', '४': '4', '५': '5', '६': '6', '७': '7', '८': '8', '९': '9',
    '।': '.', ' ': ' '
}

# Simple Telugu mapping to English phonetics
_TELUGU_MAP = {
    'అ': 'a', 'ఆ': 'aa', 'ఇ': 'i', 'ఈ': 'ee', 'ఉ': 'u', 'ఊ': 'oo', 'ఋ': 'ri',
    'ఎ': 'e', 'ఏ': 'ee', 'ఐ': 'ai', 'ఒ': 'o', 'ఓ': 'oo', 'ఔ': 'au', 'అం': 'an', 'అః': 'ah',
    'ా': 'aa', 'ి': 'i', 'ీ': 'ee', 'ు': 'u', 'ూ': 'oo', 'ృ': 'ri',
    'ె': 'e', 'ే': 'ee', 'ై': 'ai', 'ొ': 'o', 'ో': 'oo', 'ౌ': 'au', 'ం': 'n', 'ః': 'h',
    'క': 'ka', 'ఖ': 'kha', 'గ': 'ga', 'ఘ': 'gha', 'ఙ': 'nga',
    'చ': 'cha', 'ఛ': 'chha', 'జ': 'ja', 'ఝ': 'jha', 'ఞ': 'nya',
    'ట': 'ta', 'ఠ': 'tha', 'డ': 'da', 'ఢ': 'dha', 'ణ': 'na',
    'త': 'ta', 'థ': 'tha', 'ద': 'da', 'ధ': 'dha', 'న': 'na',
    'ప': 'pa', 'ఫ': 'pha', 'బ': 'ba', 'భ': 'bha', 'మ': 'ma',
    'య': 'ya', 'ర': 'ra', 'ల': 'la', 'వ': 'va', 'శ': 'sha',
    'ష': 'sha', 'స': 'sa', 'హ': 'ha', 'ళ': 'la', 'క్ష': 'ksha',
    '౦': '0', '౧': '1', '౨': '2', '౩': '3', '౪': '4', '౫': '5', '౬': '6', '౭': '7', '౮': '8', '౯': '9',
    ' ': ' '
}

def transliterate_text(text: str) -> str:
    """Detects Hindi/Telugu scripts and transliterates to phonetic Latin script."""
    # Check if text contains Devanagari (Hindi) or Telugu characters
    has_hindi = any('\u0900' <= char <= '\u097F' for char in text)
    has_telugu = any('\u0c00' <= char <= '\u0c7F' for char in text)

    if not has_hindi and not has_telugu:
        return text

    mapping = _HINDI_MAP if has_hindi else _TELUGU_MAP
    result = []
    
    # Simple transliteration parser (converts consonants + matras)
    for char in text:
        result.append(mapping.get(char, char))

    transliterated = "".join(result)
    # Clean up double vowel occurrences resulting from consonant mapping + matras
    transliterated = transliterated.replace("aae", "e").replace("aai", "ai").replace("aao", "o")
    transliterated = transliterated.replace("aaa", "aa")
    return transliterated


def linear2ulaw(sample: int) -> int:
    """Converts a 16-bit signed linear PCM sample to 8-bit G.711 u-law."""
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


class MeloTTSProvider(TextToSpeechProvider):
    """
    Local Text-to-Speech provider utilizing MeloTTS.
    Optimized for multi-language (EN, HI, TE) synthesis.
    """

    _model_instance = None
    _speaker_id = None
    _model_lock = asyncio.Lock()

    @classmethod
    async def _get_model_and_speaker(cls):
        """Loads and caches the MeloTTS model and speaker ID as thread-safe singletons."""
        if cls._model_instance is not None:
            return cls._model_instance, cls._speaker_id

        async with cls._model_lock:
            if cls._model_instance is not None:
                return cls._model_instance, cls._speaker_id

            try:
                from melo.api import TTS
                logger.info("[TTS] Initializing MeloTTS EN (English) model...")
                
                # Load inside executor to prevent event loop blocking
                def load():
                    # Ensure MeloTTS caches downloads to a writable directory.
                    # MeloTTS uses XDG_CACHE_HOME / cached-path internally.
                    cache_dir = os.environ.get("XDG_CACHE_HOME", "/tmp/xdg_cache")
                    os.makedirs(cache_dir, exist_ok=True)
                    os.environ.setdefault("XDG_CACHE_HOME", cache_dir)
                    os.environ.setdefault("HF_HOME", os.environ.get("HF_HOME", "/tmp/hf_cache"))

                    model = TTS(language='EN', device='auto')
                    # Use Indian English accent for natural multilingual Indic voice
                    spk_id = model.hps.data.spk2id.get('EN_INDIA', 0)
                    return model, spk_id

                cls._model_instance, cls._speaker_id = await asyncio.get_event_loop().run_in_executor(None, load)
                logger.info("[TTS] MeloTTS model loaded successfully.")
            except Exception as e:
                logger.error(f"[TTS] Could not initialize local MeloTTS: {e}. Fallback to mock active.")
                cls._model_instance = "FAILED"
            return cls._model_instance, cls._speaker_id

    async def stream_speech(
        self,
        text: str,
        cancel_event: Optional[asyncio.Event] = None,
        language: Optional[str] = None,
        voice_config: Optional[dict] = None
    ) -> AsyncGenerator[bytes, None]:
        # Phoneticize Indic languages so English VITS model can speak them natively
        processed_text = transliterate_text(text)
        logger.info(f"[TTS] Synthesizing: '{processed_text[:60]}...' (original: '{text[:60]}...')")

        # 1. Try local MeloTTS model
        model, default_speaker_id = await self._get_model_and_speaker()
        if model != "FAILED" and model is not None:
            try:
                import miniaudio
                # Determine speaker id from voice_config
                speaker_id = default_speaker_id
                speed = 1.0
                if voice_config:
                    spk_name = voice_config.get("speaker_id")
                    if spk_name and hasattr(model, "hps") and hasattr(model.hps, "data") and hasattr(model.hps.data, "spk2id"):
                        speaker_id = model.hps.data.spk2id.get(spk_name, default_speaker_id)
                    speed = voice_config.get("speed", 1.0)

                # Create temporary file to store MeloTTS output wav
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmpfile:
                    tmp_path = tmpfile.name

                try:
                    def generate():
                        model.tts_to_file(processed_text, speaker_id, tmp_path, speed=speed)
                    
                    # Run synthesis in executor
                    await asyncio.get_event_loop().run_in_executor(None, generate)

                    if cancel_event and cancel_event.is_set():
                        logger.info("[TTS] Cancelled after synthesis.")
                        return

                    # Decode synthesized WAV at 8kHz PCMU target
                    decoded = miniaudio.decode_file(
                        tmp_path,
                        sample_rate=8000,
                        nchannels=1,
                        output_format=miniaudio.SampleFormat.SIGNED16
                    )

                    mulaw_bytes = bytes(linear2ulaw(s) for s in decoded.samples)

                    # Stream G.711 mu-law 20ms (160 bytes) frames
                    for i in range(0, len(mulaw_bytes), 160):
                        if cancel_event and cancel_event.is_set():
                            logger.info("[TTS] Cancelled mid-stream.")
                            return
                        yield mulaw_bytes[i:i + 160]

                finally:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                return
            except Exception as e:
                logger.error(f"[TTS] MeloTTS synthesis failed: {e}. Falling back to mock audio...")

        # 2. Mock Audio fallback if MeloTTS is missing or fails
        async for chunk in self._mock_speech_generator(processed_text, cancel_event):
            yield chunk

    async def _mock_speech_generator(
        self,
        text: str,
        cancel_event: Optional[asyncio.Event] = None
    ) -> AsyncGenerator[bytes, None]:
        logger.warning("[TTS] Yielding mock speech audio frames...")
        # Generates a simple low frequency beep sequence to simulate sound
        import math
        sample_rate = 8000
        words = len(text.split())
        duration = max(1.0, words * 0.3)  # 300ms per word average
        total_samples = int(duration * sample_rate)
        
        chunk_size = 160  # 20ms
        for i in range(0, total_samples, chunk_size):
            if cancel_event and cancel_event.is_set():
                break
            
            chunk = []
            for j in range(chunk_size):
                t = (i + j) / sample_rate
                # 440Hz sine wave tone
                val = int(10000.0 * math.sin(2.0 * math.pi * 440.0 * t))
                chunk.append(linear2ulaw(val))
                
            yield bytes(chunk)
            await asyncio.sleep(0.02)
