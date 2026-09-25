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
from urllib.parse import parse_qs, unquote, urlsplit

import control.drafts as drafts
import control.materials as materials
import control.outputs as outputs
import control.runs as runs
import control.tryon_library as tryon_library
import control.uploads as uploads
from control.idempotency import IdempotencyError
from httpapi.files import send_file


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


NOT_FOUND = ApiError(404, "not_found", "no such resource")

_DOMAIN_STATUS = {"bad_request": 400, "forbidden": 403, "not_found": 404, "in_use": 409,
                  "incomplete": 409, "conflict": 409, "too_many": 409, "busy": 409, "too_large": 413,
                  "unprobeable": 422, "no_space": 507,
                  "unknown_pipeline": 422, "unknown_provider": 422, "unknown_role": 422,
                  "wrong_kind": 422, "not_applicable": 422, "missing_slots": 422,
                  # DraftStore.patch's refusal of a tryon_seed on a job whose
                  # try-on does not run locally: a semantic no, like its
                  # siblings above — without this it fell through to 400.
                  "not_local": 422,
                  # DraftStore.patch's tryon_seed naming a library entry that was
                  # deleted: 404 like not_found, but its own code so the phone
                  # does not mistake it for a stale material and reload them.
                  "seed_not_found": 404,
                  "duplicate": 422, "nothing_to_validate": 422, "invalid": 422}
MAX_JSON_BODY = 64 * 1024
# A body this size or smaller is read and thrown away to keep the connection
# usable; anything bigger is not worth reading, so the connection is closed
# instead. Same limit as MAX_JSON_BODY: nothing this API accepts is larger.
MAX_DRAIN_BODY = 64 * 1024


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

    def do_PATCH(self):
        self._handle("PATCH")

    def _handle(self, method: str) -> None:
        # Routes that answer without reading the body (DELETE /v1/materials/…,
        # POST …/complete) left whatever the client sent in the socket, and the
        # next request on the same keep-alive connection was parsed starting
        # from those bytes: a DELETE with body `{}` followed by a GET answered
        # 501 "Unsupported method ('{}GET')" (reproduced 2026-09-21). Every
        # response path settles the body first — see _settle_body.
        self._body_consumed = False
        try:
            self._authenticate()
            self._route(method)
        except ApiError as exc:
            self._error(exc.status, exc.code, exc.message)
        except (uploads.UploadError, materials.MaterialError, drafts.DraftError,
                IdempotencyError) as exc:
            self._error(_DOMAIN_STATUS.get(exc.code, 400), exc.code, exc.message)
        except Exception:
            # Logged in full, returned opaque: the client gets no internals.
            self.server.log("api: request failed\n" + traceback.format_exc())
            self._error(500, "internal", "internal error")

    def _error(self, status: int, code: str, message: str) -> None:
        # A request that carried a body may not have been read to the end (and,
        # for a chunk PUT, may have been read only halfway); the leftover bytes
        # would be parsed as the next request on this connection.
        if self.command in ("POST", "PUT", "PATCH", "DELETE"):
            self.close_connection = True
        self._send_json(status, {"error": {"code": code, "message": message}})

    def _settle_body(self) -> None:
        """Make the connection safe to reuse before a response goes out."""
        if self._body_consumed or self.close_connection:
            return
        self._body_consumed = True
        raw = self.headers.get("Content-Length")
        try:
            n = int(raw) if raw else 0
        except ValueError:
            n = -1
        if n <= 0:
            self.close_connection = n < 0        # unparseable: don't guess where it ends
            return
        if n > MAX_DRAIN_BODY:
            self.close_connection = True
            return
        # A client that promised more than it sent would block this read until
        # the handler's own socket timeout, so assume the worst until it lands.
        self.close_connection = True
        try:
            self.rfile.read(n)
        except OSError:
            # This runs from inside _handle's except clauses (via _error), so
            # nothing upstream catches an exception raised here — it would
            # escape to socketserver's default handler and print a raw
            # traceback instead of going through self.server.log. A stalled
            # client (TimeoutError/socket.timeout) or a dropped one
            # (ConnectionError) is exactly a connection we must not reuse, so
            # log it and return with close_connection already True.
            self.server.log("api: settle body failed, closing connection\n" + traceback.format_exc())
            return
        self.close_connection = False

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
        raw = self.rfile.read(n)
        self._body_consumed = True
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            raise ApiError(400, "bad_request", "body is not valid JSON")
        if not isinstance(data, dict):
            raise ApiError(400, "bad_request", "body must be a JSON object")
        return data

    def _app_runs(self):
        """The `AppRuns` instance the bot builds in `_start_control_api`, or
        the 503 every route in this section answers when the bot started
        before the idempotency store existed (or the phone API is otherwise
        not wired up) — never an AttributeError that would 500 instead."""
        app_runs = self.server.app_runs
        if app_runs is None:
            raise ApiError(503, "runs_unavailable", "the app's run routes are not available")
        return app_runs

    def _app_pod(self):
        """The `AppPod` the bot builds in `_start_control_api`, or the 503
        every pod route answers when it is not wired — the same shape and
        reason as `_app_runs`, with its own code so the phone can tell "no
        runs" from "no pod"."""
        app_pod = self.server.app_pod
        if app_pod is None:
            raise ApiError(503, "pod_unavailable", "the app's pod routes are not available")
        return app_pod

    def _idempotency_key(self) -> str:
        # Checked here, before any AppRuns method runs, so a missing key
        # never reaches — and never spends — anything: the shape check on a
        # key that IS present still happens inside IdempotencyStore.begin.
        key = self.headers.get("Idempotency-Key")
        if not key:
            raise ApiError(400, "bad_request", "Idempotency-Key is required")
        return key

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
            if len(rest) == 4 and rest[0] == "outputs" and rest[3] == "poster":
                path = outputs.poster(s.out_dir, rest[1], rest[2])
                try:
                    self._settle_body()
                    return send_file(self, path)
                except FileNotFoundError:
                    raise NOT_FOUND
            if len(rest) == 3 and rest[0] == "outputs":
                path = outputs.resolve_output(s.out_dir, rest[1], rest[2])
                if path is None:
                    raise NOT_FOUND
                try:
                    self._settle_body()
                    return send_file(self, path)
                except FileNotFoundError:
                    raise NOT_FOUND
        if method == "POST" and rest == ["runs", "phase-a"]:
            app_runs = self._app_runs()
            key = self._idempotency_key()
            status, body = app_runs.phase_a(key)
            return self._send_json(status, body)
        if method == "GET" and len(rest) == 3 and rest[0] == "runs" and rest[2] == "rent-panel":
            force = parse_qs(urlsplit(self.path).query).get("force", ["0"])[0] == "1"
            status, body = self._app_runs().rent_panel(rest[1], force=force)
            return self._send_json(status, body)
        if method == "POST" and len(rest) == 3 and rest[0] == "runs" and rest[2] == "confirm":
            app_runs = self._app_runs()
            key = self._idempotency_key()
            payload = self._read_json()
            status, body = app_runs.confirm(rest[1], payload, key)
            return self._send_json(status, body)
        if method == "GET" and len(rest) == 3 and rest[0] == "runs" and rest[2] == "tryon":
            status, body = self._app_runs().tryon(rest[1])
            return self._send_json(status, body)
        if method == "GET" and len(rest) == 4 and rest[0] == "runs" and rest[2] == "tryon":
            image = self._app_runs().tryon_image(rest[1], rest[3])
            if image is None:
                raise NOT_FOUND
            try:
                self._settle_body()
                return send_file(self, image)
            except FileNotFoundError:
                raise NOT_FOUND
        if (method == "GET" and len(rest) == 6 and rest[0] == "runs" and rest[2] == "tryon"
                and rest[4] == "versions"):
            image = self._app_runs().tryon_version_image(rest[1], rest[3], rest[5])
            if image is None:
                raise NOT_FOUND
            try:
                self._settle_body()
                return send_file(self, image)
            except FileNotFoundError:
                raise NOT_FOUND
        if (method == "POST" and len(rest) == 5 and rest[0] == "runs" and rest[2] == "tryon"
                and rest[4] == "regen"):
            app_runs = self._app_runs()
            key = self._idempotency_key()
            payload = self._read_json()
            status, body = app_runs.regen(rest[1], rest[3], payload, key)
            return self._send_json(status, body)
        if method == "POST" and len(rest) == 3 and rest[0] == "runs" and rest[2] == "kill":
            app_pod = self._app_pod()
            key = self._idempotency_key()
            status, body = app_pod.kill(rest[1], key)
            return self._send_json(status, body)
        if method == "POST" and len(rest) == 3 and rest[0] == "runs" and rest[2] == "resume":
            app_pod = self._app_pod()
            key = self._idempotency_key()
            payload = self._read_json()
            status, body = app_pod.resume(rest[1], payload, key)
            return self._send_json(status, body)
        if method == "GET" and rest == ["pod"]:
            status, body = self._app_pod().pod()
            return self._send_json(status, body)
        if method == "GET" and rest == ["gpu", "stock"]:
            # Only the exact string "1" turns these on, like rent-panel's force:
            # "true" or "11" silently meaning yes would make a paid-for slow call
            # (or a forced runpodctl round trip) depend on a spelling.
            force = parse_qs(urlsplit(self.path).query).get("force", ["0"])[0] == "1"
            status, body = self._app_pod().gpu_stock(force)
            return self._send_json(status, body)
        if method == "GET" and rest == ["balance"]:
            vast = parse_qs(urlsplit(self.path).query).get("vast", ["0"])[0] == "1"
            status, body = self._app_pod().balance(vast)
            return self._send_json(status, body)
        if method == "PUT" and rest == ["pod", "gpu"]:
            app_pod = self._app_pod()
            payload = self._read_json()
            status, body = app_pod.set_gpu(payload)
            return self._send_json(status, body)
        if method == "POST" and rest == ["pod", "migrate", "ask"]:
            app_pod = self._app_pod()
            payload = self._read_json()
            status, body = app_pod.migrate_ask(payload)
            return self._send_json(status, body)
        if method == "POST" and rest == ["pod", "migrate"]:
            app_pod = self._app_pod()
            key = self._idempotency_key()
            payload = self._read_json()
            status, body = app_pod.migrate(payload, key)
            return self._send_json(status, body)
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
            # write_chunk reads exactly `length` bytes, so the body is settled
            # either way: on success it is consumed, on failure _error closes.
            self._body_consumed = True
            uploads.write_chunk(s.uploads_root, rest[1], n, self.rfile, length)
            return self._send_json(200, {"received": n})
        if method == "GET" and len(rest) == 2 and rest[0] == "uploads":
            return self._send_json(200, uploads.upload_status(s.uploads_root, rest[1]))
        if method == "POST" and len(rest) == 3 and rest[0] == "uploads" and rest[2] == "complete":
            # Assembly, ingest and the record of what was created all live in
            # uploads.complete: it journals its own response (done.json), so a
            # retry is answered from one place rather than re-derived here.
            return self._send_json(201, uploads.complete(
                s.uploads_root, rest[1], s.staging_root))
        if method == "GET" and rest == ["materials"]:
            return self._send_json(200, {"materials": materials.list_materials(s.staging_root)})
        if method == "GET" and len(rest) == 4 and rest[0] == "materials" and rest[3] == "thumb":
            thumb = materials.thumbnail(s.staging_root, s.thumbs_root, rest[1], rest[2])
            try:
                self._settle_body()
                return send_file(self, thumb)
            except FileNotFoundError:
                raise NOT_FOUND
        if method == "DELETE" and len(rest) == 3 and rest[0] == "materials":
            materials.delete_material(s.staging_root, s.batch_dir, rest[1], rest[2])
            return self._send_empty(204)
        if method == "GET" and rest == ["pipelines"]:
            return self._send_json(200, {"pipelines": drafts.pipeline_catalog()})
        if method == "GET" and rest == ["tryon-library"]:
            return self._send_json(200, {"entries": s.tryon_library.list()})
        if method == "POST" and rest == ["tryon-library"]:
            # No Idempotency-Key: this is a file copy of a preview already
            # sitting on disk, not a spend — unlike phase-a/confirm/regen,
            # which rent or drain a pod.
            app_runs = self._app_runs()
            body = self._read_json()
            run_id, index = str(body.get("run_id") or ""), str(body.get("index") or "")
            info = app_runs.tryon_save_info(index) if run_id == app_runs.run_id else None
            if info is None:
                raise NOT_FOUND
            image, material_ids, provider = info
            record = s.tryon_library.save(image=image, material_ids=material_ids, provider=provider)
            return self._send_json(200, record)
        if method == "GET" and len(rest) == 3 and rest[0] == "tryon-library" and rest[2] == "image":
            image = s.tryon_library.resolve_image(rest[1])
            if image is None:
                raise NOT_FOUND
            try:
                self._settle_body()
                return send_file(self, image)
            except FileNotFoundError:
                raise NOT_FOUND
        if method == "DELETE" and len(rest) == 2 and rest[0] == "tryon-library":
            try:
                s.tryon_library.delete(rest[1])
            except tryon_library.TryonLibraryError as exc:
                raise ApiError(404 if exc.code == "not_found" else 400, exc.code, exc.message)
            return self._send_json(200, {"ok": True})
        if rest[:1] == ["draft"]:
            return self._route_draft(method, rest[1:])
        raise NOT_FOUND

    def _route_draft(self, method: str, rest: list[str]) -> None:
        store = self.server.drafts
        if method == "GET" and rest == []:
            return self._send_json(200, store.view())
        if method == "PATCH" and rest == []:
            return self._send_json(200, store.patch(self._read_json()))
        if method == "POST" and rest == ["add-to-batch"]:
            return self._send_json(200, store.add_to_batch())
        if method == "POST" and rest == ["clear"]:
            return self._send_json(200, store.clear())
        if method == "POST" and rest == ["validate"]:
            return self._send_json(200, store.validate(repo_root=self.server.repo_root))
        if method == "DELETE" and len(rest) == 2 and rest[0] == "batch":
            return self._send_json(200, store.drop_from_batch(rest[1]))
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
        self._settle_body()
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
        self._settle_body()
        self.send_response(status)
        self.send_header("Content-Length", "0")
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()


class _Server(ThreadingHTTPServer):
    daemon_threads = True


def make_server(*, token: str, batch_dir: Path, out_dir: Path,
                host: str = "127.0.0.1", port: int = 0, log=print,
                default_pipeline: str = "tryon-motion-enhance",
                default_provider: str = "gemini", probe=None, app_runs=None,
                app_pod=None) -> _Server:
    if not token:
        raise ValueError("an empty token would authenticate nothing")
    server = _Server((host, port), _Handler)
    server.token, server.batch_dir, server.out_dir, server.log = token, batch_dir, out_dir, log
    server.staging_root = batch_dir / "tg-staging"
    server.uploads_root = batch_dir / "uploads"
    server.thumbs_root = batch_dir / "thumbs"
    server.repo_root = batch_dir.parent
    server.tryon_library = tryon_library.TryonLibrary(batch_dir / "tryon-library", materials.APP_OWNER)
    server.drafts = drafts.DraftStore(
        batch_dir, server.staging_root, materials.APP_OWNER,
        default_pipeline=default_pipeline, default_provider=default_provider,
        tryon_library=server.tryon_library,
        **({"probe": probe} if probe is not None else {}))
    # Set by the caller (bot._start_control_api) once its own AppRuns exists
    # — a chicken-and-egg the constructor can't resolve itself, since AppRuns
    # needs this server's own `drafts` store. None until then, and every
    # /v1/runs/... route in this section answers 503 rather than AttributeError.
    server.app_runs = app_runs
    # Same story for AppPod (slice 5), which shares AppRuns's idempotency store
    # and is built beside it; its migrate confirm token lives on the instance,
    # so exactly one is built, before the thread starts.
    server.app_pod = app_pod
    return server


def start_in_thread(server: _Server) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, name="control-api", daemon=True)
    thread.start()
    return thread
