import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from control.outputs import final_names, list_outputs, resolve_output


class TestOutputs(unittest.TestCase):
    def setUp(self):
        self.out = Path(tempfile.mkdtemp())
        for batch, mtime in (("b-old", 1000), ("b-new", 2000)):
            final = self.out / batch / "_final"
            final.mkdir(parents=True)
            (final / "x.mp4").write_bytes(b"12345")
            (final / "y.png").write_bytes(b"1")
            (final / "_index.tsv").write_text("t")
            os.utime(final, (mtime, mtime))
        (self.out / "b-empty" / "runs").mkdir(parents=True)
        (self.out / "b-new" / "runs" / "r").mkdir(parents=True)
        (self.out / "b-new" / "runs" / "r" / "01-motion.mp4").write_bytes(b"i")

    def test_final_names_filters_by_suffix(self):
        self.assertEqual(final_names(self.out, "b-new"), ["x.mp4", "y.png"])

    def test_final_names_of_a_hostile_batch_is_empty(self):
        self.assertEqual(final_names(self.out, "../etc"), [])

    def test_list_is_newest_first_and_skips_batches_without_final(self):
        listed = list_outputs(self.out)
        self.assertEqual([b["batch"] for b in listed], ["b-new", "b-old"])
        self.assertEqual(listed[0]["files"], [{"name": "x.mp4", "bytes": 5},
                                              {"name": "y.png", "bytes": 1}])

    def test_resolve_only_inside_final(self):
        self.assertEqual(resolve_output(self.out, "b-new", "x.mp4"),
                         (self.out / "b-new" / "_final" / "x.mp4").resolve())
        self.assertIsNone(resolve_output(self.out, "b-new", "01-motion.mp4"))
        self.assertIsNone(resolve_output(self.out, "b-new", "_index.tsv"))
        self.assertIsNone(resolve_output(self.out, "..", "x.mp4"))
        self.assertIsNone(resolve_output(self.out, "b-new", "../runs/r/01-motion.mp4"))
        self.assertIsNone(resolve_output(self.out, "b-new", "missing.mp4"))

    def test_symlinked_batch_dir_is_excluded(self):
        # runner.py maintains out/latest -> newest batch dir (runner.py
        # ~371-374). Without this, list_outputs's is_dir() follows the
        # symlink and lists "latest" as a second copy of "b-new", and
        # resolve_output would happily serve it too.
        (self.out / "latest").symlink_to("b-new")
        listed = list_outputs(self.out)
        self.assertEqual([b["batch"] for b in listed], ["b-new", "b-old"])
        self.assertIsNone(resolve_output(self.out, "latest", "x.mp4"))
        self.assertEqual(final_names(self.out, "latest"), [])


if __name__ == "__main__":
    unittest.main()
