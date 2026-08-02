# `setup/` — Script cài đặt Motion Backend

Các trình cài đặt. **Chạy từ thư mục gốc repo** (script tự `cd` về gốc):

| Lệnh | Mode | Khi nào dùng | HTTPS |
|---|---|---|---|
| `./setup/setup-create-image.sh` | **PM2 — box CHUYÊN create-image** (KHOÁ) | Box GPU chỉ tạo ảnh Qwen-Image-Edit | Cloudflare Tunnel / nginx+certbot |
| `./setup/setup-motion-transfer.sh` | **PM2 — box CHUYÊN motion-transfer + teen-flycam + trend-tiktok** (KHOÁ) | Box GPU chạy motion theo video, Teen Flycam và Trend TikTok bằng cùng stack Wan | Cloudflare Tunnel / nginx+certbot |
| `./setup/setup-tryon.sh` | **PM2 — box CHUYÊN Try-On** (KHOÁ) | Box GPU thử đồ (Qwen-Image-Edit 2509). Khác `setup-create-image.sh` ở chỗ nhận thêm `tryon,edit-image` — đây là box dùng cho Task Cloud capability `edit-image` | Cloudflare Tunnel / nginx+certbot |
| `./setup/setup-full.sh` | **PM2 — box ĐẦY ĐỦ** (21 type, catalog KHÔNG lọc) | Một box ôm hết: motion + tạo ảnh + tryon + LTX + dịch. Cần GPU lớn | Cloudflare Tunnel / nginx+certbot |
| `./setup/setup-pm2.sh` | **PM2 native — FULL, monolith cũ** | Bản tiền-`lib-feature`. `setup-full.sh` làm cùng việc qua chuỗi phase chung — ưu tiên nó | Cloudflare Tunnel / nginx+certbot |
| `./setup/setup.sh` | **Docker** (Linux) | Linux + NVIDIA chạy cả stack bằng Docker Compose | Caddy (kèm domain) |
| `./setup/fullstack-setup.sh` | **IP fullstack** (BE+FE) | Không có domain → chạy BE + FE qua `http://<IP>:port`. Gọi lại `setup-pm2.sh` (`SKIP_HTTPS=1`) | ❌ HTTP qua IP |

## Chọn profile: box CHUYÊN hay box ĐẦY ĐỦ

Bốn script trên dùng chung thư viện **`lib-feature.sh`** (tham số hoá: `COMFY_NODES`, `JOB_TYPE`,
`CATALOG_FILE`, `NEED_OLLAMA`, `NEED_BG_REMOVER`, `PM2_APPS`). Cùng cơ chế, khác giá trị:

| | box CHUYÊN (`motion-transfer`, `create-image`, `tryon`) | box ĐẦY ĐỦ (`full`) |
|---|---|---|
| Custom node | chỉ node của feature (2–6) | 9 node |
| `JOB_TYPES` | đúng nhóm type của feature | 21 type (mọi handler trừ `wan-dancer`) |
| Catalog | `catalog-<feature>.json` — allow-list, **không pull được model khác** | `catalog.json` — không lọc |
| Ollama · bg-remover | tuỳ feature | cả hai |
| Đánh đổi | ít thứ hỏng, cài nhanh, không tranh VRAM | một box làm mọi thứ, cài lâu hơn |

Điểm chung cả hai: **không** `ComfyUI-Manager`, và **KHÔNG tải model lúc cài** — model tải riêng
qua Settings → Models AI.

`wan-dancer` cố ý nằm ngoài profile full: `run_wan_dancer` tự báo lỗi khi GPU < 90GB VRAM, mà card
khuyến nghị của dự án là RTX 5090 32GB. Bật nó chỉ đổi "job nằm chờ" thành "job fail sau khi đã
claim". Máy ≥90GB thì nối `,wan-dancer` vào `JOB_TYPES` trong `.env` rồi
`pm2 restart ecosystem.config.cjs --only worker --update-env`.

Các danh sách job type này bị chép ở nhiều file. Chạy `make check-job-types` ở repo gốc để kiểm
chúng còn khớp nhau — lệch thì job nằm `queued` vĩnh viễn, không lỗi, không log.

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
