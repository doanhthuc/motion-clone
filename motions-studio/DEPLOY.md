# Deploy Motion Backend — mỗi box 1 chức năng (KHOÁ)

> **Mô hình mới:** thay vì 1 box ôm hết (tranh GPU/VRAM → OOM, cold-start, khó scale — chính là vấn đề của box `.165` cũ), giờ **mỗi chức năng chạy trên 1 box riêng, cài gọn + khoá cứng**. Box chỉ chứa đúng custom node + đúng catalog model của chức năng đó; không thể vô tình pull model/feature khác. FE (`motions`) trỏ tới đúng box theo từng tính năng.

## 4 kịch bản setup

| Script | Box dùng cho | Custom node | JOB_TYPES | FE |
|---|---|---|---|---|
| `setup/fullstack-setup.sh` | **Fullstack** — BE + FE trên 1 box, 1 lệnh | tất cả | union tất cả | ✅ build + chạy `motions` |
| `setup/setup-pm2.sh` | **Backend full** (mọi tính năng, không FE) | tất cả | union tất cả | ❌ deploy FE riêng |
| `setup/setup-create-image.sh` | **Chuyên tạo ảnh** (Qwen-Image-Edit) — KHOÁ | GGUF · KJNodes | `create-image` | ❌ |
| `setup/setup-motion-transfer.sh` | **Chuyên motion theo video** (Wan 2.2 Animate) — KHOÁ | WanVideoWrapper · KJNodes · VideoHelperSuite · controlnet_aux · Frame-Interpolation | `motion,teen-flycam,trend-tiktok,enhance` | ❌ |
| `setup/setup-tryon.sh` | **Chuyên thử đồ** (Qwen-Image-Edit 2509) — KHOÁ | GGUF · KJNodes | `tryon,create-image,edit-image,enhance` | ✅ (dịch prompt) |

- **Fullstack / Backend full** → mục **"Fullstack — Backend + Frontend trên cùng 1 box"** ở CUỐI file (cài đầy đủ, cần GPU lớn).
- **2 box chuyên (khoá)** → phần chính ngay bên dưới (gọn, mỗi box 1 chức năng, không pull được model khác).

---

## Cấu hình máy đề xuất

GPU **NVIDIA** là bắt buộc. Installer tự chọn PyTorch/CUDA theo GPU + driver; RTX 50xx/Blackwell với driver R580+ dùng baseline stable PyTorch 2.12.1 CUDA 13.0. Cả 2 box dùng **1 GPU duy nhất** cho 1 chức năng (không tranh chấp).

### Box create-image (Qwen-Image-Edit)
| Hạng mục | Tối thiểu | Khuyến nghị |
|---|---|---|
| **GPU (VRAM)** | **24GB** — RTX 3090 / 4090 (đủ, hơi sát) | **32GB — RTX 5090** |
| CPU | 8 core | 12+ core |
| RAM | 32GB | 48GB |
| Disk (SSD/NVMe) | **120GB** | 200GB |
| Model chiếm | ~31.5GB (gguf 21.8 + qwen-vl 9.4 + vae + lora) + Ollama qwen2.5:7b ~4.7GB |

> Qwen-Image-Edit Q8 cần ~20GB VRAM + encoder qwen-vl → đỉnh ~24–28GB. Trên 24GB ComfyUI tự offload (chậm hơn chút); 32GB chạy thoải mái.

### Box motion-transfer (Wan 2.2 Animate)
| Hạng mục | Tối thiểu | Khuyến nghị |
|---|---|---|
| **GPU (VRAM)** | **24GB** — RTX 4090 (bật BlockSwap, clip ngắn 480–720p) | **32GB — RTX 5090** |
| CPU | 8 core | 12+ core |
| RAM | 48GB (video nhiều frame + nạp model) | 64GB |
| Disk (SSD/NVMe) | **120GB** | 200GB |
| Model chiếm | ~33GB (animate 18.4 + umt5 11.4 + clip_vision 1.3 + relight 1.4 + …) + DWPose/RIFE ~0.5GB (node tự tải) |

> Wan 2.2 Animate 14B fp8 ~17GB + umt5 + clip_vision + frame video → đỉnh cao. `WanVideoBlockSwap` đẩy bớt block ra RAM nên 24GB chạy được clip ngắn; 32GB cho 720p/clip dài. **Nội suy RIFE (lên 30/60fps) ngốn thêm VRAM** → đã giới hạn preset (vd 15s-720p chỉ 16fps).

### Hạ tầng gợi ý
- **Cloud GPU theo giờ:** vast.ai / RunPod (RTX 4090 ~$0.4/h, RTX 5090 ~$0.7/h). Máy NAT → **dùng Cloudflare Tunnel** (không cần map port 80/443).
- **On-prem:** 1 máy RTX 5090 32GB chạy được cả 2 (nhưng vẫn nên tách container/box để khoá).
- **OS:** Ubuntu 22.04 (script tự cài driver NVIDIA nếu thiếu → cần reboot 1 lần).

---

## Bước 0 — Chuẩn bị server
- Ubuntu 22.04, user có `sudo` (hoặc root). GPU NVIDIA gắn sẵn.
- Mở SSH. Nếu dùng Cloudflare Tunnel thì **không cần** mở 80/443.
- Domain quản lý trên Cloudflare (để HTTPS tự động). Mỗi box 1 subdomain, vd `create-image.datools.info`, `motion-transfer.datools.info`.

## Bước 1 — Lấy code lên server
```bash
git clone <repo-url> motion-backend && cd motion-backend
```

## Bước 2 — Tạo Cloudflare API Token (1 lần, để setup TỰ ĐỘNG hết)

> Đây là thứ **DUY NHẤT** lấy từ dashboard. Có token, script tự tạo tunnel + DNS + ingress + SSL. Token tái dùng được cho nhiều box.

### ⚠️ BẮT BUỘC đủ CẢ 3 quyền — thiếu 1 là tunnel fail
| # | Permission (3 cột trong UI) | Dùng để | Thiếu thì lỗi |
|---|---|---|---|
| 1 | **Account** · **Cloudflare Tunnel** · **Edit** | tạo tunnel + ingress | `Tạo/lấy tunnel lỗi… Authentication error` |
| 2 | **Zone** · **DNS** · **Edit** | tạo DNS CNAME proxied | `Tạo DNS record lỗi… thiếu Zone.DNS:Edit` |
| 3 | **Zone** · **Zone** · **Read** | tìm zone theo domain | `Không thấy zone Cloudflare cho '<domain>'` |

### Các bước (trình duyệt)
1. **https://dash.cloudflare.com/profile/api-tokens** → **Create Token** → *Create Custom Token* → **Get started**.
2. **Permissions** — *+ Add more* cho đủ **3 dòng** y hệt bảng trên.
3. **Account Resources**: `Include` → đúng tài khoản chứa domain.
4. **Zone Resources**: `Include` → **Specific zone** → domain gốc (vd `datools.info`).
5. **Continue to summary** → **Create Token** → **Copy** (`cfut_…` hoặc 40 ký tự).

### Kiểm tra token trước khi setup
```bash
curl -s -H "Authorization: Bearer <TOKEN>" \
  "https://api.cloudflare.com/client/v4/zones?name=<DOMAIN-GỐC>" \
  | grep -o '"name":"[^"]*"' | head
# Ra "name":"datools.info" → Zone:Read OK. Rỗng → thiếu Zone:Read / sai account.
```

> Không muốn API token? Dùng **Tunnel token** (bán tự động) hoặc **certbot** (cần mở 80/443) — xem README.

---

## Bước 3 — Chạy setup (1 lệnh)

Chọn đúng script theo chức năng của box. Hỏi tương tác: **Domain · Email admin · Gmail App Password · Cloudflare API Token · URL Frontend (CORS)**.

**Box create-image:**
```bash
./setup/setup-create-image.sh
# hoặc không tương tác:
DOMAIN=create-image.datools.info SUPER_ADMIN=ban@gmail.com \
GMAIL_USER=ban@gmail.com GMAIL_APP_PASSWORD=xxxxxxxxxxxxxxxx \
CF_API_TOKEN=<token> CORS_ORIGINS=https://motions.cong-ty.com \
./setup/setup-create-image.sh
```

**Box motion-transfer:**
```bash
./setup/setup-motion-transfer.sh
# hoặc không tương tác: như trên, DOMAIN=motion-transfer.datools.info, đổi script.
```

Script tự làm: cài Node/PM2/Postgres/MinIO (+ Ollama nếu cần) → dò GPU/driver → ComfyUI + PyTorch CUDA stable phù hợp + SageAttention khi tương thích + **chỉ custom node của feature** → chạy đúng PM2 app → Cloudflare Tunnel + DNS + SSL → seed + in `.env` FE.

**KHÔNG tải model lúc cài** (xem Bước 5). Script **idempotent** — chạy lại an toàn.

**Cờ env tuỳ chọn:** `CF_API_TOKEN` (tunnel tự động) · `CF_TUNNEL_TOKEN` (bán tự động) · `CORS_ORIGINS` · `COMFY_DIR=/path` (đổi nơi cài ComfyUI) · `SKIP_COMFY=1` (dùng ComfyUI máy khác, set `COMFY_URL`) · `SKIP_HTTPS=1` (local/IP, không domain).

---

## Bước 4 — Verify
```bash
curl https://create-image.datools.info/health      # {"status":"ok",...}
pm2 ls          # 5 online: minio · api · wf-worker · worker · comfyui  (KHÔNG bg-remover)
pm2 logs comfyui                # ComfyUI :8188 boot xong
systemctl status cloudflared    # tunnel active
grep -E '^(JOB_TYPES|MODEL_CATALOG_PATH)=' .env     # phải đúng feature (khoá)
```

## Bước 5 — Tải model (BẮT BUỘC, làm sau khi cài)
Model **không** tải trong setup. Mở **FE → Settings → Models AI**:
- Box create-image: nhóm **Qwen-Image-Edit** → **Cài cả nhóm** (~31.5GB). + nhóm **Ollama** → `qwen2.5:7b-instruct` (dịch prompt VN→EN).
- Box motion-transfer: nhóm **Wan 2.2 Animate** → **Cài cả nhóm** (~33GB). DWPose + RIFE node **tự tải** lần render đầu (đừng hốt hoảng nếu job đầu hơi lâu).

> Box chỉ **thấy & tải được** model của đúng feature (đã khoá qua `catalog-<feature>.json`). Không có model nào khác trong danh sách.

## Bước 6 — Nối Frontend (`motions`)
Dán block `.env` setup in ra cuối (cũng gửi qua email admin):
```bash
NUXT_MOTION_API_URL=https://create-image.datools.info
NUXT_MOTION_API_KEY=mk_<sinh-tu-dong-khi-cai-dat>
NUXT_PUBLIC_MOTION_BACKEND_URL=https://create-image.datools.info
```
Nhiều box → FE trỏ từng tính năng tới box tương ứng (cấu hình theo route/feature trong FE).

---

## Cơ chế KHOÁ (vì sao box không pull được model/feature khác)
1. **`MODEL_CATALOG_PATH` → `comfyui/catalog-<feature>.json`** — allow-list; Settings → Models AI chỉ liệt kê model của feature.
2. **Không cài `ComfyUI-Manager`** — không có UI cài node/model tuỳ ý.
3. **`JOB_TYPES` = đúng 1 type** — worker bỏ qua mọi job loại khác (job nằm im, không chạy nhầm).
4. **Chỉ clone custom node của feature** — workflow khác thiếu node → fail ngay, không "âm thầm" chạy.
5. **Chỉ bật PM2 app cần** (`--only minio,api,wf-worker,worker,comfyui`).

**Muốn mở khoá có chủ đích** (không khuyến khích trên box chuyên): đổi `MODEL_CATALOG_PATH` về `comfyui/catalog.json`, thêm custom node vào biến `COMFY_NODES` trong script feature, nới `JOB_TYPES` rồi `pm2 restart ecosystem.config.cjs`.

---

## Vận hành sau deploy
```bash
pm2 ls · pm2 logs api · pm2 logs worker · pm2 logs comfyui
pm2 restart ecosystem.config.cjs --only minio,api,wf-worker,worker,comfyui
sudo systemctl restart cloudflared      # restart tunnel
```
- Đổi model đang dùng (vd Qwen Q8 ↔ Q6): bảng `app_settings` (`model.*`) hoặc Settings.
- 1 job GPU/lúc (mặc định `WORKER_CONCURRENCY=1`, `WF_CONCURRENCY=1` — chống tranh VRAM). Box VRAM lớn + clip nhẹ mới nên tăng.

## Lỗi hay gặp
- **`Connection refused :8188`** → ComfyUI chưa boot/đang nạp model: `pm2 logs comfyui`. Lần đầu tải torch/node lâu.
- **Settings → Models AI rỗng** → thiếu seed api_key hoặc sai `MODEL_CATALOG_PATH`: kiểm tra `.env` + `pm2 logs api`.
- **Job `motion` trên box create-image không chạy** → ĐÚNG (đã khoá `JOB_TYPES`). Gửi job đúng loại của box.
- **certbot không cấp được cert** → cần DNS trỏ IP public + mở 80/443 ở firewall nhà cung cấp; hoặc chuyển sang Cloudflare Tunnel (`CF_API_TOKEN=...`).
- **OOM giữa job motion** → giảm độ phân giải/độ dài, bật BlockSwap, giữ fps thấp (tắt RIFE), 1 job/lúc.

## Fullstack — Backend + Frontend trên CÙNG 1 box (1 lệnh)

Dùng khi muốn 1 VPS chạy CẢ backend (`motion-backend`) lẫn frontend (`motions`) — end-user chỉ mở 1 link.

```bash
git clone git@github.com:ALD-Project/motion-backend.git && cd motion-backend
./setup/fullstack-setup.sh
```
Hỏi tương tác: **URL Frontend · URL Backend · Cloudflare API Token · email SuperAdmin · Gmail + App Password**.

**2 chế độ — tự chọn theo việc có Cloudflare API Token hay không:**

| | Có `CF_API_TOKEN` ⭐ khuyên dùng | Không token (IP trực tiếp) |
|---|---|---|
| HTTPS | Cloudflare Tunnel cho **cả FE + BE** → end-user mở `https://<FE-domain>` | `http://<IP>:2030` |
| Mở port | **KHÔNG cần** (chạy được cả máy NAT vast.ai/RunPod) | Cần IP public + mở 2030/8080 |
| Frontend | clone + build `motions` + thêm PM2 app `motions` | như trên |

Bên trong, `fullstack-setup.sh` gọi lại `setup-pm2.sh` cho backend (full: mọi node + union JOB_TYPES + tải bộ core), rồi dựng frontend.

**Không tương tác (CI / dán sẵn):**
```bash
FE_DOMAIN=app.datools.info BE_DOMAIN=api.datools.info CF_API_TOKEN=<token> \
SUPER_ADMIN=ban@gmail.com GMAIL_USER=ban@gmail.com GMAIL_APP_PASSWORD=xxxxxxxxxxxxxxxx \
./setup/fullstack-setup.sh
```
Cờ tuỳ chọn: `FE_PORT=2030 · API_PORT=8080 · MOTIONS_DIR=~/motions · MOTIONS_REPO=... · SKIP_COMFY=1 · SKIP_MODELS=1`.

Cấu hình máy: fullstack cài **đầy đủ** → cần GPU **≥32GB (RTX 5090)** + RAM ≥48GB + SSD ≥200GB (gồm FE build + model nhiều feature). Muốn nhẹ/khoá → dùng 2 box chuyên ở trên.

### Chỉ Backend full (không kèm FE)
```bash
./setup/setup-pm2.sh         # mọi node + union JOB_TYPES, KHÔNG cài frontend (deploy FE riêng)
```

### Local / không domain (cả 3 script PM2)
Thêm `SKIP_HTTPS=1` → API expose thẳng `http://<IP>:8080`, bỏ qua Cloudflare/nginx (nhớ mở cổng ở firewall nhà cung cấp).

## Gỡ cài đặt (uninstall)
```bash
./setup/uninstall.sh --dry-run     # XEM TRƯỚC, không xoá gì
./setup/uninstall.sh               # xoá thật (gõ 'XOA TAN GOC' để xác nhận)
```
Gỡ: 7 PM2 app · ComfyUI + models (~47GB) · Postgres DB/role `motion` · MinIO data · repo `motion-backend` + `motions` · cloudflared + nginx site. **GIỮ nguyên:** Ollama + models · Supabase · postgres-server/nginx/nodejs dùng chung. Gỡ luôn tunnel+DNS Cloudflare: thêm `CF_API_TOKEN=... DOMAIN=... FE_DOMAIN=...`.
