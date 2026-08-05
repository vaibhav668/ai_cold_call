import sys
import os
import traceback

sys.path.append('.')

def main():
    try:
        from melo.api import TTS
        cache_dir = os.path.abspath("data/cache/xdg")
        os.makedirs(cache_dir, exist_ok=True)
        os.environ["XDG_CACHE_HOME"] = cache_dir
        os.environ["HF_HOME"] = os.path.abspath("data/cache/hf")
        
        print("Instantiating TTS...")
        model = TTS(language='EN', device='cpu')
        print("TTS instantiated! Speaker IDs:", model.hps.data.spk2id)
    except Exception as e:
        print("MeloTTS load failed:")
        traceback.print_exc()

if __name__ == "__main__":
    main()
