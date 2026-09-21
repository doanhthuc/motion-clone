import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from control import materials


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


if __name__ == "__main__":
    unittest.main()
