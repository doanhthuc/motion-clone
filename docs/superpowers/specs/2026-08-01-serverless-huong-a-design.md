# RunPod Serverless cho stack tự chủ (hướng A) — thiết kế

Ngày: 2026-08-01
Trạng thái: **thiết kế, chưa triển khai.** Cả ba giả định đã đo xong trên pod thật 01/08/2026 —
xem [§Đo thật](#measured). Spec sẵn sàng chuyển thành plan.

## Vấn đề

GPU pod tính tiền theo giờ suốt thời gian tồn tại, kể cả khi không có job nào. Với tải thưa —
vài job một ngày — phần lớn tiền trả cho một con GPU ngồi không.

RunPod Serverless scale-to-zero: không request thì không có worker, không có worker thì không
tính tiền. Nhưng container serverless là stateless và chết sau mỗi job, nên nó chỉ thay được
**worker**, không thay được api / Postgres / MinIO / frontend.

## Cái đã có trong source, và vì sao không dùng thẳng được

`motions-studio/worker/runpod/` có sẵn `rp_handler.py` + `Dockerfile` + `entrypoint.sh` +
`README-runpod.md`. Nhưng nó viết cho **Task Cloud**, không cho stack tự chủ:

- `rp_handler.py:189-196` POST kết quả về `<taskcloud_base>/api/connector/tasks/<id>/results`
  kèm token provider `mtcw_...`
- api trong repo này là **client** của Task Cloud (`task-cloud-auto` poll
  `/api/connector/tasks/claim`), **không phục vụ** route `/connector` nào — đã grep xác nhận
- Phạm vi hẹp: chỉ job `motion`, preset `drv-10s/15s/20s/30s`, độ phân giải cứng trong code,
  hậu kỳ cố định Lanczos 1080p

Nên handler đó là tham chiếu tốt, không phải thứ để tái dùng.

## Kiến trúc

Serverless **thay** worker local. Đúng một nguồn worker tại một thời điểm.

```
   TRƯỚC (hôm nay)                      SAU
   ┌──────────────────────┐             ┌──────────────────────┐
   │ GPU pod              │             │ VPS (không GPU)      │
   │  api · Postgres      │             │  api · Postgres      │
   │  MinIO · FE          │             │  MinIO · FE          │
   │  worker · comfyui    │             │  dispatcher          │
   └──────────────────────┘             └──────────┬───────────┘
    một máy làm tất cả                             │ POST /v2/<id>/run
                                                   ▼
                                        RunPod Serverless (0..N container)
                                          poll /worker/claim ── cùng giao thức
```

Chuyển đổi là `pm2 stop worker && pm2 stop comfyui` cộng việc dời api/Postgres/MinIO sang VPS.
Vì hai bên dùng **cùng một giao thức** (§Quyết định 1), không có bước migrate dữ liệu hay đổi
API nào.

**Không chạy song song hai nguồn worker.** Cân nhắc ban đầu là để serverless gánh thêm cho pod
trong giai đoạn chuyển tiếp, nhưng nó không có lợi ích thật: ở hình dạng giữ pod bạn đã trả tiền
GPU 24/7 nên worker local là miễn phí, thêm serverless chỉ tốn thêm; ở hình dạng VPS thì không
còn GPU nào để chạy worker local. Chỉ có ý nghĩa nếu mục tiêu là gánh tải cao điểm — bài toán
khác, chưa có số liệu, xem §Cố tình KHÔNG làm.

## Quyết định 1 — tái dùng nguyên giao thức worker, không monkeypatch

`worker_runtime/linux.py:5` cho thấy worker local đã nói HTTP đầy đủ với api:

| Gọi | Việc |
|---|---|
| `POST /worker/claim` | nhận 1 job queued, 204 nếu trống |
| `GET /files/{key}` | tải input |
| `POST /jobs/{id}/output` | trả kết quả |
| `POST /jobs/{id}/log` · `PATCH /jobs/{id}` | log, trạng thái |
| `GET /jobs/{id}/cancelled` | honor cancel |
| `POST /worker/heartbeat` | báo sống |

Auth: header `X-Worker-Token`.

Container serverless chạy **đúng code đó**, chỉ đổi `API_URL` sang URL public của api và đặt
`WORKER_TOKEN`. Handler mới chỉ là lớp vỏ mỏng: nhận request → chạy một nhịp claim-and-run →
trả kết quả.

Hệ quả kéo theo, đều là lợi:

- **Mọi job type chạy được ngay** (`motion`, `teen-flycam`, `trend-tiktok`, `enhance`), không
  phải chỉ `motion` như `rp_handler.py`
- Không có nhánh code thứ hai để hai bên lệch nhau theo thời gian
- Input tải qua `GET /files/{key}` của api, nên **MinIO không cần lộ ra internet** cho đường
  input — bỏ được gotcha `S3_ENDPOINT` mà README-runpod cảnh báo

## Quyết định 2 — dispatcher đơn giản, để claim tự phân xử

api thêm một chỗ: job chuyển sang `queued` → `POST https://api.runpod.ai/v2/<ENDPOINT_ID>/run`,
body rỗng.

Worker serverless tỉnh dậy thì **tự gọi `/worker/claim`**, không nhận job id từ request.

Thoạt nhìn lỏng, thực ra là chỗ chặt nhất trong thiết kế. Claim đã atomic trong Postgres. Hai
worker cùng tỉnh mà chỉ có một job thì đứa thứ hai nhận 204 rồi thoát sau ~3 giây — mất vài xu,
không mất tính đúng đắn. Đổi lại:

- không cần route mới cho việc giao job
- không cần trạng thái chia sẻ giữa dispatcher và worker
- không cần xử lý job mồ côi — cơ chế reclaim sẵn có đã lo

Phương án thay thế (truyền `job_id` trong request) đòi thêm route claim-theo-id, và đẻ ra tình
huống job bị gán cho một worker đã chết. Không đáng.

<a id="quyet-dinh-3"></a>
## Quyết định 3 — presigned PUT cho output lớn · **HOÃN**

> **Cập nhật 01/08/2026 — ĐÃ ĐO, không còn là lời kể.** Sau một job motion thật: object lớn nhất
> trong bucket **17.7 MB**, cả bucket 71 MB. Xem [§Đo thật](#measured). Toàn bộ mục này **không
> cần làm cho lần triển khai đầu**.
>
> Ba hệ quả, đều theo hướng nhẹ đi:
> - Không cần `presignPut`, không cần route `/worker/output-url`, không cần nhánh mới trong worker
> - **Ràng buộc "phải là VPS" tan biến** — MinIO không cần cổng public, nên RunPod CPU pod dùng
>   được cho scale-to-zero về sau
> - Bảng hai hình dạng bên dưới không còn là giới hạn thực tế
>
> Giữ nguyên phần dưới đây làm tài liệu: nếu sau này có gói dài hơn hoặc độ phân giải cao hơn đẩy
> output vượt 100MB, đây là thiết kế sẵn sàng dùng. Dấu hiệu để quay lại: job fail ở bước upload
> với HTTP 413 từ Cloudflare.

`api_upload_output` (`linux.py:718`) POST **nguyên file mp4 dạng multipart** qua api. Cloudflare
Tunnel gói free chặn body 100MB, mà `auto-worker.js` đặt `MAX_OUTPUT_BYTES` mặc định 500MB. Đường
này sẽ vỡ với video dài.

Thêm một route và một nhánh trong worker:

```
worker → GET  /worker/output-url?job=<id>   → { url, key }   presigned PUT, TTL 15 phút
worker → PUT  <url>                                          thẳng MinIO, KHÔNG qua Cloudflare
worker → POST /jobs/<id>/output  { key }                     chỉ metadata, vài trăm byte
```

`storage.js` đã import `@aws-sdk/s3-request-presigner` và có `presignGet`; thêm `presignPut` là
việc nhỏ.

**Ràng buộc kéo theo, quan trọng hơn cả bản thân tính năng:** presigned URL phải trỏ tới MinIO mà
worker RunPod với tới được. Cloudflare Tunnel cũng chặn 100MB nên không đi đường đó được. MinIO
cần một cổng public thật.

RunPod pod nằm sau NAT, không mở được cổng tuỳ ý. VPS có IP public thì mở được. **Nên nếu sau này
muốn scale-to-zero, box luôn bật phải là VPS, không phải RunPod CPU pod.** Đây là ràng buộc kỹ
thuật, không phải sở thích.

Worker local trên cùng máy với api vẫn dùng đường multipart cũ — nhánh mới chỉ bật khi
`MOTION_OUTPUT_PRESIGN=1`.

**Mâu thuẫn phải nói rõ:** ở hình dạng "giữ pod" (§Kiến trúc), api và MinIO nằm TRÊN pod, mà pod
sau NAT và chỉ ra internet qua Cloudflare Tunnel. Worker serverless do đó **không PUT thẳng vào
MinIO được**, và output >100MB sẽ hỏng — trần 100MB áp dụng, không né được.

Nói cách khác: hai hình dạng có hai giới hạn khác nhau.

| | Box luôn bật | Output tối đa qua serverless |
|---|---|---|
| Giữ pod (giai đoạn đầu) | pod (NAT + Cloudflare) | **100MB** |
| Scale-to-zero (về sau) | VPS (IP public) | không giới hạn thực tế |

Nếu giả định 1 ở §Ba giả định cho thấy output thật thường dưới 100MB thì mâu thuẫn này vô hại và
Quyết định 3 hoãn được. Nếu output thường vượt 100MB thì **hình dạng "giữ pod" không dùng
serverless cho job nặng được** — phải sang VPS sớm hơn dự định. Đây là lý do giả định 1 phải đo
trước khi viết bất kỳ dòng code nào.

<a id="always-on-box"></a>
## Quyết định 3b — box luôn bật là VPS

Chốt 01/08/2026: **VPS**, không phải RunPod CPU pod. Lý do là chi phí, không còn là ràng buộc kỹ
thuật — §Quyết định 3 đã bỏ nên MinIO không cần cổng public nữa, cả hai lựa chọn đều khả thi về
mặt kỹ thuật.

VPS tính tiền cố định theo tháng và đã bao gồm đĩa. RunPod CPU pod tính theo giờ — chạy 24/7 là
730 giờ mỗi tháng — **cộng** một Network Volume riêng, vì container disk chết theo pod nên PGDATA
và MinIO không sống được nếu thiếu nó. Không lấy được đơn giá CPU của RunPod qua `runpodctl`
(`gpu list` trả 0 entry cho CPU), nhưng cấu trúc tính tiền đủ để kết luận.

Đã xác minh không có gì trên box đó cần GPU:

| PM2 app | Cần ComfyUI? | Trên VPS |
|---|---|---|
| `api` · `minio` | không | chạy |
| `wf-worker` | chỉ ở `freeGpuRam()` (`wf-worker/handlers.js:669`), bọc try/catch, hỏng thì `warn` | chạy |
| `task-cloud-auto` | **có** — throw `"COMFY_URL chưa cấu hình"` (`task-cloud/auto-worker.js:100`) | tắt; stack tự chủ không dùng Task Cloud |
| `worker` · `comfyui` | có | tắt; serverless gánh |

Cài bằng script sẵn có, không viết mới — `fullstack-setup.sh` vốn được viết cho VPS, và
`setup-pm2.sh:20` đã có cờ:

```bash
SKIP_COMFY=1 SKIP_MODELS=1 ./setup/fullstack-setup.sh
```

**Chi phí ẩn phải theo dõi:** trên pod, MinIO nằm trên Network Volume 100GB. Trên VPS nó nằm trên
đĩa VPS — nhỏ hơn, đắt hơn mỗi GB, và video tích tụ dần. Cần hạn lưu output hoặc chuyển file cũ
sang object storage rẻ. Đây là thứ duy nhất có thể làm VPS đắt hơn dự tính, và nó không lộ ra
trong tháng đầu.

## Quyết định 4 — image và model

`custom_nodes/` là thư mục extension của ComfyUI. Mỗi extension đăng ký thêm nhiều **node type**;
workflow gọi node theo tên, thiếu extension thì ComfyUI trả 400 *node type not found*. Danh sách
nền mà `setup-motion-transfer.sh` clone, và node type tương ứng mà `auto-worker.js` bắt buộc box
phải có:

| Extension | Node type |
|---|---|
| `kijai/ComfyUI-WanVideoWrapper` | `WanVideoModelLoader` `WanVideoVAELoader` `WanVideoTextEncodeCached` `WanVideoClipVisionEncode` `WanVideoAnimateEmbeds` `WanVideoSampler` `WanVideoDecode` `WanVideoBlockSwap` `WanVideoLoraSelectMulti` |
| `kijai/ComfyUI-KJNodes` | `ImageResizeKJv2` `FaceMaskFromPoseKeypoints` `ImageCropByMaskAndResize` |
| `Kosinkadink/ComfyUI-VideoHelperSuite` | `VHS_LoadVideo` `VHS_VideoCombine` |
| `Fannovel16/comfyui_controlnet_aux` | `DWPreprocessor` (DWPose) |
| `Fannovel16/ComfyUI-Frame-Interpolation` | `RIFE VFI` |
| `naxci1/ComfyUI-FlashVSR_Stable` | FlashVSR |

`CLIPVisionLoader` nằm trong ComfyUI lõi.

Đây mới là danh sách **nền**. Giả định 2 tồn tại vì pod thật còn có node vá tay ngoài git, nên
clone lại từ đầu chưa chắc ra cùng kết quả.

- **Image**: ComfyUI + custom nodes nướng sẵn. Phải copy **y hệt** `custom_nodes/` của pod thật.
  README-runpod gọi đây là "nguyên nhân #1" gây lỗi 400 *node type not found*: pod thật có node
  vá tay ngoài git (`WanAnimatePreprocess` đã patch, DWPose, hai file onnx trong
  `models/detection`).
- **Model**: Network Volume mount `/app/ComfyUI/models`, cùng region với endpoint. Tải một lần,
  dùng cho mọi worker.
- **Build**: qua GitHub Actions. Không build trên Mac M-series — cross-build image CUDA ~20GB qua
  QEMU quá chậm. Workflow sẵn có (`build-worker-image.yml`) build
  `worker-image/Dockerfile`, không phải file này; cần thêm job mới.
- Image dựng sẵn của upstream (`ghcr.io/ald-project/motion-backend-worker`) **không pull ẩn danh
  được** — đã thử, HTTP 403. Phải build vào registry riêng.

<a id="handler-shape"></a>
## Quyết định 5 — hình dạng handler, và WORKER_ID phải duy nhất

Đọc code ngày 01/08/2026, cả hai điểm dưới đây đều là quan sát từ source chứ không phải phỏng đoán.

### Handler tách sạch, khoảng 15 dòng

`worker_runtime/runner.py` là **hàm thuần nhận callback**: `run_worker_loop(api_claim, api_patch,
api_heartbeat, pipelines, startup, …)`. Việc chạy job thu về hai dòng — `fn = pipelines.get(jt)`
rồi `fn(job)`. `PIPELINES` (`linux.py:9728`) là dict phẳng `job type → hàm`, có sẵn `motion`,
`teen-flycam`, `trend-tiktok`, `enhance`. `_startup()` chỉ dọn queue ComfyUI mồ côi và đã bọc
try/except nên vô hại khi queue rỗng.

```python
from worker_runtime.linux import PIPELINES, api_claim, api_patch, _startup

def handler(event):
    job = api_claim([])
    if not job:
        return {"ok": True, "claimed": False}      # hết job, thoát, tốn vài giây
    fn = PIPELINES.get(job["type"])
    if not fn:
        api_patch(job["id"], status="error", error=f"unsupported {job['type']}")
        return {"ok": False, "error": "unsupported"}
    try:
        fn(job)
        return {"ok": True, "job": job["id"]}
    except Exception as e:
        api_patch(job["id"], status="error", error=str(e))
        return {"ok": False, "error": str(e)}
```

Không monkeypatch gì. Cấu hình đọc từ env lúc import (`API_URL`, `WORKER_TOKEN`, `COMFY_URL`,
`JOB_TYPES`) nên chỉ cần đặt env trước khi import.

**Không cần heartbeat.** `runner.py` gửi mỗi 15 giây, nhưng chính comment trong đó nói
`active_job_id` "chỉ là thông tin hiển thị badge FE". Reclaim không dựa vào heartbeat — xem dưới.

### WORKER_ID phải duy nhất cho từng container — nếu không, worker giết job của nhau

`api/src/routes/jobs.js:219-224`, chạy mỗi lần có ai gọi `/worker/claim`:

```sql
UPDATE jobs SET status='error', error='Worker khởi động lại giữa chừng — vui lòng chạy lại'
 WHERE status='running' AND worker_id=$1 AND NOT (id::text = ANY($2::text[]))
```

Reclaim theo **`worker_id` + danh sách `active_job_ids` gửi kèm**, không theo thời gian chờ
heartbeat. Với worker local chạy một tiến trình dài hạn thì đúng: claim lại nghĩa là nó vừa
restart, nên job `running` cũ là tàn dư.

Với serverless thì sai chết người. `linux.py:36` — `WORKER_ID = os.environ.get("WORKER_ID",
"worker-1")`, **mặc định là hằng số**. Kịch bản:

1. Worker A tỉnh, claim job J1 → `jobs.worker_id='worker-1'`, `status='running'`
2. Worker B tỉnh vài giây sau, gọi `/worker/claim` với `active_job_ids=[]`
3. Câu UPDATE trên khớp J1 → **J1 thành `error` trong lúc A vẫn đang render**

RunPod Serverless với `max workers 3-5` rơi vào đúng kịch bản này ngay từ job thứ hai chạy song
song. Triệu chứng sẽ là job fail rải rác với thông báo "Worker khởi động lại giữa chừng" mà không
có worker nào restart cả — cực khó lần ra nếu không biết trước.

Lưu ý kẻo hiểu nhầm: lỗi này **không** sinh ra từ việc ghép worker local với serverless. Nó sinh
ra từ **nhiều claimer dùng chung một `worker_id`**, mà serverless bản chất là nhiều container ngắn
hạn. Tắt sạch worker local không chữa được — ba container serverless vẫn giết job của nhau.
`max workers = 1` thì né được, nhưng đánh mất lý do dùng serverless.

**Bắt buộc:** mỗi container đặt `WORKER_ID` riêng, sinh lúc khởi động (id worker của RunPod, hoặc
`serverless-<uuid4>`). Đây là một dòng trong `entrypoint.sh`, nhưng thiếu nó thì hệ thống hỏng
theo kiểu ngẫu nhiên và chỉ hỏng khi có tải.

Thêm vào §Kiểm chứng mục 6: hai job cùng lúc, không job nào bị chuyển `error` oan.

## Cố tình KHÔNG làm

**Chạy song song worker local và serverless để gánh tải cao điểm.** Đây là bài toán khác với
scale-to-zero và cần số liệu tải mà hiện chưa có. Nếu sau này cần, nó rẻ: bật lại `pm2 start
worker` trên một máy có GPU là xong, giao thức đã chung. Nhưng lúc đó phải đọc lại
[§Quyết định 5](#handler-shape) — thêm một claimer nữa thì ràng buộc `WORKER_ID` duy nhất càng
chặt hơn.

Ngân sách trần theo ngày · fallback tự động khi serverless lỗi · autoscale theo độ dài hàng đợi ·
nhiều endpoint tách theo job type · concurrency > 1 trong một worker.

Chưa có số liệu tải thật thì mọi thứ trên là đoán. Job lỗi cứ trả về `queued`, worker local nhặt —
đủ dùng, và là hành vi sẵn có chứ không phải code mới.

<a id="assumptions"></a>
## Ba giả định chưa kiểm chứng

Đây là lý do spec này chưa chuyển thành plan. Cả ba chỉ đo được sau khi pod thật chạy một job thật.

1. ~~**Output thật nặng bao nhiêu.**~~ **ĐÃ TRẢ LỜI 01/08/2026: dưới 100MB.** Nguồn: chủ dự án
   xác nhận, chưa phải số đo từ job chạy qua đường này. §Quyết định 3 hoãn, ràng buộc VPS bỏ.
   Nếu về sau thấy job fail với HTTP 413 ở bước upload thì giả định này đã hết đúng.
2. ~~**`custom_nodes/` trên pod thật gồm những gì.**~~ **ĐÃ ĐO 01/08/2026** trên pod
   `ua9a220uubwfl9` sau khi một job motion chạy thành công. Xem [§Đo thật](#measured). Cách đo cũ
   giữ lại bên dưới để lặp lại khi cần:
   `ssh pod 'ls ~/comfyui/custom_nodes && git -C ... rev-parse HEAD'` cho từng node, cộng
   `models/detection/*.onnx`.
3. ~~**Worker code có chạy được trong container serverless không.**~~ **ĐÃ TRẢ LỜI 01/08/2026:
   có, tách sạch.** Xem [§Quyết định 5](#handler-shape). Rủi ro lớn nhất của spec này đã gỡ.

<a id="measured"></a>
## Đo thật trên pod — 01/08/2026

Pod `ua9a220uubwfl9`, RTX 5090, driver 580.126.20, torch 2.12.1+cu130, sau một job motion chạy
thành công end-to-end.

### Kích thước output — giả định 1 xác nhận

| Object lớn nhất trong bucket `motion` | 17.7 MB |
|---|---|
| Object thứ hai | 5.6 MB |
| Cả bucket sau 1 job | 71 MB |

Dưới 100MB rất xa, nên [§Quyết định 3](#quyet-dinh-3) (presigned PUT) đúng là hoãn được. Cảnh báo:
đây là **một** job với clip ngắn. Gói dài hơn hoặc độ phân giải cao hơn có thể vượt — dấu hiệu để
mở lại vẫn là HTTP 413 ở bước upload.

### `custom_nodes/` thật — giả định 2 đóng

Sáu node, đúng bằng danh sách `setup-motion-transfer.sh` clone. **Không có node vá tay ngoài git**
như README-runpod cảnh báo — cảnh báo đó thuộc về box .165, không phải box dựng từ script này.

| Node | Commit |
|---|---|
| `ComfyUI-WanVideoWrapper` | `088128b` |
| `ComfyUI-KJNodes` | `4d46ac1` |
| `ComfyUI-VideoHelperSuite` | `4ee72c0` |
| `comfyui_controlnet_aux` | `e8b689a` |
| `ComfyUI-Frame-Interpolation` | `26545cc` |
| `ComfyUI-FlashVSR_Stable` | `f7f55ba` |

### Weight DWPose nằm ngoài `models/` — phát hiện mới, ảnh hưởng image

```
comfyui_controlnet_aux/ckpts/hr16/yolox-onnx/yolox_l.torchscript.pt              207.6 MB
comfyui_controlnet_aux/ckpts/hr16/DWPose-TorchScript-BatchSize5/dw-ll_..._bs5.pt 128.8 MB
                                                                          tổng   337 MB
```

Ba hệ quả:

1. Chúng **không** nằm trong `models/` nên **không** ở trên Network Volume — nằm trên container
   disk, mất khi `gpu-destroy`, tải lại ở lần dùng DWPose đầu tiên. 337MB, không phải 33GB, nên
   chấp nhận được với pod.
2. Với **serverless thì không chấp nhận được**: mỗi worker cold-start sẽ tải lại 337MB. Phải nướng
   `ckpts/` vào image, hoặc symlink nó sang volume.
3. Định dạng là `.torchscript.pt`, **không phải `.onnx` trong `models/detection`** như
   README-runpod mô tả. Đường dẫn trong README đó không áp dụng cho box dựng bằng script này.

### Model trên volume

13 file · 42 GB (nhóm Wan 2.2 Animate + FlashVSR Enhance). `pod-volume.sh --check` xác nhận
không hồi quy.

## Xử lý lỗi

| Hỏng | Hành vi |
|---|---|
| Worker tỉnh mà không còn job | `/worker/claim` trả 204 → thoát ngay, tốn vài giây |
| Job lỗi giữa chừng | `PATCH /jobs/{id}` về `queued` như worker local; worker local nhặt lại |
| Presigned URL hết hạn | PUT trả 403 → worker xin URL mới một lần, rồi mới báo lỗi |
| MinIO không với tới được từ RunPod | worker phát hiện lúc PUT → rơi về multipart qua api; job >100MB sẽ hỏng và phải báo rõ, không im lặng |
| ComfyUI thiếu custom node | 400 *node type not found* → job fail, log giữ nguyên tên node thiếu |
| Endpoint hết capacity | `/run` trả lỗi → dispatcher bỏ qua, job nằm `queued` cho worker local |

## Kiểm chứng

Không có unit test cho phần này — nó là hạ tầng. Thứ chứng minh được:

1. `custom_nodes/` trong image khớp pod thật, đối chiếu từng commit hash
2. Một job `motion` chạy hết qua serverless, kết quả về đúng database và hiện trên FE
3. Kích thước output thật của vài job đại diện được ghi lại — xác nhận bằng số cái mà §Ba giả
   định mục 1 đang tin theo lời kể. Vượt 100MB thì mở lại §Quyết định 3
4. Tắt `pm2 stop worker` trên pod, job vẫn chạy — chứng minh serverless tự đứng được
5. Không có job nào trong 10 phút → `runpodctl` báo 0 worker đang chạy, hoá đơn không tăng
6. Hai job cùng lúc → hai worker, không job nào bị nhận hai lần, và **không job nào bị chuyển
   `error` oan** với thông báo "Worker khởi động lại giữa chừng" — đây là bài kiểm cho
   [§Quyết định 5](#handler-shape), chạy với `max workers` ≥ 2 mới có ý nghĩa

## Việc phải làm, theo thứ tự

Giả định 1 và 3 đã trả lời. Chỉ còn giả định 2, đo được bằng vài lệnh `ssh` khi pod sống.

1. Đo giả định 2: `custom_nodes/` thật trên pod, kèm commit hash từng repo và
   `models/detection/*.onnx`
2. ~~`presignPut` + route `/worker/output-url` + cờ `MOTION_OUTPUT_PRESIGN`~~ — **bỏ**, output
   dưới 100MB nên đường multipart sẵn có là đủ
3. Handler serverless + Dockerfile + job CI build image
4. Network Volume cho model của endpoint, đổ model vào
5. Dispatcher trong api
6. Chạy hết các mục Kiểm chứng

Bỏ được mục 2 kéo theo: không đụng `storage.js`, không thêm route api, không sửa worker. Phần code
mới thu về đúng ba thứ — handler, Dockerfile, dispatcher.
