# RunPod Serverless (hướng A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Chạy worker GPU trên RunPod Serverless (scale-to-zero) thay cho worker local, dùng đúng giao thức HTTP mà worker hiện có đã nói với api.

**Architecture:** Container serverless chạy nguyên `worker_runtime.linux.PIPELINES` với `API_URL` trỏ về api công khai. Một tiến trình dispatcher poll bảng `jobs` và gọi `POST /v2/<endpoint>/run` khi có job `queued`. Worker tỉnh dậy tự `POST /worker/claim` — claim đã atomic trong Postgres nên không cần điều phối gì thêm.

**Tech Stack:** Python 3.11 (handler, `runpod` SDK) · Docker + GitHub Actions → GHCR · Node.js (dispatcher, `pg`) · PM2.

Spec: [`docs/superpowers/specs/2026-08-01-serverless-huong-a-design.md`](../specs/2026-08-01-serverless-huong-a-design.md). Cả ba giả định đã đo trên pod thật 01/08/2026.

## Global Constraints

- **Không sửa file upstream nào trong `motions-studio/`.** `scripts/sync-upstream.sh:81` rsync **không** dùng `--delete`, nên file MỚI sống sót qua sync còn file upstream ĐÃ SỬA bị ghi đè. Mọi thứ trong plan này là file mới, trừ `scripts/pod-bootstrap.sh` và `.github/` vốn thuộc fork.
- **Mỗi container serverless phải có `WORKER_ID` riêng.** `api/src/routes/jobs.js:219-224` reclaim job theo `worker_id` cộng `active_job_ids` mỗi lần có ai gọi `/worker/claim`; `worker_runtime/linux.py:36` mặc định `worker-1`. Trùng id ⇒ worker B chuyển job đang chạy của worker A sang `error`.
- **Registry:** `ghcr.io/${{ github.repository_owner }}/motion-serverless`. Không gõ cứng tên tài khoản.
- **`custom_nodes` phải ghim commit** đúng bản đã chạy được trên pod 01/08/2026:
  `ComfyUI-WanVideoWrapper 088128b` · `ComfyUI-KJNodes 4d46ac1` · `ComfyUI-VideoHelperSuite 4ee72c0` · `comfyui_controlnet_aux e8b689a` · `ComfyUI-Frame-Interpolation 26545cc` · `ComfyUI-FlashVSR_Stable f7f55ba`
- **Phải nướng `comfyui_controlnet_aux/ckpts/` (337MB) vào image.** Weight DWPose không nằm trong `models/` nên không có trên Network Volume; thiếu nó thì mỗi cold start tải lại 337MB.
- **Model nằm trên Network Volume**, mount vào `/app/ComfyUI/models`. Endpoint phải cùng datacenter với volume (`EU-RO-1`, volume `wfe86wzkpm`).
- Không dùng `pm2 restart --update-env` để đổi đối số của tiến trình — nó chỉ làm mới env, `args` vẫn đóng băng. Phải `pm2 delete` rồi `pm2 start`.

---

## File Structure

| File | Trách nhiệm | Mới/Sửa |
|---|---|---|
| `motions-studio/worker/runpod/mc_handler.py` | Handler serverless: một nhịp claim-and-run | **Mới** |
| `motions-studio/worker/runpod/test_mc_handler.py` | Test handler bằng stub, không cần GPU | **Mới** |
| `motions-studio/worker/runpod/Dockerfile.selfhosted` | Image: ComfyUI + 6 node ghim commit + ckpts DWPose + worker | **Mới** |
| `motions-studio/worker/runpod/entrypoint-selfhosted.sh` | Sinh `WORKER_ID` duy nhất → ComfyUI nền → handler | **Mới** |
| `.github/workflows/build-serverless-image.yml` | Build + push GHCR (repo root, nếu không GitHub không đọc) | **Mới** |
| `motions-studio/api/src/mc-dispatcher.js` | Poll `jobs` queued → `POST /v2/<id>/run` | **Mới** |
| `motions-studio/api/src/test-mc-dispatcher.mjs` | Test dispatcher bằng stub | **Mới** |
| `scripts/pod-bootstrap.sh` | Đăng ký dispatcher với PM2 | Sửa (file của fork) |
| `docs/gpu-pod.md` | Mục vận hành serverless | Sửa (file của fork) |

`rp_handler.py`, `Dockerfile`, `entrypoint.sh` sẵn có **giữ nguyên không đụng** — chúng thuộc đường Task Cloud, còn dùng.

---

### Task 1: Handler serverless

**Files:**
- Create: `motions-studio/worker/runpod/mc_handler.py`
- Test: `motions-studio/worker/runpod/test_mc_handler.py`

**Interfaces:**
- Consumes: `worker_runtime.linux.PIPELINES` (dict `job type → callable(job)`), `api_claim(active_ids) -> dict|None`, `api_patch(job_id, **fields)`, `_startup()`
- Produces: `handler(event) -> dict` với các khoá `ok: bool`, `claimed: bool`, `job: str|None`, `error: str|None`

- [ ] **Step 1: Viết test thất bại**

```python
# motions-studio/worker/runpod/test_mc_handler.py
"""Test mc_handler bằng stub — không cần GPU, ComfyUI hay mạng."""
import sys, types, pytest

@pytest.fixture
def stub(monkeypatch):
    """Dựng module worker_runtime.linux giả trước khi mc_handler import nó."""
    calls = {"claim": [], "patch": [], "ran": [], "startup": 0}
    mod = types.ModuleType("worker_runtime.linux")

    def api_claim(active_ids):
        calls["claim"].append(active_ids)
        return calls.get("next_job")

    def api_patch(job_id, **fields):
        calls["patch"].append((job_id, fields))

    def _startup():
        calls["startup"] += 1

    def run_motion(job):
        calls["ran"].append(job["id"])

    mod.api_claim = api_claim
    mod.api_patch = api_patch
    mod._startup = _startup
    mod.PIPELINES = {"motion": run_motion}
    pkg = types.ModuleType("worker_runtime")
    pkg.linux = mod
    monkeypatch.setitem(sys.modules, "worker_runtime", pkg)
    monkeypatch.setitem(sys.modules, "worker_runtime.linux", mod)
    monkeypatch.setitem(sys.modules, "runpod", types.ModuleType("runpod"))
    sys.modules.pop("mc_handler", None)
    return calls


def test_khong_co_job_thi_thoat_ngay(stub):
    stub["next_job"] = None
    import mc_handler
    assert mc_handler.handler({}) == {"ok": True, "claimed": False}
    assert stub["ran"] == []


def test_co_job_thi_chay_dung_pipeline(stub):
    stub["next_job"] = {"id": "j1", "type": "motion"}
    import mc_handler
    out = mc_handler.handler({})
    assert out["ok"] is True and out["job"] == "j1"
    assert stub["ran"] == ["j1"]


def test_job_type_la_khong_ho_tro_thi_bao_error_chu_khong_treo(stub):
    stub["next_job"] = {"id": "j2", "type": "khong-ton-tai"}
    import mc_handler
    out = mc_handler.handler({})
    assert out["ok"] is False
    assert stub["patch"] == [("j2", {"status": "error",
                                     "error": "worker khong ho tro type 'khong-ton-tai'"})]


def test_pipeline_nem_loi_thi_job_ve_error_kem_thong_diep(stub):
    def no(job):
        raise RuntimeError("bung")
    stub["next_job"] = {"id": "j3", "type": "motion"}
    import mc_handler
    mc_handler.PIPELINES["motion"] = no
    out = mc_handler.handler({})
    assert out["ok"] is False and "bung" in out["error"]
    assert stub["patch"][0][0] == "j3"
    assert stub["patch"][0][1]["status"] == "error"
```

- [ ] **Step 2: Chạy để chắc nó thất bại**

Run: `cd motions-studio/worker/runpod && python -m pytest test_mc_handler.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mc_handler'`

- [ ] **Step 3: Viết implementation tối thiểu**

```python
# motions-studio/worker/runpod/mc_handler.py
"""Handler RunPod Serverless cho stack TỰ CHỦ.

Khác rp_handler.py (đường Task Cloud) ở đúng một điểm cốt lõi: không monkeypatch gì.
worker_runtime/linux.py đã nói HTTP đầy đủ với api của chính chúng ta
(/worker/claim, /files/{key}, /jobs/{id}/output, ...), nên container chỉ cần đặt
API_URL + WORKER_TOKEN rồi gọi thẳng PIPELINES. Xem spec §Quyết định 1.

Một request = một nhịp claim-and-run. Không claim được thì thoát ngay, tốn vài giây.
"""
import os
import traceback

import runpod

from worker_runtime.linux import PIPELINES, api_claim, api_patch, _startup

# _startup() dọn queue ComfyUI mồ côi. Trên container mới queue luôn rỗng nên nó là no-op,
# nhưng gọi cho giống worker thường: nếu RunPod tái dùng container ấm, queue có thể còn rác.
_STARTED = False


def _ensure_started():
    global _STARTED
    if not _STARTED:
        try:
            _startup()
        except Exception:
            traceback.print_exc()
        _STARTED = True


def handler(event):
    _ensure_started()

    # Không truyền job_id: claim tự phân xử. Nếu hai worker cùng tỉnh mà chỉ có một job,
    # đứa thứ hai nhận None rồi thoát — mất vài xu, không mất tính đúng đắn (spec §Quyết định 2).
    job = api_claim([])
    if not job:
        return {"ok": True, "claimed": False}

    job_id = job.get("id")
    job_type = job.get("type")
    fn = PIPELINES.get(job_type)
    if not fn:
        msg = "worker khong ho tro type '%s'" % job_type
        api_patch(job_id, status="error", error=msg)
        return {"ok": False, "claimed": True, "job": job_id, "error": msg}

    try:
        fn(job)
        return {"ok": True, "claimed": True, "job": job_id}
    except Exception as e:
        traceback.print_exc()
        api_patch(job_id, status="error", error=str(e))
        return {"ok": False, "claimed": True, "job": job_id, "error": str(e)}


if __name__ == "__main__":
    missing = [k for k in ("API_URL", "WORKER_TOKEN", "WORKER_ID") if not os.environ.get(k)]
    if missing:
        raise SystemExit("thieu bien moi truong bat buoc: %s" % ", ".join(missing))
    runpod.serverless.start({"handler": handler})
```

- [ ] **Step 4: Chạy test cho chắc nó xanh**

Run: `cd motions-studio/worker/runpod && python -m pytest test_mc_handler.py -v`
Expected: PASS, 4 passed

- [ ] **Step 5: Commit**

```bash
git add motions-studio/worker/runpod/mc_handler.py motions-studio/worker/runpod/test_mc_handler.py
git commit -m "Handler serverless cho stack tự chủ: một nhịp claim-and-run, không monkeypatch"
```

---

### Task 2: Image + entrypoint

**Files:**
- Create: `motions-studio/worker/runpod/entrypoint-selfhosted.sh`
- Create: `motions-studio/worker/runpod/Dockerfile.selfhosted`

**Interfaces:**
- Consumes: `mc_handler.py` từ Task 1
- Produces: image chạy được với env `API_URL`, `WORKER_TOKEN`, `COMFY_URL=http://127.0.0.1:8188`, `JOB_TYPES`; tự sinh `WORKER_ID`

- [ ] **Step 1: Viết entrypoint**

```bash
# motions-studio/worker/runpod/entrypoint-selfhosted.sh
#!/usr/bin/env bash
# Entrypoint image serverless cho stack tự chủ: sinh WORKER_ID duy nhất → ComfyUI nền → handler.
set -euo pipefail

# WORKER_ID PHẢI khác nhau giữa các container. api/src/routes/jobs.js:219 reclaim mọi job
# 'running' của cùng worker_id mỗi lần có ai gọi /worker/claim, nên hai container dùng chung id
# nghĩa là container B chuyển job đang render của container A sang 'error'. Với max workers >= 2
# lỗi này xảy ra ngay job thứ hai, và triệu chứng ("Worker khởi động lại giữa chừng") không hề
# gợi ý nguyên nhân. Ưu tiên id RunPod cấp; không có thì sinh ngẫu nhiên.
export WORKER_ID="${WORKER_ID:-serverless-${RUNPOD_POD_ID:-$(head -c 8 /dev/urandom | od -An -tx1 | tr -d ' \n')}}"
echo "[entrypoint] WORKER_ID=$WORKER_ID"

export COMFY_URL="${COMFY_URL:-http://127.0.0.1:8188}"

cd /app/ComfyUI
echo "[entrypoint] starting ComfyUI on 127.0.0.1:8188 ..."
python -u main.py --listen 127.0.0.1 --port 8188 ${COMFY_EXTRA_ARGS:-} > /tmp/comfyui.log 2>&1 &

cd /app/worker
echo "[entrypoint] starting serverless handler ..."
exec python -u runpod/mc_handler.py
```

- [ ] **Step 2: Viết Dockerfile**

```dockerfile
# motions-studio/worker/runpod/Dockerfile.selfhosted
# Image RunPod Serverless cho stack TỰ CHỦ (khác Dockerfile cạnh nó, vốn cho Task Cloud).
# Build từ thư mục motions-studio/:
#   docker build -f worker/runpod/Dockerfile.selfhosted -t ghcr.io/<owner>/motion-serverless:latest .
# Model KHÔNG bake — nằm trên Network Volume mount vào /app/ComfyUI/models.
FROM pytorch/pytorch:2.12.1-cuda13.0-cudnn9-runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
      git wget aria2 ffmpeg libgl1 libglib2.0-0 ca-certificates build-essential pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git
WORKDIR /app/ComfyUI
RUN pip install --no-cache-dir -r requirements.txt

# Ghim commit ĐÚNG bản đã chạy được trên pod 01/08/2026. `--depth 1` không checkout commit cụ thể
# được nên clone rồi fetch từng cái. Không ghim thì image trôi theo upstream và một ngày nào đó
# ComfyUI trả 400 "node type not found" mà không ai đổi gì.
WORKDIR /app/ComfyUI/custom_nodes
RUN set -eux; \
    for spec in \
      "https://github.com/kijai/ComfyUI-WanVideoWrapper.git 088128b" \
      "https://github.com/kijai/ComfyUI-KJNodes.git 4d46ac1" \
      "https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git 4ee72c0" \
      "https://github.com/Fannovel16/comfyui_controlnet_aux.git e8b689a" \
      "https://github.com/Fannovel16/ComfyUI-Frame-Interpolation.git 26545cc" \
      "https://github.com/naxci1/ComfyUI-FlashVSR_Stable.git f7f55ba" \
    ; do \
      url="${spec% *}"; sha="${spec#* }"; d="$(basename "$url" .git)"; \
      git clone --filter=blob:none "$url" "$d"; \
      git -C "$d" checkout "$sha"; \
    done
RUN for d in */ ; do [ -f "$d/requirements.txt" ] && pip install --no-cache-dir -r "$d/requirements.txt" || true ; done

# Weight DWPose 337MB. Đo trên pod thật: chúng nằm TRONG thư mục node, không nằm trong models/,
# nên KHÔNG có trên Network Volume. Không bake thì mỗi cold start tải lại 337MB.
RUN mkdir -p /app/ComfyUI/custom_nodes/comfyui_controlnet_aux/ckpts/hr16/yolox-onnx \
             /app/ComfyUI/custom_nodes/comfyui_controlnet_aux/ckpts/hr16/DWPose-TorchScript-BatchSize5 \
 && wget -q -O /app/ComfyUI/custom_nodes/comfyui_controlnet_aux/ckpts/hr16/yolox-onnx/yolox_l.torchscript.pt \
      https://huggingface.co/hr16/yolox-onnx/resolve/main/yolox_l.torchscript.pt \
 && wget -q -O /app/ComfyUI/custom_nodes/comfyui_controlnet_aux/ckpts/hr16/DWPose-TorchScript-BatchSize5/dw-ll_ucoco_384_bs5.torchscript.pt \
      https://huggingface.co/hr16/DWPose-TorchScript-BatchSize5/resolve/main/dw-ll_ucoco_384_bs5.torchscript.pt

COPY worker /app/worker
RUN pip install --no-cache-dir -r /app/worker/requirements.txt runpod

ENV PYTHONPATH=/app/worker PYTHONUNBUFFERED=1
COPY worker/runpod/entrypoint-selfhosted.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
CMD ["/entrypoint.sh"]
```

- [ ] **Step 3: Kiểm Dockerfile parse được và entrypoint đúng cú pháp**

Run:
```bash
bash -n motions-studio/worker/runpod/entrypoint-selfhosted.sh
docker --version >/dev/null 2>&1 && \
  docker build --check -f motions-studio/worker/runpod/Dockerfile.selfhosted motions-studio 2>&1 | tail -5 || \
  echo "docker chua cai — CI se build, bo qua buoc nay"
```
Expected: entrypoint không báo lỗi; `docker build --check` không báo lỗi cú pháp.

- [ ] **Step 4: Kiểm WORKER_ID sinh ra là duy nhất**

Run:
```bash
for i in 1 2 3; do
  env -u WORKER_ID -u RUNPOD_POD_ID bash -c \
    'export WORKER_ID="${WORKER_ID:-serverless-${RUNPOD_POD_ID:-$(head -c 8 /dev/urandom | od -An -tx1 | tr -d " \n")}}"; echo "$WORKER_ID"'
done | sort -u | wc -l
```
Expected: `3` — ba lần chạy ra ba id khác nhau. Ra `1` là hỏng ràng buộc quan trọng nhất.

- [ ] **Step 5: Commit**

```bash
git add motions-studio/worker/runpod/Dockerfile.selfhosted motions-studio/worker/runpod/entrypoint-selfhosted.sh
git commit -m "Image serverless: ghim commit custom node, bake ckpts DWPose, WORKER_ID duy nhất"
```

---

### Task 3: CI build image lên GHCR

**Files:**
- Create: `.github/workflows/build-serverless-image.yml`

**Interfaces:**
- Consumes: `motions-studio/worker/runpod/Dockerfile.selfhosted` từ Task 2
- Produces: image `ghcr.io/<owner>/motion-serverless:latest` (+ tag `sha-<short>`)

- [ ] **Step 1: Viết workflow**

```yaml
# .github/workflows/build-serverless-image.yml
# PHẢI nằm ở GỐC repo. GitHub Actions chỉ đọc .github/workflows/ ở gốc — workflow trong
# motions-studio/.github/ và motions/.github/ là file chết, chưa từng chạy lần nào.
name: Build serverless image

on:
  workflow_dispatch:
  push:
    branches: [main]
    paths:
      - motions-studio/worker/runpod/Dockerfile.selfhosted
      - motions-studio/worker/runpod/entrypoint-selfhosted.sh
      - motions-studio/worker/runpod/mc_handler.py
      - .github/workflows/build-serverless-image.yml

permissions:
  contents: read
  packages: write

concurrency:
  group: serverless-image-${{ github.ref }}
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
          images: ghcr.io/${{ github.repository_owner }}/motion-serverless
          tags: |
            type=raw,value=latest
            type=sha,prefix=sha-

      - uses: docker/build-push-action@v6
        with:
          context: motions-studio
          file: motions-studio/worker/runpod/Dockerfile.selfhosted
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
```

- [ ] **Step 2: Kiểm YAML hợp lệ**

Run: `python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/build-serverless-image.yml')); print('YAML OK')"`
Expected: `YAML OK`

- [ ] **Step 3: Commit và đẩy để CI chạy**

```bash
git add .github/workflows/build-serverless-image.yml
git commit -m "CI: build image serverless vào GHCR, workflow đặt ở GỐC repo"
git push
```

- [ ] **Step 4: Xác nhận CI chạy và image pull được ẩn danh**

Run:
```bash
gh run list --workflow build-serverless-image.yml --limit 1
OWNER=$(gh repo view --json owner -q .owner.login)
TOK=$(curl -s "https://ghcr.io/token?scope=repository:${OWNER}/motion-serverless:pull" | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
curl -s -o /dev/null -w "manifest HTTP %{http_code}\n" -H "Authorization: Bearer $TOK" \
  -H "Accept: application/vnd.oci.image.index.v1+json" \
  "https://ghcr.io/v2/${OWNER}/motion-serverless/manifests/latest"
```
Expected: run `completed success`, manifest `HTTP 200`. Nếu `403` thì package đang private — vào GitHub → Packages → motion-serverless → Package settings → Change visibility → Public. RunPod pull ẩn danh nên bước này bắt buộc.

---

### Task 4: Dispatcher

**Files:**
- Create: `motions-studio/api/src/mc-dispatcher.js`
- Test: `motions-studio/api/src/test-mc-dispatcher.mjs`
- Modify: `scripts/pod-bootstrap.sh` (thêm đăng ký PM2, sau khối MinIO)

**Interfaces:**
- Consumes: bảng `jobs` trong Postgres; env `RUNPOD_ENDPOINT_ID`, `RUNPOD_API_KEY`, `DISPATCH_POLL_SEC` (mặc định 5), `DISPATCH_MAX_INFLIGHT` (mặc định 3)
- Produces: `decideDispatch({queued, running, maxInflight}) -> number` — số lần `/run` cần bắn. Hàm thuần để test không cần DB.

**Vì sao tiến trình riêng thay vì hook trong `jobs.js`:** spec §Quyết định 2 nói "api thêm một chỗ", nhưng `api/src/routes/jobs.js` là file upstream — sửa nó thì `make sync-upstream` ghi đè. Poll 5 giây là vô nghĩa so với cold start 1-3 phút, nên đánh đổi này gần như miễn phí và bỏ được hẳn một local delta.

- [ ] **Step 1: Viết test thất bại**

```javascript
// motions-studio/api/src/test-mc-dispatcher.mjs
// Chạy: node motions-studio/api/src/test-mc-dispatcher.mjs
import assert from "node:assert/strict"
import { decideDispatch } from "./mc-dispatcher.js"

// Không có job queued → không bắn gì. Bắn thừa là đốt tiền cold start vô ích.
assert.equal(decideDispatch({ queued: 0, running: 0, maxInflight: 3 }), 0)

// Một job, không có worker nào chạy → bắn đúng một lần.
assert.equal(decideDispatch({ queued: 1, running: 0, maxInflight: 3 }), 1)

// Đã có worker đang chạy đúng bằng số job queued → không cần thêm.
assert.equal(decideDispatch({ queued: 2, running: 2, maxInflight: 5 }), 0)

// Trần maxInflight chặn scale vô hạn khi hàng đợi dài.
assert.equal(decideDispatch({ queued: 50, running: 0, maxInflight: 3 }), 3)

// Đang chạy nhiều hơn queued (job vừa được nhận) → không âm.
assert.equal(decideDispatch({ queued: 1, running: 4, maxInflight: 3 }), 0)

console.log("mc-dispatcher: 5 assertions passed")
```

- [ ] **Step 2: Chạy để chắc nó thất bại**

Run: `node motions-studio/api/src/test-mc-dispatcher.mjs`
Expected: FAIL — `Cannot find module ... mc-dispatcher.js`

- [ ] **Step 3: Viết dispatcher**

```javascript
// motions-studio/api/src/mc-dispatcher.js
// Poll bảng jobs, gọi RunPod Serverless /run khi có job queued mà chưa đủ worker.
//
// Worker tỉnh dậy TỰ gọi /worker/claim — dispatcher không giao job cho ai cả, chỉ đánh thức.
// Claim đã atomic trong Postgres nên thừa một worker chỉ tốn vài giây, không sai kết quả.
//
// File MỚI, không sửa file upstream nào: scripts/sync-upstream.sh không dùng --delete nên file
// mới sống sót qua sync, còn file upstream đã sửa thì bị ghi đè.
import { query } from "./db.js"

const ENDPOINT = process.env.RUNPOD_ENDPOINT_ID || ""
const API_KEY = process.env.RUNPOD_API_KEY || ""
const POLL_MS = Math.max(2000, Number(process.env.DISPATCH_POLL_SEC || 5) * 1000)
const MAX_INFLIGHT = Math.max(1, Number(process.env.DISPATCH_MAX_INFLIGHT || 3))
const log = (...a) => console.log("[mc-dispatcher]", ...a)

/** Số lần /run cần bắn. Hàm thuần để test được mà không cần DB lẫn mạng. */
export function decideDispatch({ queued, running, maxInflight }) {
  const need = queued - running
  if (need <= 0) return 0
  return Math.min(need, maxInflight)
}

async function counts() {
  const { rows } = await query(
    `SELECT count(*) FILTER (WHERE status='queued')  AS queued,
            count(*) FILTER (WHERE status='running') AS running
       FROM jobs`,
  )
  return { queued: Number(rows[0].queued), running: Number(rows[0].running) }
}

async function fireOne() {
  const res = await fetch(`https://api.runpod.ai/v2/${ENDPOINT}/run`, {
    method: "POST",
    headers: { Authorization: `Bearer ${API_KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify({ input: {} }),
  })
  if (!res.ok) throw new Error(`/run ${res.status} ${(await res.text()).slice(0, 200)}`)
  return (await res.json())?.id || "?"
}

async function tick() {
  const { queued, running } = await counts()
  const n = decideDispatch({ queued, running, maxInflight: MAX_INFLIGHT })
  if (!n) return
  log(`queued=${queued} running=${running} → đánh thức ${n} worker`)
  for (let i = 0; i < n; i++) {
    try {
      log("  /run →", await fireOne())
    } catch (e) {
      // Hết capacity hoặc endpoint lỗi: job nằm nguyên 'queued', vòng sau thử lại.
      log("  /run lỗi:", e.message)
      break
    }
  }
}

async function main() {
  if (!ENDPOINT || !API_KEY) {
    log("thiếu RUNPOD_ENDPOINT_ID hoặc RUNPOD_API_KEY → không chạy")
    process.exit(1)
  }
  log(`bắt đầu · endpoint=${ENDPOINT} · poll=${POLL_MS}ms · maxInflight=${MAX_INFLIGHT}`)
  for (;;) {
    try {
      await tick()
    } catch (e) {
      log("tick lỗi:", e.message)
    }
    await new Promise((r) => setTimeout(r, POLL_MS))
  }
}

if (process.argv[1] && process.argv[1].endsWith("mc-dispatcher.js")) main()
```

- [ ] **Step 4: Chạy test cho chắc nó xanh**

Run: `node motions-studio/api/src/test-mc-dispatcher.mjs`
Expected: `mc-dispatcher: 5 assertions passed`

- [ ] **Step 5: Đăng ký dispatcher với PM2 từ pod-bootstrap.sh**

Chèn ngay **sau** khối MinIO trong `scripts/pod-bootstrap.sh` (khối bắt đầu bằng `# ── MinIO must point AT the volume`):

```bash
# ── Dispatcher serverless (tuỳ chọn) ──────────────────────────────────────────
# Đăng ký bằng `pm2 start <script>` chứ không thêm vào ecosystem.config.cjs: file đó là upstream,
# sửa vào là mất sau make sync-upstream.
RUNPOD_ENDPOINT_ID="$(env_get RUNPOD_ENDPOINT_ID)"
RUNPOD_API_KEY_ENV="$(env_get RUNPOD_API_KEY)"
if [ -n "$RUNPOD_ENDPOINT_ID" ] && [ -n "$RUNPOD_API_KEY_ENV" ]; then
  log "starting mc-dispatcher (serverless) — endpoint $RUNPOD_ENDPOINT_ID"
  remote "cd ~/$REMOTE_DIR && pm2 delete mc-dispatcher >/dev/null 2>&1 ; \
    RUNPOD_ENDPOINT_ID='$RUNPOD_ENDPOINT_ID' RUNPOD_API_KEY='$RUNPOD_API_KEY_ENV' \
    pm2 start api/src/mc-dispatcher.js --name mc-dispatcher --update-env >/dev/null 2>&1 ; \
    pm2 save >/dev/null 2>&1 ; sleep 4 ; \
    pm2 jlist | python3 -c \"import sys,json;m=[p for p in json.load(sys.stdin) if p['name']=='mc-dispatcher'];print('mc-dispatcher', m[0]['pm2_env']['status'] if m else 'MISSING')\"" \
    || warn "mc-dispatcher không start được — xem 'pm2 logs mc-dispatcher'"
else
  log "RUNPOD_ENDPOINT_ID/RUNPOD_API_KEY chưa đặt trong .env → bỏ qua dispatcher (worker local vẫn chạy)"
fi
```

- [ ] **Step 6: Kiểm cú pháp và commit**

```bash
bash -n scripts/pod-bootstrap.sh
git add motions-studio/api/src/mc-dispatcher.js motions-studio/api/src/test-mc-dispatcher.mjs scripts/pod-bootstrap.sh
git commit -m "Dispatcher: tiến trình poll riêng, không sửa file upstream nào"
```

---

### Task 5: Dựng endpoint và chứng minh end-to-end

**Files:**
- Modify: `docs/gpu-pod.md` (thêm mục Serverless)
- Modify: `.env.example` (thêm `RUNPOD_ENDPOINT_ID`, `RUNPOD_API_KEY`, `DISPATCH_MAX_INFLIGHT`)

**Interfaces:**
- Consumes: image từ Task 3, dispatcher từ Task 4
- Produces: endpoint chạy được, và các số đo chứng minh scale-to-zero

- [ ] **Step 1: Thêm biến vào `.env.example`**

```bash
# --- RunPod Serverless (tuỳ chọn) — không có task = không tốn tiền GPU -------------------
# Đặt cả hai thì make gpu-bootstrap dựng thêm tiến trình mc-dispatcher: nó poll bảng jobs và
# đánh thức worker serverless khi có job queued. Bỏ trống = worker local gánh như cũ.
# Endpoint PHẢI cùng datacenter với Network Volume (POD_VOLUME_ID ở trên).
RUNPOD_ENDPOINT_ID=
RUNPOD_API_KEY=
# Trần số worker đánh thức cùng lúc. Mỗi worker là một cold start ~1-3 phút, nên đặt cao không
# làm nhanh hơn mà chỉ tốn hơn.
DISPATCH_MAX_INFLIGHT=3
```

- [ ] **Step 2: Tạo endpoint trên RunPod**

Run:
```bash
runpodctl serverless --help
```
Rồi tạo qua dashboard (runpod.io/console/serverless → New Endpoint) với đúng các giá trị:

| Trường | Giá trị |
|---|---|
| Container Image | `ghcr.io/<owner>/motion-serverless:latest` |
| GPU | RTX 5090 |
| Datacenter | **EU-RO-1** — phải trùng volume `wfe86wzkpm` |
| Network Volume | `motion`, mount `/app/ComfyUI/models` |
| Container Disk | 30 GB |
| Min / Max Workers | 0 / 3 |
| Idle Timeout | 120 s |
| FlashBoot | ON |
| Env | `API_URL=https://api.doanhthuc.xyz` · `WORKER_TOKEN=<API_KEY trong .env backend>` · `JOB_TYPES=motion,teen-flycam,trend-tiktok,enhance` |

Chép Endpoint ID vào `.env` gốc là `RUNPOD_ENDPOINT_ID`, và API key RunPod vào `RUNPOD_API_KEY`.

- [ ] **Step 3: Bắn một request thẳng vào endpoint, không qua dispatcher**

Run:
```bash
EP=$(grep -E '^RUNPOD_ENDPOINT_ID=' .env | cut -d= -f2-)
KEY=$(grep -E '^RUNPOD_API_KEY=' .env | cut -d= -f2-)
curl -s -X POST "https://api.runpod.ai/v2/$EP/runsync" \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"input":{}}' | python3 -m json.tool
```
Expected: `{"output": {"ok": true, "claimed": false}}` khi hàng đợi rỗng. Đây chứng minh container boot được, `API_URL`/`WORKER_TOKEN` đúng, và `/worker/claim` tới được api. Cold start lần đầu 1-3 phút.

Nếu ra `{"error": ...}`: xem log worker trên dashboard. `thieu bien moi truong bat buoc` nghĩa là quên set env; lỗi kết nối nghĩa là `API_URL` sai hoặc `WORKER_TOKEN` không khớp `API_KEY` trong `.env` của backend.

- [ ] **Step 4: Chạy một job thật qua đường serverless**

Run:
```bash
# API_KEY của backend nằm trên pod, không nằm trong .env gốc — lấy về một lần để dùng cho cả Step 4 và 5
H=$(grep -E '^GPU_SSH_HOST=' .env | cut -d= -f2-); P=$(grep -E '^GPU_SSH_PORT=' .env | cut -d= -f2-)
APIKEY=$(ssh -p "$P" root@"$H" "grep -E '^API_KEY=' ~/motion-backend/.env | cut -d= -f2-" </dev/null)

# Tắt worker local để chắc chắn job do serverless xử lý, không phải pod
ssh -p "$P" root@"$H" 'pm2 stop worker' </dev/null

# Tạo job qua UI (https://app.doanhthuc.xyz) rồi theo dõi
watch -n 5 "curl -s https://api.doanhthuc.xyz/jobs?limit=3 -H "x-api-key: $APIKEY" | python3 -m json.tool | head -30"
```
Expected: job chuyển `queued → running → done`, `worker_id` bắt đầu bằng `serverless-`. Đó là bằng chứng serverless thật sự làm việc chứ không phải worker local.

- [ ] **Step 5: Chứng minh scale-to-zero và không giết job của nhau**

Run:
```bash
# a) Sau 3 phút không job, RunPod phải báo 0 worker
runpodctl serverless get "$EP" -o json 2>/dev/null | python3 -m json.tool | grep -iE 'worker|idle' || \
  echo "kiểm trên dashboard: Workers = 0"

# b) Hai job cùng lúc — bài kiểm cho ràng buộc WORKER_ID
#    Tạo hai job liên tiếp qua UI, rồi:
curl -s "https://api.doanhthuc.xyz/jobs?limit=5" -H "x-api-key: $APIKEY" \
  | python3 -c 'import sys,json;[print(j["id"][:8], j["status"], j.get("error","")[:60]) for j in json.load(sys.stdin)]'
```
Expected (a): 0 worker khi rỗi — đây là toàn bộ lý do làm serverless.
Expected (b): **không job nào** có `error` chứa `"Worker khởi động lại giữa chừng"`. Xuất hiện dòng đó nghĩa là `WORKER_ID` bị trùng giữa các container — quay lại Task 2 Step 4.

- [ ] **Step 6: Ghi mục Serverless vào `docs/gpu-pod.md`**

Chèn khối dưới đây vào `docs/gpu-pod.md`, ngay **trước** dòng `<a id="network-volume"></a>`:

```markdown
<a id="serverless"></a>
## Serverless — không có job thì không tốn tiền GPU

Worker chạy trên RunPod Serverless thay vì trên pod. Rỗi thì 0 worker, 0 đồng GPU.
api/Postgres/MinIO/FE vẫn cần một máy luôn bật — xem spec hướng A.

Bật bằng cách điền hai biến vào `.env` gốc rồi chạy lại `make gpu-bootstrap`:

| Biến | Ý nghĩa |
|---|---|
| `RUNPOD_ENDPOINT_ID` | id endpoint, lấy sau khi tạo ở runpod.io/console/serverless |
| `RUNPOD_API_KEY` | API key RunPod, dùng để gọi `/run` |
| `DISPATCH_MAX_INFLIGHT` | trần worker đánh thức cùng lúc, mặc định 3 |

Bootstrap sẽ dựng thêm tiến trình PM2 `mc-dispatcher`: nó poll bảng `jobs` mỗi 5 giây và gọi
`POST /v2/<endpoint>/run` khi có job `queued`. Worker tỉnh dậy **tự** gọi `/worker/claim` —
dispatcher không giao job cho ai, chỉ đánh thức.

### Cấu hình endpoint

| Trường | Giá trị |
|---|---|
| Image | `ghcr.io/<owner>/motion-serverless:latest` |
| GPU | RTX 5090 |
| Datacenter | **phải trùng datacenter của Network Volume** |
| Network Volume | mount `/app/ComfyUI/models` |
| Container Disk | 30 GB |
| Min / Max Workers | 0 / 3 |
| Idle Timeout | 120 s · FlashBoot ON |
| Env | `API_URL` · `WORKER_TOKEN` (= `API_KEY` trong `.env` backend) · `JOB_TYPES` |

### WORKER_ID phải duy nhất — đọc trước khi đổi entrypoint

`api/src/routes/jobs.js:219` reclaim mọi job `running` của cùng `worker_id` **mỗi lần** có ai gọi
`/worker/claim`. Với worker local chạy dài hạn thì đúng. Với serverless nhiều container thì
container B chuyển job đang render của container A sang `error`.

`entrypoint-selfhosted.sh` sinh `WORKER_ID` ngẫu nhiên cho từng container. Triệu chứng khi hỏng:
job fail rải rác kèm `"Worker khởi động lại giữa chừng"` mà **không worker nào restart**.

### Kiểm scale-to-zero

Sau ~3 phút không job, dashboard endpoint phải hiện **Workers = 0**. Còn worker nghĩa là idle
timeout quá dài hoặc dispatcher đang bắn `/run` liên tục — xem `pm2 logs mc-dispatcher`.
```

- [ ] **Step 7: Commit**

```bash
git add .env.example docs/gpu-pod.md
git commit -m "Serverless: biến cấu hình, runbook dựng endpoint, cách kiểm scale-to-zero"
```

---

## Những gì plan này cố tình KHÔNG làm

Ngân sách trần theo ngày · fallback tự động khi endpoint lỗi · autoscale theo độ dài hàng đợi ·
nhiều endpoint tách theo job type · concurrency > 1 trong một worker · presigned PUT cho output
>100MB (đã đo 17.7MB, xem spec §Quyết định 3) · dời api/Postgres/MinIO sang VPS (bước riêng, sau
khi serverless chứng minh chạy được).

Job lỗi cứ trả về `queued` và worker local nhặt — hành vi sẵn có, không phải code mới.
