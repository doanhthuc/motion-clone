# GPU pod cho backend motion-transfer (motions-studio)

Frontend (`motions`) chạy local (`make dev`). Backend (`motions-studio`) cần GPU NVIDIA
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

`GPU_PROVIDER=runpod` chạy được nhưng **kém kiểm chứng hơn** nhánh vast.ai — `runpodctl`'s CLI
flags đổi qua từng bản, script chỉ đoán tốt nhất (`runpodctl create pod --name ... --gpuType ...`).
Trước khi tin, chạy `runpodctl create pod --help` đối chiếu. `make gpu-wait` KHÔNG tự động cho
RunPod (chỉ biết hỏi `vastai show instance`) — điền `GPU_SSH_HOST`/`GPU_SSH_PORT` thủ công từ
`runpodctl get pod` hoặc dashboard rồi chạy thẳng `make gpu-bootstrap`.

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
