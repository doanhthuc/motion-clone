# GPU pod cho backend motion-transfer (motions-studio)

Frontend (`motions`) mặc định chạy local (`make dev`); muốn nó chạy luôn trên pod để người khác
mở được thì xem [Frontend on the pod](#frontend-on-the-pod). Backend (`motions-studio`) cần GPU NVIDIA
(≥24GB VRAM cho Wan 2.2 Animate) nên chạy trên máy thuê theo giờ — vast.ai hoặc RunPod — thay vì
máy Mac của bạn. Toàn bộ flow dưới đây tự động hoá đúng những bước thủ công đã làm ở lần chạy
trước (SSH vào máy thuê, chạy `motions-studio/setup/setup-motion-transfer.sh`, dán `.env` in ra
vào `motions/.env`).

## Luồng nhanh

```bash
cp .env.example .env        # điền DOMAIN, SUPER_ADMIN, GMAIL_*, CF_API_TOKEN — xem bên dưới
make gpu-preflight           # check .env đủ CHƯA, trước khi tốn tiền thuê máy
make gpu-provision            # dry-run: tìm offer + in lệnh thuê (KHÔNG thuê)
CONFIRM=yes make gpu-provision # thuê thật — điền GPU_INSTANCE_ID vào .env
make gpu-wait                 # chờ SSH lên, tự lưu GPU_SSH_HOST/GPU_SSH_PORT vào .env
make gpu-bootstrap            # rsync code + chạy setup-motion-transfer.sh trên pod (không tương tác)
make gpu-status                # curl https://$DOMAIN/health
```

`gpu-bootstrap` tự dán block `NUXT_MOTION_API_URL`/`NUXT_MOTION_API_KEY`/`NUXT_PUBLIC_MOTION_BACKEND_URL`
vào `motions/.env` — chỉ cần `make down && make dev` lại là FE local trỏ đúng backend mới thuê.

Việc còn lại KHÔNG tự động (cố ý — xem "Vì sao không tự động" bên dưới): tải model. Mở
`http://localhost:2030` → login bằng `SUPER_ADMIN` (OTP) → **Settings → Models AI** → nhóm
**Wan 2.2 Animate** → **Cài cả nhóm** (~33GB).

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

## Costs — pod dừng vẫn tính tiền

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

`make gpu-wait` giờ chạy được cả RunPod: nó poll `runpodctl pod get`, đọc SSH endpoint ở **cả hai
dạng JSON** mà RunPod từng dùng (`portMappings` map và `runtime.ports` list) rồi tự điền
`GPU_SSH_HOST`/`GPU_SSH_PORT`.

Lệnh vòng đời dùng cú pháp mới `runpodctl pod start|stop|delete` — dạng cũ (`runpodctl start pod`)
vẫn chạy nhưng đã bị đánh dấu deprecated.

## Vì sao không tự động tải model

Tải model là hành động qua UI đã login (OTP), tải ~33GB — không phải lệnh SSH đơn giản mà là
click trong FE sau khi đăng nhập; catalog model cũng có thể đổi theo thời gian. Tự động hoá bước
này rủi ro hơn lợi ích (tải nhầm/tải thiếu mà không ai để ý), nên để thủ công, chỉ 1 lần mỗi pod
mới.

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

## Lấy bản mới từ upstream: `make sync-upstream`

```bash
make sync-upstream           # rsync + scrub + gate, rồi cho bạn xem diff
make sync-upstream PULL=1    # git pull bản theo dõi trước
make sync-upstream COMMIT=1  # commit nếu gate pass
```

Bản theo dõi upstream nằm ở `../motion-upstream-tracking/` (ngoài repo này, đã khóa push). Đổi chỗ
bằng `UPSTREAM_DIR=`.

**Vì sao phải là script:** `rsync` ghi đè các file `scrub-secrets.sh` đã dọn, nên mỗi lần sync là
credentials của upstream quay lại — `DEFAULT_API_KEY` (key **chia sẻ** giữa mọi bản deploy của
source này), admin key Motion Task Cloud, `setup/templates.json`, vài email cá nhân, **và các dòng
`.gitignore` mà fork thêm vào**. Lần sync đầu tiên đã mất đúng 4 dòng `.gitignore` đó, nên
`scrub-secrets.sh` giờ cưỡng chế luôn cả chúng.

Script từ chối chạy khi working tree bẩn: nếu không thì trong diff kết quả bạn không phân biệt được
hunk nào của upstream, hunk nào của mình — mà diff đó là lần review duy nhất bản import này có.
Ghi đè bằng `FORCE=1`.

Không dùng `--delete`: nó sẽ xoá mọi file fork thêm vào (`pod-volume.sh`, `scrub-secrets.sh`,
specs). Đánh đổi: file bị xoá ở upstream thì còn sót lại đây. Đó là hướng sai an toàn hơn.
