#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
# setup-create-image.sh — Cài box CHUYÊN create-image (Qwen-Image-Edit), KHOÁ cứng.
#
#   git clone <repo> motion-backend && cd motion-backend
#   ./setup/setup-create-image.sh
#
# Box này CHỈ chạy chức năng tạo ảnh (text/ảnh → ảnh, Qwen-Image-Edit):
#   • Custom node: ComfyUI-GGUF (UnetLoaderGGUF) + ComfyUI-KJNodes. (Qwen text-encode,
#     CFGNorm, ModelSamplingAuraFlow = node CORE của ComfyUI — không cần cài thêm.)
#   • KHÔNG cài: WanVideoWrapper, VHS, controlnet_aux, LTXVideo, Frame-Interpolation, ComfyUI-Manager.
#   • JOB_TYPES = create-image (worker không nhận job khác).
#   • Model tải RIÊNG qua Settings → Models AI (nhóm Qwen-Image-Edit) — KHÔNG tải lúc cài.
#   • Catalog khoá: comfyui/catalog-create-image.json (chỉ thấy/tải được model Qwen).
#
# Cấu hình máy đề xuất: xem DEPLOY.md (≥24GB VRAM, khuyến nghị RTX 5090 32GB).
# Cờ env tuỳ chọn: DOMAIN, SUPER_ADMIN, GMAIL_USER, GMAIL_APP_PASSWORD,
#   CF_API_TOKEN, CF_TUNNEL_TOKEN, CORS_ORIGINS, HF_TOKEN, COMFY_DIR, SKIP_COMFY, SKIP_HTTPS.
# ════════════════════════════════════════════════════════════════════════════
set -uo pipefail
cd "$(dirname "$0")/.."; ROOT="$(pwd)"

# ── PROFILE: create-image ────────────────────────────────────────────────────
FEATURE="create-image"
FEATURE_TITLE="Create-Image (Qwen-Image-Edit)"
JOB_TYPE="create-image"
CATALOG_FILE="$ROOT/comfyui/catalog-create-image.json"
MODEL_GROUP="Qwen-Image-Edit"
DEFAULT_DOMAIN="${DOMAIN:-create-image.datools.info}"
NEED_OLLAMA=1                                   # dịch prompt VN→EN (qwen2.5:7b-instruct, tải riêng)
PM2_APPS="minio,api,wf-worker,worker,comfyui"   # KHÔNG bg-remover
COMFY_NODES="https://github.com/city96/ComfyUI-GGUF https://github.com/kijai/ComfyUI-KJNodes"

source "$ROOT/setup/lib-feature.sh"
feature_main
