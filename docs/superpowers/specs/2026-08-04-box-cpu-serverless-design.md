# Box CPU + GPU serverless — Design

**Ngày:** 04/08/2026 · **Trạng thái:** **ĐÃ DỰNG THẬT VÀ CHẠY** — box CPU đầu-cuối OK, nhưng
serverless bị `throttled` nên chưa có job nào chạy qua. Xem [§Đã dựng thật](#da-dung-that).

<a id="da-dung-that"></a>
## ĐÃ DỰNG THẬT — 04/08/2026

Box `cpu5g × 4 vCPU`, **$0,184/giờ**, EU-RO-1, volume `wfe86wzkpm`. Chạy ~2,5 giờ rồi destroy.

| Mục | Kết quả |
|---|---|
| Pod CPU + volume ở EU-RO-1 | ✅ tạo được, mount `/workspace` (MooseFS), model còn nguyên |
| Giá | ✅ **$0,184/giờ** ở 4 vCPU/16 GB · $0,06/giờ ở 2 vCPU/4 GB |
| `setup-cpu-box.sh` | ✅ **~90 giây** (profile GPU ~30 phút — bỏ hẳn phần torch) |
| `npm run build` Nuxt trên 16 GB | ✅ không OOM |
| PM2 | ✅ `api`·`wf-worker`·`minio`·`motions`·`mc-dispatcher`, KHÔNG `worker`/`comfyui`/`task-cloud-auto` |
| `/health` · frontend | ✅ cả hai, `https://app.doanhthuc.xyz` live |
| `mc-dispatcher` | ✅ online, log sạch, cấu hình đúng |
| **Job chạy qua serverless** | ❌ **worker `throttled: 3`** — RunPod hết GPU trống |

**Hai bug lộ ra khi chạy, cả hai đã sửa:**

1. `pod-volume.sh:261` `awk '{print $1*1024}'` → mawk (awk của Ubuntu) in volume 76,8GB thành
   `7.67828e+10`, `$(( ))` chết, bootstrap **abort trước khi cài gì**. Không lộ ở local vì awk của
   macOS in nguyên số. Cùng bug commit `4b6a388` đã sửa ở `preload-models.sh`. Sửa: `%.0f` + `_int()`.
2. Pod CPU có **trần đĩa container = `diskLimitPerVcpu × vCPU`** (`cpu5*` = 15 GB/vCPU → 60 GB).
   `DISK=100` của đường GPU đụng trần → REST trả 500. `pod-provision.sh` nay hỏi API rồi tự hạ.

**Kinh tế cập nhật theo giá thật $0,184** (không phải $0,10 đã giả định): ngưỡng đảo chiều
**79 job/ngày** (≈51% GPU bận) thay vì 87. Box 24/7 = $132/tháng; 20 job/ngày = **$282/tháng** so
với **$720** của pod GPU 24/7.

**Blocker còn lại, ngoài tầm kiểm soát:** giao của `gpuTypeIds=[5090]` × `minCudaVersion=13.0` ×
EU-RO-1 (volume ghim cứng) đang **rỗng** cho serverless. Ba đường thoát chưa thử ở
[docs/gpu-pod.md#serverless-throttled](../../gpu-pod.md#serverless-throttled). Đây là rủi ro cố hữu:
**serverless không có SLA về chỗ trống**; worker local trên pod GPU không có vấn đề này.

## Mục tiêu

Bỏ GPU khỏi cái máy luôn bật. `api`, Postgres, MinIO và frontend đều là việc CPU — thuê RTX 5090
để chạy chúng là trả tiền cho một con GPU nằm không 24/7. GPU chỉ xuất hiện khi có job, qua RunPod
Serverless, và tắt hẳn khi không.

Đây là bước mà [spec hướng A](2026-08-01-serverless-huong-a-design.md) cố ý hoãn: *"dời
api/Postgres/MinIO sang VPS — bước riêng, sau khi serverless chứng minh chạy được"*. Serverless đã
chứng minh 02/08/2026 (5 job, không job nào lỗi). Nên bước này đang mở.

## Vì sao bây giờ, bằng số thật

Hoá đơn thật (`runpodctl billing`), không phải `currentSpendPerHr`:

| Khoản | Đo được |
|---|---|
| 9 dòng hoá đơn pod, 24/07 → 07/08 | **$12,62** cho 12,70 giờ — riêng RTX 5090 $1,004/giờ (cập nhật 08/08; lúc viết spec: $11,79 / 11,9 giờ tới 02/08) |
| Pod GPU nếu bật 24/7 | **~$720/tháng** |
| Serverless, cả ngày chạy thử 02/08 | **$0,3894** (884 giây được tính) |
| Volume 100GB | ~**$7,10/tháng**, không tránh được ở mọi hình dạng |
| Pod CPU | **$0,184/giờ** ở `cpu5g × 4 vCPU`/16 GB · **$0,06/giờ** ở 2 vCPU/4 GB (đo 04/08) |

Ngày 02/08 trả **cả hai**: $1,49 pod GPU **và** $0,39 serverless, cùng một ngày, cho cùng một khối
công việc. Đó là chỗ tiền chảy ra hai lần — và nó không phải tính chất của serverless, mà là hệ quả
của việc cái box luôn bật có GPU.

**Một hệ quả mới rút ra từ hoá đơn**, chưa có trong spec hướng A: chi phí serverless bị chi phối bởi
**số lần đánh thức**, không phải độ dài job. 884 giây được tính so với 25,3 giây execution — phần
lớn tiền đi vào cold start và khoảng chờ idle-timeout. Nên `DISPATCH_COOLDOWN_SEC`, `idle timeout`
và `DISPATCH_MAX_INFLIGHT` là ba núm **chi phí**, không phải ba núm hiệu năng. Job rải rác đắt hơn
job dồn cục.

## Kiến trúc

```
box CPU (luôn bật)                          RunPod Serverless (0 khi rỗi)
├── Postgres      ← PGDATA container disk    └── container GPU
├── MinIO         ← data trên volume            ├── ComfyUI + 6 node ghim commit
├── api           ← HTTPS qua CF Tunnel         └── worker_runtime → HTTP về api
├── frontend Nuxt                                    ↑ mount cùng Network Volume
└── mc-dispatcher ── POST /run ────────────────────┘   (model 42GB, chỉ đọc)
   KHÔNG có: ComfyUI, worker, task-cloud-auto
```

Không có gì mới về giao thức. Worker serverless đã nói HTTP với api từ 02/08; đổi box từ GPU sang
CPU không chạm vào đường đó. Cái đổi là **box cài ít hơn**, không phải box nói khác đi.

### Một tác dụng phụ tốt: bẫy PGDATA tự hết

`VOLUME_PGDATA=0` là mặc định vì MooseFS chặn `chown` nên Postgres không sống được trên Network
Volume ([docs/gpu-pod.md#runpod-gotchas §2](../../gpu-pod.md#runpod-gotchas)). Hệ quả hiện nay: DB
**mất** khi `gpu-destroy`.

Ở hình dạng CPU, hệ quả đó biến mất — không phải vì ta sửa được MooseFS, mà vì **không còn lý do
destroy box**. Bẫy này tồn tại chỉ vì pod GPU $1/giờ buộc phải destroy giữa các lần dùng. Box CPU
rẻ thì cứ để chạy, container disk còn nguyên.

## Phải đo trước — cả ba đều rẻ, và mục 1 có thể chặn cả hướng

> **Mục 1 và 2: ĐÃ TRẢ LỜI 04/08/2026** — có, pod CPU tồn tại ở EU-RO-1 và mount được volume; giá
> $0,184/giờ ở 4 vCPU. Xem [§Đã dựng thật](#da-dung-that). Giữ nguyên chữ dưới đây làm hồ sơ vì nó
> ghi cách đo. **Mục 3 vẫn mở**, và nay có thêm một blocker mới không ai lường: worker serverless
> `throttled`.

1. **Pod CPU có tồn tại ở EU-RO-1 không, và mount được volume không.**
   Volume `wfe86wzkpm` nằm EU-RO-1 và **không dời được**. Không có pod CPU ở đó thì MinIO không có
   nơi lưu và cả hướng này phải đổi (dùng VPS ngoài + S3 khác, phạm vi lớn hơn nhiều).
   `runpodctl datacenter list` **chỉ báo GPU**, không nói gì về CPU — nên phải thử thật:
   ```bash
   runpodctl pod create --name cpu-probe --compute-type cpu \
     --image runpod/base:1.0.2-ubuntu2204 --data-center-ids EU-RO-1 \
     --network-volume-id wfe86wzkpm --container-disk-in-gb 20 --ssh
   # rồi: ssh vào, `df -h /workspace`, `ls /workspace`, xong `runpodctl pod delete`
   ```
   Bằng chứng gián tiếp là bước preload 02/08 đã làm đúng việc này — nhưng xem mục 2, bằng chứng đó
   không đứng vững.

2. **Giá pod CPU.** Không có đường nào lấy bằng lệnh: `runpodctl` không có `cpu list`; REST `/v1`
   không có endpoint giá (đã đọc `openapi.json`, 23 path, không path nào về giá); GraphQL
   `cpuFlavors` trả spec (6 flavor `cpu3c/g/m` · `cpu5c/g/m`, 2–32 vCPU, ram ×2/×4/×8) nhưng
   **không có field giá** qua ~14 lần dò tên. Phải đọc dashboard, hoặc thuê một cái ở mục 1 rồi xem
   `runpodctl billing pods` hôm sau.

   Kéo theo: con số *"pod CPU $0,06/giờ, 9 phút, ~$0,08"* trong
   [docs/gpu-pod.md#preload](../../gpu-pod.md#preload) là **chưa kiểm chứng**. Nó tự mâu thuẫn
   (0,15 giờ × $0,06 = $0,009, không phải $0,08) và **không có dòng pod CPU nào** trong cả 7 dòng
   `billing pods` từ 24/07 đến 02/08. Mục 1 sẽ trả lời luôn cả câu này.

   **Đã loại một lối thoát:** giá rẻ **không** đến từ Community Cloud. RTX 5090 community $0,69/giờ
   so với secure $0,99 nhìn như giảm 30%, nhưng đo 04/08/2026 thì Community Cloud **không tồn tại ở
   bất kỳ datacenter nào có Network Volume** — 5 GPU thử ở EU-RO-1 và A4000 thử ở 4 DC khác có
   `storageSupport=true`, cả 9 lần đều `no instances available`, trong khi cùng lệnh đó bỏ ràng buộc
   DC thì tạo được pod ngay. Volume không dời DC được, nên: hoặc volume, hoặc $0,69 — không cả hai.
   Chi tiết: [docs/gpu-pod.md#community-cloud](../../gpu-pod.md#community-cloud).

3. **Chi phí serverless thật khi dùng hằng ngày.** $0,3894 là của một ngày chạy thử 5 job dồn cục.
   Nó không nói được gì về ngày có 30 job rải rác — mà theo §Vì sao bây giờ thì đó mới là hình dạng
   đắt. Cần một tuần dùng thật rồi đọc `billing serverless`.

<a id="job-9-phut"></a>
## Độ dài một job thật: 8-10 phút — và nó đảo kết luận

Thuc quan sát 04/08/2026: một job **motion + enhance, clip 15-20s, mất 8-10 phút**. Đây là
wall-clock nhìn từ UI (gồm chờ hàng đợi, tải input, upload output), **không phải** giây GPU được
tính tiền — nhưng nó nhất quán với hằng số đo trong code, nên dùng được để tính:

| | |
|---|---|
| Enhance 240 frame × 0,85 s/frame (`worker_runtime/linux.py:850`, đo thật trên 5090) | 3,4 phút |
| Phần còn lại cho motion, nếu tổng là 8-10 phút | 4,6-6,6 phút |

Hai nguồn độc lập khớp nhau, nên lấy **9 phút/job** làm mốc tính.

### Hệ quả: serverless thắng hay thua phụ thuộc CÁCH DÙNG, không phụ thuộc số job

Đơn giá **all-in** serverless $1,586/giờ so với pod $1,004/giờ — serverless đắt hơn **1,32–1,58×**
cho mỗi giây GPU, tuỳ cách tách `diskSpaceBilledGB` ra khỏi `amount` (cả hai dòng hoá đơn đều gộp
tiền đĩa; tiền đĩa áp cho cả hai nên phần lớn triệt tiêu — [bảng độ
bền](../../gpu-pod.md#premium-serverless)). Mỏ neo: hoá đơn pod all-in $1,004 khớp giá niêm yết
$0,99. Bảng dưới dùng **all-in cho cả hai** ($1,586 và $1,00) — cùng một cách tính, không thiên vị
bên nào. Trừ tiền đĩa ở cả hai thì hai cột cùng co lại và ngưỡng đảo chiều nhích từ 87 lên 89
job/ngày; **không kết luận nào đổi.**

Serverless không rẻ hơn về đơn giá; nó chỉ tính $0 khi rỗi — bạn đang mua **quyền có 0 worker**.
Nên phép so thật là: *bạn có đang trả tiền cho thời gian rỗi hay không.*

**Kiểu B — bật khi làm việc, job chạy nối nhau** (kiểu đang dùng: 12,70 giờ trong 15 ngày, 24/07 → 07/08):

| Giờ/ngày | Job/ngày | Pod GPU | Box CPU + serverless (c=$0,10) |
|---|---|---|---|
| 2h | 13 | **$60/tháng** | $169 |
| 4h | 26 | **$120/tháng** | $266 |
| 8h | 53 | **$240/tháng** | $467 |

**Pod GPU thắng đậm.** GPU bận gần 100% suốt số giờ đã trả, nên không có thời gian rỗi nào để
serverless tiết kiệm — chỉ còn phần đắt thêm 59%.

**Kiểu A — app luôn truy cập được** (người dùng thật, box bật 24/7):

| Job/ngày | GPU bận | Pod GPU 24/7 | Box CPU + serverless |
|---|---|---|---|
| 20 | 12% | $720 | **$221** |
| 60 | 38% | $720 | **$519** |
| 110 | 69% | **$720** | $892 |

Ngưỡng đảo chiều **79 job/ngày** (≈51% GPU bận) — tính với giá box CPU **thật $0,184/giờ** đo
04/08/2026. Bảng trên dùng c=$0,10 (giả định cũ) nên cho 87; con số đúng là 79.

> **Vì thế spec này chỉ đáng làm nếu đích đến là kiểu A**: app mở cho người ngoài 24/7, tải dưới
> ~79 job/ngày. Nếu cách dùng vẫn là bật-tắt theo phiên làm việc thì hình dạng đúng là **pod GPU +
> `WORKER_SOURCE=local`**, và hướng box CPU nên để đó chờ. Đây là ràng buộc quan trọng nhất của
> spec, và nó không lộ ra từ bất kỳ số nào trong hoá đơn — chỉ lộ ra khi hỏi *"GPU có rỗi không?"*.

**Không có ba số này thì phép so "CPU + serverless rẻ hơn GPU pod" là niềm tin, không phải kết
luận.** Hướng này *rất có thể* rẻ hơn — $720/tháng là mốc rất cao để vượt — nhưng spec không ghi
con số nó chưa đo.

## Phải sửa những gì

| File | Sửa gì | Vì sao |
|---|---|---|
| `scripts/pod-provision.sh` | thêm `COMPUTE_TYPE=gpu\|cpu`; khi `cpu` thì bỏ `--gpu-id`/`--gpu-count`/`--min-cuda-version`, thêm `--compute-type cpu` | hiện tại **luôn** tạo pod GPU; pod CPU hôm preload là lệnh gõ tay trong docs |
| `scripts/gpu-preflight.sh` | in `COMPUTE_TYPE` trong khối Hình dạng deploy; chặn `cpu` + `WORKER_SOURCE=local` | box CPU mà worker local nghĩa là **không ai chạy được job nào** — phải chặn, không phải cảnh báo |
| `motions-studio/setup/` | profile mới `setup-cpu-box.sh`: `PM2_APPS="minio,api,wf-worker"`, `SKIP_COMFY=1` | bỏ `comfyui`, `worker`, `task-cloud-auto` |
| `.env` / `.env.example` | `COMPUTE_TYPE=cpu` · `WORKER_SOURCE=serverless` · `SETUP_PROFILE=cpu-box` | |
| `docs/gpu-pod.md` | mục hình dạng thứ ba | |

### Ba chỗ hỏng lặng lẽ nếu bỏ qua

1. **`task-cloud-auto` throw `"COMFY_URL chưa cấu hình"`** (`task-cloud/auto-worker.js:100`, đã ghi
   ở spec hướng A §Quyết định). Nó nằm trong `PM2_APPS` của `setup-motion-transfer.sh`. Không bỏ nó
   khỏi danh sách thì PM2 có một app crash-loop vĩnh viễn trên box mới.

2. **`phase_comfyui` không chết khi thiếu GPU — nó *cảnh báo*** (`lib-feature.sh:582`: *"Không có
   GPU NVIDIA → bỏ ComfyUI. Set COMFY_URL trong .env trỏ box GPU khác"*). Nghe như tin tốt, và đúng
   là tin tốt: setup chạy được trên box CPU không cần sửa gì. Nhưng nó cũng nghĩa là **quên
   `COMPUTE_TYPE` sẽ không có ai báo** — box dựng xong, xanh hết, `/health` trả lời, và không job nào
   chạy được. Đó là lý do cổng chặn ở bảng trên là *chặn*, không phải *cảnh báo*.

3. **MinIO từ chối symlink làm drive** ([§3](../../gpu-pod.md#runpod-gotchas)). Ràng buộc này
   **không** đổi ở box CPU — vẫn phải theo đúng cách hiện tại đang làm.

## Cố tình KHÔNG làm

- **Dời khỏi RunPod hẳn** (Hetzner/VPS thường, rẻ hơn nữa). Volume EU-RO-1 giữ 42GB model *và* dữ
  liệu MinIO; dời box ra ngoài RunPod nghĩa là serverless vẫn mount volume để đọc model, nhưng MinIO
  phải chuyển sang chỗ khác — một bước riêng, sau khi hình dạng này chạy được.
- Autoscale, ngân sách trần theo ngày, nhiều endpoint theo job type. Như spec hướng A.
- Bỏ `WORKER_SOURCE=local`. Nó vẫn là hình dạng đúng khi ai đó *muốn* thuê pod GPU (job dồn cục, cần
  không có cold start). Hai hình dạng cùng tồn tại.

## Kiểm chứng

1. `runpodctl billing pods` cho box CPU sau 24 giờ → số $/giờ thật, ghi vào docs
2. Job `motion` chạy hết qua serverless với box **không có GPU** — `nvidia-smi` trên box phải
   *không tồn tại*, và job vẫn `done`
3. `pm2 status` trên box: không app nào ở `errored`/restart-loop (đây là bài kiểm cho
   `task-cloud-auto`)
4. `gpu-preflight` chặn đúng cặp `COMPUTE_TYPE=cpu` + `WORKER_SOURCE=local`
5. Reboot box → Postgres và MinIO tự lên, DB còn nguyên
6. Một tuần dùng thật → `billing serverless` + `billing pods`, so với $720/tháng của hình dạng cũ.
   **Đây là mục quyết định hướng này có thắng hay không**, và là mục duy nhất không rẻ để chạy.
