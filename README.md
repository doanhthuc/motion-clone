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

Muốn người khác dùng được app mà không cần máy bạn bật: điền `FE_DOMAIN` vào `.env`, khi đó
`make gpu-bootstrap` chạy luôn frontend trên pod và tunnel phục vụ 2 hostname
(`app.…`→FE, `api.…`→BE). Sửa code FE sau đó chỉ cần `make gpu-fe`.
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
