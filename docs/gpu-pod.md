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

Từ đây trở đi đồng hồ chạy. Toàn bộ giai đoạn này mất ~30-40 phút, phần lớn là chờ — **trừ khi
`MTC_PREBUILT=1`**: đo thật 06/08/2026 trên pod GPU, bước 4-5 (chờ SSH + `make gpu-bootstrap`) gộp
lại còn **284 giây ≈ 4,7 phút** (pull image 16,20 GB + boot 176 giây, `make gpu-bootstrap` 108
giây). Số ~30-40 phút cũ vẫn đúng cho đường không prebuilt (`MTC_PREBUILT=0`, cài từ đầu).

```bash
make gpu-preflight                          # 1. chặn mọi lỗi cấu hình TRƯỚC khi tốn tiền
bash scripts/pod-provision.sh               # 2. dry-run — ĐỌC lệnh pod create nó in ra
CONFIRM=yes bash scripts/pod-provision.sh   # 3. thuê thật; tự ghi GPU_INSTANCE_ID vào .env
make gpu-wait                               # 4. chờ SSH; tự ghi GPU_SSH_HOST/PORT vào .env
make gpu-bootstrap                          # 5. ~30 phút, CHỈ backend — xem bên dưới nó làm gì
make gpu-fe                                 # 6. (tuỳ chọn) đưa frontend lên pod, nếu FE_DOMAIN đã đặt
make gpu-volume-check                       # 7. chứng minh volume thật sự đang được dùng
```

Bước 5 làm, theo đúng thứ tự này (thứ tự có lý do — xem `scripts/pod-bootstrap.sh`):

1. rsync `motions-studio/` lên pod
2. symlink `models` + `PGDATA` + `MinIO` sang volume — **trước** khi setup chạy, nếu không
   Postgres dựng cluster trên container disk rồi mất sạch khi destroy pod
3. `setup-<SETUP_PROFILE>.sh`: Postgres, MinIO, ComfyUI, PyTorch khớp driver ([CUDA 13](#cuda-13)),
   custom nodes, Cloudflare Tunnel với **2 hostname** (`DOMAIN`→:8080, `FE_DOMAIN`→:2030)
4. ghi `motions/.env` **local** trỏ vào backend mới

`make gpu-bootstrap` **không** deploy frontend nữa (từng làm, đã bỏ) — nó là bước riêng, bước 6:
`make gpu-fe` ([chi tiết](#frontend-on-the-pod)). Không phải vì bước FE luôn thất bại: `pod-fe.sh`
so **cây** `motions/` (`git diff --quiet <headSha-của-run> HEAD -- motions/`), không so đúng SHA,
nên một commit chỉ chạm backend/infra vẫn dùng được artifact CI của lần build FE gần nhất — miễn
`motions/` chưa đổi từ đó — và script in rõ commit nào khi artifact không phải build từ HEAD. Tách
riêng vì hai lý do khác: `gpu-bootstrap` chạy lại (idempotent) mỗi lần sửa backend, không cần build
lại FE mỗi lần; và khi `motions/` THẬT SỰ đổi mà chưa có CI xanh, `gh` chưa login, hay artifact đã
hết hạn (giữ 90 ngày) — lỗi FE đó không nên chặn luôn backend đã ổn trong cùng một lệnh.

**8 · Tải model** — không tự động, cố ý ([lý do](#no-auto-models)). Chỉ cần làm
**một lần cho mỗi volume**, pod sau không phải tải lại:

`https://$FE_DOMAIN` → login bằng `SUPER_ADMIN` (bấm gửi OTP) → **Settings → Models AI** → nhóm
**Wan 2.2 Animate** → **Cài cả nhóm** (~33GB).

> **Bỏ qua được bước này** nếu đã làm [0.4 · đổ model bằng pod CPU](#preload) — Models AI sẽ hiện
> "đã cài" sẵn. Làm ở đây nghĩa là pod GPU $1.014/giờ nằm chờ suốt lúc tải; ở 0.4 là $0.06/giờ.
> Với `SETUP_PROFILE=full` thì catalog mở hết 245GB, càng không nên tải trên đồng hồ GPU.

**9 · Kiểm chứng thật** — [`make gpu-smoke`](#smoke). Đừng tin
màu xanh, chạy một job thật.

---

### Giai đoạn 2 — dùng hằng ngày

| Việc | Lệnh |
|---|---|
| **Xong việc** | `make gpu-destroy` — xoá hẳn, dừng mọi đồng hồ trừ volume |
| Dựng lại phiên sau | `make gpu-provision` → `gpu-wait` → `gpu-bootstrap` (~5 phút) |
| Nghỉ ngắn, còn quay lại trong ngày | `make gpu-down` rồi `make gpu-up` |
| Backend còn sống không | `make gpu-status` |
| Xem log | `make gpu-logs` · `LOG=worker make gpu-logs` |
| Sửa code FE → đẩy lên pod | `make gpu-fe` (~2 phút) |
| Sửa code BE → đẩy lên pod | `make gpu-bootstrap` (idempotent, chạy lại an toàn) |
| Code FE ở máy mình | `make dev` → `localhost:2030` |

<a id="destroy-first"></a>
**Vì sao `gpu-destroy` là mặc định, không phải `gpu-down`.** Ba khoản làm cho việc dựng lại pod
từng đắt đã bị xoá lần lượt, và khoản cuối vừa xong:

| Chi phí mỗi lần dựng pod | Cách xoá |
|---|---|
| ~33 GB tải model | `POD_VOLUME` — model nằm trên Network Volume |
| ~20-35 phút cài phần mềm | `MTC_PREBUILT=1` — image dựng sẵn ở CI |
| Build frontend trên pod | `FE_BUILD=ci` — artifact từ GitHub Actions |
| **Mất database** | **[`pod-pgdump.sh`](#pg-backup)** — dựng lại từ bản `pg_dump` trên volume |

Còn lại đúng **~5 phút** dựng lại (đo thật: pull image + boot 176 giây, `gpu-bootstrap` 108 giây).
Đổi lại, `gpu-destroy` dừng mọi đồng hồ trừ volume, còn `gpu-down` thì container disk **vẫn tính
tiền suốt thời gian pod tồn tại**.

Số đo hoá đơn (`runpodctl billing pods`, 3 phiên RTX 5090 trong 02-07/08/2026): giá trọn gói
**~$1,003-1,006 mỗi giờ** cho RTX 5090 + container disk 100 GB. Trừ đi $0,99 GPU khai trong `.env`,
phần đĩa còn **~$0,013-0,016/giờ** — nhỏ khi pod đang chạy, nhưng nó chạy **24/24 suốt thời gian
pod tồn tại**, kể cả lúc dừng. Với nhịp dùng 0,85 giờ/ngày thì đó là khoản trả cho 23,2 giờ không
dùng mỗi ngày.

`gpu-down` vẫn còn chỗ dùng: nghỉ ngắn trong ngày và sẽ quay lại, khi 5 phút dựng lại đắt hơn vài
giờ tiền đĩa. Nó cũng **tự dump trước khi dừng**, nên không mất gì.

---

### Giai đoạn 3 — dọn

```bash
make gpu-destroy    # xoá pod, verify nó chết thật, rồi TỰ dọn .env
```

Ba khoá `GPU_INSTANCE_ID` / `GPU_SSH_HOST` / `GPU_SSH_PORT` được `scripts/env-clear-pod.sh` xoá
**tự động**, và chỉ ở nhánh đã verify pod biến mất thật. Script giữ nguyên dòng `KEY=` (xoá cả
dòng sẽ làm `gpu-preflight` báo "thiếu khoá" thay vì "chưa có pod"), giữ nguyên quyền file, ghi
qua file tạm rồi `mv` nguyên tử, và **từ chối ghi đè** nếu kết quả lệch số dòng so với bản gốc —
`.env` giữ `POSTGRES_PASSWORD` và `API_KEY`, một lần ghi hỏng ở đây đắt hơn nhiều so với xoá tay.

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

Chạy nhiều job một lượt thay vì bấm UI từng cái: xem §"Chạy lô material" trong `README.md`.
Runner tự `make gpu-up` nếu pod đang dừng, nhưng **không bao giờ tự `gpu-destroy`** —
và đặc biệt không destroy khi có run hỏng, vì đó đúng là lúc cần pod để đọc `pm2 logs worker`.

## Frontend on the pod

Mặc định FE chạy local, nghĩa là **tắt máy bạn là không ai vào được app**. Muốn đưa cho người
dùng thật thì cho FE chạy luôn trên pod: điền `FE_DOMAIN` (và `FE_PORT`, mặc định 2030) vào
`.env`, thêm `https://<FE_DOMAIN>` vào `CORS_ORIGINS`, chạy `make gpu-bootstrap` (dựng ingress FE
trên tunnel), rồi `make gpu-fe` (build + deploy chính FE lên pod).

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
| `make gpu-bootstrap` | backend + tunnel 2 hostname (KHÔNG deploy FE) | lần đầu ~30 phút |
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

**Số từ HOÁ ĐƠN, không phải `currentSpendPerHr`** (`runpodctl billing pods`, 3 phiên RTX 5090 trong 02-07/08/2026):

| Ngày | Thời gian | Tiền | $/giờ trọn gói |
|---|---|---|---|
| 02/08 | 89,2 phút | $1,4915 | 1,003 |
| 06/08 | 36,5 phút | $0,6102 | 1,003 |
| 07/08 | 12,8 phút | $0,2145 | 1,006 |

Trừ $0,99 GPU khai trong `.env`, phần container disk 100 GB còn **~$0,013-0,016/giờ**. Nhỏ khi
đang chạy — nhưng nó tính **suốt thời gian pod TỒN TẠI**, kể cả lúc dừng, còn GPU thì không. Đó là
toàn bộ lý do [`gpu-destroy` là mặc định khi xong việc](#destroy-first), không phải `gpu-down`.

Lưu ý cách đọc: hai con số trên là hoá đơn thật chia cho thời gian; phần tách riêng cho đĩa là
**suy ra** bằng cách trừ giá GPU, không phải một dòng riêng trong hoá đơn.

<a id="pod-max-hours"></a>
### Lưới chống quên tắt pod: `POD_MAX_HOURS`

Nhịp dùng thật đo 24/07 → 07/08 (15 ngày): **0,85 giờ/ngày ≈ $25/tháng**. Nhưng **một** lần quên tắt để pod
chạy cả tháng là **$713** — gấp 28 lần. `POD_MAX_HOURS` (mặc định **8**) truyền `--stop-after` vào
`runpodctl pod create`, nên một lần quên tốn **~$8** thay vì $713.

**`--stop-after`, không `--terminate-after`** — vẫn là lựa chọn có chủ ý, dù cái giá của `terminate`
đã đổi từ 07/08/2026. Cơ chế không đổi: cả hai đều dừng tiền GPU, nhưng `terminate` **xoá pod**
(gồm container disk), còn `stop` giữ nguyên container disk nên `make gpu-up` bật lại được ngay với
DB còn nguyên. `VOLUME_PGDATA=0` (mặc định, vì MooseFS chặn `chown`) nghĩa là PGDATA vẫn nằm trên
container disk đó — `terminate` vẫn xoá nó.

Cái mất đã đổi. Trước đây `terminate` là mất SẠCH database, không có gì để dựng lại. Từ khi có
[`pod-pgdump.sh`](#pg-backup), `gpu-destroy` cố dump lần cuối trước khi xoá, và pod mới dựng lên sẽ
tự khôi phục từ bản dump gần nhất trên volume — nên cái mất tối đa giờ chỉ còn là **metadata ghi
sau lần dump cuối cùng** (users, jobs, workflows, api keys mới tạo giữa hai lần dump). Media (ảnh,
video output) không nằm trong dump này — MinIO cũng ở trên volume, không phụ thuộc PGDATA, nên
không mất.

Vẫn giữ `--stop-after`: mất metadata từ lần dump cuối vẫn là **mất thật**, không phải mất-nhưng-
không-sao. Một lưới chống-quên-tắt-pod không nên đặt cược vào việc bạn đã nhớ `make gpu-db-dump`
hay chưa trước khi để nó xoá pod.

Đổi lại, và phải nhớ: **pod đã dừng vẫn tính tiền container disk.** Lưới này chặn khoản đắt (GPU
$0,99/giờ), không chặn hết. Dọn hẳn vẫn là `make gpu-destroy`.

`make gpu-preflight` in ra mốc này trong khối **Pod rental**, kèm số tiền một lần quên sẽ tốn. Đặt
`POD_MAX_HOURS=0` để tắt lưới — preflight sẽ cảnh báo vàng thay vì im lặng.

**Không áp được cho `COMPUTE_TYPE=cpu`**: `POST /v1/pods` không có field auto-stop (đã đọc
`openapi.json`). Rủi ro ở đó nhỏ hơn nhiều — box CPU để quên cả tháng ~$50 so với $713 — và
`pod-provision.sh` nói rõ điều này ngay trong dry-run thay vì để bạn tưởng có lưới.

<a id="hoa-don-that"></a>
### Hoá đơn thật, không phải số suy ra — sửa 04/08/2026

`currentSpendPerHr` là **tốc độ tiêu tiền tại một thời điểm**, không phải số tiền đã trả. Đối chiếu
với hoá đơn (`runpodctl billing pods` · `billing serverless` · `billing network-volume`):

| Khoản | Hoá đơn thật |
|---|---|
| 9 dòng hoá đơn pod, 24/07 → 07/08 | **$12,62** cho 12,70 giờ — riêng RTX 5090: $12,51 / 12,46 h = **$1,004/giờ**, ổn định |
| Serverless, cả ngày chạy thử 02/08 | **$0,3894** cho **884 giây** được tính |
| Volume 100GB | $0,00972/giờ → **~$7,10/tháng** |
| **Tổng đã tiêu** (pod + serverless + volume) | **$14,61** |

Cập nhật 08/08/2026. Serverless **vẫn chỉ có đúng một dòng hoá đơn** (02/08) — không chạy thêm lần
nào từ đó, nên mọi con số serverless dưới đây vẫn đứng trên nền mẫu 884 giây. Bảng này chốt ở các
ngày **đã đóng**; hoá đơn 08/08 còn đang chạy nên không tính vào.

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
| 0 (**all-in — số phòng thủ được nhất**) | 1,004 | 1,586 | **1,58×** |
| $0,0000108/GB-h | 0,991 | 1,559 | 1,57× |
| $0,0000972/GB-h (đơn giá volume đo được) | 0,888 | 1,348 | 1,52× |
| $0,000300/GB-h | 0,648 | 0,853 | 1,32× |

**Serverless đắt hơn pod 1,32–1,58× cho mỗi giây GPU.** Mỏ neo độc lập: hoá đơn pod all-in
**$1,004/giờ** khớp giá niêm yết **$0,99** (`gpuTypes.securePrice` cho RTX 5090) cộng chút đĩa — nên
cách đọc hoá đơn đúng, không phải đọc nhầm cột.

Cột pod tính trên **7 dòng 5090** (24/07 → 07/08); cột serverless vẫn là một dòng 02/08. Thêm 5 giờ
pod so với lần đo trước không làm tỉ lệ nhúc nhích (1,580× → 1,580×) — đó là dấu hiệu đơn giá pod đã
hội tụ, còn phía serverless thì chưa có dữ liệu mới để hội tụ.

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

<a id="gpu-4090"></a>
## Đổi GPU: 4090 dùng được, A40 không — đo 10/08/2026

Câu hỏi là "5090 có phải lựa chọn duy nhất không". Trả lời bằng phép đo trên pod thật, không suy luận.

### Ràng buộc quyết định trước cả VRAM: volume khoá EU-RO-1

Volume nằm ở EU-RO-1 và **datacenter không dời được tại chỗ** (di chuyển được, nhưng phải tạo
volume mới + rsync — xem [Thu nhỏ hoặc đổi datacenter volume](#volume-migrate)). Pod khác
datacenter thì không mount được → GPU nào không có mặt ở EU-RO-1 là loại, bất kể nó tốt đến đâu.
**RTX 5090 chỉ tồn tại ở đúng 2 datacenter trên toàn RunPod: EU-RO-1 và EU-CZ-1** (`get-gpu-type`,
product=POD, đo 29/08/2026) — hết hàng ở EU-RO-1 thì EU-CZ-1 là phương án dự phòng duy nhất, không
phải "tìm region nào còn hàng".

```bash
runpodctl gpu list -o json | python3 -c 'import sys,json
for g in json.load(sys.stdin):
    if g["gpuId"] in ("NVIDIA GeForce RTX 5090","NVIDIA GeForce RTX 4090","NVIDIA A40"):
        d = {x["dataCenterId"]: x["stockStatus"] for x in g.get("dataCenterAvailability") or []}
        print(g["gpuId"], g["memoryInGb"], "GB —", d.get("EU-RO-1","KHÔNG CÓ MẶT"))'
```

Đo 10/08/2026:

| | EU-RO-1 | $/giờ secure | VRAM |
|---|---|---|---|
| RTX 5090 | **Low** (01/08 còn Medium) | $0,99 | 32 GB |
| RTX 4090 | **High** | $0,74 | 24 GB |
| A40 | **không có mặt** (chỉ EU-SE-1 High, CA-MTL-1 Low) | $0,44 | 48 GB |

**A40 loại**, hai lý do độc lập: không có ở EU-RO-1, **và** Ampere không có fp8 native nên model
`Wan2_2-Animate-14B_fp8_e4m3fn_scaled` + `base_precision=fp16_fast` phải dequant sang fp16. Lý do
thứ hai đúng kể cả khi bạn tạo volume mới ở EU-SE-1.

### 4090 24GB: đo thật, chạy được, thừa 40% VRAM

Chạy đúng job đã dùng để đo 5090 (dựng từ `build_wan_workflow`, 544×960 / 453 frame /
`block_swap=30` / `offload_device`):

| | 5090 (32 GB) | 4090 (24 GB) |
|---|---|---|
| **VRAM đỉnh** | chưa đo | **14 390 / 24 081 MiB = 60%** (trống 9,7 GB) |
| cgroup RAM | 55,9 GiB (v2) | **56,8 GiB (v1)** |
| `anon` đỉnh | 17,8 GiB | 24 GiB |
| Giây/cửa sổ | 48 s (12,10 s/it) | **71 s (17,86 s/it)** |
| **Cả job** | **443,75 s** | **656 s = 1,48×** |
| `memory.failcnt` | — | **0** |
| Video ra | có | có |

Hai điều phép đo bắt được mà suy luận sai:

- **`minMemory` của API không phải RAM bạn nhận.** API báo 4090 EU-RO-1 `minMemory: 46` → tưởng
  cgroup 42,8 GiB. Máy thật cấp **56,8 GiB**, nhiều hơn cả 5090. `minMemory` là mức thấp nhất trong
  các offer. Cách duy nhất biết chắc là `cat /sys/fs/cgroup/memory/memory.limit_in_bytes` trên pod.
- **VAE decode KHÔNG phải đỉnh VRAM.** Tưởng decode 453 frame là chỗ vọt; thực tế VRAM tụt từ
  14,3 GB xuống 2 GB ngay khi decode chạy. Đỉnh nằm ở sampling.

Pod 4090 này dùng **cgroup v1** (`/sys/fs/cgroup/memory/memory.limit_in_bytes`) còn pod 5090 là v2
(`/sys/fs/cgroup/memory.max`) — nên nhánh fallback v1 của `scripts/pysite/sitecustomize.py`
([#cgroup-comfyui](#cgroup-comfyui)) đã được kiểm trên phần cứng thật. Đừng giả định layout cgroup
theo provider; đọc cả hai đường.

### Cái bẫy: 24GB chạy tốt clip DÀI, OOM clip NGẮN

Nghịch lý, và dễ hiểu sai thành "4090 không chạy được Wan". `_vram_ok` trong `build_wan_workflow`
chọn đường **chỉ theo resolution + số frame**, không biết card có bao nhiêu VRAM:

```python
_vram_ok = (max(W,H) <= MOTION_VRAM_MAX_EDGE and _fr <= MOTION_VRAM_MAX_FRAMES)
(_def_bs, _def_ld) = (0,"main_device") if _vram_ok else (30,"offload_device")
```

Ngưỡng mặc định (968 / 250 frame) đo trên 5090: 544×960/241f = đỉnh 29,9/32 GB. Trên 24 GB thì:

- clip **dài** (>250 frame) → tự đi offload → **chạy tốt** (số đo ở trên)
- clip **ngắn** (≤250 frame) → chọn `main_device` → cần 29,9 GB → **CUDA OOM**

Nên `motion_autoset_vram_gate()` trong `setup/lib-gpu.sh` chốt việc này một lần lúc setup, theo
`nvidia-smi` chứ không theo bảng cứng: VRAM < 31 GB thì `set_kv MOTION_VRAM_MAX_FRAMES 0` (ép offload
mọi clip), ≥ 31 GB thì trả về 250. Ghi tường minh **cả hai chiều** để pod từng bị ép 0 mà nay đổi
sang card to thì tự lấy lại đường nhanh. Ghi đè tay: đặt `MOTION_VRAM_MAX_FRAMES` trong `.env` gốc.

> ⚠ Trường hợp clip-ngắn-trên-24GB là **suy ra** từ số 29,9 GB đã đo, **chưa chạy thử**. Chênh 6 GB
> nên gần như chắc chắn OOM, nhưng nếu bạn thấy nó chạy được thì hạ `MOTION_VRAM_MODEL_ON_GPU_MIB`.

### Tiền: rẻ hơn 25%/giờ nhưng đắt hơn 10%/job — ngưỡng là 70%

- Mỗi job: 5090 = $0,99 × 443,75/3600 = **$0,1220** · 4090 = $0,74 × 656/3600 = **$0,1349**
- Gọi `f` = tỉ lệ thời gian thuê mà GPU thật sự chạy job. Phiên 5090 dài `T`, phiên 4090 dài
  `T(1 + 0,48f)` vì job chậm 1,48×. Rẻ hơn khi `0,74(1 + 0,48f) < 0,99`:

> **f < 70% → 4090 rẻ hơn. f > 70% → 5090 rẻ hơn.**

Nhịp dùng thật (0,85 giờ/ngày, và pod GPU được chọn chính vì "job chạy ngay" tức GPU rỗi nhiều) cho
`f` khoảng 0,3-0,5 → 4090 rẻ hơn 7-14%, tức **$2-4/tháng**. Không đáng đổi vì tiền.

**Kết luận: giữ 5090 làm chính, 4090 là lựa chọn đã đo chạy được khi cần đổi tay.** Lý do là *stock*,
không phải giá — mất 3,5 phút mỗi video để tiết kiệm $3/tháng thì không đáng, nhưng khi EU-RO-1 hết
5090 thì đã biết chắc 4090 chạy được.

`pod-provision.sh` **không còn tự xoay GPU** (đã bỏ `GPU_FALLBACK`) — hết máy `$GPU` là dừng luôn,
không âm thầm đổi card. Muốn dùng 4090 thì tự đặt `GPU=NVIDIA GeForce RTX 4090` trong `.env` rồi
chạy lại `make gpu-provision`; nhớ card <31GB cần `MOTION_VRAM_MAX_FRAMES=0` (setup tự dò qua
`nvidia-smi`, xem phần trên).

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

Không. `setup-motion-transfer.sh` (hàm `phase_dotenv()` trong `setup/lib-feature.sh`) tự sinh **toàn bộ**
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
| PGDATA nằm trên volume | ❌ không thể (MooseFS chặn `chown`) |
| DB sống qua `gpu-destroy` | ✅ dựng lại từ bản `pg_dump` trên volume — mất phần ghi SAU lần dump cuối |
| Model 33GB | ✅ vẫn trên volume, vẫn không phải tải lại |

PGDATA vẫn ở container disk và `gpu-destroy` vẫn xoá nó. Thứ sống sót là một bản sao **logic**
(`pg_dump`) đặt trên volume — ghi file thường lên MooseFS thì bình thường, chỉ `chown` mới bị
chặn. `setup/pod-pgdump.sh` là nơi duy nhất biết bố cục thư mục dump; đừng ghi backup vào chỗ
khác, `--restore` sẽ không thấy.

Đã tự động hoá, **không cần gõ `pg_dump` tay**:

```bash
make gpu-db-dump     # sao lưu ngay bây giờ
make gpu-db-check    # bản mới nhất bao lâu rồi + NẠP THỬ vào một DB tạm rồi so số dòng
make gpu-down        # tự dump trước khi dừng pod
make gpu-destroy     # cố dump lần cuối trước khi xoá
make gpu-bootstrap   # trên pod MỚI: tự khôi phục, nhưng CHỈ khi DB đang trống
```

Ba điều cần biết trước khi tin vào nó:

- **Không có cổng chặn nào.** Dump hỏng thì `gpu-down`/`gpu-destroy` in cảnh báo rồi **vẫn chạy
  tiếp** — không để một backup hỏng giữ lại một pod đang tính tiền theo giờ. Nghĩa là bạn phải
  ĐỌC output, không chỉ nhìn nó chạy xong.
- **`--restore` chỉ nạp khi schema `public` không có bảng nào.** DB đã có bảng thì nó bỏ qua và
  không nạp đè. Muốn khôi phục lên một DB đang có dữ liệu thì phải tự quyết và tự làm.
- **`make gpu-db-check` là bằng chứng, `ls` thì không.** Một file `.sql.gz` rỗng vẫn là một file.
  `--verify` nạp thật vào DB tạm rồi so số dòng với `.meta`, nên nó chứng minh chứ không ghi nhận.
  Chạy nó **trước** `gpu-destroy`, không phải sau.

Lớp 6 của `make gpu-smoke` chạy đúng hai lệnh này và làm smoke **đỏ** nếu đã đặt `POD_VOLUME` mà
không có bản dump nạp được.


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

> **Hình dạng đang dùng, chốt 04/08/2026: pod GPU + `local`.** motion-clone hiện là **tool dùng
> riêng, bật theo phiên làm việc**, không phải service 24/7. Đã dựng và đo cả hình dạng box CPU —
> nó chạy được — nhưng chọn pod GPU vì ba lý do **không phải tiền**: throttle serverless đã gặp
> thật (job nằm `IN_QUEUE` vô hạn, mà đang ngồi dùng thì chờ vô hạn tệ hơn trả thêm), cold start
> ~155 giây cho job đầu mỗi phiên, và mỗi box mới phải đồng bộ `WORKER_TOKEN` sang template.
>
> Ngưỡng tiền nếu cần tính lại: box CPU rẻ hơn khi chạy **dưới ~2,6-3,7 job mỗi giờ bật máy** (GPU
> rỗi phần lớn thời gian). Ở 4 giờ/ngày: 5 job/ngày → $62 so với $119; 15 job/ngày → $120 so với
> $119; 26 job/ngày → $202 so với $119. Hình dạng cpu còn nguyên, đổi lại là ba dòng `.env`.

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

Đơn giá all-in serverless **$1,586/giờ** so với pod **$1,004/giờ** — serverless đắt hơn
**1,32–1,58×** cho mỗi giây GPU tuỳ cách tách tiền đĩa ([bảng độ bền](#premium-serverless)). Nó
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

| Flavor | RAM | Đĩa | Giá đo thật 04/08/2026 |
|---|---|---|---|
| `cpu3c × 2` (mặc định `runpodctl`) | 4 GB | 10 GB/vCPU | **$0,06/giờ** = $43/tháng |
| `cpu5c × 2` | 4 GB | 15 GB/vCPU | $0,07/giờ = $50/tháng |
| **`cpu5c × 4`** ← mặc định của repo | **8 GB** | 15 GB/vCPU | **$0,14/giờ = $101/tháng** |
| `cpu5g × 4` | 16 GB | 15 GB/vCPU | $0,184/giờ = $132/tháng |

Hệ số RAM theo hậu tố: `c` = vCPU × 2 (Compute) · `g` = × 4 (General) · `m` = × 8 (Memory).

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

**Đã đo build cần bao nhiêu** (04/08/2026, `/usr/bin/time -l npm run build` trên `motions/`):
**đỉnh 2,49 GB RSS**, 26 giây. Cộng ~0,7 GB của Postgres/MinIO/api đang chạy song song → **3,2 GB**.

Nên chọn thế nào:

| Box | Build 3,2 GB vừa không | Giá |
|---|---|---|
| 4 GB | 80% trần — **sát quá** | $0,06-0,07/giờ |
| **8 GB** (`cpu5c × 4`) | **40% trần, thoải mái** | **$0,14/giờ** |
| 16 GB | 20% trần — thừa | $0,184/giờ |

**Repo dùng `cpu5c × 4` = 8 GB.** Hạ từ `cpu5g × 4` (16 GB) tiết kiệm $31/tháng mà giữ nguyên 4 vCPU
nên build không chậm đi. Chọn 8 GB thay vì 4 GB là dựa trên số 2,49 GB đó, không phải phỏng đoán.

`pod-fe.sh` nay tự chốt `NODE_OPTIONS=--max-old-space-size` theo **cgroup limit**, không theo RAM
node tự thấy — vì trong container node thấy RAM của **host** (box CPU RunPod báo 755 GB), nên nó cho
heap phình tới hàng chục GB rồi bị OOM-killer của cgroup giết. Triệu chứng là `Killed` trần trụi,
không stack trace, không dòng nào nhắc tới RAM. Nhờ chốt này, box 4 GB (heap 2896 MB) **có thể** đủ —
nhưng chưa ai thử.

<a id="cgroup-comfyui"></a>
#### Cùng cái bẫy đó cắn ComfyUI — 10/08/2026

Đúng cơ chế trên, lần này ở pod GPU. Job motion 453 frame (544×960) chết **hai lần liên tiếp**, cách
nhau 12 phút, **cùng một điểm**: 16-17 giây sau khi bước sampling đầu bắt đầu.

```
03:38:33  Frames 0-81:  0%|  | 0/4     03:50:43  Frames 0-81:  0%|  | 0/4
03:38:50  ComfyUI boot lại từ đầu      03:50:59  ComfyUI boot lại từ đầu
```

**Không traceback, không "CUDA out of memory".** Python crash luôn để lại traceback — im lặng tuyệt
đối nghĩa là `SIGKILL` từ ngoài. Bằng chứng nằm ở cgroup, không nằm trong log ComfyUI:

```
/sys/fs/cgroup/memory.max     59999997952   trần THẬT của container: 55,9 GiB
/sys/fs/cgroup/memory.peak    60000010240   đỉnh chạm ĐÚNG trần
/sys/fs/cgroup/memory.events  max 18494  oom_kill 1     (rồi lên 2 sau lần chạy lại)
/proc/meminfo MemTotal        129429860 kB  RAM của HOST: 123 GiB
```

ComfyUI đọc RAM qua `psutil`, và `psutil` đọc `/proc/meminfo` = RAM host. Nó tự cấp cho mình gấp đôi:

| Chỗ | Công thức | Tưởng có | Thật sự có |
|---|---|---|---|
| Trần pinned memory | `model_management.py` `ram * 0.90` | 111 GiB | 50 GiB |
| Ngưỡng cache inactive | `main.py` `min(128, total_ram)` | 123 GiB | 56 GiB |
| RAM pressure cache | `caching.py` so `virtual_memory().available` | host còn 108 GiB rảnh | cgroup đã sát trần |

Hàng thứ ba là hàng giết: **RAM pressure cache không bao giờ kích hoạt** vì nó nhìn host chứ không
nhìn cgroup. Nó giữ nguyên umt5 encoder (11,4 GB), CLIP-vision, và tensor ảnh 453 frame của mọi node
(`VHS_LoadVideo` + 3 nhánh DWPose + face crop ≈ 15 GB), cộng 12,17 GB block-swap nằm **pinned**
(page-locked — kernel KHÔNG đòi lại được). Vượt 56 GB thì bị giết, không có bước cảnh báo nào.

Cấu hình block swap **không sai**: gate ở `linux.py` chọn offload vì 453 frame > 250, đúng như A/B
22/06. Vấn đề nằm ở phía RAM chứ không phải VRAM — lúc chết GPU mới dùng 4,8/32 GB.

**Chữa** — hai mũi, cùng một lý do, đều trong `comfy-start-native.sh`:

1. `scripts/pysite/sitecustomize.py` kẹp `psutil.virtual_memory()` theo cgroup, nạp qua `PYTHONPATH`
   (chỉ ComfyUI dính). Chữa tận gốc: cả ba hàng trong bảng trên đều tính từ `psutil`.
2. `--cache-none`. ComfyUI mặc định giữ output **mọi node** giữa các lần chạy — với box này gần như
   vô dụng vì đây là *worker*, mỗi job là prompt mới với video khác nên cache chẳng bao giờ trúng,
   chỉ ôm tensor. Model vẫn nằm nguyên trong RAM/VRAM (cache model là cơ chế khác) nên không phát
   sinh nạp lại model. A/B bằng `COMFY_CACHE_ARGS` trong `.env`.

Nhận ra ngay lần sau bằng ba dòng này lúc ComfyUI khởi động:

```
[sitecustomize] RAM kẹp theo cgroup: 55.9 GiB (RAM host 123.4 GiB — KHÔNG dùng)
[INFO] Total VRAM 32109 MB, total RAM 57220 MB        ← KHÔNG còn 126396
[INFO] Enabled pinned memory 51498.0                  ← KHÔNG còn 113756
[INFO] Disabling intermediate node cache.             ← KHÔNG còn "Using RAM pressure cache."
```

Còn nghi OOM-kill thì đọc `memory.events`, đừng đọc log ComfyUI: `oom_kill` tăng là chắc chắn.
Lưu ý `memory.peak` **không reset được** trong container RunPod (`Permission denied`), nên muốn đo
đỉnh của một lần chạy phải tự lấy mẫu `memory.current`.

**Nghiệm thu 10/08/2026** — chạy lại ĐÚNG job đã chết hai lần (544×960, 453 frame, `block_swap=30`,
`offload_device`, dựng bằng chính `build_wan_workflow`):

| | Trước | Sau |
|---|---|---|
| Cửa sổ sampling | chết ở cửa sổ 1 sau 16-17 giây | **6/6 xong**, 48 giây/cửa sổ |
| Kết quả | không có gì | `Prompt executed in 443.75 seconds` + video ra file |
| `oom_kill` | 1 → 2 (mỗi lần chạy +1) | **đứng nguyên ở 2** |
| `anon` (RAM không thu hồi được) | phình tới khi bị giết | **17,8 GiB** suốt lượt chạy |

<a id="cgroup-99pct"></a>
**Đừng hoảng khi thấy `memory.current` = 99% trần.** Lúc chạy nó dính 55/55,9 GiB, nhưng phân rã
`memory.stat` cho thấy phần lớn là page cache **thu hồi được**, không phải RAM thật:

```
anon           17,8 GiB   ← chỉ chỗ này mới là RAM không đòi lại được
file           37,3 GiB   ┐ page cache đọc file model 18,4 GB —
inactive_file  19,6 GiB   ┘ kernel lấy lại bất cứ lúc nào
```

Linux giữ page cache tới khi cần chỗ; 99% với 19,6 GiB `inactive_file` là bình thường. Muốn biết
thật sự còn bao nhiêu margin thì đọc `anon`, đừng đọc `memory.current`.

**Sau khi job xong ComfyUI vẫn restart một lần — đó là CHỦ Ý, không phải hỏng.** Worker có watchdog
riêng: `ComfyUI idle ôm RAM 31GB ≥ 22GB → recycle`, SIGKILL rồi để PM2 dựng lại (~12 giây). Nhìn
`pm2.log` sẽ thấy `exited with code [0] via signal [SIGKILL]` — phân biệt với OOM-kill bằng
`memory.events`: recycle thì `oom_kill` KHÔNG tăng.

<a id="fe-build-ci"></a>
#### Build FE ở CI — đã làm, và nó xoá hẳn câu hỏi RAM

`FE_BUILD=ci` (mặc định) không build gì trên pod. `.github/workflows/build-frontend.yml` build
`motions/` trên `ubuntu-latest`, `pod-fe.sh` tải artifact về rồi rsync `.output` lên pod.

| | `FE_BUILD=pod` | `FE_BUILD=ci` |
|---|---|---|
| Trên pod | `npm install` + `npm run build` | rsync **31 MB** |
| Đỉnh RAM trên pod | 2,49 GB | **0** |
| `make gpu-fe` | ~2-4 phút | **84 giây** (đo 04/08) |
| Box CPU đủ dùng | 8 GB ($0,14/giờ) | **4 GB ($0,07/giờ)** |
| `node_modules` trên pod | ~290 MB | **không có** |

**Đã chạy thật 04/08/2026** trên box `cpu5c × 2` = 4 GB, **$0,07/giờ**:

| Đo | Kết quả |
|---|---|
| `sharp` nạp được trên pod Linux | ✅ `require('./.output/server/node_modules/sharp')` OK |
| **`API_KEY` theo pod, inject lúc chạy** | ✅ `GET app.…/api/motion/jobs` → **HTTP 200** |
| RAM | **1929 / 3815 MB (51%)** |
| Đĩa container | 1,6 / 30 GB (6%) — thoải mái vì không có `node_modules` |
| `motions` RSS | 76 MB |
| `make gpu-fe` | 84 giây (tải artifact + rsync 31 MB + chờ tunnel trả lời) |

Mục thứ hai là mục quan trọng nhất: `server/api/motion/jobs.get.js:4` đọc `useRuntimeConfig()` rồi
gọi backend bằng header `X-API-Key`. Route đó trả 200 nghĩa là **một artifact build sẵn ở CI dùng
được với `API_KEY` sinh riêng cho từng pod** — điều tưởng sẽ chặn cả hướng, nay đã chứng minh chứ
không còn là suy luận.

> **84 giây, không phải ~15 giây như từng ghi ở đây.** Tải artifact + rsync 31 MB + vòng chờ
> `https://$FE_DOMAIN` trả lời đều tính vào. Vẫn nhanh hơn build trên pod 2-3 lần, nhưng con số cũ
> là suy đoán.

**Vì sao CI được mà máy dev thì không** — đây là chỗ comment `pod-fe.sh:13` nói đúng nhưng chỉ đúng
một nửa. Nitro **nhúng** sharp vào `.output/server/node_modules/@img/`. Build trên macOS ra
`sharp-darwin-arm64`, copy sang pod Linux x64 là chết lúc chạy. `ubuntu-latest` là linux-x64, đúng
kiến trúc pod — nên cùng cơ chế nhúng đó lại thành ưu điểm. Workflow có cổng kiểm bắt buộc thấy
`@img/sharp-linux*` trong `.output`, và `pod-fe.sh` kiểm lại lần nữa trước khi rsync.

**API key theo từng pod vẫn hoạt động** — đây là điều kiện tưởng sẽ chặn cả hướng mà lại không.
`nuxt.config.js:35` đưa cả ba giá trị qua `runtimeConfig`, tên biến khớp quy ước `NUXT_*`, và
`motions/.run.sh` nạp `motions/.env` **lúc khởi động** rồi `exec node .output/server/index.mjs`.
Nên một artifact build sẵn dùng được với `API_KEY` sinh riêng cho từng pod. App là SSR nên client
nhận `public` từ payload server, tức giá trị runtime thắng giá trị baked lúc build.

Hai cổng chặn trong `pod-fe.sh`:

- `motions/` có thay đổi **chưa commit** → chặn. Artifact build từ commit nên không chứa chúng, và
  deploy ra một bản FE khác cái bạn đang sửa là lỗi mất hàng giờ mới nhận ra.
- Không có run **thành công** nào mà `motions/` khớp HEAD → chặn, kèm ba cách sửa. `pod-fe.sh` so
  **cây** (`git diff --quiet <headSha-của-run> HEAD -- motions/`), không so đúng SHA — vì
  `build-frontend.yml` chỉ trigger theo `paths: [motions/**, ...]`, nên một commit chỉ chạm
  backend/infra không có run riêng cho nó dù `motions/` chưa đổi một byte từ lần build gần nhất. So
  SHA (cách cũ) thì `make gpu-fe` đỏ đúng vào lúc tài liệu này bảo chạy nó. Chọn được run của một
  commit khác HEAD thì script **in rõ** commit đó là gì và vì sao vẫn đúng (`motions/` giống hệt) —
  không âm thầm dùng artifact của commit khác, đó là loại "thành công giả" `pod-smoke.sh` cảnh báo.

Đang sửa FE mà chưa muốn push thì `FE_BUILD=pod bash scripts/pod-fe.sh`.

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

**Throttle DAO ĐỘNG, không phải chặn cứng.** Đo 04/08/2026 trong vòng vài phút: `throttled:3` →
`idle:3 ready:3 throttled:0` → `throttled:3` trở lại. Nên đường thoát số 2 (chờ) là thật, và kéo
theo hai điều thực dụng:

- Đừng kết luận endpoint hỏng từ **một** lần đọc `/health`. Đọc vài lần cách nhau vài phút.
- Job nằm `queued` trong lúc throttle **không mất** — dispatcher vẫn poll, và worker nhặt khi có chỗ.
  Nhưng nếu bạn destroy box giữa lúc đó thì worker tỉnh dậy sẽ không gọi được api. Nên **purge hàng
  đợi trước khi destroy**: `curl -X POST https://api.runpod.ai/v2/<ep>/purge-queue -H "Authorization: Bearer $KEY"`.

Cũng xác nhận được điều docs nói ở [§Costs](#costs): worker `idle`/`ready` của FlashBoot **không**
tính tiền — lúc `ready:3` thì `currentSpendPerHr` vẫn đúng bằng mức chỉ-có-volume ($0,0100).

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
| ~20-35 phút cài phần mềm | `setup-motion-transfer.sh` cài ComfyUI + torch + custom node từ đầu | `MTC_PREBUILT=1` — **đo 06/08/2026 trên pod GPU thật: còn 284 giây ≈ 4,7 phút** (pull image 16,20 GB + boot 176 giây + `make gpu-bootstrap` 108 giây, rc=0) |
| Mất users/jobs/workflows khi `gpu-destroy` | PGDATA nằm ở `/var/lib/postgresql` trên container disk — MooseFS chặn `chown` nên **không** dời được lên volume ([bẫy #2](#runpod-gotchas)) | `POD_VOLUME` + [`pod-pgdump.sh`](#pg-backup) — dựng lại từ bản `pg_dump`, mất phần ghi sau lần dump cuối |

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

<a id="volume-migrate"></a>
### Thu nhỏ hoặc đổi datacenter volume — không resize tại chỗ được

RunPod chỉ cho **tăng** dung lượng (`network-volume update` báo lỗi nếu size mới nhỏ hơn cũ) và
không có API đổi datacenter. Muốn nhỏ hơn hoặc ở DC khác thì phải: tạo volume mới → chuyển dữ liệu
→ xoá volume cũ. Không có đường tắt resize.

**Trạng thái thật hiện tại (đo 29/08/2026):** volume `motion-100` (`u469c9efga`), 100GB, EU-RO-1,
đang dùng ~79GB. Trước đó là volume `motion` (`wfe86wzkpm`) 150GB (nâng từ 100→150GB giữa tháng
8/2026) — sau khi dọn 2 model group không còn dùng (engine character-swap `scail2` ~24,6GB và
`ollama-models` 10GB, tổng ~34GB) thì 100GB là đủ, nên migrate xuống để giảm hoá đơn.

**Quy trình di chuyển.** Một pod chỉ mount được **1** network volume (`mounts.network` trong
`openapi.json` của RunPod ghi rõ `maxItems: 1`), nên bắt buộc qua 2 pod:

1. Tạo volume mới đúng DC muốn chuyển tới (`create-network-volume`).
2. Dựng 2 pod CPU tạm song song (~$0,06-0,07/giờ mỗi pod, `COMPUTE_TYPE=cpu`): pod A gắn volume
   CŨ, pod B gắn volume MỚI. `pod-provision.sh` + `.env` chỉ track được 1 pod tại một thời điểm —
   theo dõi SSH info của pod B riêng bằng `runpodctl ssh info <podId> -o json`.
3. Tạo cặp khoá SSH tạm (`ssh-keygen -t ed25519`), gắn public key vào `~/.ssh/authorized_keys` của
   pod B, copy private key sang pod A. `rsync -a` THẲNG pod A → pod B qua IP public — **đừng** đi
   qua laptop làm trạm trung chuyển (chậm hơn nhiều, băng thông pod-pod cao hơn hẳn đường nhà).
4. Verify bằng `rsync -avnc` (dry-run + checksum) — phải ra **0 file** cần đồng bộ trước khi tin
   dữ liệu đã khớp, rồi mới xoá gì.
5. Xoá 2 pod tạm. Sửa `.env` (`POD_VOLUME_ID`) **và endpoint Serverless** (`networkVolumeIds` +
   `dataCenterIds` nếu đổi luôn DC) sang volume mới — quên endpoint thì worker serverless vẫn chạy
   nhưng gắn volume cũ.
6. Xoá volume cũ (`delete-network-volume`).

**Tốc độ đo thật (29/08/2026), không phải suy luận:**

| Chặng | Luồng | Công cụ | Tốc độ | 79GB mất |
|---|---|---|---|---|
| Cùng DC (EU-RO-1→EU-RO-1, lúc thu nhỏ 150→100GB) | 1 | `rsync` | **~500MB/s** | ~3 phút |
| Khác DC (EU-RO-1→EU-CZ-1) | 1 | `rsync` | ~57MB/s | ~24 phút |
| Khác DC | 4 | `rsync` | ~94MB/s (1.6×) | ~14-15 phút |
| Khác DC | 1 | `dd\|ssh cat` (bỏ protocol rsync) | ~57MB/s | khớp rsync |
| Khác DC | 4 | `dd\|ssh cat` | **~184MB/s (3.2×)** | gấp ~2× rsync cùng số luồng |
| Khác DC | 8 | `dd\|ssh cat` | **~427MB/s (7.5×)** | **~3 phút** |
| Khác DC | 16 | `dd\|ssh cat` | ~447MB/s, nhưng **3/16 kết nối lỗi** | không nên dùng |

Nén trước khi sync (`rsync -z`/tar+gzip) **không đáng thử**: phần lớn dữ liệu là tensor
`.safetensors`/`.gguf`/`.ckpt` (fp16/fp8) — entropy cao, nén được dưới 5%, trong khi pod CPU tạm
(2 vCPU) yếu, nén tốn CPU nhiều khả năng lỗ hơn lãi.

**Phát hiện quan trọng: nghẽn ở phép đo 4-luồng đầu tiên là CPU của `rsync`, không phải mạng.**
Cùng 4 luồng, `dd|ssh cat` (gần như không tốn CPU checksum) nhanh gấp đôi `rsync` (184 vs 94MB/s)
— vì pod tạm chỉ 2 vCPU, 4 tiến trình `rsync` tranh CPU checksum/protocol trước khi kịp bão hoà
đường truyền. Trần thật của link cao hơn nhiều: **~430MB/s**, chạm được ở 8 luồng.

**Đừng vượt quá ~8 kết nối SSH mới đồng thời tới cùng 1 pod đích.** Ở 16 luồng, sshd phía nhận từ
chối 3/16 kết nối ngay lúc bắt tay (`kex_exchange_identification: read: Connection reset by peer`)
— giới hạn `MaxStartups` chống bão kết nối của OpenSSH, không phải lỗi mạng. 8→16 cũng gần như
không nhanh hơn (427→447MB/s, đã gần bão hoà), nên vượt 8 chỉ có rủi ro, không có lợi.

**Áp dụng cho migration thật:** vẫn nên dùng `rsync` (không dùng `dd` thô) để có resume + verify
checksum sẵn — nhưng muốn `rsync` chạm gần trần ~430MB/s thay vì bị CPU chặn ở ~94MB/s, phải tăng
vCPU cho pod tạm (vd 4 thay vì 2) trước khi chạy song song. Giới hạn ở **~8 luồng** (mỗi luồng 1
thư mục con model — `diffusion_models`, `text_encoders`, `loras`, `checkpoints`…), đừng cao hơn.

**Bẫy: xoá volume cũ báo lỗi dù `list-pods` rỗng.** RunPod từ chối `delete-network-volume` với
`"You must remove this network volume from all pods before deleting it"` kể cả khi không còn Pod
nào chạy. Thủ phạm là **worker Serverless đang `THROTTLED`** (hết capacity GPU, xem [mục dưới về
`throttled`](#serverless-throttled)) vẫn giữ tham chiếu volume dù không hiện trong `list-pods`.
`purge-endpoint-queue` không giúp gì (chỉ xoá job đang chờ, không đụng worker). Cách gỡ:
`update-endpoint` đặt tạm `workersMax: 0` (giết hết worker throttled ngay lập tức), rồi trả lại
giá trị cũ — lúc đó `delete-network-volume` mới thành công.

**Vì sao không giữ sẵn volume dự phòng ở EU-CZ-1.** Với tần suất hết 5090 ở EU-RO-1 **hiếm** (vài
lần/tháng hoặc ít hơn — đo bằng cảm nhận thực tế, không phải số liệu RunPod), giữ volume dự phòng
thường trực tốn ~$7/tháng ([giá thật](#network-volume) ~$0,07/GB/tháng) **cộng** phải nhớ đồng bộ
lại mỗi lần đổi model ở volume chính — trong khi chờ ~25-30 phút di chuyển theo yêu cầu (có giới
hạn rõ ràng, khác hẳn kiểu chờ vô thời hạn của serverless `throttled`) chỉ xảy ra vài lần/tháng.
Quyết định: **on-demand**, không standing backup. Chạy xong việc thì xoá luôn volume tạm ở
EU-CZ-1 — output đã tải về laptop, không cần giữ lại data đã chạy.

<a id="pg-backup"></a>
### Sao lưu database (pg_dump sang volume) — nghiệm thu 07/08/2026

PGDATA vẫn ở container disk như [bẫy #2](#runpod-gotchas) giải thích — MooseFS chặn `chown` nên
Postgres không mở được cluster đặt trên volume. Thứ sống sót qua `gpu-destroy` là một bản sao
**logic**: `motions-studio/setup/pod-pgdump.sh` chạy `pg_dump` rồi ghi ra volume như file thường,
thứ MooseFS không cấm.

**Bố cục trên `$POD_VOLUME/pg/`:**

```
$POD_VOLUME/pg/
├── dumps/
│   ├── motion-20260807-083400.sql.gz
│   ├── motion-20260807-083400.meta      # created, pg_version, dump_bytes, rồi <bảng>=<số dòng>
│   ├── motion-20260807-083529.sql.gz
│   └── motion-20260807-083529.meta
└── latest -> dumps/motion-20260807-083529.sql.gz   # symlink tương đối, luôn trỏ bản mới nhất
```

Có thêm **một khoá tuỳ chọn**, chỉ xuất hiện khi cần: `meta_incomplete=1`. `--dump` ghi nó khi
parser gặp một dòng mở đầu bằng `COPY ` mà nó không xử được, và cờ đi cùng **file** chứ không chỉ
cùng lần chạy — cảnh báo trên terminal biến mất khi màn hình cuộn qua, hệ quả thì sống với bản
dump. Hai nguyên nhân, và chúng khác nhau ở chỗ quan trọng nhất:

| Nguyên nhân | `.meta` | `--verify` |
|---|---|---|
| identifier (tên bảng/cột) chứa ký tự **xuống dòng** — `pg_dump` phát header COPY vỡ làm nhiều dòng vật lý | **thiếu hẳn** bảng đó | **đỏ**, và đỏ vì `.meta` cụt chứ không phải vì file hỏng |
| một dòng SQL `pg_dump` chép nguyên văn (thân `CREATE FUNCTION`, `COMMENT ON`, định nghĩa view) trông giống header nhưng nháy kép không cân — `COPY jobs " FROM stdin;` | **đầy đủ** | **xanh** |

Nên `meta_incomplete=1` đọc là *"danh sách bảng CÓ THỂ thiếu"*, không phải *"chắc chắn thiếu"*.
`--check` không đọc lại file dump nên nó không phân biệt được hai trường hợp và nói đúng như vậy;
chỉ `--verify` mới trả lời dứt điểm. Cả hai trường hợp: **bản dump vẫn tốt và vẫn `--restore`
được** — cờ này không bao giờ là lý do để xoá một bản backup.

**Ba điểm gọi `--dump`, không cái nào định kỳ (không cron, không app PM2):**

1. **`make gpu-down`** — điểm chính. Đây là lúc **cuối cùng** còn SSH vào pod được trước khi dừng
   nó; mọi thay đổi sau mốc này (cho tới lần `gpu-db-dump` hoặc `gpu-down` kế tiếp) không nằm
   trong backup nào.
2. **`make gpu-destroy`** — cố dump lần cuối, best-effort, chỉ có tác dụng nếu pod còn sống (chưa
   bị `gpu-down` từ trước).
3. **`make gpu-db-dump`** — thủ công, gọi bất cứ lúc nào pod đang chạy.

**Khôi phục:** `phase_pg_restore()` (`motions-studio/setup/lib-feature.sh:877`), gọi từ
`feature_main()` ngay **sau `phase_postgres`** — dump chứa `ALTER TABLE ... OWNER TO` và `GRANT`
nên role + database phải tồn tại trước — và **trước `phase_pm2`**, vì api khởi động sẽ tự chạy
`migrate.js` tạo bảng rỗng; nạp dump vào DB đã có bảng là lỗi trùng. Chỉ nạp khi schema `public`
**không có bảng nào** — có bảng (kể cả 0 dòng) thì bỏ qua, không nạp đè.

**Kiểm:** `make gpu-db-check` chạy `--check` (tuổi bản dump, số bảng, số dòng trong `.meta`) rồi
`--verify` (nạp thật vào DB tạm `${PG_DB}_verify` và so số dòng). `ls` không chứng minh được gì —
một `.sql.gz` rỗng vẫn là một file; `--verify` là bằng chứng, không phải dấu vết.

**Không có cổng chặn nào.** Dump hỏng không ngăn `gpu-down`/`gpu-destroy` chạy tiếp — cố ý: chặn
việc dừng một pod đang tính tiền $0,99/giờ để "bảo vệ" một bản backup là đốt tiền thật, để đổi lấy
một rủi ro nhỏ hơn nhiều (mất phần ghi từ lần dump cuối, khôi phục lại được từ bản trước đó).

**Số đo thật, 07/08/2026, hai lần thuê pod RTX 5090 ở EU-RO-1, tổng ~25 phút:**

| | Pod 1 (`oqjoy0ri5dlsmo`) | Pod 2 (`rrywwstiq11q97`, dựng mới sau khi pod 1 đã bị xoá) |
|---|---|---|
| SSH sẵn sàng | 2 phút | 2 phút |
| `make gpu-bootstrap` | rc=0 · phase khôi phục: "chưa có bản dump nào trên volume — bỏ qua (đây là phiên đầu)" | rc=0 · "tuổi bản dump: 0 giờ (motion-20260807-083529.sql.gz)" → "✓ khôi phục xong từ motion-20260807-083529.sql.gz" |
| `make gpu-db-dump` | tạo `motion-20260807-083400.sql.gz`, **6656 bytes** | — |
| `make gpu-db-check` | "nạp lại được và số dòng khớp .meta (25 bảng)" | 25 bảng, verify xanh, **số bản đang giữ: 2** |
| `make gpu-down` | dump lần nữa (`motion-20260807-083529.sql.gz`) rồi dừng pod | — |
| `make gpu-destroy` trên pod đã dừng | `ssh: connect to host ... Connection refused` → `!! sao lưu lần cuối KHÔNG thành công (lý do ở ngay trên) — vẫn XOÁ pod theo yêu cầu.` → pod xoá, biến mất khỏi `runpodctl pod list` | — |
| Dữ liệu sau khôi phục | — | `users=2 · workflows=2 · workers=1 · api_keys=1 · task_cloud_auto_settings=1` |

Pod 2 không có gì chung với Pod 1 ngoài cùng một volume. Số dòng quay về đúng những gì Pod 1 đã ghi
trước khi bị xoá — chứng minh cả đường dây: dump ở `gpu-down` → `gpu-destroy` (dump lần cuối thất
bại vì pod đã dừng, vẫn xoá) → provision pod mới → `gpu-bootstrap` tự khôi phục từ bản dump mới nhất
còn đọc được (bản của `gpu-down`, vì bản của `gpu-destroy` không tạo được).

**Đã đo bổ sung, 08/08/2026 — pod thứ ba (`u8xnp8yw2n4p1w`), đường 9 lớp đầy đủ:**

Lệnh chạy — cả ba lớp GPU đều bật, không lớp nào bị bỏ qua:

```
SMOKE_REF=.smoke/nhanvat.jpeg SMOKE_DRIVER=.smoke/dandong.mp4 \
SMOKE_PRODUCT=.smoke/sanpham.jpeg SMOKE_PROMPT="a red car on a street" make gpu-smoke
```

| | |
|---|---|
| Kết quả | **9/9 lớp pass**, `smoke test passed`, exit 0 |
| Lớp 6 (sao lưu DB) | `bản dump mới nhất: motion-20260807-084148.sql.gz · 17 giờ tuổi · 6755 bytes` · `số bản đang giữ: 3` · `verify: nạp lại được và số dòng khớp .meta (25 bảng)` |
| Lớp 7 motion | Wan 2.2 Animate → mp4 286 KB |
| Lớp 8 tryon | Qwen-Image-Edit (tryon) + bg-remover → png 1203 KB |
| Lớp 9 create-image | Qwen-Image-Edit → png 2799 KB |

Lần chạy này còn kiểm được hai đường mà đợt 07/08 không chạm tới:

- **Khôi phục từ bản dump CŨ.** Pod 3 dựng mới hoàn toàn, `gpu-bootstrap` nạp bản dump 17 giờ tuổi
  còn trên volume từ hôm trước: `tuổi bản dump: 17 giờ` → `✓ khôi phục xong`. Đợt 07/08 bản dump
  chỉ 0 giờ tuổi nên đường in tuổi thật chưa được kiểm.
- **`--dump` trên volume bỏ qua `chmod` KHÔNG còn báo lỗi.** Lệnh dump trong `gpu-destroy` in ra
  dòng thông tin `… 666; dump vẫn tốt. Bảo mật ở đây dựa vào volume là riêng của tài khoản và pod
  đơn-người-thuê, không dựa vào mode.` thay vì cảnh báo `QUYỀN SAI` như đợt 07/08 — đúng hành vi
  mong đợi sau khi thêm phép dò `_fs_honors_modes` (xem tiểu mục ngay dưới).

#### Quyền file trên volume: `600` không đặt được

Đo thật 07/08/2026, trên volume `wfe86wzkpm` mount ở `/workspace`:

```
chmod 600 <file trên /workspace>  → stat vẫn 666
chmod 600 <file trên /tmp>         → stat 600
thư mục trên volume                → 777
mount: mfs#euro-3.runpod.net:9421 on /workspace type fuse (rw,nosuid,nodev,relatime,user_id=0,group_id=0,allow_other)
symlink(): CHẠY ĐƯỢC — latest -> dumps/motion-20260807-083529.sql.gz (tương đối)
```

MooseFS bỏ qua `chmod` hoàn toàn — y như nó đã chặn `chown` ([bẫy #2](#runpod-gotchas)). `chmod 600`
trả về `0` (không báo lỗi gì) nhưng không đổi mode thật, nên chờ `stat` ra `600` sau một lệnh
`chmod` là chờ một thứ không bao giờ tới trên volume này. `pod-pgdump.sh` dò việc này bằng
`_fs_honors_modes()` — tạo một file dò, `chmod 600`, `stat` lại — trước khi quyết: filesystem không
tôn trọng mode thì in **một dòng** thông tin và `--dump` vẫn trả `0`; filesystem CÓ tôn trọng mà
mode vẫn sai thì cảnh báo to + exit khác `0`. Test ở `motions-studio/setup/tests/pgdump-test.sh`
vẫn giữ nguyên assertion `700`/`600` — chạy trên APFS của máy dev, một filesystem tôn trọng mode,
nên hai nhánh không nuốt lẫn nhau.

Vì mode không đặt được, bảo mật của bản dump trên volume **không dựa vào quyền file** — nó dựa vào
volume là tài sản riêng của tài khoản RunPod (không tài khoản khác mount được) và pod là
đơn-người-thuê (không có tenant nào khác đọc chung filesystem này). Dump chứa dữ liệu nhạy cảm
(`api_keys`, `user_sessions.token_hash`, token `social_accounts`), nên đây là một đánh đổi có ý
thức, không phải sơ suất bỏ quên.

<a id="image-dung-san"></a>
### Image dựng sẵn (`MTC_PREBUILT=1`)

`motions-studio/worker-image/Dockerfile` — base `runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404`
(ship sẵn `/start.sh` sshd, không va [bẫy §1](#runpod-gotchas)) — bake ComfyUI lõi ghim commit +
9 custom node ghim SHA + `worker-venv` + `bg-remover-venv` + `api-node_modules` + Ollama vào
`/opt/mtc-prebuilt`. CI (`.github/workflows/build-prebuilt-image.yml`) build và push image lên
`ghcr.io/<owner>/motion-prebuilt`, tag `sha-<commit>` (và `latest` chỉ khi push `main`). Đặt
`POD_IMAGE=ghcr.io/<owner>/motion-prebuilt:sha-<commit>` — **ghim tag, không dùng `latest`** — và
`MTC_PREBUILT=1` trong `.env`. Hàm `phase_prebuilt_deps()` (`setup/lib-feature.sh`) symlink thẳng
vào đó và bỏ qua toàn bộ phần cài đặt.

Thiếu `/opt/mtc-prebuilt/.ready` là setup `die` ngay — cố ý, để bạn không âm thầm rơi về đường
cài-từ-đầu mà không biết.

**Số đo thật, 06/08/2026, pod RunPod thật** (image `sha-f17ef55`, nén 16,20 GB / 60 layer, profile
`full`):

| | |
|---|---|
| Pull image + boot container + sshd sẵn sàng | 176 giây |
| `make gpu-bootstrap` (backend, đường sạch không can thiệp tay), rc=0 | 108 giây |
| **Tổng đường đi sạch** | **284 giây ≈ 4,7 phút**, so với baseline 20-35 phút |
| torch trong image | `2.12.1+cu130` — `torch.cuda.get_device_capability()` ra `(12, 0)` trên RTX 5090 |
| `JOB_TYPES` thật (bị `JOB_TYPES_OVERRIDE` cắt từ 21 type của profile `full` xuống, vì model chưa tải đủ — xem `.env.example`) | 16 type |
| `make gpu-smoke` | 8/8 lớp pass — đo TRƯỚC khi thêm lớp sao lưu DB (khi đó tổng 8 lớp: motion/tryon/create-image là lớp 6/7/8 lúc đó). Sau khi Lớp 6 sao lưu DB chen vào, numbering hiện tại là **motion=7, tryon=8, create-image=9** (trigger bằng `SMOKE_PRODUCT`/`SMOKE_PROMPT`, xem [§Kiểm chứng](#smoke)); tổng đường 9 lớp **đã chạy đầy đủ 08/08/2026: 9/9 pass** (xem [§Sao lưu database](#pg-backup)) |

Hai điều tưởng đúng lúc thiết kế nhưng đo thật thì sai:

- **Pull image không phải nút thắt.** 16,20 GB kéo về trong ~3 phút — pod RunPod nằm trong
  datacenter, băng thông nội bộ lớn (đo được `maxDownloadSpeedMbps: 6674` trên một pod CPU cùng
  đợt), không phải băng thông Internet gia đình.
- **Image không ăn quota container disk.** Lo image giải nén 32-41 GB phải vừa chung `DISK=100`
  với OS và PGDATA là sai mô hình: layer image nằm ở tầng đọc-riêng (read-only) của overlayfs, chỉ
  tầng ghi (writable) mới tính vào quota container disk. Đo thật: `df -h /` báo `401M / 100G, dùng
  1%`; `du -sh /opt/mtc-prebuilt` = 9,0 GB.

---

<a id="smoke"></a>
## Character swap — đo thật trên RTX 5090, 21-22/08/2026

Phiên nghiệm thu job type `character-swap` (thay nhân vật trong video bằng người mẫu trong ảnh,
GIỮ background của video). Pod 5090 32GB tại EU-RO-1, image prebuilt, model đã preload sẵn.

| Engine | Preset | Khung | Frame | Độ dài ra | Thời gian render |
|---|---|---|---|---|---|
| `wananimate` (Wan2.2-Animate Mix) | drv-5s | 544×960 | 150 @30fps | **5.0s** | ~2 phút |
| `wananimate` | drv-15s | 544×960 | 450 @30fps | **15.0s** | ~7 phút |
| `scail2` (SCAIL-2 fp8_scaled) | drv-5s | 544×960 | 81 @30fps | **2.7s** | ~3,5 phút |

VRAM đỉnh đo bằng `nvidia-smi` lúc sampling: **25,3/31,4 GB** (wananimate 5s, 544×960/150f — SAM3
1,7GB nạp trước rồi nhả, không cộng dồn với Wan). Clip 15s (450f > trần 250f) rơi vào nhánh
offload của VRAM-gate: model xuống RAM, block_swap=30, VRAM chỉ còn **14,1 GB** — chậm hơn nhưng
an toàn. SCAIL-2 fp8_scaled nạp 16,8GB staged, vừa 32GB.

Clip 15s chạy 6 window (`Frames 0-81`, `80-161`, `160-241`, …), mỗi window ~40 giây. Kiểm frame
430/450 (14,3 giây): identity, trang phục và background **không trôi** — windowing autoregressive
của Wan giữ ổn định suốt clip.

**Trần 81 frame của scail2 là thật và thấy ngay ở output**: `WanSCAILToVideo` train theo chunk
81 frame, `build_scail2_swap_workflow` cap cứng ở đó, nên ở 30fps mọi clip đều ra đúng 2,7 giây
bất kể driver dài bao nhiêu. Muốn dài hơn phải nối segment (Base + Extend, overlap 5 frame) —
chưa làm. Vì vậy **wananimate là engine mặc định cho clip dài**; scail2 chỉ để A/B chất lượng.

Chất lượng (3 cặp ảnh×driver, xem bằng mắt): cả hai giữ đúng background + camera của video và
thay đúng identity/trang phục của ảnh mẫu. Khác biệt: wananimate **bịa thêm vật thể** trong tay
nhân vật ở một clip (bó hoa không có trong cả hai input) — nghi do `BlockifyMask` 32px nới mask
quá rộng quanh bàn tay; scail2 không dính lỗi này và nét mặt sạch hơn.

Cạm bẫy đã trả giá trong phiên này (đã vá, xem commit `b0b33bc`):

- **`ecosystem.config.cjs` là danh sách JOB_TYPES thứ SÁU** và `scripts/check-job-types.mjs` không
  đọc nó. Năm danh sách kia xanh hết mà worker vẫn dựng với list cũ → job `character-swap` nằm
  `queued` im lặng, không log, không lỗi. PM2 gắn cứng dòng đó và **cố ý không đọc `.env`**, nên
  sửa `JOB_TYPES` trong `.env` trên pod cũng vô ích. Nay gate đã nhìn cả file này.
- **rsync của `make gpu-bootstrap` treo cứng** khi upload từ máy dev về EU-RO-1 chậm (đo thật:
  ~28 KB/s, 15 phút đẩy được 1,2MB rồi đứng hẳn). Đường vòng nhanh hơn nhiều: push nhánh lên
  GitHub rồi `git clone` TRÊN POD (tải phía pod nhanh — cùng đường mà preload kéo 25GB trong 3
  phút), copy `motions-studio/.` vào `~/motion-backend/`, rồi chạy lại `make gpu-bootstrap` (rsync
  lúc đó không còn gì để truyền nên đi thẳng sang pod-volume + setup).

### Tỉ lệ khung của driver quyết định enhance nhanh hay chậm — đo 23/08/2026

Swap là job type ĐẦU TIÊN sinh ra video không phải 9:16: nó bám tỉ lệ driver (`fitDriver` mặc định
bật, vì `enforceMotionResolution()` và `normalizeMotionDriverSegment()` đều `return` sớm khi
`type !== "motion"`). Hệ quả không ai lường trước, đo trên 4 cặp ảnh×driver, clip 15s @30fps:

| Driver | Swap ra | Enhance 1080p ra | Pixel | Thời gian enhance |
|---|---|---|---|---|
| 576×768 (**3:4**) | 544×720 | 1450×1920 | 2,78 MP | **1995s · 2095s** |
| 576×1024 (**9:16**) | 544×960 | 1088×1920 | 2,09 MP | **200s · 228s** |

Chênh **13,7×** giữa hai clip cùng độ dài, cùng engine, cùng 30,7GB VRAM trống. Lý do: "1080p"
nghĩa là cạnh DÀI 1920, nên clip 3:4 thấp hơn phải phóng mạnh hơn (×2,67 so ×2,00) và nở ngang
thành 1450px. FlashVSR cần ~12,8GB mỗi MP đầu ra → 2,78 MP đòi ~35,7GB, tràn 30,5GB trống, node
tự lùi sang tile 6 mảnh và trả giá **cho từng chunk**: 2 lần thử hỏng (~70s phí) + tile ~6 phút.
Log ComfyUI đếm đúng 10 dòng `OOM detected` = 5 chunk × 2 lần, toàn bộ thuộc hai clip 3:4.

Khớp lại mốc cũ: job 2K ngày 16/08 (3,71 MP → ~47,6GB) lỗi sau 16,1 phút — cùng một cơ chế.

**Hai cần gạt, mỗi cái chữa một bệnh — đừng nhầm:**

- `quality: 720p` + `maxRenderEdge: 1280` (nay là **mặc định** của `run_character_swap`) chữa ĐỘ NÉT
  và TỈ LỆ, không chữa tile. Cạnh ngắn render 544→720 (+76% pixel Wan sinh tại chỗ); khung ra
  720×960 = 0,7500 **đúng** tỉ lệ driver 576×768, còn 544×720 = 0,7556 thì lệch. `maxRenderEdge`
  bắt buộc đi kèm: trần mặc định 968 kẹp driver 9:16 ngược về 544×960, thiếu nó thì 720p vô nghĩa
  với clip dọc.
- `MOTION_FLASHVSR_CHUNK` mới chữa TILE, và **mặc định đã đổi 100 → 50** (23/08,
  `ecosystem.config.cjs` + fallback trong `linux.py`; `.env` ghi đè được, pod-bootstrap chuyển tiếp).
  VRAM tỉ lệ với số frame mỗi chunk, chia đôi là lọt: 0 dòng OOM, chạy full-frame. Nghịch lý —
  chunk nhỏ hơn lại **nhanh hơn ~8×** vì tránh được tile. Lưu ý `MOTION_FLASHVSR_TILED=0` chỉ là ý
  định chứ không phải bảo đảm: thiếu VRAM thì node TỰ bật tile, đúng cái giá mà dòng 24/07 sợ.
  Mặc định mới áp cho CẢ job motion — khung ra 2,09 MP vốn đã lọt ở 100 nên không được lợi gì, đo
  +28% ở một clip và −4% ở clip khác (ngược chiều ⇒ trong nhiễu). Chỉ chạy 9:16 và muốn ít mối nối
  nhất thì đặt lại `MOTION_FLASHVSR_CHUNK=100` trong `.env`.

Đo bản 720p + chunk 50 trên đúng 4 cặp đó: enhance 257s · 290s · 257s · 218s, và **cả lô tốn 66
phút GPU so với 107 phút** của bản 540p. Nét hơn mà rẻ hơn. Laplacian vùng mặt +95%/+36%/+17% trên
3 clip (clip thứ 4 −3%, nhưng thước toàn khung bị nền chi phối và hai bản là hai lần sinh độc lập
nên frame không trùng tư thế — cắt vào mặt thì cả 4 đều nét hơn thấy rõ: mi tách sợi, tóc ra sợi
lẻ, da có lỗ chân lông).

Chưa đo: chunk 50 có thêm mối nối nên về lý có thể thêm giật thời gian; với khung ra 2,09 MP (vốn
đã lọt ở chunk 100) hai phép đo cho +28% và −4%, ngược chiều nhau nên chưa kết luận được.

**Card <32GB**: 720×1280 × 450 frame nặng hơn baseline 25,3GB đã đo ở 544×960 — gửi `quality: 540p`.

## Kiểm chứng sau khi dựng: `make gpu-smoke`

`make gpu-status` chỉ curl `/health`. Mà `/health` là một handler tĩnh
(`api/src/server.js:55`) — nó trả 200 kể cả khi Postgres không nối được, ComfyUI chưa nạp custom
node, hoặc `models/` là thư mục rỗng trên container disk. Lỗi đắt tiền nhất ở đây không ồn ào mà
là **thành công giả**: mọi thứ xanh, và app lặng lẽ tải lại 33GB.

`make gpu-smoke` kiểm chín lớp, rẻ trước đắt sau, mỗi lớp in ra nó **chứng minh** điều gì:

| Lớp | Kiểm | Chứng minh |
|---|---|---|
| 1 | `GET /health` | Cloudflare Tunnel sống + tiến trình api sống |
| 2 | `GET /jobs?limit=1` | api + **Postgres** + API key — `/health` không chứng minh được DB, endpoint này có truy vấn thật |
| 3 | `pm2 jlist` | mọi app online, không app nào crash-loop |
| 4 | `/object_info/WanVideoModelLoader` | custom node đã nạp thật; thiếu là job motion chết với 400 *"node type not found"* |
| 5 | `pod-volume.sh --check` | volume thật sự đang được dùng, số file model không giảm |
| 6 | `pod-pgdump.sh --check && --verify` | có bản dump trên volume, và nó **nạp lại được** — diễn tập vào một DB tạm, không chỉ kiểm "có file" (xem [§Sao lưu database](#pg-backup)) |
| 7 | một job motion thật | sáu lớp trên chứng minh từng mảnh đúng; chỉ job thật chứng minh chúng **ghép lại** đúng |
| 8 | một job tryon thật | `qwen2.5vl:7b` auto-detect trang phục + venv `bg-remover` bake trong image — chỉ profile `full` mới có, chưa lớp nào trên chạm tới |
| 9 | một job create-image thật | Qwen-Image-Edit chạy chỉ-bằng-prompt (không cần ảnh) |

Lớp 7-9 opt-in, mỗi lớp một biến trigger riêng — repo không có sẵn media mẫu, và lớp 9 cố ý
**không** chạy cùng `SMOKE_REF`/`SMOKE_DRIVER` để `make gpu-smoke` trần giữ đúng cam kết "chỉ kiểm
readiness, không tốn GPU" khi không đặt biến. Lớp 6 (sao lưu DB) **không** opt-in — nó chạy cùng
nhóm rẻ 1-5 mỗi lần, vì `--check`/`--verify` không tốn GPU:

```bash
SMOKE_REF=nhanvat.jpg SMOKE_DRIVER=dandong.mp4 make gpu-smoke   # + lớp 7: motion
SMOKE_REF=nhanvat.jpg SMOKE_PRODUCT=aoso.jpg make gpu-smoke     # + lớp 8: tryon
SMOKE_PROMPT="a red car" make gpu-smoke                          # + lớp 9: create-image
```

Lớp 7 gửi job nhỏ nhất mà vẫn đi hết đường ống (540p, 33 frame), poll tới khi `done`, rồi tải
output về `/tmp/smoke-out.mp4` và kiểm dung lượng. Đường đi: Postgres → worker claim → ComfyUI nạp
Wan Animate **từ volume** → DWPose → sampling → VAE decode → MinIO → API trả URL. Lớp 8-9 cùng cơ
chế, tải về `/tmp/smoke-out-tryon.png` và `/tmp/smoke-out-create.png`.

Đo thật 06/08/2026 (**trước khi thêm lớp 6 sao lưu DB** — khi đó tổng chỉ 8 lớp, và motion/tryon/
create-image là lớp 6/7/8 lúc bấy giờ): 8/8 lớp pass — motion 286 KB mp4, tryon 1378 KB png,
create-image 1785 KB png. Lớp 6 sao lưu DB (numbering hiện tại) được thêm sau đó và **chưa từng
chạy qua `make gpu-smoke` trên pod thật** — nghiệm thu 07/08/2026 gọi thẳng `make gpu-db-check`
thay vì full smoke, xem [§Sao lưu database](#pg-backup).

Thiếu `GPU_SSH_HOST`/`GPU_SSH_PORT` trong `.env` thì lớp 3-6 tự bỏ qua kèm thông báo rõ, không
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
