"""The control-plane HTTP API: a thin stdlib server over scripts/control/.

Runs as a daemon thread inside motion-bot (spec §3, approach A) so it sees the
same in-process run handles the bot does. Bound to 127.0.0.1; the only way in
from outside is the Cloudflare Tunnel, which also enforces an Access service
token before a request reaches this process (spec §4.4). The bearer check here
is the second, independent layer.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

import control.outputs as outputs
import control.runs as runs
from httpapi.files import send_file


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


NOT_FOUND = ApiError(404, "not_found", "no such resource")


class _Handler(BaseHTTPRequestHandler):
    server: "_Server"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):          # route stdlib access logs to our logger
        self.server.log("api: " + fmt % args)

    def do_GET(self):
        try:
            self._authenticate()
            self._route()
        except ApiError as exc:
            self._send_json(exc.status, {"error": {"code": exc.code, "message": exc.message}})
        except Exception:
            # Logged in full, returned opaque: the client gets no internals.
            self.server.log("api: request failed\n" + traceback.format_exc())
            self._send_json(500, {"error": {"code": "internal", "message": "internal error"}})

    def _authenticate(self) -> None:
        header = self.headers.get("Authorization", "")
        given = header[len("Bearer "):] if header.startswith("Bearer ") else ""
        if not given or not hmac.compare_digest(given.encode(), self.server.token.encode()):
            raise ApiError(401, "unauthorized", "missing or wrong bearer token")

    def _route(self) -> None:
        # Split on the RAW path before unquoting, so an encoded "/" (%2F) stays
        # inside one segment and is then refused by safe_child, instead of
        # becoming a separator that changes which route matched.
        parts = [unquote(p) for p in urlsplit(self.path).path.split("/")[1:]]
        if parts[:1] != ["v1"]:
            raise NOT_FOUND
        rest = parts[1:]
        s = self.server
        if rest == ["health"]:
            return self._send_json(200, {"ok": True})
        if rest == ["runs"]:
            return self._send_json(200, {"runs": runs.list_runs(s.batch_dir, s.out_dir)})
        if len(rest) == 2 and rest[0] == "runs":
            detail = runs.run_detail(s.batch_dir, s.out_dir, rest[1])
            if detail is None:
                raise NOT_FOUND
            return self._send_json(200, detail)
        if rest == ["outputs"]:
            return self._send_json(200, {"outputs": outputs.list_outputs(s.out_dir)})
        if len(rest) == 3 and rest[0] == "outputs":
            path = outputs.resolve_output(s.out_dir, rest[1], rest[2])
            if path is None:
                raise NOT_FOUND
            return send_file(self, path)
        raise NOT_FOUND

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        etag = '"' + hashlib.sha1(body).hexdigest() + '"'
        if status == 200 and self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if status == 200:
            self.send_header("ETag", etag)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


class _Server(ThreadingHTTPServer):
    daemon_threads = True


def make_server(*, token: str, batch_dir: Path, out_dir: Path,
                host: str = "127.0.0.1", port: int = 0, log=print) -> _Server:
    if not token:
        raise ValueError("an empty token would authenticate nothing")
    server = _Server((host, port), _Handler)
    server.token, server.batch_dir, server.out_dir, server.log = token, batch_dir, out_dir, log
    return server


def start_in_thread(server: _Server) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, name="control-api", daemon=True)
    thread.start()
    return thread
