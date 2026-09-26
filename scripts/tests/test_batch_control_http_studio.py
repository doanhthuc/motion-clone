import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import control.studio_runner as sr
from control.studio_runner import StudioRunner
from test_batch_control_http import FakeAppRuns, HttpWriteBase

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 16


class StudioHttpBase(HttpWriteBase):
    def setUp(self):
        super().setUp()
        for target in ("qwen_max_configured", "_available"):
            p = mock.patch.object(sr, target, return_value=True)
            p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(sr, "_key", return_value="k")
        p.start(); self.addCleanup(p.stop)
        self.gemini_calls = []

        def gemini(spec, prompt, images, aspect):
            self.gemini_calls.append((spec.key, prompt, len(images)))
            return PNG
        self.server.studio_runner.shutdown()
        self.server.studio_runner = StudioRunner(self.server.studio, self.batch.parent,
                                                 gemini=gemini, log=lambda *_: None)
        self.addCleanup(self.server.studio_runner.shutdown)
        app = self.batch / "tg-staging" / "app"
        app.mkdir(parents=True)
        (app / "me.png").write_bytes(PNG)
        (app / "dance.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")

    def json_call(self, method, path, body=None, headers=None):
        resp, data = self.send(method, path, json_body=body if body is not None else None,
                               headers=headers)
        return resp.status, (json.loads(data) if data else None)

    def new_project(self):
        status, body = self.json_call("POST", "/v1/studio/projects", {})
        self.assertEqual(status, 201)
        return body["project"]["id"]

    def generate(self, pid, body, key="k-1"):
        return self.json_call("POST", f"/v1/studio/projects/{pid}/generations", body,
                              headers={"Idempotency-Key": key})


class TestStudioRoutes(StudioHttpBase):
    def test_models_catalog(self):
        status, body = self.json_call("GET", "/v1/studio/models")
        self.assertEqual(status, 200)
        self.assertIn("nano-banana-2", [m["key"] for m in body["models"]])

    def test_project_crud(self):
        pid = self.new_project()
        status, body = self.json_call("PATCH", f"/v1/studio/projects/{pid}", {"title": "Model A"})
        self.assertEqual((status, body["project"]["title"]), (200, "Model A"))
        status, body = self.json_call("GET", "/v1/studio/projects")
        self.assertEqual([p["id"] for p in body["projects"]], [pid])
        status, _ = self.json_call("DELETE", f"/v1/studio/projects/{pid}")
        self.assertEqual(status, 204)
        status, body = self.json_call("GET", f"/v1/studio/projects/{pid}")
        self.assertEqual((status, body["error"]["code"]), (404, "not_found"))

    def test_generation_with_material_ref_runs_and_serves_the_image(self):
        pid = self.new_project()
        status, body = self.generate(pid, {"prompt": "red dress", "model": "nano-banana-2",
                                           "aspect": "9:16", "count": 2,
                                           "refs": [{"kind": "material", "id": "app/me.png"}]})
        self.assertEqual(status, 202, body)
        self.assertTrue(self.server.studio_runner.wait_idle(5))
        _, body = self.json_call("GET", f"/v1/studio/projects/{pid}")
        gen = body["project"]["generations"][0]
        self.assertEqual(gen["status"], "done")
        self.assertEqual(self.gemini_calls[0], ("nano-banana-2", "red dress", 1))
        resp, data = self.send("GET", f"/v1/studio/projects/{pid}/images/{gen['slots'][0]['image']}")
        self.assertEqual((resp.status, data), (200, PNG))
        resp, data = self.send("GET", f"/v1/studio/projects/{pid}/refs/{gen['refs'][0]['file']}")
        self.assertEqual((resp.status, data), (200, PNG))

    def test_generation_needs_an_idempotency_key(self):
        pid = self.new_project()
        status, body = self.json_call("POST", f"/v1/studio/projects/{pid}/generations",
                                      {"prompt": "x", "model": "nano-banana-2", "aspect": "1:1",
                                       "count": 1})
        self.assertEqual(status, 400)

    def test_generation_post_is_idempotent(self):
        pid = self.new_project()
        body = {"prompt": "x", "model": "nano-banana-2", "aspect": "1:1", "count": 1, "refs": []}
        first = self.generate(pid, body, key="same")
        second = self.generate(pid, body, key="same")
        self.assertEqual(first, second)
        self.assertTrue(self.server.studio_runner.wait_idle(5))
        self.assertEqual(len(self.gemini_calls), 1)

    def test_video_material_ref_is_422(self):
        pid = self.new_project()
        status, body = self.generate(pid, {"prompt": "x", "model": "nano-banana-2", "aspect": "1:1",
                                           "count": 1,
                                           "refs": [{"kind": "material", "id": "app/dance.mp4"}]})
        self.assertEqual((status, body["error"]["code"]), (422, "ref_not_image"))

    def test_missing_ref_is_422_and_nothing_is_recorded(self):
        pid = self.new_project()
        status, body = self.generate(pid, {"prompt": "x", "model": "nano-banana-2", "aspect": "1:1",
                                           "count": 1,
                                           "refs": [{"kind": "tryon", "id": "gone"}]})
        self.assertEqual((status, body["error"]["code"]), (422, "ref_not_found"))
        _, body = self.json_call("GET", f"/v1/studio/projects/{pid}")
        self.assertEqual(body["project"]["generations"], [])

    def test_too_many_refs_for_qwen_is_422(self):
        pid = self.new_project()
        status, body = self.generate(pid, {"prompt": "x", "model": "qwen-image-3", "aspect": "1:1",
                                           "count": 1,
                                           "refs": [{"kind": "material", "id": "app/me.png"}] * 4})
        self.assertEqual((status, body["error"]["code"]), (422, "too_many_refs"))

    def test_studio_ref_and_run_tryon_ref_resolve(self):
        pid = self.new_project()
        self.generate(pid, {"prompt": "x", "model": "nano-banana-2", "aspect": "1:1", "count": 1})
        self.assertTrue(self.server.studio_runner.wait_idle(5))
        _, body = self.json_call("GET", f"/v1/studio/projects/{pid}")
        image_id = body["project"]["generations"][0]["slots"][0]["image"]
        preview = self.out / "b1" / "tryon.png"
        preview.write_bytes(PNG)
        fake = FakeAppRuns()
        fake.tryon_image_path = preview
        self.server.app_runs = fake
        status, body = self.generate(pid, {"prompt": "y", "model": "nano-banana-2", "aspect": "1:1",
                                           "count": 1,
                                           "refs": [{"kind": "studio", "id": f"{pid}/{image_id}"},
                                                    {"kind": "run_tryon", "id": "run-1/0"}]},
                                     key="k-2")
        self.assertEqual(status, 202, body)
        self.assertEqual(len(body["generation"]["refs"]), 2)

    def test_snapshot_ref_survives_the_source_material_being_pruned(self):
        pid = self.new_project()
        status, body = self.generate(pid, {"prompt": "x", "model": "nano-banana-2", "aspect": "1:1",
                                           "count": 1,
                                           "refs": [{"kind": "material", "id": "app/me.png"}]})
        self.assertEqual(status, 202, body)
        self.assertTrue(self.server.studio_runner.wait_idle(5))
        gen = body["generation"]
        snapshot_file = gen["refs"][0]["file"]
        (self.batch / "tg-staging" / "app" / "me.png").unlink()   # pruned, as materials are after 7 days
        status, body = self.generate(pid, {"prompt": "y", "model": "nano-banana-2", "aspect": "1:1",
                                           "count": 1,
                                           "refs": [{"kind": "snapshot", "id": f"{pid}/{snapshot_file}"}]},
                                     key="k-2")
        self.assertEqual(status, 202, body)

    def test_promote_to_material_and_tryon(self):
        pid = self.new_project()
        self.generate(pid, {"prompt": "x", "model": "nano-banana-2", "aspect": "1:1", "count": 1})
        self.assertTrue(self.server.studio_runner.wait_idle(5))
        _, body = self.json_call("GET", f"/v1/studio/projects/{pid}")
        image_id = body["project"]["generations"][0]["slots"][0]["image"]
        base = f"/v1/studio/projects/{pid}/images/{image_id}/promote"
        status, body = self.json_call("POST", base, {"to": "material"})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["material"]["id"].startswith("app/studio-"))
        status, body = self.json_call("POST", base, {"to": "tryon"})
        self.assertEqual((status, body["entry"]["provider"]), (200, "studio:nano-banana-2"))
        status, body = self.json_call("POST", base, {"to": "elsewhere"})
        self.assertEqual(status, 400)

    def test_422_then_corrected_retry_with_same_key_succeeds(self):
        pid = self.new_project()
        too_many = {"prompt": "x", "model": "qwen-image-3", "aspect": "1:1", "count": 1,
                   "refs": [{"kind": "material", "id": "app/me.png"}] * 4}
        status, body = self.generate(pid, too_many, key="retry-1")
        self.assertEqual((status, body["error"]["code"]), (422, "too_many_refs"))
        fixed = {"prompt": "x", "model": "qwen-image-3", "aspect": "1:1", "count": 1,
                "refs": [{"kind": "material", "id": "app/me.png"}]}
        status, body = self.generate(pid, fixed, key="retry-1")
        self.assertEqual(status, 202, body)

    def test_replay_after_project_deleted_returns_the_original_body(self):
        pid = self.new_project()
        body = {"prompt": "x", "model": "nano-banana-2", "aspect": "1:1", "count": 1, "refs": []}
        first = self.generate(pid, body, key="del-1")
        self.assertEqual(first[0], 202, first)
        self.assertTrue(self.server.studio_runner.wait_idle(5))
        status, _ = self.json_call("DELETE", f"/v1/studio/projects/{pid}")
        self.assertEqual(status, 204)
        second = self.generate(pid, body, key="del-1")
        self.assertEqual(second, first)

    def test_unknown_project_is_404_and_retry_with_same_key_is_still_404(self):
        body = {"prompt": "x", "model": "nano-banana-2", "aspect": "1:1", "count": 1}
        status, resp = self.generate("no-such-project", body, key="unk-1")
        self.assertEqual((status, resp["error"]["code"]), (404, "not_found"))
        status, resp = self.generate("no-such-project", body, key="unk-1")
        self.assertEqual((status, resp["error"]["code"]), (404, "not_found"))


class TestStudioStartup(StudioHttpBase):
    def test_make_server_recovers_interrupted_slots(self):
        from httpapi.server import make_server
        pid = self.new_project()
        gen = self.server.studio.add_generation(pid, prompt="p", model="nano-banana-2", aspect="1:1",
                                                count=1, refs=[], unit_price_usd=0.1)
        again = make_server(token="t", batch_dir=self.batch, out_dir=self.out, port=0,
                            log=lambda *_: None)
        self.addCleanup(again.server_close)
        self.addCleanup(again.studio_runner.shutdown)
        self.assertEqual(again.studio.generation(pid, gen["id"])["slots"][0]["error"], "interrupted")

    def test_make_server_survives_a_malformed_studio_index(self):
        from httpapi.server import make_server
        index = self.batch / "studio" / "app.json"
        index.parent.mkdir(parents=True, exist_ok=True)
        index.write_text(json.dumps([{"id": "x"}]))        # valid JSON, wrong shape: KeyError
        logged = []
        again = make_server(token="t", batch_dir=self.batch, out_dir=self.out, port=0,
                            log=logged.append)
        self.addCleanup(again.server_close)
        self.addCleanup(again.studio_runner.shutdown)
        self.assertTrue(any("recover_interrupted failed" in line for line in logged))


if __name__ == "__main__":
    unittest.main()
