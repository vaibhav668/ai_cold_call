import sys
import os
import asyncio
import traceback

sys.path.append('.')

async def main():
    log_file = "data/scratch_output.txt"
    os.makedirs("data", exist_ok=True)
    with open(log_file, "w", encoding="utf-8") as out:
        try:
            out.write("Script started...\n")
            from app.services.speech.tts.melotts_provider import MeloTTSProvider
            out.write("MeloTTSProvider imported successfully!\n")
            
            # Set cache dirs
            cache_dir = os.path.abspath("data/cache/xdg")
            os.makedirs(cache_dir, exist_ok=True)
            os.environ["XDG_CACHE_HOME"] = cache_dir
            os.environ["HF_HOME"] = os.path.abspath("data/cache/hf")
            
            provider = MeloTTSProvider()
            out.write("MeloTTSProvider instantiated!\n")
            
            chunks = []
            out.write("Synthesizing speech...\n")
            async for chunk in provider.stream_speech("Hello Vaibhav, this is a test of MeloTTS speech streaming."):
                chunks.append(chunk)
            
            out.write(f"Generated {len(chunks)} chunks.\n")
            if chunks:
                out.write(f"First chunk length: {len(chunks[0])} bytes.\n")
                out.write(f"First few bytes: {list(chunks[0][:10])}\n")
        except Exception as e:
            out.write("Error during execution:\n")
            traceback.print_exc(file=out)

if __name__ == "__main__":
    asyncio.run(main())
