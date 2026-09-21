"""Resumable chunked uploads (spec §5.1).

Cloudflare's free plan caps a request body at 100 MB, and phone uploads get
cut off, so a file arrives as numbered chunks that can be re-sent in any
order. Everything lives on disk under uploads_root/<id>/ — nothing is held in
memory beyond one READ_BLOCK, because the VPS has 1 GB of RAM.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import threading
import time
import uuid
from pathlib import Path

from control.materials import safe_name, stage_file
from control.paths import safe_child

CHUNK_SIZE = 32 * 1024 * 1024        # well under Cloudflare's 100 MB body cap
MAX_UPLOAD_BYTES = 2 * 1024 ** 3     # the local Telegram Bot API's own file limit
DISK_HEADROOM = 1024 ** 3            # left free for the bot, out/ and the journals
READ_BLOCK = 1024 * 1024

# assemble() must not run twice for one upload at once (a phone retrying a
# slow complete) — the second would find half-deleted chunks.
_ASSEMBLE_LOCK = threading.Lock()


class UploadError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def _dir(uploads_root: Path, upload_id: str) -> Path:
    d = safe_child(uploads_root, upload_id) if upload_id else None
    if d is None or not (d / "meta.json").is_file():
        raise UploadError("not_found", "no such upload")
    return d


def _meta(d: Path) -> dict:
    return json.loads((d / "meta.json").read_text(encoding="utf-8"))


def _chunks_total(size: int) -> int:
    return math.ceil(size / CHUNK_SIZE)


def open_upload(uploads_root: Path, file_name: str, size: int, *, free_bytes=None) -> dict:
    if not isinstance(file_name, str) or not file_name.strip():
        raise UploadError("bad_request", "file_name is required")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise UploadError("bad_request", "size must be a positive integer")
    if size > MAX_UPLOAD_BYTES:
        raise UploadError("too_large", f"files over {MAX_UPLOAD_BYTES} bytes are not accepted")
    uploads_root.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(uploads_root).free if free_bytes is None else free_bytes
    # Chunks and the assembled file coexist until assembly finishes: 2x.
    if free < 2 * size + DISK_HEADROOM:
        raise UploadError("no_space", "not enough free disk on the server for this file")
    upload_id = uuid.uuid4().hex
    d = uploads_root / upload_id
    d.mkdir()
    meta = {"file_name": safe_name(file_name), "size": size, "chunk_size": CHUNK_SIZE,
            "created_at": time.time()}
    (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return {"upload_id": upload_id, "chunk_size": CHUNK_SIZE, "chunks_total": _chunks_total(size)}


def expected_chunk_length(meta: dict, n: int) -> int:
    total = math.ceil(meta["size"] / meta["chunk_size"])
    if not 0 <= n < total:
        return -1
    return meta["chunk_size"] if n < total - 1 else meta["size"] - meta["chunk_size"] * (total - 1)


def write_chunk(uploads_root: Path, upload_id: str, n: int, stream, length: int) -> None:
    d = _dir(uploads_root, upload_id)
    if length != expected_chunk_length(_meta(d), n):
        raise UploadError("bad_request", f"chunk {n} must be exactly "
                          f"{expected_chunk_length(_meta(d), n)} bytes")
    tmp = d / f"{n:05d}.part.{uuid.uuid4().hex}.tmp"
    remaining = length
    try:
        with tmp.open("wb") as f:
            while remaining > 0:
                block = stream.read(min(READ_BLOCK, remaining))
                if not block:
                    raise UploadError("bad_request", f"chunk {n} ended early")
                f.write(block)
                remaining -= len(block)
        os.replace(tmp, d / f"{n:05d}.part")
    finally:
        tmp.unlink(missing_ok=True)


def _received(d: Path, meta: dict) -> list[int]:
    got = []
    for n in range(math.ceil(meta["size"] / meta["chunk_size"])):
        part = d / f"{n:05d}.part"
        if part.is_file() and part.stat().st_size == expected_chunk_length(meta, n):
            got.append(n)
    return got


def upload_status(uploads_root: Path, upload_id: str) -> dict:
    d = _dir(uploads_root, upload_id)
    meta = _meta(d)
    return {"upload_id": upload_id, "file_name": meta["file_name"], "size": meta["size"],
            "chunk_size": meta["chunk_size"],
            "chunks_total": math.ceil(meta["size"] / meta["chunk_size"]),
            "received": _received(d, meta)}


def assemble(uploads_root: Path, upload_id: str, dest_dir: Path) -> Path:
    with _ASSEMBLE_LOCK:
        d = _dir(uploads_root, upload_id)
        meta = _meta(d)
        total = math.ceil(meta["size"] / meta["chunk_size"])
        if len(_received(d, meta)) != total:
            raise UploadError("incomplete", "not every chunk has arrived; check GET /v1/uploads/{id}")
        combined = d / "assembled"
        with combined.open("wb") as out:
            for n in range(total):
                with (d / f"{n:05d}.part").open("rb") as part:
                    shutil.copyfileobj(part, out, READ_BLOCK)
        staged = stage_file(dest_dir, combined, meta["file_name"], move=True)
        shutil.rmtree(d, ignore_errors=True)
        return staged


def prune_uploads(uploads_root: Path, max_age_sec: float, now: float) -> list[str]:
    removed = []
    for d in uploads_root.iterdir() if uploads_root.is_dir() else []:
        try:
            created = _meta(d)["created_at"]
        except (OSError, ValueError, KeyError):
            created = d.stat().st_mtime            # unreadable meta: judge by the dir
        if now - created > max_age_sec:
            shutil.rmtree(d, ignore_errors=True)
            removed.append(d.name)
    return removed
