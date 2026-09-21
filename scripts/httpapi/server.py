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

import control.materials as materials
import control.outputs as outputs
import control.runs as runs
import control.uploads as uploads
from httpapi.files import send_file


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


NOT_FOUND = ApiError(404, "not_found", "no such resource")

_DOMAIN_STATUS = {"bad_request": 400, "forbidden": 403, "not_found": 404, "in_use": 409,
                  "incomplete": 409, "too_large": 413, "unprobeable": 422, "no_space": 507}
MAX_JSON_BODY = 64 * 1024


class _Handler(BaseHTTPRequestHandler):
    server: "_Server"
    protocol_version = "HTTP/1.1"
    # socketserver's per-request socket timeout. Without it, a keep-alive
    # connection that goes idle (or a client that stops reading mid-response)
    # holds a ThreadingHTTPServer worker thread forever.
    timeout = 60

    def log_message(self, fmt, *args):          # route stdlib access logs to our logger
        # AVPlayer issues many Range requests per playback (one per seek/
        # chunk); logging every 200/206 on /v1/outputs/<batch>/<file> would
        # flood the journal with routine seek traffic. log_request's default
        # call is log_message('"%s" %s %s', requestline, code, size), so
        # args[1] is the status code.
        if len(args) >= 2 and args[1] in ("200", "206"):
            parts = urlsplit(self.path).path.split("/")
            if len(parts) == 5 and parts[1] == "v1" and parts[2] == "outputs":
                return
        self.server.log("api: " + fmt % args)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def do_DELETE(self):
        self._handle("DELETE")

    def _handle(self, method: str) -> None:
        try:
            self._authenticate()
            self._route(method)
        except ApiError as exc:
            self._error(exc.status, exc.code, exc.message)
        except (uploads.UploadError, materials.MaterialError) as exc:
            self._error(_DOMAIN_STATUS.get(exc.code, 400), exc.code, exc.message)
        except Exception:
            # Logged in full, returned opaque: the client gets no internals.
            self.server.log("api: request failed\n" + traceback.format_exc())
            self._error(500, "internal", "internal error")

    def _error(self, status: int, code: str, message: str) -> None:
        # A request that carried a body may not have been read to the end; the
        # leftover bytes would be parsed as the next request on this connection.
        if self.command in ("POST", "PUT", "DELETE"):
            self.close_connection = True
        self._send_json(status, {"error": {"code": code, "message": message}})

    def _content_length(self) -> int:
        raw = self.headers.get("Content-Length")
        if raw is None:
            raise ApiError(411, "length_required", "Content-Length is required")
        try:
            n = int(raw)
        except ValueError:
            raise ApiError(400, "bad_request", "bad Content-Length")
        if n < 0:
            raise ApiError(400, "bad_request", "bad Content-Length")
        return n

    def _read_json(self) -> dict:
        n = self._content_length()
        if n > MAX_JSON_BODY:
            raise ApiError(413, "too_large", "JSON body too large")
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            raise ApiError(400, "bad_request", "body is not valid JSON")
        if not isinstance(data, dict):
            raise ApiError(400, "bad_request", "body must be a JSON object")
        return data

    def _authenticate(self) -> None:
        header = self.headers.get("Authorization", "")
        given = header[len("Bearer "):] if header.startswith("Bearer ") else ""
        if not given or not hmac.compare_digest(given.encode(), self.server.token.encode()):
            raise ApiError(401, "unauthorized", "missing or wrong bearer token")

    def _route(self, method: str) -> None:
        # Split on the RAW path before unquoting, so an encoded "/" (%2F) stays
        # inside one segment and is then refused by safe_child, instead of
        # becoming a separator that changes which route matched.
        parts = [unquote(p) for p in urlsplit(self.path).path.split("/")[1:]]
        if parts[:1] != ["v1"]:
            raise NOT_FOUND
        rest = parts[1:]
        s = self.server
        if method == "GET":
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
                try:
                    return send_file(self, path)
                except FileNotFoundError:
                    raise NOT_FOUND
        if method == "POST" and rest == ["uploads"]:
            body = self._read_json()
            return self._send_json(201, uploads.open_upload(
                s.uploads_root, body.get("file_name"), body.get("size")))
        if method == "PUT" and len(rest) == 4 and rest[0] == "uploads" and rest[2] == "chunks":
            try:
                n = int(rest[3])
            except ValueError:
                raise NOT_FOUND
            length = self._content_length()
            if length > uploads.CHUNK_SIZE:
                raise ApiError(413, "too_large", "chunk larger than chunk_size")
            uploads.write_chunk(s.uploads_root, rest[1], n, self.rfile, length)
            return self._send_json(200, {"received": n})
        if method == "GET" and len(rest) == 2 and rest[0] == "uploads":
            return self._send_json(200, uploads.upload_status(s.uploads_root, rest[1]))
        if method == "POST" and len(rest) == 3 and rest[0] == "uploads" and rest[2] == "complete":
            staged = uploads.assemble(s.uploads_root, rest[1], s.staging_root / materials.APP_OWNER)
            try:
                final, probe = materials.ingest(staged)
            except materials.MaterialError:
                # Unreadable material must not sit in the list looking usable.
                # A HEIC may already have produced its PNG twin before probe failed.
                staged.unlink(missing_ok=True)
                if staged.suffix.lower() in (".heic", ".heif"):
                    staged.with_suffix(".png").unlink(missing_ok=True)
                raise
            item = next(m for m in materials.list_materials(s.staging_root)
                        if m["id"] == f"{materials.APP_OWNER}/{final.name}")
            return self._send_json(201, {"material": item, "probe": probe})
        if method == "GET" and rest == ["materials"]:
            return self._send_json(200, {"materials": materials.list_materials(s.staging_root)})
        if method == "GET" and len(rest) == 4 and rest[0] == "materials" and rest[3] == "thumb":
            thumb = materials.thumbnail(s.staging_root, s.thumbs_root, rest[1], rest[2])
            try:
                return send_file(self, thumb)
            except FileNotFoundError:
                raise NOT_FOUND
        if method == "DELETE" and len(rest) == 3 and rest[0] == "materials":
            materials.delete_material(s.staging_root, s.batch_dir, rest[1], rest[2])
            return self._send_empty(204)
        raise NOT_FOUND

    def _etag_matches(self, etag: str) -> bool:
        # Cloudflare (and other proxies) can rewrite a strong ETag to a weak
        # one (`W/"..."`) when compressing the response, and a client may
        # send several candidates comma-separated. RFC 7232's weak comparison
        # only requires the opaque tag to match, so without stripping the
        # W/ prefix here, 304 would never fire once any such proxy sits in
        # front of this server.
        header = self.headers.get("If-None-Match")
        if not header:
            return False
        for candidate in header.split(","):
            candidate = candidate.strip()
            if candidate.startswith("W/"):
                candidate = candidate[2:].strip()
            if candidate == "*" or candidate == etag:
                return True
        return False

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        etag = '"' + hashlib.sha1(body).hexdigest() + '"'
        if status == 200 and self._etag_matches(etag):
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
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _send_empty(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()


class _Server(ThreadingHTTPServer):
    daemon_threads = True


def make_server(*, token: str, batch_dir: Path, out_dir: Path,
                host: str = "127.0.0.1", port: int = 0, log=print) -> _Server:
    if not token:
        raise ValueError("an empty token would authenticate nothing")
    server = _Server((host, port), _Handler)
    server.token, server.batch_dir, server.out_dir, server.log = token, batch_dir, out_dir, log
    server.staging_root = batch_dir / "tg-staging"
    server.uploads_root = batch_dir / "uploads"
    server.thumbs_root = batch_dir / "thumbs"
    return server


def start_in_thread(server: _Server) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, name="control-api", daemon=True)
    thread.start()
    return thread
