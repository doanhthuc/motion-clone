#!/bin/bash
# viXTTS TTS service launcher (venv riêng, KHÔNG đụng ComfyUI). Port 127.0.0.1:8090.
# ALD 02/07/2026 - path theo $HOME/VIXTTS_DIR thay hardcode /home/ubuntu: VPS chạy user khác vẫn đúng.
# Override được qua env: VIXTTS_DIR / HF_HOME / VIXTTS_MODEL / VIXTTS_REF.
VIXTTS_DIR="${VIXTTS_DIR:-$HOME/ai/vixtts}"
cd "$VIXTTS_DIR" || exit 1
export HF_HOME="${HF_HOME:-$HOME/ai/hf-cache}"
export COQUI_TOS_AGREED=1
export VIXTTS_MODEL="${VIXTTS_MODEL:-$VIXTTS_DIR/model}"
export VIXTTS_REF="${VIXTTS_REF:-$VIXTTS_DIR/voices/ref_main.wav}"
exec ./venv/bin/python -m uvicorn service:app --host 127.0.0.1 --port "${VIXTTS_PORT:-8090}"
