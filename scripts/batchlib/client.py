"""Nói chuyện với API trên pod. Không biết pipeline là gì.

Chỉ stdlib: `requests` KHÔNG có trên máy dev (đo 18/08/2026) và thêm dependency
cho ba lời gọi HTTP là không đáng.

Hợp đồng API (motions-studio/api/src/routes/jobs.js):
  POST /jobs                 multipart: type, params(JSON), + file theo fieldname → 202 {id,…}
  GET  /jobs/<id>            → {status, progress, current_step, error, …}  (rowToDto, jobs.js:73-87)
  GET  /jobs/<id>/download   → bytes
Xác thực: header x-api-key ở cả ba.
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


class JobError(Exception):
    """Job hỏng, quá hạn, hoặc output không dùng được."""


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
    while now() < deadline:
        sleep(POLL_SECONDS)
        try:
            code, raw = _request(s, f"/jobs/{job_id}", timeout=30)
            data = json.loads(raw) if code == 200 else {}
        except (urllib.error.URLError, OSError, ValueError):
            # Rớt mạng KHÔNG được giết job đang chạy trên pod — nó vẫn chạy tiếp.
            misses += 1
            if misses > 30:
                raise JobError(f"mất liên lạc với API quá lâu; job {job_id} có thể vẫn đang chạy — "
                               f"chạy lại với RESUME=1")
            continue
        misses = 0
        if on_progress:
            on_progress(data)
        status = str(data.get("status") or "")
        if status == "done":
            return data
        if status in ("error", "cancelled"):
            raise JobError(f"job {job_id} {status}: {data.get('error') or 'không có lý do'}")
    raise JobError(
        f"job {job_id} chưa xong sau {timeout_min} phút. Nó VẪN đang chạy trên pod — "
        f"chạy lại với RESUME=1 để bắt tiếp, đừng submit job mới."
    )


def download_output(s: Settings, job_id: str, dest: Path, min_bytes: int) -> int:
    code, raw = _request(s, f"/jobs/{job_id}/download", timeout=600)
    if code != 200:
        raise JobError(f"tải output job {job_id} → {code}")
    if len(raw) < min_bytes:
        # Bẫy pod-smoke.sh dựng sàn để bắt: job báo done nhưng MinIO trả về gần rỗng.
        raise JobError(
            f"output job {job_id} chỉ {len(raw)} byte (cần ≥ {min_bytes}) — "
            f"job báo done nhưng MinIO không có gì dùng được"
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)
    return len(raw)
