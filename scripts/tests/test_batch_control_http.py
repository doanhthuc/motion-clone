import http.client
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from io import BytesIO

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import control.runs as runs
import tgbot.run as run_mod
import httpapi.files as files_module
from httpapi.files import parse_range
from httpapi.server import make_server, start_in_thread

TOKEN = "t-123"


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
        from http.server import BaseHTTPRequestHandler

        # Create a minimal fake handler that raises BrokenPipeError on write
        class FakeHandler(BaseHTTPRequestHandler):
            def __init__(self):
                self.response_status = None
                self.headers_sent = {}
                self.close_connection = False
                self.headers = {}

            def send_response(self, status):
                self.response_status = status

            def send_header(self, name, value):
                self.headers_sent[name] = value

            def end_headers(self):
                pass

        # Create wfile that raises BrokenPipeError on any write
        class FailingWFile:
            def write(self, data):
                raise BrokenPipeError("client disconnected")

        handler = FakeHandler()
        handler.wfile = FailingWFile()

        # send_file should handle the disconnect gracefully
        test_file = Path(tempfile.mktemp(suffix=".bin"))
        test_file.write_bytes(b"x" * 1000)
        try:
            files_module.send_file(handler, test_file)
            # Should not raise, and should mark connection for close
            self.assertTrue(handler.close_connection)
        finally:
            test_file.unlink()

    def test_file_vanishes_between_resolve_and_send(self):
        # Verify that file vanishing becomes a 404, not a 500
        resp, body = self.request("/v1/outputs/b1/a.mp4")
        # Delete the file after it was resolved but before response is complete
        # We do this by patching resolve_output to delete the file after returning it
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


if __name__ == "__main__":
    unittest.main()
