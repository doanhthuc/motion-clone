# Character Swap — thay nhân vật trong video bằng người mẫu từ ảnh

Ngày: 2026-08-21. Trạng thái: design đã duyệt (làm cả 2 engine song song, phạm vi API + batch runner, chưa làm FE node).

## 1. Mục tiêu

Tính năng mới cạnh motion control: người dùng đưa **ảnh người mẫu** + **video nguồn có người đang chuyển động**. Đầu ra là video **giữ nguyên background, camera, chuyển động của video nguồn**, nhưng nhân vật trong video được thay bằng người mẫu trong ảnh (mặt + quần áo). Khác motion hiện tại ở chỗ: motion lấy background từ **ảnh**, character swap giữ background từ **video**.

## 2. Phạm vi

- **Làm**: job type mới `character-swap` (API `POST /jobs` + worker + batch runner), 2 engine chọn qua param.
- **Không làm đợt này**: FE workflow node/Inspector, task-cloud capability, serverless mc_handler JOB_TYPES mở rộng (chỉ thêm vào danh sách để gate pass, xem §7).

## 3. Kiến trúc

Job type mới đi đúng flow jobs hiện có: `POST /jobs` (multipart) → bảng `jobs` → worker claim → builder graph ComfyUI → MinIO `output.mp4`. Không đụng vào các normalizer của `motion` (`normalizeMotionDriverSegment`, `enforceMotionResolution` chỉ áp cho type `motion` — job type mới né được toàn bộ clamping đó; worker tự chuẩn hóa).

Hai engine, chung một pipeline handler `run_character_swap` trong `worker_runtime/linux.py`:

| Engine | Model | Cơ chế | Node mới cần |
|---|---|---|---|
| `wananimate` (mặc định) | Wan2.2-Animate-14B fp8 (ĐÃ có trên volume) | Mix/replacement mode của Wan-Animate: cấp `bg_images` (frame video gốc) + `mask` (mask người) vào `WanVideoAnimateEmbeds` | Không — wrapper pin `088128b` đã có 2 input này (đã xác minh trên source) |
| `scail2` | SCAIL-2 fp8_scaled 17.7GB (tải mới) | Node core native `WanSCAILToVideo` với `replacement_mode=true` + `SCAIL2ColoredMask` | Không — core pin v0.29.2 (`3221224`) đã chứa `comfy_extras/nodes_scail.py` + `nodes_sam3.py` (đã xác minh) |

**Segmentation dùng chung**: SAM3 core node (`SAM3_VideoTrack` cho cả video nguồn LẪN ảnh ref — ảnh ref cũng chạy qua `SAM3_VideoTrack`, không dùng `SAM3_Detect`), prompt bằng **text conditioning "person"** → chạy headless không cần click điểm. Model `sam3.1_multiplex_fp16.safetensors` (1.7GB, thư mục `models/checkpoints`).

## 4. Inputs / params

Inputs (multipart fieldname = key trong `inputs`):

- `ref` — ảnh người mẫu (bắt buộc)
- `video` — video nguồn cần thay người (bắt buộc)
- `audio` — thay audio (tùy chọn; mặc định giữ audio video nguồn như motion)

Params (`params` jsonb, camelCase, đọc qua `_motion_*` helpers dùng chung):

- `engine`: `wananimate` | `scail2` (mặc định `wananimate`)
- `prompt`/`positive_prompt`, `negative_prompt`, `seed`, `steps` (mặc định: wananimate 4 bước lightx2v — baseline Fast của Motion Transfer; scail2 6 bước turbo theo template chính thức — `run_character_swap` setdefault, `_normalize_motion_params` cố ý KHÔNG ép về 4 bước cho scail2)
- `lora_relight` / `loraRelight` (wananimate: LoRA `WanAnimate_relight_lora_fp16` đã có sẵn, hiện gắn strength 0 trong graph motion → swap bật mặc định 1.0; scail2 không dùng param này — xem `dpoLora`/`distillLora`)
- `pose_strength` / `poseStrength`, `face_strength` / `faceStrength` (wananimate, mặc định 1.0 cả hai theo example replacement của kijai — `bodyProportionLock` bị tắt mặc định cho CẢ 2 engine để khỏi bị `_normalize_motion_params` ép `pose_strength` xuống 0.7)
- `dpoLora`, `distillLora` (scail2: strength của LoRA DPO và lightx2v distill rank64)
- `swapNegativeExtra` (chuỗi, mặc định cụm chặn bịa vật thể `bouquet, flowers, holding objects, …`; **rỗng = tắt** khi cần mẫu cầm sản phẩm thật) — nối thêm vào negative prompt gốc, chỉ áp cho swap
- `maskGrow` (px, mặc định 10), `maskBlockify` (block size, **mặc định 16** — hạ từ 32 sau khi đo thật 21/08: ô 32px nới vùng vẽ lại phình ra ngoài dáng người nên Wan có đất bịa vật thể), `maskIndices` (rỗng = union mọi người, `"0"` = chỉ người đầu) — nới rồi block-hoá mask người trước khi đưa vào graph, đúng chuỗi kijai `GrowMaskWithBlur` → `BlockifyMask`. Không có `maskFeather`: Blockify nhị phân hoá mask thành các ô vuông cứng cạnh, blur/feather trước đó vô nghĩa (đã bỏ khỏi thiết kế)
- `sam3Prompt` (dùng chung wananimate + fallback scail2), `sam3VideoPrompt`/`sam3ImagePrompt` (scail2, tách riêng prompt track video vs segment ảnh ref), `sam3Threshold`, `sam3MaxObjects`, `sam3DetectInterval` — điều khiển `SAM3_VideoTrack`
- `driverStartSec` / `driverDurSec` — tái dùng `_cut_motion_driver_segment`
- Độ phân giải: theo aspect video nguồn (logic FIT DRIVER hiện có), cạnh dài mặc định 704, chịu VRAM gate `MOTION_VRAM_MAX_EDGE/FRAMES` như motion
- Toàn bộ param mask/SAM3/lora/prompt kể trên là public param — khai trong `scripts/batch-params.json` khối `character-swap.extra` (gate `make check-batch-params`), vì `run_character_swap` chỉ `setdefault(...)` (AST không thấy), giá trị thật được các builder graph `.get()` trực tiếp.

## 5. Graph chi tiết

### 5.1 Engine `wananimate` — mở rộng builder hiện có

Thêm tham số `swap_mode` vào `build_wan_workflow(ref_name, motion_name, p, prefix)` (hoặc builder mỏng `build_wan_swap_workflow` gọi lại phần chung). Khác biệt so với graph motion:

1. Thêm nhánh mask: `VHS_LoadVideo`(12) → `CLIPTextEncode`("person") + `SAM3ModelLoader` → `SAM3_VideoTrack` → mask union → `GrowMask`/blur (KJNodes có sẵn) → input `mask` của node 81.
2. `bg_images` của node 81 = output frame của node 12 (video gốc, resize cùng W×H).
3. LoRA relight strength từ 0 → `lora_relight`/`loraRelight` (node 40 giữ nguyên cấu trúc `WanVideoLoraSelectMulti`).
4. Pose retarget (node 27) giữ **tắt** — replacement mode không hỗ trợ retarget.
5. Phần còn lại (DWPose, ViTPose face-crop, sampler 4-step dpm++_sde cfg 1, decode tiled, VHS_VideoCombine mux audio driver) giữ nguyên.

VRAM: cùng model + thêm SAM3 1.7GB — dùng `_ensure_vram_for_motion` + `comfy_recycle` hiện có; SAM3 chạy trước khi model Wan nạp nên đỉnh VRAM không cộng dồn (nếu đo thấy cộng dồn thì chèn free-vram giữa 2 bước).

### 5.2 Engine `scail2` — builder mới, node core native

Theo template chính thức `video_wan21_scail2_character_replacement.json` (Comfy-Org/workflow_templates):

1. Video nguồn → `SAM3_VideoTrack` (text "person"/`sam3VideoPrompt`) → `track_data` → `SCAIL2ColoredMask` (nền trắng cho replacement) → `pose_video_mask`.
2. Ảnh ref → cũng `SAM3_VideoTrack` (text "person"/`sam3ImagePrompt`, cùng checkpoint) → `reference_image_mask` màu tương ứng identity.
3. `WanSCAILToVideo`: `pose_video` = frame video nguồn, `replacement_mode=true`, `reference_image` + mask.
4. Loader core: `UNETLoader` (`wan2.1_14B_SCAIL_2_fp8_scaled.safetensors`), `CLIPLoader` (`umt5_xxl_fp8_e4m3fn_scaled.safetensors`), VAE `Wan2_1_VAE_bf16.safetensors` (đã có), `clip_vision_h.safetensors` (đã có), LoRA lightx2v rank64 + DPO.
5. Sampler core (`SamplerCustom`, euler/simple) **6 bước turbo** (nhánh turbo của template chính thức: cfg 1, shift 5, LoRA DPO 1.0 + lightx2v rank64 0.8), 81 frame/segment; clip dài dùng cấu trúc Base + Extend (overlap 5 frame) — phase 1 giới hạn ≤81 frame/lần render rồi mới thêm Extend nếu cần.

Builder viết theo đúng graph của template chính thức (đọc file JSON template làm nguồn chân lý khi implement, không phỏng đoán tên input).

### 5.3 Xử lý driver dùng chung

Tái dùng của `run_motion`: cắt segment, chuẩn hóa fps/VFR, clamp frame theo video thật, delivery normalize. **Không** dùng: drift fix theo ref (background giờ là của video), faceLock vẫn giữ là tùy chọn.

## 6. Model mới (catalog + preload)

Thêm vào `motions-studio/comfyui/catalog-motion-transfer.json`, `comfyui/models.txt`; tải qua `setup/preload-models.sh`:

| File | Thư mục | Cỡ | Nguồn |
|---|---|---|---|
| `sam3.1_multiplex_fp16.safetensors` | `models/checkpoints` | 1.7GB | Comfy-Org/sam3.1 |
| `wan2.1_14B_SCAIL_2_fp8_scaled.safetensors` | `models/diffusion_models` | 17.7GB | Comfy-Org/SCAIL-2 |
| `umt5_xxl_fp8_e4m3fn_scaled.safetensors` | `models/text_encoders` | ~6.7GB | Comfy-Org/Wan_2.1_ComfyUI_repackaged |
| `lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors` | `models/loras` | ~0.7GB | Kijai/WanVideo_comfy |
| `wan2.1_SCAIL_2_DPO_lora_bf16.safetensors` | `models/loras` | ~0.3GB | Comfy-Org/SCAIL-2 |

Đã có sẵn, không tải lại: Wan2.2-Animate fp8, relight LoRA, lightx2v rank32 (wrapper), `Wan2_1_VAE_bf16`, `clip_vision_h`, umt5 enc-bf16 (wrapper), ViTPose/YOLO/DWPose.

## 7. Điểm đăng ký (gate `make check-job-types` liệt kê 6 nguồn)

1. `worker_runtime/linux.py` `PIPELINES["character-swap"] = run_character_swap`.
2. `worker/runpod/Dockerfile.selfhosted` — thêm vào `JOB_TYPES` cả 2 nhánh profile.
3. `setup/setup-full.sh` `JOB_TYPE`.
4. `setup/setup-motion-transfer.sh` `JOB_TYPE` (thêm `character-swap`).
5. `api/src/mc-dispatcher.js` `DEFAULT_JOB_TYPES`.
6. `scripts/check-job-types.mjs` `EXCLUDED` — không cần nếu thêm đủ 5 nguồn trên.

Batch runner: `scripts/batchlib/pipelines.py` — stage `character-swap` (fieldnames `ref`, `video`) + pipelines `character-swap-enhance`, `tryon-character-swap-enhance` (output try-on làm `ref`); whitelist `scripts/batch-params.json` (gate `make check-batch-params`); hướng dẫn `docs` batch nếu có.

Không thêm node ComfyUI mới → `check-comfy-nodes` không đổi. API `routes/jobs.js` nhận type mới tự nhiên (generic); chỉ thêm timeout hợp lý nếu `mediaJobTimeoutSec` cần entry riêng khi sau này làm FE node (ngoài phạm vi).

## 8. Kiểm thử

1. Gate tĩnh: `make check-job-types`, `make check-batch-params` pass.
2. Smoke trên pod: 1 ảnh mẫu + 1 clip ~5s, chạy tuần tự `engine=wananimate` rồi `engine=scail2`, kiểm output.mp4 tồn tại, đúng số frame/aspect, background là của video nguồn (khác biệt then chốt so với motion).
3. A/B chất lượng bằng mắt: identity mặt + quần áo, độ khớp ánh sáng (bật/tắt relight LoRA), viền mask (chỉnh maskGrow/maskBlockify).
4. Đo VRAM đỉnh từng engine trên 5090 32GB, ghi vào docs/gpu-pod.md như các đo đạc trước.

## 9. Rủi ro & đối sách

- **SAM3 bắt nhầm/miss người khi nhiều người trong video**: mặc định union mọi mask "person"; nếu cần chọn 1 người → param `maskIndices` (đã có, vd `"0"` = chỉ người đầu).
- **SCAIL-2 fp16 chi tiết mặt hạn chế (theo paper)**: đã có sẵn đường faceLock (inswapper) làm hậu kỳ tùy chọn.
- **Wrapper `mask` semantics** (mask = vùng người hay vùng giữ?): xác minh bằng example workflow `wanvideo_WanAnimate_example_01.json` của kijai tại commit pin trước khi code.
- **VRAM cộng dồn SAM3 + Wan trên 32GB**: recycle ComfyUI giữa bước segmentation và render nếu đo thấy sát trần.
