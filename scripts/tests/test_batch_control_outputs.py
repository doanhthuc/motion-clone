import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import control.outputs as outputs
from control.materials import MaterialError
from control.outputs import final_names, list_outputs, poster, prune_posters, resolve_output


class TestOutputs(unittest.TestCase):
    def setUp(self):
        self.out = Path(tempfile.mkdtemp())
        for batch, mtime in (("b-old", 1000), ("b-new", 2000)):
            final = self.out / batch / "_final"
            final.mkdir(parents=True)
            (final / "x.mp4").write_bytes(b"12345")
            (final / "y.png").write_bytes(b"1")
            (final / "_index.tsv").write_text("t")
            for f in ("x.mp4", "y.png"):
                os.utime(final / f, (mtime, mtime))
        (self.out / "b-empty" / "runs").mkdir(parents=True)
        (self.out / "b-new" / "runs" / "r").mkdir(parents=True)
        (self.out / "b-new" / "runs" / "r" / "01-motion.mp4").write_bytes(b"i")

    def test_final_names_filters_by_suffix(self):
        self.assertEqual(final_names(self.out, "b-new"), ["x.mp4", "y.png"])

    def test_final_names_of_a_hostile_batch_is_empty(self):
        self.assertEqual(final_names(self.out, "../etc"), [])

    def test_list_is_newest_first_and_skips_batches_without_final(self):
        with mock.patch.object(outputs, "_duration", return_value=12.5):
            listed = list_outputs(self.out)
        self.assertEqual([b["batch"] for b in listed], ["b-new", "b-old"])
        self.assertEqual(listed[0]["updated_at"], 2000)
        # Only videos carry a duration.
        self.assertEqual(listed[0]["files"], [{"name": "x.mp4", "bytes": 5, "duration": 12.5},
                                              {"name": "y.png", "bytes": 1}])

    def test_posters_dir_neither_lists_nor_reorders(self):
        # Creating .posters/ in the old batch touches _final/'s mtime; the
        # order must follow the files, not the directory.
        (self.out / "b-old" / "_final" / ".posters").mkdir()
        (self.out / "b-old" / "_final" / ".posters" / "x.mp4.jpg").write_bytes(b"j")
        with mock.patch.object(outputs, "_duration", return_value=None):
            listed = list_outputs(self.out)
        self.assertEqual([b["batch"] for b in listed], ["b-new", "b-old"])
        self.assertEqual([f["name"] for f in listed[1]["files"]], ["x.mp4", "y.png"])

    def test_duration_is_probed_once_per_file_version(self):
        video = self.out / "b-new" / "_final" / "x.mp4"
        probe = mock.Mock(return_value=mock.Mock(returncode=0, stdout="14.2\n"))
        with mock.patch.object(outputs.subprocess, "run", probe):
            self.assertEqual(outputs._duration(video), 14.2)
            self.assertEqual(outputs._duration(video), 14.2)
            self.assertEqual(probe.call_count, 1)
            os.utime(video, (3000, 3000))       # re-rendered in place
            outputs._duration(video)
            self.assertEqual(probe.call_count, 2)

    def test_duration_without_ffprobe_is_not_cached(self):
        video = self.out / "b-new" / "_final" / "x.mp4"
        with mock.patch.object(outputs.subprocess, "run", side_effect=FileNotFoundError):
            self.assertIsNone(outputs._duration(video))
        self.assertFalse((video.parent / ".posters" / "x.mp4.json").exists())

    def test_poster_renders_beside_the_file_and_reuses_it(self):
        def fake_render(src, dest, width, *, video):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"jpeg")
            return True
        render = mock.Mock(side_effect=fake_render)
        with mock.patch.object(outputs, "render_frame", render):
            path = poster(self.out, "b-new", "x.mp4")
            self.assertEqual(path, (self.out / "b-new" / "_final" / ".posters" / "x.mp4.jpg").resolve())
            self.assertTrue(render.call_args.kwargs["video"])
            poster(self.out, "b-new", "x.mp4")
        self.assertEqual(render.call_count, 1)

    def test_poster_errors(self):
        with self.assertRaises(MaterialError) as missing:
            poster(self.out, "b-new", "../runs/r/01-motion.mp4")
        self.assertEqual(missing.exception.code, "not_found")
        with mock.patch.object(outputs, "render_frame", return_value=False):
            with self.assertRaises(MaterialError) as broken:
                poster(self.out, "b-new", "x.mp4")
        self.assertEqual(broken.exception.code, "unprobeable")

    def test_prune_removes_only_orphaned_posters(self):
        posters = self.out / "b-new" / "_final" / ".posters"
        posters.mkdir()
        for name in ("x.mp4.jpg", "x.mp4.json", "gone.mp4.jpg", "gone.mp4.json"):
            (posters / name).write_text("p")
        removed = prune_posters(self.out)
        self.assertEqual(sorted(p.name for p in removed), ["gone.mp4.jpg", "gone.mp4.json"])
        self.assertEqual(sorted(p.name for p in posters.iterdir()), ["x.mp4.jpg", "x.mp4.json"])

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
