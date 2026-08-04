# GPU pod cho backend motion-transfer (motions-studio)

Frontend (`motions`) mặc định chạy local (`make dev`); muốn nó chạy luôn trên pod để người khác
mở được thì xem [Frontend on the pod](#frontend-on-the-pod). Backend (`motions-studio`) cần GPU NVIDIA
(≥24GB VRAM cho Wan 2.2 Animate) nên chạy trên máy thuê theo giờ — vast.ai hoặc RunPod — thay vì
máy Mac của bạn. Toàn bộ flow dưới đây tự động hoá đúng những bước thủ công đã làm ở lần chạy
trước (SSH vào máy thuê, chạy script setup của profile trong `motions-studio/setup/`, dán `.env` in ra
vào `motions/.env`).

## Runbook

Bốn giai đoạn. Giai đoạn 0 làm một lần trong đời; giai đoạn 1 làm mỗi lần dựng pod mới; giai
đoạn 2 là dùng hằng ngày; giai đoạn 3 là dọn.

Chi tiết từng khái niệm nằm ở các mục sau trong file này — runbook chỉ nói làm gì, theo thứ tự nào.

---

### Giai đoạn 0 — một lần duy nhất

Chỉ làm lại khi đổi máy dev, đổi tài khoản RunPod, hoặc đổi domain.

**0.1 · CLI + API key**

```bash
brew install runpod/runpodctl/runpodctl    # cần >= 2.8, xem #runpod
runpodctl doctor                            # hỏi API key, lấy ở runpod.io/console/user/settings
```

**0.2 · `.env`**

```bash
cp .env.example .env
```

Điền: `DOMAIN` (backend, vd `api.you.xyz`), `FE_DOMAIN` (frontend, vd `app.you.xyz`),
`CORS_ORIGINS=https://$FE_DOMAIN,http://localhost:2030`, `SUPER_ADMIN`, `GMAIL_USER` +
`GMAIL_APP_PASSWORD`, `CF_API_TOKEN`. Xem [Cloudflare API Token](#cloudflare-api-token) cho 3
quyền bắt buộc.

**0.3 · Network Volume** — [chi tiết](#network-volume)

**Datacenter** không sửa được sau khi tạo — chọn nơi còn stock GPU bạn thuê. **Dung lượng** thì
nới lên được (`runpodctl network-volume update <id> --size <GB>`, chỉ tăng, không giảm), nên
đừng mua dư ngay từ đầu: nó tính tiền hằng tháng kể cả khi không có pod nào.

```bash
runpodctl gpu list -o json | python3 -c 'import sys,json
for g in json.load(sys.stdin):
    if "5090" in g.get("gpuId",""):
        print(g["gpuId"], "$%s/hr" % g.get("securePricePerHr"))
        for d in g.get("dataCenterAvailability") or []: print("  ", d["dataCenterId"], d["stockStatus"])'

runpodctl network-volume create --name motion --size 100 --data-center-id <DC>
```

Volume tính tiền **hàng tháng kể cả khi không có pod nào** — `make gpu-destroy` không tắt được
đồng hồ đó, và không nên tắt: đó chính là thứ giữ 33GB model và database.

**0.4 · Đổ model vào volume bằng pod CPU** — [chi tiết](#preload)

Bước này **tuỳ chọn nhưng nên làm**, và cũng chỉ một lần cho mỗi volume. Tải model không cần GPU;
làm nó trên pod CPU $0.06/giờ thay vì pod GPU $1.014/giờ, và pod GPU sau đó dựng lên là có model
sẵn thay vì bạn ngồi chờ tải. Đo thật 02/08/2026: 38,9 GB trong 9 phút, tốn $0.08.

```bash
runpodctl pod create --name preload --compute-type CPU \
  --image runpod/base:1.0.2-ubuntu2204 --data-center-ids <DC> \
  --network-volume-id <vol-id> --container-disk-in-gb 20 --ssh
# rồi trên pod: ./setup/preload-models.sh --list  →  chọn nhóm  →  tải  →  runpodctl pod delete
```

Bỏ qua bước này cũng được — bước 7 dưới đây tải qua UI, chỉ là đắt hơn và phải chờ.

---

### Giai đoạn 1 — dựng pod mới

Từ đây trở đi đồng hồ chạy. Toàn bộ giai đoạn này mất ~30-40 phút, phần lớn là chờ.

```bash
make gpu-preflight                          # 1. chặn mọi lỗi cấu hình TRƯỚC khi tốn tiền
bash scripts/pod-provision.sh               # 2. dry-run — ĐỌC lệnh pod create nó in ra
CONFIRM=yes bash scripts/pod-provision.sh   # 3. thuê thật; tự ghi GPU_INSTANCE_ID vào .env
make gpu-wait                               # 4. chờ SSH; tự ghi GPU_SSH_HOST/PORT vào .env
make gpu-bootstrap                          # 5. ~30 phút, xem bên dưới nó làm gì
make gpu-volume-check                       # 6. chứng minh volume thật sự đang được dùng
```

Bước 5 làm, theo đúng thứ tự này (thứ tự có lý do — xem `scripts/pod-bootstrap.sh`):

1. rsync `motions-studio/` lên pod
2. symlink `models` + `PGDATA` + `MinIO` sang volume — **trước** khi setup chạy, nếu không
   Postgres dựng cluster trên container disk rồi mất sạch khi destroy pod
3. `setup-<SETUP_PROFILE>.sh`: Postgres, MinIO, ComfyUI, PyTorch khớp driver ([CUDA 13](#cuda-13)),
   custom nodes, Cloudflare Tunnel với **2 hostname** (`DOMAIN`→:8080, `FE_DOMAIN`→:2030)
4. ghi `motions/.env` **local** trỏ vào backend mới
5. rsync + `npm install` + `npm run build` + PM2 cho frontend **trên pod**
   ([chi tiết](#frontend-on-the-pod))

**7 · Tải model** — không tự động, cố ý ([lý do](#no-auto-models)). Chỉ cần làm
**một lần cho mỗi volume**, pod sau không phải tải lại:

`https://$FE_DOMAIN` → login bằng `SUPER_ADMIN` (bấm gửi OTP) → **Settings → Models AI** → nhóm
**Wan 2.2 Animate** → **Cài cả nhóm** (~33GB).

> **Bỏ qua được bước này** nếu đã làm [0.4 · đổ model bằng pod CPU](#preload) — Models AI sẽ hiện
> "đã cài" sẵn. Làm ở đây nghĩa là pod GPU $1.014/giờ nằm chờ suốt lúc tải; ở 0.4 là $0.06/giờ.
> Với `SETUP_PROFILE=full` thì catalog mở hết 245GB, càng không nên tải trên đồng hồ GPU.

**8 · Kiểm chứng thật** — [`make gpu-smoke`](#smoke). Đừng tin
màu xanh, chạy một job thật.

---

### Giai đoạn 2 — dùng hằng ngày

| Việc | Lệnh |
|---|---|
| Bật pod đã có | `make gpu-up` |
| **Tắt pod khi xong** | `make gpu-down` |
| Backend còn sống không | `make gpu-status` |
| Xem log | `make gpu-logs` · `LOG=worker make gpu-logs` |
| Sửa code FE → đẩy lên pod | `make gpu-fe` (~2 phút) |
| Sửa code BE → đẩy lên pod | `make gpu-bootstrap` (idempotent, chạy lại an toàn) |
| Code FE ở máy mình | `make dev` → `localhost:2030` |

`make gpu-down` **không** xoá pod: container disk vẫn tính tiền theo giờ suốt thời gian pod tồn
tại. Nghỉ vài ngày thì `make gpu-destroy` rẻ hơn — volume giữ lại model và database.

---

### Giai đoạn 3 — dọn

```bash
make gpu-destroy    # xoá pod, verify nó chết thật, rồi nhắc dọn .env
```

Rồi xoá tay 3 dòng trong `.env`: `GPU_INSTANCE_ID`, `GPU_SSH_HOST`, `GPU_SSH_PORT`.

Cái **không** bị xoá, và không nên xoá: Network Volume (giữ model + database, vẫn tính tiền
tháng), Cloudflare Tunnel + DNS (miễn phí, lần dựng sau tái dùng đúng tunnel đó vì tên tunnel
suy ra từ `DOMAIN`).

Lần dựng pod tiếp theo: quay lại **giai đoạn 1**, bỏ qua bước 7 (model đã nằm trên volume).

---

### Hỏng ở đâu thì đọc gì

| Triệu chứng | Nguyên nhân thường gặp | Chỗ đọc |
|---|---|---|
| `gpu-preflight` báo đỏ | thiếu key/volume/CORS | thông báo tự nói cách sửa |
| `pod create` báo hết máy | `MIN_CUDA_VERSION=13.0` lọc quá chặt | hạ xuống `12.8`, [CUDA 13](#cuda-13) |
| Setup trên vast mất 1-2 tiếng | bốc trúng máy đĩa chậm | [Vì sao vast chậm](#vast-slow) |
| `gpu-wait` hết giờ | pod chưa mở cổng 22, hoặc host chết | thông báo timeout in sẵn lệnh chẩn đoán |
| Console lặp `start container ...: begin` | image không phải `runpod/*` → crash loop | [Ba cái bẫy của RunPod](#runpod-gotchas) §1 |
| `pod-volume.sh` chết ở `rsync PGDATA` | MooseFS chặn `chown` | [§2](#runpod-gotchas) — `VOLUME_PGDATA=0` |
| `minio` restart mãi, `pm2 ls` hiện `waiting` | MinIO từ chối symlink làm drive | [§3](#runpod-gotchas) |
| DB trống sau khi dựng lại pod | PGDATA nằm trên container disk, không trên volume | [§2](#runpod-gotchas) — đánh đổi đã biết |
| `https://$DOMAIN` không trả lời | cloudflared không chạy (pod không có systemd) | [systemd / cloudflared](#systemd-cloudflared) |
| App load được, mọi API call chết CORS | `CORS_ORIGINS` thiếu `https://$FE_DOMAIN` | [Frontend on the pod](#frontend-on-the-pod) |
| Pod mới lại tải 33GB model | volume không được mount thật | `make gpu-volume-check`, [Kiểm chứng](#volume-check) |
| Job chạy chậm bất thường | rơi xuống nhánh cu128 | [CUDA 13](#cuda-13) |
| `https://$FE_DOMAIN` trả 404 | tunnel chưa có ingress cho FE | chạy `make gpu-bootstrap` một lần |

## Frontend on the pod

Mặc định FE chạy local, nghĩa là **tắt máy bạn là không ai vào được app**. Muốn đưa cho người
dùng thật thì cho FE chạy luôn trên pod: điền `FE_DOMAIN` (và `FE_PORT`, mặc định 2030) vào
`.env`, thêm `https://<FE_DOMAIN>` vào `CORS_ORIGINS`, rồi `make gpu-bootstrap`.

```
                    ┌─ Cloudflare Tunnel (1 tunnel, 2 hostname)
Browser end-user ───┤
                    ├─ app.yourdomain.com  →  pod localhost:2030   PM2 "motions"
                    └─ api.yourdomain.com  →  pod localhost:8080   PM2 "api"

Máy bạn: make dev (localhost:2030) → https://api.yourdomain.com   [CORS đã whitelist]
```

Cả hai vẫn chạy song song: pod phục vụ người dùng, `make dev` để bạn code.

| Lệnh | Làm gì | Mất bao lâu |
|---|---|---|
| `make gpu-bootstrap` | backend + tunnel 2 hostname + frontend | lần đầu ~30 phút |
| `make gpu-fe` | CHỈ frontend: rsync + build + PM2 restart | ~2 phút |

Sửa code FE xong thì `make gpu-fe`, không cần bootstrap lại.

**`FE_DOMAIN` cần `CF_API_TOKEN`, không dùng được với `CF_TUNNEL_TOKEN`.** Đường tunnel-token
không tạo được Public Hostname thứ hai qua API — phải tự thêm `FE_DOMAIN → localhost:2030` trên
dashboard Cloudflare.

Vài điểm dễ vấp:

- **`CORS_ORIGINS` thiếu `https://<FE_DOMAIN>`** là lỗi tệ nhất: FE load bình thường, mọi API call
  từ browser chết CORS, trông y hệt backend hỏng. `make gpu-preflight` chặn sẵn.
- Trên pod, `motions/.env` dùng `NUXT_MOTION_API_URL=http://127.0.0.1:8080` (server-side proxy đi
  loopback, cùng máy) nhưng `NUXT_PUBLIC_MOTION_BACKEND_URL=https://<DOMAIN>` (chạy trong browser
  người dùng nên bắt buộc URL public). File này do `scripts/pod-fe.sh` sinh, **ghi đè mỗi lần
  deploy** — đừng sửa tay. `API_KEY` được đọc từ `~/motion-backend/.env` ngay trên pod, không bao
  giờ đi qua máy bạn.
- **Build chạy trên pod, không build local rồi copy `.output/`.** `@nuxt/image` kéo `sharp`, và
  `node_modules/@img/` trên máy Mac là `sharp-darwin-arm64` — copy sang pod Linux thì build xong
  vẫn chết lúc chạy.
- Đổi `DOMAIN` = tunnel mới. Tên tunnel là `motion-${DOMAIN//./-}`, nên đổi domain xong bootstrap
  lại sẽ tạo tunnel khác và bỏ lại tunnel cũ trong account Cloudflare — xoá tay nếu muốn gọn.
- `make gpu-fe` giả định ingress đã có sẵn. Nếu nó báo HTTP 404 thì đó là catch-all của tunnel,
  nghĩa là chưa có ingress rule → chạy `make gpu-bootstrap` một lần.

## Cloudflare API Token

Máy thuê ở vast.ai/RunPod đều NAT (không IP public cố định) → dùng Cloudflare Tunnel thay vì mở
port 80/443. Cần 1 token tại **dash.cloudflare.com/profile/api-tokens** → *Create Custom Token*
với ĐỦ CẢ 3 quyền (thiếu 1 là tunnel fail):

| # | Permission | Dùng để |
|---|---|---|
| 1 | Account · Cloudflare Tunnel · Edit | tạo tunnel + ingress |
| 2 | Zone · DNS · Edit | tạo DNS CNAME proxied |
| 3 | Zone · Zone · Read | tìm zone theo domain |

Account Resources → tài khoản chứa domain. Zone Resources → Specific zone → domain gốc (vd
`yourdomain.com`). `DOMAIN` trong `.env` phải là subdomain của domain đó (vd
`motion-transfer.yourdomain.com`).

Token dùng lại được cho nhiều lần bootstrap/nhiều pod.

## CUDA 13

`lib-gpu.sh` → `motion_install_best_pytorch()` chọn wheel torch theo **driver mà `nvidia-smi` báo**,
không theo image gốc của pod:

| GPU / driver | Kết quả |
|---|---|
| compute cap ≥ 7.5 **và driver ≥ R580** | `cu130` + torch 2.12.1 — CUDA 13.0, đích nhắm |
| Blackwell (RTX 50xx, cc ≥ 10) nhưng driver < R580 | `cu128` + torch 2.11.0, in warn bảo nâng driver rồi chạy lại setup |
| còn lại (Maxwell/Pascal/Volta…) | `cu126` — CUDA 13 đã loại các đời này |

Image gốc không quyết định vì ba lẽ: ComfyUI dùng venv riêng (`python3 -m venv`, KHÔNG
`--system-site-packages`) nên torch của image không nhìn thấy được; torch wheel bundle sẵn CUDA
runtime của nó; và `sageattention` cài từ PyPI dạng wheel chứ không compile `nvcc`.

Điều image ảnh hưởng là **host bạn được xếp lên**. Vì vậy `POD_IMAGE` mặc định pin
`pytorch/pytorch:2.12.1-cuda13.0-cudnn9-devel` — CUDA khai báo khớp với nhánh cu130. Trên trang
deploy RunPod, đặt luôn filter **CUDA Version = 13.0** cho ăn khớp.

Muốn ép khác đi: `MOTION_PYTORCH_CHANNEL=cu128 MOTION_PYTORCH_VERSION=2.11.0` (lib-gpu.sh tôn
trọng pin có chủ đích). Kiểm sau khi dựng:

```bash
ssh pod '~/comfyui/venv/bin/python -c "import torch;print(torch.__version__, torch.version.cuda)"'
```

## Kiến trúc thật trên pod — KHÔNG cần Docker

`setup-motion-transfer.sh` cài mọi thứ NATIVE (apt + PM2), không qua `docker compose` — Postgres
(apt package + `pg_ctlcluster`) và MinIO (tải thẳng binary `/usr/local/bin/minio`) đều chạy trực
tiếp trên pod, y hệt ComfyUI/worker. Docker chỉ dùng ở **kiến trúc lai macOS** (mục 🍎 trong
`motions-studio/README.md`) hoặc nhánh `setup/setup.sh` (docker-compose đầy đủ) — KHÔNG áp dụng
cho `setup-motion-transfer.sh`. Vì vậy `pod-provision.sh` chỉ cần image CUDA có `apt`/`sudo`
(PyTorch của image gốc không quan trọng — script tự cài lại bản khớp driver phát hiện được), không
cần lo image có hỗ trợ nested Docker hay không.

<a id="vast-slow"></a>
## Vì sao setup trên vast lâu gấp 5 lần RunPod

Không phải RunPod nhanh hơn. Là **bộ lọc offer trước đây chọn đúng máy chậm nhất**.

vast là chợ máy của người khác. Đo ngày 01/08/2026 trên 64 offer RTX 5090:

```
disk_bw:   min 395   ·   median 3641   ·   max 12800 MB/s      → chênh 32 lần, cùng con GPU
```

`setup-motion-transfer.sh` gần như toàn I/O và CPU: apt Postgres, giải nén ~3-4GB torch wheel,
clone 6 custom node kèm pip deps của chúng, ComfyUI requirements. Trên đĩa 395 MB/s đó là việc
1-2 tiếng; trên đĩa nhanh là 10-20 phút. Cùng script, cùng GPU, cùng khoảng giá.

Bộ lọc cũ chỉ nhìn `gpu_name`, `disk_space`, `reliability` rồi **sort theo giá rẻ nhất**. Rẻ và
chậm đi đôi với nhau, nên nó chọn trúng máy tệ nhất một cách có hệ thống.

Giờ có `MIN_DISK_BW` (mặc định 3000 MB/s) và `MIN_CPU_GHZ` (2.5). Lần đo gần nhất chúng loại 22
trên 41 offer nằm dưới trần giá, và chọn máy $0.336/hr với đĩa 3487 MB/s thay vì máy rẻ hơn
$0.02 mà chậm gấp mấy lần.

Muốn thuê máy chậm vẫn được — script nói rõ phân bố `disk_bw` hiện có và in sẵn lệnh hạ ngưỡng.

RunPod không cần hai biến này: secure cloud là phần cứng datacenter đồng đều, không có phương sai
kiểu chợ.

<a id="costs"></a>
## Costs — pod dừng vẫn tính tiền

**RunPod, số đo thật 02/08/2026** (`runpodctl user` → `currentSpendPerHr`):

| Trạng thái | $/giờ |
|---|---|
| Pod RTX 5090 đang chạy + volume 100GB | **1,014** |
| Chỉ volume 100GB (pod đã destroy) | **0,0100** |
| Serverless rỗi (kể cả khi `/health` báo `idle=2 ready=2`) | **0** — không cộng thêm gì |

Nghĩa là volume 100GB tốn ~$7,1/tháng và `make gpu-destroy` KHÔNG dừng đồng hồ đó — cố ý, vì nó
đang giữ 42GB model.

<a id="hoa-don-that"></a>
### Hoá đơn thật, không phải số suy ra — sửa 04/08/2026

`currentSpendPerHr` là **tốc độ tiêu tiền tại một thời điểm**, không phải số tiền đã trả. Đối chiếu
với hoá đơn (`runpodctl billing pods` · `billing serverless` · `billing network-volume`):

| Khoản | Hoá đơn thật |
|---|---|
| 7 phiên pod GPU, 24/07 → 02/08 | **$11,79** cho 11,9 giờ (~$1,00/giờ, ổn định) |
| Serverless, cả ngày chạy thử 02/08 | **$0,3894** cho **884 giây** được tính |
| Volume 100GB | $0,00972/giờ → **~$7,10/tháng** |

> **Con số $0,0116 trước đây ở đây là SAI**, và nó là con số chống lưng cho toàn bộ lập luận chi
> phí của đường serverless. Nó tính theo 25,3 giây *execution* của 5 job. RunPod tính **884 giây** —
> gấp 35 lần — vì **cold start và khoảng chờ idle-timeout cũng được tính tiền**, không chỉ lúc GPU
> thật sự chạy. Thực thu $0,3894, tức **33 lần** số đã ghi.
>
> Vẫn rẻ về tuyệt đối, nhưng hệ quả thì đổi: chi phí serverless bị chi phối bởi **số lần đánh
> thức**, không phải bởi độ dài job. Ba job rải rác trong ngày đắt hơn ba job liên tiếp cùng một
> worker ấm. `DISPATCH_COOLDOWN_SEC` và `idle timeout` do đó là hai núm chi phí, không phải hai núm
> hiệu năng.

Ngày 02/08 trả **cả hai**: $1,49 pod GPU **và** $0,39 serverless. Đó là hình dạng "trả tiền GPU hai
lần" — không phải tính chất của serverless, mà vì cái box luôn bật lúc đó là một pod GPU. Xem
[§Hai hình dạng deploy](#deploy-shapes).

<a id="premium-serverless"></a>
### Serverless đắt hơn pod bao nhiêu — và con số này đứng vững tới đâu

Cả hai dòng hoá đơn đều gộp tiền đĩa (`diskSpaceBilledGB`) vào `amount`, nên chia thẳng
`amount / thời gian` ra **giá all-in**, không phải giá GPU. Tiền đĩa áp cho cả hai bên nên nó phần
lớn triệt tiêu, và tỉ lệ bền qua mọi giả định:

| Đơn giá đĩa giả định | Pod $/giờ | Serverless $/giờ | Tỉ lệ |
|---|---|---|---|
| 0 (**all-in — số phòng thủ được nhất**) | 1,003 | 1,586 | **1,58×** |
| $0,0000108/GB-h | 0,990 | 1,559 | 1,57× |
| $0,0000972/GB-h (đơn giá volume đo được) | 0,886 | 1,348 | 1,52× |
| $0,000300/GB-h | 0,640 | 0,853 | 1,33× |

**Serverless đắt hơn pod 1,33–1,58× cho mỗi giây GPU.** Mỏ neo độc lập: hoá đơn pod all-in
**$1,003/giờ** khớp giá niêm yết **$0,99** (`gpuTypes.securePrice` cho RTX 5090) cộng chút đĩa — nên
cách đọc hoá đơn đúng, không phải đọc nhầm cột.

**Vì sao đắt hơn:** bạn mua **quyền có 0 worker**. Nhà cung cấp vẫn phải giữ năng lực sẵn và chịu
rủi ro máy nằm không; phần chênh là tiền trả cho việc đó. Serverless không bán GPU rẻ hơn — nó bán
hoá đơn $0 lúc rỗi. Không rỗi thì không mua được gì. (RunPod có tier rẻ hơn là **Active workers**,
`workersMin > 0`, nhưng bật nó là trả liên tục, tức mất đúng lý do dùng serverless.)

> **Chưa xác minh được:** giá serverless **niêm yết**. Không có trong REST `/v1` (đã đọc
> `openapi.json`), không có trong GraphQL (`gpuTypes` chỉ có `securePrice`/`communityPrice`/spot/
> 1-3-6 tháng), không có trong object endpoint. Toàn bộ số serverless ở trên đến từ **một** dòng hoá
> đơn, 884 giây, một ngày.

<a id="community-cloud"></a>
### Community Cloud rẻ hơn 30% — và không dùng được với Network Volume

| RTX 5090 | $/giờ |
|---|---|
| Secure Cloud | 0,99 |
| Community Cloud | **0,69** |

`scripts/pod-provision.sh` **không bao giờ set `--cloud-type`**, nên nó luôn dùng SECURE. Con số
$0,69 nhìn như giảm 30% miễn phí. Nó không phải.

**Đo 04/08/2026** — Community Cloud **không tồn tại ở datacenter nào có Network Volume**:

| Thử | Kết quả |
|---|---|
| COMMUNITY + volume `wfe86wzkpm`, EU-RO-1 | `no instances available` |
| COMMUNITY, EU-RO-1, **không** volume — 5090 · 4090 · A4000 · A4500 · L4 | cả 5: `no instances available` |
| COMMUNITY, A4000, ở 4 DC khác có `storageSupport=true` (EU-CZ-1 · EU-FR-1 · US-TX-3 · US-IL-1) | cả 4: `no instances available` |
| COMMUNITY, A4000, **không ràng DC** (đối chứng dương) | ✅ **tạo được**, $0,17/giờ |

Community chạy được — chỉ là không ở nơi có network storage. 5 datacenter `storageSupport=true` đã
thử đều không có máy community nào. Volume **không dời được datacenter**, nên với dự án này
Community Cloud là ngõ cụt: hoặc volume, hoặc giá $0,69, không thể cả hai.

Suy luận từ 5 DC + 1 đối chứng dương, **không** từ tài liệu RunPod — nếu sau này cần $0,69 thì thử
lại, có thể họ đã mở community ở DC có storage.

**Giá pod CPU: chưa đo được.** Không có `runpodctl cpu list`; REST `/v1` không có endpoint giá;
GraphQL `cpuFlavors` cho spec (6 flavor: `cpu3c/g/m`, `cpu5c/g/m`, 2–32 vCPU) nhưng **không** có
field giá nào qua ~14 lần dò tên. Phải đọc trên dashboard hoặc thuê một cái rồi xem hoá đơn. Kéo
theo: con số *"pod CPU $0,06/giờ, 9 phút, ~$0,08"* ở [§Tải model vào volume trước](#preload) là
**chưa kiểm chứng** — nó tự mâu thuẫn (0,15 giờ × $0,06 = $0,009, không phải $0,08) và **không có
dòng pod CPU nào** trong cả 7 dòng hoá đơn từ 24/07 đến 02/08.

Kiểm bất cứ lúc nào:

```bash
# Tốc độ tiêu tiền LÚC NÀY (không phải số đã trả)
runpodctl user | python3 -c "import sys,json;d=json.load(sys.stdin);print('balance \$%.3f | spend/hr \$%.4f'%(d['clientBalance'],d['currentSpendPerHr']))"
runpodctl pod list          # [] = không còn pod nào

# Số ĐÃ TRẢ, theo ngày. Dùng cái này khi so chi phí hai hình dạng deploy — spend/hr không
# thấy được cold start lẫn idle-timeout của serverless, và đó là chỗ tiền serverless thật sự đi.
runpodctl billing pods -o json | python3 -c "import sys,json;d=json.load(sys.stdin);[print(r['time'][:10], '\$%.3f'%r['amount'], '%.2fh'%(r['timeBilledMs']/3.6e6)) for r in sorted(d,key=lambda x:x['time'])];print('tổng \$%.2f'%sum(r['amount'] for r in d))"
runpodctl billing serverless -o json | python3 -c "import sys,json;[print(r['time'][:10], r['endpointId'], '\$%.4f'%r['amount'], '%.0fs'%(r['timeBilledMs']/1000)) for r in json.load(sys.stdin)]"
```

Phần dưới là luật của **vast.ai**, giữ lại cho trường hợp `GPU_PROVIDER=vast`:

Vast.ai tính tiền **ổ đĩa theo giờ SUỐT THỜI GIAN INSTANCE TỒN TẠI** — kể cả khi đã `stop`. Không
có chuyện "dừng rồi để đó tuần sau vào tiếp" miễn phí. Quy tắc:

- Xong việc trong ngày → `make gpu-down` (dừng, giữ ổ đĩa — vẫn tính phí ổ đĩa nhỏ mỗi giờ).
- Không quay lại trong 1-2 ngày tới → `make gpu-destroy` (xoá hẳn, dừng tính phí hoàn toàn). Lần
  sau `make gpu-provision` + `make gpu-bootstrap` lại từ đầu — code đồng bộ trong vài giây, phần
  tốn thời gian là cài PyTorch CUDA + tải model (không tránh được, dù giữ pod hay không, vì model
  không nằm trong ổ đĩa gốc trừ khi bạn tự backup).
- Đặt idle auto-stop 15 phút trong Vast UI — lưới an toàn cho đêm quên `make gpu-down`.

Kiểm tra chi phí thật bất kỳ lúc nào:

```bash
vastai show instances --raw | python3 -c "import json,sys;d=json.load(sys.stdin);[print(i['id'], i['cur_state'], '\$%.3f/h storage'%i['storage_total_cost']) for i in d]"
vastai show user --raw | python3 -c "import json,sys;print('credit \$%s'%json.load(sys.stdin)['credit'])"
```

<a id="reusing-an-existing-tunnel-token"></a>
## Reusing an existing tunnel token

Nếu `.env` có `CF_TUNNEL_TOKEN` mà KHÔNG có `CF_API_TOKEN` (vd copy từ một project khác đã có
sẵn tunnel/domain), `setup-motion-transfer.sh` chỉ cài `cloudflared` chạy connector cho **tunnel
CÓ SẴN** đó — nó KHÔNG tự tạo DNS/ingress mới (bước đó chỉ chạy khi có `CF_API_TOKEN`). Nghĩa là:

- Nếu `DOMAIN` trong `.env` là một subdomain MỚI (project khác chưa từng cấu hình), Cloudflare edge
  sẽ trả 404 cho nó cho tới khi bạn **thêm public hostname thủ công**: Cloudflare dashboard →
  Zero Trust → Networks → Tunnels → chọn đúng tunnel → **Public Hostname → Add a public hostname**
  → hostname = `$DOMAIN`, service = `http://localhost:8080` (port API của motion-backend). Không
  cần token mới, chỉ cần đăng nhập dashboard 1 lần.
- Nếu project cũ (chủ token) vẫn còn pod GPU đang chạy dùng chung tunnel này, chạy thêm 1 connector
  nữa (từ pod motion-transfer) có thể tranh route với connector cũ (Cloudflare cho phép nhiều
  replica/tunnel nhưng route theo hostname là DÙNG CHUNG). Kiểm tra trước:
  `vastai show instances --raw` (hoặc dashboard RunPod) — không còn instance nào đang chạy của
  project cũ thì an toàn.
- `make gpu-status` / `pod-bootstrap.sh`'s health check có thể báo "down" dù cloudflared chạy tốt
  — đó là do THIẾU public hostname (bước thủ công ở trên), khác với lỗi "cloudflared không khởi
  động được" (mục systemd/cloudflared bên dưới). Phân biệt bằng: `ssh ... pgrep -f cloudflared`
  (có tiến trình = cloudflared ổn, vấn đề nằm ở ingress chưa map hostname).

## RunPod

Cần `runpodctl` **≥ 2.8**: `brew install runpod/runpodctl/runpodctl`, rồi `runpodctl doctor` để
nhập API key (lấy ở runpod.io/console/user/settings).

Từ 2.8 CLI làm được cả hai thứ trước đây phải vào dashboard bấm tay:

| Flag | Giải quyết |
|---|---|
| `--network-volume-id` + `--volume-mount-path` | attach Network Volume ngay lúc tạo pod |
| `--min-cuda-version` | chỉ nhận host đủ driver — giữ đúng nhánh cu130, xem [CUDA 13](#cuda-13) |

`pod-provision.sh` gate theo **flag** chứ không theo chuỗi version (`runpodctl version` đổi format
qua các bản; thứ quan trọng là binary này có nhận flag ta sắp truyền hay không). Thiếu flag → nó
chết kèm lệnh `brew upgrade`, không âm thầm thuê pod không có volume.

Nó cũng tự lo cái bẫy hay dính nhất: **pod phải cùng datacenter với volume**. Script đọc
datacenter của volume rồi ghim `--data-center-ids` theo đúng đó. Chỉ có 1 volume thì tự lấy id và
ghi vào `.env` là `POD_VOLUME_ID`; có nhiều hơn thì nó liệt kê ra và bắt bạn chọn.

`make gpu-wait` chạy được cả RunPod: nó poll `runpodctl ssh info` (nguồn đúng — `pod get` **không**
mang endpoint SSH, trường `ports` của nó chỉ là `["22/tcp"]`), rồi tự điền `GPU_SSH_HOST` và
`GPU_SSH_PORT`.

Hai chi tiết đã trả giá mới biết:

- **Điều kiện dừng không được dựa vào status.** Khi pod sẵn sàng, `ssh info` trả `id · ip · name ·
  port · ssh_command · ssh_key` — **không có trường status nào**. Bản đầu bắt buộc
  `status == running` nên chờ hết 25 phút trong khi đã cầm sẵn host và port. Giờ nó chỉ dựa vào
  endpoint cộng một lần bắt tay SSH thật; status chỉ để hiển thị.
- **`runpodctl pod get` thỉnh thoảng trả rỗng**, exit 0, không cảnh báo. Đo được 1 trên 3 lần.
  Vòng lặp coi đó là "chưa sẵn sàng" và thử lại, không coi là lỗi.

Lệnh vòng đời dùng cú pháp mới `runpodctl pod start|stop|delete` — dạng cũ (`runpodctl start pod`)
vẫn chạy nhưng đã bị đánh dấu deprecated.

<a id="no-auto-models"></a>
## Vì sao không tự động tải model

Tải model là hành động qua UI đã login (OTP), tải ~33GB — không phải lệnh SSH đơn giản mà là
click trong FE sau khi đăng nhập; catalog model cũng có thể đổi theo thời gian. Tự động hoá bước
này rủi ro hơn lợi ích (tải nhầm/tải thiếu mà không ai để ý), nên để thủ công, chỉ 1 lần mỗi pod
mới.

<a id="systemd-cloudflared"></a>
## systemd / cloudflared

Nhiều container vast.ai/RunPod KHÔNG có systemd. `setup-motion-transfer.sh` đã tự dò việc này cho
Postgres (fallback `pg_ctlcluster`), nhưng **cloudflared thì chưa** — `cloudflared service install`
ghi file systemd unit nhưng có thể không có gì khởi động nó thật. `pod-bootstrap.sh` tự phát hiện
(`curl https://$DOMAIN/health` fail) và thử fallback: đọc token thẳng từ file unit rồi chạy
`cloudflared tunnel run --token ...` bằng `nohup`. Đây là best-effort — nếu vẫn không lên, SSH vào
xem `/etc/systemd/system/cloudflared.service` và `/tmp/cloudflared.log` (lệnh in sẵn ở cuối
`make gpu-bootstrap` khi rơi vào trường hợp này).

## motions-studio/.env — có cần điền tay gì thêm không

Không. `setup-motion-transfer.sh` (phase `phase_dotenv`, `lib-feature.sh:370`) tự sinh **toàn bộ**
`.env` thật trên pod từ 6 flag `pod-bootstrap.sh` đã truyền (DOMAIN, SUPER_ADMIN, GMAIL_USER,
GMAIL_APP_PASSWORD, CF_API_TOKEN/CF_TUNNEL_TOKEN, CORS_ORIGINS):

- **Secret tự sinh random** (không cần điền): `POSTGRES_PASSWORD`, `MINIO_ROOT_PASSWORD`,
  `SESSION_JWT_SECRET`, `WORKER_TOKEN`, `API_KEY`.
- **Tự khoá feature**: `JOB_TYPES=motion,teen-flycam,trend-tiktok`, `MODEL_CATALOG_PATH` trỏ
  catalog riêng — worker/Settings→Models AI chỉ thấy đúng nhóm Wan 2.2 Animate.
- **Tự set theo môi trường**: `COMFY_URL`, `INTERNAL_API_URL`, `PUBLIC_BASE_URL`,
  `S3_PUBLIC_ENDPOINT`, `OLLAMA_URL=""` (motion-transfer không cần LLM), `MOTION_ATTENTION` (script
  tự verify SageAttention rồi ghi).
- Các biến khác trong `motions-studio/.env.example` (`QWEN_EDIT_GGUF`, `LTX_*`, `SS_*`,
  `CREATE_IMAGE_*`, `WORKFLOW_AI_MODEL`, Social Management…) thuộc các feature KHÁC
  (create-image/tryon/teaser/LTX) — box này khoá `JOB_TYPES` nên không chạy tới, khỏi cần đụng.

Chỉ 1 thứ THỰC SỰ tuỳ chọn, không bắt buộc: **`HF_TOKEN`** — nếu lúc tải model ở Settings → Models
AI gặp lỗi 401 (model HuggingFace gated), thêm token tại huggingface.co/settings/tokens, SSH vào
sửa `.env` trên pod rồi `pm2 restart worker`. Wan 2.2 Animate thường không cần, nhưng chưa thử nên
không chắc 100%.

## Tại sao không rsync `.git`/`node_modules`/model

`pod-bootstrap.sh` rsync `motions-studio/` loại trừ `.git`, `node_modules`, `.venv`,
`__pycache__`, `.env*`, `.data`, `data`, `*.mp4`, `ltx-ss-prebuilt/*.safetensors` — đúng những gì
`.gitignore` của `motions-studio` đã loại, cộng `.git` vì pod tự có bản riêng, không cần lịch sử.
Model KHÔNG nằm trong repo — luôn tải qua Settings → Models AI sau khi bootstrap xong.

---

<a id="runpod-gotchas"></a>
## Ba cái bẫy của RunPod — đo trên pod thật 01/08/2026

Cả ba đều thuộc loại "nhìn từ ngoài giống hệt boot chậm". Tốn $0.43 và vài vòng lặp sai hướng
mới lần ra.

### 1 · Image phải là `runpod/*`, nếu không container restart vô hạn

| | Ai chạy sshd |
|---|---|
| vast.ai | `vastai create --ssh --direct` **tiêm sshd của vast** → image thường vẫn chạy |
| RunPod | **không tiêm gì** — image phải tự chạy sshd và không được thoát |

`pytorch/pytorch:*` (image chính thức của PyTorch) có `CMD=bash`, thoát ngay khi không có tty →
RunPod khởi động lại → lặp mãi. Triệu chứng: console lặp `start container for <image>: begin`,
`runpodctl ssh info` mãi trả `"pod not ready"`.

`runpod/pytorch:*` có `/start.sh` chạy sshd rồi block. `pod-provision.sh` giờ chọn mặc định theo
provider và cảnh báo nếu `POD_IMAGE` không phải `runpod/*` khi dùng RunPod.

Tag CUDA của image **không** quyết định CUDA mà ComfyUI dùng — `--min-cuda-version` mới làm việc
đó. Xem [CUDA 13](#cuda-13).

### 2 · PGDATA không sống được trên Network Volume

```
mfs#euro-3.runpod.net:9421 on /workspace type fuse (rw,...,user_id=0,group_id=0)
chown: 'Operation not permitted'      ← ngay cả khi là root
```

RunPod mount volume bằng MooseFS, ép `root:root` và chặn `chown`. Postgres **từ chối khởi động**
nếu PGDATA không thuộc user `postgres` mode 0700. `rsync -a` chết ở lần `chown` đầu tiên và
`pod-volume.sh` dừng trước khi cài gì.

Phương án file ext4 loopback trên volume: **bất khả thi**, container không có `/dev/loop*`.

Nên `VOLUME_PGDATA=0` là mặc định. Hệ quả:

| | |
|---|---|
| DB sống qua `gpu-down` → `gpu-up` | ✅ container disk còn |
| DB sống qua `gpu-destroy` | ❌ **mất** |
| Model 33GB | ✅ vẫn trên volume, vẫn không phải tải lại |

Muốn giữ DB qua `destroy` thì dump ra volume **trước khi destroy** (chưa tự động hoá):

```bash
# trên pod, TRƯỚC make gpu-destroy
ssh -p $GPU_SSH_PORT root@$GPU_SSH_HOST \
  'mkdir -p /workspace/pg-backup && sudo -u postgres pg_dump motion \
   | gzip > /workspace/pg-backup/motion-$(date +%Y%m%d-%H%M).sql.gz'

# trên pod MỚI, SAU make gpu-bootstrap
ssh -p $GPU_SSH_PORT root@$GPU_SSH_HOST \
  'zcat $(ls -t /workspace/pg-backup/*.sql.gz | head -1) | sudo -u postgres psql motion'
```

Kiểm dump có toàn vẹn không trước khi tin: `gzip -t <file>`. Một bản dump hỏng còn tệ hơn không
có, vì nó khiến bạn yên tâm destroy.


### 3 · MinIO từ chối symlink làm drive

`pod-volume.sh` nối `.data/minio → $POD_VOLUME/minio` bằng symlink. Mọi thứ khác chấp nhận, riêng
MinIO chết:

```
FATAL Unable to initialize backend: Unable to write to the backend
HINT: Drives are not directories, MinIO erasure coding needs directories
```

rồi PM2 restart mãi — **37 lần** trước khi bị phát hiện, và `pm2 ls` chỉ hiện `waiting` chứ không
`errored`, nên nhìn lướt tưởng đang khởi động.

`mount --bind` bị container từ chối. `ecosystem.config.cjs:58` đọc `MINIO_DATA_DIR`, nên
`pod-bootstrap.sh` trỏ MinIO thẳng vào `$POD_VOLUME/minio`, bỏ qua symlink.

<a id="preload"></a>
## Tải model vào volume TRƯỚC, bằng máy CPU rẻ tiền

Tải model không cần GPU — nó là việc mạng cộng đĩa. Làm việc đó trên pod GPU $1.014/giờ nghĩa là
trả tiền cho một card 5090 ngồi không suốt thời gian `aria2c` chạy. Volume thì dùng lại được cho
mọi pod sau, nên đây là việc làm **một lần cho mỗi volume**.

Công thức dưới đây đã chạy thật 02/08/2026: **$0.06/giờ**, tải 38,9 GB trong **9 phút**, tổng
khoảng **$0.08**. Cùng việc đó trên pod GPU là $1.014/giờ.

```bash
# 1. Pod CPU, cùng datacenter với volume (EU-RO-1 — volume khoá vào đó).
#    --image PHẢI là runpod/* : RunPod không tiêm sshd, image tự thoát sẽ restart vô hạn (#runpod-gotchas §1).
#    --terminate-after: lưới an toàn, pod tự chết kể cả khi bạn quên.
runpodctl pod create --name preload --compute-type CPU \
  --image runpod/base:1.0.2-ubuntu2204 \
  --data-center-ids EU-RO-1 --network-volume-id <vol-id> \
  --container-disk-in-gb 20 --ssh --terminate-after 2026-08-02T21:00:00Z

# 2. SSH vào (KHÔNG chờ runtime.ports — với pod CPU nó ở null mãi, sshd vẫn lên bình thường)
runpodctl ssh info <pod-id>          # in sẵn câu lệnh ssh
git clone https://github.com/<owner>/motion-clone.git && cd motion-clone/motions-studio

# 3. Xem có gì, thử khan, rồi tải. VOLUME_GB = quota volume, thiếu nó là cổng chặn tắt.
POD_VOLUME=/workspace VOLUME_GB=100 ./setup/preload-models.sh --list
POD_VOLUME=/workspace VOLUME_GB=100 ./setup/preload-models.sh --group "Qwen-Image-Edit" --dry-run
nohup env POD_VOLUME=/workspace VOLUME_GB=100 ./setup/preload-models.sh \
  --group "Qwen-Image-Edit" > /workspace/preload.log 2>&1 &

# 4. XOÁ pod ngay khi xong — pod CPU vẫn tính tiền theo giờ
runpodctl pod delete <pod-id>
runpodctl pod list        # phải rỗng
```

Script dùng **chung `comfyui/catalog.json` với app**, ghi vào đúng đường app ghi
(`comfy-models/<type>/<filename>`, xem `api/src/models-install.js:93-95`), nên pod sau bấm
Settings → Models AI sẽ thấy "đã cài" chứ không tải lại.

Nó lo cả **model Ollama** (nhóm `Ollama (LLM nội bộ)`, 12,2 GB — dịch VN→EN cho `create-image`,
dịch phụ đề cho `subtitle`, vision cho tryon Auto). Đường của chúng khác hẳn: `ollama pull` chứ
không `aria2c`, và ghi vào `ollama-models/` chứ không `comfy-models/`. Script tự cài Ollama, trỏ
`OLLAMA_MODELS` vào volume, và chạy `ollama serve` nền — container không có systemd nên
`systemctl start ollama` vô nghĩa ở đây.

Nó **kiểm cỡ file trước khi đổi tên** `.part` → tên thật, và mốc kiểm là **`Content-Length` của máy
chủ**, KHÔNG phải `sizeBytes` trong catalog. Chuyện này học bằng một lần chạy hỏng: `sizeBytes` là
số cũ và lệch **cả hai chiều** — `qwen-vae` ghi 257.698.037 còn HF trả 253.806.246 (thiếu 3,9 MB),
`qwen-vl-7b` ghi 9.384.497.971 còn HF trả 9.384.670.680 (**thừa** 172 KB). Lấy catalog làm mốc thì
5/5 file tải hoàn chỉnh bị báo hỏng và kẹt ở `.part`.

Catalog vẫn còn một việc: nếu `Content-Length` **nhỏ hơn nửa** số catalog thì script dừng cả mẻ.
So `Content-Length` với chính file tải về không bắt được trường hợp HuggingFace trả 200 kèm trang
HTML lỗi — lúc đó header khớp thân HTML nên phép so luôn "đúng", và bạn có một "model" vài KB mà
ComfyUI chỉ báo lỗi ở job đầu tiên, sau khi đã thuê GPU.

Chạy lại an toàn: file khớp `Content-Length` thì bỏ qua, file dở thì `--continue` tải tiếp.

**Dung lượng là ràng buộc thật.** Catalog đầy đủ là **245 GB** (ComfyUI) + 12,2 GB (Ollama); volume
mặc định của dự án là 100 GB.

`df` **không dùng được** để đo chỗ trống ở đây. Đo thật trên pod 02/08/2026:

```
mfs#euro-3.runpod.net:9421   1.4P   1.1P   344T   76%   /workspace
```

Đó là dung lượng cả cụm MooseFS, không phải quota volume. Cổng chặn dựa vào `df` sẽ báo "còn 344TB"
ngay cả khi volume đầy. Vì thế script đòi **`VOLUME_GB`** (quota, lấy từ `runpodctl network-volume
list`) và trừ đi mức dùng thật đo bằng `du -sb`; thiếu biến đó thì nó **nói rõ là cổng đang tắt**
chứ không im lặng cho qua.

| Muốn thêm | Dung lượng |
|---|---|
| Tạo ảnh / tryon (Qwen-Image-Edit) | **31,5 GB** — vừa chỗ trống hiện có |
| Flux (text→image) | 34,5 GB |
| LTX-2.3 (node SS, `video`, `text-to-video`) | 39,8 GB |
| Wan I2V · Wan T2V | 47,0 · 44,5 GB |

Thiếu chỗ thì nới volume (`network-volume update --size`), nhưng nhớ nó tính tiền hằng tháng mãi
mãi theo mức mới — rẻ hơn là chỉ tải nhóm thật sự dùng.

<a id="deploy-shapes"></a>
## Hai hình dạng deploy — chọn bằng hai biến

Ba biến trong `.env`, ba câu hỏi độc lập: **box có GPU không**, **box cài gì**, **ai chạy job**.

```
COMPUTE_TYPE=gpu|cpu                                  ← pod-provision.sh đọc
SETUP_PROFILE=motion-transfer|full|create-image|tryon|cpu-box
WORKER_SOURCE=local|serverless|both
```

Ba tổ hợp có nghĩa; mọi tổ hợp khác bị cổng kiểm chặn hoặc cảnh báo:

| Hình dạng | `COMPUTE_TYPE` | `SETUP_PROFILE` | `WORKER_SOURCE` | Khi nào đúng |
|---|---|---|---|---|
| **Pod GPU** (mặc định) | `gpu` | `motion-transfer` … | `local` | bật theo phiên, job nối nhau |
| **Box CPU** | `cpu` | `cpu-box` | `serverless` | app 24/7, dưới ~79 job/ngày |
| **Test serverless** | `gpu` | `motion-transfer` | `serverless` | chỉ để đo, GPU nằm không |

| | `WORKER_SOURCE=local` | `WORKER_SOURCE=serverless` |
|---|---|---|
| Ai claim job | worker trên pod | container RunPod Serverless |
| Chi phí GPU | **0 thêm** — pod đã trả theo giờ rồi | trả theo giây thực chạy |
| Cold start | không có | ~155 giây lần đầu |
| Dispatcher | không bật | `mc-dispatcher` chạy dưới PM2 |
| Job type chạy được | mọi type trong `JOB_TYPES` của box | chỉ type bake trong image serverless |

**Vì sao `local` không tốn thêm gì:** ở hình dạng giữ pod, bạn đã trả tiền GPU 24/7. Đẩy job sang
serverless nghĩa là trả tiền lần thứ hai cho cùng công việc, *và* thêm cold start, trong khi GPU
đã mua vẫn nằm không. Đây là kết luận của [spec §Kiến trúc](superpowers/specs/2026-08-01-serverless-huong-a-design.md).
Serverless chỉ thắng khi box luôn bật KHÔNG có GPU — hình dạng VPS mà spec nhắm tới.

Đo được, ngày 02/08/2026: trả **cả hai** trong cùng một ngày — $1,49 pod GPU **và** $0,39
serverless, cho cùng một khối công việc. Xem [§Hoá đơn thật](#hoa-don-that).

> **Còn một hình dạng thứ ba, chưa dựng: box CPU + serverless.** `api`/Postgres/MinIO/FE đều là
> việc CPU, nên bỏ GPU khỏi máy luôn bật là cách duy nhất khiến serverless thật sự thắng. Pod GPU
> bật 24/7 là **~$720/tháng**, nên mốc để vượt rất cao. Thiết kế, ba số phải đo trước (giá pod CPU
> chưa lấy được bằng lệnh), và ba chỗ hỏng lặng lẽ:
> [spec box CPU](superpowers/specs/2026-08-04-box-cpu-serverless-design.md).

### Câu hỏi quyết định không phải "bao nhiêu job", mà "GPU có rỗi không"

Đơn giá all-in serverless **$1,586/giờ** so với pod **$1,003/giờ** — serverless đắt hơn
**1,33–1,58×** cho mỗi giây GPU tuỳ cách tách tiền đĩa ([bảng độ bền](#premium-serverless)). Nó
không rẻ hơn về đơn giá; nó chỉ tính $0 khi rỗi.

Với job thật **~9 phút** (motion + enhance, clip 15-20s — quan sát 04/08, kiểm chéo với 0,85 s/frame
trong `worker_runtime/linux.py:850`):

| Cách dùng | GPU bận | Rẻ hơn |
|---|---|---|
| Bật khi làm việc, job nối nhau (2-8 giờ/ngày) | gần 100% | **pod GPU + `local`** — $60-240/tháng, so với $169-467 |
| App bật 24/7, dưới **79 job/ngày** | dưới 51% | **box CPU + serverless** — $170-580/tháng, so với $720 |
| App bật 24/7, trên **79 job/ngày** | trên 51% | pod GPU + `local` |

Bật-tắt theo phiên làm việc thì không có thời gian rỗi nào để serverless tiết kiệm — chỉ còn phần
đắt thêm 59%. Chi tiết bảng và cách tính:
[spec box CPU §Độ dài một job thật](superpowers/specs/2026-08-04-box-cpu-serverless-design.md#job-9-phut).

<a id="box-cpu"></a>
### Dựng box CPU

**Đã dựng thật 04/08/2026** — box `cpu5g × 4 vCPU`, và đây là toàn bộ số đo:

| | |
|---|---|
| Giá | **$0,184/giờ** (≈$132/tháng nếu bật 24/7) |
| RAM · đĩa | 16 GB · trần container disk **60 GB** |
| Giá ở 2 vCPU (probe riêng) | $0,06/giờ, RAM 4 GB |
| `setup-cpu-box.sh` trên box | **~90 giây** — so với ~30 phút của profile GPU, vì bỏ hẳn phần cài torch |
| `npm run build` Nuxt trên 16 GB | ✅ xong, không OOM |
| PM2 sau bootstrap | `api` · `wf-worker` · `minio` · `motions` · `mc-dispatcher` — **không** `worker`/`comfyui`/`task-cloud-auto` |
| `/health` · frontend | ✅ cả hai |
| Volume | mount `/workspace` (MooseFS), model còn nguyên, 81,7/100 GB đã dùng |
| `nvidia-smi` | không tồn tại — đúng là box CPU |

Giá thật $0,184 (không phải $0,10 như từng giả định) kéo ngưỡng đảo chiều xuống: **79 job/ngày**
thay vì 87, tức ≈51% GPU bận. Dưới mức đó box CPU + serverless vẫn rẻ hơn nhiều — 20 job/ngày là
**$282/tháng** so với **$720** của pod GPU bật 24/7.

**Trần đĩa container của pod CPU = `diskLimitPerVcpu × vCPU`** (`cpu3*` = 10 GB/vCPU · `cpu5*` = 15).
Với `cpu5g × 4` là 60 GB. Vượt trần thì REST trả `500 Container Disk must be less than or equal to
N` — `DISK=100` của đường GPU đụng đúng cái này. `pod-provision.sh` nay hỏi API để lấy trần rồi tự
hạ kèm cảnh báo, thay vì để bạn mất một vòng chạy.

```bash
# .env
COMPUTE_TYPE=cpu
SETUP_PROFILE=cpu-box
WORKER_SOURCE=serverless

make gpu-preflight                              # in cả ba biến + RAM suy ra từ flavor
COMPUTE_TYPE=cpu bash scripts/pod-provision.sh  # dry-run, đọc body REST nó in
CONFIRM=yes COMPUTE_TYPE=cpu bash scripts/pod-provision.sh
make gpu-wait                                   # hoạt động với pod CPU, cùng đường runpodctl
make gpu-bootstrap
```

**Nhánh `cpu` đi qua REST, không qua `runpodctl`.** `runpodctl pod create` không có cờ nào chọn
flavor hay số vCPU — nó luôn cấp **2 vCPU / 4 GB RAM**. `POST /v1/pods` có `cpuFlavorIds` +
`vcpuCount`, nên script dùng nó. (Nhưng xem [16 GB là thừa](#box-cpu-ram) trước khi trả tiền cho
flavor lớn — 4 GB có thể đã đủ, chưa ai đo.)

| Flavor | RAM | Mặc định của repo |
|---|---|---|
| `cpu3c` · `cpu5c` Compute | vCPU × 2 | |
| `cpu3g` · `cpu5g` General | vCPU × 4 | **`cpu5g` + 4 vCPU = 16 GB** |
| `cpu3m` · `cpu5m` Memory | vCPU × 8 | |

<a id="box-cpu-ram"></a>
#### 16 GB gần như chắc chắn là thừa — và nó đắt gấp 3

Đo thật lúc box chạy: **toàn bộ PM2 chỉ 311 MB.**

| Tiến trình | RSS |
|---|---|
| `minio` | 119 MB |
| `api` | 70 MB |
| `wf-worker` | 65 MB |
| `mc-dispatcher` | 55 MB |
| `motions` | 2 MB (mới lên 0 giây; sẽ tăng ~150-250 MB) |

Cộng Postgres (~100-300 MB) thì **chạy ổn định dưới 1 GB**. 16 GB không phục vụ việc chạy. Nó phục
vụ **đúng một** việc: `npm run build` của Nuxt trong `pod-fe.sh`, một lần mỗi lần deploy FE.

> **Và chưa ai đo rằng 4 GB KHÔNG đủ cho build đó.** Mới chỉ đo 16 GB thì đủ. Mặc định `cpu5g × 4`
> ra đời từ một phỏng đoán, và phỏng đoán đó tốn **$0,184/giờ so với $0,06** — $132 so với
> $43/tháng, tức **$89/tháng**.

Bốn cách rẻ hơn, **chưa thử cách nào**:

| Cách | Tiết kiệm | Rủi ro |
|---|---|---|
| Bỏ trống `CPU_FLAVOR`/`CPU_VCPU` → 2 vCPU/4 GB | $89/tháng | build có thể OOM, nhưng biết ngay sau 1 lần chạy |
| `CPU_FLAVOR=cpu5m CPU_VCPU=2` → cũng 16 GB, ít vCPU | chưa đo, có thể ~½ | giá flavor memory-optimized chưa đo |
| **Build FE ở CI rồi rsync `.output`** | $89/tháng **và** `make gpu-fe` nhanh hẳn | phải thêm workflow |
| `NODE_OPTIONS=--max-old-space-size` + swap | $89/tháng | build chậm, có thể vẫn OOM |

Cách 3 đúng nhất về kiến trúc. Lý do `pod-fe.sh:13` build trên pod là `sharp` — `node_modules/@img/`
ở máy dev chứa `sharp-darwin-arm64`, copy sang pod Linux x64 thì build xong mà chết lúc chạy. Lý do
đó loại **máy bạn**, không loại **CI**: GitHub Actions `ubuntu-latest` là linux-x64, đúng kiến trúc
pod. Build Nuxt trên 2 vCPU cũng là phần lâu nhất của `make gpu-fe`, nên cách này xoá cả hai vấn đề.

`SETUP_PROFILE=cpu-box` khác `motion-transfer` ở ba điểm: `PM2_APPS="minio,api,wf-worker"` (bỏ
`comfyui`, `worker`, và `task-cloud-auto` — cái cuối throw `"COMFY_URL chưa cấu hình"` rồi
crash-loop vĩnh viễn nếu để lại), `SKIP_COMFY=1` tường minh, và `JOB_TYPES` đúng 4 type mà image
serverless bake.

**Cặp bị chặn cứng:** `COMPUTE_TYPE=cpu` + `WORKER_SOURCE=local`. Box không GPU mà job giao cho
worker local nghĩa là không ai chạy được job nào — box vẫn lên xanh, `/health` vẫn trả lời, job nằm
`queued` vĩnh viễn. Không có cách đọc nào hợp lý nên cổng kiểm chặn thay vì cảnh báo.

<a id="serverless-throttled"></a>
### Chỗ hình dạng này thật sự tắc: worker serverless bị `throttled`

Box CPU chạy hoàn hảo, nhưng `GET /v2/<ep>/health` trả:

```json
"workers": { "idle": 0, "ready": 0, "running": 0, "throttled": 3 }
```

`throttled` = RunPod **không có GPU trống** cho endpoint này, nên job nằm `IN_QUEUE` vô hạn. Không
phải lỗi cấu hình — `WORKER_TOKEN` đã khớp, dispatcher log sạch, `JOB_TYPES` khớp ba nơi.

Nguyên nhân là **giao của ba ràng buộc, và nó đang rỗng**:

| Ràng buộc | Vì sao có |
|---|---|
| `gpuTypeIds` = chỉ RTX 5090 | chọn lúc tạo endpoint |
| `minCudaVersion` = 13.0 | image là cuda13.0; hạ xuống là torch chết trên host driver cũ |
| datacenter EU-RO-1 | **Network Volume ghim cứng**, không dời được |

`runpodctl datacenter list` báo 5090 ở EU-RO-1 là `Medium` — nhưng đó là stock cho **pod**, pool
khác với serverless. Đừng dùng nó để suy ra serverless có chỗ.

Ba đường thoát, chưa thử cái nào:

1. **Thêm loại card vào endpoint** — `RTX PRO 6000` (Low) hoặc `A100 SXM 80GB` (Low) ở EU-RO-1, đều
   ≥24GB VRAM. Mở rộng pool mà không đụng volume. Đắt hơn mỗi giây, nhưng đắt vẫn hơn không chạy.
2. **Chờ.** Throttle là tạm thời theo định nghĩa. Job vẫn nằm `queued`, không mất.
3. **Bỏ Network Volume khỏi endpoint** và bake model vào image → endpoint chạy được ở mọi
   datacenter. Đổi lại image phồng thêm ~40GB và cold start dài ra nhiều.

Đây là rủi ro cố hữu của hình dạng box CPU mà spec đã cảnh báo, nay đã gặp thật: **serverless không
có SLA về chỗ trống**. Worker local trên pod GPU không có vấn đề này — bạn thuê là bạn có.

**Cạm bẫy khi ghép `full` với `serverless`:** profile `full` cho box claim 21 type, nhưng image
serverless bản mặc định chỉ bake 4. 17 type còn lại sẽ nằm `queued` **vĩnh viễn** — không lỗi,
không log, chỉ là không ai nhận. Ba cách thoát:

1. dùng image bản full (`:latest-full`) và mở `DISPATCH_JOB_TYPES` cho khớp;
2. `WORKER_SOURCE=both` — pod gánh phần serverless không làm được;
3. `WORKER_SOURCE=local` — đơn giản nhất khi pod đã có GPU.

`pod-bootstrap.sh` so hai danh sách sau khi cài và **cảnh báo** nếu có type rơi vào khoảng trống
đó. Cảnh báo, không chặn — nó không biết bạn có cố ý hay không.

`WORKER_SOURCE=both` chỉ an toàn khi hai bên nhận nhóm type **rời nhau**. Trùng type thì worker
local nhặt job trong vài mili-giây còn container serverless vẫn tỉnh dậy sau 1–3 phút, thấy hàng
đợi rỗng rồi thoát — ta trả tiền cold start đó cho không, và log hai bên đều sạch.

### Để trống hai biến này là chọn, không phải hoãn

Bỏ trống `WORKER_SOURCE` thì nó vẫn được suy ra: có đủ `RUNPOD_ENDPOINT_ID` + `RUNPOD_API_KEY` →
`serverless`, không thì `local`. Nghĩa là hai key RunPod điền từ đời nào cũng đủ để bootstrap
`pm2 stop worker` trên pod bạn vừa trả tiền — không ai đọc `.env` mà đoán ra điều đó.

Nên `make gpu-preflight` in cả khối **Hình dạng deploy** ra trước khi đồng hồ tiền chạy: profile
nào, ai claim job, giá trị đó *đặt trong `.env`* hay *suy ra*, và năm biến `DISPATCH_*` cái nào
đang có hiệu lực. Nó chặn (exit ≠ 0) đúng những cấu hình mà `pod-bootstrap.sh` sẽ chết vì chúng —
`WORKER_SOURCE` sai chính tả, khai `serverless` mà thiếu key RunPod, `SETUP_PROFILE` không tồn tại.

Cách suy ra nằm ở `scripts/lib-deploy-shape.sh`, được **cả hai** script source. Chép logic sang
preflight là cách hiển nhiên và là cách sai: lệch một chi tiết giữa hai bản chép thì cổng kiểm báo
một đằng, pod dựng một nẻo.

<a id="serverless"></a>
## RunPod Serverless — GPU chỉ tính tiền khi có job

Chạy được thật, đo trên endpoint `fggbwsbhidwbdi` ngày 02/08/2026: 5 job `motion` 540p/33 frame
qua đường serverless, không job nào lỗi. Con số quan trọng nhất:

| Đo | Giá trị |
|---|---|
| Pod GPU luôn bật | **$1.014/giờ** — trả cả lúc không ai dùng |
| Serverless, tổng cho 5 job | **$0.3894** — hoá đơn thật, 884 giây được tính ([vì sao không phải $0.0116](#hoa-don-that)) |
| Job 540p/33 frame, worker ấm | 2 phút 46 giây từ `queued` → `done` |
| Cold start (kéo image 5,09GB nén lần đầu) | ~155 giây |
| Worker ấm | delay 1,9s · execution 0,3s |
| Output thật | 344 KB — xa dưới ngưỡng 100MB của [§Quyết định 3](superpowers/specs/2026-08-01-serverless-huong-a-design.md) |

`GET /v2/<endpoint>/health` báo `idle=2 ready=2` cả khi rỗi, nhưng `currentSpendPerHr` KHÔNG đổi
(vẫn đúng bằng pod + volume). Đó là slot FlashBoot, không phải worker tính tiền — đừng hoảng.

### Dựng lại từ đầu

```bash
# 1. Image: đẩy code lên main, CI build HAI bản song song
#    :latest      / :sha-<commit>        6 node · 4 type Wan   (nhẹ, cold start ngắn)
#    :latest-full / :sha-<commit>-full   9 node · 21 type       (thêm GGUF, LTXVideo, SeedVR2)
git push origin main
gh run list --limit 2            # chờ CẢ HAI 'success' (~10-18 phút mỗi bản)

# 2. Template — dùng tag sha-<commit>, KHÔNG dùng :latest (xem bẫy 3 bên dưới)
#    JOB_TYPES ở đây chỉ để GHI ĐÈ; bỏ trống thì entrypoint lấy đúng list bake trong image
#    (/etc/motion/job-types), nên bản full tự nhận đủ 21 type mà không cần chép lại danh sách.
WT=$(ssh -p $GPU_SSH_PORT root@$GPU_SSH_HOST "grep -E '^WORKER_TOKEN=' ~/motion-backend/.env | cut -d= -f2-")
runpodctl template create --serverless --name motion-serverless \
  --image ghcr.io/<owner>/motion-serverless:sha-<commit> --container-disk-in-gb 40 \
  --env "{\"API_URL\":\"https://api.doanhthuc.xyz\",\"WORKER_TOKEN\":\"$WT\"}"

# 3. Endpoint — datacenter PHẢI trùng volume
runpodctl serverless create --name motion-serverless --template-id <tpl> \
  --gpu-id "NVIDIA GeForce RTX 5090" --data-center-ids EU-RO-1 \
  --network-volume-id <vol> --workers-min 0 --workers-max 3 --idle-timeout 120 \
  --flash-boot --scale-by requests --scale-threshold 1 \
  --min-cuda-version 13.0   # BẮT BUỘC: image là cuda13.0, mặc định endpoint là 12.0

# 4. Ghi vào .env gốc rồi make gpu-bootstrap (nó dựng mc-dispatcher và DỪNG worker local)
#    RUNPOD_ENDPOINT_ID=<endpoint>   RUNPOD_API_KEY=<key>   WORKER_SOURCE=serverless
#    Dùng image bản full thì mở luôn DISPATCH_JOB_TYPES cho khớp 21 type — xem §Hai hình dạng
#    deploy. Bỏ quên là 17 type nằm queued vĩnh viễn.
```

### Năm cái bẫy, cả năm đều trả giá bằng một vòng chạy

**1. `{"input":{}}` bị RunPod bỏ.** SDK kiểm bằng độ chân trị của Python nên `{}` (falsy) bị coi là
THIẾU `input`: `Job has missing field(s): id or input.` → thử lại một lần → trả về
`job timed out after 1 retries`. Thông báo đó không hề chỉ về payload, và đây từng là lỗi trong
chính `mc-dispatcher.js` — dispatcher không đánh thức được worker nào, lần nào cũng vậy. Luôn gửi
một khoá gì đó: `{"input":{"wake":1}}`.

**2. `WORKER_TOKEN` ≠ `API_KEY`.** `auth.js:12` đọc header `x-worker-token` và so với biến môi
trường `WORKER_TOKEN` của api. Điền nhầm `API_KEY` thì handler chạy tới nơi rồi ăn 401 ở
`/worker/claim`.

**3. Đổi env của template KHÔNG chạm tới worker đang sống.** Sau `POST /endpoints/<id>/update`,
worker cũ vẫn phục vụ request với env cũ thêm một lúc. Triệu chứng đánh lừa: `API_URL` và
`JOB_TYPES` trông đúng (vì hai template chỉ khác một biến), chỉ mình biến vừa sửa là sai. Bắn lại
vài lần cho tới khi trúng worker mới, hoặc đợi hẳn.

**4. Không có đường lấy log bằng lệnh.** Không `runpodctl serverless logs`, REST v1 chỉ có
`/endpoints`, GraphQL tắt introspection. Vì thế `entrypoint-selfhosted.sh` tee toàn bộ stdout ra
`/runpod-volume/serverless-logs/<worker-id>.log`; đọc từ pod:

```bash
ssh -p $GPU_SSH_PORT root@$GPU_SSH_HOST 'ls -t /workspace/serverless-logs/ | head; cat /workspace/serverless-logs/<file>'
```

Riêng việc file có xuất hiện hay không đã là tín hiệu: không có file nào = volume không mount được.

**5. `pm2 start <script>` không đọc file `.env` nào cả.** Chỉ `ecosystem.config.cjs` mới có khối
`env:`; dispatcher được start thẳng bằng `pm2 start api/src/mc-dispatcher.js`, nên mọi biến nó cần
phải được `pod-bootstrap.sh` **chuyển tay** vào dòng lệnh. Trước 03/08/2026 nó chỉ chuyển
`DATABASE_URL`, `RUNPOD_ENDPOINT_ID`, `RUNPOD_API_KEY` — cả năm biến `DISPATCH_*` đặt trong `.env`
gốc là no-op im lặng, dispatcher luôn chạy bằng default. Không có triệu chứng nào: `.env.example`
mô tả chúng đầy đủ, `pm2 status` báo online, log sạch. Nặng nhất là `DISPATCH_ORPHAN_SEC=0` —
`.env.example` nói "tắt hẳn" trong khi thực tế vẫn reclaim job ở 900 giây.

Thêm biến `DISPATCH_*` mới thì **phải** thêm tên nó vào vòng lặp trong `scripts/pod-bootstrap.sh`,
nếu không lỗi này quay lại y nguyên. `make gpu-preflight` in ra biến nào đang có hiệu lực và biến
nào đang rơi về default, nên lệch là thấy được từ máy local.

### Volume mount ở đâu

`/runpod-volume`, **cố định** — template Serverless không có ô "Volume Mount Path" như template Pod
(pod của ta dùng `/workspace`). `entrypoint-selfhosted.sh` tự nối `comfy-models/` và `hf-cache/`
sang `/app/ComfyUI/`, và `exit 1` kèm cách sửa nếu không thấy. Gắn volume KHOÁ endpoint vào
datacenter của volume — hết GPU trống ở đó nghĩa là job chờ, không phải job lỗi.

<a id="network-volume"></a>
<a id="volume"></a>
## Network Volume — hết tải lại model, hết mất database

Hai chi phí lặp lại mỗi lần dựng pod, và cách xoá chúng:

| Chi phí | Nguyên nhân | Cách xoá |
|---|---|---|
| ~33GB tải model **trong app** | `lib-feature.sh` cố ý không tải model; bạn tự bấm Settings → Models AI mỗi pod mới | `POD_VOLUME` |
| ~20-35 phút cài phần mềm | `setup-motion-transfer.sh` cài ComfyUI + torch + custom node từ đầu | `MTC_PREBUILT=1` |
| Mất users/jobs/workflows | Postgres nằm ở `/var/lib/postgresql` trên container disk, bị dựng lại mỗi Stop/Start | `POD_VOLUME` |

### Tạo volume (một lần)

Qua CLI (runpodctl ≥ 2.8):

```bash
runpodctl datacenter list                    # chọn dc còn stock GPU bạn thuê
runpodctl network-volume create --name motion --size 100 --data-center-id <DC>
```

Hoặc dashboard: RunPod → **Storage → Network Volume**. ~100GB là đủ (Wan 2.2 Animate ~33GB +
PGDATA + MinIO + chỗ thở). **Region quan trọng hơn dung lượng** — volume không di chuyển được, và
pod khác region thì không mount được. Chọn region còn stock GPU bạn hay thuê.

Rồi trong `.env`:

```bash
POD_VOLUME=/workspace
MODELS_MIN_GB=20          # ngưỡng cảnh báo "symlink trỏ thư mục rỗng"
POD_VOLUME_ID=            # để trống nếu chỉ có 1 volume — pod-provision.sh tự điền
```

`make gpu-provision` sẽ attach volume và ghim pod vào đúng datacenter của nó.

Từ đó `make gpu-bootstrap` tự nối `models` + `PGDATA` + `MinIO` sang volume trước khi chạy setup.

### Lần đầu trên pod ĐÃ tải model rồi

`pod-volume.sh` **không bao giờ tự xoá dữ liệu**. Nếu `$COMFY_DIR/models` đang là thư mục thật có
dữ liệu, nó dừng và yêu cầu bạn dời một lần:

```bash
make gpu-volume-adopt     # rsync sang volume, đổi tên nguồn thành .bak-<timestamp>
```

Nguồn được giữ lại dưới dạng `.bak-*` — tự xoá khi bạn đã yên tâm.

<a id="volume-check"></a>
### Kiểm chứng — đừng tin màu xanh

Lỗi đáng sợ ở đây không ồn ào mà là **thành công giả**: pod lên, `/health` trả ok, login được,
nhưng `models/` là thư mục rỗng trên container disk và app lặng lẽ tải lại 33GB.

```bash
make gpu-volume-check
```

Nó kiểm bằng con số, không bằng cảm giác: symlink có trỏ đúng volume, `data_directory` của Postgres
có nằm trên volume, và **số file model có giảm so với manifest** hay không. Số file giảm là lỗi
cứng (exit 1) — và manifest cố ý **không** bị ghi đè lúc đó, để bạn còn số cũ mà đối chiếu.

`make gpu-bootstrap` cũng chạy bước check này ở cuối.

### Ràng buộc phải biết

- **PGDATA khoá theo major version Postgres.** Volume tạo bởi PG 16 thì pod có PG 17 không mở
  được cluster. `pod-volume.sh` đọc `pgdata/PG_VERSION` và dừng sớm kèm thông báo rõ thay vì để
  Postgres chết âm thầm. Cách tránh: luôn dùng cùng một base image.
- **Volume khoá theo region.** Region hết GPU thì phải chờ, hoặc tạo volume thứ hai ở region khác.
- **Volume tính tiền liên tục**, kể cả khi không có pod nào (~$0.07/GB/tháng → 100GB ≈ $7/tháng).
  Vẫn rẻ hơn nhiều so với trả tiền pod trong lúc ngồi chờ tải 33GB mỗi lần.
- **ComfyUI code + venv CỐ Ý không nằm trên volume.** Volume là network storage; `import torch`
  đọc hàng nghìn file nhỏ, mà `run_enhance` gọi `comfy_recycle` giữa mỗi chunk RIFE nên ComfyUI
  restart nhiều lần trong một job. Phần mềm để `MTC_PREBUILT=1` lo.

### Image dựng sẵn (`MTC_PREBUILT=1`)

`motions-studio/worker-image/Dockerfile` bake ComfyUI + custom node + `worker-venv` +
`api-node_modules` vào `/opt/mtc-prebuilt`. Build, push, dùng làm image của pod, rồi đặt
`MTC_PREBUILT=1` trong `.env`. `lib-feature.sh:475` sẽ symlink thẳng vào đó và bỏ qua toàn bộ
phần cài đặt.

Thiếu `/opt/mtc-prebuilt/.ready` là setup `die` ngay — cố ý, để bạn không âm thầm rơi về đường
cài-từ-đầu mà không biết.

---

<a id="smoke"></a>
## Kiểm chứng sau khi dựng: `make gpu-smoke`

`make gpu-status` chỉ curl `/health`. Mà `/health` là một handler tĩnh
(`api/src/server.js:55`) — nó trả 200 kể cả khi Postgres không nối được, ComfyUI chưa nạp custom
node, hoặc `models/` là thư mục rỗng trên container disk. Lỗi đắt tiền nhất ở đây không ồn ào mà
là **thành công giả**: mọi thứ xanh, và app lặng lẽ tải lại 33GB.

`make gpu-smoke` kiểm sáu lớp, rẻ trước đắt sau, mỗi lớp in ra nó **chứng minh** điều gì:

| Lớp | Kiểm | Chứng minh |
|---|---|---|
| 1 | `GET /health` | Cloudflare Tunnel sống + tiến trình api sống |
| 2 | `GET /jobs?limit=1` | api + **Postgres** + API key — `/health` không chứng minh được DB, endpoint này có truy vấn thật |
| 3 | `pm2 jlist` | mọi app online, không app nào crash-loop |
| 4 | `/object_info/WanVideoModelLoader` | custom node đã nạp thật; thiếu là job motion chết với 400 *"node type not found"* |
| 5 | `pod-volume.sh --check` | volume thật sự đang được dùng, số file model không giảm |
| 6 | một job motion thật | năm lớp trên chứng minh từng mảnh đúng; chỉ job thật chứng minh chúng **ghép lại** đúng |

Lớp 6 là opt-in vì repo không có sẵn video dẫn động:

```bash
SMOKE_REF=nhanvat.jpg SMOKE_DRIVER=dandong.mp4 make gpu-smoke
```

Nó gửi job nhỏ nhất mà vẫn đi hết đường ống (540p, 33 frame), poll tới khi `done`, rồi tải output
về `/tmp/smoke-out.mp4` và kiểm dung lượng. Đường đi: Postgres → worker claim → ComfyUI nạp Wan
Animate **từ volume** → DWPose → sampling → VAE decode → MinIO → API trả URL.

Thiếu `GPU_SSH_HOST`/`GPU_SSH_PORT` trong `.env` thì lớp 3-5 tự bỏ qua kèm thông báo rõ, không
im lặng báo pass.

## Không còn lấy bản mới từ upstream

`motions/` và `motions-studio/` khởi nguồn từ `ALD-Project` (source đã mua). **Từ 02/08/2026 repo
này không sync nữa** — coi hai thư mục đó là code của mình, sửa thẳng vào.

Đã xoá: `scripts/sync-upstream.sh`, `make sync-upstream`, `UPSTREAM_SHA`, `scripts/check-local-deltas.sh`
và `make check-deltas`. Cái cổng delta cuối chỉ tồn tại để giữ các bản sửa cục bộ khỏi bị `rsync`
của sync ghi đè âm thầm; không còn sync thì không còn gì ghi đè, nên nó là nghi thức rỗng.

Hai thứ trước đây phải canh giờ đơn giản là code của ta:

| Từng là "delta cục bộ" | Bây giờ |
|---|---|
| `ENV PIP_BREAK_SYSTEM_PACKAGES=1` trong `comfyui/Dockerfile` và `worker/runpod/Dockerfile` | dòng bình thường trong file của mình |
| `VOLUME_PGDATA=0` + kiểm MinIO hai-cách-hợp-lệ trong `setup/pod-volume.sh` | như trên |

`motions-studio/setup/scrub-secrets.sh` thì **giữ**: nó không còn dọn secret mà sync mang về, nhưng
`--check` vẫn là cổng chặn trước mỗi commit lên repo public — secret về theo đường ai đó dán một key
vào `.env.example` cũng nguy hiểm y như về theo đường sync.

Nếu sau này muốn lấy lại một bản vá từ upstream: `git clone` họ vào đâu đó, `diff` bằng tay, chọn
hunk. Không đáng dựng lại cả bộ máy sync cho việc đó.
