#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
# setup-full.sh — Cài box ĐẦY ĐỦ: mọi job type có handler, catalog model KHÔNG lọc.
#
#   git clone <repo> motion-backend && cd motion-backend
#   ./setup/setup-full.sh
#
# Khác ba profile cạnh nó (motion-transfer / create-image / tryon) ở đúng một điểm: chúng KHOÁ
# box vào một nhóm chức năng, file này MỞ hết. Cùng thư viện, cùng phase, chỉ khác giá trị.
#
#   • Catalog: comfyui/catalog.json (34 model — Wan Animate, Qwen-Image-Edit, Flux, LTX-2.3,
#     Upscale, FlashVSR, InfiniteTalk). Settings → Models AI thấy và tải được tất cả.
#   • Ollama: CÓ (dịch prompt VN→EN cho create-image, dịch phụ đề cho subtitle, tryon Auto).
#     Chỉ cài server — model pull riêng qua Settings → Models AI, nhóm "Ollama".
#   • bg-remover: CÓ (tách nền / crop sản phẩm cho tryon + product-overlay).
#   • Custom node: 9 node, đúng bộ mà setup-pm2.sh (box không khoá) vẫn cài — TRỪ
#     ComfyUI-Manager, xem lib-feature.sh §header.
#   • KHÔNG tải model lúc cài (giống mọi profile) — ~33GB đó tải qua Settings → Models AI,
#     và nếu có Network Volume thì chỉ tải một lần cho mọi pod về sau.
#
# Cấu hình máy: ≥24GB VRAM cho Wan 2.2 Animate + BlockSwap, khuyến nghị RTX 5090 32GB.
# Cờ env tuỳ chọn: DOMAIN, SUPER_ADMIN, GMAIL_USER, GMAIL_APP_PASSWORD,
#   CF_API_TOKEN, CF_TUNNEL_TOKEN, CORS_ORIGINS, HF_TOKEN, COMFY_DIR, SKIP_COMFY, SKIP_HTTPS.
# ════════════════════════════════════════════════════════════════════════════
set -uo pipefail
cd "$(dirname "$0")/.."; ROOT="$(pwd)"

# ── PROFILE: full ────────────────────────────────────────────────────────────
FEATURE="full"
FEATURE_TITLE="Đầy đủ (mọi job type · Wan · Qwen · Flux · LTX)"

# Nguồn sự thật là registry PIPELINES ở worker/worker_runtime/linux.py:9728 — 22 handler.
# Danh sách dưới là 22 trừ 'wan-dancer'.
#
# Vì sao phải soi registry chứ không chép danh sách có sẵn: trong repo đang có BA danh sách
# job type và cả ba đều lệch nhau, không cái nào khớp registry (đo 02/08/2026):
#   • linux.py:64 _DEFAULT_JOB_TYPES — 19 type, thiếu text-to-video, ss, wan-dancer, dù comment
#     ngay trên nó ghi "bao trùm MỌI node type có handler".
#   • setup-pm2.sh:520 REQ_JT      — 19 type, có text-to-video + ss nhưng thiếu edit-image, reveal.
#   • .env.example:94              — 20 type, thiếu edit-image.
# Chép nhầm một trong ba thì hậu quả im lặng: job type bị sót nằm 'queued' vĩnh viễn, không lỗi,
# không log, chỉ là không ai nhận.
#
# 'wan-dancer' CỐ Ý bỏ: run_wan_dancer (linux.py:9668) tự raise khi GPU < 90GB VRAM. Card khuyến
# nghị của dự án là RTX 5090 32GB → bật type này chỉ đổi "job nằm chờ" thành "job fail sau khi đã
# claim", tệ hơn. Máy ≥90GB thì thêm tay: nối ',wan-dancer' vào JOB_TYPES trong .env rồi
#   pm2 restart ecosystem.config.cjs --only worker --update-env   (thiếu --update-env thì PM2
#   giữ env cũ và .env mới không có tác dụng — xem setup-pm2.sh:850).
JOB_TYPE="motion,bds,tryon,create-image,edit-image,product-overlay,teaser,video,text-to-video,wan-i2v,teen-flycam,trend-tiktok,ss,talk,face-motion,concat,reveal,voiceover,story-film,subtitle,enhance,character-swap"

CATALOG_FILE="$ROOT/comfyui/catalog.json"
# In ra trong hướng dẫn cuối phần cài. Không phải allow-list (catalog.json mới là) — chỉ là câu
# gợi ý bấm nhóm nào trước, nên viết tên nhóm thật để đọc xuôi.
MODEL_GROUP="Wan 2.2 Animate / Qwen-Image-Edit / LTX-2.3 — catalog KHÔNG lọc"
DEFAULT_DOMAIN="${DOMAIN:-motion-server.datools.info}"
NEED_OLLAMA=1                                   # dịch VN→EN (create-image), dịch phụ đề (subtitle), tryon Auto
NEED_BG_REMOVER=1                               # tryon + product-overlay cần tách nền/crop
PM2_APPS="minio,api,wf-worker,worker,comfyui,task-cloud-auto,bg-remover"

# Bộ 9 node = hợp của cả ba profile chuyên, cộng LTXVideo + SeedVR2 cho nhóm video/text-to-video/ss.
# Giống hệt danh sách setup-pm2.sh:713-721 trừ ComfyUI-Manager.
COMFY_NODES="https://github.com/kijai/ComfyUI-KJNodes https://github.com/kijai/ComfyUI-WanVideoWrapper https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite https://github.com/city96/ComfyUI-GGUF https://github.com/Fannovel16/comfyui_controlnet_aux https://github.com/Fannovel16/ComfyUI-Frame-Interpolation https://github.com/Lightricks/ComfyUI-LTXVideo https://github.com/numz/ComfyUI-SeedVR2_VideoUpscaler https://github.com/naxci1/ComfyUI-FlashVSR_Stable https://github.com/kijai/ComfyUI-WanAnimatePreprocess"

source "$ROOT/setup/lib-feature.sh"
feature_main
