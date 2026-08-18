"""Nói chuyện với API trên pod. Không biết pipeline là gì.

Chỉ stdlib: `requests` KHÔNG có trên máy dev (đo 18/08/2026) và thêm dependency
cho ba lời gọi HTTP là không đáng.

Hợp đồng API (motions-studio/api/src/routes/jobs.js):
  POST /jobs                 multipart: type, params(JSON), + file theo fieldname → 202 {id,…}
  GET  /jobs/<id>            → {status, progress, current_step, error, …}  (rowToDto, jobs.js:73-87)
  GET  /jobs/<id>/download   → bytes
Xác thực: header x-api-key ở cả ba.

Mã lỗi CÓ NGHĨA, không phải "chưa xong": 404 = hàng job không còn (jobs.js:154 — pod
dựng lại thì DB mới) · 401/403 = key sai (auth.js:7). Poll tiếp hai mã đó chỉ nhận lại
đúng nó, trong khi pod vẫn tính tiền — nên chúng là lỗi CHẾT NGAY, xem poll_job.
"""
from __future__ import annotations

import json
import mimetypes
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Callable

from .config import Settings

POLL_SECONDS = 10
# Trần số lần poll không dùng được liên tiếp. 30 × POLL_SECONDS ≈ 5 phút rớt mạng vẫn
# tha được — job trên pod không chết vì Wi-Fi của máy local.
MAX_POLL_MISSES = 30


class JobError(Exception):
    """Job hỏng, quá hạn, hoặc output không dùng được."""


class JobFailed(JobError):
    """Job đã CHẠY và HỎNG THẬT trên pod (status error/cancelled).

    Tách khỏi JobError để --resume phân biệt được hai chuyện KHÔNG giống nhau:
    job hỏng thật thì gửi lại là đúng, còn "quá hạn"/"mất liên lạc" thì job VẪN
    có thể đang chạy trên pod — gửi lại ở đó là trả tiền GPU hai lần cho cùng một
    việc (xem runner._try_reattach).
    """


class JobGone(JobError):
    """GET /jobs/<id> trả 404: hàng job không còn tồn tại.

    Xảy ra thật khi pod được dựng lại (DB mới, jobs.js:154 trả "Không tìm thấy").
    Cũng thuộc nhóm "gửi lại là đúng" như JobFailed, nhưng vì lý do khác — và khác
    hẳn 401/403 (key sai: poll tiếp hay gửi lại đều chỉ nhận đúng mã đó).
    """


def encode_multipart(fields: dict[str, str], files: dict[str, Path]) -> tuple[bytes, str]:
    boundary = f"----batchrunner{uuid.uuid4().hex}"
    sep = f"--{boundary}\r\n".encode()
    body = bytearray()
    for name, value in fields.items():
        body += sep
        body += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        body += f"{value}\r\n".encode()
    for name, path in files.items():
        filename = path.name
        ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        body += sep
        body += (f'Content-Disposition: form-data; name="{name}"; '
                 f'filename="{filename}"\r\n').encode()
        body += f"Content-Type: {ctype}\r\n\r\n".encode()
        body += path.read_bytes()
        body += b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def _request(s: Settings, path: str, *, data: bytes | None = None,
             content_type: str = "", timeout: int = 60) -> tuple[int, bytes]:
    req = urllib.request.Request(f"{s.base_url}{path}", data=data)
    req.add_header("x-api-key", s.api_key)
    if content_type:
        req.add_header("content-type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        with exc:
            return exc.code, exc.read()


def health_ok(s: Settings, timeout: int = 15) -> bool:
    try:
        code, _ = _request(s, "/health", timeout=timeout)
    except (urllib.error.URLError, OSError):
        return False
    return code == 200


def submit_job(s: Settings, job_type: str, params: dict, files: dict[str, Path]) -> str:
    fields = {"type": job_type, "params": json.dumps(params or {}, ensure_ascii=False)}
    body, ctype = encode_multipart(fields, files)
    code, raw = _request(s, "/jobs", data=body, content_type=ctype, timeout=300)
    if code not in (200, 202):
        raise JobError(f"POST /jobs → {code}: {raw[:300].decode('utf-8', 'replace')}")
    try:
        job_id = (json.loads(raw) or {}).get("id", "")
    except ValueError:
        job_id = ""
    if not job_id:
        raise JobError(f"POST /jobs không trả về id: {raw[:300].decode('utf-8', 'replace')}")
    return str(job_id)


def poll_job(s: Settings, job_id: str, timeout_min: int,
             on_progress: Callable[[dict], None] | None = None,
             sleep: Callable[[float], None] = time.sleep,
             now: Callable[[], float] = time.time) -> dict:
    deadline = now() + timeout_min * 60
    misses = 0

    def _miss(reason: str) -> None:
        """Một lần poll không dùng được. Rớt mạng / 500 / JSON hỏng KHÔNG được giết
        job đang chạy trên pod — nhưng cũng không được thử vô hạn: trước bản sửa
        này, mọi mã != 200 rơi vào `data = {}` và misses bị RESET, nên một 404 vĩnh
        viễn in "None 0%" mỗi 10 giây cho tới hết timeout của chặng (enhance: 90
        phút) trong khi pod tính tiền từng phút.
        """
        nonlocal misses
        misses += 1
        if misses > MAX_POLL_MISSES:
            raise JobError(
                f"mất liên lạc với API {misses} lần liên tiếp (lý do cuối: {reason}); "
                f"job {job_id} có thể vẫn đang chạy — chạy lại với RESUME=1 để bắt tiếp, "
                f"đừng submit job mới"
            )

    while now() < deadline:
        sleep(POLL_SECONDS)
        try:
            code, raw = _request(s, f"/jobs/{job_id}", timeout=30)
        except (urllib.error.URLError, OSError) as exc:
            _miss(f"{type(exc).__name__}: {exc}")
            continue

        body = raw[:300].decode("utf-8", "replace")
        if code == 404:
            # Hàng job biến mất (pod dựng lại → DB mới). Poll tiếp là vô nghĩa: không
            # có gì để bắt lại nữa. Ném riêng một lớp để runner tự quyết gửi job mới.
            raise JobGone(
                f"job {job_id} không còn trên pod (GET /jobs/{job_id} → 404: {body}). "
                f"Hàng job mất khi pod được dựng lại — không còn gì để bắt lại. "
                f"Chạy lại với RESUME=1: runner sẽ gửi job MỚI cho chặng này."
            )
        if code in (401, 403):
            raise JobError(
                f"API từ chối xác thực (GET /jobs/{job_id} → {code}: {body}). "
                f"API_KEY trong .env không khớp key của pod (key đổi khi dựng lại pod?) — "
                f"sửa .env rồi chạy lại với RESUME=1. Chờ thêm cũng chỉ nhận đúng {code}."
            )
        if code != 200:
            _miss(f"GET /jobs/{job_id} → {code}: {body}")
            continue

        try:
            data = json.loads(raw)
        except ValueError:
            _miss(f"trả lời không phải JSON: {body}")
            continue
        if not isinstance(data, dict):
            _miss(f"trả lời JSON không phải object: {body}")
            continue

        misses = 0
        if on_progress:
            on_progress(data)
        status = str(data.get("status") or "")
        if status == "done":
            return data
        if status in ("error", "cancelled"):
            raise JobFailed(f"job {job_id} {status}: {data.get('error') or 'không có lý do'}")
    raise JobError(
        f"job {job_id} chưa xong sau {timeout_min} phút. Nó VẪN đang chạy trên pod — "
        f"chạy lại với RESUME=1 để bắt tiếp, đừng submit job mới."
    )


def download_output(s: Settings, job_id: str, dest: Path, min_bytes: int) -> int:
    code, raw = _request(s, f"/jobs/{job_id}/download", timeout=600)
    if code != 200:
        raise JobError(
            f"tải output job {job_id} → {code}: {raw[:300].decode('utf-8', 'replace')} — "
            f"kiểm tra GET /jobs/{job_id} xem job đã thực sự ra output chưa trước khi thử lại"
        )
    if len(raw) < min_bytes:
        # Bẫy pod-smoke.sh dựng sàn để bắt: job báo done nhưng MinIO trả về gần rỗng.
        raise JobError(
            f"output job {job_id} chỉ {len(raw)} byte (cần ≥ {min_bytes}) — "
            f"job báo done nhưng MinIO không có gì dùng được"
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)
    return len(raw)
