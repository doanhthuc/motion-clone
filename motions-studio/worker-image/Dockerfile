# syntax=docker/dockerfile:1.7
# #region ALD 19/07/2026 - Image dựng sẵn để boot worker GPU trong 3–5 phút.
# Không chứa source private, token hay model khách hàng. Source được clone ở on-start; model được
# tải resumable theo đúng profile. Image này chỉ thêm runtime Task Cloud và custom node.
# CUDA 13.2 + PyTorch cu130 đã hỗ trợ RTX 5090 (sm_120). Ghim tag để build có thể lặp lại.
#
# ALD 26/07/2026 - Image KHÔNG gắn với nhà cung cấp nào: bootstrap của Task Cloud chỉ cần marker
# /opt/mtc-prebuilt/.ready để đi đường fast-boot MTC_PREBUILT=1 (setup/lib-feature.sh:763).
# Hiện đang dùng cho RunPod (tích hợp Vast.ai đã gỡ 19/07).
#
# Base vẫn là image ComfyUI của Vast.ai — thuần tuý vì nó đã dựng sẵn ComfyUI + venv + torch khớp
# CUDA, tiết kiệm ~40 phút build. Nó là image công khai trên Docker Hub, không ràng buộc gì với
# dịch vụ Vast.ai. Muốn bỏ hẳn thì phải tự dựng lại chuỗi CUDA/torch/ComfyUI — việc lớn, chưa cần.
FROM vastai/comfy:v0.27.0-cuda-13.2-py312

USER root
ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MTC_PREBUILT_DIR=/opt/mtc-prebuilt

# #region ALD 26/07/2026 - Ghim phiên bản Ollama.
# Bản cũ tải https://ollama.com/download/ollama-linux-amd64.tgz — Ollama ĐÃ BỎ định dạng .tgz,
# nay chỉ phát hành .tar.zst (kiểm 26/07: .tgz trả 404). curl -f gặp 404 là thoát khác 0 → cả RUN
# này hỏng → build CHẾT NGAY Ở BƯỚC ĐẦU, không bao giờ ra image. Đây là lý do GHCR luôn trống.
# Ghim tag thay vì latest để build lặp lại được và không vỡ lần nữa khi upstream đổi tiếp.
ARG OLLAMA_VERSION=v0.32.4
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends \
      ca-certificates curl gnupg git jq aria2 zstd \
      postgresql postgresql-contrib \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - >/dev/null \
    && apt-get install -y -qq --no-install-recommends nodejs \
    && npm install -g pm2 \
    && curl -fsSL -o /usr/local/bin/minio https://dl.min.io/server/minio/release/linux-amd64/minio \
    && chmod +x /usr/local/bin/minio \
    && curl -fsSL -o /tmp/ollama.tar.zst \
         "https://github.com/ollama/ollama/releases/download/${OLLAMA_VERSION}/ollama-linux-amd64.tar.zst" \
    && tar --use-compress-program=unzstd -xf /tmp/ollama.tar.zst -C /usr \
    && rm -f /tmp/ollama.tar.zst \
    && ollama --version >/dev/null 2>&1 || test -x /usr/bin/ollama \
    && rm -rf /var/lib/apt/lists/* /root/.cache
# #endregion

# Base image đã dựng sẵn ComfyUI tại /opt/workspace-internal/ComfyUI và venv tại /venv/main.
# Giữ đường dẫn /opt/mtc-prebuilt ổn định để bootstrap không phụ thuộc cấu trúc image upstream.
RUN test -f /opt/workspace-internal/ComfyUI/main.py \
    && test -x /venv/main/bin/python \
    && mkdir -p /opt/mtc-prebuilt \
    && ln -s /opt/workspace-internal/ComfyUI /opt/mtc-prebuilt/ComfyUI \
    && ln -s /venv/main /opt/workspace-internal/ComfyUI/venv

# Một image dùng chung cho cả Motion và Try-On để autoscaler có thể đổi loại job mà không dựng máy lại.
WORKDIR /opt/mtc-prebuilt/ComfyUI/custom_nodes
RUN git clone --depth 1 https://github.com/kijai/ComfyUI-WanVideoWrapper \
    && git clone --depth 1 https://github.com/kijai/ComfyUI-KJNodes \
    && git clone --depth 1 https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite \
    && git clone --depth 1 https://github.com/Fannovel16/comfyui_controlnet_aux \
    && git clone --depth 1 https://github.com/Fannovel16/ComfyUI-Frame-Interpolation \
    && git clone --depth 1 https://github.com/city96/ComfyUI-GGUF \
    `# ALD 26/07/2026 - FlashVSR: enhance video sau motion (catalog-motion-transfer.json có 4 file FlashVSR v1.1).` \
    `# Thiếu node này thì job type 'enhance' của box motion fail ngay ở /prompt.` \
    && git clone --depth 1 https://github.com/naxci1/ComfyUI-FlashVSR_Stable \
    && for d in */; do \
         [ ! -f "$d/requirements.txt" ] || /opt/mtc-prebuilt/ComfyUI/venv/bin/pip install --no-cache-dir -r "$d/requirements.txt"; \
         [ ! -f "$d/requirements-no-cupy.txt" ] || /opt/mtc-prebuilt/ComfyUI/venv/bin/pip install --no-cache-dir -r "$d/requirements-no-cupy.txt"; \
       done \
    && /opt/mtc-prebuilt/ComfyUI/venv/bin/pip install --no-cache-dir -U \
         "transformers>=4.50,<4.57" "diffusers>=0.31" \
    && /opt/mtc-prebuilt/ComfyUI/venv/bin/python -c \
         'import torch, sageattention; assert torch.backends.cuda.is_built()' \
    && rm -rf /root/.cache

WORKDIR /opt/mtc-build/api
COPY api/package.json ./package.json
# ALD 27/07/2026 - CHẶN ở build: layer npm hỏng/cache dở từng cho ra api-node_modules thiếu
# dependency bắc cầu (pg có, pg-types không). Runtime chỉ lộ ra bằng "Cannot find module" lúc
# worker crash-loop — rất tốn công truy. Nạp thử vài package chính, thiếu là fail build luôn.
RUN npm install --omit=dev --no-audit --no-fund \
    && node -e "require('pg');require('pg-types');require('express')" \
    && mv node_modules /opt/mtc-prebuilt/api-node_modules

WORKDIR /opt/mtc-build/worker
COPY worker/requirements.txt ./requirements.txt
RUN python3 -m venv /opt/mtc-prebuilt/worker-venv \
    && /opt/mtc-prebuilt/worker-venv/bin/pip install --no-cache-dir -U pip wheel \
    && /opt/mtc-prebuilt/worker-venv/bin/pip install --no-cache-dir -r requirements.txt

RUN mkdir -p /opt/mtc-prebuilt/ComfyUI/models/uploads \
    && touch /opt/mtc-prebuilt/.ready \
    && rm -rf /opt/mtc-build

WORKDIR /root
# Tiến trình chính do nhà cung cấp quản lý (RunPod: dockerStartCmd). Bootstrap của Task Cloud
# sẽ cấu hình và bật PM2, nên CMD mặc định chỉ cần giữ container sống.
#
# ⚠ ALD 27/07/2026 - CMD Ở ĐÂY (và dockerStartCmd của RunPod) KHÔNG BAO GIỜ ĐƯỢC CHẠY.
# Base vastai/comfy có ENTRYPOINT ["/opt/instance-tools/bin/entrypoint.sh"] (kiểm bằng config blob
# trên Docker Hub) và entrypoint đó `exec boot_default.sh "$@"` rồi bung supervisord — tham số CMD bị
# nuốt. Triệu chứng 27/07: pod lên, /workspace TRỐNG, không có cả /workspace/logs → phải SSH gõ tay.
# Vì vậy Task Cloud chạy bootstrap qua env PROVISIONING_SCRIPT (cơ chế sẵn có của base image), xem
# buildPodEnv() trong motion-task-cloud/server/utils/workerBootstrap.js.
# Muốn CMD/dockerStartCmd có tác dụng trở lại thì phải thêm `ENTRYPOINT []` ở đây — nhưng như vậy là
# mất luôn phần init của base (venv /venv/main, ssh, supervisord), nên KHÔNG làm.
CMD ["sleep", "infinity"]
# #endregion
