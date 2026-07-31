#!/usr/bin/env bash
# lib-gpu.sh — Baseline PyTorch/CUDA dùng chung cho mọi installer Linux NVIDIA.
# File này được source sau khi caller đã khai báo say/ok/warn.

#region ALD 13/07/2026 - PyTorch stable tối ưu theo GPU/driver, ưu tiên RTX 5090
motion_install_best_pytorch() {
  local comfy_dir="$1" py="$1/venv/bin/python" pip="$1/venv/bin/pip"
  local cc driver driver_major cc_major cc_minor modern=0 blackwell=0
  local channel torch_version vision_version cuda_min label

  cc="$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -1 | tr -d ' ')"
  driver="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1 | tr -d ' ')"
  driver_major="${driver%%.*}"; cc_major="${cc%%.*}"; cc_minor="${cc#*.}"
  [[ "$driver_major" =~ ^[0-9]+$ ]] || driver_major=0
  [[ "$cc_major" =~ ^[0-9]+$ ]] || cc_major=0
  [[ "$cc_minor" =~ ^[0-9]+$ ]] || cc_minor=0
  { [ "$cc_major" -gt 7 ] || { [ "$cc_major" -eq 7 ] && [ "$cc_minor" -ge 5 ]; }; } && modern=1
  [ "$cc_major" -ge 10 ] && blackwell=1

  # PyTorch 2.12: cu130 là stable; cu132 còn experimental. CUDA 13 cần driver R580+ và GPU Turing+.
  if [ "$modern" = 1 ] && [ "$driver_major" -ge 580 ]; then
    channel="cu130"; torch_version="2.12.1"; vision_version="0.27.1"; cuda_min="13.0"
    label="PyTorch $torch_version CUDA 13.0 stable"
  elif [ "$blackwell" = 1 ]; then
    # Driver R570 của RTX 50xx đời đầu chỉ chạy CUDA 12.8; vẫn dùng wheel có sm_120 thay vì cu126.
    channel="cu128"; torch_version="2.11.0"; vision_version="0.26.0"; cuda_min="12.8"
    label="PyTorch $torch_version CUDA 12.8 fallback"
    warn "Driver NVIDIA $driver chưa đạt R580 cho CUDA 13.0; dùng cu128. Nâng driver + reboot rồi chạy lại setup để lên cu130."
  else
    # CUDA 12.6 giữ hỗ trợ GPU cũ (Maxwell/Pascal/Volta) mà CUDA 13 đã loại bỏ.
    channel="cu126"; torch_version="2.12.1"; vision_version="0.27.1"; cuda_min="12.6"
    label="PyTorch $torch_version CUDA 12.6 compatibility"
  fi

  # Cho phép operator pin có chủ đích; mặc định luôn là ma trận stable ở trên.
  channel="${MOTION_PYTORCH_CHANNEL:-$channel}"
  torch_version="${MOTION_PYTORCH_VERSION:-$torch_version}"
  vision_version="${MOTION_TORCHVISION_VERSION:-$vision_version}"
  case "$channel" in
    cu132) cuda_min="13.2";; cu130) cuda_min="13.0";; cu128) cuda_min="12.8";; cu126) cuda_min="12.6";;
    *) warn "MOTION_PYTORCH_CHANNEL=$channel không được hỗ trợ tự động."; return 1;;
  esac

  if ! MOTION_MIN_TORCH="$torch_version" MOTION_MIN_CUDA="$cuda_min" "$py" - <<'PY' >/dev/null 2>&1
import os, re, torch
def v(s): return tuple(int(x) for x in re.findall(r"\d+", s)[:3])
assert torch.cuda.is_available()
assert v(torch.__version__) >= v(os.environ["MOTION_MIN_TORCH"])
assert v(torch.version.cuda or "0") >= v(os.environ["MOTION_MIN_CUDA"])
torch.empty(1, device="cuda")
PY
  then
    say "    Cài $label ($channel)…"
    "$pip" install -q -U \
      "torch==$torch_version" "torchvision==$vision_version" \
      --index-url "https://download.pytorch.org/whl/$channel" \
      || { warn "Cài $label lỗi."; return 1; }
  fi

  if MOTION_MIN_CUDA="$cuda_min" "$py" - <<'PY'
import os, re, torch
def v(s): return tuple(int(x) for x in re.findall(r"\d+", s)[:3])
assert torch.cuda.is_available() and v(torch.version.cuda or "0") >= v(os.environ["MOTION_MIN_CUDA"])
cc = torch.cuda.get_device_capability(0)
torch.empty(1, device="cuda")
print(f"torch={torch.__version__} · cuda={torch.version.cuda} · gpu={torch.cuda.get_device_name(0)} · cc={cc[0]}.{cc[1]}")
PY
  then
    ok "GPU stack đã verify ($channel)."
  else
    warn "PyTorch cài xong nhưng CUDA smoke test thất bại; kiểm tra driver bằng nvidia-smi."
    return 1
  fi
}
#endregion
