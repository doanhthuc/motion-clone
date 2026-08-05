# syntax=docker/dockerfile:1.7
# Image dựng sẵn cho pod GPU (MTC_PREBUILT=1). Xoá ~20-35 phút cài phần mềm mỗi lần dựng pod:
# apt/Node/torch/ComfyUI/custom-node làm ở CI một lần, pod chỉ symlink vào.
#
# Base PHẢI ship sshd và không được thoát: RunPod không tiêm sshd, image tự thoát là container
# restart vô hạn (docs/gpu-pod.md#runpod-gotchas §1). runpod/pytorch có /start.sh làm đúng việc đó.
#
# Bản trước base vastai/comfy — bỏ vì là image bên thứ ba và tên gây hiểu nhầm là dùng vast.ai
# (nhà cung cấp do GPU_PROVIDER quyết, không phải image). Nó KHÔNG va bẫy §1.
FROM runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404

USER root
ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MTC_PREBUILT_DIR=/opt/mtc-prebuilt

# apt cho ComfyUI + ffmpeg (worker gọi ffmpeg cho concat/reveal/subtitle) + build-essential
# (một số node compile extension lúc pip install).
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends \
      git wget curl ca-certificates jq aria2 ffmpeg libgl1 libglib2.0-0 \
      build-essential pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Ghim cả ComfyUI lõi, không chỉ custom node. Ghim 9 node mà thả lõi trôi theo master là ghim hụt:
# node và lõi khớp nhau qua API nội bộ của ComfyUI, và lõi là bên đổi API.
# `--depth 1` không checkout được commit cụ thể → clone --filter rồi checkout.
RUN git clone --filter=blob:none https://github.com/comfyanonymous/ComfyUI.git /opt/mtc-prebuilt/ComfyUI \
 && git -C /opt/mtc-prebuilt/ComfyUI checkout 322122449c9d2ba8b8df1bb517364527dd0615f1

# venv RIÊNG, không dùng python hệ thống: setup/lib-feature.sh:544 `die` nếu thiếu
# $COMFY_DIR/venv/bin/python, và venv tránh luôn PEP 668 của Ubuntu 24.04.
# --system-site-packages: KHÔNG dùng. Base có torch 2.8/cu128, kế thừa nó vào venv thì
# pip có thể coi torch là "đã có" và bỏ qua bản cu130 ta cần.
RUN python3 -m venv /opt/mtc-prebuilt/ComfyUI/venv \
 && /opt/mtc-prebuilt/ComfyUI/venv/bin/pip install --no-cache-dir -U pip wheel

# torch TRONG IMAGE là torch cuối cùng chạy trên pod: MTC_PREBUILT=1 bỏ qua
# motion_install_best_pytorch (lib-feature.sh:828-836), nên không có bước nào cài lại cho khớp
# driver ở runtime. Base ship 2.8.0+cu128; ta cài đè 2.12.1+cu130 cho khớp chuỗi đã chứng minh
# của Dockerfile.selfhosted và MIN_CUDA_VERSION=13.0.
RUN /opt/mtc-prebuilt/ComfyUI/venv/bin/pip install --no-cache-dir \
      --index-url https://download.pytorch.org/whl/cu130 \
      torch==2.12.1 torchvision torchaudio

RUN /opt/mtc-prebuilt/ComfyUI/venv/bin/pip install --no-cache-dir \
      -r /opt/mtc-prebuilt/ComfyUI/requirements.txt

# 9 node = đúng COMFY_NODES của setup/setup-full.sh:62, và trùng PROFILE=full của
# worker/runpod/Dockerfile.selfhosted:52-64. Hai image ghim CÙNG commit là cố ý: lệch nhau thì
# một bên chạy được mà bên kia không, và không có gì để so.
WORKDIR /opt/mtc-prebuilt/ComfyUI/custom_nodes
RUN set -eux; \
    set -- \
      "https://github.com/kijai/ComfyUI-WanVideoWrapper.git 088128b" \
      "https://github.com/kijai/ComfyUI-KJNodes.git 4d46ac1" \
      "https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git 4ee72c0" \
      "https://github.com/Fannovel16/comfyui_controlnet_aux.git e8b689a" \
      "https://github.com/Fannovel16/ComfyUI-Frame-Interpolation.git 26545cc" \
      "https://github.com/naxci1/ComfyUI-FlashVSR_Stable.git f7f55ba" \
      "https://github.com/city96/ComfyUI-GGUF.git 6ea2651" \
      "https://github.com/Lightricks/ComfyUI-LTXVideo.git 3b9c5cd" \
      "https://github.com/numz/ComfyUI-SeedVR2_VideoUpscaler.git 4490bd1" \
    ; \
    for spec in "$@"; do \
      url="${spec% *}"; sha="${spec#* }"; d="$(basename "$url" .git)"; \
      git clone --filter=blob:none "$url" "$d"; \
      git -C "$d" checkout "$sha"; \
    done

# KHÔNG `|| true`: lỗi pip PHẢI làm vỡ build. Image thiếu deps mà build vẫn xanh còn tệ hơn build đỏ.
# Hai bẫy đã biết, cả hai đã trả giá bằng một vòng chạy (Dockerfile.selfhosted:72-85):
#   - ComfyUI-FlashVSR_Stable (f7f55ba): requirements.txt có dòng
#       flash-attn --no-build-isolation; platform_system!="Windows"
#     `--no-build-isolation` là option của LỆNH pip, không hợp lệ TRONG file requirements → pip
#     parse lỗi cả file trước khi cài bất cứ gì. Bỏ qua thẳng file của node này, cài tường minh dưới.
#   - ComfyUI-Frame-Interpolation (26545cc): KHÔNG có requirements.txt (chỉ requirements-no-cupy.txt
#     và requirements-with-cupy.txt) → vòng lặp chỉ tìm "requirements.txt" bỏ qua ÂM THẦM, node RIFE
#     VFI (dùng bởi job type "enhance") thiếu deps. Cài bản no-cupy — RIFE không cần cupy.
RUN set -eux; \
    for d in */ ; do \
      d="${d%/}"; \
      [ "$d" = "ComfyUI-FlashVSR_Stable" ] && continue; \
      if [ -f "$d/requirements.txt" ]; then \
        /opt/mtc-prebuilt/ComfyUI/venv/bin/pip install --no-cache-dir -r "$d/requirements.txt"; fi; \
      if [ -f "$d/requirements-no-cupy.txt" ]; then \
        /opt/mtc-prebuilt/ComfyUI/venv/bin/pip install --no-cache-dir -r "$d/requirements-no-cupy.txt"; fi; \
    done

# Danh sách runtime tường minh của FlashVSR (bỏ qua requirements.txt hỏng của chính nó ở trên).
# KHÔNG cần flash-attn/triton — node fallback được, xem setup/lib-feature.sh:594-606.
RUN /opt/mtc-prebuilt/ComfyUI/venv/bin/pip install --no-cache-dir \
      einops safetensors tqdm pillow huggingface_hub psutil "opencv-python>=4.8.1.78" pyyaml

# SageAttention = đường attention chính cho Wan (worker đặt MOTION_ATTENTION=sageattn).
# Assert kiểm CẢ bản CUDA, không chỉ số phiên bản: ComfyUI-SeedVR2_VideoUpscaler có torch/torchvision
# KHÔNG ghim trong requirements — pip đổi wheel cu130 sang wheel CPU thì số version vẫn đúng còn
# CUDA thì mất. torch.version.cuda đọc được lúc build (thuộc tính của wheel), không cần GPU.
# Đây là cổng chặn quan trọng nhất của cả image: MTC_PREBUILT bỏ bước cài torch khớp driver.
RUN /opt/mtc-prebuilt/ComfyUI/venv/bin/pip install --no-cache-dir sageattention \
 && /opt/mtc-prebuilt/ComfyUI/venv/bin/python -c "import torch, sageattention; \
assert torch.__version__.split('+')[0]=='2.12.1', torch.__version__; \
assert (torch.version.cuda or '').startswith('13'), f'wheel torch không phải CUDA 13: cuda={torch.version.cuda} ver={torch.__version__}'"

# Weight DWPose 337MB nằm TRONG thư mục node, KHÔNG nằm trong models/ nên KHÔNG có trên Network
# Volume. Không bake thì mỗi pod mới tải lại 337MB.
RUN mkdir -p /opt/mtc-prebuilt/ComfyUI/custom_nodes/comfyui_controlnet_aux/ckpts/hr16/yolox-onnx \
             /opt/mtc-prebuilt/ComfyUI/custom_nodes/comfyui_controlnet_aux/ckpts/hr16/DWPose-TorchScript-BatchSize5 \
 && wget -q -O /opt/mtc-prebuilt/ComfyUI/custom_nodes/comfyui_controlnet_aux/ckpts/hr16/yolox-onnx/yolox_l.torchscript.pt \
      https://huggingface.co/hr16/yolox-onnx/resolve/main/yolox_l.torchscript.pt \
 && wget -q -O /opt/mtc-prebuilt/ComfyUI/custom_nodes/comfyui_controlnet_aux/ckpts/hr16/DWPose-TorchScript-BatchSize5/dw-ll_ucoco_384_bs5.torchscript.pt \
      https://huggingface.co/hr16/DWPose-TorchScript-BatchSize5/resolve/main/dw-ll_ucoco_384_bs5.torchscript.pt

# Postgres/MinIO/Node chạy NATIVE trên pod (apt + PM2), KHÔNG qua docker compose —
# xem docs/gpu-pod.md#kiến-trúc-thật-trên-pod.
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends \
      gnupg postgresql postgresql-contrib \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - >/dev/null \
    && apt-get install -y -qq --no-install-recommends nodejs \
    && npm install -g pm2 \
    && curl -fsSL -o /usr/local/bin/minio https://dl.min.io/server/minio/release/linux-amd64/minio \
    && chmod +x /usr/local/bin/minio \
    && rm -rf /var/lib/apt/lists/* /root/.cache

# GHIM PHIÊN BẢN OLLAMA LÀ BẮT BUỘC, không phải cẩn thận thừa.
# Bản cũ tải ollama-linux-amd64.tgz — Ollama ĐÃ BỎ định dạng .tgz, nay chỉ phát hành .tar.zst
# (kiểm 26/07/2026: .tgz trả 404). `curl -f` gặp 404 thoát khác 0 → vỡ RUN đầu tiên → build CHẾT,
# không bao giờ ra image. ĐÂY CHÍNH LÀ LÝ DO GHCR LUÔN TRỐNG trước spec này.
# Ollama cần cho profile full: setup/setup-full.sh:56 NEED_OLLAMA=1 (dịch VN→EN cho create-image,
# dịch phụ đề cho subtitle, tryon Auto).
ARG OLLAMA_VERSION=v0.32.4
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends zstd \
    && curl -fsSL -o /tmp/ollama.tar.zst \
         "https://github.com/ollama/ollama/releases/download/${OLLAMA_VERSION}/ollama-linux-amd64.tar.zst" \
    && tar --use-compress-program=unzstd -xf /tmp/ollama.tar.zst -C /usr \
    && rm -f /tmp/ollama.tar.zst \
    && test -x /usr/bin/ollama \
    && rm -rf /var/lib/apt/lists/* /root/.cache

# CHẶN Ở BUILD: layer npm hỏng/cache dở từng cho ra api-node_modules thiếu dependency bắc cầu
# (pg có, pg-types không). Runtime chỉ lộ ra bằng "Cannot find module" lúc worker crash-loop —
# rất tốn công truy. Nạp thử vài package chính, thiếu là fail build luôn.
# (setup/lib-feature.sh:518-533 cũng tự kiểm và cài lại ở runtime; đây là lớp chặn sớm hơn.)
WORKDIR /opt/mtc-build/api
COPY api/package.json ./package.json
RUN npm install --omit=dev --no-audit --no-fund \
    && node -e "require('pg');require('pg-types');require('express')" \
    && mv node_modules /opt/mtc-prebuilt/api-node_modules

# venv worker — setup/lib-feature.sh:536 symlink worker/venv sang đây.
WORKDIR /opt/mtc-build/worker
COPY worker/requirements.txt ./requirements.txt
# CHẶN Ở BUILD: cùng lý do với api-node_modules ở trên — layer pip hỏng thiếu dependency bắc cầu
# thì không gì bắt được cho tới lúc worker crash-loop trên pod thật. Nạp thử vài module chính.
# Tên MODULE khác tên PACKAGE: websocket-client → websocket, Pillow → PIL, faster-whisper →
# faster_whisper (kiểm chéo với worker/requirements.txt trước khi đổi danh sách này).
RUN python3 -m venv /opt/mtc-prebuilt/worker-venv \
    && /opt/mtc-prebuilt/worker-venv/bin/pip install --no-cache-dir -U pip wheel \
    && /opt/mtc-prebuilt/worker-venv/bin/pip install --no-cache-dir -r requirements.txt \
    && /opt/mtc-prebuilt/worker-venv/bin/python -c "import requests, websocket, PIL, faster_whisper"

# venv bg-remover — MỚI so với bản Dockerfile cũ. setup/setup-full.sh:57 đặt NEED_BG_REMOVER=1,
# và ensure_bg_remover (lib-feature.sh:470-489, gọi từ phase_prebuilt_deps:540) dựng venv này ở
# RUNTIME mỗi lần boot — comment của nó tự thú "chậm hơn fast-boot vài phút". Với profile
# motion-transfer thì NEED_BG_REMOVER=0 nên không ai để ý; với full nó ăn đúng vào cái fast-boot
# đang mua. Bake vào đây thì mất hẳn.
WORKDIR /opt/mtc-build/bg-remover
COPY bg-remover/requirements.txt ./requirements.txt
RUN python3 -m venv /opt/mtc-prebuilt/bg-remover-venv \
    && /opt/mtc-prebuilt/bg-remover-venv/bin/pip install --no-cache-dir -U pip wheel \
    && /opt/mtc-prebuilt/bg-remover-venv/bin/pip install --no-cache-dir -r requirements.txt

RUN mkdir -p /opt/mtc-prebuilt/ComfyUI/models/uploads && rm -rf /opt/mtc-build
WORKDIR /root

# Cổng chặn hợp đồng: marker .ready dưới đây là lời hứa "image đủ ruột" mà lib-feature.sh tin
# tuyệt đối (nó `die` nếu thiếu marker, và KHÔNG kiểm lại gì bên trong). Nếu không bảo chứng lời
# hứa ở đây thì một RUN im lặng thiếu thứ gì vẫn cho ra image có marker — pod dựng xong mới chết,
# trên đồng hồ GPU $1/giờ. Kiểm đúng những gì lib-feature.sh sẽ đòi.
RUN set -eux; \
    test -f /opt/mtc-prebuilt/ComfyUI/main.py; \
    test -x /opt/mtc-prebuilt/ComfyUI/venv/bin/python; \
    test -d /opt/mtc-prebuilt/api-node_modules; \
    test -x /opt/mtc-prebuilt/worker-venv/bin/python; \
    test -x /opt/mtc-prebuilt/bg-remover-venv/bin/python; \
    test -x /usr/bin/ollama; \
    test -x /usr/local/bin/minio; \
    n=$(ls /opt/mtc-prebuilt/ComfyUI/custom_nodes | wc -l); \
    [ "$n" = 9 ] || { echo "cần 9 custom node, đếm được $n" >&2; exit 1; }

# Marker cuối cùng: setup/lib-feature.sh:516 `die` nếu thiếu file này. Đặt ở cuối image để một
# build hỏng giữa chừng KHÔNG bao giờ cho ra image có marker nhưng thiếu ruột.
RUN mkdir -p /opt/mtc-prebuilt && touch /opt/mtc-prebuilt/.ready
