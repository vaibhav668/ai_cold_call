import sys
import os
import asyncio
import traceback

sys.path.append('.')

async def main():
    log_file = "data/scratch_output_cached.txt"
    os.makedirs("data", exist_ok=True)
    with open(log_file, "w", encoding="utf-8") as out:
        try:
            out.write("Script started using default cache...\n")
            out.flush()
            
            # Print current environment HF_HOME
            out.write(f"Initial HF_HOME in env: {os.environ.get('HF_HOME')}\n")
            out.write(f"Initial XDG_CACHE_HOME in env: {os.environ.get('XDG_CACHE_HOME')}\n")
            out.flush()
            
            from app.services.speech.tts.melotts_provider import MeloTTSProvider
            out.write("MeloTTSProvider imported successfully!\n")
            out.flush()
            
            provider = MeloTTSProvider()
            out.write("MeloTTSProvider instantiated!\n")
            out.flush()
            
            chunks = []
            out.write("Synthesizing speech...\n")
            out.flush()
            
            import time
            start = time.time()
            async for chunk in provider.stream_speech("Hello Vaibhav, this is a test of MeloTTS speech streaming using the default cache directory."):
                chunks.append(chunk)
            
            duration = time.time() - start
            out.write(f"Speech synthesis completed in {duration:.3f} seconds!\n")
            out.write(f"Generated {len(chunks)} chunks.\n")
            if chunks:
                out.write(f"First chunk length: {len(chunks[0])} bytes.\n")
            out.flush()
        except Exception as e:
            out.write("Error during execution:\n")
            traceback.print_exc(file=out)
            out.flush()

if __name__ == "__main__":
    asyncio.run(main())
