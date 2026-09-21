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
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "app").mkdir()

    def old(self, name, now):
        path = self.root / "app" / name
        path.write_bytes(b"o")
        os.utime(path, (now - 8 * 86400, now - 8 * 86400))
        return path

    def test_removes_only_old_files(self):
        now = time.time()
        old = self.old("old.mp4", now)
        new = self.root / "app" / "new.mp4"; new.write_bytes(b"n")
        self.assertEqual(materials.prune_staged(self.root, 7, now), [old])
        self.assertTrue(new.exists())

    def test_a_file_deleted_before_the_stat_does_not_abort_the_sweep(self):
        # A DELETE (or /clear) between iterdir and stat used to raise
        # FileNotFoundError out of the whole tick, skipping the upload and
        # thumbnail sweeps for another 24 h.
        now = time.time()
        vanishing, other = self.old("a.mp4", now), self.old("b.mp4", now)
        real_is_file = Path.is_file

        def is_file(self):
            result = real_is_file(self)
            if self == vanishing:
                os.unlink(self)                 # gone between the listing and the stat
            return result

        with mock.patch.object(Path, "is_file", is_file):
            removed = materials.prune_staged(self.root, 7, now)
        self.assertIn(other, removed)
        self.assertFalse(other.exists())

    def test_a_file_deleted_before_the_unlink_does_not_abort_the_sweep(self):
        now = time.time()
        vanishing, other = self.old("a.mp4", now), self.old("b.mp4", now)
        real_unlink = Path.unlink

        def unlink(self, missing_ok=False):
            if self == vanishing:
                os.unlink(self)                 # gone between the stat and the unlink
            return real_unlink(self, missing_ok=missing_ok)

        with mock.patch.object(Path, "unlink", unlink):
            removed = materials.prune_staged(self.root, 7, now)
        self.assertIn(other, removed)
        self.assertFalse(other.exists())


class TestTickPrunesIndependently(unittest.TestCase):
    """One failing sweep must not skip the other two for another 24 h."""

    def setUp(self):
        import tgbot.bot as bot
        self.bot = bot
        bot._LAST_STAGING_PRUNE = 0.0
        self.addCleanup(setattr, bot, "_LAST_STAGING_PRUNE", 0.0)

    def test_a_failing_sweep_does_not_skip_the_others(self):
        with mock.patch.object(self.bot, "_prune_old_staged_files",
                               side_effect=OSError("disk hiccup")), \
             mock.patch.object(self.bot.uploads, "prune_uploads", return_value=[]) as up, \
             mock.patch.object(self.bot.materials, "prune_thumbs", return_value=[]) as th, \
             mock.patch.object(self.bot, "log") as log:
            self.bot._tick_staging_prune()
        up.assert_called_once()
        th.assert_called_once()
        self.assertTrue(any("disk hiccup" in str(c) for c in log.call_args_list),
                        log.call_args_list)


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


class TestThumbConcurrency(unittest.TestCase):
    """ffmpeg is capped and each run gets its own temp file (1 GB box)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.staging, self.thumbs = self.tmp / "tg-staging", self.tmp / "thumbs"
        (self.staging / "app").mkdir(parents=True)
        for n in range(4):
            (self.staging / "app" / f"p{n}.png").write_bytes(b"i")

    def test_temp_names_are_unique_and_removed(self):
        seen = []

        def fake_run(cmd, **kwargs):
            seen.append(cmd[-1])
            Path(cmd[-1]).write_bytes(b"\xff\xd8jpeg")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with mock.patch.object(materials.subprocess, "run", side_effect=fake_run):
            materials.thumbnail(self.staging, self.thumbs, "app", "p0.png")
            materials.thumbnail(self.staging, self.thumbs, "app", "p1.png")
        self.assertEqual(len(set(seen)), 2)
        self.assertEqual(list((self.thumbs / "app").glob("*.tmp*")), [])

    def test_at_most_two_ffmpeg_runs_at_once(self):
        inside, peak, lock = [], [0], threading.Lock()
        two_inside, release = threading.Event(), threading.Event()

        def fake_run(cmd, **kwargs):
            with lock:
                inside.append(1)
                peak[0] = max(peak[0], len(inside))
                if len(inside) == 2:
                    two_inside.set()
            release.wait(10)
            with lock:
                inside.pop()
            Path(cmd[-1]).write_bytes(b"\xff\xd8jpeg")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with mock.patch.object(materials.subprocess, "run", side_effect=fake_run):
            workers = [threading.Thread(
                target=materials.thumbnail,
                args=(self.staging, self.thumbs, "app", f"p{n}.png"), daemon=True)
                for n in range(4)]
            for w in workers:
                w.start()
            try:
                self.assertTrue(two_inside.wait(5))
                # All slots taken, so the other two threads cannot be running ffmpeg.
                self.assertFalse(materials._FFMPEG_SLOTS.acquire(blocking=False))
            finally:
                release.set()
                for w in workers:
                    w.join(10)
        self.assertEqual(peak[0], 2)


class TestDeleteVsAppDraft(unittest.TestCase):
    def test_delete_refuses_material_the_app_draft_uses(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        batch, staging = tmp / "batch", tmp / "batch" / "tg-staging"
        (staging / "app").mkdir(parents=True)
        used, free = staging / "app" / "a.png", staging / "app" / "a.png.bak"
        used.write_bytes(b"x"); free.write_bytes(b"x")
        # .resolve(): the store always saves a resolved path (dump_jobs, fed
        # by materials.resolve_material -> control.paths.safe_child), same
        # reason the manifest tests above compare against self.a.resolve().
        (batch / "app.draft.json").write_text(
            json.dumps({"slots": {"character": str(used.resolve())}}, indent=2))
        with self.assertRaises(materials.MaterialError) as cm:
            materials.delete_material(staging, batch, "app", "a.png")
        self.assertEqual(cm.exception.code, "in_use")
        materials.delete_material(staging, batch, "app", "a.png.bak")   # whole-path match only
        self.assertFalse(free.exists())


class TestPruneThumbs(unittest.TestCase):
    def test_removes_thumbs_whose_source_is_gone(self):
        tmp = Path(tempfile.mkdtemp())
        staging, thumbs = tmp / "tg-staging", tmp / "thumbs"
        (staging / "app").mkdir(parents=True); (thumbs / "app").mkdir(parents=True)
        (staging / "app" / "keep.png").write_bytes(b"k")
        keep, gone = thumbs / "app" / "keep.png.jpg", thumbs / "app" / "gone.png.jpg"
        keep.write_bytes(b"j"); gone.write_bytes(b"j")
        self.assertEqual(materials.prune_thumbs(thumbs, staging), [gone])
        self.assertTrue(keep.exists())


if __name__ == "__main__":
    unittest.main()
