import sys
import os

sys.path.append('.')

def main():
    log_file = "data/inspect_output.txt"
    os.makedirs("data", exist_ok=True)
    with open(log_file, "w", encoding="utf-8") as out:
        try:
            out.write("Importing TTS...\n")
            from melo.api import TTS
            out.write("Instantiating TTS...\n")
            model = TTS(language='EN', device='cpu')
            out.write(f"model.hps type: {type(model.hps)}\n")
            out.write(f"model.hps.data type: {type(model.hps.data)}\n")
            out.write(f"model.hps.data.spk2id type: {type(model.hps.data.spk2id)}\n")
            out.write(f"model.hps.data.spk2id content: {model.hps.data.spk2id}\n")
            
            # Let's inspect spk2id attributes and dict conversions
            spk2id = model.hps.data.spk2id
            out.write(f"Has attribute get: {hasattr(spk2id, 'get')}\n")
            if hasattr(spk2id, 'items'):
                out.write(f"Has items: {list(spk2id.items())}\n")
            
        except Exception as e:
            import traceback
            traceback.print_exc(file=out)

if __name__ == "__main__":
    main()
