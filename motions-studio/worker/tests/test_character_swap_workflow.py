import sys
import types
import unittest
from unittest.mock import patch

# linux.py chỉ cần requests khi worker thật gọi API — stub như test_wan_anchored_context.py
try:
    import requests  # noqa: F401
except ModuleNotFoundError:
    requests_stub = types.ModuleType("requests")
    requests_stub.exceptions = types.SimpleNamespace(
        ConnectionError=ConnectionError,
        RequestException=Exception,
    )
    sys.modules["requests"] = requests_stub

from worker_runtime import linux  # noqa: E402
from worker_runtime.linux import (  # noqa: E402
    _apply_swap_to_wan_workflow,
    build_scail2_swap_workflow,
    build_wan_workflow,
)


class ApplySwapToWanWorkflowTests(unittest.TestCase):
    def _wf(self, p=None):
        params = {"width": 544, "height": 960, "frames": 81, "steps": 4, **(p or {})}
        return _apply_swap_to_wan_workflow(
            build_wan_workflow("ref.png", "drv.mp4", params), params)

    def test_embeds_nhan_bg_va_mask(self):
        wf = self._wf()
        self.assertEqual(wf["81"]["inputs"]["bg_images"], ["206", 0])
        self.assertEqual(wf["81"]["inputs"]["mask"], ["205", 0])

    def test_chuoi_mask_sam3_dung_thu_tu_kijai(self):
        wf = self._wf()
        self.assertEqual(wf["200"]["class_type"], "CheckpointLoaderSimple")
        self.assertEqual(wf["200"]["inputs"]["ckpt_name"], "sam3.1_multiplex_fp16.safetensors")
        self.assertEqual(wf["202"]["class_type"], "SAM3_VideoTrack")
        self.assertEqual(wf["202"]["inputs"]["images"], ["12", 0])   # frame driver
        self.assertEqual(wf["203"]["class_type"], "SAM3_TrackToMask")
        self.assertEqual(wf["204"]["class_type"], "GrowMaskWithBlur")
        self.assertEqual(wf["205"]["class_type"], "BlockifyMask")
        self.assertEqual(wf["206"]["class_type"], "DrawMaskOnImage")
        self.assertEqual(wf["206"]["inputs"]["image"], ["12", 0])
        self.assertEqual(wf["206"]["inputs"]["mask"], ["205", 0])
        self.assertEqual(wf["206"]["inputs"]["color"], "0, 0, 0")

    def test_param_chinh_mask(self):
        wf = self._wf({"sam3Prompt": "woman in red dress", "maskGrow": 20, "maskBlockify": 16})
        self.assertEqual(wf["201"]["inputs"]["text"], "woman in red dress")
        self.assertEqual(wf["204"]["inputs"]["expand"], 20)
        self.assertEqual(wf["205"]["inputs"]["block_size"], 16)

    def test_khong_dung_vao_graph_motion_goc(self):
        params = {"width": 544, "height": 960, "frames": 81, "steps": 4}
        wf = build_wan_workflow("ref.png", "drv.mp4", params)
        self.assertNotIn("bg_images", wf["81"]["inputs"])
        self.assertNotIn("200", wf)


class BuildScail2SwapWorkflowTests(unittest.TestCase):
    def _params(self, **overrides):
        return {"width": 544, "height": 960, "frames": 81, "render_fps": 16, **overrides}

    def test_kich_thuoc_boi_32_va_cap_81_frame(self):
        wf = build_scail2_swap_workflow("ref.png", "drv.mp4", self._params(width=550, height=970, frames=161))
        n70 = wf["70"]["inputs"]
        self.assertEqual(n70["width"] % 32, 0)
        self.assertEqual(n70["height"] % 32, 0)
        self.assertEqual(n70["length"], 81)

    def test_wiring_theo_template_chinh_thuc(self):
        wf = build_scail2_swap_workflow("ref.png", "drv.mp4", self._params())
        n70 = wf["70"]["inputs"]
        self.assertEqual(wf["70"]["class_type"], "WanSCAILToVideo")
        self.assertIs(n70["replacement_mode"], True)
        self.assertEqual(n70["pose_video"], ["12", 0])          # frame driver
        self.assertEqual(n70["pose_video_mask"], ["25", 0])     # SCAIL2ColoredMask output 0
        self.assertEqual(n70["reference_image_mask"], ["25", 1])
        self.assertIs(wf["25"]["inputs"]["replacement_mode"], True)
        self.assertEqual(wf["30"]["inputs"]["unet_name"], "wan2.1_14B_SCAIL_2_fp8_scaled.safetensors")
        self.assertEqual(wf["90"]["inputs"]["sigmas"], ["81", 0])
        self.assertEqual(wf["100"]["inputs"]["samples"], ["90", 1])   # denoised output
        self.assertEqual(wf["110"]["inputs"]["audio"], ["12", 2])     # giữ audio driver

    def test_turbo_defaults(self):
        wf = build_scail2_swap_workflow("ref.png", "drv.mp4", self._params())
        self.assertEqual(wf["81"]["inputs"]["steps"], 6)
        self.assertEqual(wf["90"]["inputs"]["cfg"], 1.0)
        self.assertEqual(wf["32"]["inputs"]["strength_model"], 0.8)   # lightx2v rank64
        self.assertEqual(wf["31"]["inputs"]["strength_model"], 1.0)   # DPO


class RunCharacterSwapTests(unittest.TestCase):
    def test_dang_ky_pipeline(self):
        self.assertIn("character-swap", linux.PIPELINES)
        self.assertIs(linux.PIPELINES["character-swap"], linux.run_character_swap)

    def test_map_video_sang_motion_va_default_wananimate(self):
        job = {"id": "j1", "inputs": {"ref": "a/ref.png", "video": "a/drv.mp4"}, "params": {}}
        with patch.object(linux, "run_motion") as rm:
            linux.run_character_swap(job)
        rm.assert_called_once_with(job)
        self.assertEqual(job["inputs"]["motion"], "a/drv.mp4")
        p = job["params"]
        self.assertEqual(p["_swapEngine"], "wananimate")
        self.assertEqual(p["lora_relight"], 1.0)      # relight LoRA sinh ra cho Mix mode
        self.assertEqual(p["pose_strength"], 1.0)     # bộ số theo example kijai replacement
        self.assertEqual(p["face_strength"], 1.0)
        self.assertEqual(p["preset"], "drv-5s")

    def test_engine_scail2_va_engine_la(self):
        job = {"id": "j2", "inputs": {"ref": "r.png", "video": "d.mp4"},
               "params": {"engine": "scail2"}}
        with patch.object(linux, "run_motion"):
            linux.run_character_swap(job)
        self.assertEqual(job["params"]["_swapEngine"], "scail2")
        bad = {"id": "j3", "inputs": {"ref": "r.png", "video": "d.mp4"},
               "params": {"engine": "xyz"}}
        with self.assertRaises(RuntimeError):
            linux.run_character_swap(bad)

    def test_thieu_input_bao_ro(self):
        with self.assertRaises(RuntimeError):
            linux.run_character_swap({"id": "j4", "inputs": {"ref": "r.png"}, "params": {}})


if __name__ == "__main__":
    unittest.main()
