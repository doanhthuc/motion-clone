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
chạy `setup/setup-motion-transfer.sh` ở đó, rồi tự dán block `NUXT_MOTION_*` vào `motions/.env`
để FE local trỏ đúng backend vừa dựng.

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

Đọc `docs/gpu-pod.md` trước lần thuê đầu tiên — đặc biệt phần chi phí: **pod dừng vẫn tính tiền ổ đĩa.**

## Quan hệ với upstream

`motions/` và `motions-studio/` có nguồn gốc từ `ALD-Project` (source đã mua). Bản theo dõi
upstream giữ riêng ở `~/Desktop/motion-upstream-tracking/`, đã khóa đường push để không đẩy
nhầm lên đó. Lấy bản mới: `git pull` trong bản theo dõi → rsync sang đây → chạy lại
`motions-studio/setup/scrub-secrets.sh`.

## Secrets

Repo này **public**, nên có một cổng chặn bắt buộc:

```bash
motions-studio/setup/scrub-secrets.sh --check    # phải exit 0 trước mọi lần commit
```

Nó quét credentials và email cá nhân trên mọi file được track, kể cả `docs/`. Lý do phải chạy
lại sau mỗi lần sync upstream: `rsync` ghi đè các file đã scrub và mang secret của upstream
trở lại. Chi tiết: `motions-studio/docs/superpowers/specs/2026-07-31-toi-uu-khoi-tao-pod-design.md` §3.2

File chứa secret thật **không bao giờ** vào git: `.env` (gốc và `motions/`),
`motions-studio/setup/templates.json`, `motions-studio/setup/pod.env`.
