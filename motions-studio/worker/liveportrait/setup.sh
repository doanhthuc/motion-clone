#!/usr/bin/env bash
# ALD 13/07/2026 - Cài LivePortrait tách biệt để test identity Ref + expression Driver cho Motion Transfer.
# Source/runtime nằm ở ~/liveportrait, KHÔNG sửa ComfyUI venv. Chỉ dùng Python/Torch CUDA đã có của ComfyUI;
# bốn package còn thiếu được cài vào PYTHONPATH overlay riêng. Script idempotent và pin commit đã kiểm thử.
set -euo pipefail

HOME_DIR="${LIVEPORTRAIT_HOME:-$HOME/liveportrait}"
REPO_DIR="${LIVEPORTRAIT_REPO:-$HOME_DIR/repo}"
PYTHON_BIN="${LIVEPORTRAIT_PYTHON:-$HOME/ComfyUI/venv/bin/python}"
DEPS_DIR="${LIVEPORTRAIT_PYTHONPATH:-$HOME_DIR/python-packages}"
COMMIT="${LIVEPORTRAIT_COMMIT:-9b294b3d0536135442ea73cb01e6cb3ca7029dd3}"
HF_HOME="${HF_HOME:-$HOME_DIR/huggingface-cache}"
HUGGINGFACE_HUB_CACHE="${HUGGINGFACE_HUB_CACHE:-$HF_HOME/hub}"
HF_XET_CACHE="${HF_XET_CACHE:-$HF_HOME/xet}"

export HF_HOME HUGGINGFACE_HUB_CACHE HF_XET_CACHE
# Box từng chạy Hugging Face bằng root nên ~/.cache/huggingface không ghi được.
# Dùng cache riêng của LivePortrait để setup luôn chạy bằng user ubuntu.
export HF_HUB_DISABLE_XET="${HF_HUB_DISABLE_XET:-1}"

mkdir -p "$HOME_DIR" "$DEPS_DIR" "$HUGGINGFACE_HUB_CACHE" "$HF_XET_CACHE"
if [[ ! -d "$REPO_DIR/.git" ]]; then
  git clone https://github.com/KlingAIResearch/LivePortrait.git "$REPO_DIR"
fi
git -C "$REPO_DIR" fetch --depth=1 origin "$COMMIT"
git -C "$REPO_DIR" checkout --detach "$COMMIT"

"$PYTHON_BIN" -m pip install --upgrade --target "$DEPS_DIR" \
  'lmdb==1.4.1' 'ffmpeg-python==0.2.0' 'tyro==0.8.5' 'pykalman==0.9.7'

PYTHONPATH="$DEPS_DIR${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" - <<PY
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id="KlingTeam/LivePortrait",
    local_dir=r"$REPO_DIR/pretrained_weights",
    ignore_patterns=["*.git*", "README.md", "docs/*", "liveportrait_animals/*"],
)
PY

PYTHONPATH="$DEPS_DIR${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" - <<'PY'
import torch, tyro, lmdb, ffmpeg, pykalman, onnxruntime
print(f"LivePortrait runtime OK · torch={torch.__version__} · cuda={torch.version.cuda} · ort={onnxruntime.__version__}")
assert torch.cuda.is_available(), "CUDA không sẵn sàng"
PY

printf 'LivePortrait ready: %s @ %s\n' "$REPO_DIR" "$COMMIT"
