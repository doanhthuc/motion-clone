#!/usr/bin/env python3
# ALD 14/06/2026 - Tối ưu disk cho node "SS" (LTX-2.3) sau khi setup tải models theo comfyui/models.txt.
# Checkpoint distilled-1.1 (~46GB) chứa: transformer model.* (~40GB, KHÔNG dùng vì đã có GGUF) + video VAE (vae.*)
# + audio-vae (audio_vae.*) + vocoder + projection (text_embedding_projection.*). Script này:
#   (1) Tách video VAE → models/vae/ltx-2.3-vae.safetensors (kèm config metadata) cho VAELoader (= env SS_VAE).
#   (2) CẮT transformer model.* khỏi checkpoint → còn ~4GB (giữ audio-vae+vocoder+projection+metadata để
#       LTXVAudioVAELoader/LTXAVTextEncoderLoader đọc; = env SS_CKPT). Đĩa cuối ~38GB thay vì ~82GB.
# Idempotent: re-run an toàn (bỏ qua nếu VAE đã có / checkpoint đã cắt). PHẢI chạy bằng python có safetensors
# (vd venv ComfyUI). Dùng:  python ltx-ss-postprocess.py [/path/to/ComfyUI/models]
import os, sys, json, struct

try:
    from safetensors import safe_open
    from safetensors.torch import save_file
except Exception as e:  # noqa
    print(f"[ltx-ss] BỎ QUA: thiếu safetensors/torch (chạy bằng venv ComfyUI). {e}")
    sys.exit(0)

MODELS = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/ai/ComfyUI/models")
CKPT = os.path.join(MODELS, "checkpoints", "ltx-2.3-22b-distilled-1.1.safetensors")
VAE = os.path.join(MODELS, "vae", "ltx-2.3-vae.safetensors")

if not os.path.exists(CKPT):
    print("[ltx-ss] không thấy checkpoint LTX-2.3 → bỏ qua (node SS chưa cài hoặc đã xử lý sang nguồn khác)")
    sys.exit(0)


def _meta(path):
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        return {k: str(v) for k, v in (json.loads(f.read(n)).get("__metadata__") or {}).items()}


meta = _meta(CKPT)

# (1) Tách video VAE (kèm config metadata — VAELoader cần để dựng đúng kiến trúc VAE 2.3).
if not os.path.exists(VAE):
    os.makedirs(os.path.dirname(VAE), exist_ok=True)
    out = {}
    with safe_open(CKPT, "pt", "cpu") as f:
        for k in f.keys():
            if k.startswith("vae."):
                out[k[len("vae."):]] = f.get_tensor(k)
    save_file(out, VAE, metadata=meta or None)
    print(f"[ltx-ss] (1) tách video VAE → {VAE} ({len(out)} tensors, {round(os.path.getsize(VAE)/1e9, 2)}GB)")
else:
    print("[ltx-ss] (1) video VAE đã có → bỏ qua")

# (2) Cắt transformer model.* khỏi checkpoint (chỉ khi còn model.* → idempotent).
has_model = False
with safe_open(CKPT, "pt", "cpu") as f:
    for k in f.keys():
        if k.startswith("model."):
            has_model = True
            break

if has_model:
    tmp = CKPT + ".strip"
    out = {}
    with safe_open(CKPT, "pt", "cpu") as f:
        for k in f.keys():
            if not k.startswith("model."):
                out[k] = f.get_tensor(k)
    save_file(out, tmp, metadata=meta or None)
    os.replace(tmp, CKPT)  # thay nguyên tử, GIỮ tên (SS_CKPT không đổi)
    print(f"[ltx-ss] (2) CẮT transformer → checkpoint còn {round(os.path.getsize(CKPT)/1e9, 2)}GB ({len(out)} tensors)")
else:
    print("[ltx-ss] (2) checkpoint đã gọn (không còn model.*) → bỏ qua")

print("[ltx-ss] xong — node SS dùng GGUF(unet) + ltx-2.3-vae.safetensors(VAE) + checkpoint-gọn(audio-vae+projection).")
