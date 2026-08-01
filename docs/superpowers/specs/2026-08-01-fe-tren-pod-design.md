# Chạy frontend trên GPU pod — thiết kế

Ngày: 2026-08-01

## Vấn đề

`make gpu-bootstrap` hiện chỉ dựng backend trên pod. Frontend `motions/` chạy local qua
`make dev`, nên **không ai ngoài máy dev mở được ứng dụng**. Muốn đưa cho người dùng thật
thì phải có một link HTTPS công khai — đúng thứ `motions-studio/setup/fullstack-setup.sh`
làm cho VPS thường, nhưng chưa được nối vào bộ script pod của monorepo này.

## Mục tiêu

Một lệnh `make gpu-bootstrap` dựng CẢ backend lẫn frontend trên pod, end-user chỉ mở
`https://app.doanhthuc.xyz`. `make dev` local vẫn chạy song song để phát triển.

Ngoài phạm vi: CI/CD, nhiều môi trường, auto-deploy khi push.

## Kiến trúc

```
                    ┌─ Cloudflare Tunnel "motion-api-doanhthuc-xyz" (1 tunnel, 2 hostname)
Browser end-user ───┤
                    ├─ app.doanhthuc.xyz  →  pod localhost:2030  (Nuxt/Nitro, PM2 "motions")
                    └─ api.doanhthuc.xyz  →  pod localhost:8080  (motion-backend API)

Máy dev: make dev (localhost:2030) → https://api.doanhthuc.xyz   [CORS đã whitelist]
```

Trên pod, FE gọi BE qua hai đường khác nhau — giống hệt `fullstack-setup.sh:356-361`:

| Biến | Giá trị trên pod | Vì sao |
|---|---|---|
| `NUXT_MOTION_API_URL` | `http://127.0.0.1:8080` | Server-side proxy `/api/motion/*`. Cùng máy nên đi loopback, không vòng qua Cloudflare. |
| `NUXT_MOTION_API_KEY` | `API_KEY` của backend | Server-side only, không lộ ra browser. |
| `NUXT_PUBLIC_MOTION_BACKEND_URL` | `https://api.doanhthuc.xyz` | Browser gọi trực tiếp workflows/storage bằng Bearer, buộc phải là URL public. |

`motions/.env` **local** vẫn trỏ cả hai vào `https://api.doanhthuc.xyz` — máy dev không có
loopback tới backend.

## Phần đã có sẵn, không phải viết

`setup-pm2.sh` hỗ trợ đầy đủ frontend từ trước, chỉ chờ biến môi trường:

- dòng 209 — `CF_FE_DOMAIN` thêm ingress `→ localhost:${CF_FE_PORT:-2030}` vào cùng tunnel
- dòng 216-232 — vòng lặp tạo DNS CNAME proxied cho **từng** hostname trong `pairs`
- dòng 279 — `cf_api_preflight` kiểm token trên cả `DOMAIN` lẫn `CF_FE_DOMAIN`
- dòng 78-90 — `FRONTEND_URL` dạng `https://…` đổi email báo cài xong thành "mở 1 link"

Nên `motions-studio/` không cần sửa gì. Điều này cũng tránh xung đột với
`make sync-upstream` (rsync ghi đè `motions-studio/`).

`motions/.run.sh` đã có trong repo và giống hệt heredoc `fullstack-setup.sh:369-375`.
`rsync -a` giữ bit thực thi nên không cần sinh lại.

## Thành phần

### `scripts/pod-fe.sh` (mới)

Một việc: đưa FE lên pod. Idempotent, chạy lại bao nhiêu lần cũng được.

1. **Chặn sớm** — `node -v` trên pod phải ≥ 20.19 (yêu cầu của Nuxt 4;
   `setup-pm2.sh:444` cài Node 20 qua nodesource). Thấp hơn thì chết kèm lệnh sửa,
   **không tự nâng** vì tiến trình `api` đang chạy trên chính Node đó.
2. `rsync motions/` → `root@pod:~/motions`, loại `node_modules .nuxt .output .env .git`
3. Đọc `API_KEY` từ `~/motion-backend/.env` **trên pod**, ghi `~/motions/.env` (chmod 600).
   Key không đi qua máy dev, không vào log, không vào git.
4. `npm install --no-audit --no-fund && npm run build` **trên pod**
5. `pm2 delete motions` → `PORT=$FE_PORT pm2 start ~/motions/.run.sh --name motions
   --interpreter bash --update-env` → `pm2 save`
6. Postflight `curl -fsS https://$FE_DOMAIN/` retry ~60s

**Build phải chạy trên pod, không build local rồi rsync `.output/`.** `@nuxt/image` kéo
`sharp`, và `motions/node_modules/@img/` chứa `sharp-darwin-arm64` — binary native của
macOS, copy sang pod Linux x64 là chết runtime.

### `scripts/pod-bootstrap.sh` (sửa)

- đọc thêm `FE_DOMAIN` / `FE_PORT` từ `.env`
- truyền `CF_FE_DOMAIN="$FE_DOMAIN" CF_FE_PORT="$FE_PORT" FRONTEND_URL="https://$FE_DOMAIN"`
  vào lệnh chạy `setup-motion-transfer.sh` trên pod
- sau khi backend xong và `motions/.env` local đã ghi, gọi `scripts/pod-fe.sh` nếu
  `FE_DOMAIN` có giá trị
- `FE_DOMAIN` trống → giữ nguyên hành vi cũ (chỉ backend, FE local)

### `Makefile` (sửa)

`gpu-fe` — rsync + build + restart RIÊNG frontend, ~2 phút thay vì chạy lại toàn bộ
bootstrap. Dùng khi sửa code FE. Giả định ingress FE đã tồn tại nên lần đầu vẫn phải qua
`gpu-bootstrap`.

### `scripts/gpu-preflight.sh` (sửa)

- báo cáo `FE_DOMAIN` / `FE_PORT`
- **chặn** nếu `FE_DOMAIN` set mà `CORS_ORIGINS` thiếu `https://$FE_DOMAIN` — đây là lỗi im
  lặng khó chịu nhất: FE load bình thường, mọi API call chết CORS, và triệu chứng trông
  giống backend hỏng
- **chặn** nếu `FE_DOMAIN` == `DOMAIN`
- cảnh báo nếu `FE_DOMAIN` set nhưng chỉ có `CF_TUNNEL_TOKEN` (không có `CF_API_TOKEN`):
  đường tunnel-token không tự tạo được Public Hostname thứ hai, phải vào dashboard tay

### `.env.example`, `docs/gpu-pod.md` (sửa)

Tài liệu `FE_DOMAIN` / `FE_PORT`, `CORS_ORIGINS` hai giá trị, và mục "FE trên pod".

## Xử lý lỗi

| Hỏng | Triệu chứng | Script làm gì |
|---|---|---|
| Node < 20.19 trên pod | `nuxt build` chết giữa chừng | Chặn trước bước rsync, in lệnh nâng cấp |
| Ingress FE chưa tạo | `https://app…` trả 404 catch-all của tunnel | Postflight phân biệt 404-tunnel với FE chưa lên, bảo chạy `make gpu-bootstrap` |
| `CORS_ORIGINS` thiếu FE domain | FE load được, mọi API call chết CORS | preflight chặn từ trước khi thuê máy |
| `npm run build` lỗi | — | `die` kèm gợi ý `pm2 logs motions` và đường dẫn log |
| Thiếu `API_KEY` trong `~/motion-backend/.env` | FE chạy nhưng mọi job 401 | `die` trước khi build |

## Việc thủ công còn lại

1. Tạo CF API Token cho zone `doanhthuc.xyz`, đúng 3 quyền: `Account · Cloudflare Tunnel ·
   Edit` + `Zone · DNS · Edit` + `Zone · Zone · Read`, Zone Resources include
   `doanhthuc.xyz`.
2. Sửa `.env`: `DOMAIN=api.doanhthuc.xyz`, `FE_DOMAIN=app.doanhthuc.xyz`,
   `CORS_ORIGINS=https://app.doanhthuc.xyz,http://localhost:2030`, `CF_API_TOKEN=<token mới>`.
3. `make gpu-preflight` → `make gpu-bootstrap`.

**Về pod đang chạy:** tên tunnel là `motion-${DOMAIN//./-}` (`setup-pm2.sh:187`). Đổi
`DOMAIN` sang `api.doanhthuc.xyz` tạo tunnel **mới** `motion-api-doanhthuc-xyz`;
`cloudflared` được uninstall/install lại với token mới. Tunnel cũ
`motion-comfy-snapiq-dev` nằm lại trong account Cloudflare cũ — xoá tay nếu muốn. Backend
không phải cài lại, `setup-motion-transfer.sh` idempotent.

## Kiểm chứng

Không có test tự động — đây là script deploy, thứ duy nhất chứng minh được là pod thật:

1. `make gpu-preflight` báo `ready`
2. `make gpu-bootstrap` chạy hết, không `die`
3. `ssh pod 'pm2 ls'` thấy `motions` **online** cạnh `api`/`worker`/`comfyui`
4. `curl -fsS https://app.doanhthuc.xyz/` trả HTML
5. Mở `https://app.doanhthuc.xyz` trên máy KHÁC (không phải máy dev), login bằng
   `SUPER_ADMIN`, DevTools Network không có lỗi CORS
6. `make gpu-fe` chạy lại được, `motions` restart, không đụng backend
