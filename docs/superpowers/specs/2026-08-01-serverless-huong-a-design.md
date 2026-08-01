# RunPod Serverless cho stack tự chủ (hướng A) — thiết kế

Ngày: 2026-08-01
Trạng thái: **thiết kế, chưa triển khai.** Ba giả định ở §8 phải đo trên pod thật trước khi viết code.

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

Serverless là **nguồn worker thứ hai**, không thay pod:

```
                    ┌──────────────────────────────────────────┐
  FE tạo job ──────▶│ api + Postgres + MinIO   (luôn bật)      │
                    │  jobs: queued → processing → done        │
                    └────────┬────────────────────┬────────────┘
                             │                    │
              worker local (pod, PM2)    dispatcher → RunPod Serverless
                    poll /worker/claim           POST /v2/<id>/run
                             │                    │
                             └──── cùng một giao thức ────┘
```

Muốn chuyển hẳn sang "không task = không tốn tiền" về sau: `pm2 stop worker` và dời
api/Postgres/MinIO sang box không GPU. Không phải viết lại gì — đó là lý do thiết kế theo hình
này thay vì thay thế thẳng.

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

## Quyết định 3 — presigned PUT cho output lớn

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

## Quyết định 4 — image và model

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

## Cố tình KHÔNG làm

Ngân sách trần theo ngày · fallback tự động khi serverless lỗi · autoscale theo độ dài hàng đợi ·
nhiều endpoint tách theo job type · concurrency > 1 trong một worker.

Chưa có số liệu tải thật thì mọi thứ trên là đoán. Job lỗi cứ trả về `queued`, worker local nhặt —
đủ dùng, và là hành vi sẵn có chứ không phải code mới.

<a id="assumptions"></a>
## Ba giả định chưa kiểm chứng

Đây là lý do spec này chưa chuyển thành plan. Cả ba chỉ đo được sau khi pod thật chạy một job thật.

1. **Output thật nặng bao nhiêu.** Nếu đa số dưới 100MB thì §Quyết định 3 hoãn được, bỏ luôn ràng
   buộc VPS — tiết kiệm hẳn một mảng việc. Đo: chạy vài job đại diện, xem kích thước mp4.
2. **`custom_nodes/` trên pod thật gồm những gì.** Quyết định nội dung image. Đo:
   `ssh pod 'ls ~/comfyui/custom_nodes && git -C ... rev-parse HEAD'` cho từng node, cộng
   `models/detection/*.onnx`.
3. **Worker code có chạy được trong container serverless không.** Nó viết cho tiến trình dài hạn:
   có vòng lặp, heartbeat, và trạng thái toàn cục. Cần đọc `worker_runtime/runner.py` xem tách ra
   một nhịp claim-and-run có sạch không, hay phải bọc.

Nếu giả định 3 sai — worker code không tách nhịp được — thiết kế phải quay lại kiểu monkeypatch
như `rp_handler.py`, và §Quyết định 1 mất phần lớn giá trị. Đây là rủi ro lớn nhất của spec này.

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
3. Một job có output >100MB đi qua đường presigned PUT thành công — **chỉ kiểm được ở hình dạng
   VPS**; ở hình dạng "giữ pod" thì thay bằng: job >100MB fail với thông báo rõ ràng, không im lặng
   cắt file
4. Tắt `pm2 stop worker` trên pod, job vẫn chạy — chứng minh serverless tự đứng được
5. Không có job nào trong 10 phút → `runpodctl` báo 0 worker đang chạy, hoá đơn không tăng
6. Hai job cùng lúc → hai worker, không job nào bị nhận hai lần

## Việc phải làm, theo thứ tự

Chỉ bắt đầu sau khi §Ba giả định được đo.

1. Đo ba giả định trên pod thật
2. `presignPut` trong `storage.js` + route `GET /worker/output-url` + nhánh worker sau cờ
   `MOTION_OUTPUT_PRESIGN`
3. Handler serverless + Dockerfile + job CI build image
4. Network Volume cho model của endpoint, đổ model vào
5. Dispatcher trong api
6. Chạy hết 6 mục Kiểm chứng
