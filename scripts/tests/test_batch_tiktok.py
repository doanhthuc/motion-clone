import json
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
        # The tikwm fallback is real network I/O — off by default so every
        # test below that doesn't care about it can't accidentally reach the
        # internet. Tests that DO care override this with their own patch.
        tikwm_patcher = mock.patch("tgbot.tiktok._download_via_tikwm",
                                   side_effect=RuntimeError("tikwm disabled in tests"))
        tikwm_patcher.start()
        self.addCleanup(tikwm_patcher.stop)

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

    def test_an_audio_only_result_raises_a_clear_error_not_stopiteration(self):
        # Some TikTok videos expose only an audio format to yt-dlp (observed
        # 2026-09-06). Without this check the caller stages a fake ".mp4" and
        # ingest.probe() fails with an opaque StopIteration instead of naming
        # the real cause.
        (self.tmp_dir / "video.mp3").write_bytes(b"x")
        with mock.patch("tgbot.tiktok.subprocess.Popen",
                        return_value=self._fake_proc([], returncode=0)):
            with self.assertRaises(RuntimeError) as cm:
                tiktok.download("https://vt.tiktok.com/x")
        self.assertIn("audio only", str(cm.exception))

    def test_missing_yt_dlp_binary_raises_before_spawning_anything(self):
        with mock.patch("tgbot.tiktok.shutil.which", return_value=None):
            with mock.patch("tgbot.tiktok.subprocess.Popen") as popen:
                with self.assertRaises(RuntimeError) as cm:
                    tiktok.download("https://vt.tiktok.com/x")
                popen.assert_not_called()
        self.assertIn("yt-dlp", str(cm.exception))

    def test_falls_back_to_tikwm_when_ytdlp_fails(self):
        # yt-dlp and tikwm fail independently (2026-09-06: TikTok blanks the
        # CDN url for non-app-signed traffic, which yt-dlp's request is and
        # tikwm's replicated-app-signature request isn't) — a fallback only
        # earns its keep if download() actually reaches for it.
        fallback_path = self.tmp_dir / "from-tikwm.mp4"
        fallback_path.write_bytes(b"x")
        with mock.patch("tgbot.tiktok.subprocess.Popen",
                        return_value=self._fake_proc([], returncode=1)):
            with mock.patch("tgbot.tiktok._download_via_tikwm",
                            return_value=fallback_path) as tikwm:
                path = tiktok.download("https://vt.tiktok.com/x")
        self.assertEqual(path, fallback_path)
        tikwm.assert_called_once()

    def test_a_combined_error_names_both_failures_when_fallback_also_fails(self):
        lines = ["ERROR: Unable to extract video data\n"]
        with mock.patch("tgbot.tiktok.subprocess.Popen",
                        return_value=self._fake_proc(lines, returncode=1)):
            with mock.patch("tgbot.tiktok._download_via_tikwm",
                            side_effect=RuntimeError("tikwm lookup failed: boom")):
                with self.assertRaises(RuntimeError) as cm:
                    tiktok.download("https://vt.tiktok.com/x")
        self.assertIn("Unable to extract video data", str(cm.exception))
        self.assertIn("tikwm lookup failed: boom", str(cm.exception))


class _FakeResponse:
    """Just enough of urllib's response object for _download_via_tikwm: a
    context manager with a buffered .read(), since shutil.copyfileobj calls
    it repeatedly with a byte count until it returns empty.
    """
    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            chunk, self._pos = self._data[self._pos:], len(self._data)
            return chunk
        chunk = self._data[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestTikwmFallback(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        # The retry backoff in _tikwm_lookup() sleeps for real otherwise —
        # every test below exercises the lookup, so this is set up once.
        sleep_patcher = mock.patch("tgbot.tiktok.time.sleep")
        sleep_patcher.start()
        self.addCleanup(sleep_patcher.stop)

    def test_downloads_the_play_url_from_a_successful_lookup(self):
        video_resp = _FakeResponse(b"fake video bytes")
        by_url = {
            tiktok._TIKWM_API + "?url=https%3A%2F%2Fvt.tiktok.com%2Fx":
                lambda: _FakeResponse(json.dumps(
                    {"code": 0, "data": {"play": "https://cdn.example/video.mp4"}}).encode()),
            "https://cdn.example/video.mp4": lambda: video_resp,
        }
        with mock.patch("tgbot.tiktok.urllib.request.urlopen",
                        side_effect=lambda req, timeout=None: by_url[req.full_url]()):
            path = tiktok._download_via_tikwm("https://vt.tiktok.com/x",
                                              self.tmp_dir, timeout=30)
        self.assertEqual(path, self.tmp_dir / "video.mp4")
        self.assertEqual(path.read_bytes(), b"fake video bytes")

    def test_a_nonzero_api_code_retries_then_raises_with_its_own_message(self):
        # A fresh _FakeResponse per call: _tikwm_lookup() retries
        # _TIKWM_LOOKUP_ATTEMPTS times, and a response object's .read() is
        # single-use, same as a real urlopen() result would be.
        make_resp = lambda: _FakeResponse(json.dumps({"code": -1, "msg": "url invalid"}).encode())
        with mock.patch("tgbot.tiktok.urllib.request.urlopen",
                        side_effect=lambda *a, **k: make_resp()) as urlopen:
            with self.assertRaises(RuntimeError) as cm:
                tiktok._download_via_tikwm("https://vt.tiktok.com/x", self.tmp_dir, timeout=30)
        self.assertIn("url invalid", str(cm.exception))
        self.assertEqual(urlopen.call_count, tiktok._TIKWM_LOOKUP_ATTEMPTS)

    def test_a_response_with_no_play_url_raises_without_retrying(self):
        # code == 0 means the lookup itself succeeded — a missing play url
        # is a shape yt-dlp already handles, not tikwm's own rate limit, so
        # retrying it would only add latency for the same result.
        api_resp = _FakeResponse(json.dumps({"code": 0, "data": {}}).encode())
        with mock.patch("tgbot.tiktok.urllib.request.urlopen",
                        return_value=api_resp) as urlopen:
            with self.assertRaises(RuntimeError) as cm:
                tiktok._download_via_tikwm("https://vt.tiktok.com/x", self.tmp_dir, timeout=30)
        self.assertIn("no video url", str(cm.exception))
        self.assertEqual(urlopen.call_count, 1)

    def test_a_network_error_retries_then_raises_runtimeerror_not_urlerror(self):
        with mock.patch("tgbot.tiktok.urllib.request.urlopen",
                        side_effect=tiktok.urllib.error.URLError("boom")) as urlopen:
            with self.assertRaises(RuntimeError):
                tiktok._download_via_tikwm("https://vt.tiktok.com/x", self.tmp_dir, timeout=30)
        self.assertEqual(urlopen.call_count, tiktok._TIKWM_LOOKUP_ATTEMPTS)

    def test_a_transient_error_that_clears_on_retry_still_succeeds(self):
        video_resp = _FakeResponse(b"fake video bytes")
        responses = iter([
            tiktok.urllib.error.URLError("rate limited"),
            _FakeResponse(json.dumps(
                {"code": 0, "data": {"play": "https://cdn.example/video.mp4"}}).encode()),
        ])
        def fake_urlopen(req, timeout=None):
            if req.full_url == "https://cdn.example/video.mp4":
                return video_resp
            item = next(responses)
            if isinstance(item, Exception):
                raise item
            return item
        with mock.patch("tgbot.tiktok.urllib.request.urlopen", side_effect=fake_urlopen):
            path = tiktok._download_via_tikwm("https://vt.tiktok.com/x",
                                              self.tmp_dir, timeout=30)
        self.assertEqual(path.read_bytes(), b"fake video bytes")


if __name__ == "__main__":
    unittest.main()
