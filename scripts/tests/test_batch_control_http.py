import errno
import http.client
import json
import socket
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
import control.runs as runs
import tgbot.run as run_mod
import httpapi.files as files_module
from httpapi.files import parse_range
from httpapi.server import _Handler, make_server, start_in_thread

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
                                  port=0, log=self.logged.append)
        start_in_thread(self.server)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

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


if __name__ == "__main__":
    unittest.main()
