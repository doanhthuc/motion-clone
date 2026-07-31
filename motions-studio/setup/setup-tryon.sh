#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
# setup-tryon.sh — Cài box CHUYÊN Try-On (thử đồ, Qwen-Image-Edit 2509), KHOÁ cứng.
#
#   git clone <repo> motion-backend && cd motion-backend
#   ./setup/setup-tryon.sh
#
# #region ALD 26/07/2026 - Box Try-On riêng cho luồng thuê máy RunPod 1-click.
# Vì sao KHÔNG dùng lại setup-create-image.sh: file đó khoá JOB_TYPE="create-image"
# nên worker sẽ KHÔNG BAO GIỜ claim job `tryon` dù máy có đủ model Qwen-Image-Edit.
# Task Cloud gửi task type `edit-image` → auto-worker dựng node `tryon` (auto-worker.js:258)
# → box phải nhận được cả `tryon` lẫn `edit-image` thì mới render được.
# #endregion
#
# Box này CHỈ chạy nhóm sửa/tạo ảnh (Qwen-Image-Edit 2509):
#   • Custom node: ComfyUI-GGUF (UnetLoaderGGUF) + ComfyUI-KJNodes. (Qwen text-encode,
#     CFGNorm, ModelSamplingAuraFlow = node CORE của ComfyUI — không cần cài thêm.)
#   • KHÔNG cài: WanVideoWrapper, VHS, controlnet_aux, LTXVideo, Frame-Interpolation, ComfyUI-Manager.
#   • JOB_TYPES = tryon,create-image,edit-image,enhance (worker không nhận job khác).
#   • Model tải RIÊNG qua Settings → Models AI (nhóm Qwen-Image-Edit) — KHÔNG tải lúc cài.
#   • Catalog khoá: comfyui/catalog-create-image.json (chỉ thấy/tải được model Qwen).
#
# Cấu hình máy đề xuất: xem DEPLOY.md (≥24GB VRAM, khuyến nghị RTX 5090 32GB).
# Cờ env tuỳ chọn: DOMAIN, SUPER_ADMIN, GMAIL_USER, GMAIL_APP_PASSWORD,
#   CF_API_TOKEN, CF_TUNNEL_TOKEN, CORS_ORIGINS, HF_TOKEN, COMFY_DIR, SKIP_COMFY, SKIP_HTTPS.
# ════════════════════════════════════════════════════════════════════════════
set -uo pipefail
cd "$(dirname "$0")/.."; ROOT="$(pwd)"

# ── PROFILE: tryon ───────────────────────────────────────────────────────────
FEATURE="tryon"
FEATURE_TITLE="Try-On (Qwen-Image-Edit 2509)"
JOB_TYPE="tryon,create-image,edit-image,enhance"
CATALOG_FILE="$ROOT/comfyui/catalog-create-image.json"
MODEL_GROUP="Qwen-Image-Edit"
DEFAULT_DOMAIN="${DOMAIN:-tryon.datools.info}"
NEED_OLLAMA=1                                                   # dịch prompt VN→EN (qwen2.5:7b-instruct, tải riêng)
PM2_APPS="minio,api,wf-worker,worker,comfyui,task-cloud-auto"   # KHÔNG bg-remover (autocrop sản phẩm tự tắt khi thiếu)
COMFY_NODES="https://github.com/city96/ComfyUI-GGUF https://github.com/kijai/ComfyUI-KJNodes"

source "$ROOT/setup/lib-feature.sh"
feature_main
