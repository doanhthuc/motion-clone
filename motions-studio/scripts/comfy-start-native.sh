#!/usr/bin/env bash
# comfy-start-native.sh — Khởi động ComfyUI native cho PM2 (app "comfyui" trong ecosystem.config.cjs).
# PM2 lo auto-restart, nên file này chỉ exec ComfyUI ở foreground (KHÔNG tự loop/supervise).
# COMFY_DIR / COMFY_PORT truyền từ ecosystem (mặc định ~/ComfyUI : 8188).
set -eu

COMFY_DIR="${COMFY_DIR:-$HOME/ComfyUI}"
PORT="${COMFY_PORT:-8188}"

if [ ! -x "$COMFY_DIR/venv/bin/python" ]; then
  echo "[comfy] Chưa cài ComfyUI ở $COMFY_DIR (thiếu venv). Chạy lại ./setup/setup.sh." >&2
  exit 1
fi

cd "$COMFY_DIR"
# shellcheck disable=SC1091
source venv/bin/activate
export PYTHONUNBUFFERED=1
export HF_HOME="${HF_HOME:-$COMFY_DIR/hf-cache}"
mkdir -p "$HF_HOME"

# ALD 05/07/2026 - TỰ CHỮA "Driver/library version mismatch". Khi unattended-upgrade nâng gói driver
# NVIDIA nhưng kernel-module bản cũ còn nạp trong RAM → NVML lệch phiên bản → CUDA ném Error 804 →
# ComfyUI crash-loop → node (TryOn/motion) báo "refused 8188" hoặc kẹt 15% (bước upload). PM2 restart
# comfyui liên tục nên preflight này reload module cho khớp userspace ngay lần start kế — KHÔNG cần
# reboot (DKMS đã build sẵn .ko cho kernel hiện tại). Best-effort: hỏng thì vẫn thử start như cũ.
if command -v nvidia-smi >/dev/null 2>&1; then
  if ! nvidia-smi >/dev/null 2>&1 && nvidia-smi 2>&1 | grep -qi "version mismatch"; then
    echo "[comfy] ⚠ NVML driver/library mismatch — reload kernel module NVIDIA…" >&2
    _sudo=""; [ "$(id -u)" -ne 0 ] && _sudo="sudo -n"
    # Lúc mismatch mọi tiến trình CUDA khác cũng không init được nên GPU thường trống; gỡ theo thứ tự phụ thuộc.
    for _m in nvidia_uvm nvidia_drm nvidia_modeset nvidia; do $_sudo rmmod "$_m" 2>/dev/null || true; done
    $_sudo modprobe nvidia_uvm 2>/dev/null || true   # kéo theo nvidia + nvidia_modeset
    if nvidia-smi >/dev/null 2>&1; then
      echo "[comfy] ✓ Đã reload driver: $(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1)" >&2
    else
      echo "[comfy] ✗ Reload chưa được (module bị giữ / thiếu sudo) — có thể cần reboot. Vẫn thử start." >&2
    fi
  fi
fi

# expandable_segments giảm phân mảnh VRAM (tránh OOM "free 30MB" trên Wan 14B).
# PYTORCH_ALLOC_CONF là tên chuẩn; vẫn nhận alias cũ từ .env của các box đã cài.
export PYTORCH_ALLOC_CONF="${PYTORCH_ALLOC_CONF:-${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}}"
export CUDA_MODULE_LOADING="${CUDA_MODULE_LOADING:-LAZY}"

# ALD 25/07/2026 - Fix "nvrtc: error: failed to open libnvrtc-builtins.so.13.0". Torch build cu13
# nạp libnvrtc.so.13 rồi dlopen builtins THEO SONAME — nvrtc không dùng RPATH cho builtins nên thư mục
# chứa nó (site-packages/nvidia/cu13/lib) phải nằm trong LD_LIBRARY_PATH, không thì crash giữa job
# (DWPose/yolox torchscript JIT-compile 1 kernel qua nvrtc). Gom mọi nvidia/*/lib torch bundle cho chắc.
for _nvlib in "$COMFY_DIR"/venv/lib/python*/site-packages/nvidia/*/lib; do
  [ -d "$_nvlib" ] && LD_LIBRARY_PATH="${_nvlib}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
done
export LD_LIBRARY_PATH

exec python main.py --listen 127.0.0.1 --port "$PORT" --disable-smart-memory
