#!/usr/bin/env python3
# #region ALD 23/07/2026 - RunPod Serverless handler cho gói VVIP motion (video render KHÔNG xếp hàng).
"""Handler serverless cho VVIP: mỗi request = 1 job motion, render 1 lần rồi trả kết quả.

TÁI DÙNG nguyên pipeline `run_motion` trong worker_runtime/linux.py (KHÔNG viết lại) — chỉ monkeypatch
biên I/O để không phụ thuộc API .165:
  - api_download        → nạp input từ file cục bộ (handler tự tải trước từ URL do task-cloud cấp).
  - api_upload_output   → CHỤP đường dẫn mp4 cuối thay vì POST /jobs/:id/output về .165.
  - api_patch/log/progress/preview → đẩy tiến độ lên RunPod (best-effort), không gọi API .165.

Sau render: POST mp4 về task-cloud `/api/connector/tasks/<id>/results` (token provider `mtcw_...`) — y hệt
connector worker .165, tái dùng NGUYÊN path lưu trữ + email + phục vụ FE của task-cloud (KHÔNG cần MinIO ở RunPod).

Rule resolution VVIP: drv-10s → 720p (long-edge cap 1280); drv-15s/20s/30s → 540p (cap 968).

event["input"] = {
  id, ref(url), motion(url), preset(drv-10s|15s|20s|30s), quality?(auto theo preset),
  audioMode?(original|silent|replacement), params?{...tuỳ chọn}, output_prefix?
}
return = { ok, output_key, output_url, quality, seconds }   (ok=false → { ok:false, error })

⚠️ Yêu cầu: ComfyUI (WanVideoWrapper + Wan-Animate nodes/models) chạy sẵn tại COMFY_URL (entrypoint lo);
   handler chạy 1 job/lần (monkeypatch global KHÔNG an toàn nếu chạy song song trong 1 worker).
"""
import os, sys, time, uuid, shutil, tempfile, traceback, urllib.parse

# --- env PHẢI set TRƯỚC khi import worker_runtime.linux (module đọc env lúc load) ---
os.environ.setdefault("COMFY_URL", "http://127.0.0.1:8188")
os.environ.setdefault("API_URL", "http://127.0.0.1:9")      # không dùng (đã monkeypatch), giá trị vô hại
os.environ.setdefault("WORKER_TOKEN", "serverless")
os.environ.setdefault("MOTION_FRESH_COMFY", "0")            # box riêng → KHÔNG recycle ComfyUI trước Wan

import requests
import runpod

# đưa thư mục worker/ lên sys.path để import worker_runtime.linux
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import worker_runtime.linux as motion   # noqa: E402  (chạy module-level code của linux.py)

# ───────────────────────── Trả kết quả về task-cloud (connector endpoint, giống worker .165) ─────────────
# KHÔNG dùng MinIO ở RunPod: handler POST mp4 về POST /api/connector/tasks/<id>/results với token provider
# `mtcw_...` (task đã được dispatcher gán claimed_by = provider này → assertProviderOwnsTask pass). task-cloud
# lưu vào storage của nó + gửi email + set 'completed' — tái dùng NGUYÊN path connector hiện có.
# taskcloud_base + connector_token đến theo TỪNG request (dispatcher điều khiển), fallback về env nếu có.
TASKCLOUD_BASE_ENV   = os.environ.get("TASKCLOUD_BASE", "").rstrip("/")
CONNECTOR_TOKEN_ENV  = os.environ.get("CONNECTOR_TOKEN", "")

def _post_result_to_taskcloud(base, token, task_id, mp4_path, note=""):
    url = f"{base.rstrip('/')}/api/connector/tasks/{task_id}/results"
    with open(mp4_path, "rb") as f:
        r = requests.post(url, headers={"Authorization": f"Bearer {token}"},
                          files={"files": (os.path.basename(mp4_path), f, "video/mp4")},
                          data={"note": note}, timeout=600)
    r.raise_for_status()
    return r.json()

# ───────────────────────── ALD 24/07/2026 - Hậu kỳ Task Cloud cố định, nhẹ và ổn định ─────────────────────────
# Mọi video VVIP xuất Lanczos 1080p và giữ FPS nguồn. Không FlashVSR, ESRGAN, CAS hoặc RIFE.
ENHANCE_TARGET = "1080p"
ENHANCE_ENGINE = "lanczos"
ENHANCE_FPS = ""

# ───────────────────────── helpers ─────────────────────────
def _vvip_resolution(preset):
    # 10s → 720p (short 720, long-edge cap 1280); còn lại → 540p (short 544, cap 968). Đây là NGUỒN SỰ THẬT.
    return ("720p", 1280) if str(preset or "").strip() == "drv-10s" else ("540p", 968)

def _download(url, dest):
    with requests.get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1024 * 1024):
                if chunk:
                    f.write(chunk)
    return dest

def _ext_from_url(url, fallback):
    ext = os.path.splitext(urllib.parse.urlparse(url).path)[1]
    return ext if ext else fallback

_comfy_ready = False
def _ensure_comfy():
    """Chờ ComfyUI sẵn sàng 1 lần khi cold start (entrypoint đã khởi nền)."""
    global _comfy_ready
    if _comfy_ready:
        return
    timeout = float(os.environ.get("COMFY_BOOT_TIMEOUT", "600"))
    try:
        motion.comfy_wait_ready(timeout=timeout)
    except Exception:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if requests.get(motion.COMFY_URL + "/system_stats", timeout=5).ok:
                    break
            except Exception:
                time.sleep(3)
    _comfy_ready = True

# ───────────────────────── handler ─────────────────────────
def handler(event):
    job_in = event.get("input") or {}
    job_id = str(job_in.get("id") or uuid.uuid4())
    ref_url = job_in.get("ref") or job_in.get("image")
    motion_url = job_in.get("motion")
    if not ref_url or not motion_url:
        return {"ok": False, "error": "thiếu ref hoặc motion (URL input)"}

    preset = job_in.get("preset") or "drv-10s"
    quality, max_edge = _vvip_resolution(preset)   # rule VVIP thắng mọi giá trị client gửi

    _ensure_comfy()

    tmp = tempfile.mkdtemp(prefix=f"rp-{job_id[:8]}-")
    captured = {"path": None}
    t0 = time.time()

    # lưu bản gốc để khôi phục ở finally (worker serverless tái dùng process giữa các request)
    _orig = (motion.api_download, motion.api_upload_output,
             motion.api_patch, motion.api_log, motion.api_progress, motion.api_preview)

    def _dl(key, dest):
        shutil.copy(key, dest)   # inputs đã là path cục bộ handler đặt sẵn
        return dest

    def _up(job_id_, path, content_type="video/mp4", variant=None, label=None, final=True):
        captured["path"] = path
        return {"ok": True}

    def _progress(job_id_, p, step=""):
        try:
            runpod.serverless.progress_update(event, f"{int((p or 0) * 100)}% {step}")
        except Exception:
            pass

    def _noop(*a, **k):
        return None

    try:
        ref_local = os.path.join(tmp, "ref" + _ext_from_url(ref_url, ".png"))
        motion_local = os.path.join(tmp, "motion" + _ext_from_url(motion_url, ".mp4"))
        _download(ref_url, ref_local)
        _download(motion_url, motion_local)

        motion.api_download = _dl
        motion.api_upload_output = _up
        motion.api_patch = _noop
        motion.api_log = _noop
        motion.api_progress = _progress
        motion.api_preview = _noop

        params = dict(job_in.get("params") or {})
        params.update({
            "preset": preset,
            "quality": quality,
            "maxRenderEdge": max_edge,
            "max_render_edge": max_edge,
            "prepVram": False,
            "audioMode": params.get("audioMode") or job_in.get("audioMode") or "original",
        })
        # Task Cloud luôn hậu kỳ nhẹ bằng Lanczos 1080p, giữ nguyên FPS.
        do_enhance = True
        job = {"id": job_id, "inputs": {"ref": ref_local, "motion": motion_local}, "params": params}

        motion.run_motion(job)

        out = captured["path"]
        if not out or not os.path.exists(out):
            return {"ok": False, "error": "render xong nhưng không thu được file output"}

        # Node Enhance nhẹ: Lanczos 1080p + FPS gốc. Input = mp4 nền local
        # (api_download đã monkeypatch = copy). Lỗi enhance → GIAO BẢN GỐC, không mất video.
        if do_enhance:
            captured["path"] = None
            try:
                motion.run_enhance({
                    "id": job_id,
                    "inputs": {"input": out},
                    "params": {"mode": "video", "targetRes": ENHANCE_TARGET, "fpsInterp": ENHANCE_FPS, "engine": ENHANCE_ENGINE}
                })
                if captured["path"] and os.path.exists(captured["path"]):
                    out = captured["path"]
                else:
                    _progress(job_id, 0.99, "enhance không tạo output, giao bản nền")
            except Exception:
                traceback.print_exc()

        # Trả kết quả về task-cloud y như connector worker (POST /results) → tái dùng path lưu/email/FE.
        base = (job_in.get("taskcloud_base") or TASKCLOUD_BASE_ENV).rstrip("/")
        token = job_in.get("connector_token") or CONNECTOR_TOKEN_ENV
        if not base or not token:
            return {"ok": False, "error": "thiếu taskcloud_base hoặc connector_token để trả kết quả"}
        _note = "VVIP RunPod Lanczos 1080p · FPS gốc" if do_enhance else f"VVIP RunPod {quality}"
        _post_result_to_taskcloud(base, token, job_id, out, note=_note)

        return {"ok": True, "delivered": True, "enhanced": do_enhance,
                "quality": (ENHANCE_TARGET if do_enhance else quality),
                "seconds": round(time.time() - t0, 1)}
    except Exception as e:
        traceback.print_exc()
        return {"ok": False, "error": str(e)[:2000]}
    finally:
        (motion.api_download, motion.api_upload_output,
         motion.api_patch, motion.api_log, motion.api_progress, motion.api_preview) = _orig
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
# #endregion
