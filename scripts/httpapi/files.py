"""File responses with single-range support, which AVPlayer needs to play and seek."""
from __future__ import annotations

import mimetypes
import os
import re
from http.server import BaseHTTPRequestHandler
from pathlib import Path

_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
_CHUNK = 1024 * 1024


def parse_range(header: str | None, size: int):
    if not header:
        return None
    m = _RANGE.match(header.strip())
    if not m or (not m.group(1) and not m.group(2)):
        return None                     # malformed or multi-range: whole file
    first, last = m.group(1), m.group(2)
    if not first:                       # suffix form: last N bytes
        n = int(last)
        if n == 0:
            return "unsatisfiable"
        return max(size - n, 0), size - 1
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start >= size or start > end:
        return "unsatisfiable"
    return start, end


def send_file(handler: BaseHTTPRequestHandler, path: Path) -> None:
    kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    # Open file first and get size from the descriptor to avoid TOCTTOU race
    # where path.stat().st_size and path.open("rb") could see different files
    with path.open("rb") as f:
        size = os.fstat(f.fileno()).st_size
        rng = parse_range(handler.headers.get("Range"), size)

        headers_sent = False
        try:
            if rng == "unsatisfiable":
                handler.send_response(416)
                handler.send_header("Content-Range", f"bytes */{size}")
                handler.send_header("Content-Length", "0")
                handler.end_headers()
                return

            start, end = rng if rng else (0, size - 1)
            length = end - start + 1 if size else 0
            handler.send_response(206 if rng else 200)
            handler.send_header("Content-Type", kind)
            handler.send_header("Accept-Ranges", "bytes")
            handler.send_header("Content-Length", str(length))
            if rng:
                handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            handler.end_headers()
            headers_sent = True

            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(_CHUNK, remaining))
                if not chunk:
                    # File ended before promised length: mark connection for close
                    handler.close_connection = True
                    break
                handler.wfile.write(chunk)
                remaining -= len(chunk)
        except ConnectionError:
            # Client disconnected (seek, timeout, network issue, headers flush) — normal.
            # Catch BrokenPipeError, ConnectionResetError, etc. Do not propagate.
            handler.close_connection = True
        except OSError as exc:
            if not headers_sent:
                raise   # e.g. before send_response — do_GET's normal error path handles it
            # A 200/206 is already on the wire, so do_GET must not also write
            # a second (500) response on top of it — that would corrupt the
            # stream. Best available response to a mid-read failure (e.g. EIO
            # from a flaky disk) is to close the connection and log it.
            handler.close_connection = True
            log = getattr(handler.server, "log", None)
            if log is not None:
                log(f"httpapi: send_file failed after headers were sent: {exc!r}")
