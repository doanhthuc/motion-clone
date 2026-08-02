# #region ALD 23/07/2026 - Image RunPod Serverless VVIP: 1 container chạy CẢ ComfyUI (:8188) LẪN handler.
# Build từ THƯ MỤC GỐC motion-backend/ :
#   docker build -f worker/runpod/Dockerfile -t <user>/motion-vvip-runpod:latest .
# Models KHÔNG bake — nằm trên RunPod Network Volume mount vào /app/ComfyUI/models (scale-to-zero, image gọn).
FROM pytorch/pytorch:2.12.1-cuda13.0-cudnn9-runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    git wget aria2 ffmpeg libgl1 libglib2.0-0 ca-certificates build-essential pkg-config \
    && rm -rf /var/lib/apt/lists/*

# ── PEP 668: không có dòng này thì image KHÔNG build được ─────────────────────────────────────
# Base Ubuntu 24.04 có /usr/lib/python3.12/EXTERNALLY-MANAGED → pip từ chối cài vào system
# site-packages, build chết ở requirements.txt của ComfyUI với một bức tường chữ về python3-full và
# pipx, không hề nói rằng bạn đang ở trong container. Đo thật 02/08/2026 trên GitHub Actions bằng
# Dockerfile.selfhosted cạnh đây (cùng base, cùng lệnh pip).
# Lệnh build trong README-runpod.md và ở đầu file này vì thế đang KHÔNG chạy được — dòng này sửa nó.
ENV PIP_BREAK_SYSTEM_PACKAGES=1

# ───────────────── ComfyUI + custom nodes (ĐỒNG BỘ với comfyui/Dockerfile) ─────────────────
WORKDIR /app
RUN git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git
WORKDIR /app/ComfyUI
RUN pip install --no-cache-dir -r requirements.txt

WORKDIR /app/ComfyUI/custom_nodes
RUN git clone --depth 1 https://github.com/kijai/ComfyUI-KJNodes.git \
 && git clone --depth 1 https://github.com/kijai/ComfyUI-WanVideoWrapper.git \
 && git clone --depth 1 https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git \
 && git clone --depth 1 https://github.com/city96/ComfyUI-GGUF.git \
 && git clone --depth 1 https://github.com/Fannovel16/comfyui_controlnet_aux.git \
 && git clone --depth 1 https://github.com/Lightricks/ComfyUI-LTXVideo.git \
 && git clone --depth 1 https://github.com/Fannovel16/ComfyUI-Frame-Interpolation.git
RUN for d in */ ; do [ -f "$d/requirements.txt" ] && pip install --no-cache-dir -r "$d/requirements.txt" || true ; done
# ALD 24/07/2026 - Hậu kỳ VVIP cố định Lanczos 1080p · FPS gốc bằng ffmpeg;
# không cần ESRGAN, RIFE hoặc FlashVSR cho bước giao video.

# ⚠️⚠️ ON-BOX PARITY (chỗ dễ vỡ nhất — xem memory box165-landmines):
#   Box .165 có thêm node VÁ TAY ngoài git mà workflow Wan-Animate CẦN, điển hình:
#     - WanAnimatePreprocess (kijai, đã patch tay) — thiếu → ComfyUI 400 "node type not found"
#     - node DWPose/detection + 2 file onnx trong models/detection
#   BẮT BUỘC copy y hệt custom_nodes trên .165 vào image này (thêm COPY hoặc git clone bản patch nội bộ)
#   thì handler mới render được. Đây là bước phải test 1 vòng trên RunPod.
# RUN git clone --depth 1 <repo-noi-bo>/WanAnimatePreprocess.git ...

# SageAttention = đường attention chính cho Wan trên RTX 5090 (verify torch/cuda ngay lúc build)
RUN pip install --no-cache-dir sageattention \
 && python -c "import torch, sageattention; assert torch.__version__.split('+')[0]=='2.12.1', torch.__version__"

# ───────────────── Worker code + handler serverless + deps ─────────────────
WORKDIR /app/worker
COPY worker/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
 && pip install --no-cache-dir runpod boto3
# Copy toàn bộ worker/ (worker_runtime, facelock, liveportrait, wan_animate, assets, subworkers, runpod/…)
COPY worker/ ./
COPY worker/runpod/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENV COMFY_URL=http://127.0.0.1:8188 \
    API_URL=http://127.0.0.1:9 \
    WORKER_TOKEN=serverless \
    MOTION_FRESH_COMFY=0 \
    PYTHONPATH=/app/worker \
    HF_HOME=/root/.cache/hf

ENTRYPOINT ["/app/entrypoint.sh"]
# #endregion
