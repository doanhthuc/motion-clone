# MTC_PREBUILT trên RunPod — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dựng image prebuilt trên GHCR để `MTC_PREBUILT=1` xoá ~20-35 phút cài phần mềm khỏi mỗi lần dựng pod GPU mới.

**Architecture:** Viết lại `motions-studio/worker-image/Dockerfile` trên base `runpod/pytorch`, bê nguyên chuỗi ComfyUI/torch/custom-node đã chứng minh trong CI từ `worker/runpod/Dockerfile.selfhosted`, cộng lớp chỉ pod cần (Postgres, MinIO, Node+pm2, Ollama, ba venv). CI ở gốc repo build và đẩy lên GHCR. Runtime không phải sửa gì — đường dây `MTC_PREBUILT` đã nối sẵn từ trước.

**Tech Stack:** Docker · GitHub Actions · GHCR · runpodctl · ComfyUI · PyTorch cu130

**Spec:** [`docs/superpowers/specs/2026-08-05-mtc-prebuilt-runpod-design.md`](../specs/2026-08-05-mtc-prebuilt-runpod-design.md)

## Global Constraints

Mọi task ngầm định bao gồm phần này. Giá trị chép nguyên từ spec:

- **Base image:** `runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404` — ship `/start.sh` (sshd + block), thoả bẫy RunPod §1
- **torch:** `2.12.1` + CUDA `13` (index-url `https://download.pytorch.org/whl/cu130`) — cài đè torch 2.8/cu128 của base
- **ComfyUI lõi ghim:** `322122449c9d2ba8b8df1bb517364527dd0615f1`
- **9 custom node ghim SHA** (bản `full`): `ComfyUI-WanVideoWrapper 088128b` · `ComfyUI-KJNodes 4d46ac1` · `ComfyUI-VideoHelperSuite 4ee72c0` · `comfyui_controlnet_aux e8b689a` · `ComfyUI-Frame-Interpolation 26545cc` · `ComfyUI-FlashVSR_Stable f7f55ba` · `ComfyUI-GGUF 6ea2651` · `ComfyUI-LTXVideo 3b9c5cd` · `ComfyUI-SeedVR2_VideoUpscaler 4490bd1`
- **Ollama ghim:** `v0.32.4` — không ghim là vỡ build, đây là lý do GHCR luôn trống
- **Thư mục prebuilt:** `/opt/mtc-prebuilt`, marker `/opt/mtc-prebuilt/.ready`
- **Hợp đồng bắt buộc** (`setup/lib-feature.sh:531-532` **`die`** nếu thiếu): `/opt/mtc-prebuilt/ComfyUI/main.py` và `/opt/mtc-prebuilt/ComfyUI/venv/bin/python`
- **Image GHCR:** `ghcr.io/<owner>/motion-prebuilt` — `<owner>` = `${{ github.repository_owner }}`, không gõ cứng
- **Workflow PHẢI ở gốc repo** `.github/workflows/` — file trong `motions-studio/.github/` là file chết
- **`POD_IMAGE` ghim tag `sha-<commit>`**, không dùng `latest`
- **`JOB_TYPE` 16 type:** `motion,teen-flycam,trend-tiktok,enhance,create-image,edit-image,tryon,talk,face-motion,story-film,product-overlay,concat,reveal,voiceover,subtitle,teaser`
- **Volume `wfe86wzkpm`** (EU-RO-1, 100 GB) — giữ nguyên dung lượng, `VOLUME_GB=100` khi chạy preload
- **Tiền:** đọc `runpodctl billing`, **không** `currentSpendPerHr` (trễ ~45 giây, đã một lần lệch 33×)

## File Structure

| File | Trách nhiệm |
|---|---|
| `.github/workflows/build-prebuilt-image.yml` | **Tạo mới.** Build + đẩy image lên GHCR |
| `motions-studio/worker-image/Dockerfile` | **Viết lại.** Toàn bộ nội dung image |
| `.env` | **Sửa.** `MTC_PREBUILT`, `SETUP_PROFILE`, `POD_IMAGE`, `DISPATCH_JOB_TYPES` |
| `.env.example` | **Sửa.** Cùng bộ, kèm giải thích |
| `docs/gpu-pod.md` | **Sửa.** Ghi số đo thật vào §Network Volume và §Image dựng sẵn |
| `docs/superpowers/specs/2026-08-05-mtc-prebuilt-runpod-design.md` | **Sửa.** Cập nhật trạng thái sau khi verify |

---

### Task 1: CI workflow + Dockerfile khung tối thiểu

Mục đích: chứng minh **đường ống** chạy (syntax workflow, login GHCR, tạo package, chiến lược tag, quyền pull) bằng một image nhỏ build trong ~2 phút, **trước** khi đổ 40 phút build vào nó. Đây là "rẻ trước, đắt sau" áp cho chính CI.

**Files:**
- Create: `.github/workflows/build-prebuilt-image.yml`
- Modify: `motions-studio/worker-image/Dockerfile` (thay toàn bộ nội dung)

**Interfaces:**
- Consumes: không có (task đầu)
- Produces: image `ghcr.io/<owner>/motion-prebuilt:sha-<commit>` chứa `/opt/mtc-prebuilt/.ready`. Task 2 và 3 mở rộng đúng file Dockerfile này. Task 4 dùng tên image này cho `POD_IMAGE`.

- [ ] **Step 1: Tạo nhánh làm việc**

```bash
cd /Users/thucpham/Desktop/motion-clone
git checkout -b mtc-prebuilt-runpod
```

- [ ] **Step 2: Thay toàn bộ `motions-studio/worker-image/Dockerfile` bằng khung tối thiểu**

```dockerfile
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

# Marker cuối cùng: setup/lib-feature.sh:504 `die` nếu thiếu file này. Đặt ở cuối image để một
# build hỏng giữa chừng KHÔNG bao giờ cho ra image có marker nhưng thiếu ruột.
RUN mkdir -p /opt/mtc-prebuilt && touch /opt/mtc-prebuilt/.ready
```

- [ ] **Step 3: Tạo `.github/workflows/build-prebuilt-image.yml`**

```yaml
# .github/workflows/build-prebuilt-image.yml
# PHẢI nằm ở GỐC repo. GitHub Actions chỉ đọc .github/workflows/ ở gốc — workflow trong
# motions-studio/.github/ là file chết, chưa từng chạy lần nào.
name: Build prebuilt pod image

on:
  workflow_dispatch:
  push:
    # mtc-prebuilt-runpod: nhánh phát triển của spec này. `gh` trên máy dev đăng nhập bằng tài
    # khoản chỉ có quyền READ nên KHÔNG chạy `gh workflow run` được; đẩy nhánh là cách kích hoạt
    # build. XOÁ dòng nhánh này ở Task 7 trước khi merge — để lại thì mọi push nhánh đều đốt
    # ~40 phút CI.
    branches: [main, mtc-prebuilt-runpod]
    paths:
      # Image bake dependency của api, worker và bg-remover — ba file requirements đó đổi là
      # image lệch code. Theo dõi cả ba, không chỉ Dockerfile.
      - motions-studio/worker-image/Dockerfile
      - motions-studio/api/package.json
      - motions-studio/worker/requirements.txt
      - motions-studio/bg-remover/requirements.txt
      - .github/workflows/build-prebuilt-image.yml

permissions:
  contents: read
  packages: write

concurrency:
  group: prebuilt-image-${{ github.ref }}
  cancel-in-progress: true

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      # Runner tiêu chuẩn chỉ còn ~14GB trống; image này vài chục GB sau khi giải nén.
      - name: Dọn disk
        run: |
          sudo rm -rf /usr/share/dotnet /usr/local/lib/android /opt/ghc /opt/hostedtoolcache
          df -h /

      - uses: docker/setup-buildx-action@v3

      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - uses: docker/metadata-action@v5
        id: meta
        with:
          # repository_owner, không gõ cứng: ai fork cũng build vào registry của mình.
          images: ghcr.io/${{ github.repository_owner }}/motion-prebuilt
          tags: |
            # enable= guard: build thử gần như chắc chắn chạy bằng workflow_dispatch trên một
            # nhánh, không phải push vào main. Không có guard này thì lần chạy thử ĐÓ ghi đè
            # `latest` bằng image dựng từ code chưa merge.
            type=raw,value=latest,enable=${{ github.ref == 'refs/heads/main' }}
            type=sha,prefix=sha-

      - uses: docker/build-push-action@v6
        id: build
        with:
          context: motions-studio
          file: motions-studio/worker-image/Dockerfile
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}

      # Dung lượng image là RỦI RO LỚN NHẤT của cả thiết kế: pull chậm ăn hết phần tiết kiệm.
      # In ra ngay trong log build thay vì bắt người ta vào GHCR bấm tìm.
      - name: In dung lượng image
        run: |
          docker buildx imagetools inspect \
            "ghcr.io/${{ github.repository_owner }}/motion-prebuilt:sha-${GITHUB_SHA::7}" \
            --raw | python3 -c 'import json,sys; m=json.load(sys.stdin); \
            print("TỔNG NÉN:", round(sum(l["size"] for l in m.get("layers",[]))/2**30, 2), "GB")'
```

- [ ] **Step 4: Commit và đẩy nhánh**

```bash
git add .github/workflows/build-prebuilt-image.yml motions-studio/worker-image/Dockerfile
git commit -m "Khung image prebuilt + CI: chứng minh đường ống GHCR trước khi đổ 40 phút build vào"
git push -u origin mtc-prebuilt-runpod
```

- [ ] **Step 5: Chờ build (đã tự chạy do push ở Step 4) và xác nhận nó XANH**

`gh` trên máy này chỉ có quyền READ nên **không** dùng `gh workflow run`; push ở Step 4 đã kích hoạt build. Đọc kết quả thì quyền READ là đủ:

```bash
sleep 20
RID=$(gh run list --repo doanhthuc/motion-clone --workflow="Build prebuilt pod image" --limit 1 --json databaseId -q '.[0].databaseId')
gh run watch "$RID" --repo doanhthuc/motion-clone
gh run view "$RID" --repo doanhthuc/motion-clone --log | grep -A3 "TỔNG NÉN"
```

Mong đợi: kết luận `success`. Bước "In dung lượng image" in ra khoảng **2-4 GB** (base runpod/pytorch, chưa có gì thêm).

- [ ] **Step 6: Xác nhận image pull được mà KHÔNG cần đăng nhập**

Package GHCR mới mặc định **private**; RunPod không có credential nên sẽ không pull được.

```bash
docker logout ghcr.io
docker manifest inspect ghcr.io/<owner>/motion-prebuilt:sha-<7-ký-tự-đầu> >/dev/null && echo "PULL ĐƯỢC (public)"
```

Nếu lỗi `denied`/`unauthorized`: vào GitHub → Packages → `motion-prebuilt` → Package settings → **Change visibility → Public**, rồi chạy lại lệnh trên tới khi in `PULL ĐƯỢC (public)`.

**Cổng chặn của task này:** không sang Task 2 khi chưa in được `PULL ĐƯỢC (public)`. Phát hiện vấn đề quyền ở đây tốn 2 phút; phát hiện ở Task 6 tốn một phiên thuê pod GPU.

---

### Task 2: Lớp ComfyUI + torch + 9 custom node

Bê nguyên chuỗi đã chạy thật trong CI và production từ `worker/runpod/Dockerfile.selfhosted`. Khác một điểm: cài vào **venv riêng** tại `/opt/mtc-prebuilt/ComfyUI/venv` thay vì system python — vừa thoả hợp đồng `lib-feature.sh:532`, vừa tránh hẳn PEP 668.

**Files:**
- Modify: `motions-studio/worker-image/Dockerfile`

**Interfaces:**
- Consumes: `MTC_PREBUILT_DIR=/opt/mtc-prebuilt` và base image từ Task 1
- Produces: `/opt/mtc-prebuilt/ComfyUI/main.py`, `/opt/mtc-prebuilt/ComfyUI/venv/bin/python` (torch 2.12.1+cu130, sageattention), 9 node trong `custom_nodes/`. Task 3 dùng cùng venv path cho không gì cả — nó dựng venv riêng cho worker và bg-remover.

- [ ] **Step 1: Chèn khối ComfyUI + torch vào Dockerfile, TRƯỚC dòng `RUN mkdir -p /opt/mtc-prebuilt && touch`**

```dockerfile
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

# venv RIÊNG, không dùng python hệ thống: setup/lib-feature.sh:532 `die` nếu thiếu
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
```

- [ ] **Step 2: Chèn khối 9 custom node ngay sau đó**

```dockerfile
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
```

- [ ] **Step 3: Commit và đẩy**

```bash
git add motions-studio/worker-image/Dockerfile
git commit -m "Lớp ComfyUI + torch cu130 + 9 node, bê chuỗi đã chứng minh từ Dockerfile.selfhosted"
git push
```

- [ ] **Step 4: Chờ build (push ở Step 3 đã kích hoạt) và xác nhận assert torch XANH**

```bash
sleep 20
RID=$(gh run list --repo doanhthuc/motion-clone --workflow="Build prebuilt pod image" --limit 1 --json databaseId -q '.[0].databaseId')
gh run watch "$RID" --repo doanhthuc/motion-clone
gh run view "$RID" --repo doanhthuc/motion-clone --log | grep -A3 "TỔNG NÉN"
```

Mong đợi: `success`, và trong log có dòng của assert chạy qua (không có `AssertionError`). Dung lượng in ra tăng lên khoảng **15-25 GB**.

Nếu assert đỏ với `wheel torch không phải CUDA 13`: một node đã kéo torch CPU đè lên. Sửa bằng cách chuyển khối `pip install sageattention` + assert lên **trước** khối cài requirements của node, rồi thêm một assert thứ hai ở cuối để bắt chính trường hợp đó.

- [ ] **Step 5: Ghi lại dung lượng image vào ghi chú tạm**

```bash
echo "Task2 image nén: <số> GB" >> /tmp/mtc-prebuilt-do.txt
```

Số này dùng ở Task 7. Nếu đã vượt **40 GB** ở bước này, dừng lại và báo — `DISK=100` còn phải chứa OS và PGDATA.

---

### Task 3: Lớp chỉ pod cần + marker `.ready`

Postgres, MinIO, Node+pm2, Ollama, và ba venv mà `phase_prebuilt_deps` sẽ symlink vào.

**Files:**
- Modify: `motions-studio/worker-image/Dockerfile`

**Interfaces:**
- Consumes: `/opt/mtc-prebuilt/ComfyUI/venv` từ Task 2
- Produces: `/opt/mtc-prebuilt/api-node_modules`, `/opt/mtc-prebuilt/worker-venv`, `/opt/mtc-prebuilt/bg-remover-venv`, `/opt/mtc-prebuilt/.ready`. Task 4 bật `MTC_PREBUILT=1` dựa trên marker này.

- [ ] **Step 1: Chèn khối apt + Ollama TRƯỚC dòng marker `.ready`**

```dockerfile
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
```

- [ ] **Step 2: Chèn khối ba venv + node_modules, vẫn TRƯỚC dòng marker**

```dockerfile
# CHẶN Ở BUILD: layer npm hỏng/cache dở từng cho ra api-node_modules thiếu dependency bắc cầu
# (pg có, pg-types không). Runtime chỉ lộ ra bằng "Cannot find module" lúc worker crash-loop —
# rất tốn công truy. Nạp thử vài package chính, thiếu là fail build luôn.
# (setup/lib-feature.sh:513-522 cũng tự kiểm và cài lại ở runtime; đây là lớp chặn sớm hơn.)
WORKDIR /opt/mtc-build/api
COPY api/package.json ./package.json
RUN npm install --omit=dev --no-audit --no-fund \
    && node -e "require('pg');require('pg-types');require('express')" \
    && mv node_modules /opt/mtc-prebuilt/api-node_modules

# venv worker — setup/lib-feature.sh:488 symlink worker/venv sang đây.
WORKDIR /opt/mtc-build/worker
COPY worker/requirements.txt ./requirements.txt
RUN python3 -m venv /opt/mtc-prebuilt/worker-venv \
    && /opt/mtc-prebuilt/worker-venv/bin/pip install --no-cache-dir -U pip wheel \
    && /opt/mtc-prebuilt/worker-venv/bin/pip install --no-cache-dir -r requirements.txt

# venv bg-remover — MỚI so với bản Dockerfile cũ. setup/setup-full.sh:57 đặt NEED_BG_REMOVER=1,
# và ensure_bg_remover (lib-feature.sh:470-477, gọi từ phase_prebuilt_deps:528) dựng venv này ở
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
```

- [ ] **Step 3: Sửa `setup/lib-feature.sh` để dùng venv bg-remover dựng sẵn**

`ensure_bg_remover` hiện luôn dựng venv tại chỗ. Thêm nhánh symlink khi image có sẵn — nếu không, venv bake vào image không ai dùng.

Sửa `motions-studio/setup/lib-feature.sh`, hàm `ensure_bg_remover` (dòng 470-477), chèn ngay sau dòng `[ "${NEED_BG_REMOVER:-0}" = "1" ] || return 0`:

```sh
  # ALD 05/08/2026 - Image dựng sẵn có venv bg-remover → symlink thay vì pip lại vài phút mỗi boot.
  # Kiểm chạy được chứ không tin nó có mặt là đủ, cùng cách phase_prebuilt_deps kiểm api-node_modules.
  local _pre="${MTC_PREBUILT_DIR:-/opt/mtc-prebuilt}/bg-remover-venv"
  if [ "${MTC_PREBUILT:-0}" = "1" ] && [ -x "$_pre/bin/python" ]; then
    if "$_pre/bin/python" -c "import rembg" >/dev/null 2>&1; then
      rm -rf "$ROOT/bg-remover/venv"
      ln -s "$_pre" "$ROOT/bg-remover/venv"
      ok "bg-remover: dùng venv dựng sẵn từ image"
      return 0
    fi
    warn "venv bg-remover dựng sẵn không import được rembg → dựng lại tại chỗ."
  fi
```

- [ ] **Step 4: Xác nhận marker `.ready` vẫn là lệnh CUỐI CÙNG trong Dockerfile**

```bash
tail -5 motions-studio/worker-image/Dockerfile
```

Mong đợi: dòng `RUN mkdir -p /opt/mtc-prebuilt && touch /opt/mtc-prebuilt/.ready` nằm cuối. Nếu đã bị đẩy lên trên, di chuyển xuống cuối. Lý do: build hỏng giữa chừng không bao giờ được cho ra image có marker nhưng thiếu ruột — `lib-feature.sh:504` tin vào marker này.

- [ ] **Step 5: Commit và đẩy**

```bash
git add motions-studio/worker-image/Dockerfile motions-studio/setup/lib-feature.sh
git commit -m "Lớp pod: Postgres/MinIO/Node/pm2/Ollama + ba venv, và symlink bg-remover dựng sẵn"
git push
```

- [ ] **Step 6: Chờ build (push ở Step 5 đã kích hoạt), xác nhận xanh, ghi dung lượng**

```bash
sleep 20
RID=$(gh run list --repo doanhthuc/motion-clone --workflow="Build prebuilt pod image" --limit 1 --json databaseId -q '.[0].databaseId')
gh run watch "$RID" --repo doanhthuc/motion-clone
gh run view "$RID" --repo doanhthuc/motion-clone --log | grep -A3 "TỔNG NÉN"
echo "Task3 image nén: <số> GB" >> /tmp/mtc-prebuilt-do.txt
```

Mong đợi: `success`. **Cổng chặn:** dung lượng nén vượt **45 GB** thì dừng và báo trước khi thuê pod — `DISK=100` phải chứa image giải nén + OS + PGDATA.

- [ ] **Step 7: Kiểm hợp đồng với `lib-feature.sh` bằng cách chạy thử image trên máy**

```bash
IMG="ghcr.io/<owner>/motion-prebuilt:sha-<7-ký-tự-đầu>"
docker run --rm --platform linux/amd64 "$IMG" sh -c '
  set -e
  test -f /opt/mtc-prebuilt/.ready                  && echo "OK .ready"
  test -f /opt/mtc-prebuilt/ComfyUI/main.py         && echo "OK ComfyUI/main.py"
  test -x /opt/mtc-prebuilt/ComfyUI/venv/bin/python && echo "OK ComfyUI/venv"
  test -d /opt/mtc-prebuilt/api-node_modules        && echo "OK api-node_modules"
  test -x /opt/mtc-prebuilt/worker-venv/bin/python  && echo "OK worker-venv"
  test -x /opt/mtc-prebuilt/bg-remover-venv/bin/python && echo "OK bg-remover-venv"
  test -x /usr/bin/ollama && test -x /usr/local/bin/minio && echo "OK ollama + minio"
  ls /opt/mtc-prebuilt/ComfyUI/custom_nodes | wc -l
'
```

Mong đợi: sáu dòng `OK`, và số cuối là **9** (đủ 9 node). Đây kiểm đúng những gì `lib-feature.sh:504,531,532` sẽ `die` nếu thiếu — bắt ở đây rẻ hơn bắt trên pod GPU.

---

### Task 4: Đổi `.env` sang profile `full`, bật `MTC_PREBUILT`, và khoá `JOB_TYPES` về 16 type

**Files:**
- Modify: `.env` (dòng 51, 117, 135; thêm `POD_IMAGE`, `JOB_TYPES_OVERRIDE`)
- Modify: `.env.example` (dòng 39, 114, 152, 185)
- Modify: `scripts/pod-bootstrap.sh` (khối env truyền vào `$SETUP_SCRIPT`, ~dòng 155-161)
- Modify: `motions-studio/setup/lib-feature.sh` (`phase_dotenv`, ~dòng 396-397)

**Interfaces:**
- Consumes: tên image từ Task 1 và marker đã kiểm ở Task 3
- Produces: `.env` mà `pod-provision.sh` và `pod-bootstrap.sh` đọc ở Task 6; biến `JOB_TYPES_OVERRIDE` đi từ `.env` gốc → `pod-bootstrap.sh` → `lib-feature.sh:phase_dotenv` → `JOB_TYPES` trong `.env` trên pod

> **Vì sao cần đường ghi đè này:** `lib-feature.sh:397` ghi `set_kv JOB_TYPES "$JOB_TYPE"`, mà
> `JOB_TYPE` là hằng cứng trong profile — `setup-full.sh:49` khai đủ **21 type**. `DISPATCH_JOB_TYPES`
> **không** thay được: nó chỉ điều khiển dispatcher serverless, mà `WORKER_SOURCE=local`. Không có
> đường ghi đè thì worker claim cả 5 type không có model rồi set `error` sau khi đã nhận job.
>
> **Không sửa thẳng `setup-full.sh:49`.** Profile khai *phần mềm chạy được gì*; model thiếu là sự
> thật *của volume này*, không phải của profile. Trộn hai thứ vào một chỗ thì volume sau tải đủ
> model vẫn bị khoá ở 16 type mà không ai nhớ vì sao.

- [ ] **Step 1: Cho `pod-bootstrap.sh` đọc và truyền `JOB_TYPES_OVERRIDE`**

Trong `scripts/pod-bootstrap.sh`, thêm cạnh dòng `MTC_PREBUILT="$(env_get MTC_PREBUILT)"` (dòng 55):

```sh
# ALD 05/08/2026 - Khoá JOB_TYPES hẹp hơn profile khai. Profile nói phần mềm chạy được gì;
# biến này nói VOLUME NÀY có model cho gì. Bỏ trống = dùng nguyên JOB_TYPE của profile.
JOB_TYPES_OVERRIDE="$(env_get JOB_TYPES_OVERRIDE)"
```

Và trong khối `ssh ... ./$SETUP_SCRIPT` (dòng ~155-161), thêm một dòng vào danh sách env — dùng dạng `${VAR:+...}` để khi bỏ trống thì **không** truyền gì cả, không phải truyền chuỗi rỗng:

```sh
${JOB_TYPES_OVERRIDE:+JOB_TYPES_OVERRIDE='$JOB_TYPES_OVERRIDE'} \
```

- [ ] **Step 2: Cho `lib-feature.sh` áp dụng ghi đè**

Trong `motions-studio/setup/lib-feature.sh`, `phase_dotenv`, thay dòng 397 `set_kv JOB_TYPES "$JOB_TYPE"` bằng:

```sh
  # ALD 05/08/2026 - JOB_TYPES_OVERRIDE hẹp hơn JOB_TYPE của profile: dùng khi volume chưa có
  # model cho hết số type profile khai. Chỉ CẮT BỚT, không thêm — type ngoài profile nghĩa là
  # thiếu custom node, worker sẽ chết ở /prompt chứ không phải chỉ thiếu model.
  _JT="$JOB_TYPE"
  if [ -n "${JOB_TYPES_OVERRIDE:-}" ]; then
    _bad=""
    for _t in $(echo "$JOB_TYPES_OVERRIDE" | tr ',' ' '); do
      case ",$JOB_TYPE," in *",$_t,"*) ;; *) _bad="$_bad $_t" ;; esac
    done
    if [ -n "$_bad" ]; then
      die "JOB_TYPES_OVERRIDE có type ngoài profile $FEATURE:$_bad — profile khai: $JOB_TYPE"
    fi
    _JT="$JOB_TYPES_OVERRIDE"
    warn "JOB_TYPES bị thu hẹp bằng JOB_TYPES_OVERRIDE: $_JT (profile khai $(echo "$JOB_TYPE" | tr ',' '\n' | wc -l | tr -d ' ') type)"
  fi
  set_kv JOB_TYPES "$_JT"
```

Sửa luôn hai dòng log dùng `$JOB_TYPE` để in đúng giá trị thật: dòng 409 `ok ".env sẵn sàng (JOB_TYPES=$JOB_TYPE)"` → `...(JOB_TYPES=$_JT)"`.

- [ ] **Step 3: Sửa `.env`**

```bash
cd /Users/thucpham/Desktop/motion-clone
SHA=$(git rev-parse --short=7 HEAD)
OWNER=$(gh repo view --json owner -q .owner.login)

sed -i '' "s|^MTC_PREBUILT=0$|MTC_PREBUILT=1|" .env
sed -i '' "s|^SETUP_PROFILE=motion-transfer$|SETUP_PROFILE=full|" .env
sed -i '' "s|^DISPATCH_JOB_TYPES=.*$|DISPATCH_JOB_TYPES=motion,teen-flycam,trend-tiktok,enhance,create-image,edit-image,tryon,talk,face-motion,story-film,product-overlay,concat,reveal,voiceover,subtitle,teaser|" .env
grep -q '^POD_IMAGE=' .env \
  && sed -i '' "s|^POD_IMAGE=.*$|POD_IMAGE=ghcr.io/$OWNER/motion-prebuilt:sha-$SHA|" .env \
  || printf 'POD_IMAGE=ghcr.io/%s/motion-prebuilt:sha-%s\n' "$OWNER" "$SHA" >> .env

TYPES16='motion,teen-flycam,trend-tiktok,enhance,create-image,edit-image,tryon,talk,face-motion,story-film,product-overlay,concat,reveal,voiceover,subtitle,teaser'
grep -q '^JOB_TYPES_OVERRIDE=' .env \
  && sed -i '' "s|^JOB_TYPES_OVERRIDE=.*$|JOB_TYPES_OVERRIDE=$TYPES16|" .env \
  || printf 'JOB_TYPES_OVERRIDE=%s\n' "$TYPES16" >> .env
```

- [ ] **Step 4: Xác nhận năm dòng đã đúng**

```bash
grep -nE '^(MTC_PREBUILT|SETUP_PROFILE|POD_IMAGE|DISPATCH_JOB_TYPES|JOB_TYPES_OVERRIDE)=' .env
awk -F= '/^JOB_TYPES_OVERRIDE=/{n=split($2,a,","); print "JOB_TYPES_OVERRIDE có", n, "type"}' .env
```

Mong đợi: `MTC_PREBUILT=1`, `SETUP_PROFILE=full`, `POD_IMAGE=ghcr.io/<owner>/motion-prebuilt:sha-<7 ký tự>`, và **`JOB_TYPES_OVERRIDE có 16 type`**.

- [ ] **Step 5: Cập nhật `.env.example` với giải thích**

Sửa `.env.example` dòng 114 (`MTC_PREBUILT=0`) — giữ mặc định `0` cho người mới clone, nhưng thay khối comment phía trên bằng:

```sh
# MTC_PREBUILT=1 → boot từ image dựng sẵn (POD_IMAGE), bỏ ~20-35 phút cài apt/Node/torch/ComfyUI.
#   BẮT BUỘC đi kèm POD_IMAGE trỏ vào image do .github/workflows/build-prebuilt-image.yml dựng.
#   Thiếu /opt/mtc-prebuilt/.ready trong image là setup `die` ngay (lib-feature.sh:504) — cố ý, để
#   bạn không âm thầm rơi về đường cài-từ-đầu mà không biết.
#   Để 0 nếu dùng image runpod/pytorch trần.
MTC_PREBUILT=0
```

Và dòng 39 (`POD_IMAGE=`):

```sh
# POD_IMAGE — bỏ trống thì pod-provision.sh tự chọn theo provider (runpod/pytorch cho GPU,
#   runpod/base cho CPU). Với MTC_PREBUILT=1 thì BẮT BUỘC điền, và GHIM TAG sha-<commit>:
#   `latest` trôi theo main, pod dựng lại tuần sau sẽ khác image mà không ai đổi gì.
#   vd: ghcr.io/<owner>/motion-prebuilt:sha-a1b2c3d
POD_IMAGE=
```

Và thêm khối mới cạnh `SETUP_PROFILE` (dòng 152):

```sh
# JOB_TYPES_OVERRIDE — thu hẹp JOB_TYPES so với những gì SETUP_PROFILE khai. Chỉ CẮT BỚT được,
#   type ngoài profile là cổng chặn `die` (thiếu custom node thì worker chết ở /prompt, không
#   phải chỉ thiếu model). Dùng khi volume chưa có model cho hết số type profile hỗ trợ:
#   worker không claim job nó không chạy nổi, thay vì nhận rồi set error.
#   Bỏ trống = dùng nguyên JOB_TYPE của profile.
JOB_TYPES_OVERRIDE=
```

- [ ] **Step 6: Chạy preflight — cổng chặn TRƯỚC khi tốn tiền**

```bash
make gpu-preflight
```

Mong đợi: không có lỗi đỏ. `pod-provision.sh:118` **không** được cảnh báo về image nữa (base đã là `runpod/*`). Nếu vẫn cảnh báo thì `POD_IMAGE` sai — nó phải bắt đầu bằng `ghcr.io/...` và cổng chặn chỉ kiểm tiền tố `runpod/*` cho image mặc định.

> Nếu preflight cảnh báo `POD_IMAGE` không phải `runpod/*`: đây là cảnh báo đúng về mặt logic cũ nhưng sai với image này. Sửa `scripts/pod-provision.sh:115-123` để bỏ qua cảnh báo khi `MTC_PREBUILT=1`, kèm comment giải thích base của image prebuilt đã là `runpod/*`.

- [ ] **Step 7: Commit**

```bash
git add .env.example scripts/pod-provision.sh scripts/pod-bootstrap.sh motions-studio/setup/lib-feature.sh
git commit -m "Profile full + MTC_PREBUILT, và JOB_TYPES_OVERRIDE: khoá worker về 16 type có model"
git push
```

`.env` **không** commit — nó chứa secret và nằm trong `.gitignore`.

---

### Task 5: Tải `qwen2.5vl:7b` vào volume bằng pod CPU

5,6 GB, là model duy nhất còn thiếu để `create-image`, `edit-image`, `tryon` chạy đủ. Làm trên pod CPU $0,06/giờ, **không** trên đồng hồ GPU $1,014/giờ.

**Files:** không sửa file nào — thao tác hạ tầng.

**Interfaces:**
- Consumes: volume `wfe86wzkpm`
- Produces: `/workspace/ollama-models` có `qwen2.5vl`. Task 6 bước 6 chạy job `tryon` dựa vào nó.

- [ ] **Step 1: Tạo pod CPU gắn volume, kèm lưới tự huỷ**

```bash
runpodctl pod create --name preload-vl --compute-type CPU \
  --image runpod/base:1.0.2-ubuntu2204 \
  --data-center-ids EU-RO-1 --network-volume-id wfe86wzkpm \
  --container-disk-in-gb 20 --ssh \
  --terminate-after "$(date -u -v+2H +%Y-%m-%dT%H:%M:%SZ)"
```

- [ ] **Step 2: Chờ SSH sẵn sàng**

```bash
PID=$(runpodctl pod list | jq -r '.[] | select(.name=="preload-vl") | .id')
for i in $(seq 1 40); do
  out=$(runpodctl ssh info "$PID" 2>&1)
  echo "$out" | grep -q "pod not ready" || { echo "$out"; break; }
  sleep 10
done
```

Mong đợi: JSON có `ssh_command`. Với pod CPU, `runtime.ports` ở `null` mãi — **đừng** chờ nó, sshd vẫn lên bình thường.

- [ ] **Step 3: Trên pod, lấy repo và chạy preload ở chế độ thử khan**

```bash
# thay <ip>/<port> bằng số từ Step 2
ssh -i ~/.runpod/ssh/runpodctl-ssh-key root@<ip> -p <port>
# --- từ đây là lệnh CHẠY TRÊN POD ---
git clone https://github.com/<owner>/motion-clone.git && cd motion-clone/motions-studio
POD_VOLUME=/workspace VOLUME_GB=100 ./setup/preload-models.sh --list
POD_VOLUME=/workspace VOLUME_GB=100 ./setup/preload-models.sh --id ollama-qwen25vl-7b --dry-run
```

Mong đợi từ `--list`: `qwen2.5:7b-instruct` báo đã cài, `qwen2.5vl:7b` báo chưa. `--dry-run` in ra kế hoạch tải 5,6 GB và **không** báo hết chỗ (`VOLUME_GB=100` bật cổng chặn; thiếu biến này là tắt cổng).

- [ ] **Step 4: Tải thật**

```bash
# --- CHẠY TRÊN POD ---
POD_VOLUME=/workspace VOLUME_GB=100 ./setup/preload-models.sh --id ollama-qwen25vl-7b
POD_VOLUME=/workspace VOLUME_GB=100 ./setup/preload-models.sh --list
du -sh /workspace/ollama-models
```

Mong đợi: `--list` báo `qwen2.5vl:7b` **đã cài**, và `ollama-models` khoảng **10 GB** (4,4 + 5,6).

- [ ] **Step 5: Xoá pod NGAY và xác nhận không còn pod nào**

```bash
# --- quay lại máy mình ---
runpodctl pod delete "$PID"
runpodctl pod list | jq -r 'if length==0 then "RỖNG" else .[].name end'
```

Mong đợi: `RỖNG`. Pod CPU vẫn tính tiền theo giờ — quên xoá là đốt tiền cho một máy không làm gì.

---

### Task 6: Verify trên pod GPU thật

Đây là task tốn tiền duy nhất (~$0,5-1). Mọi cổng chặn rẻ hơn đã qua ở Task 1-5.

**Files:** không sửa file — đo và ghi số.

**Interfaces:**
- Consumes: image từ Task 3, `.env` từ Task 4, model từ Task 5
- Produces: các con số vào `/tmp/mtc-prebuilt-do.txt` cho Task 7

- [ ] **Step 1: Ghi mốc thời gian rồi thuê pod**

```bash
cd /Users/thucpham/Desktop/motion-clone
date -u +%s > /tmp/mtc-t0
make gpu-preflight
bash scripts/pod-provision.sh                # dry-run — ĐỌC lệnh nó in ra, xác nhận --image đúng
CONFIRM=yes bash scripts/pod-provision.sh
make gpu-wait
date -u +%s > /tmp/mtc-t1
echo "PULL + BOOT: $(( $(cat /tmp/mtc-t1) - $(cat /tmp/mtc-t0) )) giây" >> /tmp/mtc-prebuilt-do.txt
```

Mong đợi ở dry-run: `--image ghcr.io/<owner>/motion-prebuilt:sha-...`, `--container-disk-in-gb 100`, và có `--network-volume-id wfe86wzkpm`.

- [ ] **Step 2: Chạy bootstrap và đo**

```bash
date -u +%s > /tmp/mtc-t2
make gpu-bootstrap
date -u +%s > /tmp/mtc-t3
echo "BOOTSTRAP: $(( $(cat /tmp/mtc-t3) - $(cat /tmp/mtc-t2) )) giây" >> /tmp/mtc-prebuilt-do.txt
echo "TỔNG: $(( $(cat /tmp/mtc-t3) - $(cat /tmp/mtc-t0) )) giây (baseline 20-35 phút = 1200-2100 giây)" >> /tmp/mtc-prebuilt-do.txt
```

Mong đợi trong log: dòng `2–7/11 · Fast boot từ image dựng sẵn (bỏ apt, Node, PyTorch và ComfyUI install)` — chứng minh nhánh prebuilt thật sự chạy. Và `bg-remover: dùng venv dựng sẵn từ image` từ Task 3 Step 3.

Thấy `phase_apt` chạy nghĩa là `MTC_PREBUILT` chưa tới được pod — kiểm `.env` dòng 51 và `pod-bootstrap.sh:55`.

Cũng phải thấy dòng `JOB_TYPES bị thu hẹp bằng JOB_TYPES_OVERRIDE: ...` từ Task 4. Xác nhận trên pod:

```bash
source .env
ssh -i ~/.runpod/ssh/runpodctl-ssh-key root@"$GPU_SSH_HOST" -p "$GPU_SSH_PORT" \
  "grep '^JOB_TYPES=' ~/motion-clone/motions-studio/.env" \
  | awk -F= '{n=split($2,a,","); print "pod JOB_TYPES có", n, "type"}'
```

Mong đợi: **16 type**. Ra 21 nghĩa là đường ghi đè chưa thông — worker sẽ claim `text-to-video`, `wan-i2v`, `bds`, `ss`, `video` rồi set `error`.

- [ ] **Step 3: Kiểm torch nhận đúng GPU — cổng chặn của cả thiết kế**

```bash
source .env
ssh -i ~/.runpod/ssh/runpodctl-ssh-key root@"$GPU_SSH_HOST" -p "$GPU_SSH_PORT" \
  '/opt/mtc-prebuilt/ComfyUI/venv/bin/python -c "
import torch
print(\"version:\", torch.__version__)
print(\"cuda:\", torch.version.cuda)
print(\"available:\", torch.cuda.is_available())
print(\"capability:\", torch.cuda.get_device_capability())
x = torch.randn(2048, 2048, device=\"cuda\"); print(\"matmul ok:\", float((x @ x).sum()) == float((x @ x).sum()))
"'
```

Mong đợi: `capability: (12, 0)` (sm_120 của RTX 5090), `available: True`, `matmul ok: True`.

`(12, 0)` sai hoặc `available: False` → torch trong image không khớp driver. Đây đúng là lỗi mà `MTC_PREBUILT` bỏ qua `motion_install_best_pytorch` để lộ ra. Dừng, ghi số đo, `make gpu-destroy`, quay lại Task 2 đổi index-url/phiên bản torch.

- [ ] **Step 4: Chạy `gpu-smoke` năm lớp đầu**

```bash
make gpu-smoke
```

Mong đợi: lớp 1-5 xanh. Lớp 4 (`/object_info/WanVideoModelLoader`) chứng minh custom node đã nạp thật; lớp 5 chứng minh volume đang được dùng và số file model không giảm.

- [ ] **Step 5: Chạy lớp 6 — job motion thật**

```bash
SMOKE_REF=<đường-dẫn-ảnh-nhân-vật> SMOKE_DRIVER=<đường-dẫn-video-dẫn-động> make gpu-smoke
```

Mong đợi: job chạy tới `done`, tải về `/tmp/smoke-out.mp4` với dung lượng > 0. Đây là lớp duy nhất chứng minh sáu mảnh ghép lại đúng.

- [ ] **Step 6: Chạy một job `tryon` và một job `create-image` qua UI**

Mở `https://$FE_DOMAIN`, đăng nhập bằng `SUPER_ADMIN`, tạo một job **Try-on** (bật Auto nhận loại đồ) và một job **Tạo ảnh**.

Mong đợi: cả hai ra kết quả. Đây là thứ duy nhất chứng minh `qwen2.5vl:7b` (Task 5) và venv bg-remover trong image (Task 3) chạy thật — `gpu-smoke` không đụng tới hai đường này.

- [ ] **Step 7: Đọc tiền thật rồi dọn**

```bash
runpodctl billing | head -20        # KHÔNG dùng currentSpendPerHr
make gpu-destroy
runpodctl pod list | jq -r 'if length==0 then "RỖNG" else .[].name end'
cat /tmp/mtc-prebuilt-do.txt
```

Mong đợi: `RỖNG`, và file ghi chú có đủ dung lượng image, PULL+BOOT, BOOTSTRAP, TỔNG.

---

### Task 7: Ghi số đo vào docs và chốt trạng thái spec

Số đo không vào tài liệu thì lần sau lại phải suy lại từ đầu — đúng lý do repo này viết docs dày.

**Files:**
- Modify: `docs/gpu-pod.md` (§Network Volume bảng chi phí lặp lại; §Image dựng sẵn `MTC_PREBUILT=1`; §Giai đoạn 1 câu "~30-40 phút")
- Modify: `docs/superpowers/specs/2026-08-05-mtc-prebuilt-runpod-design.md` (dòng trạng thái)

**Interfaces:**
- Consumes: `/tmp/mtc-prebuilt-do.txt` từ Task 2, 3, 6
- Produces: không có (task cuối)

- [ ] **Step 1: Sửa bảng chi phí lặp lại ở `docs/gpu-pod.md:1119-1122`**

Thay dòng `| ~20-35 phút cài phần mềm | ... | MTC_PREBUILT=1 |` bằng phiên bản có số thật:

```markdown
| ~20-35 phút cài phần mềm | `setup-motion-transfer.sh` cài ComfyUI + torch + custom node từ đầu | `MTC_PREBUILT=1` — **đo 05/08/2026: còn <TỔNG> phút** (pull <PULL> + bootstrap <BOOTSTRAP>) |
```

- [ ] **Step 2: Viết lại §Image dựng sẵn (`docs/gpu-pod.md:1189`)**

Thay toàn bộ mục bằng nội dung đã cập nhật: base `runpod/pytorch`, image `ghcr.io/<owner>/motion-prebuilt`, workflow `build-prebuilt-image.yml`, dung lượng nén thật, và ghi rõ `POD_IMAGE` phải ghim `sha-<commit>`. Giữ nguyên câu cảnh báo về marker `.ready` — nó vẫn đúng.

- [ ] **Step 3: Sửa câu "~30-40 phút" ở §Giai đoạn 1 (`docs/gpu-pod.md:79`)**

Thêm ngay sau: nêu con số mới khi `MTC_PREBUILT=1`, và giữ số cũ cho đường không prebuilt.

- [ ] **Step 4: Cập nhật dòng trạng thái của spec**

Sửa dòng 3-4 của `docs/superpowers/specs/2026-08-05-mtc-prebuilt-runpod-design.md` từ `**Trạng thái:** thiết kế, **chưa dựng**` thành trạng thái thật kèm ngày và kết quả sáu bước verify.

- [ ] **Step 5: GỠ nhánh dev khỏi trigger của workflow**

Trong `.github/workflows/build-prebuilt-image.yml`, đổi lại:

```yaml
    branches: [main]
```

và xoá khối comment giải thích về nhánh dev. Để lại thì mọi push lên nhánh đó đốt ~40 phút CI. Đây là bước dễ quên nhất của cả plan — nó không làm gì hỏng ngay, chỉ âm thầm tốn.

- [ ] **Step 6: Commit và đẩy**

```bash
git add docs/gpu-pod.md docs/superpowers/specs/2026-08-05-mtc-prebuilt-runpod-design.md \
        .github/workflows/build-prebuilt-image.yml
git commit -m "Số đo thật MTC_PREBUILT: <TỔNG> phút thay vì 20-35, image <X> GB"
git push
```

- [ ] **Step 7: Mở PR trên web UI**

`gh` trên máy này chỉ có quyền READ nên không tạo PR được. Mở:

```
https://github.com/doanhthuc/motion-clone/compare/main...mtc-prebuilt-runpod
```

Tiêu đề: `MTC_PREBUILT trên RunPod: image dựng sẵn + profile full`

Nội dung: xoá ~20-35 phút cài phần mềm khỏi mỗi lần dựng pod; spec ở `docs/superpowers/specs/2026-08-05-mtc-prebuilt-runpod-design.md`; số đo thật ở `docs/gpu-pod.md` §Image dựng sẵn.

---

## Thứ tự và cổng chặn

```
Task 1  CI + khung        →  cổng: image PULL ĐƯỢC không cần login
Task 2  ComfyUI + torch   →  cổng: assert torch/CUDA xanh · image < 40 GB
Task 3  lớp pod + .ready  →  cổng: 6 dòng OK + đủ 9 node · image < 45 GB
Task 4  .env + JOB_TYPES  →  cổng: gpu-preflight sạch · JOB_TYPES_OVERRIDE có 16 type
Task 5  qwen2.5vl:7b      →  cổng: --list báo đã cài · pod CPU đã xoá
Task 6  pod GPU thật      →  cổng: capability (12,0) · smoke 6 lớp · tryon + create-image
Task 7  docs              →  PR
```

Task 1-4 không tốn tiền thuê máy. Task 5 ~$0,01. Task 6 ~$0,5-1. Mọi cổng chặn rẻ đứng trước cổng đắt — nếu image quá to hoặc torch sai, ta biết trước khi thuê GPU.
