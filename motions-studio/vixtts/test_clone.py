import re
import soundfile as sf
from TTS.tts.configs.xtts_config import XttsConfig
from TTS.tts.models.xtts import Xtts
from TTS.tts.layers.xtts import tokenizer as xtok

MODEL = "/home/ubuntu/ai/vixtts/model"
REF = "/home/ubuntu/ai/vixtts/voices/ref_main.wav"
TEXT = ("Xin chào, đây là giọng đọc thử cho video quảng cáo. "
        "Đôi giày êm như mây, bứt tốc như nắng, có mặt tại tất cả cửa hàng.")

config = XttsConfig()
config.load_json(MODEL + "/config.json")
model = Xtts.init_from_config(config)
model.load_checkpoint(config, checkpoint_dir=MODEL, use_deepspeed=False)
model.cuda()
print("LOADED")

# viXTTS train tiếng Việt (vocab có [vi]) nhưng tokenizer upstream chặn 'vi' → patch: cho 'vi' qua
# multilingual_cleaners; lỗi (vd expand số) → clean tối thiểu. + đăng ký char_limits để split câu.
tk = model.tokenizer
tk.char_limits["vi"] = 250
_orig = tk.preprocess_text
def _pp(txt, lang):
    if lang == "vi":
        try:
            return xtok.multilingual_cleaners(txt, "vi")
        except Exception:
            return re.sub(r"\s+", " ", txt.strip().lower())
    return _orig(txt, lang)
tk.preprocess_text = _pp

gpt, spk = model.get_conditioning_latents(audio_path=[REF])
print("LATENTS_OK")

out = model.inference(TEXT, "vi", gpt, spk, temperature=0.7, enable_text_splitting=True)
sf.write("/tmp/clone_test.wav", out["wav"], 24000)
print("CLONE_OK samples", len(out["wav"]))
