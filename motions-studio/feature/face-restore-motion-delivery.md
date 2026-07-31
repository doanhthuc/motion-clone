# Feature: CodeFormer phục hồi mặt/răng trong delivery Motion (trị "răng bể pixel")

> Trạng thái: **PLAN — chưa triển khai** (17/07/2026, hoãn vì client đang sử dụng hệ thống).
> Điều kiện tiên quyết: cài node + model trên box `.165` (bước 1) — cần restart ComfyUI, chỉ làm lúc vắng job.

## 1. Vấn đề & chẩn đoán (đã xác minh từ DB job 17/07)

Răng trong output motion bị "bể pixel" (vỡ khối). Chẩn đoán từ job `074be83e` (motion, faceSource=liveportrait,
detailUpscale+deliverySharpen bật) và `c08532c8` (enhance video 2K theo sau):

**KHÔNG phải lỗi Wan hay LivePortrait** — là 3 tầng làm nét chồng nhau nghiền vùng răng (~20px):

1. Wan render gốc 576×1024 → răng vốn mềm/mush do VAE nén (miệng quá nhỏ).
2. `detailUpscale: true` → RealESRGAN/4x-UltraSharp ×4 (`_apply_motion_detail_upscale`, linux.py ~815).
   **Thủ phạm chính**: ESRGAN là GAN thuần không hiểu mặt người → "bịa" răng thành khối vỡ.
   (Đúng failure mode đã ghi ở comment linux.py ~9384 — vì thế enhance ẢNH mới có CodeFormer, còn nhánh
   delivery VIDEO motion thì CHƯA có.)
3. `deliverySharpen: true` (CAS trong `_apply_motion_delivery`) + enhance 2K (FlashVSR thiếu node →
   fallback ffmpeg lanczos+sharpen) → mài sắc viền vỡ thêm 2 lần nữa.

Ghi chú: `faceRestore: true` trong job enhance VIDEO hiện là **NO-OP** (CodeFormer chỉ nối cho mode Ảnh),
và node `facerestore_cf` **CHƯA CÀI trên box** (đã verify 17/07: `~/ComfyUI/custom_nodes` không có,
`models/facerestore_models` không tồn tại) → cả enhance ảnh lâu nay cũng chỉ warn rồi bỏ qua.

**Workaround tạm (không code):** video có cảnh mở miệng gần → tắt `detailUpscale` (răng mềm thay vì vỡ).

**17/07 — ĐÃ ẨN + ÉP TẮT "Làm nét chi tiết" chờ feature này.** Khi triển khai xong CodeFormer, MỞ LẠI 3 chỗ:
1. `motions/.../InspectorMotionTransfer.vue` — bỏ `v-if="false"` toggle ESRGAN + bỏ dòng ép `detailUpscale: false` trong init local (sau spread).
2. `motion-task-cloud/app/pages/index.vue` — bỏ `v-if="false"` label "Làm nét chi tiết".
3. `motion-task-cloud/server/api/tasks/index.post.js` — khôi phục `requestedDetailUpscale` + `detailUpscale: Boolean(queue.active && requestedDetailUpscale)`.
4. `motion-backend/api/src/routes/jobs.js` POST /jobs — bỏ block ép `params.detailUpscale = false` cho type motion (chốt chặn API).
5. `motion-backend/api/src/task-cloud/auto-worker.js` — khôi phục `detailUpscale: cfg.detailUpscale === true`.
("Sharpen khi xuất"/CAS vẫn để nguyên, không ẩn.)

## 2. Giải pháp: nối CodeFormer SAU pass ESRGAN trong delivery motion

CodeFormer detect mặt trên từng frame → dựng lại CHỈ vùng mặt/răng, phần còn lại giữ nét ESRGAN.
Builder ảnh đã có sẵn khuôn (`build_image_upscale_workflow` linux.py ~9383) — mở rộng sang video.

### Bước 1 — Cài trên box `.165` (SSH, làm LÚC VẮNG JOB vì phải restart ComfyUI)

```bash
# 1. Node
cd ~/ComfyUI/custom_nodes
git clone https://github.com/mav-rik/facerestore_cf
~/ComfyUI/venv/bin/pip install -r facerestore_cf/requirements.txt   # facexlib...

# 2. Model CodeFormer
mkdir -p ~/ComfyUI/models/facerestore_models
wget -O ~/ComfyUI/models/facerestore_models/codeformer-v0.1.0.pth \
  https://github.com/sczhou/CodeFormer/releases/download/v0.1.0/codeformer.pth

# 3. Restart ComfyUI (supervisor tự dậy — xem memory: pkill python comfy; KIỂM TRA queue rỗng trước!)
#    curl -s http://127.0.0.1:8188/queue  → running/pending rỗng mới pkill
# 4. Verify:
curl -s http://127.0.0.1:8188/object_info/FaceRestoreCFWithModel | head -c 300   # phải != {}
```

Lưu ý: facexlib tự tải weights detect (retinaface_resnet50) vào `models/facedetection` ở lần chạy đầu
→ job đầu tiên sau khi bật sẽ chậm hơn ~30s. Cài xong node này thì `faceRestore` của **enhance ẢNH**
cũng tự sống lại (mặc định BẬT sẵn trong code).

### Bước 2 — Code worker `linux.py` (commit → box pull → `pm2 restart worker --update-env`)

Sửa `build_video_detail_upscale_workflow` (~798) + `_apply_motion_detail_upscale` (~815):

- Thêm tham số `face_restore=False, face_fidelity=0.5` vào builder; khi bật, chèn giữa node `40`
  (ImageScale về target) và node `110` (VHS_VideoCombine):
  ```python
  "45": {"class_type": "FaceRestoreModelLoader", "inputs": {"model_name": _frm}},   # env MOTION_FACE_RESTORE_MODEL, default codeformer-v0.1.0.pth
  "50": {"class_type": "FaceRestoreCFWithModel", "inputs": {
      "facerestore_model": ["45", 0], "image": ["40", 0],
      "facedetection": _fd,            # env MOTION_FACE_DETECT, default retinaface_resnet50
      "codeformer_fidelity": fid}},
  # node 110 đổi images: ["40",0] → ["50",0]
  ```
  ĐẶT SAU ImageScale (target ~1080×1920) chứ không phải sau ESRGAN thô (2304×4096) — ít pixel hơn 4×,
  detect mặt nhanh hơn, kết quả tương đương. Meta-batch VHS xử lý theo chunk `frames_per_batch` → node
  nhận image batch per chunk, hoạt động bình thường trong vòng requeue.
- Trong `_apply_motion_detail_upscale`: mirror đúng pattern của `_run_enhance_image` (~9424):
  - `_fr_want` = `params.faceRestore` (camel+snake), **default BẬT** ("1") khi detailUpscale chạy;
    fidelity = `params.faceFidelity` default 0.5 (0=đẹp/bịa nhiều ↔ 1=bám gốc).
  - `_fr_on = _fr_want and _comfy_has_node("FaceRestoreCFWithModel")` — thiếu node → warn + chạy
    ESRGAN trần như cũ (không vỡ job).
  - Log: thêm "+ CodeFormer mặt (fidelity X)" vào dòng "Làm nét chi tiết OK".
  - Deadline: CodeFormer ~cộng 0.2–0.4s/frame trên 5090 → nới hệ số ước lượng `_est` từ 0.85 → 1.2 s/frame
    và trần `max(900, frames * 1.0)` → `frames * 1.5`.

### Bước 3 — (Tùy chọn, đợt sau) FE toggle

Không bắt buộc cho lần test: default BẬT theo detailUpscale, tắt per-node bằng `faceRestore: false`.
Nếu muốn lộ UI: thêm checkbox "Phục hồi mặt (CodeFormer)" cạnh toggle "Làm nét chi tiết" trong
`InspectorMotionTransfer.vue` + plumb qua task-cloud generators (4 repo — xem memory motion-task-cloud-repo).

## 3. Test plan

1. Chạy lại đúng cặp input của job `074be83e` (drv-15s, detailUpscale+deliverySharpen bật).
2. Soi log worker: phải có "Làm nét chi tiết OK: ... + CodeFormer mặt (fidelity 0.50)".
3. So sánh vùng răng/miệng frame mở miệng: hết vỡ khối, răng liền khối; kiểm tra mặt KHÔNG bị
   "đổi nét" (fidelity 0.5 — nếu mặt bị bịa đẹp quá/khác người → tăng 0.7).
4. Soi flicker: mặt rung nhẹ giữa frame là rủi ro biết trước của per-frame restore — nếu khó chịu,
   thử fidelity 0.7–0.8 (bám gốc hơn = ít rung hơn) trước khi bỏ.
5. KHÔNG chạy enhance 2K chồng lên khi đánh giá (tránh nhiễu biến số).

## 4. Revert plan

- Code: revert commit worker (1 commit riêng cho feature này) + `pm2 restart worker --update-env`.
- Hoặc không cần revert code: set node `faceRestore: false` / env `MOTION_FACE_RESTORE...` — pass
  CodeFormer bị bỏ, ESRGAN trần như hiện tại.
- Node/model trên box vô hại khi không dùng (chỉ tốn ~360MB disk); enhance ảnh được lợi luôn.

## 5. Việc liên quan còn treo (không thuộc feature này)

- Cài FlashVSR (`naxci1/ComfyUI-FlashVSR_Stable` + model) để enhance video 2K là VSR thật thay vì
  lanczos+sharpen (memory motion-enhance-flashvsr).
- Cân nhắc: khi user đã bật enhance 2K sau motion thì detailUpscale + deliverySharpen trong motion
  là chồng chéo — có thể auto-tắt CAS khi biết sẽ enhance tiếp (chưa quyết).
