import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tgbot.ingest import Probe
from tgbot.job import run_id_for, Job, missing_slots, render_manifest, slot_for

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
        text = render_manifest([job], now="2026-08-31 21:40")
        self.assertIn("2026-08-31 21:40", text)
        self.assertIn("14.8", text)          # the measurement, not a guess
        self.assertIn("drv-15s", text)       # the preset it implies

    def test_output_is_valid_yaml_the_existing_loader_accepts(self):
        import tempfile, yaml
        job = Job(slots={"character": Path("/c.png"), "driver": Path("/d.mp4")},
                  probes={"driver": VIDEO}, pipeline="motion-enhance")
        data = yaml.safe_load(render_manifest([job], now="2026-08-31 21:40"))
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
        text = render_manifest([job], now="2026-08-31 21:40")
        self.assertIn("character-swap: { preset: drv-15s }", text)
        self.assertNotIn("motion:", text)


class TestMultiRunManifest(unittest.TestCase):
    """One pod, many runs — spec section 5's basket.

    The runner, the journal and final_files were already multi-run; this
    writer was the only thing pinning the bot to a single job, and the user's
    own hand-written manifests have had 2-6 runs all along.
    """

    IMG = Probe(kind="image", width=1536, height=2720, duration_s=0.0,
                bitrate_kbps=0, size_bytes=4_873_992)

    def _job(self, character, outfit, driver, pipeline="tryon-motion-enhance"):
        return Job(pipeline=pipeline,
                   slots={"character": Path(f"/s/{character}.png"),
                          "outfit": Path(f"/s/{outfit}.png"),
                          "driver": Path(f"/s/{driver}.mp4")},
                   probes={"character": self.IMG, "outfit": self.IMG,
                           "driver": VIDEO})

    def test_every_job_becomes_its_own_run(self):
        import yaml
        jobs = [self._job("c1", "o4", "m1"), self._job("c1", "o8", "m1"),
                self._job("c2", "o4", "m2")]
        data = yaml.safe_load(render_manifest(jobs, now="2026-09-01 12:40"))
        self.assertEqual(len(data["runs"]), 3)
        self.assertEqual([r["inputs"]["outfit"] for r in data["runs"]],
                         ["/s/o4.png", "/s/o8.png", "/s/o4.png"])

    def test_run_ids_name_the_material_they_used(self):
        """The id names the output directory, `_final/<id>.mp4` and every
        journal row. `job-3` would make a six-run batch unreadable at exactly
        the moment it matters — when one of the six came out wrong."""
        self.assertEqual(run_id_for(self._job("c1", "o4", "m1")), "c1-o4-m1")

    def test_two_jobs_sharing_material_still_get_distinct_ids(self):
        """batchlib refuses a manifest with a repeated id — rightly, since the
        second run would overwrite the first one's finished video. Sharing
        material and differing only by pipeline is legitimate, so this is a
        suffix rather than an error."""
        import yaml
        jobs = [self._job("c1", "o4", "m1", "tryon-motion-enhance"),
                self._job("c1", "o4", "m1", "tryon-character-swap-enhance")]
        data = yaml.safe_load(render_manifest(jobs, now="2026-09-01 12:40"))
        ids = [r["id"] for r in data["runs"]]
        self.assertEqual(len(set(ids)), 2, f"ids collide: {ids}")

    def test_each_run_carries_its_own_pipeline_and_preset(self):
        import yaml
        jobs = [self._job("c1", "o4", "m1", "tryon-motion-enhance"),
                self._job("c1", "o4", "m2", "tryon-character-swap-enhance")]
        data = yaml.safe_load(render_manifest(jobs, now="2026-09-01 12:40"))
        self.assertEqual(data["runs"][0]["pipeline"], "tryon-motion-enhance")
        self.assertIn("motion", data["runs"][0])
        self.assertEqual(data["runs"][1]["pipeline"], "tryon-character-swap-enhance")
        self.assertIn("character-swap", data["runs"][1])

    def test_an_id_survives_a_filename_with_nothing_usable_in_it(self):
        job = Job(pipeline="motion-enhance",
                  slots={"character": Path("/s/....png"), "driver": Path("/s/---.mp4")},
                  probes={"driver": VIDEO})
        self.assertTrue(run_id_for(job), "an empty id is rejected by batchlib")


if __name__ == "__main__":
    unittest.main()
