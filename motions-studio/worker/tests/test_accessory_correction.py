import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from worker_runtime.accessory_correction import (
    chunk_visible_intervals,
    choose_reference_frames,
    validate_reference,
)


class TestAccessoryCorrection(unittest.TestCase):
    def test_chunks_cover_each_visible_interval_with_overlap(self):
        self.assertEqual(
            chunk_visible_intervals([(0, 41), (80, 151)], chunk_size=32, overlap=8),
            [(0, 32), (24, 41), (80, 112), (104, 136), (128, 151)],
        )

    def test_reference_must_be_immutable_source_not_generated_video(self):
        result = choose_reference_frames(
            source_frames=["tryon.png", "driver-clear.png"],
            generated_frames=["wan-0001.png"],
            sharpness={"tryon.png": 120.0, "driver-clear.png": 180.0},
            minimum_sharpness=100.0,
        )
        self.assertEqual(result, ["driver-clear.png", "tryon.png"])
        self.assertNotIn("wan-0001.png", result)

    def test_blurry_reference_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "reference quality"):
            validate_reference(area_ratio=0.002, sharpness=14.0)

    def test_clear_small_reference_passes_measured_gate(self):
        self.assertTrue(validate_reference(area_ratio=0.012, sharpness=85.0))


if __name__ == "__main__":
    unittest.main()
