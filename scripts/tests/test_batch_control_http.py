import errno
import http.client
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock
from io import BytesIO

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib_ext.lease import Lease
from control import links, materials, uploads
import control.runs as runs
import tgbot.run as run_mod
import httpapi.files as files_module
from httpapi.files import parse_range
import httpapi.server as server_module
from httpapi.server import ApiError, _Handler, make_server, start_in_thread
from tgbot.ingest import Probe

TOKEN = "t-123"


class FakeHandler:
    """Minimal fake HTTP handler for testing file streaming."""
    def __init__(self):
        self.close_connection = False
        self.headers = {}
        self.headers_sent = {}
        self.response_status = None
        self.wfile = BytesIO()
        self.server = None   # real BaseHTTPRequestHandler always has one

    def send_response(self, status):
        self.response_status = status

    def send_header(self, name, value):
        self.headers_sent[name] = value

    def end_headers(self):
        pass


class HttpTestBase(unittest.TestCase):
    def setUp(self):
        self.batch = Path(tempfile.mkdtemp())
        self.out = Path(tempfile.mkdtemp())
        (self.batch / "r1.yaml").write_text("runs: []\n")
        (self.batch / "r1.state.json").write_text(json.dumps(
            {"batch": "b1", "runs": {"a": {"status": "done", "stages": {}}}}))
        final = self.out / "b1" / "_final"
        final.mkdir(parents=True)
        self.video = bytes(range(256)) * 40            # 10240 bytes
        (final / "a.mp4").write_bytes(self.video)
        for name in ("drain_running", "phase_a_running"):
            p = mock.patch.object(run_mod, name, return_value=False)
            p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(run_mod, "lease_for", return_value=None)
        p.start(); self.addCleanup(p.stop)
        self.logged = []
        self.server = make_server(token=TOKEN, batch_dir=self.batch, out_dir=self.out,
                                  port=0, log=self.logged.append, **self.server_kwargs())
        start_in_thread(self.server)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def server_kwargs(self) -> dict:
        """Extra make_server keywords, for subclasses that need a non-default
        pipeline/provider/probe (e.g. the draft routes)."""
        return {}

    def request(self, path, *, token=TOKEN, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        h = dict(headers or {})
        if token is not None:
            h["Authorization"] = f"Bearer {token}"
        conn.request("GET", path, headers=h)
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        return resp, body


class TestAuth(HttpTestBase):
    def test_missing_and_wrong_token_are_401(self):
        for token in (None, "wrong", ""):
            resp, body = self.request("/v1/health", token=token)
            self.assertEqual(resp.status, 401, token)
            self.assertEqual(json.loads(body)["error"]["code"], "unauthorized")

    def test_right_token_is_200(self):
        resp, body = self.request("/v1/health")
        self.assertEqual((resp.status, json.loads(body)), (200, {"ok": True}))


class TestRoutes(HttpTestBase):
    def test_runs_list_and_detail(self):
        resp, body = self.request("/v1/runs")
        self.assertEqual(resp.status, 200)
        self.assertEqual([r["id"] for r in json.loads(body)["runs"]], ["r1"])
        resp, body = self.request("/v1/runs/r1")
        self.assertEqual(json.loads(body)["status"], "done")

    def test_unknown_run_and_unknown_route_are_404(self):
        for path in ("/v1/runs/nope", "/v1/runs/..%2Fetc", "/v1/nope", "/", "/v2/runs"):
            resp, body = self.request(path)
            self.assertEqual(resp.status, 404, path)
            self.assertEqual(json.loads(body)["error"]["code"], "not_found")

    def test_outputs_list(self):
        resp, body = self.request("/v1/outputs")
        self.assertEqual(json.loads(body)["outputs"][0]["batch"], "b1")

    def test_output_outside_final_is_404(self):
        for path in ("/v1/outputs/b1/..%2F..%2Fr1.yaml", "/v1/outputs/..%2Fb1/a.mp4",
                     "/v1/outputs/b1/missing.mp4"):
            resp, _ = self.request(path)
            self.assertEqual(resp.status, 404, path)


class TestEtag(HttpTestBase):
    def test_if_none_match_returns_304(self):
        resp, _ = self.request("/v1/runs/r1")
        etag = resp.getheader("ETag")
        self.assertTrue(etag)
        resp, body = self.request("/v1/runs/r1", headers={"If-None-Match": etag})
        self.assertEqual((resp.status, body), (304, b""))

    def test_etag_is_stable_across_requests_with_a_live_lease(self):
        # elapsed_sec (derived from time.time()) used to sit in the body, so
        # the ETag (a hash of the body) changed every second and 304 could
        # never fire while a pod was live (review finding 2a). provisioned_at
        # is a fixed number from the lease, so it must not do that.
        lease = Lease(pod_id="p1", provisioned_at=time.time() - 30,
                      manifest=str(self.batch / "r1.yaml"), abs_max_min=120, provider="vast")
        with mock.patch.object(run_mod, "lease_for", return_value=lease):
            resp1, _ = self.request("/v1/runs/r1")
            resp2, _ = self.request("/v1/runs/r1")
            etag = resp1.getheader("ETag")
            self.assertTrue(etag)
            self.assertEqual(etag, resp2.getheader("ETag"))
            resp3, body3 = self.request("/v1/runs/r1", headers={"If-None-Match": etag})
        self.assertEqual((resp3.status, body3), (304, b""))

    def test_weak_etag_matches(self):
        # Cloudflare can rewrite a strong ETag to a weak one (W/"...") when
        # compressing the response; RFC 7232 weak comparison only needs the
        # opaque tag to match, so this must still 304.
        resp, _ = self.request("/v1/runs/r1")
        etag = resp.getheader("ETag")
        resp, body = self.request("/v1/runs/r1", headers={"If-None-Match": f"W/{etag}"})
        self.assertEqual((resp.status, body), (304, b""))

    def test_weak_etag_in_a_list_matches(self):
        resp, _ = self.request("/v1/runs/r1")
        etag = resp.getheader("ETag")
        resp, body = self.request(
            "/v1/runs/r1", headers={"If-None-Match": f'"other", W/{etag}'})
        self.assertEqual((resp.status, body), (304, b""))


class TestErrors(HttpTestBase):
    def test_an_exception_is_a_logged_500_and_the_server_survives(self):
        with mock.patch.object(runs, "list_runs", side_effect=RuntimeError("boom")):
            resp, body = self.request("/v1/runs")
        self.assertEqual(resp.status, 500)
        self.assertEqual(json.loads(body)["error"]["code"], "internal")
        self.assertNotIn("boom", body.decode())          # no internals to the client
        self.assertTrue(any("boom" in line for line in self.logged))
        resp, _ = self.request("/v1/health")
        self.assertEqual(resp.status, 200)


class TestSettleBody(unittest.TestCase):
    def test_a_stalled_drain_closes_the_connection_instead_of_raising(self):
        # _settle_body runs inside _handle's except clauses (via _error), so
        # nothing upstream catches an exception raised here (reproduced by a
        # GET that errors while the client holds back a promised body).
        logged = []

        class FakeServer:
            log = staticmethod(logged.append)

        class FakeRfile:
            def read(self, n):
                raise TimeoutError("timed out")

        fake = types.SimpleNamespace(
            headers={"Content-Length": "10"},
            rfile=FakeRfile(),
            close_connection=False,
            _body_consumed=False,
            server=FakeServer(),
        )
        _Handler._settle_body(fake)          # must not raise
        self.assertTrue(fake.close_connection)
        self.assertEqual(len(logged), 1)


class TestHandlerTimeout(unittest.TestCase):
    def test_handler_has_a_finite_idle_timeout(self):
        # Without this, a keep-alive connection that goes idle (or a client
        # that stops reading mid-response) holds a ThreadingHTTPServer worker
        # thread forever.
        self.assertEqual(_Handler.timeout, 60)


class TestAccessLogSuppression(HttpTestBase):
    def test_successful_output_requests_are_not_logged(self):
        # AVPlayer issues many Range requests per playback; logging every
        # 200/206 on /v1/outputs/... would flood the journal with routine
        # seek traffic.
        self.logged.clear()
        resp, _ = self.request("/v1/outputs/b1/a.mp4")
        self.assertEqual(resp.status, 200)
        self.assertFalse(any("outputs" in line for line in self.logged), self.logged)

    def test_a_404_on_outputs_is_still_logged(self):
        self.logged.clear()
        resp, _ = self.request("/v1/outputs/b1/missing.mp4")
        self.assertEqual(resp.status, 404)
        self.assertTrue(any("outputs" in line for line in self.logged), self.logged)

    def test_other_routes_are_still_logged(self):
        self.logged.clear()
        self.request("/v1/health")
        self.assertTrue(any("health" in line for line in self.logged), self.logged)


class TestParseRange(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(parse_range("bytes=0-1", 100), (0, 1))
        self.assertEqual(parse_range("bytes=10-", 100), (10, 99))
        self.assertEqual(parse_range("bytes=-10", 100), (90, 99))
        self.assertEqual(parse_range("bytes=90-500", 100), (90, 99))   # end clamped

    def test_whole_file_cases(self):
        for header in (None, "", "items=0-1", "bytes=0-1,5-6", "bytes=abc"):
            self.assertIsNone(parse_range(header, 100), header)

    def test_unsatisfiable(self):
        for header in ("bytes=100-", "bytes=5-2", "bytes=-0"):
            self.assertEqual(parse_range(header, 100), "unsatisfiable", header)


class TestFileStreaming(HttpTestBase):
    def test_full_file(self):
        resp, body = self.request("/v1/outputs/b1/a.mp4")
        self.assertEqual(resp.status, 200)
        self.assertEqual(body, self.video)
        self.assertEqual(resp.getheader("Accept-Ranges"), "bytes")
        self.assertEqual(resp.getheader("Content-Type"), "video/mp4")

    def test_range(self):
        resp, body = self.request("/v1/outputs/b1/a.mp4", headers={"Range": "bytes=100-199"})
        self.assertEqual(resp.status, 206)
        self.assertEqual(body, self.video[100:200])
        self.assertEqual(resp.getheader("Content-Range"), f"bytes 100-199/{len(self.video)}")

    def test_unsatisfiable_range_is_416(self):
        resp, _ = self.request("/v1/outputs/b1/a.mp4",
                               headers={"Range": f"bytes={len(self.video)}-"})
        self.assertEqual(resp.status, 416)
        self.assertEqual(resp.getheader("Content-Range"), f"bytes */{len(self.video)}")

    def test_multi_chunk_stream(self):
        # Verify that streaming works correctly when file is read in small chunks
        with mock.patch.object(files_module, "_CHUNK", 1000):
            # Full file request should return all bytes
            resp, body = self.request("/v1/outputs/b1/a.mp4")
            self.assertEqual(resp.status, 200)
            self.assertEqual(body, self.video)
            # Range request should return exact slice
            resp, body = self.request("/v1/outputs/b1/a.mp4", headers={"Range": "bytes=100-199"})
            self.assertEqual(resp.status, 206)
            self.assertEqual(body, self.video[100:200])

    def test_disconnect_during_stream(self):
        # Verify that client disconnect (BrokenPipeError) is handled gracefully
        class FailingWFile:
            def write(self, data):
                raise BrokenPipeError("client disconnected")

        handler = FakeHandler()
        handler.wfile = FailingWFile()

        test_file = Path(tempfile.mktemp(suffix=".bin"))
        test_file.write_bytes(b"x" * 1000)
        try:
            files_module.send_file(handler, test_file)
            self.assertTrue(handler.close_connection)
        finally:
            test_file.unlink()

    def test_file_vanishes_between_resolve_and_send(self):
        # Verify that file vanishing becomes a 404, not a 500
        from pathlib import Path
        import control.outputs as outputs_mod

        original_resolve = outputs_mod.resolve_output
        def resolve_and_delete(out_dir, batch, name):
            path = original_resolve(out_dir, batch, name)
            if path and path.exists():
                path.unlink()  # Delete immediately
            return path

        with mock.patch.object(outputs_mod, "resolve_output", side_effect=resolve_and_delete):
            resp, body = self.request("/v1/outputs/b1/a.mp4")
            self.assertEqual(resp.status, 404)
            self.assertEqual(json.loads(body)["error"]["code"], "not_found")

    def test_os_error_after_headers_sent_does_not_propagate(self):
        # A non-ConnectionError OSError (e.g. EIO from a flaky disk) reading
        # the file after the 200/206 headers are already on the wire must
        # not escape send_file: do_GET's except would otherwise write a
        # second (500) response on top of the first, corrupting the stream.
        class EIOFile:
            def __init__(self, real):
                self._real = real

            def fileno(self):
                return self._real.fileno()

            def seek(self, pos):
                return self._real.seek(pos)

            def read(self, n=-1):
                raise OSError(errno.EIO, "Input/output error")

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                self._real.close()
                return False

        logged = []
        handler = FakeHandler()
        handler.server = types.SimpleNamespace(log=logged.append)

        test_file = Path(tempfile.mktemp(suffix=".bin"))
        test_file.write_bytes(b"x" * 1000)
        real = test_file.open("rb")
        try:
            with mock.patch.object(Path, "open", return_value=EIOFile(real)):
                files_module.send_file(handler, test_file)   # must not raise
            self.assertTrue(handler.close_connection)
            self.assertEqual(handler.response_status, 200)   # not overwritten to 500
            self.assertTrue(any("EIO" in m or "Input/output" in m for m in logged), logged)
        finally:
            test_file.unlink()

    def test_disconnect_during_headers(self):
        # Verify that disconnect during end_headers (header flush) is caught
        class HeaderFailingHandler(FakeHandler):
            def end_headers(self):
                raise BrokenPipeError("client disconnected during headers")

        handler = HeaderFailingHandler()
        test_file = Path(tempfile.mktemp(suffix=".bin"))
        test_file.write_bytes(b"x" * 1000)

        try:
            files_module.send_file(handler, test_file)
            self.assertTrue(handler.close_connection)
        finally:
            test_file.unlink()

    def test_short_file_sets_close_connection(self):
        # Verify that when file ends before promised Content-Length (because
        # fstat reported larger size than real file), close_connection is set.
        class CountingWFile:
            def __init__(self):
                self.written_bytes = 0

            def write(self, data):
                self.written_bytes += len(data)

        handler = FakeHandler()
        handler.wfile = CountingWFile()

        test_file = Path(tempfile.mktemp(suffix=".bin"))
        test_file.write_bytes(b"x" * 500)

        try:
            # Patch os.fstat to report larger size (1000) than real file (500)
            fake_stat = types.SimpleNamespace(st_size=1000)
            with mock.patch.object(files_module.os, "fstat", return_value=fake_stat):
                files_module.send_file(handler, test_file)

            # Should not raise, close_connection should be True
            self.assertTrue(handler.close_connection,
                          "close_connection not set when file ends early")
            # Content-Length should be 1000 (from fstat)
            self.assertEqual(handler.headers_sent.get("Content-Length"), "1000")
            # But only 500 bytes written (real file size)
            self.assertEqual(handler.wfile.written_bytes, 500,
                           f"Wrote {handler.wfile.written_bytes} bytes but file was 500")
        finally:
            test_file.unlink()


class TestBotStartsApi(unittest.TestCase):
    def setUp(self):
        import tgbot.bot as bot
        self.bot = bot
        self.tg = mock.Mock()

    def env(self, **values):
        return mock.patch.object(self.bot, "env_get",
                                 side_effect=lambda _path, key: values.get(key))

    def test_no_token_means_disabled_and_silent(self):
        with self.env():
            self.assertIsNone(self.bot._start_control_api(self.tg, 1))
        self.tg.send_message.assert_not_called()

    def test_starts_on_the_configured_port(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
        with self.env(CONTROL_API_TOKEN="x", CONTROL_API_PORT=str(port)):
            server = self.bot._start_control_api(self.tg, 1)
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        self.assertEqual(server.server_address, ("127.0.0.1", port))

    def test_app_pod_is_built_once_and_wired_before_the_thread_starts(self):
        # The migrate confirm token lives on the AppPod instance, so a
        # per-request instance would void every token, and a server that
        # starts serving before app_pod is set would answer 503 to the first
        # phone request (the reason app_runs is wired the same way).
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
        built = []
        real = self.bot.AppPod

        def build(*args, **kwargs):
            built.append(real(*args, **kwargs))
            return built[-1]

        seen = {}

        def fake_start(server):
            seen["app_pod"] = server.app_pod
            seen["app_runs"] = server.app_runs
            return mock.Mock()

        with self.env(CONTROL_API_TOKEN="x", CONTROL_API_PORT=str(port)), \
             mock.patch.object(self.bot, "AppPod", side_effect=build), \
             mock.patch.object(self.bot, "start_in_thread", side_effect=fake_start):
            server = self.bot._start_control_api(self.tg, 7)
        self.addCleanup(server.server_close)
        self.assertEqual(len(built), 1)
        self.assertIs(seen["app_pod"], built[0])
        self.assertIs(server.app_pod, built[0])
        # One idempotency store for both classes: a key is unique across the
        # whole API, and the staging-prune sweep covers one directory.
        self.assertIs(built[0].idem, seen["app_runs"].idem)
        self.assertEqual(built[0].chat_id, 7)

    def test_port_in_use_reports_and_keeps_the_bot_alive(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0)); s.listen(); port = s.getsockname()[1]
            with self.env(CONTROL_API_TOKEN="x", CONTROL_API_PORT=str(port)):
                self.assertIsNone(self.bot._start_control_api(self.tg, 1))
        self.tg.send_message.assert_called_once()
        self.assertIn("API", self.tg.send_message.call_args.args[1])

    def test_thread_start_failure_reports_and_frees_the_port(self):
        # If the OS refuses to create the daemon thread (RuntimeError: can't
        # start new thread), that used to escape _start_control_api and, with
        # systemd Restart=always, crash-loop the whole bot (spec §4.2: the
        # bot must keep polling even if the phone API can't start).
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
        with self.env(CONTROL_API_TOKEN="x", CONTROL_API_PORT=str(port)), \
             mock.patch.object(self.bot, "start_in_thread",
                               side_effect=RuntimeError("can't start new thread")):
            result = self.bot._start_control_api(self.tg, 1)
        self.assertIsNone(result)
        self.tg.send_message.assert_called_once()
        self.assertIn("API", self.tg.send_message.call_args.args[1])
        # The port must be free again — make_server already bound it before
        # start_in_thread failed, so the fix must close it on that path.
        with socket.socket() as s:
            s.bind(("127.0.0.1", port))


class HttpWriteBase(HttpTestBase):
    def send(self, method, path, body=b"", *, token=TOKEN, headers=None, json_body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        h = dict(headers or {})
        if token is not None:
            h["Authorization"] = f"Bearer {token}"
        if json_body is not None:
            body = json.dumps(json_body).encode()
            h["Content-Type"] = "application/json"
        conn.request(method, path, body=body, headers=h)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp, data

    def setUp(self):
        super().setUp()
        p = mock.patch.object(uploads, "CHUNK_SIZE", 4)
        p.start(); self.addCleanup(p.stop)
        p = mock.patch("shutil.disk_usage", return_value=mock.Mock(free=10 ** 13))
        p.start(); self.addCleanup(p.stop)


class TestWriteAuthAndErrors(HttpWriteBase):
    def test_every_method_requires_the_token_and_answers_json(self):
        for method in ("POST", "PUT", "DELETE"):
            resp, body = self.send(method, "/v1/uploads", token=None)
            self.assertEqual(resp.status, 401, method)
            self.assertEqual(json.loads(body)["error"]["code"], "unauthorized")

    def test_bad_json_is_400(self):
        resp, body = self.send("POST", "/v1/uploads", b"{not json",
                               headers={"Content-Type": "application/json"})
        self.assertEqual(resp.status, 400)

    def test_staging_dir_name_matches_the_bot(self):
        import tgbot.bot as bot
        self.assertEqual(self.server.staging_root.name, bot.STAGING_DIR_NAME)


class TestUploadFlow(HttpWriteBase):
    def test_chunked_upload_end_to_end(self):
        resp, body = self.send("POST", "/v1/uploads", json_body={"file_name": "clip.bin", "size": 10})
        self.assertEqual(resp.status, 201)
        uid = json.loads(body)["upload_id"]
        for n, data in ((2, b"89"), (0, b"0123"), (1, b"4567")):
            resp, _ = self.send("PUT", f"/v1/uploads/{uid}/chunks/{n}", data)
            self.assertEqual(resp.status, 200)
        resp, body = self.send("GET", f"/v1/uploads/{uid}")
        self.assertEqual(json.loads(body)["received"], [0, 1, 2])
        with mock.patch.object(materials, "ingest",
                               side_effect=lambda p: (p, {"kind": "video"})):
            resp, body = self.send("POST", f"/v1/uploads/{uid}/complete")
        self.assertEqual(resp.status, 201)
        got = json.loads(body)
        self.assertEqual(got["material"]["id"], "app/clip.bin")
        self.assertEqual((self.batch / "tg-staging" / "app" / "clip.bin").read_bytes(), b"0123456789")

    def test_wrong_chunk_length_is_400_and_closes(self):
        uid = json.loads(self.send("POST", "/v1/uploads",
                                   json_body={"file_name": "c.bin", "size": 10})[1])["upload_id"]
        resp, _ = self.send("PUT", f"/v1/uploads/{uid}/chunks/0", b"abc")
        self.assertEqual(resp.status, 400)
        self.assertEqual(resp.getheader("Connection"), "close")

    def test_complete_before_all_chunks_is_409(self):
        uid = json.loads(self.send("POST", "/v1/uploads",
                                   json_body={"file_name": "c.bin", "size": 10})[1])["upload_id"]
        resp, body = self.send("POST", f"/v1/uploads/{uid}/complete")
        self.assertEqual((resp.status, json.loads(body)["error"]["code"]), (409, "incomplete"))

    def test_unprobeable_complete_is_422_and_leaves_nothing(self):
        uid = json.loads(self.send("POST", "/v1/uploads",
                                   json_body={"file_name": "junk.mp4", "size": 4})[1])["upload_id"]
        self.send("PUT", f"/v1/uploads/{uid}/chunks/0", b"junk")
        with mock.patch.object(materials, "ingest",
                               side_effect=materials.MaterialError("unprobeable", "bad")):
            resp, _ = self.send("POST", f"/v1/uploads/{uid}/complete")
        self.assertEqual(resp.status, 422)
        self.assertFalse((self.batch / "tg-staging" / "app" / "junk.mp4").exists())

    def test_too_large_is_413(self):
        resp, _ = self.send("POST", "/v1/uploads",
                            json_body={"file_name": "x", "size": uploads.MAX_UPLOAD_BYTES + 1})
        self.assertEqual(resp.status, 413)

    def test_chunk_bigger_than_chunk_size_is_413_and_closes_with_no_tmp_left(self):
        uid = json.loads(self.send("POST", "/v1/uploads",
                                   json_body={"file_name": "big.bin", "size": 10})[1])["upload_id"]
        resp, _ = self.send("PUT", f"/v1/uploads/{uid}/chunks/0", b"12345")   # CHUNK_SIZE patched to 4
        self.assertEqual(resp.status, 413)
        self.assertEqual(resp.getheader("Connection"), "close")
        upload_dir = self.batch / "uploads" / uid
        self.assertEqual(list(upload_dir.glob("*.tmp")), [])

    def test_complete_twice_returns_the_same_material(self):
        # A lost 201 (phone on a flaky link) must be retryable: the retry
        # replays the stored response instead of 404-ing on a gone upload.
        uid = json.loads(self.send("POST", "/v1/uploads",
                                   json_body={"file_name": "twice.bin", "size": 4})[1])["upload_id"]
        self.send("PUT", f"/v1/uploads/{uid}/chunks/0", b"data")
        with mock.patch.object(materials, "ingest", side_effect=lambda p: (p, {"kind": "video"})):
            first = self.send("POST", f"/v1/uploads/{uid}/complete")
            second = self.send("POST", f"/v1/uploads/{uid}/complete")
        self.assertEqual((first[0].status, second[0].status), (201, 201))
        self.assertEqual(json.loads(first[1]), json.loads(second[1]))
        self.assertEqual([p.name for p in (self.batch / "tg-staging" / "app").iterdir()],
                         ["twice.bin"])
        status = json.loads(self.send("GET", f"/v1/uploads/{uid}")[1])
        self.assertEqual(status["material"], json.loads(first[1])["material"])

    def test_a_chunk_during_assembly_is_409_conflict(self):
        uid = json.loads(self.send("POST", "/v1/uploads",
                                   json_body={"file_name": "busy.bin", "size": 4})[1])["upload_id"]
        (self.batch / "uploads" / uid / uploads.ASSEMBLING).write_text(uploads._PROCESS_TOKEN)
        resp, body = self.send("PUT", f"/v1/uploads/{uid}/chunks/0", b"data")
        self.assertEqual((resp.status, json.loads(body)["error"]["code"]), (409, "conflict"))

    def test_too_many_open_uploads_is_409(self):
        for _ in range(uploads.MAX_OPEN_UPLOADS):
            self.send("POST", "/v1/uploads", json_body={"file_name": "c.bin", "size": 10})
        resp, body = self.send("POST", "/v1/uploads", json_body={"file_name": "c.bin", "size": 10})
        self.assertEqual((resp.status, json.loads(body)["error"]["code"]), (409, "too_many"))

    def test_a_nul_byte_in_an_upload_id_is_404_not_500(self):
        resp, body = self.send("GET", "/v1/uploads/%00")
        self.assertEqual((resp.status, json.loads(body)["error"]["code"]), (404, "not_found"))

    def test_a_chunk_body_that_stalls_is_400_and_closes(self):
        uid = json.loads(self.send("POST", "/v1/uploads",
                                   json_body={"file_name": "stall.bin", "size": 4})[1])["upload_id"]
        with mock.patch.object(_Handler, "timeout", 0.5):
            conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
            conn.request("PUT", f"/v1/uploads/{uid}/chunks/0", body=b"da",
                         headers={"Authorization": f"Bearer {TOKEN}", "Content-Length": "4"})
            resp = conn.getresponse()
            body = resp.read()
            conn.close()
        self.assertEqual((resp.status, json.loads(body)["error"]["code"]), (400, "bad_request"))
        self.assertEqual(resp.getheader("Connection"), "close")

    def test_complete_when_the_file_vanishes_meanwhile_is_404_not_500(self):
        # A concurrent DELETE or prune between ingest() and the material_item()
        # stat: the upload itself succeeded, so this must be a clean 404, not
        # a StopIteration turned opaque 500.
        uid = json.loads(self.send("POST", "/v1/uploads",
                                   json_body={"file_name": "vanish.bin", "size": 4})[1])["upload_id"]
        self.send("PUT", f"/v1/uploads/{uid}/chunks/0", b"data")

        def ingest_then_delete(p):
            p.unlink()
            return p, {"kind": "video"}

        with mock.patch.object(materials, "ingest", side_effect=ingest_then_delete):
            resp, body = self.send("POST", f"/v1/uploads/{uid}/complete")
        self.assertEqual((resp.status, json.loads(body)["error"]["code"]), (404, "not_found"))


class TestLinkImport(HttpWriteBase):
    """POST /v1/materials/link — a pasted TikTok link becomes an app material
    (2026-09-25), through the bot's own tgbot/tiktok.py downloader."""
    URL = "https://vt.tiktok.com/ZS8abcde/"

    def fake_download(self, url, *, timeout):
        self.downloaded = (url, timeout)
        d = Path(tempfile.mkdtemp(prefix="tiktok-"))
        (d / "video.mp4").write_bytes(b"clip")
        self.tmp_dir = d
        return d / "video.mp4"

    def post(self, url):
        return self.send("POST", "/v1/materials/link", json_body={"url": url})

    def test_a_tiktok_link_is_staged_as_an_app_material(self):
        with mock.patch.object(links.tiktok, "download", side_effect=self.fake_download), \
             mock.patch.object(links.materials, "ingest",
                               side_effect=lambda p: (p, {"kind": "video", "size_bytes": 4, "warning": ""})):
            resp, body = self.post(f"look at this {self.URL} lol")
        self.assertEqual(resp.status, 201)
        got = json.loads(body)
        self.assertEqual(got["material"]["owner"], "app")
        self.assertTrue(got["material"]["name"].startswith("tiktok-"))
        self.assertEqual(self.downloaded, (self.URL, links.DOWNLOAD_TIMEOUT))
        staged = self.batch / "tg-staging" / "app" / got["material"]["name"]
        self.assertEqual(staged.read_bytes(), b"clip")
        self.assertFalse(self.tmp_dir.exists(), "the download's temp dir must be removed")

    def test_anything_but_a_tiktok_link_is_400_and_downloads_nothing(self):
        with mock.patch.object(links.tiktok, "download") as download:
            for url in ("https://youtube.com/watch?v=1", "", None, 42):
                resp, body = self.post(url)
                self.assertEqual((resp.status, json.loads(body)["error"]["code"]),
                                 (400, "bad_request"), url)
        download.assert_not_called()

    def test_a_failed_download_is_502_and_stages_nothing(self):
        with mock.patch.object(links.tiktok, "download",
                               side_effect=RuntimeError("yt-dlp failed: boom")):
            resp, body = self.post(self.URL)
        error = json.loads(body)["error"]
        self.assertEqual((resp.status, error["code"]), (502, "download_failed"))
        self.assertIn("boom", error["message"])
        self.assertFalse((self.batch / "tg-staging" / "app").exists()
                         and any((self.batch / "tg-staging" / "app").iterdir()))

    def test_an_unprobeable_download_is_422_and_leaves_nothing(self):
        with mock.patch.object(links.tiktok, "download", side_effect=self.fake_download), \
             mock.patch.object(links.materials, "ingest",
                               side_effect=materials.MaterialError("unprobeable", "no video")):
            resp, _ = self.post(self.URL)
        self.assertEqual(resp.status, 422)
        self.assertEqual(list((self.batch / "tg-staging" / "app").iterdir()), [])

    def test_a_second_link_while_one_downloads_is_409(self):
        self.assertTrue(links._SLOT.acquire(blocking=False))
        try:
            resp, body = self.post(self.URL)
        finally:
            links._SLOT.release()
        self.assertEqual((resp.status, json.loads(body)["error"]["code"]), (409, "busy"))


class TestKeepAlive(HttpWriteBase):
    def test_a_body_no_route_read_does_not_desync_the_connection(self):
        # DELETE and complete answer without reading the request body, so a
        # client that sends `{}` left those two bytes in the socket and the
        # next request on the same keep-alive connection was parsed as
        # "{}GET / ..." → 501 Unsupported method (reproduced 2026-09-21).
        d = self.batch / "tg-staging" / "app"
        d.mkdir(parents=True)
        (d / "a.mp4").write_bytes(b"v")
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        auth = {"Authorization": f"Bearer {TOKEN}"}
        conn.request("DELETE", "/v1/materials/app/a.mp4", body=b"{}",
                     headers={**auth, "Content-Type": "application/json"})
        first = conn.getresponse()
        first.read()
        conn.request("GET", "/v1/health", headers=auth)
        second = conn.getresponse()
        body = second.read()
        conn.close()
        self.assertEqual((first.status, second.status), (204, 200))
        self.assertEqual(json.loads(body), {"ok": True})


class TestMaterialRoutes(HttpWriteBase):
    def setUp(self):
        super().setUp()
        d = self.batch / "tg-staging" / "app"
        d.mkdir(parents=True)
        (d / "a.mp4").write_bytes(b"v")
        (self.batch / "tg-staging" / "99").mkdir()
        (self.batch / "tg-staging" / "99" / "t.png").write_bytes(b"i")

    def test_list(self):
        resp, body = self.send("GET", "/v1/materials")
        self.assertEqual(sorted(m["id"] for m in json.loads(body)["materials"]),
                         ["99/t.png", "app/a.mp4"])

    def test_delete_app_material_is_204(self):
        resp, body = self.send("DELETE", "/v1/materials/app/a.mp4")
        self.assertEqual((resp.status, body), (204, b""))
        self.assertFalse((self.batch / "tg-staging" / "app" / "a.mp4").exists())

    def test_delete_telegram_material_is_403(self):
        resp, _ = self.send("DELETE", "/v1/materials/99/t.png")
        self.assertEqual(resp.status, 403)

    def test_delete_traversal_is_404(self):
        resp, _ = self.send("DELETE", "/v1/materials/app/..%2F99%2Ft.png")
        self.assertEqual(resp.status, 404)

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg required")
    def test_thumb_is_jpeg(self):
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=blue:s=640x480",
                        "-frames:v", "1", str(self.batch / "tg-staging" / "app" / "p.png")], check=True)
        resp, body = self.send("GET", "/v1/materials/app/p.png/thumb")
        self.assertEqual((resp.status, resp.getheader("Content-Type")), (200, "image/jpeg"))
        self.assertTrue(body.startswith(b"\xff\xd8"))


def _fake_probe(path: Path) -> Probe:
    """A stand-in for ingest.probe: kind from the extension, no ffprobe call —
    these tests run offline and don't care about real dimensions/bitrate."""
    if path.suffix == ".mp4":
        return Probe(kind="video", width=1080, height=1920, duration_s=5.0,
                     bitrate_kbps=4000, size_bytes=path.stat().st_size)
    return Probe(kind="image", width=1024, height=1024, duration_s=0.0,
                 bitrate_kbps=0, size_bytes=path.stat().st_size)


class TestDraftRoutes(HttpWriteBase):
    def setUp(self):
        super().setUp()
        d = self.batch / "tg-staging" / "app"
        d.mkdir(parents=True)
        (d / "me.png").write_bytes(b"i")
        (d / "dress.png").write_bytes(b"i")
        (d / "dance.mp4").write_bytes(b"v")

    def server_kwargs(self) -> dict:
        return {"default_pipeline": "tryon-motion-enhance", "default_provider": "gemini",
                "probe": _fake_probe}

    def request(self, method, path, body=None):
        resp, data = self.send(method, path, json_body=body) if body is not None \
            else self.send(method, path)
        return resp.status, (json.loads(data) if data else None)

    def test_pipelines(self):
        status, body = self.request("GET", "/v1/pipelines")
        self.assertEqual(status, 200)
        self.assertIn("tryon-motion-enhance", [p["id"] for p in body["pipelines"]])

    def test_compose_batch_and_clear(self):
        status, body = self.request("GET", "/v1/draft")
        self.assertEqual((status, body["pipeline"], body["provider"]),
                         (200, "tryon-motion-enhance", "gemini"))
        status, body = self.request("PATCH", "/v1/draft", {"slots": {
            "character": "app/me.png", "outfit": "app/dress.png", "driver": "app/dance.mp4"}})
        self.assertEqual((status, body["missing"]), (200, []))
        status, body = self.request("POST", "/v1/draft/add-to-batch")
        self.assertEqual((status, len(body["batch"])), (200, 1))
        digest = body["batch"][0]["digest"]
        status, body = self.request("DELETE", f"/v1/draft/batch/{digest}")
        self.assertEqual((status, body["batch"]), (200, []))
        status, body = self.request("POST", "/v1/draft/clear")
        self.assertEqual((status, body["slots"]), (200, {}))

    def test_refusal_statuses(self):
        cases = [
            ("PATCH", "/v1/draft", {"pipeline": "nope"}, 422, "unknown_pipeline"),
            ("PATCH", "/v1/draft", {"slots": {"driver": "app/me.png"}}, 422, "wrong_kind"),
            ("PATCH", "/v1/draft", {"slots": {"character": "app/none.png"}}, 404, "not_found"),
            ("PATCH", "/v1/draft", {"tryon_seed": "nope"}, 404, "seed_not_found"),
            ("PATCH", "/v1/draft", {"hat": 1}, 400, "bad_request"),
            ("POST", "/v1/draft/add-to-batch", None, 422, "missing_slots"),
            ("DELETE", "/v1/draft/batch/0000000000", None, 404, "not_found"),
            ("POST", "/v1/draft/validate", None, 422, "nothing_to_validate"),
        ]
        for method, path, body, want_status, want_code in cases:
            with self.subTest(method=method, path=path, body=body):
                status, got = self.request(method, path, body)
                self.assertEqual((status, got["error"]["code"]), (want_status, want_code))

    def test_seed_not_found_is_mapped_to_404(self):
        # An unmapped DraftError code falls through to _DOMAIN_STATUS.get(code, 400),
        # so a missing table entry would silently turn this 404 into a 400.
        self.assertEqual(server_module._DOMAIN_STATUS["seed_not_found"], 404)

    def test_draft_etag_is_stable_between_polls(self):
        resp, _ = self.send("GET", "/v1/draft")
        etag = resp.getheader("ETag")
        self.assertTrue(etag)
        resp, body = self.send("GET", "/v1/draft", headers={"If-None-Match": etag})
        self.assertEqual((resp.status, body), (304, b""))

    def test_deleting_material_the_draft_uses_is_409(self):
        self.request("PATCH", "/v1/draft", {"slots": {"character": "app/me.png"}})
        status, body = self.request("DELETE", "/v1/materials/app/me.png")
        self.assertEqual((status, body["error"]["code"]), (409, "in_use"))

    def test_patch_error_closes_the_connection(self):
        # Same technique as the existing keep-alive tests for DELETE/POST
        # errors: a body-bearing method that errors must not leave the
        # connection open for a client that expects it closed.
        resp, _ = self.send("PATCH", "/v1/draft", json_body={"pipeline": "nope"})
        self.assertEqual(resp.status, 422)
        self.assertEqual(resp.getheader("Connection"), "close")

    def test_validate_route_passes_repo_root(self):
        self.request("PATCH", "/v1/draft", {"slots": {
            "character": "app/me.png", "outfit": "app/dress.png", "driver": "app/dance.mp4"}})
        with mock.patch("control.drafts.subprocess.run",
                        return_value=mock.Mock(returncode=0, stdout="ok", stderr="")) as run:
            status, body = self.request("POST", "/v1/draft/validate")
        self.assertEqual((status, body["valid"]), (200, True))
        self.assertEqual(run.call_args.kwargs["cwd"], self.batch.parent)


class FakeAppRuns:
    """Records every call `_route` makes into it and answers with whatever
    the test set up beforehand — the real `AppRuns` (bot.py) already has its
    own unit tests; these HTTP tests only need to prove the routing, the
    Idempotency-Key gate and the 503 wiring, not `AppRuns`'s own logic."""

    def __init__(self):
        self.calls = []
        self.run_id = "tg-1"
        self.phase_a_response = (202, {"run_id": "tg-1", "outcome": "started"})
        self.confirm_response = (202, {"run_id": "tg-1", "outcome": "started"})
        self.rent_panel_response = (200, {"run_id": "tg-1", "panel_token": "p1"})
        self.tryon_response = (200, {"run_id": "tg-1", "previews": []})
        self.regen_response = (202, {"run_id": "tg-1", "outcome": "started"})
        self.tryon_image_path = None
        self.tryon_image_error = None
        self.tryon_version_image_path = None
        self.tryon_save_info_result = None

    def phase_a(self, key):
        self.calls.append(("phase_a", key))
        return self.phase_a_response

    def confirm(self, run_id, body, key):
        self.calls.append(("confirm", run_id, body, key))
        return self.confirm_response

    def rent_panel(self, run_id, *, force):
        self.calls.append(("rent_panel", run_id, force))
        return self.rent_panel_response

    def tryon(self, run_id):
        self.calls.append(("tryon", run_id))
        return self.tryon_response

    def tryon_image(self, run_id, index):
        self.calls.append(("tryon_image", run_id, index))
        if self.tryon_image_error is not None:
            raise self.tryon_image_error
        return self.tryon_image_path

    def regen(self, run_id, index, body, key):
        self.calls.append(("regen", run_id, index, body, key))
        return self.regen_response

    def tryon_version_image(self, run_id, index, n):
        self.calls.append(("tryon_version_image", run_id, index, n))
        return self.tryon_version_image_path

    def tryon_save_info(self, index):
        self.calls.append(("tryon_save_info", index))
        return self.tryon_save_info_result


class TestAppRunRoutes(HttpWriteBase):
    def setUp(self):
        self.fake = FakeAppRuns()
        super().setUp()
        self.server.app_runs = self.fake

    def test_phase_a_reaches_app_runs_with_the_key(self):
        resp, body = self.send("POST", "/v1/runs/phase-a", headers={"Idempotency-Key": "k1"})
        self.assertEqual((resp.status, json.loads(body)), self.fake.phase_a_response)
        self.assertEqual(self.fake.calls, [("phase_a", "k1")])

    def test_phase_a_missing_key_is_400_and_never_reaches_app_runs(self):
        resp, body = self.send("POST", "/v1/runs/phase-a")
        self.assertEqual(resp.status, 400)
        self.assertEqual(json.loads(body)["error"]["code"], "bad_request")
        self.assertEqual(self.fake.calls, [])

    def test_rent_panel_reaches_app_runs_and_parses_force(self):
        resp, body = self.send("GET", "/v1/runs/tg-1/rent-panel?force=1")
        self.assertEqual((resp.status, json.loads(body)), self.fake.rent_panel_response)
        self.assertEqual(self.fake.calls, [("rent_panel", "tg-1", True)])

    def test_rent_panel_defaults_force_to_false(self):
        self.send("GET", "/v1/runs/tg-1/rent-panel")
        self.assertEqual(self.fake.calls, [("rent_panel", "tg-1", False)])

    def test_confirm_reaches_app_runs_with_body_and_key(self):
        payload = {"provider": "runpod", "panel_token": "p1", "tryon": "reuse"}
        resp, body = self.send("POST", "/v1/runs/tg-1/confirm", json_body=payload,
                               headers={"Idempotency-Key": "k2"})
        self.assertEqual((resp.status, json.loads(body)), self.fake.confirm_response)
        self.assertEqual(self.fake.calls, [("confirm", "tg-1", payload, "k2")])

    def test_confirm_missing_key_is_400_and_never_reaches_app_runs(self):
        resp, body = self.send("POST", "/v1/runs/tg-1/confirm",
                               json_body={"provider": "runpod", "panel_token": "p1"})
        self.assertEqual(resp.status, 400)
        self.assertEqual(json.loads(body)["error"]["code"], "bad_request")
        self.assertEqual(self.fake.calls, [])

    def test_a_202_from_app_runs_passes_through(self):
        self.fake.confirm_response = (202, {"run_id": "tg-1", "outcome": "resumed"})
        resp, body = self.send("POST", "/v1/runs/tg-1/confirm",
                               json_body={"provider": "vast", "panel_token": "p1"},
                               headers={"Idempotency-Key": "k3"})
        self.assertEqual((resp.status, json.loads(body)), (202, {"run_id": "tg-1",
                                                                 "outcome": "resumed"}))

    def test_tryon_reaches_app_runs(self):
        resp, body = self.send("GET", "/v1/runs/tg-1/tryon")
        self.assertEqual((resp.status, json.loads(body)), self.fake.tryon_response)
        self.assertEqual(self.fake.calls, [("tryon", "tg-1")])

    def test_tryon_image_streams_a_file(self):
        image = Path(tempfile.mktemp(suffix=".jpg"))
        image.write_bytes(b"\xff\xd8fake-jpeg")
        self.addCleanup(image.unlink)
        self.fake.tryon_image_path = image
        resp, body = self.send("GET", "/v1/runs/tg-1/tryon/0")
        self.assertEqual((resp.status, body), (200, image.read_bytes()))
        self.assertEqual(self.fake.calls, [("tryon_image", "tg-1", "0")])

    def test_tryon_image_404s_on_none(self):
        resp, body = self.send("GET", "/v1/runs/tg-1/tryon/0")
        self.assertEqual(resp.status, 404)
        self.assertEqual(json.loads(body)["error"]["code"], "not_found")

    def test_tryon_image_503s_on_bot_busy_not_404(self):
        # AppRuns.tryon_image raises ApiError(503, "bot_busy", ...) on a lock
        # timeout, distinct from the None it returns for a genuinely missing
        # preview; the route must let it through, not fold it into 404.
        self.fake.tryon_image_error = ApiError(503, "bot_busy",
                                               "the bot is busy — try again in a moment")
        resp, body = self.send("GET", "/v1/runs/tg-1/tryon/0")
        self.assertEqual(resp.status, 503)
        self.assertEqual(json.loads(body)["error"]["code"], "bot_busy")

    def test_tryon_version_image_streams_a_file(self):
        image = Path(tempfile.mktemp(suffix=".jpg"))
        image.write_bytes(b"\xff\xd8fake-jpeg-version")
        self.addCleanup(image.unlink)
        self.fake.tryon_version_image_path = image
        resp, body = self.send("GET", "/v1/runs/tg-1/tryon/0/versions/1")
        self.assertEqual((resp.status, body), (200, image.read_bytes()))
        self.assertEqual(self.fake.calls, [("tryon_version_image", "tg-1", "0", "1")])

    def test_tryon_version_route_404s_for_an_unknown_version(self):
        self.fake.tryon_version_image_path = None
        resp, body = self.send("GET", "/v1/runs/tg-1/tryon/0/versions/99")
        self.assertEqual(resp.status, 404)
        self.assertEqual(json.loads(body)["error"]["code"], "not_found")

    def test_regen_reaches_app_runs_with_body_and_key(self):
        resp, body = self.send("POST", "/v1/runs/tg-1/tryon/2/regen",
                               json_body={"run_token": "rt1"},
                               headers={"Idempotency-Key": "k4"})
        self.assertEqual((resp.status, json.loads(body)), self.fake.regen_response)
        self.assertEqual(self.fake.calls, [("regen", "tg-1", "2", {"run_token": "rt1"}, "k4")])

    def test_regen_missing_key_is_400_and_never_reaches_app_runs(self):
        resp, body = self.send("POST", "/v1/runs/tg-1/tryon/2/regen",
                               json_body={"run_token": "rt1"})
        self.assertEqual(resp.status, 400)
        self.assertEqual(self.fake.calls, [])

    def test_existing_run_routes_are_unaffected_by_app_runs_being_set(self):
        resp, body = self.send("GET", "/v1/runs")
        self.assertEqual(resp.status, 200)
        self.assertEqual([r["id"] for r in json.loads(body)["runs"]], ["r1"])
        resp, body = self.send("GET", "/v1/runs/r1")
        self.assertEqual(json.loads(body)["status"], "done")

    def test_a_post_error_on_a_new_route_closes_the_connection(self):
        resp, _ = self.send("POST", "/v1/runs/phase-a")
        self.assertEqual(resp.status, 400)
        self.assertEqual(resp.getheader("Connection"), "close")


class TestTryonLibraryRoutes(HttpWriteBase):
    """§5.10: a saved try-on entry that outlives one draft — separate from
    AppRuns's per-run previews, so listing/reading/deleting an entry works
    even with `app_runs` unset; only the save itself needs AppRuns to hand
    back the preview file (`FakeAppRuns.tryon_save_info`)."""

    def setUp(self):
        self.fake = FakeAppRuns()
        super().setUp()
        self.server.app_runs = self.fake

    def request(self, method, path, body=None):
        resp, data = self.send(method, path, json_body=body) if body is not None \
            else self.send(method, path)
        return resp.status, (json.loads(data) if data else None)

    def test_tryon_library_round_trip(self):
        # Seed one preview the fake AppRuns can hand back.
        image = self.batch / "preview.png"
        image.write_bytes(b"preview-bytes")
        self.fake.tryon_save_info_result = (image, {"character": "app/c.png"}, "gemini")

        status, body = self.request("POST", "/v1/tryon-library", {"run_id": "tg-1", "index": "0"})
        self.assertEqual(status, 200)
        self.assertEqual(self.fake.calls, [("tryon_save_info", "0")])
        self.assertEqual(body["material_ids"], {"character": "app/c.png"})
        self.assertEqual(body["provider"], "gemini")
        entry_id = body["id"]

        status, listed = self.request("GET", "/v1/tryon-library")
        self.assertEqual(status, 200)
        self.assertEqual([e["id"] for e in listed["entries"]], [entry_id])

        resp, image_body = self.send("GET", f"/v1/tryon-library/{entry_id}/image")
        self.assertEqual((resp.status, image_body), (200, b"preview-bytes"))

        status, _ = self.request("DELETE", f"/v1/tryon-library/{entry_id}")
        self.assertEqual(status, 200)
        status, listed = self.request("GET", "/v1/tryon-library")
        self.assertEqual(listed["entries"], [])

    def test_tryon_library_save_with_no_such_preview_is_404(self):
        self.fake.tryon_save_info_result = None
        status, body = self.request("POST", "/v1/tryon-library", {"run_id": "tg-1", "index": "0"})
        self.assertEqual((status, body["error"]["code"]), (404, "not_found"))

    def test_tryon_library_save_wrong_run_id_is_404_and_never_reaches_app_runs(self):
        # A stale run_id (e.g. a panel from a chat that has since restarted)
        # must not fall through to whatever preview `tryon_save_info` would
        # happen to return for the wrong run.
        image = self.batch / "preview.png"
        image.write_bytes(b"preview-bytes")
        self.fake.tryon_save_info_result = (image, {}, "gemini")
        status, body = self.request("POST", "/v1/tryon-library", {"run_id": "tg-9", "index": "0"})
        self.assertEqual((status, body["error"]["code"]), (404, "not_found"))
        self.assertEqual(self.fake.calls, [])

    def test_tryon_library_image_of_unknown_id_is_404(self):
        status, body = self.request("GET", "/v1/tryon-library/nope/image")
        self.assertEqual((status, body["error"]["code"]), (404, "not_found"))

    def test_tryon_library_delete_of_unknown_id_is_404(self):
        status, body = self.request("DELETE", "/v1/tryon-library/nope")
        self.assertEqual((status, body["error"]["code"]), (404, "not_found"))

    def test_post_tryon_library_needs_no_idempotency_key(self):
        # A file copy, not a spend — unlike phase-a/confirm/regen, this route
        # must not gate on Idempotency-Key.
        image = self.batch / "preview.png"
        image.write_bytes(b"preview-bytes")
        self.fake.tryon_save_info_result = (image, {}, "gemini")
        status, body = self.request("POST", "/v1/tryon-library", {"run_id": "tg-1", "index": "0"})
        self.assertEqual(status, 200)


class TestAppRunRoutesUnavailable(HttpWriteBase):
    def test_every_new_route_is_503_when_app_runs_is_unset(self):
        cases = [
            ("POST", "/v1/runs/phase-a", {"Idempotency-Key": "k1"}, None),
            ("GET", "/v1/runs/tg-1/rent-panel", {}, None),
            ("POST", "/v1/runs/tg-1/confirm", {"Idempotency-Key": "k2"},
             {"provider": "runpod", "panel_token": "p1"}),
            ("GET", "/v1/runs/tg-1/tryon", {}, None),
            ("GET", "/v1/runs/tg-1/tryon/0", {}, None),
            ("POST", "/v1/runs/tg-1/tryon/0/regen", {"Idempotency-Key": "k3"},
             {"run_token": "rt1"}),
            ("POST", "/v1/tryon-library", {}, {"run_id": "tg-1", "index": "0"}),
        ]
        for method, path, headers, payload in cases:
            with self.subTest(method=method, path=path):
                resp, body = self.send(method, path, headers=headers, json_body=payload)
                self.assertEqual(resp.status, 503, path)
                self.assertEqual(json.loads(body)["error"]["code"], "runs_unavailable", path)

    def test_tryon_library_list_read_delete_work_without_app_runs(self):
        # Only the save itself asks AppRuns for the preview; the rest of the
        # library is independent state and must not 503 alongside it.
        resp, body = self.send("GET", "/v1/tryon-library")
        self.assertEqual((resp.status, json.loads(body)), (200, {"entries": []}))
        resp, body = self.send("GET", "/v1/tryon-library/nope/image")
        self.assertEqual(resp.status, 404)
        resp, body = self.send("DELETE", "/v1/tryon-library/nope")
        self.assertEqual(resp.status, 404)


class FakeAppPod:
    """The `FakeAppRuns` pattern for slice 5: records every call `_route`
    makes and answers with whatever the test set up. The real `AppPod`
    (bot.py) has its own unit tests; here only the routing, the
    Idempotency-Key gate and the 503 wiring are under test — and nothing
    here can reach `_do_kill`, `_start_migration` or a pod."""

    def __init__(self):
        self.calls = []
        self.kill_response = (202, {"run_id": "tg-1", "outcome": "kill_started"})
        self.resume_response = (202, {"run_id": "tg-1", "outcome": "resumed"})
        self.pod_response = (200, {"run_id": "tg-1", "kill_running": False, "last_kill": None})
        self.gpu_stock_response = (200, {"gpus": []})
        self.balance_response = (200, {"runpod": None})
        self.set_gpu_response = (200, {"gpu": "NVIDIA GeForce RTX 5090"})
        self.migrate_ask_response = (200, {"confirm_token": "tok", "to_dc": "EU-CZ-1"})
        self.migrate_response = (202, {"outcome": "started", "to_dc": "EU-CZ-1"})

    def kill(self, run_id, key):
        self.calls.append(("kill", run_id, key))
        return self.kill_response

    def resume(self, run_id, body, key):
        self.calls.append(("resume", run_id, body, key))
        return self.resume_response

    def pod(self):
        self.calls.append(("pod",))
        return self.pod_response

    def gpu_stock(self, force):
        self.calls.append(("gpu_stock", force))
        return self.gpu_stock_response

    def balance(self, vast):
        self.calls.append(("balance", vast))
        return self.balance_response

    def set_gpu(self, body):
        self.calls.append(("set_gpu", body))
        return self.set_gpu_response

    def migrate_ask(self, body):
        self.calls.append(("migrate_ask", body))
        return self.migrate_ask_response

    def migrate(self, body, key):
        self.calls.append(("migrate", body, key))
        return self.migrate_response


# (method, path) of every route slice 5 adds, with whether it takes a body
# and an Idempotency-Key. One table so the auth, 503 and key tests cannot
# drift from the routes themselves.
_POD_ROUTES = [
    ("POST", "/v1/runs/tg-1/kill", False, True),
    ("POST", "/v1/runs/tg-1/resume", True, True),
    ("GET", "/v1/pod", False, False),
    ("GET", "/v1/gpu/stock", False, False),
    ("GET", "/v1/balance", False, False),
    ("PUT", "/v1/pod/gpu", True, False),
    ("POST", "/v1/pod/migrate/ask", True, False),
    ("POST", "/v1/pod/migrate", True, True),
]


class TestAppPodRoutes(HttpWriteBase):
    def setUp(self):
        self.fake = FakeAppPod()
        super().setUp()
        self.server.app_pod = self.fake

    def test_kill_reaches_app_pod_with_the_key(self):
        resp, body = self.send("POST", "/v1/runs/tg-1/kill", headers={"Idempotency-Key": "k1"})
        self.assertEqual((resp.status, json.loads(body)), self.fake.kill_response)
        self.assertEqual(self.fake.calls, [("kill", "tg-1", "k1")])

    def test_resume_reaches_app_pod_with_body_and_key(self):
        payload = {"provider": "runpod", "run_token": "rt1", "gpu": "NVIDIA GeForce RTX 5090"}
        resp, body = self.send("POST", "/v1/runs/tg-1/resume", json_body=payload,
                               headers={"Idempotency-Key": "k2"})
        self.assertEqual((resp.status, json.loads(body)), self.fake.resume_response)
        self.assertEqual(self.fake.calls, [("resume", "tg-1", payload, "k2")])

    def test_pod_reaches_app_pod_and_needs_no_key(self):
        resp, body = self.send("GET", "/v1/pod")
        self.assertEqual((resp.status, json.loads(body)), self.fake.pod_response)
        self.assertEqual(self.fake.calls, [("pod",)])

    def test_gpu_stock_parses_force_only_for_the_exact_string_1(self):
        for query, expected in (("", False), ("?force=1", True), ("?force=0", False),
                                ("?force=true", False), ("?force=11", False), ("?force=", False)):
            with self.subTest(query=query):
                self.fake.calls.clear()
                resp, body = self.send("GET", "/v1/gpu/stock" + query)
                self.assertEqual((resp.status, json.loads(body)), self.fake.gpu_stock_response)
                self.assertEqual(self.fake.calls, [("gpu_stock", expected)])

    def test_balance_parses_vast_only_for_the_exact_string_1(self):
        for query, expected in (("", False), ("?vast=1", True), ("?vast=0", False),
                                ("?vast=yes", False), ("?vast=10", False)):
            with self.subTest(query=query):
                self.fake.calls.clear()
                resp, body = self.send("GET", "/v1/balance" + query)
                self.assertEqual((resp.status, json.loads(body)), self.fake.balance_response)
                self.assertEqual(self.fake.calls, [("balance", expected)])

    def test_set_gpu_reaches_app_pod_and_needs_no_key(self):
        payload = {"gpu": "NVIDIA GeForce RTX 5090"}
        resp, body = self.send("PUT", "/v1/pod/gpu", json_body=payload)
        self.assertEqual((resp.status, json.loads(body)), self.fake.set_gpu_response)
        self.assertEqual(self.fake.calls, [("set_gpu", payload)])

    def test_migrate_ask_reaches_app_pod_and_needs_no_key(self):
        payload = {"to_dc": "EU-CZ-1"}
        resp, body = self.send("POST", "/v1/pod/migrate/ask", json_body=payload)
        self.assertEqual((resp.status, json.loads(body)), self.fake.migrate_ask_response)
        self.assertEqual(self.fake.calls, [("migrate_ask", payload)])

    def test_migrate_reaches_app_pod_with_body_and_key(self):
        payload = {"to_dc": "EU-CZ-1", "confirm_token": "tok"}
        resp, body = self.send("POST", "/v1/pod/migrate", json_body=payload,
                               headers={"Idempotency-Key": "k3"})
        self.assertEqual((resp.status, json.loads(body)), self.fake.migrate_response)
        self.assertEqual(self.fake.calls, [("migrate", payload, "k3")])

    def test_migrate_202_body_has_no_run_id(self):
        # kill and resume answer with a run_id; migrate is about the volume,
        # not a run, and the phone must not look for one.
        resp, body = self.send("POST", "/v1/pod/migrate",
                               json_body={"to_dc": "EU-CZ-1", "confirm_token": "tok"},
                               headers={"Idempotency-Key": "k3"})
        self.assertEqual(resp.status, 202)
        self.assertNotIn("run_id", json.loads(body))

    def test_a_refusal_from_app_pod_passes_through(self):
        self.fake.kill_response = (409, {"error": {"code": "nothing_running", "message": "m"}})
        resp, body = self.send("POST", "/v1/runs/tg-1/kill", headers={"Idempotency-Key": "k1"})
        self.assertEqual((resp.status, json.loads(body)), self.fake.kill_response)

    def test_the_keyed_routes_without_a_key_are_400_and_never_reach_app_pod(self):
        for method, path, has_body, keyed in _POD_ROUTES:
            if not keyed:
                continue
            with self.subTest(path=path):
                resp, body = self.send(method, path, json_body={"x": 1} if has_body else None)
                self.assertEqual(resp.status, 400, path)
                self.assertEqual(json.loads(body)["error"]["code"], "bad_request", path)
        self.assertEqual(self.fake.calls, [])

    def test_the_key_is_checked_before_the_body_is_read(self):
        # A body that is not JSON would answer 400 "not valid JSON" if it were
        # read first; the missing key must win, so the message names the key.
        resp, body = self.send("POST", "/v1/pod/migrate", b"{not json")
        self.assertEqual(resp.status, 400)
        self.assertIn("Idempotency-Key", json.loads(body)["error"]["message"])
        self.assertEqual(self.fake.calls, [])

    def test_a_non_object_body_is_400_and_never_reaches_app_pod(self):
        for method, path, has_body, _keyed in _POD_ROUTES:
            if not has_body:
                continue
            with self.subTest(path=path):
                resp, body = self.send(method, path, b"[1, 2]",
                                       headers={"Idempotency-Key": "k9"})
                self.assertEqual(resp.status, 400, path)
                self.assertEqual(json.loads(body)["error"]["code"], "bad_request", path)
        self.assertEqual(self.fake.calls, [])

    def test_every_new_route_needs_the_bearer_token(self):
        for method, path, has_body, _keyed in _POD_ROUTES:
            with self.subTest(method=method, path=path):
                resp, body = self.send(method, path, token=None,
                                       headers={"Idempotency-Key": "k9"},
                                       json_body={"x": 1} if has_body else None)
                self.assertEqual(resp.status, 401, path)
                self.assertEqual(json.loads(body)["error"]["code"], "unauthorized", path)
        self.assertEqual(self.fake.calls, [])

    def test_a_wrong_method_on_a_new_path_is_404_not_a_call(self):
        for method, path in (("GET", "/v1/runs/tg-1/kill"), ("POST", "/v1/pod"),
                             ("GET", "/v1/pod/gpu"), ("POST", "/v1/pod/gpu"),
                             ("GET", "/v1/pod/migrate"), ("PUT", "/v1/pod/migrate/ask"),
                             ("POST", "/v1/balance"), ("POST", "/v1/gpu/stock")):
            with self.subTest(method=method, path=path):
                resp, _ = self.send(method, path, json_body={} if method != "GET" else None,
                                    headers={"Idempotency-Key": "k9"})
                self.assertEqual(resp.status, 404, (method, path))
        self.assertEqual(self.fake.calls, [])

    def test_existing_routes_are_unaffected_by_app_pod_being_set(self):
        resp, body = self.send("GET", "/v1/runs")
        self.assertEqual(resp.status, 200)
        resp, body = self.send("GET", "/v1/runs/r1")
        self.assertEqual(json.loads(body)["status"], "done")
        self.assertEqual(self.fake.calls, [])


class TestAppPodRoutesUnavailable(HttpWriteBase):
    def test_every_new_route_is_503_pod_unavailable_when_app_pod_is_unset(self):
        self.assertIsNone(self.server.app_pod)
        for method, path, has_body, _keyed in _POD_ROUTES:
            with self.subTest(method=method, path=path):
                resp, body = self.send(method, path, headers={"Idempotency-Key": "k1"},
                                       json_body={"x": 1} if has_body else None)
                self.assertEqual(resp.status, 503, path)
                self.assertEqual(json.loads(body)["error"]["code"], "pod_unavailable", path)


if __name__ == "__main__":
    unittest.main()
