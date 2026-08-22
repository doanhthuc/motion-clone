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
- `refFrameMatch` (mặc định bật), `refFrameMaxUpscale` (4.0), `refFrameHeadPrompt` (`head`) — **khớp khung hình ảnh ref với driver**, xem §4b
- Độ phân giải: **theo tỉ lệ khung của video nguồn**, không theo ảnh ref (ảnh ref được `keep_proportion=crop` vào khung đã chốt). Dùng logic FIT DRIVER hiện có qua `_fit_driver_wh()`: cạnh ngắn = `short` của preset (`drv-*` → 544), cạnh dài = cạnh ngắn × tỉ lệ driver, cap ≤ `MOTION_VRAM_MAX_EDGE` (968) — 9:16 → 544×960, 3:4 → 544×720, 1:1 → 544×544, 16:9 → 960×544. Chịu VRAM gate `MOTION_VRAM_MAX_EDGE/FRAMES` như motion
- **FIT DRIVER phải được mở lại riêng cho swap.** `_normalize_motion_params` tắt thẳng `fitDriver` cho MỌI preset `drv-*` (chính sách quality-v1, 19/07/2026: khung do `quality` + `aspectRatio` quyết định). Đúng cho Motion — user chọn tỉ lệ trên UI — nhưng sai cho swap, vì swap giữ nguyên background + camera của video nguồn. Không guard thì driver 3:4 bị kéo dãn vào 9:16 (đo thật trên pod 22/08: 576×768 → 544×960, mặt hẹp và dài ra). Nhánh đó nay bỏ qua khi `p["_swapEngine"]` có giá trị; user vẫn ép được bằng `fitDriver=0`
- **Bội của khung khác nhau theo engine**: wananimate bội 16 như motion, scail2 bội **32** (`WanSCAILToVideo` khai `io.Int step=32`; builder scail2 floor về bội 32, nên nếu FIT DRIVER trả bội 16 lẻ thì driver 3:4 rơi 720→704 và `VHS_LoadVideo` kéo dẹt khung ~2.2% vì `custom_width/custom_height` không giữ tỷ lệ). Với scail2, 3:4 → 544×736. Làm tròn lên mà vượt trần cạnh dài thì lùi một bậc — trần là ngân sách VRAM đã đo
- Toàn bộ param mask/SAM3/lora/prompt kể trên là public param — khai trong `scripts/batch-params.json` khối `character-swap.extra` (gate `make check-batch-params`), vì `run_character_swap` chỉ `setdefault(...)` (AST không thấy), giá trị thật được các builder graph `.get()` trực tiếp.

## 4b. Khớp khung hình ảnh ref với driver (neo bằng cái đầu)

**Bệnh** (đo thật 22/08, driver `dandong5`): driver là selfie cận sát — chỉ đầu và một bên vai, không thấy thân — còn ref `nhanvat-1.jpeg` là ảnh toàn thân. Wan cố nhét cả bố cục toàn thân của ref vào khung chỉ đủ chỗ cho cái đầu, ra **giải phẫu bịa hoàn toàn**: nửa dưới khung là một cánh tay khổng lồ quấn đúng cái chân váy xám của ảnh ref. Đây là ràng buộc của Wan-Animate replacement mode, không phải bug graph — khung hình ref phải tương đương khung hình driver.

**Cách trị.** Đầu là bộ phận DUY NHẤT chắc chắn có mặt trong cả hai ảnh, bất kể khung nào. Phóng ref cho đầu cao bằng đầu driver rồi dóng tâm đầu → phần cơ thể thấy được tự khớp. Không cần dạy máy khái niệm "cận cảnh" hay "toàn thân".

**Ba bước:**

1. **Graph thăm dò** `build_swap_headprobe_workflow` — `LoadImage(ref)` và `VHS_LoadVideo(driver, 3 frame rải đều, custom_width/height = khung render)`, mỗi nhánh qua cùng một checkpoint SAM3 với prompt `head`, ra `MaskToImage` → `SaveImage` hai prefix tách biệt. Dùng LẠI đúng chuỗi `SAM3_VideoTrack` → `SAM3_TrackToMask` của graph chính (node này nhận IMAGE batch nên ảnh tĩnh cũng chạy) — không thêm phụ thuộc node mới nào. Driver nạp đúng khung render nên bbox đo được đã ở hệ toạ độ đích.
2. **Đo bằng Python thuần** — `_comfy_node_images` tải PNG theo NODE ID (`comfy_fetch_output` chỉ trả file đầu tiên toàn graph, không tách được hai nhánh; `comfy_view_file` thì vứt payload ≤1024 byte, mà mask một màu nén rất nhỏ). `_mask_png_bbox` dùng `Image.getbbox()`. Lấy **trung vị theo chiều cao đầu** của 3 frame driver — khung hình có thể zoom trong clip.
3. **`_ref_framing_crop_box`** (hàm thuần, không I/O, test đầy đủ) → cửa sổ cắt trên ảnh ref gốc, mang sẵn tỉ lệ khung render nên node 11 `ImageResizeKJv2 keep_proportion=crop` thành resize thuần, hết tự center-crop theo ý nó. Chỉ cắt, KHÔNG resize ở PIL — để node 11 resize một lần bằng cùng lanczos.

**Chính sách biên:**
- Tràn mép ảnh ref → **dời** cửa sổ vào trong, không bóp méo (sai vị trí đầu vài chục px chấp nhận được; đổi tỉ lệ cửa sổ là méo cả người).
- Ref không đủ trường nhìn (cửa sổ rộng hơn ảnh) → **bỏ qua**, dùng ref nguyên bản.
- Phải phóng quá `refFrameMaxUpscale` (4.0) → **`RefFramingTooFar`, lỗi job ngay**. Chạy tiếp là chắc chắn hỏng và đốt ~7 phút GPU; báo sớm để người dùng đưa ảnh cận hơn.
- Mọi trục trặc khác (thiếu node, SAM3 không thấy đầu, probe lỗi) → log `warn` + ref nguyên bản. Khớp khung là tiện ích, không được phép làm hỏng job.

**Phạm vi:** chỉ chạy khi `params["_swapEngine"]` có giá trị — **Motion lấy cả background từ ảnh ref nên cắt ref là phá Motion**. Áp cho cả hai engine swap vì crop xảy ra trước `comfy_upload`. Giữ `ref_local` nguyên vẹn, chỉ đổi file đem upload (`_apply_ref_grade_video`, `_apply_face_lock`, `_apply_motion_drift_fix` còn đọc bản gốc).

**Giá phải trả:** thêm một vòng ComfyUI mỗi job (SAM3 ~1.7GB nạp rồi nhả, ước 30–60s), và ref toàn thân dùng cho cảnh cận sẽ bị phóng to nên mờ — đổi lại là hết quái dị. Chuyện mờ do `refEnhance` xử lý, xem §4c.

## 4c. Làm nét ảnh ref đã cắt (`refEnhance`)

Ảnh ref **LÀ nguồn danh tính** Wan sao chép: model làm nét mà tiện tay sửa mặt thì video cuối ra người khác. Vì vậy ba mức xếp theo rủi ro đổi danh tính chứ không theo độ nét.

| Mức | Làm gì | Ảnh rời máy? | Rủi ro danh tính |
|---|---|---|---|
| `off` | chỉ crop | không | không |
| `restore` *(mặc định)* | **FlashVSR** (`build_flashvsr_image_workflow`), tụt về ESRGAN ×4 nếu thiếu node | không | thấp — phục hồi, không sáng tác |
| `gen` | Gemini → **lỗi thì Qwen-Image-Edit cục bộ** → lỗi nữa thì `restore` → cuối cùng crop trần | có, ở tầng Gemini | cao nhất |

**Chỉ chạy khi thật sự phóng lên**: crop ≥ khung render (trường hợp `s<1`) thì bỏ qua, không có gì để phục hồi.

**Không có API Qwen.** Repo chỉ có `GEMINI_API_KEY`; DashScope trong repo này chỉ dùng cho **video** (`happyhorse-i2v`, `wan2.2-animate-move`), và `scripts/batchlib/local_tryon.py:23` ghi rõ `LOCAL_PROVIDERS = {"gemini"}  # + "qwen-max" khi có endpoint/key thật`. Tầng dự phòng vì thế là **Qwen-Image-Edit-2509 chạy cục bộ** trên pod (`build_qwen_create_workflow`, `denoise=0.35` → tinh chỉnh chứ không vẽ lại): không tốn tiền, không chết vì mạng, ảnh không rời máy — tốt hơn một API thứ hai.

**Ghim model Gemini tường minh.** Map `geminiModel` của `run_create_image:5780` và `run_edit_image:6017` còn trỏ id `-preview` mà Google khai tử 25/06/2026, và `docker-compose.yml:299` còn tái lập id đó đè lên default đã sửa. `_ref_enh_gemini` không đọc map đó.

**Vì sao FlashVSR chứ không phải CodeFormer.** Custom node `FaceRestoreCFWithModel` **không được cài** trong deployment này (không có trong Dockerfile, setup script hay catalog) — nhánh face-restore của `_run_enhance_image` xưa nay là code chết trên pod. Thêm nó vào **không đắt** (một dòng ghim sha trong `Dockerfile.selfhosted:56` nhánh `full`, sửa số đếm trong `check-comfy-nodes.mjs`, khai CodeFormer vào catalog, build lại CI, nâng `POD_IMAGE`) nhưng **có rủi ro thật**: `facerestore_cf` là pack cũ (facexlib) đặt lên image torch/cu130 rất mới, mà ComfyUI chỉ log WARNING khi một pack không nạp được — chính `Dockerfile.selfhosted:76` đang mang shim kornia vì đúng nhóm sự cố đó với LTXVideo. Image là thứ MỌI job phụ thuộc, không đáng đánh đổi khi chưa có bằng chứng cần.

**FlashVSR thì đã có sẵn**: ghim trong image cho cả hai profile, model 8.7GB đã nằm trên volume (kiểm tận nơi 22/08), là model phục hồi thật nên tốt hơn hẳn ESRGAN trên khuôn mặt, và vẫn *phục hồi* chứ không *vẽ lại* nên rủi ro danh tính thấp. **Không phải đổi gì ở deployment.** Nếu A/B cho thấy FlashVSR vẫn không đủ với mặt, lúc đó mới thêm `facerestore_cf` — và thử bằng cách clone thẳng vào `custom_nodes` trên pod rồi restart ComfyUI trước, chứ đừng nướng vào image khi chưa biết nó có nạp nổi không.

**Fail-safe:** mọi tầng hỏng → crop trần. Tầng làm nét không bao giờ được phép làm chết job.

## 4d. Thu mask khi nó phủ quá nửa khung

**Số đo 22/08 (dandong5, selfie cận sát):** mask người của driver phủ **61% khung**, bounding box **79%**. Wan phải vẽ lại hai phần ba mỗi frame trong khi DWPose chỉ có vài khớp đầu-vai để dẫn đường — nhiều đất trống, ít chỉ dẫn. Kết quả đo trên 8 lần chạy: **5/8 bịa ra CÙNG một cây đàn guitar**, cùng vị trí cùng góc. Không phải nhiễu vô hướng mà là một chế độ hút: mô hình đọc tư thế nghiêng đầu + tóc xõa + ngồi rồi khớp với tiên nghiệm "người ôm đàn hát".

Chuỗi `GrowMask(10)` → `Blockify(16)` hợp lý cho driver toàn thân (mask nhỏ, cần bao trọn viền) nhưng **phản tác dụng** khi mask đã lớn — nó chỉ nới thêm chỗ trống. Nên đảo chiều: độ phủ > `maskTightenAbove` (0.5) thì **ăn mòn** `maskTightenErode` (-8px) và **bỏ Blockify**.

Độ phủ đo bằng **nhánh thứ ba của graph thăm dò** (§4b): SAM3 đã nạp sẵn nên thêm nhánh gần như miễn phí; tách thành job riêng là trả tiền nạp checkpoint hai lần. Nhánh này dùng đúng `sam3Prompt` / `sam3MaxObjects` / `maskIndices` của graph chính để con số phản ánh mask thật sẽ dùng khi render, và lấy **trung vị 3 frame**.

**Không có số đo thì giữ nguyên hành vi cũ** — probe hỏng hoặc bị tắt thì không được đoán bừa là mask lớn. Người dùng ép `maskGrow`/`maskBlockify` thì luôn thắng.

Kèm theo: `SWAP_POSITIVE_EXTRA` neo cảnh ("một người, tay không, không có gì che thân") vì prompt gốc `MOTION_BASE_POSITIVE` chỉ nói về chất lượng ảnh, không nói gì về nội dung khung hình; và `SWAP_NEGATIVE_EXTRA` thêm cụm nhạc cụ — đây là **bằng chứng chứ không phải phòng xa**, cây đàn đã xuất hiện 5 lần.

**Chưa xác nhận trên pod.** Và bài học của lô 22/08: với nhiễu ~62% thì **tối thiểu 4 seed mỗi cấu hình** mới kết luận được. Ba giả thuyết trước đó (crop trị được, FlashVSR trị được, thuần ngẫu nhiên) đều bị bác bỏ vì rút kết luận từ một mẫu.

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
