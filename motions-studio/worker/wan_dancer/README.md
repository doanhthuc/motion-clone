# Wan-Dancer node (music-to-dance)

Node **"Vũ đạo theo nhạc"** chạy [Wan-AI/Wan-Dancer-14B](https://huggingface.co/Wan-AI/Wan-Dancer-14B):
ảnh nhân vật + nhạc + style → video nhảy dài (minute-scale). Model **chưa có node ComfyUI**, chạy raw
qua **DiffSynth-Studio** (`infer.py`).

## CHỈ chạy trên VPS GPU ≥ 90GB
Mỗi model (global + local) ~34.5GB bf16 ⇒ **không chạy nổi trên .165 (5090 32GB)**. Node bị **khóa mặc định**:
- FE ẩn/khóa mờ khi `/workflows/capabilities` báo `gpuVramGb < 90`.
- BE (`handlers.js`) từ chối tạo job khi VRAM box < 90GB.
- `infer.py` + `run_wan_dancer` tự self-check `torch.cuda` ≥ 90GB.

## Setup trên VPS thuê (full-stack riêng biệt, làm 1 lần)
1. Đảm bảo GPU CC ≥ 8.0 + **torch cu128** (Blackwell sm_120 — xem bài học VPS SimplePod V100).
2. `git clone` DiffSynth-Studio, cài theo hướng dẫn Wan-Dancer.
3. Tải weights: `WAN_DANCER_MODEL=/data/models/Wan-Dancer-14B ./download_models.sh`
   (tải trực tiếp từ HF — KHÔNG qua comfyui/catalog.json vì Wan-Dancer không chạy trên ComfyUI).
4. Đặt vào **`.env`** của repo trên VPS (ecosystem.config.cjs đọc `.env` rồi truyền cho worker):
   ```
   WAN_DANCER_MODEL=/data/models/Wan-Dancer-14B   # thư mục weights (bắt buộc)
   WAN_DANCER_PY=/path/to/venv/bin/python          # env python có DiffSynth (nếu khác python worker)
   ```
   **KHÔNG cần** set `JOB_TYPES` — `wan-dancer` đã có sẵn trong `ecosystem.config.cjs` (ecosystem BỎ QUA
   `.env` JOB_TYPES). Job chỉ tạo được khi API thấy VRAM ≥ 90GB (hard-block), nên .165 vô hại.
5. `pm2 restart worker --update-env` để nạp env mới.
6. Nghiệm thu standalone trước khi chạy qua node:
   ```
   python infer.py --image ref.jpg --music song.wav --style street --duration 15 --resolution 720p --out out.mp4
   ```

## ⚠ TODO nghiệm thu
Tên module/hàm pipeline DiffSynth cho Wan-Dancer (`_load_pipeline()` / `_run()` trong `infer.py`) là
placeholder theo cấu trúc DiffSynth phổ biến — **chỉnh cho khớp API thực tế sau khi cài**. Phần khung
(argparse, map style→prompt, ghép nhạc, ghi mp4) đã đúng.
