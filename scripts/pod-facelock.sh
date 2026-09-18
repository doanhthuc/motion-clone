#!/usr/bin/env bash
#
# Install the faceLock environment on the pod, so `faceLock: 1` on a motion job actually does
# something. Without it the worker only logs a warning and hands back the un-swapped video
# (worker_runtime/linux.py:430) — the job still costs the same GPU minutes.
#
#   make gpu-facelock
#
# What faceLock is: after Wan renders, insightface/inswapper_128 swaps the face of every frame
# back to the face in the reference image, keeping the expression and mouth shape Wan produced
# (worker/facelock/swap_video.py). It is the only lever here that fixes driver-face leakage
# without paying for it in expression, because it runs after the model, not inside it.
#
# pod-bootstrap.sh calls this at the end, because camera-motion turns faceLock on by default. It
# stays a make target of its own so a pod that was bootstrapped earlier, or whose install failed,
# can get it without re-running the whole bootstrap.
#
# Where it lands: /root/facelock on the container disk, which is the worker's default
# FACELOCK_DIR (ecosystem.config.cjs does not forward that variable). It is NOT put on the Network
# Volume. The first version did that, measured 18/09/2026: pip spent over 10 minutes writing
# 2.7GB of small files to the MooseFS mount and still had not reached the last package. The same
# install on container disk took about 30 seconds including the 528MB model download (~76MB/s) —
# but with pip's cache already warm from that first attempt. A fresh pod has not been timed.
set -uo pipefail

log()  { printf '\033[36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33m !!\033[0m %s\n' "$*"; }
die()  { printf '\033[31m ✗ \033[0m%s\n' "$*" >&2; exit 1; }

env_get() { grep -E "^$1=" .env 2>/dev/null | cut -d= -f2- | sed -E 's/[[:space:]]*#.*$//' | tr -d '"'; }

HOST="$(env_get GPU_SSH_HOST)"
PORT="$(env_get GPU_SSH_PORT)"
[ -n "$HOST" ] && [ -n "$PORT" ] || die "GPU_SSH_HOST/GPU_SSH_PORT missing from .env — run: make gpu-wait"

REMOTE_DIR="motion-backend"   # same constant pod-bootstrap.sh:99 rsyncs into

SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -p "$PORT")
remote() { ssh "${SSH_OPTS[@]}" "root@$HOST" "$1" < /dev/null; }

# Ship the installer itself rather than requiring gpu-bootstrap first: faceLock only needs a GPU
# and these two files, so an A/B that re-swaps an existing video can skip the backend entirely.
remote "mkdir -p ~/$REMOTE_DIR/worker/facelock" || die "cannot reach the pod"
rsync -az -e "ssh ${SSH_OPTS[*]}" motions-studio/worker/facelock/ \
  "root@$HOST:~/$REMOTE_DIR/worker/facelock/" || die "rsync of worker/facelock failed"

log "installing faceLock into /root/facelock"
remote "FACELOCK_DIR=/root/facelock bash ~/$REMOTE_DIR/worker/facelock/setup.sh" \
  || die "faceLock install failed — see the output above"

# Proof, not /health. get_available_providers() lists CUDAExecutionProvider even when the CUDA
# libraries fail to load, so it said "CUDA" on 18/09/2026 while every frame ran on CPU. Only a
# real InferenceSession on the real model reports what will actually be used.
CHECK="$(remote "set -e
  test -x /root/facelock/venv/bin/python && echo venv=ok
  test -f /root/facelock/swap_video.py && echo script=ok
  du -m /root/facelock/models/inswapper_128.onnx 2>/dev/null | cut -f1 | sed 's/^/model_mb=/'
  /root/facelock/venv/bin/python -c '
import onnxruntime as o
o.preload_dlls()
s = o.InferenceSession(\"/root/facelock/models/inswapper_128.onnx\",
                       providers=[\"CUDAExecutionProvider\", \"CPUExecutionProvider\"])
print(\"session=\" + s.get_providers()[0])' 2>/dev/null")"
echo "$CHECK"

echo "$CHECK" | grep -q 'venv=ok'   || die "venv missing at /root/facelock/venv"
echo "$CHECK" | grep -q 'script=ok' || die "swap_video.py missing at /root/facelock"
echo "$CHECK" | grep -q 'model_mb=' || die "inswapper_128.onnx did not download"
echo "$CHECK" | grep -q 'session=CUDAExecutionProvider' \
  || die "the swap would run on CPU (452 frames: >15 min instead of ~22s on GPU) — onnxruntime and its CUDA libraries do not match"

log "faceLock ready. Enable it per job: faceLock: 1 in the motion stage of a batch manifest."
