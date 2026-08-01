#!/usr/bin/env bash
# Entrypoint image serverless cho stack tự chủ: sinh WORKER_ID duy nhất → ComfyUI nền → handler.
set -euo pipefail

# WORKER_ID PHẢI khác nhau giữa các container. api/src/routes/jobs.js:219 reclaim mọi job
# 'running' của cùng worker_id mỗi lần có ai gọi /worker/claim, nên hai container dùng chung id
# nghĩa là container B chuyển job đang render của container A sang 'error'. Với max workers >= 2
# lỗi này xảy ra ngay job thứ hai, và triệu chứng ("Worker khởi động lại giữa chừng") không hề
# gợi ý nguyên nhân. Ưu tiên id RunPod cấp; không có thì sinh ngẫu nhiên.
export WORKER_ID="${WORKER_ID:-serverless-${RUNPOD_POD_ID:-$(head -c 8 /dev/urandom | od -An -tx1 | tr -d ' \n')}}"
echo "[entrypoint] WORKER_ID=$WORKER_ID"

export COMFY_URL="${COMFY_URL:-http://127.0.0.1:8188}"

cd /app/ComfyUI
echo "[entrypoint] starting ComfyUI on 127.0.0.1:8188 ..."
python -u main.py --listen 127.0.0.1 --port 8188 ${COMFY_EXTRA_ARGS:-} > /tmp/comfyui.log 2>&1 &

cd /app/worker
echo "[entrypoint] starting serverless handler ..."
exec python -u runpod/mc_handler.py
