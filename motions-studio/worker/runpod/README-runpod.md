# RunPod Serverless — VVIP motion (ALD 23/07/2026)

Chạy video **VVIP** (gói 139/269/519k) trên **RunPod Serverless RTX 5090** thay cho box .165: không xếp
hàng, scale-to-zero (không job = không tốn tiền). Box .165 làm fallback khi RunPod lỗi/quá ngân sách.

## Kiến trúc
```
task-cloud (khách có credit VVIP submit video)
   │  POST https://api.runpod.ai/v2/<ENDPOINT_ID>/run   { input: { id, ref(url), motion(url), preset } }
   ▼
RunPod Serverless worker (1 container = ComfyUI :8188 + rp_handler.py)
   │  handler: tải ref+motion (signed URL) → run_motion() → ComfyUI Wan-Animate → mp4
   │  POST mp4 → task-cloud /api/connector/tasks/<id>/results  (token provider mtcw_...)  ← GIỐNG worker .165
   ▼  trả { ok, delivered:true, seconds }
task-cloud: results.post.js lưu output + email + set 'completed'; dispatcher poll /status chỉ để bắt LỖI (hoàn credit / fallback .165)
```
- `rp_handler.py` **tái dùng nguyên `run_motion`** trong `worker_runtime/linux.py` (monkeypatch biên I/O).
- **KHÔNG dùng MinIO ở RunPod** — kết quả trả qua đúng endpoint connector, tái dùng path lưu/email/FE sẵn có.
- Rule resolution: **drv-10s → 720p** (cap 1280), **drv-15s/20s/30s → 540p** (cap 968) — cứng trong handler.

## Các file (repo này)
| File | Vai trò |
|---|---|
| `worker/runpod/rp_handler.py` | Handler serverless (1 request = 1 job) |
| `worker/runpod/Dockerfile` | Image: ComfyUI Blackwell + custom nodes + worker + handler |
| `worker/runpod/entrypoint.sh` | Khởi ComfyUI nền → chạy handler |

---

## Bước 1 — Build & push image
```bash
cd motion-backend                      # PHẢI ở thư mục gốc (build context)
docker build -f worker/runpod/Dockerfile -t <dockerhub-user>/motion-vvip-runpod:latest .
docker push <dockerhub-user>/motion-vvip-runpod:latest
```
> ⚠️ **Parity custom nodes (chỗ dễ vỡ nhất):** box .165 có node vá tay NGOÀI git — nhất là
> **WanAnimatePreprocess** (kijai, đã patch) + node DWPose/detection + 2 onnx `models/detection`.
> Dockerfile mới chỉ clone 6 node chuẩn giống `comfyui/Dockerfile`. Thiếu node → ComfyUI trả 400
> *"node type not found"*. **Phải copy y hệt `custom_nodes/` của .165 vào image** (thêm `COPY`/clone bản
> nội bộ ở chỗ đánh dấu trong Dockerfile) rồi build lại. Đây là 1 vòng test bắt buộc trên RunPod.

## Bước 2 — Network Volume + models
1. RunPod → **Storage → Network Volume**: tạo volume ~**80–120 GB** ở region **CÒN stock 5090**
   (vd `EUR-IS-1`). Ghi nhớ tên region — endpoint phải cùng region.
2. Đổ **đúng bộ models** như box .165 vào cấu trúc `ComfyUI/models/...` trên volume
   (diffusion Wan 2.2, 4 LoRA distill, vae, clip, `detection/*.onnx`, upscale…). Danh sách gốc:
   `motion-backend/comfyui/models.txt` + `catalog.json`. Cách nhanh: mount volume vào 1 Pod tạm rồi
   `aria2c`/`rclone` kéo từ HF hoặc từ MinIO .165.
3. Khi tạo endpoint (bước 3) chọn **mount volume này vào `/app/ComfyUI/models`**.

## Bước 3 — Tạo Serverless Endpoint
RunPod → **Serverless → New Endpoint**:
- **Image:** `<dockerhub-user>/motion-vvip-runpod:latest`
- **GPU:** RTX 5090 (32 GB) · **Region:** cùng region volume · **Volume:** mount `/app/ComfyUI/models`
- **Container disk:** ~30 GB · **Max workers:** 3–5 · **Idle timeout:** 120–300s · **FlashBoot:** ON
- **Scale type:** Queue (mặc định); scale-to-zero (min workers = 0) — đã chốt.
- **Env vars** (handler trả kết quả qua endpoint connector task-cloud — KHÔNG cần khoá MinIO):

| Env | Giá trị |
|---|---|
| `TASKCLOUD_BASE` | URL task-cloud, vd `https://tasks.datools.info` (fallback nếu request không kèm) |
| `CONNECTOR_TOKEN` | token provider `mtcw_...` của provider `runpod-vvip` (xem Bước 4; fallback nếu request không kèm) |
| `MOTION_FRESH_COMFY` | `0` |
| `MOTION_VRAM_MAX_EDGE` | `1280` (cho 720p ở 10s chạy model-on-VRAM; xem lưu ý dưới) |
| `COMFY_EXTRA_ARGS` | (tuỳ) vd `--highvram` |
| Hậu kỳ video | Cố định `Lanczos 1080p · FPS gốc`; không FlashVSR, ESRGAN, CAS hoặc RIFE |

> `TASKCLOUD_BASE` + `CONNECTOR_TOKEN` cũng được dispatcher gửi kèm mỗi request (`input.taskcloud_base`,
> `input.connector_token`) nên có thể để trống env — nhưng set env là lớp fallback an toàn.

> **Chất lượng theo thời lượng:** mọi mốc đang mở đều render nền theo rule VRAM, sau đó upscale
> bằng Lanczos lên **1080p** và giữ **FPS gốc**. Bước này chạy CPU, không cần model nâng nét hoặc RIFE.
> Nếu bước upscale lỗi, hệ thống vẫn giao bản render nền để không làm mất video.

> **720p @10s:** GPU serverless là 5090 TRỐNG (không chia Ollama/OCR như .165) nên 720p 10s KHẢ THI hơn
> hẳn — nhưng vẫn nên test frame-gate. Đặt `MOTION_VRAM_MAX_EDGE=1280` để 720p vào thẳng VRAM; nếu OOM thì
> bỏ (về 968) → handler tự offload block_swap (chậm hơn ~40% nhưng an toàn).

## Bước 4 — Nối task-cloud (Phase 4)
1. **Tạo provider `runpod-vvip`** trong task-cloud (tab admin Máy chủ GPU) → lấy **token `mtcw_...`**. Token này
   để handler POST kết quả về (đưa vào env `CONNECTOR_TOKEN` hoặc dispatcher gửi kèm request).
2. Set env task-cloud: `RUNPOD_ENDPOINT_ID`, `RUNPOD_API_KEY`, `RUNPOD_VVIP_PROVIDER_ID` (= id provider vừa tạo).
3. Dispatcher (Phase 4): task VVIP → gán `route_target = <provider id>` + `claimed_by` + `status='processing'`
   (để `assertProviderOwnsTask` cho handler POST results pass) → đẩy `/run` kèm signed input URL + token →
   poll `/status` chỉ để bắt LỖI (hoàn credit / fallback .165). Kết quả do handler tự push về `/results`.

## Bước 5 — Test tay 1 job
```bash
# Dùng 1 task VVIP thật (id + signed input URL từ task-cloud) để handler POST được kết quả về /results.
curl -s -X POST https://api.runpod.ai/v2/<ENDPOINT_ID>/runsync \
  -H "Authorization: Bearer <RUNPOD_API_KEY>" -H "Content-Type: application/json" \
  -d '{"input":{"id":"<task-id>","preset":"drv-10s","ref":"<signed-input-url-ref>","motion":"<signed-input-url-motion>","taskcloud_base":"https://tasks.datools.info","connector_token":"mtcw_..."}}'
# kỳ vọng: { "output": { "ok": true, "delivered": true, "quality": "720p", "seconds": ... } }  → task chuyển 'completed' trên task-cloud
```
Cold start lần đầu (nạp ComfyUI + Wan models từ volume) ~1–3 phút; job sau nóng máy chạy ngay.

## Chi phí (tham chiếu)
5090 serverless flex ~**$1.58/h** = ~$0.000439/s. Video 10 phút ≈ **$0.26/job** + cold-start. Vốn/gói đã
tính trong bảng giá VVIP (giữ margin ~70%). Guard cap ngày + fallback .165 ở Phase 5.

## Rủi ro / gotcha
- **Custom-node parity** (bước 1) — nguyên nhân #1 nếu job lỗi 400. Đối chiếu `custom_nodes/` .165 vs image.
- **MinIO reachable từ RunPod:** `S3_ENDPOINT` phải mở cho RunPod (public/tunnel), không phải `minio:9000` nội bộ.
- **1 job/worker:** handler monkeypatch global → KHÔNG bật concurrency >1 trong 1 worker (tăng tải = tăng `Max workers`).
- **Region ≠ volume:** endpoint và volume khác region → không mount được.
