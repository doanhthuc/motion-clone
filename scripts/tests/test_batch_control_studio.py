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


if __name__ == "__main__":
    unittest.main()
