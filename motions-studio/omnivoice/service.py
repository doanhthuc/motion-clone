"""OmniVoice HTTP service — load OmniVoice (k2-fsa, Apache-2.0) bản fine-tune tiếng Việt 1 lần, clone giọng từ
ref wav, đọc tiếng Việt. Chạy thường trú trên GPU box (venv riêng), worker gọi POST /tts. KHÔNG đụng ComfyUI.

Contract GIỐNG vixtts/service.py (POST /tts {text, ref, language} → wav 24kHz) để worker gọi gần như y hệt → có
thể tráo engine mà không đổi luồng. OmniVoice KHÔNG có HTTP server sẵn (chỉ Python lib) nên bọc FastAPI tại đây.

ENV: OMNIVOICE_MODEL (HF id / path), OMNIVOICE_REF (ref mặc định khi job không chọn giọng), OMNIVOICE_LANG,
     OMNIVOICE_DEVICE, OMNIVOICE_STEPS (số bước diffusion; ít hơn = nhanh hơn, mặc định 32), OMNIVOICE_PORT.
"""
import os, re, tempfile
import numpy as np, soundfile as sf, torch
from fastapi import FastAPI, Body, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from omnivoice import OmniVoice

MODEL = os.environ.get("OMNIVOICE_MODEL", "splendor1811/omnivoice-vietnamese")
DEFAULT_REF = os.environ.get("OMNIVOICE_REF", "")
DEFAULT_LANG = os.environ.get("OMNIVOICE_LANG", "vietnamese")
DEVICE = os.environ.get("OMNIVOICE_DEVICE", "cuda:0")
STEPS = int(os.environ.get("OMNIVOICE_STEPS", "32"))
SR = 24000

print("[omnivoice] loading model…", flush=True)
model = OmniVoice.from_pretrained(MODEL, device_map=DEVICE, dtype=torch.float16)
print("[omnivoice] model READY", flush=True)

# Cache "voice clone prompt" theo ref (giống viXTTS cache latents): pre-encode ref 1 lần, tái dùng mọi câu →
# khỏi encode lại (omnivoice khuyến nghị create_voice_clone_prompt cho serving). ref_text rỗng → tự Whisper-ASR.
_vc = {}
def clone_prompt(ref, ref_text):
    key = ref + "||" + (ref_text or "")
    if key not in _vc:
        try:
            _vc[key] = model.create_voice_clone_prompt(ref_audio=ref, ref_text=ref_text or None)
        except Exception as e:
            print(f"[omnivoice] create_voice_clone_prompt fail ({e}) → generate ref trực tiếp", flush=True)
            _vc[key] = None
    return _vc[key]

def _generate(text, language, ref, ref_text, vc):
    """Gọi model.generate chịu được khác biệt chữ ký API: thử voice_clone_prompt + num_step, lỗi kwargs → tối giản."""
    attempts = []
    if vc is not None:
        attempts.append(dict(text=text, language=language, voice_clone_prompt=vc, num_step=STEPS))
        attempts.append(dict(text=text, language=language, voice_clone_prompt=vc))
    attempts.append(dict(text=text, language=language, ref_audio=ref, ref_text=ref_text or None, num_step=STEPS))
    attempts.append(dict(text=text, language=language, ref_audio=ref, ref_text=ref_text or None))
    last = None
    for kw in attempts:
        try:
            return model.generate(**kw)
        except TypeError as e:
            last = e
    raise last or RuntimeError("omnivoice generate failed")

def to_wave(audio):
    """audio (theo doc: torchaudio.save(out, audio[0], 24000)) → mảng float32 mono 1D."""
    a = audio[0]
    try:
        a = a.detach().cpu().float().numpy()
    except Exception:
        a = np.asarray(a, dtype=np.float32)
    a = np.asarray(a, dtype=np.float32)
    if a.ndim > 1:
        a = a.mean(axis=0)  # nhiều kênh → mono
    return a.squeeze()

def split_chunks(text, maxlen=300):
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
    return {"ok": True, "model": MODEL, "ref": DEFAULT_REF, "lang": DEFAULT_LANG, "refs_cached": len(_vc)}

@app.post("/tts")
def tts(text: str = Body(..., embed=True), ref: str = Body(None, embed=True),
        language: str = Body(None, embed=True), ref_text: str = Body(None, embed=True)):
    ref = ref or DEFAULT_REF
    language = language or DEFAULT_LANG
    if not ref or not os.path.exists(ref):
        return JSONResponse({"error": f"ref not found: {ref}"}, status_code=400)
    try:
        vc = clone_prompt(ref, ref_text)
        parts = []
        for ch in split_chunks(text):
            parts.append(to_wave(_generate(ch, language, ref, ref_text, vc)))
            parts.append(np.zeros(int(SR * 0.25), dtype=np.float32))  # 0.25s nghỉ giữa câu
        audio = np.concatenate(parts) if parts else np.zeros(1, dtype=np.float32)
        fd, path = tempfile.mkstemp(suffix=".wav"); os.close(fd)
        sf.write(path, audio, SR)
        return FileResponse(path, media_type="audio/wav", filename="tts.wav")
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# #region ALD 15/06/2026 - ASR (/asr): faster-whisper transcribe + timestamp cho node "Dịch phụ đề video".
# Lazy-load để KHÔNG làm chậm khởi động TTS. Model theo OMNIVOICE_ASR_MODEL (small|medium|large-v3). GPU nếu có.
ASR_MODEL_NAME = os.environ.get("OMNIVOICE_ASR_MODEL", "medium")
_asr = {}
def _get_asr(name=None):
    key = name or ASR_MODEL_NAME
    if key not in _asr:
        from faster_whisper import WhisperModel
        dev = "cuda" if str(DEVICE).startswith("cuda") else "cpu"
        ct = "float16" if dev == "cuda" else "int8"
        print(f"[omnivoice] loading ASR {key} ({dev})…", flush=True)
        _asr[key] = WhisperModel(key, device=dev, compute_type=ct)
        print("[omnivoice] ASR READY", flush=True)
    return _asr[key]

@app.post("/asr")
async def asr(file: UploadFile = File(None), path: str = Form(None),
              language: str = Form(None), model: str = Form(None)):
    """Speech→text + timestamp. Nhận file audio/video (multipart 'file') HOẶC 'path' service đọc được.
    language=None → tự nhận. Trả {language, duration, segments:[{start,end,text}]}."""
    tmp = None
    try:
        if file is not None:
            fd, tmp = tempfile.mkstemp(suffix=os.path.splitext(file.filename or "")[1] or ".bin"); os.close(fd)
            with open(tmp, "wb") as f:
                f.write(await file.read())
            src = tmp
        elif path and os.path.exists(path):
            src = path
        else:
            return JSONResponse({"error": "thiếu file hoặc path hợp lệ"}, status_code=400)
        m = _get_asr(model)
        segs, info = m.transcribe(src, language=(language or None), vad_filter=True, beam_size=5)
        out = [{"start": float(s.start), "end": float(s.end), "text": (s.text or "").strip()}
               for s in segs if (s.text or "").strip()]
        return {"language": getattr(info, "language", language),
                "duration": float(getattr(info, "duration", 0) or 0), "segments": out}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass
# #endregion
