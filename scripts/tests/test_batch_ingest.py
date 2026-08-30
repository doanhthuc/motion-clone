# scripts/tests/test_batch_ingest.py
import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tgbot.ingest import Probe, describe, suggest_preset


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


if __name__ == "__main__":
    unittest.main()
