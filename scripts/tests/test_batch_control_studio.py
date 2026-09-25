import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from control.studio import StudioError, StudioStore

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 16


class StoreBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        # studio_runner reads GEMINI_API_KEY from <root>/.env to decide availability.
        (self.root / ".env").write_text("GEMINI_API_KEY=AIza-test\n")
        self.store = StudioStore(self.root / "studio", "app")

    def ref_file(self, name="ref.png", data=PNG):
        path = self.root / name
        path.write_bytes(data)
        return path


class TestProjects(StoreBase):
    def test_create_list_rename_delete(self):
        p = self.store.create_project()
        self.assertEqual(p["title"], "")
        self.assertEqual(p["generations"], [])
        self.assertEqual([x["id"] for x in self.store.list_projects()], [p["id"]])
        self.assertEqual(self.store.rename(p["id"], "  Model A ")["title"], "Model A")
        self.store.delete_project(p["id"])
        self.assertEqual(self.store.list_projects(), [])
        self.assertFalse((self.root / "studio" / "app" / p["id"]).exists())

    def test_unknown_project_is_not_found(self):
        with self.assertRaises(StudioError) as ctx:
            self.store.get_project("nope")
        self.assertEqual(ctx.exception.code, "not_found")

    def test_title_is_capped(self):
        p = self.store.create_project()
        self.assertEqual(len(self.store.rename(p["id"], "x" * 500)["title"]), 80)


class TestGenerations(StoreBase):
    def setUp(self):
        super().setUp()
        self.pid = self.store.create_project()["id"]

    def test_refs_are_snapshotted_and_survive_source_deletion(self):
        src = self.ref_file()
        gen = self.store.add_generation(self.pid, prompt="fix hand", model="nano-banana-2",
                                        aspect="9:16", count=2,
                                        refs=[({"kind": "material", "id": "app/ref.png"}, src)],
                                        unit_price_usd=0.101)
        src.unlink()
        ref = gen["refs"][0]
        self.assertEqual((ref["kind"], ref["id"]), ("material", "app/ref.png"))
        self.assertEqual(self.store.resolve_ref(self.pid, ref["file"]).read_bytes(), PNG)
        self.assertEqual([s["status"] for s in gen["slots"]], ["queued", "queued"])
        self.assertEqual(gen["status"], "running")
        self.assertAlmostEqual(gen["est_cost_usd"], 0.202)

    def test_slots_aggregate_and_spent_counts_only_done(self):
        gen = self.store.add_generation(self.pid, prompt="p", model="nano-banana-pro", aspect="1:1",
                                        count=2, refs=[], unit_price_usd=0.134)
        self.store.set_slot(self.pid, gen["id"], 0, status="done", image=PNG)
        self.store.set_slot(self.pid, gen["id"], 1, status="error", error="blocked: SAFETY")
        got = self.store.generation(self.pid, gen["id"])
        self.assertEqual(got["status"], "done")
        self.assertEqual(got["slots"][1]["error"], "blocked: SAFETY")
        image_id = got["slots"][0]["image"]
        self.assertEqual(self.store.resolve_image(self.pid, image_id).read_bytes(), PNG)
        self.assertAlmostEqual(self.store.get_project(self.pid)["spent_usd"], 0.134)
        summary = self.store.list_projects()[0]
        self.assertEqual((summary["cover"], summary["image_count"]), (image_id, 1))

    def test_all_failed_is_error(self):
        gen = self.store.add_generation(self.pid, prompt="p", model="nano-banana-2", aspect="1:1",
                                        count=1, refs=[], unit_price_usd=0.1)
        self.store.set_slot(self.pid, gen["id"], 0, status="error", error="quota")
        self.assertEqual(self.store.generation(self.pid, gen["id"])["status"], "error")

    def test_image_suffix_follows_magic_bytes(self):
        gen = self.store.add_generation(self.pid, prompt="p", model="nano-banana-2", aspect="1:1",
                                        count=1, refs=[], unit_price_usd=0.1)
        self.store.set_slot(self.pid, gen["id"], 0, status="done", image=b"\xff\xd8\xff" + b"0" * 8)
        image_id = self.store.generation(self.pid, gen["id"])["slots"][0]["image"]
        self.assertEqual(self.store.resolve_image(self.pid, image_id).suffix, ".jpg")

    def test_set_slot_on_deleted_project_raises_not_found(self):
        gen = self.store.add_generation(self.pid, prompt="p", model="nano-banana-2", aspect="1:1",
                                        count=1, refs=[], unit_price_usd=0.1)
        self.store.delete_project(self.pid)
        with self.assertRaises(StudioError):
            self.store.set_slot(self.pid, gen["id"], 0, status="done", image=PNG)

    def test_path_escapes_resolve_to_none(self):
        self.assertIsNone(self.store.resolve_image(self.pid, "../../app"))
        self.assertIsNone(self.store.resolve_ref(self.pid, "../x"))

    def test_recover_interrupted_marks_unfinished_slots(self):
        gen = self.store.add_generation(self.pid, prompt="p", model="nano-banana-2", aspect="1:1",
                                        count=2, refs=[], unit_price_usd=0.1)
        self.store.set_slot(self.pid, gen["id"], 0, status="done", image=PNG)
        self.store.set_slot(self.pid, gen["id"], 1, status="running")
        fresh = StudioStore(self.root / "studio", "app")        # a bot restart
        self.assertEqual(fresh.recover_interrupted(), 1)
        got = fresh.generation(self.pid, gen["id"])
        self.assertEqual([s["status"] for s in got["slots"]], ["done", "error"])
        self.assertEqual(got["slots"][1]["error"], "interrupted")


import shutil
import subprocess
import threading
from unittest import mock

import control.studio_runner as sr
from control.studio_runner import StudioRunner, catalog, fit_image, parse_request


class TestCatalogAndParse(StoreBase):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(sr, "qwen_max_configured", return_value=True)
        p.start(); self.addCleanup(p.stop)

    def body(self, **kw):
        base = {"prompt": "make it red", "model": "nano-banana-2", "aspect": "9:16", "count": 1,
                "refs": []}
        base.update(kw)
        return base

    def test_catalog_lists_four_models_with_prices(self):
        cat = catalog(self.root)
        self.assertEqual([m["key"] for m in cat["models"]],
                         ["nano-banana-pro", "nano-banana-2", "nano-banana-2-lite", "qwen-image-3"])
        self.assertEqual([m["key"] for m in cat["models"] if m["default"]], ["nano-banana-2"])
        self.assertTrue(all(m["price_usd"] > 0 for m in cat["models"]))
        self.assertEqual(cat["aspects"], ["16:9", "4:3", "1:1", "3:4", "9:16"])

    def test_qwen_unavailable_without_config(self):
        with mock.patch.object(sr, "qwen_max_configured", return_value=False):
            self.assertFalse(next(m for m in catalog(self.root)["models"]
                                  if m["key"] == "qwen-image-3")["available"])
            with self.assertRaises(sr.StudioError) as ctx:
                parse_request(self.body(model="qwen-image-3"), self.root)
            self.assertEqual(ctx.exception.code, "model_unavailable")

    def test_parse_rejects_bad_input(self):
        cases = [(self.body(prompt="  "), "bad_request"),
                 (self.body(model="dall-e"), "unknown_model"),
                 (self.body(aspect="2:1"), "bad_request"),
                 (self.body(count=5), "bad_request"),
                 (self.body(count="2"), "bad_request"),
                 (self.body(refs=[{"kind": "file", "id": "/etc/passwd"}]), "bad_request"),
                 (self.body(model="qwen-image-3",
                            refs=[{"kind": "material", "id": f"app/{i}.png"} for i in range(4)]),
                  "too_many_refs")]
        for body, code in cases:
            with self.subTest(body=body):
                with self.assertRaises(sr.StudioError) as ctx:
                    parse_request(body, self.root)
                self.assertEqual(ctx.exception.code, code)

    def test_qwen_accepts_text_only(self):
        req = parse_request(self.body(model="qwen-image-3"), self.root)
        self.assertEqual((req.model.key, req.refs), ("qwen-image-3", []))


class TestFitImage(StoreBase):
    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
    def test_fit_image_downscales_large_images(self):
        src = self.root / "big.png"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=red:s=3000x1000",
                        "-frames:v", "1", str(src)], check=True)
        dest = self.root / "out.png"
        fit_image(src, dest)
        self.assertEqual(sr.img_size(dest), (2048, 683))

    def test_small_images_are_copied_byte_for_byte(self):
        src = self.ref_file()
        dest = self.root / "copy.png"
        with mock.patch.object(sr, "img_size", return_value=(800, 600)):
            fit_image(src, dest)
        self.assertEqual(dest.read_bytes(), PNG)


class TestRunner(StoreBase):
    def setUp(self):
        super().setUp()
        self.pid = self.store.create_project()["id"]
        self.calls = []
        p = mock.patch.object(sr, "qwen_max_configured", return_value=True)
        p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(sr, "_key", return_value="k")
        p.start(); self.addCleanup(p.stop)

    def runner(self, gemini=None, qwen=None):
        r = StudioRunner(self.store, self.root, gemini=gemini, qwen=qwen, log=lambda *_: None)
        self.addCleanup(r.shutdown)
        return r

    def req(self, **kw):
        body = {"prompt": "p", "model": "nano-banana-2", "aspect": "1:1", "count": 2, "refs": []}
        body.update(kw)
        return parse_request(body, self.root)

    def test_gemini_slots_are_independent(self):
        def gemini(spec, prompt, images, aspect):
            self.calls.append((spec.api_model, prompt, len(images), aspect))
            if len(self.calls) == 2:
                raise sr.JobError("Gemini returned no image: finish: IMAGE_SAFETY")
            return PNG
        r = self.runner(gemini=gemini)
        gen = r.submit(self.pid, self.req(), [])
        self.assertTrue(r.wait_idle(5))
        got = self.store.generation(self.pid, gen["id"])
        self.assertEqual(sorted(s["status"] for s in got["slots"]), ["done", "error"])
        self.assertIn("IMAGE_SAFETY", next(s["error"] for s in got["slots"] if s["status"] == "error"))
        self.assertEqual(self.calls[0][0], "gemini-3.1-flash-image")

    def test_qwen_is_one_call_with_n(self):
        def qwen(spec, prompt, images, aspect, count):
            self.calls.append((count, len(images)))
            return [PNG] * count
        r = self.runner(qwen=qwen)
        src = self.ref_file()
        gen = r.submit(self.pid, self.req(model="qwen-image-3", count=3,
                                          refs=[{"kind": "material", "id": "app/ref.png"}]), [src])
        self.assertTrue(r.wait_idle(5))
        self.assertEqual(self.calls, [(3, 1)])
        self.assertEqual(self.store.generation(self.pid, gen["id"])["status"], "done")

    def test_qwen_failure_fails_every_slot(self):
        def qwen(spec, prompt, images, aspect, count):
            raise sr.JobError("Qwen API 400: InvalidParameter")
        r = self.runner(qwen=qwen)
        gen = r.submit(self.pid, self.req(model="qwen-image-3"), [])
        self.assertTrue(r.wait_idle(5))
        got = self.store.generation(self.pid, gen["id"])
        self.assertEqual([s["status"] for s in got["slots"]], ["error", "error"])

    def test_unexpected_exception_becomes_a_slot_error(self):
        def gemini(spec, prompt, images, aspect):
            raise RuntimeError("boom")
        r = self.runner(gemini=gemini)
        gen = r.submit(self.pid, self.req(count=1), [])
        self.assertTrue(r.wait_idle(5))
        self.assertEqual(self.store.generation(self.pid, gen["id"])["slots"][0]["error"], "internal error")

    def test_deleted_project_mid_run_is_ignored(self):
        started, release = threading.Event(), threading.Event()

        def gemini(spec, prompt, images, aspect):
            started.set()
            release.wait(5)
            return PNG
        r = self.runner(gemini=gemini)
        r.submit(self.pid, self.req(count=1), [])
        self.assertTrue(started.wait(5))
        self.store.delete_project(self.pid)
        release.set()
        self.assertTrue(r.wait_idle(5))
        self.assertEqual(self.store.list_projects(), [])


if __name__ == "__main__":
    unittest.main()
