"""Shared worker loop for the Linux/CUDA runtime."""

from __future__ import annotations

import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

import requests


def run_worker_loop(
    *,
    log,
    poll,
    api_claim,
    api_patch,
    api_heartbeat,
    pipelines,
    before_claim=None,
    before_run=None,
    startup=None,
    unsupported_message=None,
    concurrency=1,
):
    """Claim jobs, dispatch them to pipeline handlers, and keep heartbeat alive.

    Platform runtimes provide callbacks for resource guards and pre-run logging.
    This keeps Linux and macOS dispatch behavior consistent while preserving their
    different config and pipeline implementations.

    ALD 12/06/2026 - chạy SONG SONG tối đa `concurrency` job (trước đây 1 job/lần, blocking):
    job chạy trong ThreadPoolExecutor, main loop chỉ heartbeat + claim khi còn slot. GPU vẫn
    an toàn vì mọi prompt đổ về ComfyUI được ComfyUI xếp hàng TUẦN TỰ nội bộ; cái song song
    được lợi là các bước ngoài GPU (download/upload, Ollama box khác, ffmpeg…) và queue được
    rút sớm. `api_claim(active_ids)` gửi kèm danh sách job đang chạy để API không reclaim oan
    (xem /worker/claim). `before_claim` (guard RAM) vẫn chặn nhận THÊM job khi tài nguyên thấp.
    """
    if startup:
        startup()
    conc = max(1, int(concurrency or 1))
    pool = ThreadPoolExecutor(max_workers=conc, thread_name_prefix="job")
    active = {}  # job_id -> Future
    lock = threading.Lock()

    def _run_job(job):
        jt = job.get("type")
        try:
            fn = pipelines.get(jt)
            if not fn:
                msg = (
                    unsupported_message(jt, pipelines)
                    if unsupported_message
                    else f"Chưa hỗ trợ type '{jt}' (worker có: {list(pipelines)})"
                )
                api_patch(job["id"], status="error", error=msg)
                return
            if before_run:
                before_run(job)
            fn(job)
            log(f"done {job['id'][:8]}")
        except Exception as e:
            log(f"job {job['id'][:8]} fail:", e)
            traceback.print_exc()
            api_patch(job["id"], status="error", error=str(e))
        finally:
            with lock:
                active.pop(job["id"], None)

    last_hb = 0
    while True:
        try:
            with lock:
                ids = list(active)
            if time.time() - last_hb > 15:
                # active_job_id chỉ là thông tin hiển thị badge FE → gửi job đầu tiên (hoặc None)
                api_heartbeat(ids[0] if ids else None)
                last_hb = time.time()

            if len(ids) >= conc:
                time.sleep(poll)
                continue

            if before_claim:
                delay = before_claim()
                if delay:
                    time.sleep(delay)
                    continue

            job = api_claim(ids)
            if not job:
                time.sleep(poll)
                continue

            jt = job.get("type")
            log(f"claimed {job['id'][:8]} type={jt} ({len(ids) + 1}/{conc} slot)")
            api_heartbeat(job["id"])
            last_hb = time.time()
            with lock:
                active[job["id"]] = pool.submit(_run_job, job)
        except requests.RequestException as e:
            log("loop net error:", e)
            time.sleep(min(30, poll * 3))
        except KeyboardInterrupt:
            break
