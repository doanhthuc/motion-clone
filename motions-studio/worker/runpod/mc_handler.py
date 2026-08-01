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
