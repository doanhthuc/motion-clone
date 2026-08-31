import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tgbot.ingest import Probe
from tgbot.job import Job, missing_slots, render_manifest, slot_for

VIDEO = Probe(kind="video", width=1080, height=1920, duration_s=14.8,
              bitrate_kbps=12400, size_bytes=22_000_000)
IMAGE = Probe(kind="image", width=1024, height=1536, duration_s=0.0,
              bitrate_kbps=0, size_bytes=412_000)


class TestSlotInference(unittest.TestCase):
    def test_a_video_is_always_the_driver(self):
        # Structural, not a guess: driver is the only slot that takes video.
        self.assertEqual(slot_for(VIDEO, Job(slots={}, probes={}, pipeline="motion-enhance")), "driver")

    def test_an_image_is_ambiguous_and_must_be_asked(self):
        # Guessing character vs outfit vs background from a filename would burn
        # a real $1 batch on a mislabeled outfit.
        self.assertIsNone(slot_for(IMAGE, Job(slots={}, probes={}, pipeline="tryon-motion-enhance")))


class TestMissingSlots(unittest.TestCase):
    def test_motion_enhance_needs_character_and_driver(self):
        job = Job(slots={}, probes={}, pipeline="motion-enhance")
        self.assertEqual(sorted(missing_slots(job)), ["character", "driver"])

    def test_background_is_optional_for_tryon(self):
        job = Job(slots={"character": Path("/c.png"), "outfit": Path("/o.png"),
                         "driver": Path("/d.mp4")},
                  probes={}, pipeline="tryon-motion-enhance")
        self.assertEqual(missing_slots(job), [])


class TestRenderManifest(unittest.TestCase):
    def test_carries_the_measured_numbers_as_comments(self):
        job = Job(slots={"character": Path("/c.png"), "driver": Path("/d.mp4")},
                  probes={"driver": VIDEO}, pipeline="motion-enhance")
        text = render_manifest(job, now="2026-08-31 21:40")
        self.assertIn("2026-08-31 21:40", text)
        self.assertIn("14.8", text)          # the measurement, not a guess
        self.assertIn("drv-15s", text)       # the preset it implies

    def test_output_is_valid_yaml_the_existing_loader_accepts(self):
        import tempfile, yaml
        job = Job(slots={"character": Path("/c.png"), "driver": Path("/d.mp4")},
                  probes={"driver": VIDEO}, pipeline="motion-enhance")
        data = yaml.safe_load(render_manifest(job, now="2026-08-31 21:40"))
        self.assertEqual(data["runs"][0]["pipeline"], "motion-enhance")
        self.assertIn("character", data["runs"][0]["inputs"])

    def test_preset_lands_on_the_stage_that_actually_consumes_driver(self):
        # _driver_stage() resolves the driver-consuming stage from PIPELINES/STAGES
        # rather than hardcoding "motion" — character-swap-enhance runs
        # character-swap, not motion, so the preset param must land there. The
        # negative assertion (no "motion:" block) is what actually pins this:
        # without it, a regression that always emitted "motion:" would pass the
        # positive check alone by coincidence.
        job = Job(slots={"character": Path("/c.png"), "driver": Path("/d.mp4")},
                  probes={"driver": VIDEO}, pipeline="character-swap-enhance")
        text = render_manifest(job, now="2026-08-31 21:40")
        self.assertIn("character-swap: { preset: drv-15s }", text)
        self.assertNotIn("motion:", text)


if __name__ == "__main__":
    unittest.main()
