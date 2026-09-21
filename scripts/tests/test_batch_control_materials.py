import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from control import materials
import tgbot.run as run_mod


class TestNaming(unittest.TestCase):
    def test_same_rules_as_the_bot(self):
        self.assertEqual(materials.safe_name("áo dài.jpg"), "ao_dai.jpg")
        self.assertEqual(materials.safe_name("写真.heic"), "file.heic")
        self.assertEqual(materials.safe_name("my driver:v1.mp4"), "my_driver_v1.mp4")
        self.assertEqual(materials.fold_diacritics("／"), "／")

    def test_bot_aliases_are_the_moved_functions(self):
        import tgbot.bot as bot
        self.assertIs(bot._safe_name, materials.safe_name)
        self.assertIs(bot._fold_diacritics, materials.fold_diacritics)


class TestStageFile(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dest = self.tmp / "staging" / "app"

    def src(self, name="x.png", data=b"data"):
        p = self.tmp / name
        p.write_bytes(data)
        return p

    def test_copy_keeps_source(self):
        s = self.src()
        out = materials.stage_file(self.dest, s, "Blue Dress.png")
        self.assertEqual(out, self.dest / "Blue_Dress.png")
        self.assertTrue(s.exists())

    def test_move_removes_source(self):
        s = self.src()
        out = materials.stage_file(self.dest, s, "a.png", move=True)
        self.assertFalse(s.exists())
        self.assertEqual(out.read_bytes(), b"data")

    def test_never_overwrites_and_reserves_heic_png_twin(self):
        materials.stage_file(self.dest, self.src(), "photo.png")
        out = materials.stage_file(self.dest, self.src("y.heic"), "photo.heic")
        self.assertEqual(out.name, "photo-1.heic")

    def test_concurrent_staging_never_collides(self):
        results = []
        def worker(i):
            results.append(materials.stage_file(self.dest, self.src(f"s{i}.png"), "same.png"))
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(len({p.name for p in results}), 8)


class TestPrune(unittest.TestCase):
    def test_removes_only_old_files(self):
        root = Path(tempfile.mkdtemp())
        (root / "app").mkdir()
        old, new = root / "app" / "old.mp4", root / "app" / "new.mp4"
        old.write_bytes(b"o"); new.write_bytes(b"n")
        now = time.time()
        os.utime(old, (now - 8 * 86400, now - 8 * 86400))
        self.assertEqual(materials.prune_staged(root, 7, now), [old])
        self.assertTrue(new.exists())


def make_png(path: Path) -> Path:
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=red:s=640x480",
                    "-frames:v", "1", str(path)], check=True)
    return path


class MaterialsBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.staging = self.tmp / "tg-staging"
        self.batch = self.tmp
        (self.staging / "app").mkdir(parents=True)
        (self.staging / "12345").mkdir()
        self.a = self.staging / "app" / "a.mp4"; self.a.write_bytes(b"v" * 10)
        self.b = self.staging / "12345" / "b.png"; self.b.write_bytes(b"i")
        os.utime(self.a, (1000, 1000)); os.utime(self.b, (2000, 2000))
        p = mock.patch.object(run_mod, "busy", return_value=False)
        p.start(); self.addCleanup(p.stop)


class TestList(MaterialsBase):
    def test_global_newest_first_with_kind(self):
        got = materials.list_materials(self.staging)
        self.assertEqual([m["id"] for m in got], ["12345/b.png", "app/a.mp4"])
        self.assertEqual(got[1]["kind"], "video")
        self.assertEqual(got[1]["bytes"], 10)
        self.assertNotIn(str(self.tmp), json.dumps(got))

    def test_material_item_matches_the_list_entry(self):
        got = materials.list_materials(self.staging)
        listed = next(m for m in got if m["id"] == "app/a.mp4")
        self.assertEqual(materials.material_item("app", self.a), listed)

    def test_material_item_of_a_vanished_file_is_file_not_found(self):
        missing = self.staging / "app" / "gone.mp4"
        with self.assertRaises(FileNotFoundError):
            materials.material_item("app", missing)

    def test_symlinks_are_not_material(self):
        os.symlink(self.a, self.staging / "app" / "link.mp4")
        self.assertNotIn("app/link.mp4", [m["id"] for m in materials.list_materials(self.staging)])

    def test_resolve_refuses_traversal(self):
        self.assertEqual(materials.resolve_material(self.staging, "app", "a.mp4"), self.a.resolve())
        for owner, name in (("..", "a.mp4"), ("app", "../12345/b.png"), ("app", "nope.mp4")):
            self.assertIsNone(materials.resolve_material(self.staging, owner, name))


class TestDelete(MaterialsBase):
    def test_deletes_app_material(self):
        materials.delete_material(self.staging, self.batch, "app", "a.mp4")
        self.assertFalse(self.a.exists())

    def test_telegram_material_is_forbidden(self):
        with self.assertRaises(materials.MaterialError) as cm:
            materials.delete_material(self.staging, self.batch, "12345", "b.png")
        self.assertEqual(cm.exception.code, "forbidden")

    def test_unknown_is_not_found(self):
        with self.assertRaises(materials.MaterialError) as cm:
            materials.delete_material(self.staging, self.batch, "app", "nope.mp4")
        self.assertEqual(cm.exception.code, "not_found")

    def test_in_use_by_a_busy_manifest(self):
        (self.batch / "r.yaml").write_text(f"runs:\n  - inputs: {{driver: {self.a.resolve()}}}\n")
        with mock.patch.object(run_mod, "busy", return_value=True):
            with self.assertRaises(materials.MaterialError) as cm:
                materials.delete_material(self.staging, self.batch, "app", "a.mp4")
        self.assertEqual(cm.exception.code, "in_use")
        self.assertTrue(self.a.exists())

    def test_a_finished_manifest_does_not_block(self):
        (self.batch / "r.yaml").write_text(f"runs:\n  - inputs: {{driver: {self.a.resolve()}}}\n")
        materials.delete_material(self.staging, self.batch, "app", "a.mp4")
        self.assertFalse(self.a.exists())

    def test_in_use_ignores_a_path_that_merely_starts_with_it(self):
        # A manifest naming "<path>.bak" must not refuse deleting "<path>" —
        # `needle in text` would match here, a bare-substring bug this guards.
        (self.batch / "r.yaml").write_text(f"driver: {self.a.resolve()}.bak\n")
        with mock.patch.object(run_mod, "busy", return_value=True):
            materials.delete_material(self.staging, self.batch, "app", "a.mp4")
        self.assertFalse(self.a.exists())

    def test_in_use_matches_the_exact_path_at_end_of_line(self):
        (self.batch / "r.yaml").write_text(f"driver: {self.a.resolve()}\n")
        with mock.patch.object(run_mod, "busy", return_value=True):
            with self.assertRaises(materials.MaterialError) as cm:
                materials.delete_material(self.staging, self.batch, "app", "a.mp4")
        self.assertEqual(cm.exception.code, "in_use")

    def test_owner_is_stripped_before_the_app_check(self):
        materials.delete_material(self.staging, self.batch, " app ", "a.mp4")
        self.assertFalse(self.a.exists())


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg required")
class TestThumbAndIngest(MaterialsBase):
    def test_thumbnail_is_cached_and_320_wide(self):
        make_png(self.staging / "app" / "p.png")
        thumbs = self.tmp / "thumbs"
        t1 = materials.thumbnail(self.staging, thumbs, "app", "p.png")
        self.assertEqual(t1, thumbs / "app" / "p.png.jpg")
        mtime = t1.stat().st_mtime
        t2 = materials.thumbnail(self.staging, thumbs, "app", "p.png")
        self.assertEqual(t2.stat().st_mtime, mtime)          # cached, not regenerated
        from tgbot import ingest
        self.assertEqual(ingest.probe(t1).width, 320)

    def test_thumbnail_path_strips_whitespace_in_name(self):
        make_png(self.staging / "app" / "p.png")
        t = materials.thumbnail(self.staging, self.tmp / "thumbs", "app", " p.png ")
        self.assertEqual(t, self.tmp / "thumbs" / "app" / "p.png.jpg")

    def test_thumbnail_of_garbage_is_unprobeable(self):
        with self.assertRaises(materials.MaterialError) as cm:
            materials.thumbnail(self.staging, self.tmp / "thumbs", "app", "a.mp4")
        self.assertEqual(cm.exception.code, "unprobeable")

    def test_ingest_probes_an_image(self):
        path, info = materials.ingest(make_png(self.staging / "app" / "q.png"))
        self.assertEqual((info["kind"], info["width"], info["height"]), ("image", 640, 480))
        self.assertEqual(info["warning"], "")

    def test_ingest_of_garbage_is_unprobeable(self):
        with self.assertRaises(materials.MaterialError) as cm:
            materials.ingest(self.a)
        self.assertEqual(cm.exception.code, "unprobeable")


if __name__ == "__main__":
    unittest.main()
