# Motion Backend — backend AI tự chứa, chạy 1 lệnh

> **Khái niệm chính:** một backend AI **độc lập, self-contained** — tự ôm trọn auth, storage, job queue, LLM và GPU inference trong 1 stack. Mang sang dự án nào cũng chạy bằng **1 lệnh**, KHÔNG phụ thuộc dịch vụ ngoài (không Supabase, không auth provider, không S3 cloud). Frontend (`motions`) chỉ cần trỏ tới 1 domain.

```
   Client / FE
      │  X-API-Key  ·  JWT session (OTP login)
      ▼
NodeJS API (Express) ──jobs/auth/storage──▶  PostgreSQL  +  MinIO (S3)
      ▲                                            ▲
      │ poll/claim job, upload output, log         │ presigned URL
 Python Worker ────HTTP────▶ ComfyUI (GPU: Wan 2.2 Animate · Qwen · LTX) + edge-tts / viXTTS
```

| Service | Vai trò |
|---|---|
| **postgres** | Job store + user/session + workflow store (schema tự load lần đầu) |
| **minio** | Object storage S3-compatible (bucket + presigned URL) |
| **api** | REST: auth OTP, tạo/poll/cancel job, storage, workflows, AI providers, audio |
| **comfyui** | GPU inference (Wan/Qwen/LTX/ESRGAN…); models tải on-demand từ catalog |
| **worker** | Poll job → chạy pipeline → upload kết quả (Linux/CUDA: `worker.py`) |
| **wf-worker** | Chạy **workflow no-code** (graph nhiều node) — gọi lại các pipeline qua job |

---

## 🚀 Cài đặt — xem `DEPLOY.md`

> **`DEPLOY.md` là tài liệu deploy CHÍNH THỨC** (mô hình "mỗi box 1 chức năng — KHOÁ", Cloudflare Tunnel, tải model, nối FE, lỗi hay gặp). README này chỉ tóm tắt + bổ sung phần cứng và cách chạy nhanh.

### 4 kịch bản setup (PM2 native, KHÔNG Docker)

| Script | Box dùng cho | JOB_TYPES | FE |
|---|---|---|---|
| `setup/fullstack-setup.sh` | **Fullstack** — BE + FE trên 1 box, 1 lệnh | union tất cả | ✅ build + chạy `motions` |
| `setup/setup-pm2.sh` | **Backend full** (mọi tính năng, không FE) | union tất cả | ❌ deploy FE riêng |
| `setup/setup-create-image.sh` | **Chuyên tạo ảnh** (Qwen-Image-Edit) — KHOÁ | `create-image` | ❌ |
| `setup/setup-motion-transfer.sh` | **Chuyên motion theo video** (Wan 2.2 Animate) — KHOÁ | `motion,teen-flycam,trend-tiktok` | ❌ |

```bash
git clone <repo-url> motion-backend && cd motion-backend
./setup/setup-pm2.sh        # full backend, hỏi: Domain · Email admin · Gmail App Password · CF API Token · CORS
```
Script lo hết: cài Node/PM2/Postgres/MinIO (+ Ollama) → dò GPU/driver → ComfyUI + PyTorch CUDA stable tối ưu (RTX 5090: torch 2.12.1 cu130 + SageAttention) → PM2 app → Cloudflare Tunnel + DNS + SSL tự động → seed → in block `.env` cho FE. **Idempotent** (chạy lại an toàn). Chi tiết từng bước, Cloudflare API Token, tải model, gỡ cài → **`DEPLOY.md`**.

### ⚡ Cách nhanh nhất (Docker, có sẵn) — `setup/setup.sh`
```bash
git clone https://github.com/anhld2512/motion-backend.git && cd motion-backend
SUPER_ADMIN=ban@pebsteel.com ./setup/setup.sh          # IP-only
SUPER_ADMIN=ban@pebsteel.com ./setup/setup.sh motion.example.com   # kèm HTTPS (Caddy)
curl http://<IP>:8080/health      # {"ok":true}
```
Tự cài Docker nếu thiếu, sinh secret vào `.env`, nhận GPU (cài NVIDIA toolkit), bật stack. Không GPU → set `COMFY_URL` trỏ ComfyUI sẵn có rồi chạy lại.

### 👤 Admin đầu tiên (BẮT BUỘC)
> Login chỉ qua OTP và backend chỉ chấp nhận email **đã có trong DB**. Deploy mới bảng `users` rỗng → KHÔNG ai vào được, KHÔNG tạo nổi admin. Phải đặt `SUPER_ADMIN=admin@pebsteel.com` (nhiều admin ngăn bằng dấu phẩy) — mỗi lần API khởi động tự "ghim" email này thành `role=admin`, `is_active=true` (idempotent). Sau đó `send-otp` → `verify-otp` → vào quản trị tạo user khác.

### ✉️ OTP qua Gmail (App Password)
> `IS_USED_GMAIL=true` + `GMAIL_USER=ban@gmail.com` + `GMAIL_APP_PASSWORD=abcd efgh ijkl mnop` (tạo tại https://myaccount.google.com/apppasswords, cần bật 2FA — KHÔNG dùng mật khẩu Gmail thường). Đặt `IS_USED_GMAIL=false` để quay lại SMTP thường (`OTP_SMTP_*`: Mailgun/SendGrid/SES/nội bộ).

---

## 🧩 Các node / tính năng

> FE (`motions`) là **workflow builder no-code**: kéo node ra canvas, nối dây, chạy. Mỗi node FE → 1 handler (`api/src/wf-worker/handlers.js`) → gọi 1 **job type** (`worker/worker_runtime/linux.py`, dispatch theo `PIPELINES`). Dưới đây là TOÀN BỘ node, **mục ✨ MỚI là tính năng gần đây**.

### Nguồn / Kết quả
| Node | Làm gì |
|---|---|
| **input** (image/video/audio/file) | Nạp dữ liệu vào workflow. Source: `session` (user upload khi chạy) · `static` (file MinIO) · `url` · `library`. 4 biến thể media là *config variant*, cùng `type='input'` |
| **output** | Trả kết quả về end user (markdown/json/video). Config `cleanup=true` → xả RAM/VRAM cuối workflow (Chandra stop + Ollama unload + ComfyUI `/free`) |

### Ảnh
| Node | Làm gì |
|---|---|
| **create-image** | Qwen-Image-Edit / Flux / Gemini: prompt + 1–3 ảnh ref → ảnh mới. **Dịch VN→EN** qua Ollama trước khi dựng prompt (Qwen không hiểu tiếng Việt) |
| **tryon** | Qwen-Edit / Gemini: model + product → ảnh đã thay đồ (vision auto-detect loại đồ, có shoes detailer + bg composite) |
| **compose** ("Ghép vào mẫu") | Tái dùng job `create-image` đa ảnh: ảnh mẫu base + 1–2 người, GIỮ mặt |

### Video từ ảnh / prompt
| Node | Làm gì |
|---|---|
| **motion** ("Motion Transfer") | Wan 2.2 Animate: ref image + motion video → chuyển động theo pose. ✨ **540p model-on-VRAM** (xem dưới) |
| **ss** ("SS") | LTX-2.3 22B + LoRA user tự train; cổng động 1–3 ảnh; CÓ ảnh → I2V, KHÔNG ảnh → T2V; chia đoạn ~5s nối tiếp |
| **wan-i2v** ("Ảnh → Video (Wan)") | 1 ảnh + prompt EN → Wan 2.1/2.2 I2V; handle `start`/`end` (ảnh đầu/cuối); dùng cho time-lapse BĐS |
| **text-to-video** | Chỉ prompt (không ảnh) → video ngắn Wan2.x T2V / LTX |
| **bds** ("Time Lapse Construction") | 1 ảnh nhà hoàn thiện → time-lapse xây nhà (đất→móng→khung→hoàn thiện) + flycam (ESRGAN + Qwen reverse-stage + Wan FLF + concat dọc) |

### Người nói (lip-sync)
| Node | Làm gì |
|---|---|
| **talk** ("Nói (lip-sync)") | MultiTalk/InfiniteTalk: ảnh nhân vật + thoại + giọng → video nhép miệng |
| **face-motion** ("Người mẫu đọc kịch bản") | talk + HÀNH ĐỘNG theo phong thái (hồn-nhiên / trong-sáng / mạnh-mẽ / cá-tính); AI chia đoạn kịch bản, mỗi đoạn 1 động tác |
| **voiceover** ("Lồng tiếng (đọc mô tả)") | 1 clip + lời thuyết minh + giọng → TTS tiếng Việt ghép lên clip, GIỮ hình & độ dài |

### Dựng phim / ghép
| Node | Làm gì |
|---|---|
| **teaser** | Sản phẩm + storyboard → video quảng cáo điện ảnh (AI dựng bối cảnh từng shot + voiceover edge-tts + nhạc nền), KHÔNG cần người mẫu |
| **lookbook** | teaser CÓ người mẫu (try-on); cùng job `teaser`, khác `inputs.model` + `sceneMode='off'` |
| **story-film** | 1 ô kịch bản → AI đạo diễn tách cảnh / nhân vật (main/supporting/extra) / thoại → phim lip-sync ghép sẵn |
| **concat** ("Ghép cảnh") | Ghép ≥2 clip → 1 video, GIỮ tiếng từng cảnh (ffmpeg) |
| **subtitle** ("Phụ đề + Dịch") | 1 video → ASR (OmniVoice `/asr` hoặc faster-whisper local) + dịch Ollama → cháy hardsub / lồng tiếng Việt / cả hai; realtime từng câu |
| **enhance** ("Nâng chất lượng") | ✨ **MỚI** — upscale video ESRGAN ×4 → 1080p/2K/4K + RIFE fps tuỳ chọn (xem dưới) |

### Tiện ích / Luồng
| Node | Làm gì |
|---|---|
| **chat** | LLM Ollama / custom OpenAI-compat |
| **image** | Vision Ollama mô tả ảnh (qwen2.5vl) |
| **ocr** | Marker/Chandra OCR, cache theo sha256 |
| **http** | Gọi REST API ngoài, template `{{text}}` / `{{metadata.x}}` |
| **api-key** | Khai báo key (`providerType` + `apiKey`); KHÔNG chạy — engine pre-scan phân phối qua `ctx.providerKeys` (Gemini/Veo/custom; self-host không cần) |
| **condition** | if-else theo expression JS (nhánh true/false) |
| **validate** | check `notEmpty/minLength/isJson/contains` (fail → throw) |
| **transform** | trim/uppercase/lowercase/template/regex-replace/json-extract |
| **debug** | pass-through + log preview |
| **gpu-warmup** / **gpu-free** | warm / xả Chandra · Ollama · ComfyUI |
| **workflow** | Gọi workflow khác (nested, max depth 5, chống cycle) |

> ⚠️ **Drift cục bộ:** repo local đang đi sau VPS — `NODE_HANDLERS` trong `handlers.js` local CHƯA có key `wan-i2v` và `voiceover` (dù palette FE đã chào). 2 node này chỉ chạy được khi bản `handlers.js` trên box đã có mapping. Xác nhận trực tiếp trên box trước khi tin là "đã hỗ trợ".

### ✨ Chức năng MỚI gần đây (nhấn mạnh)

**Node "Nâng chất lượng" (enhance)** — `JOB_TYPES=enhance`. Upscale video bằng ESRGAN ×4 (model qua env `MOTION_UPSCALE_MODEL`, mặc định `4x-UltraSharp`) lên **1080p / 2K / 4K**, rồi tuỳ chọn nội suy **RIFE lên 30/60fps**. Chạy SAU clip motion đã render, tự recycle GPU/RAM giữa stage.
> ⚠️ **4K bị KHOÁ cứng 16fps** — 4K + nội suy = OOM RAM. Các preset cao (4K, 15s-720p) đều khoá 16fps.

**Motion 540p model-on-VRAM (dynamic VRAM)** — frame-gate: clip **≤540p và ≤250 frame** → model nằm THẲNG trên VRAM (RAM tụt còn ~22GB, nhanh hơn, model rời RAM); clip dài / 720p / 30fps → tự chuyển sang **offload sang RAM** (chống OOM). 2 knob: `MOTION_VRAM_MAX_FRAMES` (250) · `MOTION_VRAM_MAX_EDGE` (968 = cạnh dài của 540p dọc 544×960). Ép tay: `MOTION_BLOCK_SWAP` / `MOTION_LOAD_DEVICE`.
> Trước đây từng revert về "offload mọi độ phân giải"; bản mới mở lại đường VRAM nhưng CÓ gate an toàn — không tự đẩy clip nặng lên VRAM.

**Tryon "chỉ làm sạch" (clean-only)** — đổi workflow Qwen sang img2img **denoise thấp** (`QWEN_CLEAN_DENOISE=0.30`) để GIỮ ĐÚNG màu gốc của ảnh, không tô lại → trị ám tím / ám vàng. Bỏ luôn node `ColorMatch` thừa trong nhánh clean. Chỉnh độ "đụng vào ảnh" qua `QWEN_CLEAN_DENOISE` (thấp = giữ nguyên nhiều hơn).

**RIFE (nội suy frame) 30/60fps** — node `ComfyUI-Frame-Interpolation`, model `rife47.pth` **tự tải** lần render đầu. Có **fallback mềm**: thiếu node `RIFE VFI` → BỎ QUA nội suy thay vì fail 400; sau recycle ComfyUI thì chờ node nạp lại để tránh race.
> Nội suy ngốn thêm VRAM → đó là lý do preset cao bị khoá 16fps.

**3 model upscale trong catalog** — thêm vào `catalog.json` (motion + standard): `4x-UltraSharp` (**mặc định**), `4x_foolhardy_Remacri`, `RealESRGAN_x4plus`. Chọn qua `MOTION_UPSCALE_MODEL`. Tải từ **FE → Settings → Models AI**; box chỉ thấy model trong catalog của feature.

---

## 🖥️ Cấu hình phần cứng (chọn máy / VPS)

> Khâu nặng DUY NHẤT = ComfyUI chạy Wan 2.2 Animate (14B) trên GPU NVIDIA/CUDA; các service khác đều nhẹ → câu hỏi cốt lõi là **đặt GPU ở đâu**. 2 kịch bản: **A.** VPS GPU full-stack (production) · **B.** máy điều khiển nhẹ + box GPU riêng (set `COMFY_URL`).

### Box GPU (chạy ComfyUI / Wan 2.2 Animate) — phần quyết định "mượt"
Tổng trọng lượng model nạp cùng lúc ≈ **26–28GB**: diffusion fp8 `Wan2.2-Animate-14B` (~17GB) + text-encoder `umt5-xxl` (~11GB) + VAE/clip-vision (~1.5GB), chưa kể latent video 720p.

| Hạng mục | Tối thiểu | Khuyến nghị |
|---|---|---|
| **GPU (VRAM)** | **24GB** — RTX 4090/3090, L4, A10 (đẩy encoder ra RAM + BlockSwap, chậm/cold-start lâu) | **32GB — RTX 5090** (nạp cả bộ vào VRAM, dư cho batch + `sageattention`) |
| CPU | 8 vCPU (teaser montage + edge-tts dùng CPU) | 12+ vCPU |
| RAM | 32GB | 64GB |
| Disk (SSD/NVMe) | **100GB** (models ~25GB + image CUDA + output) | 200GB |
| OS | Ubuntu 22.04/24.04 + NVIDIA driver + `nvidia-container-toolkit` (script tự cài) | — |

> **<24GB kể cả 16GB** ❌ (RTX 4060 Ti 16GB, A4000) KHÔNG kham nổi, không khuyến nghị.
> Thuê GPU theo giờ (RunPod/Vast.ai/Lambda): chọn ổ ≥100GB + persist volume `comfymodels` để khỏi tải lại 25GB. RTX 4090 ~$0.4/h · RTX 5090 ~$0.7/h · L40S 48GB ~$0.8–1.1/h.

### Máy điều khiển nhẹ — kịch bản B (không GPU)
Chỉ chạy `api` + `postgres` + `minio` + `worker`. **2–4 vCPU · 8GB RAM · 60GB SSD** đủ cho motion/tryon đơn lẻ. Worker chạy teaser tại chỗ (ffmpeg ghép nhiều shot) → nâng **8 vCPU · 16GB RAM**, hoặc tách worker teaser sang box GPU bằng `JOB_TYPES`.

### Tóm tắt nhanh — "chạy mượt" cần gì
- **Production 1 máy:** VPS GPU NVIDIA 32GB (RTX 5090) · 8+ vCPU · 32–64GB RAM · 100GB+ NVMe · Ubuntu 22.04.
- **Tối thiểu chấp nhận được:** GPU 24GB (RTX 4090) · 8 vCPU · 32GB RAM · 100GB NVMe.

---

## 🔌 Dùng API (client)

**1. Đăng nhập (OTP) lấy JWT:**
```bash
curl -X POST http://<IP>:8080/auth/send-otp -H 'Content-Type: application/json' -d '{"email":"you@pebsteel.com"}'
curl -X POST http://<IP>:8080/auth/verify-otp -d '{"email":"you@pebsteel.com","otp":"123456"}'
# → { "access_token":..., "refresh_token":... }
```

**2. Tạo job motion:**
```bash
curl -X POST http://<IP>:8080/jobs -H "X-API-Key: <API_KEY>" \
  -F type=motion -F 'params={"preset":"5s-720p","width":720,"height":1280,"frames":81,"steps":4}' \
  -F ref=@person.png -F motion=@dance.mp4
# → { "id":..., "status":"queued" }
```

**3. Poll đến khi xong:**
```bash
curl http://<IP>:8080/jobs/<id> -H "X-API-Key: <API_KEY>"
# → status=done → { ..., "output_url": "<presigned URL>" }
```
Đổi `type` sang `tryon`/`create-image`/`teaser`/`enhance`… với field input tương ứng là dùng pipeline khác, CÙNG một API.

**Các nhóm route chính:** `auth` · `jobs` (+ `/download` `/logs` `/stream`) · `storage-files` · `workflows` (+ `/:slug/invoke` `/runs`) · `ai-providers` · `audio` · `worker-status` · `job-reports/stats` · `health`.

---

## ⚙️ Kiến trúc job (dễ mở rộng)

1 bảng `jobs` generic: `type` (motion|tryon|enhance…) · `inputs` (storage keys theo field upload) · `params` · `output_key`. Worker poll `POST /worker/claim {types}` (atomic, `SKIP LOCKED`) → chạy pipeline theo `type` → `POST /jobs/:id/output`.

| Job type | Pipeline (ngắn) |
|---|---|
| `motion` · `bds` | Wan 2.2 Animate / FLF (pose-driven, time-lapse) |
| `tryon` · `create-image` | Qwen-Edit / Flux / SD3.5 (ảnh, edit đa ảnh) |
| `teaser` (`lookbook`) · `story-film` · `concat` | montage / đạo diễn AI / ghép clip |
| `talk` · `face-motion` | MultiTalk/InfiniteTalk lip-sync |
| `video` · `text-to-video` · `ss` | LTX / Wan I2V·T2V |
| `subtitle` · `enhance` | ASR+dịch / upscale+RIFE |

> ⚠️ `JOB_TYPES` (env) mặc định CHỈ `"motion"` (hoặc 1 type của box chuyên). Bật type mới **phải** set lại `JOB_TYPES` đầy đủ + recreate worker — chỉ git/code KHÔNG đủ. Bản mới đã thêm `product-overlay`, `voiceover`, `wan-i2v`, `enhance` vào `JOB_TYPES` mặc định ở các file config.

**Thêm pipeline mới:** viết hàm `run_xxx(job)` trong `worker/worker_runtime/linux.py`, đăng ký vào `PIPELINES`, thêm `type` vào `JOB_TYPES`.

---

## 🌐 Domain + HTTPS

API chạy ở cổng `${API_PORT}` (mặc định `8080`). 3 cách đưa ra domain công khai có HTTPS:
- **Caddy** (tự động, gọn nhất) — `./setup/setup.sh motion.example.com` đã làm sẵn; hoặc set `DOMAIN` + `--profile proxy`. Cần mở 80/443, DNS trỏ thẳng IP.
- **Nginx + Certbot** — khi VPS đã có Nginx / nhiều site. Nhớ `client_max_body_size 250M` + tắt buffering cho SSE (`/jobs/:id/stream`).
- **Cloudflare Tunnel** ⭐ — khi VPS **không mở được 80/443** (NAT, GPU thuê map port ngẫu nhiên). `cloudflared` mở đường outbound, Cloudflare lo TLS. **Cách setup tự động 100% qua API Token → xem `DEPLOY.md` (Bước 2).**

> Set `PUBLIC_BASE_URL=https://<DOMAIN>` → client tải output qua API trên 1 domain (khỏi expose MinIO). Cloudflare Free giới hạn upload 100MB/request → file lớn tải trực tiếp MinIO bằng presigned URL.

---

## 🗺️ Hướng phát triển (roadmap)

Kiến trúc job + workflow generic thiết kế để mở rộng KHÔNG phá phần đang chạy:
- **Thêm pipeline GPU mới** (image-to-3D, inpaint/outpaint, restore video — chỉ cần 1 hàm `run_xxx` + đăng ký type).
- **Workflow builder phong phú hơn** (node điều kiện/nhánh đã có; thêm chạy lại từng bước, template chia sẻ).
- **Scale worker** (nhiều worker/nhiều GPU box cùng poll 1 queue, phân loại job theo `JOB_TYPES` tách máy nặng–nhẹ).
- **Webhook/callback** (báo done/error về client thay vì poll).
- **Đa engine** (cắm thêm backend inference khác — API thương mại, server riêng — sau cùng 1 lớp pipeline).
- **Quản trị & quota** (phân quyền role, hạn mức job, dashboard — đã có nền `job-reports`).
- **Lưu trữ phân tầng** (tự dọn output cũ, chuyển file nguội sang bucket rẻ hơn).

---

## 📝 Lưu ý

- **Storage** = MinIO (S3); client tải output qua presigned URL; worker tải input qua API (stream nội bộ từ MinIO).
- **Auth:** `X-API-Key` (client gọi job) · JWT session (login OTP, route người dùng) · `X-Worker-Token` (worker, nội bộ). Đổi hết trong `.env`.
- **Models** (`comfyui/models.txt` + `catalog*.json`): URL HuggingFace đã verify; repo gated → set `HF_TOKEN`, hoặc mount models sẵn vào volume `comfymodels` để skip tải. Box chuyên chỉ thấy model trong `catalog-<feature>.json` (KHOÁ).
- **Attention:** ComfyUI mặc định `attention_mode=sdpa` (portable); có `sageattention` (CUDA) → set `params.attention_mode="sageattn"` cho nhanh hơn.
- **GPU contention:** 1 GPU chia sẻ giữa ComfyUI / Ollama / OCR → mặc định `WORKER_CONCURRENCY=1` + `WF_CONCURRENCY=1` chống tranh VRAM. Đây là nguyên nhân thường gặp của latency/cold-start.
- **Deploy đúng cách:** sửa code qua commit → CI/CD hoặc setup script, **KHÔNG hot-edit trên VPS** (push `main` = auto-deploy prod).
