# Character Swap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Job type mới `character-swap`: ảnh người mẫu + video nguồn → video giữ nguyên background/camera của video, nhân vật được thay bằng người mẫu; 2 engine (`wananimate` Mix mode, `scail2`), phạm vi API + batch runner.

**Architecture:** Tái dùng toàn bộ đường driver-processing của `run_motion` (cắt segment, VFR fix, FIT DRIVER, frame budget, audio, delivery); rẽ nhánh duy nhất ở bước build workflow theo `params["_swapEngine"]`. Engine `wananimate` = mutate graph motion hiện có (thêm chuỗi mask SAM3 + `bg_images`/`mask` vào `WanVideoAnimateEmbeds`). Engine `scail2` = builder mới dùng node core native (`WanSCAILToVideo`, `SCAIL2ColoredMask`, `SAM3_*`).

**Tech Stack:** Python worker (`linux.py`, graph = dict thuần), ComfyUI core pin `3221224` (v0.29.2 — ĐÃ chứa `nodes_scail.py` + `nodes_sam3.py`, verify 21/08), WanVideoWrapper pin `088128b` (ĐÃ có `bg_images`/`mask` trên `WanVideoAnimateEmbeds`, verify 21/08), KJNodes pin `4d46ac1` (ĐÃ có `GrowMaskWithBlur`/`BlockifyMask`/`DrawMaskOnImage`, verify 21/08).

**Spec:** `docs/superpowers/specs/2026-08-21-character-swap-design.md`

## Global Constraints

- KHÔNG đổi hành vi job type `motion`: mọi nhánh mới phải gate bằng `params["_swapEngine"]` (chuỗi rỗng = đường motion cũ nguyên vẹn).
- KHÔNG cài custom node mới, KHÔNG nâng pin ComfyUI/wrapper — mọi node cần đều đã có ở các pin trên.
- Gate `make check-job-types` so sánh danh sách CÓ THỨ TỰ → luôn thêm `character-swap` vào CUỐI mỗi danh sách.
- Model mới đặt đúng thư mục ComfyUI: `checkpoints/` (sam3), `diffusion_models/` (scail2), `text_encoders/` (umt5 fp8), `loras/` (lightx2v rank64 + DPO).
- Test worker chạy bằng: `cd motions-studio/worker && python3 -m unittest discover -s tests` (pattern stub `requests` như `tests/test_wan_anchored_context.py:10-18`).
- Test batch chạy bằng: `make batch-test`. Gates: `make check-job-types`, `make check-batch-params`.
- Commit message tiếng Việt theo phong cách repo (xem `git log --oneline`), mỗi task một commit.

---

### Task 1: Model catalog + manifest (SAM3 + SCAIL-2)

**Files:**
- Modify: `motions-studio/comfyui/catalog-motion-transfer.json` (thêm group mới vào mảng `comfy`)
- Modify: `motions-studio/comfyui/models.txt` (thêm section mới ở cuối)

**Interfaces:**
- Produces: 5 file model trên Network Volume mà Task 2/3 tham chiếu bằng ĐÚNG filename:
  `sam3.1_multiplex_fp16.safetensors` (checkpoints), `wan2.1_14B_SCAIL_2_fp8_scaled.safetensors` (diffusion_models), `umt5_xxl_fp8_e4m3fn_scaled.safetensors` (text_encoders), `lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors` (loras), `wan2.1_SCAIL_2_DPO_lora_bf16.safetensors` (loras).

- [ ] **Step 1: Thêm section vào `models.txt`** (cuối file, format `dest_subdir | filename | url` như các dòng hiện có):

```
# --- Character-swap (thay nhân vật trong video) — ALD 21/08/2026 ---
# SAM3 (segmentation người, dùng CHUNG cho cả 2 engine wananimate + scail2; node core nodes_sam3.py).
# SCAIL-2 fp8_scaled 17.7GB (fp16 32.8GB KHÔNG vừa 5090 32GB). URL verify qua HF API 21/08/2026.
checkpoints      | sam3.1_multiplex_fp16.safetensors                              | https://huggingface.co/Comfy-Org/sam3.1/resolve/main/checkpoints/sam3.1_multiplex_fp16.safetensors
diffusion_models | wan2.1_14B_SCAIL_2_fp8_scaled.safetensors                      | https://huggingface.co/Comfy-Org/SCAIL-2/resolve/main/diffusion_models/wan2.1_14B_SCAIL_2_fp8_scaled.safetensors
text_encoders    | umt5_xxl_fp8_e4m3fn_scaled.safetensors                         | https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors
loras            | lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors | https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Lightx2v/lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors
loras            | wan2.1_SCAIL_2_DPO_lora_bf16.safetensors                       | https://huggingface.co/Comfy-Org/SCAIL-2/resolve/main/loras/wan2.1_SCAIL_2_DPO_lora_bf16.safetensors
```

- [ ] **Step 2: Thêm 5 entry vào `catalog-motion-transfer.json`** (mảng `comfy`, sau entry cuối; giữ format field của các entry hiện có — xem entry `wan-animate-14b` làm mẫu):

```json
{ "id": "swap-sam3", "group": "Character Swap (thay nhân vật)", "label": "SAM3.1 multiplex fp16 (segmentation người)", "type": "checkpoints", "filename": "sam3.1_multiplex_fp16.safetensors", "url": "https://huggingface.co/Comfy-Org/sam3.1/resolve/main/checkpoints/sam3.1_multiplex_fp16.safetensors", "sizeBytes": 1745546848, "vram": "~2GB VRAM", "tier": "optional", "gated": false, "note": "BẮT BUỘC cho character-swap (cả 2 engine). Node core SAM3_VideoTrack/SAM3_Detect." },
{ "id": "swap-scail2-unet", "group": "Character Swap (thay nhân vật)", "label": "SCAIL-2 14B fp8_scaled", "type": "diffusion_models", "filename": "wan2.1_14B_SCAIL_2_fp8_scaled.safetensors", "url": "https://huggingface.co/Comfy-Org/SCAIL-2/resolve/main/diffusion_models/wan2.1_14B_SCAIL_2_fp8_scaled.safetensors", "sizeBytes": 17694586857, "vram": "~18GB VRAM", "tier": "optional", "gated": false, "note": "Engine scail2. fp16 32.8GB KHÔNG vừa 5090 32GB → dùng fp8_scaled." },
{ "id": "swap-scail2-umt5-fp8", "group": "Character Swap (thay nhân vật)", "label": "umt5-xxl fp8 scaled (CLIPLoader core)", "type": "text_encoders", "filename": "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "url": "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors", "sizeBytes": 6735906897, "vram": "", "tier": "optional", "gated": false, "note": "Engine scail2. KHÁC umt5-xxl-enc-bf16 (format wrapper) — CLIPLoader core cần bản này." },
{ "id": "swap-scail2-lightx2v-r64", "group": "Character Swap (thay nhân vật)", "label": "LoRA lightx2v distill rank64", "type": "loras", "filename": "lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors", "url": "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Lightx2v/lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors", "sizeBytes": 738005744, "vram": "", "tier": "optional", "gated": false, "note": "Engine scail2 turbo (6 bước). Bản rank64 theo template chính thức." },
{ "id": "swap-scail2-dpo", "group": "Character Swap (thay nhân vật)", "label": "LoRA SCAIL-2 DPO (chất lượng)", "type": "loras", "filename": "wan2.1_SCAIL_2_DPO_lora_bf16.safetensors", "url": "https://huggingface.co/Comfy-Org/SCAIL-2/resolve/main/loras/wan2.1_SCAIL_2_DPO_lora_bf16.safetensors", "sizeBytes": 1226936552, "vram": "", "tier": "optional", "gated": false, "note": "Engine scail2, strength 1.0 theo template chính thức." }
```

- [ ] **Step 3: Verify JSON parse + URL sống**

Run: `python3 -c "import json; json.load(open('motions-studio/comfyui/catalog-motion-transfer.json')); print('JSON OK')"` rồi với mỗi URL: `curl -sIL <url> | grep -m1 "HTTP.*200"`.
Expected: `JSON OK` + 5 dòng HTTP 200.

- [ ] **Step 4: Commit**

```bash
git add motions-studio/comfyui/catalog-motion-transfer.json motions-studio/comfyui/models.txt
git commit -m "Catalog character-swap: SAM3 + SCAIL-2 fp8 (5 model mới ~27GB cho volume)"
```

---

### Task 2: Engine wananimate — `_apply_swap_to_wan_workflow`

**Files:**
- Modify: `motions-studio/worker/worker_runtime/linux.py` (chèn hàm mới NGAY SAU `build_wan_workflow`, sau dòng `return wf` ~1716)
- Test: `motions-studio/worker/tests/test_character_swap_workflow.py` (tạo mới)

**Interfaces:**
- Consumes: `build_wan_workflow(ref_name, motion_name, p, prefix)` (đã có), helpers `_motion_float/_motion_int` (đã có).
- Produces: `_apply_swap_to_wan_workflow(wf: dict, p: dict) -> dict` — Task 4 gọi sau `build_wan_workflow` khi `_swapEngine == "wananimate"`.

**Bối cảnh kỹ thuật (đã verify 21/08 trên source các commit pin):** Wan-Animate Mix mode theo example `wanvideo_WanAnimate_example_01.json` của kijai: `bg_images` = frame video gốc TÔ ĐEN vùng người (`DrawMaskOnImage` color "0, 0, 0"), `mask` = mask người đã `GrowMask(10)` → `BlockifyMask(32)`. Ta thay SAM2+PointsEditor (interactive) của example bằng SAM3 core (text prompt → headless). SAM3 checkpoint load bằng `CheckpointLoaderSimple` (output 0 = MODEL, output 1 = CLIP riêng của SAM3 dùng cho `CLIPTextEncode`).

- [ ] **Step 1: Viết test fail** — tạo `motions-studio/worker/tests/test_character_swap_workflow.py`:

```python
import sys
import types
import unittest

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

from worker_runtime.linux import (  # noqa: E402
    _apply_swap_to_wan_workflow,
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `cd motions-studio/worker && python3 -m unittest tests.test_character_swap_workflow -v`
Expected: FAIL/ERROR — `ImportError: cannot import name '_apply_swap_to_wan_workflow'`.

- [ ] **Step 3: Implement** — chèn vào `linux.py` ngay sau `return wf` của `build_wan_workflow` (~dòng 1716):

```python
def _apply_swap_to_wan_workflow(wf, p):
    """Character-swap engine wananimate: chuyển graph animation → replacement (Mix) mode.

    Mix mode giữ background CỦA VIDEO: bg_images = frame driver tô đen vùng người, mask = vùng
    người. Chuỗi mask theo example kijai wanvideo_WanAnimate_example_01.json (Grow 10 → Blockify 32),
    thay SAM2+PointsEditor interactive bằng SAM3 core text-prompt để chạy headless.
    Chỉ thêm node 200-206 + 2 input của node 81; pose/face/sampler của motion giữ nguyên.
    """
    sam3_prompt = str(p.get("sam3Prompt") or p.get("sam3_prompt") or "person").strip() or "person"
    wf["200"] = {"class_type": "CheckpointLoaderSimple", "inputs": {
        "ckpt_name": "sam3.1_multiplex_fp16.safetensors"}}
    wf["201"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": ["200", 1], "text": sam3_prompt}}
    wf["202"] = {"class_type": "SAM3_VideoTrack", "inputs": {
        "images": ["12", 0], "model": ["200", 0], "conditioning": ["201", 0],
        "detection_threshold": _motion_float(p, "sam3Threshold", "sam3_threshold", default=0.5),
        "max_objects": _motion_int(p, "sam3MaxObjects", "sam3_max_objects", default=4),
        "detect_interval": _motion_int(p, "sam3DetectInterval", "sam3_detect_interval", default=1)}}
    wf["203"] = {"class_type": "SAM3_TrackToMask", "inputs": {
        "track_data": ["202", 0],
        # rỗng = union mọi người trong video; "0" = chỉ người đầu (chọn khi video đông người)
        "object_indices": str(p.get("maskIndices") or p.get("mask_indices") or "")}}
    wf["204"] = {"class_type": "GrowMaskWithBlur", "inputs": {
        "mask": ["203", 0],
        "expand": _motion_int(p, "maskGrow", "mask_grow", default=10),
        "incremental_expandrate": 0.0, "tapered_corners": True, "flip_input": False,
        "blur_radius": 0.0, "lerp_alpha": 1.0, "decay_factor": 1.0, "fill_holes": False}}
    wf["205"] = {"class_type": "BlockifyMask", "inputs": {
        "masks": ["204", 0],
        "block_size": _motion_int(p, "maskBlockify", "mask_blockify", default=32)}}
    wf["206"] = {"class_type": "DrawMaskOnImage", "inputs": {
        "image": ["12", 0], "mask": ["205", 0], "color": "0, 0, 0"}}
    wf["81"]["inputs"]["bg_images"] = ["206", 0]
    wf["81"]["inputs"]["mask"] = ["205", 0]
    return wf
```

(Spec §4 nhắc `maskFeather` — bỏ chủ ý: BlockifyMask nhị phân hóa sau đó nên blur trước nó vô nghĩa; bám đúng chuỗi kijai Grow→Blockify. Ghi chú lại trong commit message.)

- [ ] **Step 4: Chạy test pass + test motion cũ không vỡ**

Run: `cd motions-studio/worker && python3 -m unittest discover -s tests -v`
Expected: PASS toàn bộ (kể cả `test_wan_anchored_context`).

- [ ] **Step 5: Commit**

```bash
git add motions-studio/worker/worker_runtime/linux.py motions-studio/worker/tests/test_character_swap_workflow.py
git commit -m "Swap engine wananimate: Mix mode qua SAM3 mask + bg_images (graph motion giữ nguyên)"
```

---

### Task 3: Engine scail2 — `build_scail2_swap_workflow`

**Files:**
- Modify: `motions-studio/worker/worker_runtime/linux.py` (chèn hàm mới NGAY SAU `_apply_swap_to_wan_workflow` của Task 2)
- Test: `motions-studio/worker/tests/test_character_swap_workflow.py` (thêm class test)

**Interfaces:**
- Consumes: `_motion_float/_motion_int` (đã có).
- Produces: `build_scail2_swap_workflow(ref_name, motion_name, p, prefix="swap-out") -> dict` — Task 4 gọi khi `_swapEngine == "scail2"`.

**Bối cảnh kỹ thuật (trích từ template chính thức Comfy-Org `video_wan21_scail2_character_replacement.json`, subgraph Base, đã bung edge-list 21/08):** nhánh turbo = steps 6, cfg 1.0, euler + BasicScheduler simple denoise 1.0, `ModelSamplingSD3` shift 5, LoRA chain UNETLoader → DPO(1.0) → lightx2v rank64(0.8). SAM3 track video + track ảnh ref (cùng `SAM3_VideoTrack`, text mặc định "human") → `SCAIL2ColoredMask(replacement_mode=True)` → 2 output nối `pose_video_mask`/`reference_image_mask` của `WanSCAILToVideo`. `SamplerCustom` output slot 1 (denoised) → `VAEDecode`. Khác template: VHS_LoadVideo/VHS_VideoCombine thay LoadVideo/SaveVideo (đồng bộ toolchain repo + mux audio driver), fp8_scaled thay fp16 (32GB VRAM), 1 segment ≤81 frame (extend chaining làm SAU khi nghiệm thu chất lượng).

- [ ] **Step 1: Viết test fail** — thêm vào `test_character_swap_workflow.py`:

```python
from worker_runtime.linux import build_scail2_swap_workflow  # noqa: E402  (thêm vào import đầu file)


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
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `cd motions-studio/worker && python3 -m unittest tests.test_character_swap_workflow -v`
Expected: ImportError `build_scail2_swap_workflow`.

- [ ] **Step 3: Implement** — chèn vào `linux.py` sau `_apply_swap_to_wan_workflow`:

```python
def build_scail2_swap_workflow(ref_name, motion_name, p, prefix="swap-out"):
    """Character-swap engine scail2 — node CORE native (comfy_extras/nodes_scail.py + nodes_sam3.py).

    Graph bám subgraph Base của template chính thức Comfy-Org video_wan21_scail2_character_replacement
    (nhánh turbo: 6 bước, cfg 1, euler/simple, shift 5, LoRA DPO 1.0 + lightx2v rank64 0.8).
    Khác template: VHS_LoadVideo/VHS_VideoCombine (đồng bộ toolchain + mux audio driver), unet
    fp8_scaled (fp16 32.8GB không vừa 5090 32GB), 1 segment ≤81 frame (extend chaining làm sau).
    """
    W = (int(p.get("width", 544)) // 32) * 32       # WanSCAILToVideo đòi bội 32 (io.Int step=32)
    H = (int(p.get("height", 960)) // 32) * 32
    F = min(int(p.get("frames", 81) or 81), 81)     # SCAIL-2 train theo chunk 81 frame
    rfps = int(p.get("render_fps", 16) or 16)
    sam3_vid = str(p.get("sam3VideoPrompt") or p.get("sam3Prompt") or "human").strip() or "human"
    sam3_img = str(p.get("sam3ImagePrompt") or p.get("sam3Prompt") or "human").strip() or "human"
    pos = str(p.get("positive_prompt") or p.get("prompt") or
              "a person moving naturally, high quality, detailed clothing and face").strip()
    neg = str(p.get("negative_prompt") or "").strip()
    return {
        "10": {"class_type": "LoadImage", "inputs": {"image": ref_name}},
        "11": {"class_type": "ImageResizeKJv2", "inputs": {
            "image": ["10", 0], "width": W, "height": H, "upscale_method": "lanczos",
            "keep_proportion": "crop", "pad_color": "0, 0, 0", "crop_position": "center",
            "divisible_by": 32, "device": "cpu"}},
        "12": {"class_type": "VHS_LoadVideo", "inputs": {
            "video": motion_name, "force_rate": rfps, "custom_width": W, "custom_height": H,
            "frame_load_cap": F, "skip_first_frames": 0, "select_every_nth": 1, "format": "AnimateDiff"}},
        # ── SAM3: track người trong driver + segment người trong ảnh ref, cùng 1 checkpoint ──
        "20": {"class_type": "CheckpointLoaderSimple", "inputs": {
            "ckpt_name": "sam3.1_multiplex_fp16.safetensors"}},
        "21": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["20", 1], "text": sam3_vid}},
        "22": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["20", 1], "text": sam3_img}},
        "23": {"class_type": "SAM3_VideoTrack", "inputs": {
            "images": ["12", 0], "model": ["20", 0], "conditioning": ["21", 0],
            "detection_threshold": _motion_float(p, "sam3Threshold", "sam3_threshold", default=0.5),
            "max_objects": _motion_int(p, "sam3MaxObjects", "sam3_max_objects", default=4),
            "detect_interval": 1}},
        "24": {"class_type": "SAM3_VideoTrack", "inputs": {
            "images": ["11", 0], "model": ["20", 0], "conditioning": ["22", 0],
            "detection_threshold": 0.5, "max_objects": 4, "detect_interval": 1}},
        "25": {"class_type": "SCAIL2ColoredMask", "inputs": {
            "driving_track_data": ["23", 0], "ref_track_data": ["24", 0],
            "object_indices": str(p.get("maskIndices") or p.get("mask_indices") or ""),
            "sort_by": "left_to_right", "replacement_mode": True}},
        # ── model + LoRA (thứ tự template: unet → DPO → lightx2v → shift) ──
        "30": {"class_type": "UNETLoader", "inputs": {
            "unet_name": os.environ.get("SCAIL2_UNET", "wan2.1_14B_SCAIL_2_fp8_scaled.safetensors"),
            "weight_dtype": "default"}},
        "31": {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": ["30", 0], "lora_name": "wan2.1_SCAIL_2_DPO_lora_bf16.safetensors",
            "strength_model": _motion_float(p, "dpoLora", "dpo_lora", default=1.0)}},
        "32": {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": ["31", 0], "lora_name": "lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors",
            "strength_model": _motion_float(p, "distillLora", "distill_lora", default=0.8)}},
        "33": {"class_type": "ModelSamplingSD3", "inputs": {
            "model": ["32", 0], "shift": _motion_float(p, "shift", default=5.0)}},
        "40": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "type": "wan", "device": "default"}},
        "41": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["40", 0], "text": pos}},
        "42": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["40", 0], "text": neg}},
        "50": {"class_type": "VAELoader", "inputs": {"vae_name": "Wan2_1_VAE_bf16.safetensors"}},
        "60": {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": "clip_vision_h.safetensors"}},
        "61": {"class_type": "CLIPVisionEncode", "inputs": {
            "clip_vision": ["60", 0], "image": ["11", 0], "crop": "none"}},
        # ── conditioning + sampler ──
        "70": {"class_type": "WanSCAILToVideo", "inputs": {
            "positive": ["41", 0], "negative": ["42", 0], "vae": ["50", 0],
            "width": W, "height": H, "length": F, "batch_size": 1,
            "pose_video": ["12", 0], "pose_video_mask": ["25", 0], "replacement_mode": True,
            "pose_strength": _motion_float(p, "pose_strength", "poseStrength", default=1.0),
            "pose_start": 0.0, "pose_end": 1.0,
            "reference_image": ["11", 0], "reference_image_mask": ["25", 1],
            "clip_vision_output": ["61", 0],
            "video_frame_offset": 0, "previous_frame_count": 5}},
        "80": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "81": {"class_type": "BasicScheduler", "inputs": {
            "model": ["33", 0], "scheduler": "simple",
            "steps": int(p.get("steps", 6) or 6), "denoise": 1.0}},
        "90": {"class_type": "SamplerCustom", "inputs": {
            "model": ["33", 0], "add_noise": True,
            "noise_seed": _motion_int(p, "seed", default=42),
            "cfg": _motion_float(p, "cfg", default=1.0),
            "positive": ["70", 0], "negative": ["70", 1],
            "sampler": ["80", 0], "sigmas": ["81", 0], "latent_image": ["70", 2]}},
        "100": {"class_type": "VAEDecode", "inputs": {"samples": ["90", 1], "vae": ["50", 0]}},
        "110": {"class_type": "VHS_VideoCombine", "inputs": {
            "images": ["100", 0], "frame_rate": rfps, "loop_count": 0, "filename_prefix": prefix,
            "format": "video/h264-mp4", "pingpong": False, "save_output": True, "audio": ["12", 2]}},
    }
```

- [ ] **Step 4: Chạy test pass**

Run: `cd motions-studio/worker && python3 -m unittest discover -s tests -v`
Expected: PASS toàn bộ.

- [ ] **Step 5: Commit**

```bash
git add motions-studio/worker/worker_runtime/linux.py motions-studio/worker/tests/test_character_swap_workflow.py
git commit -m "Swap engine scail2: graph node core native bám template chính thức (turbo 6 bước)"
```

---

### Task 4: `run_character_swap` + hook trong `run_motion` + PIPELINES

**Files:**
- Modify: `motions-studio/worker/worker_runtime/linux.py`:
  - hook build workflow trong `run_motion` (~dòng 4368-4394)
  - tắt drift-fix khi swap (~dòng 4578)
  - hàm `run_character_swap` mới (chèn NGAY SAU `run_motion`, sau dòng `api_upload_output(...)` ~4584)
  - entry `PIPELINES` (~dòng 9912, thêm TRƯỚC dấu `}` đóng)
- Test: `motions-studio/worker/tests/test_character_swap_workflow.py` (thêm class test)

**Interfaces:**
- Consumes: `_apply_swap_to_wan_workflow` (Task 2), `build_scail2_swap_workflow` (Task 3), `run_motion` (đã có).
- Produces: `run_character_swap(job)` — worker claim job type `character-swap` gọi nó; `PIPELINES["character-swap"]`. Param công khai: `engine` ("wananimate" mặc định | "scail2"), cùng các param mask/prompt của Task 2/3.

- [ ] **Step 1: Viết test fail** — thêm vào `test_character_swap_workflow.py`:

```python
from unittest.mock import patch  # (thêm vào import đầu file)
from worker_runtime import linux  # noqa: E402  (thêm vào import đầu file)


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
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `cd motions-studio/worker && python3 -m unittest tests.test_character_swap_workflow -v`
Expected: AttributeError `run_character_swap`.

- [ ] **Step 3a: Hook build trong `run_motion`** — thay khối tại ~dòng 4368-4369:

```python
    wan_prefix = f"motion-{job_id[:8]}"
    workflow = build_wan_workflow(ref_name, motion_name, params, prefix=wan_prefix)
```

bằng:

```python
    wan_prefix = f"motion-{job_id[:8]}"
    # ALD 21/08/2026 - character-swap đi chung run_motion (tái dùng toàn bộ driver-processing),
    # chỉ rẽ nhánh Ở ĐÂY theo _swapEngine. Chuỗi rỗng = motion cũ nguyên vẹn.
    _swap_engine = str(params.get("_swapEngine") or "").strip().lower()
    def _build_motion_workflow(_p):
        if _swap_engine == "scail2":
            return build_scail2_swap_workflow(ref_name, motion_name, _p, prefix=wan_prefix)
        _wf = build_wan_workflow(ref_name, motion_name, _p, prefix=wan_prefix)
        if _swap_engine == "wananimate":
            _wf = _apply_swap_to_wan_workflow(_wf, _p)
        return _wf
    if _swap_engine == "scail2" and _F > 81:
        api_log(job_id, f"scail2: 1 segment tối đa 81 frame (extend chaining làm sau) → cắt {_F}→81f", "warn")
        _F = 81; params["frames"] = 81
        params["_target_output_sec"] = min(float(params.get("_target_output_sec") or 0) or (81.0 / rfps), 81.0 / rfps)
        window_plan = _wan_window_plan(params, _F); est_windows = 1
    workflow = _build_motion_workflow(params)
```

LƯU Ý: khối `window_plan = _wan_window_plan(params, _F)` + `est_windows` gốc nằm ngay TRÊN `wan_prefix` (dòng 4357-4367) — giữ nguyên chúng; nhánh scail2 chỉ tính lại khi cắt frame. Hai chỗ fallback resubmit trong `try/except` phía dưới (dòng ~4384 `pid = comfy_submit(build_wan_workflow(...))` và ~4392 `pid = comfy_submit(build_wan_workflow(...))`) đổi thành `pid = comfy_submit(_build_motion_workflow(params))`.

- [ ] **Step 3b: Tắt drift-fix khi swap** — dòng ~4578:

```python
    out_mp4 = _apply_motion_drift_fix(out_mp4, ref_local, tmp, params, job_id)
```

thành:

```python
    if not _swap_engine:
        # drift-fix grade màu THEO ẢNH REF — đúng cho motion (background từ ảnh), SAI cho swap
        # (background từ video): sẽ kéo tông video về tông ảnh mẫu.
        out_mp4 = _apply_motion_drift_fix(out_mp4, ref_local, tmp, params, job_id)
```

- [ ] **Step 3c: `run_character_swap`** — chèn sau dòng cuối của `run_motion` (~4584):

```python
def run_character_swap(job):
    """Thay nhân vật trong video bằng người mẫu từ ảnh — GIỮ background + camera CỦA VIDEO.

    Ngược với motion (background từ ảnh ref). Tái dùng run_motion cho toàn bộ driver-processing;
    rẽ nhánh build workflow theo params['_swapEngine'] (hook trong run_motion).
    Spec: docs/superpowers/specs/2026-08-21-character-swap-design.md
    """
    inputs = job.get("inputs") or {}
    params = job.get("params") or {}
    # API/batch gửi field 'video' (video nguồn cần thay người); run_motion đọc inputs['motion']
    if inputs.get("video") and not inputs.get("motion"):
        inputs["motion"] = inputs["video"]
    if not (inputs.get("ref") or inputs.get("image")) or not inputs.get("motion"):
        raise RuntimeError("character-swap cần inputs.ref (ảnh người mẫu) + inputs.video (video nguồn)")
    engine = str(params.get("engine") or "wananimate").strip().lower()
    if engine not in ("wananimate", "scail2"):
        raise RuntimeError(f"character-swap: engine không hỗ trợ: {engine!r} (chọn wananimate | scail2)")
    params["_swapEngine"] = engine
    params.setdefault("preset", "drv-5s")            # fps/frame/tỉ lệ theo driver 1:1 như motion
    if engine == "wananimate":
        # Bộ số theo example WanAnimate replacement của kijai (KHÁC tuning animation-mode của motion:
        # 0.7/0.8 bên đó trị "driver cấp vóc dáng" — swap thì người trong video là khung sẵn).
        params.setdefault("lora_relight", 1.0)       # relight LoRA sinh ra riêng cho Mix mode
        params.setdefault("pose_strength", 1.0)
        params.setdefault("face_strength", 1.0)
        params.setdefault("bodyProportionLock", "0")
    else:
        params.setdefault("steps", 6)                # turbo scail2 (template chính thức)
    job["inputs"] = inputs
    job["params"] = params
    return run_motion(job)
```

- [ ] **Step 3d: PIPELINES** — thêm dòng trước `}` đóng (~9913):

```python
    "character-swap": run_character_swap,  # ALD 21/08/2026 - thay nhân vật trong video bằng người mẫu từ ảnh (giữ background VIDEO); engine wananimate (Mix) | scail2
```

- [ ] **Step 4: Verify `_normalize_motion_params` không nuốt key lạ**

Run: `cd motions-studio/worker && python3 -c "
import sys, types
try:
    import requests
except ModuleNotFoundError:
    stub = types.ModuleType('requests'); stub.exceptions = types.SimpleNamespace(ConnectionError=ConnectionError, RequestException=Exception); sys.modules['requests'] = stub
from worker_runtime.linux import _normalize_motion_params
p = _normalize_motion_params({'_swapEngine': 'wananimate', 'engine': 'wananimate', 'preset': 'drv-5s'})
assert p.get('_swapEngine') == 'wananimate', p
print('normalize giữ _swapEngine: OK')"`
Expected: `normalize giữ _swapEngine: OK`. Nếu FAIL → `_normalize_motion_params` strip key lạ; sửa bằng cách đọc engine trong run_motion từ `job['params']` gốc thay vì params đã normalize (giữ nguyên interface còn lại).

- [ ] **Step 5: Chạy toàn bộ test worker pass**

Run: `cd motions-studio/worker && python3 -m unittest discover -s tests -v`
Expected: PASS toàn bộ.

- [ ] **Step 6: Commit**

```bash
git add motions-studio/worker/worker_runtime/linux.py motions-studio/worker/tests/test_character_swap_workflow.py
git commit -m "Job type character-swap: tái dùng run_motion, rẽ nhánh build theo engine"
```

---

### Task 5: Đăng ký job type qua gate check-job-types

**Files:**
- Modify: `motions-studio/worker/runpod/Dockerfile.selfhosted` (2 nhánh `printf` trong `case "$PROFILE"`, ~dòng 160-163)
- Modify: `motions-studio/setup/setup-full.sh:49` (`JOB_TYPE=`)
- Modify: `motions-studio/setup/setup-motion-transfer.sh:39` (`JOB_TYPE=`)
- Modify: `motions-studio/api/src/mc-dispatcher.js:25` (`DEFAULT_JOB_TYPES`)

**Interfaces:**
- Consumes: `PIPELINES["character-swap"]` (Task 4) — gate đọc registry từ đó.
- Produces: worker profile motion-transfer + full + serverless dispatcher đều claim được `character-swap`.

- [ ] **Step 1: Chạy gate để thấy fail trước**

Run: `make check-job-types`
Expected: ĐỎ — registry có `character-swap` mà 5 danh sách kia thiếu.

- [ ] **Step 2: Thêm `character-swap` vào CUỐI cả 5 danh sách** (gate so sánh CÓ THỨ TỰ):

- `Dockerfile.selfhosted` nhánh `full)`: `...,subtitle,enhance,character-swap`
- `Dockerfile.selfhosted` nhánh `*)`: `motion,teen-flycam,trend-tiktok,enhance,character-swap`
- `setup-full.sh`: `JOB_TYPE="...,subtitle,enhance,character-swap"`
- `setup-motion-transfer.sh`: `JOB_TYPE="motion,teen-flycam,trend-tiktok,enhance,character-swap"`
- `mc-dispatcher.js`: `const DEFAULT_JOB_TYPES = "motion,teen-flycam,trend-tiktok,enhance,character-swap"`

- [ ] **Step 3: Gate xanh**

Run: `make check-job-types`
Expected: PASS. Nếu gate còn kêu thiếu chỗ khác (ví dụ registry-vs-image so khác kiểu) → làm đúng theo thông báo lỗi của nó, KHÔNG thêm vào `EXCLUDED`.

- [ ] **Step 4: Commit**

```bash
git add motions-studio/worker/runpod/Dockerfile.selfhosted motions-studio/setup/setup-full.sh motions-studio/setup/setup-motion-transfer.sh motions-studio/api/src/mc-dispatcher.js
git commit -m "Đăng ký character-swap vào 5 danh sách job type (gate check-job-types xanh)"
```

---

### Task 6: Batch runner — stage + pipeline + gate batch-params

**Files:**
- Modify: `scripts/batchlib/pipelines.py` (STAGES + PIPELINES + docstring đầu file)
- Modify: `scripts/tests/test_batch_pipelines.py` (thêm test)
- Modify (nếu gate đòi): `scripts/batch-params.json`
- Modify: file hướng dẫn batch (tìm bằng `grep -rl "motion-enhance" docs/` — thêm 2 pipeline mới vào bảng)

**Interfaces:**
- Consumes: job type `character-swap` với fieldnames `ref` (ảnh) + `video` (video nguồn) — khớp `run_character_swap` (Task 4).
- Produces: pipeline `character-swap-enhance` và `tryon-character-swap-enhance` cho `make batch`.

- [ ] **Step 1: Viết test fail** — thêm vào `scripts/tests/test_batch_pipelines.py` (mirror style test hiện có trong file):

```python
def test_character_swap_enhance_roles(self):
    self.assertEqual(required_roles("character-swap-enhance"), {"character", "driver"})

def test_tryon_character_swap_enhance_roles(self):
    self.assertEqual(required_roles("tryon-character-swap-enhance"),
                     {"character", "outfit", "driver"})

def test_character_swap_stage_fieldnames(self):
    stage = STAGES["character-swap"]
    self.assertEqual(stage.job_type, "character-swap")
    self.assertEqual(set(stage.inputs), {"ref", "video"})
```

(Điều chỉnh import/class cho khớp file test hiện có — đọc file trước khi thêm.)

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `make batch-test`
Expected: FAIL — KeyError/PipelineError `character-swap`.

- [ ] **Step 3: Implement** — trong `scripts/batchlib/pipelines.py`:

Thêm vào `STAGES` (sau entry `"motion"`):

```python
    "character-swap": Stage(
        name="character-swap", job_type="character-swap",
        inputs={"ref": "prev|material:character",
                "video": "material:driver"},
        output_ext=".mp4", min_bytes=100_000, timeout_min=60,
    ),
```

Thêm vào `PIPELINES`:

```python
    "character-swap-enhance": ["character-swap", "enhance"],
    "tryon-character-swap-enhance": ["tryon", "character-swap", "enhance"],
```

Cập nhật docstring đầu file (khối "đã đối chiếu") thêm dòng:

```
  character-swap  linux.py run_character_swap    inputs.ref (ảnh người mẫu) + inputs.video (video nguồn)
```

- [ ] **Step 4: Test pass + gate batch-params**

Run: `make batch-test && make check-batch-params`
Expected: batch-test PASS. Nếu `check-batch-params` ĐỎ vì type mới: chạy `python3 scripts/batch_params.py character-swap` xem AST thấy gì, rồi thêm block `"character-swap"` vào `scripts/batch-params.json` theo đúng schema các block hiện có (tối thiểu: `"allowed": {"engine": ["wananimate", "scail2"]}`; khai `"extra"`/`"api"` chỉ khi gate chỉ ra key AST không thấy).

- [ ] **Step 5: Cập nhật hướng dẫn batch**

Run: `grep -rln "motion-enhance" docs/ *.md` → thêm `character-swap-enhance` / `tryon-character-swap-enhance` (kèm 1 dòng: cần material role `driver` = video nguồn chứa người sẽ bị thay) vào cùng chỗ liệt kê pipeline.

- [ ] **Step 6: Commit**

```bash
git add scripts/batchlib/pipelines.py scripts/tests/test_batch_pipelines.py scripts/batch-params.json docs/
git commit -m "Batch: pipeline character-swap-enhance + tryon-character-swap-enhance"
```

---

### Task 7: Nghiệm thu trên pod (cần GPU — chạy khi có pod)

**Files:**
- Modify: `docs/gpu-pod.md` (ghi số đo VRAM/thời gian như các mục đo đạc trước)

**Interfaces:**
- Consumes: toàn bộ Task 1-6 đã merge; pod RunPod 5090 32GB (`make gpu-up`), model đã preload (`setup/preload-models.sh` đọc catalog — 5 model mới ~27GB tải một lần vào Network Volume).

- [ ] **Step 1: Preload model mới lên volume** — theo docs/gpu-pod.md mục preload (chạy `setup/preload-models.sh` từ CPU pod hoặc pod hiện tại); verify: `ls` trên volume đủ 5 file, đúng size.

- [ ] **Step 2: Smoke engine wananimate** — mirror `scripts/pod-smoke.sh:280-300` (đọc file lấy đúng URL/API key), submit:

```bash
curl -s -X POST "$API_URL/jobs" \
  -F "type=character-swap" \
  -F "ref=@/path/model.jpg" \
  -F "video=@/path/driver-5s.mp4" \
  -F 'params={"engine":"wananimate"}'
# poll GET $API_URL/jobs/<id> tới status=done, tải output
```

Expected: `output.mp4` tồn tại, ≥100KB; **background là của VIDEO nguồn** (khác biệt then chốt so với motion — xem bằng mắt); người trong video = người mẫu trong ảnh; ffprobe: đúng số frame/fps/aspect theo driver.

- [ ] **Step 3: Smoke engine scail2** — như trên với `params={"engine":"scail2"}`. Expected: như trên; nếu lỗi node/model thiếu → check `object_info` ComfyUI trên pod có `WanSCAILToVideo`/`SAM3_VideoTrack` (nếu thiếu = ComfyUI trên pod build từ trước pin mới → rebuild image/pod theo quy trình chuẩn của repo).

- [ ] **Step 4: A/B + đo** — cùng cặp input chạy 2 engine; so identity mặt + quần áo, viền mask, khớp ánh sáng (thử `lora_relight` 0 vs 1 cho wananimate); đo VRAM đỉnh (`nvidia-smi`) + thời gian mỗi engine.

- [ ] **Step 5: Batch end-to-end** — `make batch-validate` + chạy 1 job `character-swap-enhance` qua batch runner.

- [ ] **Step 6: Ghi số đo vào `docs/gpu-pod.md`** (mục mới "Character swap — đo thật <ngày>") + commit:

```bash
git add docs/gpu-pod.md
git commit -m "Đo thật character-swap trên 5090: VRAM/thời gian 2 engine + kết quả A/B"
```
