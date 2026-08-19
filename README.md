# motion-clone

Monorepo cho hệ Motion: frontend chạy local, backend chạy trên GPU pod thuê theo giờ.

```
motion-clone/
├── Makefile              vòng đời GPU pod: provision → wait → bootstrap → up/down/destroy
├── scripts/              pod-provision.sh · pod-wait.sh · pod-bootstrap.sh · gpu-preflight.sh
├── docs/gpu-pod.md       hướng dẫn đầy đủ quy trình thuê pod
├── .env.example          cấu hình gốc (DOMAIN, SUPER_ADMIN, CF_API_TOKEN, GPU_*)
├── motions/              FRONTEND (Nuxt) — chạy LOCAL, `make dev` → localhost:2030
└── motions-studio/       BACKEND — rsync lên pod, cần GPU NVIDIA ≥24GB VRAM
```

## Kiến trúc

Frontend không cần GPU nên chạy thẳng trên máy dev. Backend cần GPU cho Wan 2.2 Animate nên
chạy trên máy thuê (vast.ai / RunPod). `make gpu-bootstrap` rsync `motions-studio/` lên pod,
chạy `setup/setup-<SETUP_PROFILE>.sh` ở đó, rồi tự dán block `NUXT_MOTION_*` vào `motions/.env`
để FE local trỏ đúng backend vừa dựng.

Hai biến trong `.env` quyết hình dạng deploy: **`SETUP_PROFILE`** (`motion-transfer` mặc định —
4 job type, catalog khoá; hay `full` — 21 type, có Qwen/Flux/LTX + Ollama) và **`WORKER_SOURCE`**
(`local` dùng GPU của chính pod, `serverless` đẩy sang RunPod Serverless, `both` chia việc).
Bảng so sánh và các cạm bẫy khi ghép chúng: `docs/gpu-pod.md#deploy-shapes`.

Máy thuê đều NAT nên dùng Cloudflare Tunnel thay vì mở port — chi tiết ở `docs/gpu-pod.md`.

Muốn người khác dùng được app mà không cần máy bạn bật: điền `FE_DOMAIN` vào `.env`, khi đó tunnel
phục vụ 2 hostname (`app.…`→FE, `api.…`→BE). Frontend deploy bằng **bước riêng** `make gpu-fe` —
`make gpu-bootstrap` chỉ lo backend. Sửa code FE sau đó cũng chỉ cần `make gpu-fe`.
Xem `docs/gpu-pod.md#frontend-on-the-pod`.

## Bắt đầu

```bash
cp .env.example .env         # điền DOMAIN, SUPER_ADMIN, GMAIL_*, CF_API_TOKEN
make help                    # xem toàn bộ lệnh
make gpu-preflight           # kiểm .env TRƯỚC khi tốn tiền thuê máy
```

Toàn bộ quy trình theo thứ tự — dựng lần đầu, dùng hằng ngày, dọn, và dựng lại — nằm ở
`docs/gpu-pod.md` mục **Runbook**. Đọc nó trước lần thuê đầu tiên, đặc biệt phần chi phí:
**pod dừng vẫn tính tiền ổ đĩa**, và Network Volume tính tiền cả khi không có pod nào.

## Chạy lô material (khỏi bấm UI)

Thả material vào bốn ngăn:

```
~/materials/
├── characters/
├── outfits/
├── backgrounds/
└── drivers/
```

Rồi gõ:

```bash
make batch-scan DIR=~/materials MODE=pair    # đẻ batch/<hôm nay>.yaml — SOÁT rồi mới chạy
make batch-validate FILE=batch/2026-08-18.yaml   # kiểm, không tiêu GPU
make batch FILE=batch/2026-08-18.yaml
make batch FILE=batch/2026-08-18.yaml RESUME=1   # chạy tiếp lô dở
```

Kết quả ở `out/latest/_final/`. Param của từng chặng tra bằng
`make batch-params TYPE=motion` (hoặc `tryon` / `enhance`) — bỏ trống thì chạy mặc định. Bảng đó nói
luôn param nào API **ép giá trị** hoặc **bỏ qua** dù bạn gõ đúng chính tả.

`RESUME=1` trước tiên **bắt lại đúng job cũ** bằng `job_id` trong `batch/<tên>.state.json`: chặng đứt
ở phút 39 của một job 40 phút không phải chạy lại từ đầu. Chỉ khi job đó đã hỏng thật, hoặc không còn
trên pod (pod đã dựng lại), nó mới gửi job mới. Toàn bộ diễn biến của từng run còn lại ở
`out/latest/runs/<run-id>/run.log` — đóng terminal không mất.

Dọn đĩa sau vài lô: `make batch-clean` (mặc định giữ 3 lô gần nhất) chỉ xoá
`runs/` — file trung gian của từng chặng. `_final/` (video đã xong) không bao
giờ bị đụng tới. `make batch-clean KEEP=1 DRY=1` để xem trước sẽ xoá gì mà
chưa xoá thật.

### Điều khiển lô từ Claude Code (MCP)

Repo khai sẵn một MCP server ở `.mcp.json`; mở Claude Code trong thư mục này rồi duyệt server
`batch` một lần là dùng được, không cài thêm gói nào. Bốn tool, bọc mỏng chính các lệnh trên:

| Tool | Tương đương |
|---|---|
| `batch_validate` | `make batch-validate` |
| `batch_run` | `make batch` — nhưng **chạy nền**, trả pid, sống qua phiên chat |
| `batch_status` | đọc `batch/<tên>.state.json` + mấy dòng cuối log |
| `batch_rerun` | *không có bản CLI* — chạy lại một run **đã xong** (video ra xấu) |

Khác CLI ở đúng một chỗ về tiền: pod đang dừng thì tool **không tự** `make gpu-up`, nó báo lỗi và
bảo bạn gõ lệnh đó. Muốn nó tự bật thì phải gọi kèm `allow_start=true`. Bật pod là bắt đầu tính
tiền, nên đó là quyết định của bạn chứ không của một tool call.

Kiểm server còn sống mà không tốn gì: `make batch-mcp-check`.

**Sửa code MCP thì phải khởi động lại server.** Claude Code giữ một process
`scripts/batch_mcp.py` sống suốt phiên, nên nó nạp module cũ trong bộ nhớ — sửa
`batchlib/mcp_tools.py` mà không thoát/mở lại `claude` thì tool vẫn chạy code cũ,
im lặng. Cách nhận ra: `ps -o pid,lstart -p $(pgrep -f batch_mcp.py)` cho thời điểm
khởi động; nó cũ hơn lần sửa của bạn là đúng cái bẫy này. `make batch-mcp-check`
KHÔNG bắt được — nó chạy một process mới nên luôn thấy code mới.

**Hướng dẫn dùng đầy đủ — cả CLI và MCP, kèm bảng param và bốn cái bẫy im lặng:
[`docs/batch-runner.md`](docs/batch-runner.md).**

Thiết kế và các đánh đổi: `docs/superpowers/specs/2026-08-18-batch-runner-design.md` (MCP ở §9).

## Nguồn gốc code

`motions/` và `motions-studio/` khởi nguồn từ `ALD-Project` (source đã mua). **Từ 02/08/2026 repo
này không lấy bản mới từ đó nữa** — hai thư mục đó là code của chúng ta, sửa thẳng vào, không có
khái niệm "file upstream đừng sửa".

Kéo theo, đã bỏ hẳn: `scripts/sync-upstream.sh`, `make sync-upstream`, `UPSTREAM_SHA`, và cổng
`check-local-deltas.sh` (nó chỉ tồn tại để giữ các bản sửa cục bộ khỏi bị `rsync` của sync ghi đè).
Lịch sử của chúng còn trong git nếu cần dựng lại.

Quyền trên `ALD-Project` đo ngày 02/08/2026: **đọc được, không push được** (`git push --dry-run` →
`Write access to repository not granted`). Nên sửa tận gốc bên đó là việc không làm được — đó là
một lý do nữa để dứt khoát sở hữu code ở đây.

`motions-studio/setup/scrub-secrets.sh` **vẫn giữ**, nhưng vì lý do khác trước: nó không còn phải
dọn lại secret mà sync mang về, nó là cổng chặn trước mỗi commit lên một repo public. Xem §Secrets.

## Secrets

Repo này **public**, nên có một cổng chặn bắt buộc:

```bash
motions-studio/setup/scrub-secrets.sh --check    # phải exit 0 trước mọi lần commit
```

Nó quét credentials và email cá nhân trên mọi file được track, kể cả `docs/`. Chi tiết:
`motions-studio/docs/superpowers/specs/2026-07-31-toi-uu-khoi-tao-pod-design.md` §3.2

File chứa secret thật **không bao giờ** vào git: `.env` (gốc và `motions/`),
`motions-studio/setup/templates.json`, `motions-studio/setup/pod.env`.
