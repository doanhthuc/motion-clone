#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
# setup-motion-transfer.sh — Cài box CHUYÊN motion-transfer + teen-flycam + trend-tiktok (Wan 2.2 Animate), KHOÁ cứng.
#
#   git clone <repo> motion-backend && cd motion-backend
#   ./setup/setup-motion-transfer.sh
#
# Box này CHỈ chạy nhóm chức năng dùng cùng stack Wan motion:
#   • motion: video → nhân vật chuyển động (Wan 2.2 Animate)
#   • teen-flycam: 1 ảnh người mẫu → preset social clip, clone demo driver / Wan Animate
#   • trend-tiktok: 2 ảnh before/after → clip trend TikTok bám preset driver / Wan Animate
#   • Custom node: ComfyUI-WanVideoWrapper + ComfyUI-KJNodes + ComfyUI-VideoHelperSuite
#     + comfyui_controlnet_aux (DWPose) + ComfyUI-Frame-Interpolation (RIFE nội suy fps)
#     + ComfyUI-FlashVSR_Stable (AI enhance video).
#     WanVideoWrapper phải có cả FunCamera optional nodes (WanCameraEmbedding / WanCameraImageToVideo)
#     vì teen-flycam dùng chúng ở một số preset/nhánh fallback.
#   • KHÔNG cài: ComfyUI-GGUF, ComfyUI-LTXVideo, ComfyUI-Manager.
#   • JOB_TYPES = motion,teen-flycam,trend-tiktok (worker không nhận job khác).
#   • Model tải RIÊNG qua Settings → Models AI (nhóm Wan 2.2 Animate) — KHÔNG tải lúc cài.
#     DWPose (yolox_l, dw-ll_ucoco) + RIFE (rife47.pth) custom node TỰ tải lần chạy đầu.
#   • Catalog khoá: comfyui/catalog-motion-transfer.json (chỉ thấy/tải được model Wan Animate).
#   • KHÔNG cần Ollama.
#
# Cấu hình máy đề xuất: xem DEPLOY.md (≥24GB VRAM + BlockSwap, khuyến nghị RTX 5090 32GB).
# Cờ env tuỳ chọn: DOMAIN, SUPER_ADMIN, GMAIL_USER, GMAIL_APP_PASSWORD,
#   CF_API_TOKEN, CF_TUNNEL_TOKEN, CORS_ORIGINS, HF_TOKEN, COMFY_DIR, SKIP_COMFY, SKIP_HTTPS.
# ════════════════════════════════════════════════════════════════════════════
set -uo pipefail
cd "$(dirname "$0")/.."; ROOT="$(pwd)"

# ── PROFILE: motion-transfer ─────────────────────────────────────────────────
FEATURE="motion-transfer"
FEATURE_TITLE="Motion-Transfer + Teen Flycam + Trend TikTok (Wan 2.2 Animate)"
# #region ALD 26/07/2026 - Thêm 'enhance' + task-cloud-auto để box thuê trên RunPod tự kéo task.
#   • enhance: auto-worker của Task Cloud chèn node enhance ngay sau motion khi khách chọn
#     deliveredQuality cao hơn quality render → thiếu type này thì job enhance nằm queued mãi.
#   • task-cloud-auto: tiến trình PM2 poll /api/connector/tasks/claim. KHÔNG bật thì box
#     dựng xong vẫn "online" nhưng không bao giờ nhận được task nào từ Task Cloud.
JOB_TYPE="motion,teen-flycam,trend-tiktok,enhance"
CATALOG_FILE="$ROOT/comfyui/catalog-motion-transfer.json"
MODEL_GROUP="Wan 2.2 Animate / FlashVSR Enhance"
DEFAULT_DOMAIN="${DOMAIN:-motion-transfer.datools.info}"
NEED_OLLAMA=0                                   # motion-transfer không cần LLM
PM2_APPS="minio,api,wf-worker,worker,comfyui,task-cloud-auto"   # KHÔNG bg-remover
# #endregion
COMFY_NODES="https://github.com/kijai/ComfyUI-WanVideoWrapper https://github.com/kijai/ComfyUI-KJNodes https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite https://github.com/Fannovel16/comfyui_controlnet_aux https://github.com/Fannovel16/ComfyUI-Frame-Interpolation https://github.com/naxci1/ComfyUI-FlashVSR_Stable https://github.com/kijai/ComfyUI-WanAnimatePreprocess"

source "$ROOT/setup/lib-feature.sh"
feature_main
