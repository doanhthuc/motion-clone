import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tgbot.tiktok as tiktok


class TestFindUrl(unittest.TestCase):
    def test_a_bare_tiktok_link_is_found(self):
        self.assertEqual(
            tiktok.find_url("https://www.tiktok.com/@user/video/123"),
            "https://www.tiktok.com/@user/video/123")

    def test_a_short_link_embedded_in_other_text_is_found(self):
        self.assertEqual(
            tiktok.find_url("check this out https://vt.tiktok.com/ZS8abcde/ nice"),
            "https://vt.tiktok.com/ZS8abcde/")

    def test_text_with_no_link_finds_nothing(self):
        self.assertIsNone(tiktok.find_url("character / outfit / background"))

    def test_a_non_tiktok_url_is_not_mistaken_for_one(self):
        self.assertIsNone(tiktok.find_url("https://example.com/tiktok.com/video"))


class TestDownload(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        mkdtemp_patcher = mock.patch("tgbot.tiktok.tempfile.mkdtemp",
                                     return_value=str(self.tmp_dir))
        mkdtemp_patcher.start()
        self.addCleanup(mkdtemp_patcher.stop)
        which_patcher = mock.patch("tgbot.tiktok.shutil.which",
                                   return_value="/usr/local/bin/yt-dlp")
        which_patcher.start()
        self.addCleanup(which_patcher.stop)

    @staticmethod
    def _fake_proc(lines: list[str], returncode: int = 0) -> mock.Mock:
        proc = mock.Mock()
        proc.stdout = iter(lines)
        proc.wait = mock.Mock()
        proc.poll = mock.Mock(return_value=returncode)
        proc.returncode = returncode
        return proc

    def test_reports_each_progress_percentage_as_it_arrives(self):
        (self.tmp_dir / "video.mp4").write_bytes(b"x")
        lines = ["[download]  10.0% of 5.00MiB at 1.00MiB/s ETA 00:04\n",
                 "[download]  55.5% of 5.00MiB at 1.00MiB/s ETA 00:02\n",
                 "[download] 100.0% of 5.00MiB at 1.00MiB/s ETA 00:00\n"]
        seen: list[float] = []
        with mock.patch("tgbot.tiktok.subprocess.Popen",
                        return_value=self._fake_proc(lines)):
            path = tiktok.download("https://vt.tiktok.com/x", on_progress=seen.append)
        self.assertEqual(seen, [10.0, 55.5, 100.0])
        self.assertEqual(path, self.tmp_dir / "video.mp4")

    def test_lines_with_no_percentage_are_ignored(self):
        (self.tmp_dir / "video.mp4").write_bytes(b"x")
        lines = ["[generic] extracting url\n", "[download]  50.0% of 5.00MiB\n"]
        seen: list[float] = []
        with mock.patch("tgbot.tiktok.subprocess.Popen",
                        return_value=self._fake_proc(lines)):
            tiktok.download("https://vt.tiktok.com/x", on_progress=seen.append)
        self.assertEqual(seen, [50.0])

    def test_a_nonzero_exit_raises_with_the_tail_of_the_output(self):
        lines = ["ERROR: Unable to extract video data\n"]
        with mock.patch("tgbot.tiktok.subprocess.Popen",
                        return_value=self._fake_proc(lines, returncode=1)):
            with self.assertRaises(RuntimeError) as cm:
                tiktok.download("https://vt.tiktok.com/x")
        self.assertIn("Unable to extract video data", str(cm.exception))

    def test_success_with_no_file_produced_still_raises(self):
        # Defends against a silently-accepted job with nothing behind the
        # slot: probe() would otherwise be handed a path that does not exist.
        with mock.patch("tgbot.tiktok.subprocess.Popen",
                        return_value=self._fake_proc([], returncode=0)):
            with self.assertRaises(RuntimeError):
                tiktok.download("https://vt.tiktok.com/x")

    def test_missing_yt_dlp_binary_raises_before_spawning_anything(self):
        with mock.patch("tgbot.tiktok.shutil.which", return_value=None):
            with mock.patch("tgbot.tiktok.subprocess.Popen") as popen:
                with self.assertRaises(RuntimeError) as cm:
                    tiktok.download("https://vt.tiktok.com/x")
                popen.assert_not_called()
        self.assertIn("yt-dlp", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
