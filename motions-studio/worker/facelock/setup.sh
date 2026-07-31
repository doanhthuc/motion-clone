#!/usr/bin/env bash
# ALD 03/07/2026 - Tái tạo môi trường faceLock trên box (giống pattern vixtts/setup.sh).
# venv RIÊNG ở ~/facelock — tuyệt đối không cài vào venv ComfyUI/worker (tránh vỡ dependency prod).
set -euo pipefail
FACELOCK_DIR="${FACELOCK_DIR:-$HOME/facelock}"
mkdir -p "$FACELOCK_DIR/models"
[ -d "$FACELOCK_DIR/venv" ] || python3 -m venv "$FACELOCK_DIR/venv"
"$FACELOCK_DIR/venv/bin/pip" install -U pip wheel setuptools
"$FACELOCK_DIR/venv/bin/pip" install insightface opencv-python-headless numpy tqdm
# GOTCHA: insightface kéo `onnxruntime` (CPU) vào; để chung với -gpu thì bản CPU đè mất CUDA EP
# → phải gỡ CẢ HAI rồi cài lại riêng bản gpu. CUDA EP cần thêm wheels nvidia cu12 (cuDNN9/cuBLAS).
"$FACELOCK_DIR/venv/bin/pip" uninstall -y onnxruntime onnxruntime-gpu || true
"$FACELOCK_DIR/venv/bin/pip" install onnxruntime-gpu nvidia-cuda-runtime-cu12 nvidia-cudnn-cu12 nvidia-cublas-cu12 nvidia-cufft-cu12
# inswapper_128 (~530MB). buffalo_l tự tải về $FACELOCK_DIR/models/buffalo_l ở lần chạy đầu.
[ -f "$FACELOCK_DIR/models/inswapper_128.onnx" ] || curl -L -o "$FACELOCK_DIR/models/inswapper_128.onnx" \
  "https://huggingface.co/ezioruan/inswapper_128.onnx/resolve/main/inswapper_128.onnx"
cp "$(dirname "$0")/swap_video.py" "$FACELOCK_DIR/swap_video.py"
echo "faceLock OK: $FACELOCK_DIR"
