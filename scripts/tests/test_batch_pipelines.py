import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.pipelines import (PIPELINES, STAGES, PipelineError, optional_roles, required_roles)


class TestKhaiBao(unittest.TestCase):
    def test_moi_chang_trong_pipeline_deu_co_trong_STAGES(self):
        for name, stages in PIPELINES.items():
            for s in stages:
                self.assertIn(s, STAGES, f"pipeline {name} nhắc chặng {s} không có khai báo")

    def test_field_khop_ten_worker_that_doc(self):
        # linux.py:4734,4735,4744,4765 · pod-smoke.sh:294-295 · linux.py:9544
        self.assertEqual(set(STAGES["tryon"].inputs), {"model", "product", "background"})
        self.assertEqual(set(STAGES["motion"].inputs), {"ref", "motion"})
        self.assertEqual(set(STAGES["enhance"].inputs), {"input"})

    def test_job_type_khop_PIPELINES_cua_worker(self):
        self.assertEqual(STAGES["tryon"].job_type, "tryon")
        self.assertEqual(STAGES["motion"].job_type, "motion")
        self.assertEqual(STAGES["enhance"].job_type, "enhance")


class TestRoles(unittest.TestCase):
    def test_tryon_motion_enhance_can_character_outfit_driver(self):
        self.assertEqual(required_roles("tryon-motion-enhance"), {"character", "outfit", "driver"})
        self.assertEqual(optional_roles("tryon-motion-enhance"), {"background"})

    def test_motion_enhance_khong_can_outfit(self):
        self.assertEqual(required_roles("motion-enhance"), {"character", "driver"})
        self.assertEqual(optional_roles("motion-enhance"), set())

    def test_prev_khong_bi_tinh_la_material(self):
        # enhance chỉ ăn output chặng trước — không được đòi thêm material nào.
        self.assertNotIn("input", required_roles("motion-enhance"))

    def test_pipeline_la_bao_loi_kem_danh_sach_co_that(self):
        with self.assertRaises(PipelineError) as cm:
            required_roles("khong-co-that")
        self.assertIn("motion-enhance", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
