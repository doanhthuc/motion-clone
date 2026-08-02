# ComfyUI + custom nodes cho pipeline Wan 2.2 Animate. Models tải lúc entrypoint (skip nếu đã có).
# ALD 13/07/2026 - PyTorch 2.12.1 + CUDA 13.0 là baseline stable chính thức cho Blackwell/RTX 5090.
FROM pytorch/pytorch:2.12.1-cuda13.0-cudnn9-runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    git wget aria2 ffmpeg libgl1 libglib2.0-0 ca-certificates \
    build-essential pkg-config \
    && rm -rf /var/lib/apt/lists/*

# ── ALD-local 02/08/2026 - PEP 668: không có dòng này thì image KHÔNG build được ───────────────
# pytorch/pytorch:2.12.1-* dựng trên Ubuntu 24.04, nơi Python hệ thống có
# /usr/lib/python3.12/EXTERNALLY-MANAGED → `pip install` từ chối cài vào system site-packages và
# build chết ngay ở requirements.txt của ComfyUI. Đo thật 02/08/2026 trên GitHub Actions với
# worker/runpod/Dockerfile.selfhosted (cùng base, cùng lệnh) — lỗi in ra là một bức tường chữ về
# python3-full/pipx, không nói gì về việc bạn đang ở trong container.
# Trong container thì "phá gói hệ thống" chính là điều ta muốn: cả image chỉ phục vụ một ứng dụng.
# Đặt bằng ENV để mọi lệnh pip phía sau — kể cả lệnh thêm về sau — không quên mất nó.
# Sửa CỤC BỘ trên file upstream: scripts/check-local-deltas.sh canh dòng này, và
# scripts/sync-upstream.sh gọi nó để lần sync sau không âm thầm xoá đi.
ENV PIP_BREAK_SYSTEM_PACKAGES=1

WORKDIR /app
RUN git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git
WORKDIR /app/ComfyUI
RUN pip install --no-cache-dir -r requirements.txt

# Custom nodes cần cho workflow Wan Animate (KJNodes, WanVideoWrapper, VHS, GGUF, controlnet_aux/DWPose)
# + ComfyUI-LTXVideo cho engine LTX-2.3 ở Teaser (ảnh+prompt → video+audio).
WORKDIR /app/ComfyUI/custom_nodes
RUN git clone --depth 1 https://github.com/kijai/ComfyUI-KJNodes.git \
 && git clone --depth 1 https://github.com/kijai/ComfyUI-WanVideoWrapper.git \
 && git clone --depth 1 https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git \
 && git clone --depth 1 https://github.com/city96/ComfyUI-GGUF.git \
 && git clone --depth 1 https://github.com/Fannovel16/comfyui_controlnet_aux.git \
 && git clone --depth 1 https://github.com/Lightricks/ComfyUI-LTXVideo.git
# Cài deps của từng custom node (best-effort, không fail build nếu 1 node thiếu)
RUN for d in */ ; do [ -f "$d/requirements.txt" ] && pip install --no-cache-dir -r "$d/requirements.txt" || true ; done

# SageAttention là đường attention chính cho Wan trên RTX 5090. Cài sau custom-node deps để giữ đúng
# torch/triton của image cu130; verify ngay lúc build (không cần GPU) để tránh image build xong nhưng runtime fallback.
RUN pip install --no-cache-dir sageattention \
 && python -c "import torch, sageattention; assert torch.__version__.split('+')[0] == '2.12.1', torch.__version__; cv=tuple(map(int, torch.version.cuda.split('.')[:2])); assert cv >= (13, 0), torch.version.cuda"

WORKDIR /app/ComfyUI
COPY models.txt /app/models.txt
COPY catalog.json /app/catalog.json
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 8188
ENTRYPOINT ["/app/entrypoint.sh"]
