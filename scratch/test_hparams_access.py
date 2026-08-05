import sys
sys.path.append('.')

from melo.api import TTS
model = TTS(language='EN', device='cpu')
spk2id = model.hps.data.spk2id

print("spk2id keys/attributes:", dir(spk2id))
print("EN_INDIA attribute:", getattr(spk2id, 'EN_INDIA', 'NOT_FOUND'))
try:
    print("dict(spk2id):", dict(spk2id))
except Exception as e:
    print("dict(spk2id) failed:", e)

try:
    # VITS HParams typically has a dictionary representation or attributes
    print("spk2id as dict via __dict__ or items:")
    if hasattr(spk2id, 'items'):
        print("items:", list(spk2id.items()))
except Exception as e:
    print("items failed:", e)
