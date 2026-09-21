import http.client
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import control.runs as runs
import tgbot.run as run_mod
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


if __name__ == "__main__":
    unittest.main()
