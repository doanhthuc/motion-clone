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

    def test_character_swap_stage_fieldnames(self):
        # linux.py run_character_swap: inputs.ref (ảnh) + inputs.video (video nguồn)
        stage = STAGES["character-swap"]
        self.assertEqual(stage.job_type, "character-swap")
        self.assertEqual(set(stage.inputs), {"ref", "video"})


class TestRoles(unittest.TestCase):
    def test_tryon_motion_enhance_can_character_outfit_driver(self):
        self.assertEqual(required_roles("tryon-motion-enhance"), {"character", "outfit", "driver"})
        self.assertEqual(optional_roles("tryon-motion-enhance"), {"background"})

    def test_motion_enhance_khong_can_outfit(self):
        self.assertEqual(required_roles("motion-enhance"), {"character", "driver"})
        self.assertEqual(optional_roles("motion-enhance"), set())

    def test_prev_o_chang_sau_KHONG_doi_material_du_phong(self):
        # motion khai "prev|material:character": ở chặng ĐẦU phải đòi character,
        # ở chặng SAU phải lấy output chặng trước và KHÔNG đòi character nữa.
        #
        # Phải dựng pipeline riêng mới thấy được: trong tryon-motion-enhance thì tryon
        # đã cấp character rồi, nên motion đòi thêm cũng không đổi tập hợp — đo thật
        # 18/08/2026, bỏ hẳn nhánh prev mà cả hai pipeline có sẵn đều ra kết quả y hệt.
        from batchlib import pipelines as P
        P.PIPELINES["_test_enhance_motion"] = ["enhance", "motion"]
        try:
            self.assertEqual(required_roles("_test_enhance_motion"), {"driver"})
        finally:
            del P.PIPELINES["_test_enhance_motion"]

    def test_prev_phai_dung_dau_trong_moi_khai_bao_nhieu_nguon(self):
        # _roles() thoát ngay khi gặp "prev", nên "material:x|prev" sẽ âm thầm đòi x
        # bất kể chặng nằm ở đâu. Quy tắc đó không viết ở đâu cả — test này là chỗ viết.
        for stage in STAGES.values():
            for field, source in stage.inputs.items():
                if "|" in source:
                    self.assertTrue(source.startswith("prev|"),
                                    f"{stage.name}.{field} = {source!r}: 'prev' phải đứng đầu")

    def test_pipeline_la_bao_loi_kem_danh_sach_co_that(self):
        with self.assertRaises(PipelineError) as cm:
            required_roles("khong-co-that")
        self.assertIn("motion-enhance", str(cm.exception))

    def test_character_swap_enhance_roles(self):
        self.assertEqual(required_roles("character-swap-enhance"), {"character", "driver"})

    def test_tryon_character_swap_enhance_roles(self):
        self.assertEqual(required_roles("tryon-character-swap-enhance"),
                          {"character", "outfit", "driver"})


if __name__ == "__main__":
    unittest.main()
