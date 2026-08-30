# scripts/tests/test_batch_ingest.py
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tgbot.ingest import Probe, describe, probe, suggest_preset, to_png_if_heic


class TestSuggestPreset(unittest.TestCase):
    def test_picks_the_smallest_preset_that_fits(self):
        self.assertEqual(suggest_preset(4.2), "drv-5s")
        self.assertEqual(suggest_preset(5.0), "drv-5s")
        self.assertEqual(suggest_preset(14.8), "drv-15s")
        self.assertEqual(suggest_preset(15.0), "drv-15s")

    def test_over_thirty_seconds_still_maps_to_the_ceiling(self):
        # 34.1s is a real driver from batch/2026-08-28-lanczos-6cap.yaml. There
        # is no larger preset, and the job still runs: motion lowers fps and
        # keeps the length, character-swap trims the tail.
        self.assertEqual(suggest_preset(34.1), "drv-30s")

    def test_boundary_is_inclusive_at_the_ceiling(self):
        self.assertEqual(suggest_preset(30.0), "drv-30s")
        self.assertEqual(suggest_preset(30.1), "drv-30s")


class TestDescribe(unittest.TestCase):
    def test_video_line_carries_the_numbers_that_reveal_recompression(self):
        line = describe(Probe(kind="video", width=1080, height=1920, duration_s=30.1,
                              bitrate_kbps=12400, size_bytes=22_800_000))
        for token in ("1080x1920", "30.1", "12400", "22.8"):
            self.assertIn(token, line)

    def test_image_line_omits_duration_and_bitrate(self):
        line = describe(Probe(kind="image", width=1024, height=1536, duration_s=0.0,
                              bitrate_kbps=0, size_bytes=412_000))
        self.assertIn("1024x1536", line)
        self.assertNotIn("kbps", line)


class FakeFfprobeResult:
    """Stand-in for subprocess.CompletedProcess, patched in for ffprobe."""
    def __init__(self, data: dict, returncode: int = 0, stderr: str = ""):
        self.stdout = json.dumps(data)
        self.stderr = stderr
        self.returncode = returncode


class TestProbeKindDiscrimination(unittest.TestCase):
    def test_video_with_no_duration_raises_rather_than_becoming_an_image(self):
        # Measured 2026-08-31: a real 2-second raw h264 stream carries no
        # format.duration and no stream.duration at all. The old code read
        # that missing duration as "duration == 0.0" and called it an image —
        # which also means describe() would hide duration/bitrate on the
        # confirmation screen for what is actually a genuine video.
        fake = FakeFfprobeResult({
            "format": {"size": "123456"},
            "streams": [{"codec_type": "video", "codec_name": "h264",
                        "width": 1280, "height": 720}],
        })
        with patch("tgbot.ingest.subprocess.run", return_value=fake):
            with self.assertRaises(RuntimeError) as ctx:
                probe(Path("broken.mp4"))
        self.assertIn("broken.mp4", str(ctx.exception))

    def test_a_jpeg_is_an_image_even_though_ffprobe_calls_it_a_video_stream(self):
        # Pins the real-file finding from Step 5 of the original task: a real
        # JPEG's duration_s was 0.04, not exactly 0.0. Codec alone must decide.
        fake = FakeFfprobeResult({
            "format": {"duration": "0.04", "size": "412000"},
            "streams": [{"codec_type": "video", "codec_name": "mjpeg",
                        "width": 1024, "height": 1536, "duration": "0.04"}],
        })
        with patch("tgbot.ingest.subprocess.run", return_value=fake):
            p = probe(Path("photo.jpg"))
        self.assertEqual(p.kind, "image")


class TestToPngIfHeic(unittest.TestCase):
    def test_non_heic_path_returns_unchanged_and_invokes_no_subprocess(self):
        with patch("tgbot.ingest.subprocess.run") as run:
            result = to_png_if_heic(Path("driver.mp4"))
        self.assertEqual(result, Path("driver.mp4"))
        run.assert_not_called()

    def test_heic_on_darwin_invokes_the_converter_and_returns_the_png_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "photo.heic"
            src.write_bytes(b"fake heic bytes")
            dest = src.with_suffix(".png")

            def fake_run(cmd, **kwargs):
                dest.write_bytes(b"fake png bytes")
                return FakeFfprobeResult({}, returncode=0)

            with patch("tgbot.ingest.platform.system", return_value="Darwin"), \
                 patch("tgbot.ingest.subprocess.run", side_effect=fake_run) as run:
                result = to_png_if_heic(src)
            self.assertEqual(result, dest)
            self.assertTrue(dest.exists())
            self.assertIn("sips", run.call_args[0][0])

    def test_converter_exits_clean_but_writes_nothing_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "photo.heic"
            src.write_bytes(b"fake heic bytes")
            with patch("tgbot.ingest.platform.system", return_value="Darwin"), \
                 patch("tgbot.ingest.subprocess.run",
                      return_value=FakeFfprobeResult({}, returncode=0)):
                with self.assertRaises(RuntimeError) as ctx:
                    to_png_if_heic(src)
            self.assertIn("photo.heic", str(ctx.exception))

    def test_missing_converter_binary_raises_naming_what_to_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "photo.heic"
            src.write_bytes(b"fake heic bytes")
            with patch("tgbot.ingest.platform.system", return_value="Darwin"), \
                 patch("tgbot.ingest.subprocess.run", side_effect=FileNotFoundError):
                with self.assertRaises(RuntimeError) as ctx:
                    to_png_if_heic(src)
            self.assertIn("sips", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
