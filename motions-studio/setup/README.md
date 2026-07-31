# `setup/` — Script cài đặt Motion Backend

Các trình cài đặt. **Chạy từ thư mục gốc repo** (script tự `cd` về gốc):

| Lệnh | Mode | Khi nào dùng | HTTPS |
|---|---|---|---|
| `./setup/setup-create-image.sh` | **PM2 — box CHUYÊN create-image** (KHOÁ) | Box GPU chỉ tạo ảnh Qwen-Image-Edit | Cloudflare Tunnel / nginx+certbot |
| `./setup/setup-motion-transfer.sh` | **PM2 — box CHUYÊN motion-transfer + teen-flycam + trend-tiktok** (KHOÁ) | Box GPU chạy motion theo video, Teen Flycam và Trend TikTok bằng cùng stack Wan | Cloudflare Tunnel / nginx+certbot |
| `./setup/setup-tryon.sh` | **PM2 — box CHUYÊN Try-On** (KHOÁ) | Box GPU thử đồ (Qwen-Image-Edit 2509). Khác `setup-create-image.sh` ở chỗ nhận thêm `tryon,edit-image` — đây là box dùng cho Task Cloud capability `edit-image` | Cloudflare Tunnel / nginx+certbot |
| `./setup/setup-pm2.sh` | **PM2 native — FULL** (mọi tính năng) | VPS Ubuntu 1 box ôm hết (cần GPU lớn) | Cloudflare Tunnel / nginx+certbot |
| `./setup/setup.sh` | **Docker** (Linux) | Linux + NVIDIA chạy cả stack bằng Docker Compose | Caddy (kèm domain) |
| `./setup/fullstack-setup.sh` | **IP fullstack** (BE+FE) | Không có domain → chạy BE + FE qua `http://<IP>:port`. Gọi lại `setup-pm2.sh` (`SKIP_HTTPS=1`) | ❌ HTTP qua IP |

## Box CHUYÊN 1 chức năng (khuyên dùng — tránh tranh GPU/VRAM)

`setup-create-image.sh` và `setup-motion-transfer.sh` dùng chung thư viện **`lib-feature.sh`** (tham số hoá: `COMFY_NODES`, `JOB_TYPE`, `CATALOG_FILE`, `NEED_OLLAMA`, `PM2_APPS`). Mỗi box:
- Chỉ clone **đúng custom node** của feature (không `ComfyUI-Manager`).
- `JOB_TYPES` = đúng nhóm type của feature · `MODEL_CATALOG_PATH` → `../comfyui/catalog-<feature>.json` (allow-list) → **không pull được model/feature khác**.
- **KHÔNG tải model lúc cài** — tải riêng qua Settings → Models AI (nhóm của feature).
- Chỉ bật PM2: `minio,api,wf-worker,worker,comfyui` (không `bg-remover`).

👉 Cấu hình máy đề xuất + cơ chế khoá: **[../DEPLOY.md](../DEPLOY.md)**.

```bash
git clone <repo> motion-backend && cd motion-backend
./setup/setup-pm2.sh          # prod native + Cloudflare
# hoặc
CF_API_TOKEN=<token> ./setup/setup-pm2.sh
```

Cờ hữu ích (env): `SKIP_COMFY=1` · `SKIP_MODELS=1` · `SKIP_HTTPS=1` · `HF_TOKEN=` (tải model gated) · `COMFY_DIR=`.

## Các script này cài/cấu hình gì (liên quan model AI)

- **ComfyUI** (native ở `setup-pm2.sh`, hoặc Docker ở `setup.sh`) + custom nodes (WanVideoWrapper, GGUF, LTXVideo, VideoHelperSuite, controlnet_aux, KJNodes, **FlashVSR Stable** và **Frame-Interpolation** = node "RIFE VFI" cho enhance 30/60fps).
- **Teen Flycam** dùng chung `ComfyUI-WanVideoWrapper`. Khi dựng box chuyên motion-transfer, cần có đủ optional FunCamera nodes trong repo này, đặc biệt `WanCameraEmbedding` và `WanCameraImageToVideo`.
- **Import link social** (Facebook/TikTok/YouTube) dùng `yt-dlp`. `setup.sh` sẽ cố update `yt-dlp` lên bản mới nhất; nếu API vẫn dính extractor cũ, set `YTDLP_BIN` trỏ đúng binary mới hơn (ví dụ `$HOME/.local/bin/yt-dlp`).
- **Node "Nâng chất lượng" (enhance video)** mặc định dùng FlashVSR v1.1. Cài đủ nhóm **FlashVSR Enhance** trong **Settings → Models AI** (3 weight chính + Wan2.2 VAE, nằm ở `ComfyUI/models/FlashVSR-v1.1/`). Thiếu/lỗi model thì job báo lỗi, không tự giao bản Lanczos. Enhance ảnh vẫn dùng `4x-UltraSharp.pth` / `RealESRGAN_x4plus.pth`; nội suy fps dùng `rife47.pth` do node Frame-Interpolation tự tải.
- **Model** tải theo `../comfyui/models.txt` (skip file đã có; tôn trọng `HF_TOKEN`). Gồm cả 3 file của **node SS** (Ảnh→Video LTX-2.3 + LoRA): UNET GGUF + Gemma-3 fp8 + VAE.
- **"Model AI (custom)"** — `setup-pm2.sh` tạo 1 thư mục gom model do user tự upload qua **Settings → Model AI**:
  `ComfyUI/models/uploads/{loras,checkpoints,unet,vae,text_encoders,clip_vision}/` + đăng ký vào `ComfyUI/extra_model_paths.yaml`.
  Muốn dọn dẹp: xoá trong thư mục `uploads/` (không lẫn model hệ thống). PM2: API trỏ `MODEL_UPLOADS_DIR` vào đây (xem `../ecosystem.config.cjs`); Docker: bind-mount `MODEL_UPLOADS_HOST_DIR` (xem `../docker-compose.yml`).

## Chi tiết / khắc phục sự cố

Xem **[../DEPLOY.md](../DEPLOY.md)** (quy trình đầy đủ: Cloudflare token, nginx/certbot, Phụ lục IP) và **[../README.md](../README.md)**.
