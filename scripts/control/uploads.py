"""Resumable chunked uploads (spec §5.1).

Cloudflare's free plan caps a request body at 100 MB, and phone uploads get
cut off, so a file arrives as numbered chunks that can be re-sent in any
order. Everything lives on disk under uploads_root/<id>/ — nothing is held in
memory beyond one READ_BLOCK, because the VPS has 1 GB of RAM.

Files inside one upload directory:

    meta.json     file_name, size, chunk_size, created_at
    NNNNN.part    a received chunk (plus NNNNN.part.<hex>.tmp while it streams)
    assembling    claim marker: the token of the process assembling right now
    assembled     the chunks concatenated, before they are moved into staging
    staged.json   where the assembled file is about to land / has landed
    done.json     the response `complete` returned, replayed to a retry
"""
from __future__ import annotations

import errno
import json
import math
import os
import shutil
import threading
import time
import uuid
from pathlib import Path

from control import materials
from control.paths import safe_child

CHUNK_SIZE = 32 * 1024 * 1024        # well under Cloudflare's 100 MB body cap
MAX_UPLOAD_BYTES = 2 * 1024 ** 3     # the local Telegram Bot API's own file limit
DISK_HEADROOM = 1024 ** 3            # left free for the bot, out/ and the journals
READ_BLOCK = 1024 * 1024
# 8 unfinished uploads at 2 GiB each already reserve more than the 25 GB disk
# can spare, so the cap is what makes the reservation below refusable rather
# than a promise the disk cannot keep. It is also far more than the app opens:
# it uploads one file at a time.
MAX_OPEN_UPLOADS = 8
# ext4 allows 255 bytes per component and the staged name also carries a
# "-<counter>" collision suffix; 200 leaves room and keeps a hostile 100 KB
# file_name out of meta.json.
MAX_FILE_NAME_BYTES = 200

ASSEMBLING = "assembling"
STAGED = "staged.json"
DONE = "done.json"

# Guards the atomic steps only — claiming an upload for assembly, the final
# rename of a chunk, the age check in prune_uploads — so a chunk re-sent during
# assembly or a prune does not raise FileNotFoundError. The slow work (copying
# up to 2 GiB, ffprobe) runs OUTSIDE it (spec §4.3): it used to run inside,
# which blocked every other upload's rename and the bot's daily prune, and the
# prune runs on the poll thread, i.e. it blocked Telegram.
_LOCK = threading.Lock()

# An `assembling` marker only means "being assembled" while the process that
# wrote it is still alive. Without this, a bot restart mid-assembly left a
# marker that made every retry a 409 until the 24 h prune removed the upload.
_PROCESS_TOKEN = uuid.uuid4().hex


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


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".writing")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)          # a reader never sees half a record


def _chunks_total(size: int) -> int:
    return math.ceil(size / CHUNK_SIZE)


def _total(meta: dict) -> int:
    return math.ceil(meta["size"] / meta["chunk_size"])


def _assembling(d: Path) -> bool:
    """True only while THIS process is assembling this upload."""
    try:
        return (d / ASSEMBLING).read_text(encoding="utf-8").strip() == _PROCESS_TOKEN
    except OSError:
        return False


def _payload_bytes(d: Path) -> int:
    """Bytes of the upload's own data on disk (chunks, in-flight parts, copy)."""
    total = 0
    for f in d.iterdir():
        if f.name in ("meta.json", ASSEMBLING, STAGED, DONE):
            continue
        try:
            total += f.stat().st_size
        except OSError:                     # renamed or pruned mid-scan
            continue
    return total


def _owed(d: Path) -> int:
    """Disk this upload may still consume: chunks not yet sent plus its copy."""
    try:
        size = _meta(d)["size"]
    except (OSError, ValueError, KeyError):
        return 0
    return max(0, 2 * size - _payload_bytes(d))


def open_upload(uploads_root: Path, file_name: str, size: int, *, free_bytes=None) -> dict:
    if not isinstance(file_name, str) or not file_name.strip():
        raise UploadError("bad_request", "file_name is required")
    if len(file_name.encode("utf-8", "surrogatepass")) > MAX_FILE_NAME_BYTES:
        raise UploadError("bad_request", f"file_name must be at most {MAX_FILE_NAME_BYTES} bytes")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise UploadError("bad_request", "size must be a positive integer")
    if size > MAX_UPLOAD_BYTES:
        raise UploadError("too_large", f"files over {MAX_UPLOAD_BYTES} bytes are not accepted")
    uploads_root.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        # The check has to account for what the uploads already in flight still
        # OWE the disk, not just what they have written: the free-space check
        # ran once per upload with no reservation, so three concurrent 2 GiB
        # uploads each saw the same 5 GB free and all three were accepted.
        owed, open_count = 0, 0
        for other in uploads_root.iterdir():
            if not other.is_dir() or (other / DONE).is_file():
                continue           # a finished upload's bytes live in staging now
            open_count += 1
            owed += _owed(other)
        if open_count >= MAX_OPEN_UPLOADS:
            raise UploadError("too_many", "too many unfinished uploads; complete or wait "
                                          "for them to expire")
        free = shutil.disk_usage(uploads_root).free if free_bytes is None else free_bytes
        # Chunks and the assembled file coexist until assembly finishes: 2x.
        if free < owed + 2 * size + DISK_HEADROOM:
            raise UploadError("no_space", "not enough free disk on the server for this file")
        upload_id = uuid.uuid4().hex
        d = uploads_root / upload_id
        d.mkdir()
        meta = {"file_name": materials.safe_name(file_name), "size": size,
                "chunk_size": CHUNK_SIZE, "created_at": time.time()}
        (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return {"upload_id": upload_id, "chunk_size": CHUNK_SIZE, "chunks_total": _chunks_total(size)}


def expected_chunk_length(meta: dict, n: int) -> int:
    total = _total(meta)
    if not 0 <= n < total:
        return -1
    return meta["chunk_size"] if n < total - 1 else meta["size"] - meta["chunk_size"] * (total - 1)


def _refuse_if_claimed(d: Path) -> None:
    if (d / DONE).is_file():
        raise UploadError("conflict", "upload is already complete")
    if _assembling(d):
        raise UploadError("conflict", "upload is being assembled")


def write_chunk(uploads_root: Path, upload_id: str, n: int, stream, length: int) -> None:
    d = _dir(uploads_root, upload_id)
    meta = _meta(d)
    if length != expected_chunk_length(meta, n):
        raise UploadError("bad_request", f"chunk {n} must be exactly "
                          f"{expected_chunk_length(meta, n)} bytes")
    _refuse_if_claimed(d)
    # A second floor under open_upload's reservation: an upload opened when the
    # disk was empty must still not be the thing that fills it (out/ grows
    # ~75 MB/day underneath it, measured 2026-09-18).
    if shutil.disk_usage(uploads_root).free < DISK_HEADROOM + length:
        raise UploadError("no_space", "not enough free disk on the server for this chunk")
    tmp = d / f"{n:05d}.part.{uuid.uuid4().hex}.tmp"
    remaining = length
    try:
        # Stream to .tmp OUTSIDE the lock so slow uploads don't block others.
        # If the upload dir is removed (e.g. a prune) or disk fills up,
        # catch OSError and distinguish the cause.
        try:
            with tmp.open("wb") as f:
                while remaining > 0:
                    block = stream.read(min(READ_BLOCK, remaining))
                    if not block:
                        raise UploadError("bad_request", f"chunk {n} ended early")
                    f.write(block)
                    remaining -= len(block)
        except OSError as e:
            # A phone that goes through a tunnel dies mid-body often enough to
            # be routine: the socket read raises TimeoutError (the handler's
            # 60 s timeout) or ConnectionResetError. Both are OSErrors with no
            # errno, so they have to be recognised by type before the errno
            # checks below, or they became an opaque 500 with a traceback.
            if isinstance(e, (TimeoutError, ConnectionError)):
                raise UploadError("bad_request", f"chunk {n} upload interrupted; re-send it") from e
            if e.errno == errno.ENOSPC:
                # Disk full: tell the phone to free space and retry this chunk
                raise UploadError("no_space", "server ran out of disk while receiving this chunk; free space and retry it") from e
            # Check if the upload directory (or its meta.json) no longer exists
            if not d.is_dir() or not (d / "meta.json").is_file():
                raise UploadError("not_found", "no such upload") from e
            # Other OSError: re-raise to propagate upward
            raise
        # Atomic rename under lock: re-check the upload is still writable, then
        # replace. `complete` claims the upload under the same lock, so a chunk
        # can never land inside a file that is already being assembled.
        with _LOCK:
            if not (d / "meta.json").is_file():
                raise UploadError("not_found", "no such upload")
            _refuse_if_claimed(d)
            os.replace(tmp, d / f"{n:05d}.part")
    finally:
        tmp.unlink(missing_ok=True)


def _received(d: Path, meta: dict) -> list[int]:
    got = []
    for n in range(_total(meta)):
        part = d / f"{n:05d}.part"
        if part.is_file() and part.stat().st_size == expected_chunk_length(meta, n):
            got.append(n)
    return got


def upload_status(uploads_root: Path, upload_id: str) -> dict:
    d = _dir(uploads_root, upload_id)
    meta = _meta(d)
    total = _total(meta)
    status = {"upload_id": upload_id, "file_name": meta["file_name"], "size": meta["size"],
              "chunk_size": meta["chunk_size"], "chunks_total": total,
              "received": _received(d, meta)}
    done = _read_json(d / DONE)
    if done is not None:
        # The chunks are gone, but the upload did arrive whole — say so, and
        # hand back which material it became so a client that lost the 201
        # can carry on without a second POST.
        status["received"] = list(range(total))
        status.update(done)
    return status


def _assemble(d: Path, meta: dict, dest_dir: Path) -> Path:
    combined = d / "assembled"
    try:
        with combined.open("wb") as out:
            for n in range(_total(meta)):
                with (d / f"{n:05d}.part").open("rb") as part:
                    shutil.copyfileobj(part, out, READ_BLOCK)
        # The staged name is written down BEFORE the move claims it, while
        # stage_file still holds the name: a crash between the move and
        # done.json would otherwise leave the chunks in place and the retry
        # would assemble a second copy next to the first (clip-1.mp4).
        return materials.stage_file(dest_dir, combined, meta["file_name"], move=True,
                                    reserve=lambda dest: _write_json(d / STAGED,
                                                                     {"path": str(dest)}))
    except OSError as e:
        # Clean up the partial assembled file and the name record; chunks stay
        # for retry.
        combined.unlink(missing_ok=True)
        (d / STAGED).unlink(missing_ok=True)
        if e.errno == errno.ENOSPC:
            raise UploadError("no_space", "server ran out of disk while assembling; free space and retry complete")
        raise


def _already_staged(d: Path) -> Path | None:
    """The file a previous `complete` already moved into staging, if any."""
    record = _read_json(d / STAGED)
    path = Path(record["path"]) if isinstance(record, dict) and record.get("path") else None
    if path is None:
        return None
    # `assembled` is consumed by the move, so its absence is what says the move
    # happened. If it is still there the record is stale (the move itself
    # failed) and assembly starts over.
    if not (d / "assembled").exists() and path.is_file():
        return path
    (d / STAGED).unlink(missing_ok=True)
    return None


def _ingest(d: Path, staged: Path) -> dict:
    try:
        final, probe = materials.ingest(staged)
    except materials.MaterialError:
        # Unreadable material must not sit in the list looking usable, and
        # re-sending the same bytes cannot fix it, so the upload goes too.
        # A HEIC may already have produced its PNG twin before probe failed.
        staged.unlink(missing_ok=True)
        if staged.suffix.lower() in (".heic", ".heif"):
            staged.with_suffix(".png").unlink(missing_ok=True)
        shutil.rmtree(d, ignore_errors=True)
        raise
    try:
        item = materials.material_item(materials.APP_OWNER, final)
    except FileNotFoundError:
        # Vanished between ingest and here (a concurrent DELETE, a prune) —
        # the upload itself succeeded, but there is nothing left to hand back
        # as "the material", and nothing to replay either.
        shutil.rmtree(d, ignore_errors=True)
        raise UploadError("not_found", "the material was removed before it could be returned")
    return {"material": item, "probe": probe}


def complete(uploads_root: Path, upload_id: str, staging_root: Path) -> dict:
    """Assemble, stage and ingest an upload; return the material response.

    Idempotent: the response is journalled to done.json, so a retry after a
    lost 201 (a phone on a flaky link) replays it instead of getting a 404 for
    an upload that is already gone, and the app can still learn which material
    its bytes became.
    """
    with _LOCK:
        d = _dir(uploads_root, upload_id)
        done = _read_json(d / DONE)
        if done is not None:
            return done
        if _assembling(d):
            raise UploadError("conflict", "upload is being assembled")
        meta = _meta(d)
        staged = _already_staged(d)
        if staged is None and len(_received(d, meta)) != _total(meta):
            raise UploadError("incomplete", "not every chunk has arrived; check GET /v1/uploads/{id}")
        # Claiming is the only part that needs the lock; everything below can
        # take minutes for a 2 GiB file.
        (d / ASSEMBLING).write_text(_PROCESS_TOKEN, encoding="utf-8")
    try:
        if staged is None:
            staged = _assemble(d, meta, staging_root / materials.APP_OWNER)
        response = _ingest(d, staged)
        _write_json(d / DONE, response)
        # meta.json and done.json are all a retry (or a GET) needs; the chunks,
        # the copy and the name record are ~2x the file and go now. prune_uploads
        # ages the rest out as usual.
        for leftover in d.iterdir():
            if leftover.name not in ("meta.json", DONE):
                leftover.unlink(missing_ok=True)
        return response
    finally:
        # A failure must leave the upload claimable again, or the retry the
        # error message asks for would answer 409 forever.
        (d / ASSEMBLING).unlink(missing_ok=True)


def _newest_activity(d: Path, created: float) -> float:
    newest = created
    for f in d.iterdir():
        try:
            newest = max(newest, f.stat().st_mtime)
        except OSError:
            continue
    return newest


def prune_uploads(uploads_root: Path, max_age_sec: float, now: float) -> list[str]:
    removed = []
    for d in uploads_root.iterdir() if uploads_root.is_dir() else []:
        # Skip non-directories (e.g. stray files under uploads_root).
        if not d.is_dir():
            continue
        with _LOCK:
            # Re-check that the directory still exists (may have been removed concurrently).
            if not d.is_dir():
                continue
            try:
                marker_age = now - (d / ASSEMBLING).stat().st_mtime
            except OSError:
                marker_age = None
            if marker_age is not None and marker_age <= max_age_sec:
                continue          # being assembled right now: rmtree would break it
            try:
                created = _meta(d)["created_at"]
            except (OSError, ValueError, KeyError):
                created = d.stat().st_mtime            # unreadable meta: judge by the dir
            # Judge by the NEWEST activity, not created_at: a 2 GiB upload over
            # a phone link can take longer than the whole max age, and pruning
            # it out from under chunks that are still arriving throws away the
            # part that already made it.
            if now - _newest_activity(d, created) > max_age_sec:
                shutil.rmtree(d, ignore_errors=True)
                removed.append(d.name)
    return removed
