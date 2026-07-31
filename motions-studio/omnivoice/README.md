# OmniVoice TTS service

Engine TTS **chính** cho motion-backend (clone giọng tiếng Việt từ 1 file ref `.wav`). Bọc HTTP quanh
[`k2-fsa/OmniVoice`](https://github.com/k2-fsa/OmniVoice) (Apache-2.0) — bản fine-tune tiếng Việt 1000h
[`splendor1811/omnivoice-vietnamese`](https://huggingface.co/splendor1811/omnivoice-vietnamese).

Contract `/tts` **giống `vixtts/service.py`** nên worker gọi gần như y hệt (xem `_omnivoice_tts` trong
`worker/worker_runtime/linux.py`). Worker ưu tiên OmniVoice; lỗi/không có service → tự rớt về viXTTS → Gemini →
edge-tts → Piper. Tắt hẳn = bỏ env `OMNIVOICE_URL`.

## Setup (1 lần, trên GPU box — KHÔNG container hoá, chạy process như viXTTS)

```bash
cd /home/ubuntu/ai/omnivoice            # nơi đặt service (tương tự /home/ubuntu/ai/vixtts)
python3 -m venv venv && . venv/bin/activate

# 1) torch CUDA TRƯỚC (OmniVoice cần torchaudio; cặp stable có cu130):
pip install torch==2.11.0+cu130 torchaudio==2.11.0+cu130 --extra-index-url https://download.pytorch.org/whl/cu130
# 2) rồi mới deps service:
pip install -r requirements.txt

# 3) (tuỳ chọn) tải sẵn model VN về cache HF cho lần chạy đầu khỏi chờ:
huggingface-cli download splendor1811/omnivoice-vietnamese
```

Chuẩn bị **1 file ref giọng mẫu KHÔ** (3–10s, ít vang — xem kinh nghiệm viXTTS) đặt ví dụ
`/home/ubuntu/ai/omnivoice/voices/ref_main.wav` và trỏ `OMNIVOICE_REF` vào đó (giọng mặc định khi job không chọn).

## Chạy

```bash
OMNIVOICE_MODEL=splendor1811/omnivoice-vietnamese \
OMNIVOICE_REF=/home/ubuntu/ai/omnivoice/voices/ref_main.wav \
OMNIVOICE_LANG=vietnamese \
uvicorn service:app --host 0.0.0.0 --port 8091
```

Nên bọc systemd/pm2/supervisor để tự dậy (giống viXTTS). ENV: `OMNIVOICE_MODEL`, `OMNIVOICE_REF`, `OMNIVOICE_LANG`,
`OMNIVOICE_DEVICE` (mặc định `cuda:0`), `OMNIVOICE_STEPS` (số bước diffusion, ít = nhanh, mặc định 32), `OMNIVOICE_PORT`.

## Nối vào worker

Trong `.env` của worker (rồi **recreate container worker** — đổi env phải recreate):

```
OMNIVOICE_URL=http://127.0.0.1:8091
OMNIVOICE_REF=/home/ubuntu/ai/omnivoice/voices/ref_main.wav
OMNIVOICE_LANG=vietnamese
```

> ⚠️ ref `.wav` truyền qua field `ref` là **đường dẫn file service ĐỌC ĐƯỢC** (service chạy cùng box với worker,
> giống viXTTS). Thư viện giọng `voicelib:<id>` được worker tải về `/tmp` rồi truyền path — đảm bảo service thấy `/tmp` đó.

## Test nhanh

```bash
curl -s -XPOST http://127.0.0.1:8091/tts \
  -H 'Content-Type: application/json' \
  -d '{"text":"Xin chào, đây là giọng OmniVoice tiếng Việt.","language":"vietnamese","ref":"/home/ubuntu/ai/omnivoice/voices/ref_main.wav"}' \
  --output /tmp/omni_test.wav && echo OK
curl -s http://127.0.0.1:8091/health
# ASR (node "Dịch phụ đề video") — transcribe + timestamp:
curl -s -XPOST http://127.0.0.1:8091/asr -F file=@/tmp/clip.wav | head -c 400
```

## ASR (`/asr`) — ALD 15/06/2026
Endpoint phụ cho node **Dịch phụ đề video**: nhận file audio/video (`-F file=@…`) hoặc `path` → trả
`{language, duration, segments:[{start,end,text}]}` (dùng **faster-whisper**, lazy-load lần gọi đầu).
- Cài thêm: `pip install faster-whisper python-multipart` trong venv.
- Model: `OMNIVOICE_ASR_MODEL` (mặc định `medium`; `large-v3` chính xác hơn, chậm hơn). Chạy GPU cùng venv TTS.

## VRAM

Model 0.6B (Qwen3-0.6B backbone) fp16 ≈ 2–3GB — nhẹ, nằm cùng ComfyUI/viXTTS/Ollama trên RTX 5090 (32GB). Sau khi
validate ổn có thể **tắt viXTTS** để đòi lại VRAM (OmniVoice thay thế hẳn vai trò engine chính).
