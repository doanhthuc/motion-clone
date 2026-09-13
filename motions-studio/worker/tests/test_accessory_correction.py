import sys
import unittest
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from worker_runtime.accessory_correction import (
    chunk_visible_intervals,
    choose_reference_frames,
    validate_reference,
    visible_intervals,
    prepare_reference,
    composite_region,
)
from worker_runtime.accessory_workflow import build_vace_workflow, validate_capabilities
from PIL import Image, ImageFilter


class TestAccessoryCorrection(unittest.TestCase):
    def test_visibility_covers_initial_and_later_frames_without_bridging_occlusion(self):
        self.assertEqual(visible_intervals([True, True, False, True, True, True]),
                         [(0, 2), (3, 6)])

    def test_nonfinite_reference_metrics_are_rejected(self):
        for value in (float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                validate_reference(area_ratio=0.1, sharpness=value)

    def test_generated_reference_is_excluded_even_if_mislabeled_source(self):
        self.assertEqual(choose_reference_frames(source_frames=['wan.png'],
                         generated_frames=['wan.png'], sharpness={'wan.png': 100},
                         minimum_sharpness=32), [])

    def test_reference_gate_measures_native_crop_before_resizing(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'ref.png'
            dest = Path(directory) / 'crop.png'
            im = Image.new('RGB', (128, 128))
            for x in range(128):
                for y in range(128):
                    im.putpixel((x, y), ((255 if (x//8+y//8)%2 else 0),)*3)
            im.save(source)
            report = prepare_reference(source, dest, box=None)
            self.assertEqual(report['native_size'], [128, 128])
            im.filter(ImageFilter.GaussianBlur(12)).save(source)
            with self.assertRaisesRegex(ValueError, 'reference quality'):
                prepare_reference(source, dest, box=None)

    def test_composite_preserves_every_pixel_outside_mask(self):
        original = Image.new('RGB', (16, 16), 'red')
        fixed = Image.new('RGB', (8, 8), 'blue')
        mask = Image.new('L', (16, 16), 0)
        mask.paste(255, (6, 6, 10, 10))
        result = composite_region(original, fixed, mask, (4, 4, 12, 12))
        self.assertEqual(result.getpixel((8, 8)), (0, 0, 255))
        self.assertEqual(result.getpixel((5, 5)), (255, 0, 0))
        self.assertEqual(result.getpixel((0, 0)), (255, 0, 0))

    def test_vace_graph_supplies_reference_mask_and_matching_base(self):
        wf = build_vace_workflow('clip.mp4', 'mask.mp4', 'ref.png', frames=49,
                                 fps=30, size=512, prefix='test', seed=7)
        self.assertEqual(wf['81']['inputs']['ref_images'], ['10', 0])
        self.assertEqual(wf['81']['inputs']['input_masks'], ['14', 0])
        self.assertIn('T2V-14B', wf['42']['inputs']['model'])
        self.assertEqual(wf['42']['inputs']['extra_model'], ['40', 0])
        self.assertEqual(wf['90']['inputs']['seed'], 7)
        with self.assertRaisesRegex(RuntimeError, 'Missing ComfyUI'):
            validate_capabilities({}, wf)

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
