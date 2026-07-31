"""viXTTS HTTP service — load XTTS-v2 (viXTTS) 1 lần, clone giọng từ ref wav, đọc tiếng Việt.
Chạy thường trú trên GPU box (venv riêng), worker gọi POST /tts. KHÔNG đụng ComfyUI.
ENV: VIXTTS_MODEL, VIXTTS_REF (giọng tham chiếu mặc định), VIXTTS_PORT.
"""
import os, re, tempfile
import numpy as np, soundfile as sf
from fastapi import FastAPI, Body
from fastapi.responses import FileResponse, JSONResponse
from TTS.tts.configs.xtts_config import XttsConfig
from TTS.tts.models.xtts import Xtts
from TTS.tts.layers.xtts import tokenizer as xtok

# ALD 02/07/2026 - default theo expanduser thay hardcode /home/ubuntu (VPS user khác vẫn đúng; env override như cũ).
MODEL = os.environ.get("VIXTTS_MODEL", os.path.expanduser("~/ai/vixtts/model"))
DEFAULT_REF = os.environ.get("VIXTTS_REF", os.path.expanduser("~/ai/vixtts/voices/ref_main.wav"))
SR = 24000

print("[vixtts] loading model…", flush=True)
config = XttsConfig(); config.load_json(MODEL + "/config.json")
model = Xtts.init_from_config(config)
model.load_checkpoint(config, checkpoint_dir=MODEL, use_deepspeed=False)
model.cuda(); model.eval()

# viXTTS train tiếng Việt (vocab có [vi]) nhưng tokenizer upstream chặn 'vi' → patch.
_tk = model.tokenizer
_tk.char_limits["vi"] = 250
_orig_pp = _tk.preprocess_text
def _pp(txt, lang):
    if lang == "vi":
        try: return xtok.multilingual_cleaners(txt, "vi")
        except Exception: return re.sub(r"\s+", " ", txt.strip().lower())
    return _orig_pp(txt, lang)
_tk.preprocess_text = _pp
print("[vixtts] model READY", flush=True)

_lat = {}
def latents(ref):
    if ref not in _lat:
        _lat[ref] = model.get_conditioning_latents(audio_path=[ref])
    return _lat[ref]

def split_chunks(text, maxlen=200):
    sents = re.split(r"(?<=[.!?…])\s+|\n+", (text or "").strip())
    out, cur = [], ""
    for s in (x.strip() for x in sents if x.strip()):
        if len(cur) + len(s) + 1 <= maxlen:
            cur = (cur + " " + s).strip()
        else:
            if cur: out.append(cur)
            if len(s) <= maxlen:
                cur = s
            else:
                for i in range(0, len(s), maxlen): out.append(s[i:i + maxlen])
                cur = ""
    if cur: out.append(cur)
    return out or [(text or " ")[:maxlen]]

app = FastAPI()

@app.get("/health")
def health():
    return {"ok": True, "model": MODEL, "ref": DEFAULT_REF, "refs_cached": list(_lat.keys())}

@app.post("/tts")
def tts(text: str = Body(..., embed=True), ref: str = Body(None, embed=True),
        language: str = Body("vi", embed=True), temperature: float = Body(0.7, embed=True)):
    ref = ref or DEFAULT_REF
    if not os.path.exists(ref):
        return JSONResponse({"error": f"ref not found: {ref}"}, status_code=400)
    try:
        gpt, spk = latents(ref)
        parts = []
        for ch in split_chunks(text):
            o = model.inference(ch, language, gpt, spk, temperature=float(temperature), enable_text_splitting=False)
            parts.append(np.asarray(o["wav"], dtype=np.float32))
            parts.append(np.zeros(int(SR * 0.25), dtype=np.float32))  # 0.25s nghỉ giữa câu
        audio = np.concatenate(parts) if parts else np.zeros(1, dtype=np.float32)
        fd, path = tempfile.mkstemp(suffix=".wav"); os.close(fd)
        sf.write(path, audio, SR)
        return FileResponse(path, media_type="audio/wav", filename="tts.wav")
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
