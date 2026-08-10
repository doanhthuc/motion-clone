import os
import sys
import types
import unittest
from unittest.mock import patch


# linux.py chỉ cần requests khi worker thật gọi API. Local/unit-test có thể chưa cài
# requirements của container, nên stub transport để test thuần phần build workflow.
try:
    import requests  # noqa: F401
except ModuleNotFoundError:
    requests_stub = types.ModuleType("requests")
    requests_stub.exceptions = types.SimpleNamespace(
        ConnectionError=ConnectionError,
        RequestException=Exception,
    )
    sys.modules["requests"] = requests_stub

from worker_runtime.linux import (  # noqa: E402
    _normalize_motion_params,
    _wan_cover_frames,
    _wan_window_plan,
    build_bds_segment_workflow,
    build_wan_t2v_workflow,
    build_wan_workflow,
)


class WanAnchoredContextTests(unittest.TestCase):
    def _params(self, **overrides):
        return {
            "width": 544,
            "height": 960,
            "frames": 241,
            "steps": 4,
            "frame_window_size": 77,
            "windowMode": "anchored-context",
            **overrides,
        }

    @patch.dict(os.environ, {"MOTION_SHORT": "540"})
    def test_explicit_720p_bypasses_default_540_short_cap(self):
        wf = build_wan_workflow("ref.png", "driver.mp4", self._params(
            preset="drv-15s",
            width=720,
            height=1280,
            quality="720p",
            resolutionPolicy="quality-v1",
        ))

        self.assertEqual(wf["11"]["inputs"]["width"], 720)
        self.assertEqual(wf["11"]["inputs"]["height"], 1280)
        self.assertEqual(wf["12"]["inputs"]["custom_width"], 720)
        self.assertEqual(wf["12"]["inputs"]["custom_height"], 1280)

    @patch.dict(os.environ, {"MOTION_SHORT": "540"})
    def test_20s_keeps_legacy_540_resolution(self):
        wf = build_wan_workflow("ref.png", "driver.mp4", self._params(
            preset="drv-20s",
            width=544,
            height=960,
            quality="540p",
            resolutionPolicy="quality-v1",
        ))

        self.assertEqual(wf["12"]["inputs"]["custom_width"], 544)
        self.assertEqual(wf["12"]["inputs"]["custom_height"], 960)

    @patch.dict(os.environ, {"MOTION_ENABLE_COLOR_ADJUST": "0"})
    def test_multi_window_anchors_ref_with_context_options(self):
        wf = build_wan_workflow("ref.png", "driver.mp4", self._params())

        self.assertEqual(wf["81"]["inputs"]["frame_window_size"], 241)
        self.assertEqual(wf["81"]["inputs"]["colormatch"], "disabled")
        self.assertEqual(wf["90"]["inputs"]["context_options"], ["82", 0])
        self.assertEqual(wf["82"]["class_type"], "WanVideoContextOptions")
        self.assertEqual(wf["82"]["inputs"], {
            "context_schedule": "static_standard",
            "context_frames": 73,
            "context_stride": 4,
            "context_overlap": 16,
            "freenoise": True,
            "verbose": False,
            "fuse_method": "linear",
        })
        plan = _wan_window_plan(self._params(), 241)
        self.assertEqual(plan["final_overlap_latents"], 7)

    def test_default_window_plan_minimizes_blend_for_15s_native_30fps(self):
        plan = _wan_window_plan(self._params(frames=453), 453)

        self.assertTrue(plan["anchored"])
        self.assertEqual(plan["context_frames"], 77)
        self.assertEqual(plan["overlap"], 16)
        self.assertEqual(plan["final_overlap_latents"], 6)

    def test_common_durations_keep_final_shift_bounded(self):
        for frames in (153, 161, 241, 301, 321, 449, 453, 481, 601):
            with self.subTest(frames=frames):
                plan = _wan_window_plan(self._params(frames=frames), frames)
                self.assertEqual(plan["overlap"], 16)
                self.assertLessEqual(plan["final_overlap_latents"], plan["overlap"] // 4 + 3)

    def test_wan_duration_frames_cover_instead_of_floor(self):
        self.assertEqual(_wan_cover_frames(15, 30), 453)
        self.assertEqual(_wan_cover_frames(15, 16), 241)
        self.assertEqual(_wan_cover_frames(20, 16), 321)
        self.assertEqual(_wan_cover_frames(449 / 30, 30), 449)

    @patch.dict(os.environ, {"MOTION_ENABLE_COLOR_ADJUST": "0"})
    def test_autoregressive_mode_remains_available_for_ab(self):
        wf = build_wan_workflow(
            "ref.png",
            "driver.mp4",
            self._params(windowMode="autoregressive", windowColormatch="hm-mkl-hm"),
        )

        self.assertEqual(wf["81"]["inputs"]["frame_window_size"], 77)
        self.assertEqual(wf["81"]["inputs"]["colormatch"], "hm-mkl-hm")
        self.assertNotIn("82", wf)
        self.assertNotIn("context_options", wf["90"]["inputs"])

    @patch.dict(os.environ, {"MOTION_ENABLE_COLOR_ADJUST": "0"}, clear=True)
    def test_autoregressive_multi_window_keeps_raw_color_by_default(self):
        wf = build_wan_workflow(
            "ref.png",
            "driver.mp4",
            self._params(windowMode="autoregressive"),
        )

        self.assertEqual(wf["81"]["inputs"]["frame_window_size"], 77)
        self.assertEqual(wf["81"]["inputs"]["colormatch"], "disabled")
        self.assertNotIn("82", wf)

    def test_single_window_does_not_add_context_node(self):
        wf = build_wan_workflow(
            "ref.png",
            "driver.mp4",
            self._params(frames=77),
        )

        self.assertEqual(wf["81"]["inputs"]["frame_window_size"], 77)
        self.assertEqual(wf["81"]["inputs"]["colormatch"], "disabled")
        self.assertNotIn("82", wf)
        self.assertNotIn("context_options", wf["90"]["inputs"])

    def test_window_plan_clamps_and_normalizes_context_controls(self):
        plan = _wan_window_plan({
            "windowMode": "context",
            "contextFrames": 79,
            "contextOverlap": 999,
            "contextStride": 1,
            "contextSchedule": "invalid",
            "contextFuseMethod": "invalid",
            "contextFreeNoise": "false",
        }, 241)

        self.assertTrue(plan["anchored"])
        self.assertEqual(plan["context_frames"], 77)
        self.assertEqual(plan["overlap"], 72)
        self.assertEqual(plan["stride"], 4)
        self.assertEqual(plan["schedule"], "static_standard")
        self.assertEqual(plan["fuse_method"], "linear")
        self.assertFalse(plan["freenoise"])

    def test_normalize_forces_original_autoregressive_motion_path(self):
        normalized = _normalize_motion_params({
            "windowMode": "anchored-context",
            "contextOverlap": 16,
            "contextSchedule": "uniform_standard",
        })

        self.assertEqual(normalized["window_mode"], "autoregressive")
        self.assertEqual(normalized["windowMode"], "autoregressive")
        self.assertEqual(normalized["frame_window_size"], 81)
        self.assertEqual(normalized["context_overlap"], 16)
        self.assertEqual(normalized["context_schedule"], "uniform_standard")
        wf = build_wan_workflow("ref.png", "driver.mp4", {**self._params(), **normalized})
        self.assertEqual(wf["81"]["inputs"]["frame_window_size"], 81)
        self.assertEqual(wf["81"]["inputs"]["colormatch"], "disabled")
        self.assertNotIn("82", wf)
        self.assertNotIn("context_options", wf["90"]["inputs"])

    def test_motion_window_81_evenly_partitions_standard_16fps_durations(self):
        for frames in (81, 161, 241, 321, 481):
            with self.subTest(frames=frames):
                normalized = _normalize_motion_params({"frames": frames})
                self.assertEqual(normalized["frame_window_size"], 81)
                self.assertEqual((frames - 1) % (normalized["frame_window_size"] - 1), 0)

    def test_natural_and_hq_legacy_inputs_are_forced_to_fast(self):
        legacy = {
            "renderProfile": "natural",
            "render_profile": "hq",
            "hq": True,
            "hq_steps": 24,
            "steps": 20,
            "cfg": 6,
            "scheduler": "unipc",
            "lora_lightx2v": 0,
        }
        normalized = _normalize_motion_params(legacy)

        self.assertEqual(normalized["renderProfile"], "fast")
        self.assertEqual(normalized["render_profile"], "fast")
        self.assertFalse(normalized["hq"])
        self.assertNotIn("hq_steps", normalized)
        self.assertEqual(normalized["steps"], 4)
        self.assertEqual(normalized["cfg"], 1.0)
        self.assertEqual(normalized["scheduler"], "dpm++_sde")
        self.assertEqual(normalized["lora_lightx2v"], 1.0)

        wf = build_wan_workflow("ref.png", "driver.mp4", {
            **self._params(),
            **legacy,
        })
        self.assertEqual(wf["90"]["inputs"]["steps"], 4)
        self.assertEqual(wf["90"]["inputs"]["cfg"], 1.0)
        self.assertEqual(wf["90"]["inputs"]["scheduler"], "dpm++_sde")
        self.assertEqual(wf["40"]["inputs"]["strength_1"], 1.0)

    def test_moi_alias_khai_tu_deu_bi_ep_ve_fast(self):
        """Alias legacy = giá trị nằm sẵn trong workflow CŨ, không phải yêu cầu chủ ý của user.

        build_wan_workflow `_retired_natural` vẫn hạ đúng những chuỗi này về Fast, nên
        _normalize_motion_params không được hiểu chúng theo nghĩa ngược lại.
        """
        for alias in ("natural", "quality", "official", "hq", "NATURAL", " Hq "):
            with self.subTest(alias=alias):
                normalized = _normalize_motion_params({"renderProfile": alias})
                self.assertEqual(normalized["renderProfile"], "fast")
                self.assertEqual(normalized["steps"], 4)
                self.assertEqual(normalized["scheduler"], "dpm++_sde")
                self.assertEqual(normalized["lora_lightx2v"], 1.0)

    def test_opt_in_chu_y_bang_max_van_mo_duoc_duong_20_buoc(self):
        """Cửa A/B 21/07 phải còn sống — fix không được bịt luôn nó."""
        normalized = _normalize_motion_params({"renderProfile": "max", "hq_steps": 24})
        self.assertEqual(normalized["renderProfile"], "max20")
        self.assertEqual(normalized["steps"], 24)
        self.assertEqual(normalized["scheduler"], "unipc")
        self.assertEqual(normalized["lora_lightx2v"], 0.0)

    def test_max20_idempotent_khi_normalize_lai(self):
        """run_motion và nhánh 6785 cùng gọi normalize; chạy hai lần không được rơi về fast."""
        once = _normalize_motion_params({"renderProfile": "max"})
        twice = _normalize_motion_params(dict(once))
        self.assertEqual(twice["renderProfile"], "max20")
        self.assertEqual(twice["steps"], once["steps"])

    @patch.dict(os.environ, {"MOTION_FORCE_QUALITY": "1"})
    def test_env_force_quality_van_bat_duoc_ca_box(self):
        normalized = _normalize_motion_params({"renderProfile": "fast"})
        self.assertEqual(normalized["renderProfile"], "max20")
        self.assertEqual(normalized["steps"], 20)

    def test_khong_co_tin_hieu_gi_thi_mac_dinh_fast(self):
        normalized = _normalize_motion_params({})
        self.assertEqual(normalized["renderProfile"], "fast")
        self.assertEqual(normalized["steps"], 4)

    def test_shared_builder_keeps_non_motion_fast_tuning(self):
        wf = build_wan_workflow("ref.png", "driver.mp4", self._params(
            renderProfile="fast",
            steps=6,
            cfg=1,
            lora_lightx2v=1,
        ))

        self.assertEqual(wf["90"]["inputs"]["steps"], 6)
        self.assertEqual(wf["90"]["inputs"]["cfg"], 1.0)
        self.assertEqual(wf["40"]["inputs"]["strength_1"], 1.0)

    def test_legacy_auto_prompt_does_not_force_hand_or_lighting_redraw(self):
        legacy_extra = (
            "soft even matte lighting with retained detail in bright areas, natural matte skin with visible pores "
            "and realistic texture, stable natural mouth and lips, steady well-formed bare hands with five clearly "
            "separated fingers, natural fingertips, clean short natural fingernails"
        )
        wf = build_wan_workflow(
            "ref.png",
            "driver.mp4",
            self._params(extraPositive=legacy_extra),
        )

        prompt = wf["60"]["inputs"]["positive_prompt"]
        self.assertEqual(prompt, "natural body proportions, smooth natural motion, photorealistic video")
        self.assertNotIn("five clear", prompt)
        self.assertNotIn("matte lighting", prompt)

    def test_user_extra_prompt_is_still_preserved(self):
        wf = build_wan_workflow(
            "ref.png",
            "driver.mp4",
            self._params(extraPositive="subtle handheld camera movement"),
        )

        self.assertTrue(
            wf["60"]["inputs"]["positive_prompt"].endswith(", subtle handheld camera movement")
        )

    def test_motion_normalize_locks_body_proportions_to_reference(self):
        normalized = _normalize_motion_params({
            "poseStrength": 0.8,
            "clipStrength": 1.2,
        })
        wf = build_wan_workflow("ref.png", "driver.mp4", self._params(**normalized))

        self.assertTrue(normalized["bodyProportionLock"])
        self.assertEqual(wf["81"]["inputs"]["pose_strength"], 0.7)
        self.assertEqual(wf["71"]["inputs"]["strength_1"], 1.35)
        self.assertIn("arm thickness", wf["60"]["inputs"]["positive_prompt"])
        self.assertIn("transfer motion only from the driver", wf["60"]["inputs"]["positive_prompt"])
        self.assertIn("thin or shrunken arms", wf["60"]["inputs"]["negative_prompt"])

    def test_body_proportion_lock_can_be_disabled_for_ab(self):
        normalized = _normalize_motion_params({
            "bodyProportionLock": False,
            "poseStrength": 0.8,
            "clipStrength": 1.2,
        })
        wf = build_wan_workflow("ref.png", "driver.mp4", self._params(**normalized))

        self.assertFalse(normalized["bodyProportionLock"])
        self.assertEqual(wf["81"]["inputs"]["pose_strength"], 0.8)
        self.assertEqual(wf["71"]["inputs"]["strength_1"], 1.2)
        self.assertNotIn("transfer motion only from the driver", wf["60"]["inputs"]["positive_prompt"])

    def test_driver_native_resolution_defaults_to_540p(self):
        for preset, expected in (
            ("drv-10s", (544, 960)),
            ("drv-15s", (544, 960)),
            ("drv-20s", (544, 960)),
            ("drv-30s", (544, 960)),
        ):
            with self.subTest(preset=preset):
                normalized = _normalize_motion_params({
                    "preset": preset,
                    "aspectRatio": "9:16",
                    "width": 432,
                    "height": 960,
                    "fitDriver": True,
                })
                self.assertEqual((normalized["width"], normalized["height"]), expected)
                self.assertFalse(normalized["fitDriver"])
                self.assertFalse(normalized["fit_driver"])

    def test_driver_native_720p_respects_horizontal_aspect(self):
        normalized = _normalize_motion_params({
            "preset": "drv-15s",
            "aspectRatio": "16:9",
            "quality": "720p",
        })
        self.assertEqual((normalized["width"], normalized["height"]), (1280, 720))

    def test_wan_i2v_builder_receives_attention_params_without_undefined_name(self):
        for wan_ver in ("wan2.1", "wan2.2"):
            with self.subTest(wan_ver=wan_ver):
                wf = build_bds_segment_workflow(
                    "start.png", None, "subtle natural motion", 480, 832, 81, 4, "wan-i2v-test",
                    wan_ver=wan_ver, rife_mult=1, params={"attention_mode": "sageattn"},
                )

                self.assertEqual(wf["42"]["inputs"]["attention_mode"], "sageattn")
                if wan_ver == "wan2.2":
                    self.assertEqual(wf["43"]["inputs"]["attention_mode"], "sageattn")

    def test_wan_t2v_builder_receives_attention_params_without_undefined_name(self):
        for wan_ver in ("wan2.1", "wan2.2"):
            with self.subTest(wan_ver=wan_ver):
                wf = build_wan_t2v_workflow(
                    "subtle natural motion", "wan-t2v-test", 832, 480, 81,
                    wan_ver=wan_ver, params={"attention_mode": "sageattn"},
                )

                self.assertEqual(wf["42"]["inputs"]["attention_mode"], "sageattn")
                if wan_ver == "wan2.2":
                    self.assertEqual(wf["43"]["inputs"]["attention_mode"], "sageattn")


if __name__ == "__main__":
    unittest.main()
