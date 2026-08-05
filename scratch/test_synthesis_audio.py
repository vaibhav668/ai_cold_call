import os
import asyncio
import tempfile
import miniaudio
from app.services.speech.tts.melotts_provider import MeloTTSProvider

def linear2ulaw(sample: int) -> int:
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

async def main():
    # Force settings/environment
    cache_dir = os.path.abspath("data/cache/xdg")
    os.makedirs(cache_dir, exist_ok=True)
    os.environ["XDG_CACHE_HOME"] = cache_dir
    os.environ["HF_HOME"] = os.path.abspath("data/cache/hf")

    provider = MeloTTSProvider()
    
    print("Testing stream_speech...")
    chunks = []
    async for chunk in provider.stream_speech("Hello Vaibhav, this is a test of MeloTTS speech streaming."):
        chunks.append(chunk)
    
    print(f"Generated {len(chunks)} chunks.")
    if chunks:
        print(f"First chunk length: {len(chunks[0])} bytes.")
        print(f"First few bytes: {chunks[0][:10]}")
        # Let's save the raw mu-law bytes to check
        with open("test_mulaw.raw", "wb") as f:
            for chunk in chunks:
                f.write(chunk)
        print("Raw mu-law saved to test_mulaw.raw")

if __name__ == "__main__":
    asyncio.run(main())
