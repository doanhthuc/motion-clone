#!/usr/bin/env bash
# #region ALD 23/07/2026 - Entrypoint image RunPod Serverless VVIP: khởi ComfyUI nền → chạy handler.
set -euo pipefail

# ComfyUI nghe nội bộ :8188. Models nằm trên RunPod Network Volume mount ở /app/ComfyUI/models
# (KHÔNG bake vào image — chọn scale-to-zero). COMFY_EXTRA_ARGS để thêm cờ (vd --highvram) nếu cần.
cd /app/ComfyUI
echo "[entrypoint] starting ComfyUI on 127.0.0.1:8188 ..."
python -u main.py --listen 127.0.0.1 --port 8188 ${COMFY_EXTRA_ARGS:-} \
    > /tmp/comfyui.log 2>&1 &

# Handler serverless — tự chờ ComfyUI ready ở request đầu (_ensure_comfy), sau đó xử lý từng job.
cd /app/worker
echo "[entrypoint] starting RunPod serverless handler ..."
exec python -u runpod/rp_handler.py
# #endregion
