#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
# setup-cpu-box.sh — Box KHÔNG GPU: chỉ Postgres + MinIO + api + frontend.
#                    GPU đến từ RunPod Serverless, không từ máy này.
#
#   ./setup/setup-cpu-box.sh
#
# Vì sao có profile này: api, Postgres, MinIO và frontend đều là việc CPU. Thuê RTX 5090 để chạy
# chúng là trả tiền cho một GPU nằm không 24/7 ($0,99/giờ ≈ $720/tháng). Box CPU $0,06/giờ làm
# đúng những việc đó, và job render đi qua RunPod Serverless — trả theo giây, $0 khi rỗi.
#
# CHỈ đúng khi app cần bật 24/7 và tải dưới ~79 job/ngày (đo giá box thật $0,184/giờ 04/08/2026). Nếu bạn chỉ bật máy lúc ngồi làm và job
# chạy nối nhau thì pod GPU + WORKER_SOURCE=local RẺ HƠN, vì serverless đắt hơn pod 1,32-1,58× cho
# mỗi giây GPU và không có thời gian rỗi nào để nó tiết kiệm.
# Xem docs/gpu-pod.md#deploy-shapes và specs/2026-08-04-box-cpu-serverless-design.md.
#
# KHÁC setup-motion-transfer.sh ở đúng ba điểm:
#   • PM2_APPS bỏ `comfyui`, `worker`, `task-cloud-auto` — cả ba đều đòi ComfyUI/GPU. Riêng
#     task-cloud-auto throw "COMFY_URL chưa cấu hình" (task-cloud/auto-worker.js:100) và
#     crash-loop vĩnh viễn nếu để lại.
#   • SKIP_COMFY=1 tường minh. phase_comfyui vốn tự bỏ ComfyUI khi không thấy GPU, nhưng nó chỉ
#     CẢNH BÁO — và nếu lspci thấy card mà chưa có driver thì nó rẽ sang nhánh cài driver + đòi
#     reboot. Trên box CPU nhánh đó là vô nghĩa, nên chặn thẳng.
#   • JOB_TYPE = đúng 4 type mà image serverless bake. Lệch danh sách này thì cổng kiểm trong
#     scripts/pod-bootstrap.sh cảnh báo, và job type thừa sẽ nằm 'queued' vĩnh viễn.
#
# Đo thật trên pod CPU RunPod EU-RO-1 ngày 04/08/2026:
#   $0,06/giờ · 2 vCPU · RAM container 4 GB · Network Volume mount được ở /workspace (MooseFS)
#   → Đo thật lúc chạy: toàn bộ PM2 chỉ 311 MB, cộng Postgres thì dưới 1 GB. Thứ DUY NHẤT cần
#     nhiều RAM là `npm run build` của Nuxt trong pod-fe.sh — và chưa ai đo rằng 4 GB không đủ
#     cho nó. Xem docs/gpu-pod.md#box-cpu-ram trước khi trả tiền cho flavor lớn.
#
# Cờ env tuỳ chọn: DOMAIN, SUPER_ADMIN, GMAIL_USER, GMAIL_APP_PASSWORD,
#   CF_API_TOKEN, CF_TUNNEL_TOKEN, CORS_ORIGINS, HF_TOKEN, SKIP_HTTPS.
# ════════════════════════════════════════════════════════════════════════════
set -uo pipefail
cd "$(dirname "$0")/.."; ROOT="$(pwd)"

# ── PROFILE: cpu-box ─────────────────────────────────────────────────────────
FEATURE="cpu-box"
FEATURE_TITLE="Box CPU (GPU do RunPod Serverless lo)"
# Khớp ENV JOB_TYPES bake trong worker/runpod/Dockerfile.selfhosted VÀ DISPATCH_JOB_TYPES trong
# .env gốc. Ba nơi phải giống nhau — xem docs/gpu-pod.md#serverless.
JOB_TYPE="motion,teen-flycam,trend-tiktok,enhance"
CATALOG_FILE="$ROOT/comfyui/catalog-motion-transfer.json"
MODEL_GROUP="Wan 2.2 Animate / FlashVSR Enhance"
DEFAULT_DOMAIN="${DOMAIN:-cpu-box.datools.info}"
NEED_OLLAMA=0                                   # không có GPU thì Ollama vô ích ở đây
PM2_APPS="minio,api,wf-worker"                   # KHÔNG comfyui / worker / task-cloud-auto

# ComfyUI KHÔNG được cài trên box này (SKIP_COMFY=1 ngay dưới), nên danh sách này không bao giờ
# được clone. Vẫn phải khai vì lib-feature.sh:823 đòi COMFY_NODES khác rỗng — và giữ đúng danh
# sách thật thay vì một chuỗi giả, để ai cố tình chạy profile này trên máy CÓ GPU với SKIP_COMFY=0
# vẫn nhận được cài đặt đúng chứ không phải một mớ node sai.
COMFY_NODES="https://github.com/kijai/ComfyUI-WanVideoWrapper https://github.com/kijai/ComfyUI-KJNodes https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite https://github.com/Fannovel16/comfyui_controlnet_aux https://github.com/Fannovel16/ComfyUI-Frame-Interpolation https://github.com/naxci1/ComfyUI-FlashVSR_Stable"

export SKIP_COMFY=1

source "$ROOT/setup/lib-feature.sh"
feature_main
