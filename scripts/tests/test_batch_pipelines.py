import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.pipelines import (PIPELINES, STAGES, PipelineError, effective_stage_params,
                                locked_stage_param_errors, optional_roles, required_roles)


class TestKhaiBao(unittest.TestCase):
    def test_camera_pipeline_is_distinct_and_background_is_optional(self):
        self.assertEqual(
            PIPELINES["tryon-camera-motion-enhance"],
            ["camera-tryon", "camera-motion", "enhance"],
        )
        self.assertEqual(
            required_roles("tryon-camera-motion-enhance"),
            {"character", "outfit", "driver"},
        )
        self.assertEqual(optional_roles("tryon-camera-motion-enhance"), {"background"})

    def test_camera_aliases_keep_job_types_and_parameter_schemas_separate(self):
        self.assertEqual(STAGES["camera-tryon"].job_type, "tryon")
        self.assertEqual(STAGES["camera-tryon"].param_type, "tryon")
        self.assertEqual(STAGES["camera-motion"].job_type, "motion")
        self.assertEqual(STAGES["camera-motion"].param_type, "motion")

    def test_camera_motion_defaults_and_contractual_values(self):
        got = effective_stage_params(
            "camera-motion",
            {"poseStrength": 0.85, "fitDriver": False, "cameraAwareMotion": False},
        )
        self.assertEqual(got["poseStrength"], 0.85)
        self.assertEqual(got["clipStrength"], 1.2)
        self.assertFalse(got["bodyProportionLock"])
        self.assertTrue(got["fitDriver"])
        self.assertTrue(got["cameraAwareMotion"])

    def test_camera_motion_face_lock_is_a_default_not_a_lock(self):
        # Default on: the face drifts toward the driver without it (A/B 18/09/2026).
        self.assertEqual(effective_stage_params("camera-motion", {})["faceLock"], 1)
        # Not locked: a manifest can still turn it off, e.g. to A/B against it.
        self.assertEqual(effective_stage_params("camera-motion", {"faceLock": 0})["faceLock"], 0)
        self.assertEqual(locked_stage_param_errors("camera-motion", {"faceLock": 0}), [])

    def test_camera_motion_keeps_wans_face_texture_instead_of_restoring_it(self):
        # 20/09/2026: the swap costs the face region 39% of its detail (Laplacian variance
        # 150.7 -> 92.4 on .smoke/ab-face/'s frame-aligned pair). Two ways to put it back were
        # measured on a pod; this pins the one that won.
        got = effective_stage_params("camera-motion", {})
        # detailKeep 2.0 restores 147.3 of that 150.7 by band-limiting the swap's own change, so
        # the identity shift survives at full strength (low-freq band 1.04) — reviewed on video.
        self.assertEqual(got["faceLockDetailKeep"], 2.0)
        # CodeFormer was the earlier answer and is now redundant: it put detail back through a
        # second model's prior (etched edges, fuller lips, bigger eyes — 18/09 addendum in
        # motions-studio/feature/face-restore-motion-delivery.md), which was the other half of the
        # complaint that started this.
        self.assertEqual(got["faceLockRestore"], 0)
        # Both stay overridable — the A/B against them has to remain possible.
        self.assertEqual(
            effective_stage_params("camera-motion", {"faceLockRestore": 1})["faceLockRestore"], 1)
        self.assertEqual(
            effective_stage_params("camera-motion", {"faceLockDetailKeep": 0})["faceLockDetailKeep"], 0)

    def test_plain_motion_does_not_get_face_lock(self):
        self.assertNotIn("faceLock", effective_stage_params("motion", {}))

    def test_contractual_values_report_an_explicit_conflict(self):
        errors = locked_stage_param_errors("camera-motion", {"fitDriver": False})
        self.assertEqual(len(errors), 1)
        self.assertIn("fitDriver", errors[0])
        self.assertIn("True", errors[0])

    def test_legacy_pipeline_definitions_are_unchanged(self):
        self.assertEqual(PIPELINES["tryon-motion-enhance"], ["tryon", "motion", "enhance"])
        self.assertEqual(required_roles("tryon-motion-enhance"),
                         {"character", "outfit", "driver"})
        self.assertEqual(optional_roles("tryon-motion-enhance"), {"background"})

    def test_tryon_and_motion_stages_default_to_bare_wrists_and_natural_nails(self):
        for stage in ("tryon", "motion"):
            with self.subTest(stage=stage):
                got = effective_stage_params(stage, {})
                self.assertIs(got["naturalNails"], True)
                self.assertIs(got["removeWristAccessories"], True)
        # A manifest can still opt out per stage.
        got = effective_stage_params("tryon", {"naturalNails": False})
        self.assertIs(got["naturalNails"], False)
        self.assertIs(got["removeWristAccessories"], True)

    def test_camera_tryon_is_not_given_the_stage_wide_hands_defaults(self):
        # camera-tryon gets its hands wording from the camera compose prompt asset instead.
        self.assertNotIn("naturalNails", effective_stage_params("camera-tryon", {}))

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
