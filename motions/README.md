# Motions

Pebsteel Motion Transfer Pipeline — Nuxt 4 FE cho ComfyUI + Wan 2.2 Animate.

## Stack

- **Frontend**: Nuxt 4 (Vue 3, Nitro)
- **Backend Auth**: Supabase self-hosted (dùng chung với `pebsteel-ai`) tại `https://supabase.pebsteel.com`
- **GPU Worker**: ComfyUI + Wan 2.2 Animate trên VPS `101.99.25.165` (bridgellm-02, RTX 5090 32GB)
- **State**: Pinia
- **Utilities**: VueUse

## Cấu trúc (Nuxt 4)

```
motions/
├── app/                    # Application code
│   ├── assets/             # CSS, images
│   ├── components/         # Vue components (auto-import)
│   │   ├── ui/             # Toast, Confirm, Button, Input, OtpInput, Card
│   │   └── motion/         # Motion-specific components
│   ├── composables/        # useAuth, useToast, useConfirm, useApi, useMotionJobs
│   ├── layouts/default.vue
│   ├── middleware/         # auth.js, admin.js
│   ├── pages/              # index, login, runs
│   ├── plugins/
│   ├── utils/              # cn, format
│   ├── app.vue
│   ├── app.config.js
│   └── error.vue
├── server/                 # Nitro server (proxy ComfyUI)
├── shared/                 # Types/utils dùng chung FE↔server
├── public/
├── nuxt.config.js
└── package.json
```

## Khởi chạy

```bash
# 1. Cài deps
npm install

# 2. Tạo file env
cp .env.example .env
# rồi điền NUXT_PUBLIC_SUPABASE_ANON_KEY

# 3. Dev server (port 2030)
npm run dev
```

Truy cập: <http://localhost:2030>

## Scripts

| Lệnh | Tác dụng |
| --- | --- |
| `npm run dev` | Dev server cổng 2030 |
| `npm run build` | Build production |
| `npm run preview` | Preview build (2030) |
| `npm run start` | Chạy build |
| `npm run typecheck` | Kiểm tra TS |

## Endpoints

- `GET /api/health` — health check
- (TODO) `POST /api/motion/queue` — đẩy job ComfyUI
- (TODO) `GET /api/motion/runs/:id` — poll trạng thái

## Auth

Dùng chung Supabase với `pebsteel-ai` (cookie `pebsteel_session`). User đã login pebsteel-ai sẽ login motions luôn nếu cùng domain.

Endpoints OTP: `/functions/v1/send-otp`, `/functions/v1/verify-otp` của Supabase BE.

## GPU Worker (VPS)

VPS `ubuntu@101.99.25.165` (bridgellm-02). Plan setup ComfyUI ở [Plans/index.md](Plans/index.md).

## Quy tắc viết prompt — node Create Image (và các node ảnh/video)

<!-- #region ALD 11/06/2026 - đúc kết từ các lỗi thực tế: negative dán nhầm vào Prompt → vẽ ngược (braid → tóc tết);
     ảnh Model mẫu đè kiểu tóc; fal content checker chặn lingerie. Sửa quy tắc ở đây khi worker đổi hành vi. -->

### 1. Prompt và Negative là 2 Ô RIÊNG — đừng trộn

- Ô **Prompt**: chỉ ghi thứ **MUỐN** xuất hiện.
- Ô **Negative prompt**: chỉ ghi thứ **KHÔNG MUỐN**.
- ⚠️ Dán danh sách "cần tránh" vào ô Prompt = model hiểu ngược là **muốn vẽ** chúng (đã có ca thật: `braid, twin tails` nằm trong Prompt → ra đúng cô gái tóc tết 2 bím).

### 2. Negative chỉ ghi phần RIÊNG của ảnh đó

Worker **tự merge sẵn** bộ negative nền (chống nhựa/plastic skin, cartoon/anime, 3D render, dị dạng tay chân, chữ/logo/watermark, phông studio giả…). **Đừng dán lại** các từ đó — chỉ làm loãng. Negative của bạn chỉ cần phần đặc thù, ví dụ chống tóc dài:

```
long hair, very long hair, shoulder-length hair, ponytail, braid, twin tails, hair bun
```

### 3. Đặc điểm quan trọng (tóc, kính, râu…) — tả SỚM và DỨT KHOÁT

Chế độ **Model mẫu** (0 ảnh ref) ghép gương mặt từ ảnh mẫu — ảnh ref có xu hướng **đè kiểu tóc** lên prompt. Worker đã có lệnh tự động ép thay tóc theo mô tả (CRITICAL HAIR RULE), nhưng để chắc ăn:

- Tả tóc **ngay câu đầu**, kèm khẳng định: `SHORT WAVY BLACK BOB haircut, chin-length hair (this exact hairstyle is mandatory)`.
- Kèm negative chống kiểu tóc ngược lại (mục 2).

### 4. Cấu trúc prompt khuyên dùng — ngắn, KHÔNG lặp

Qwen-Edit ăn prompt gọn + dứt khoát hơn là dài lặp đi lặp lại. 4 đoạn theo thứ tự:

```
[1. Chủ thể] người + tóc + mặt + kính/phụ kiện + biểu cảm
[2. Trang phục] kiểu/màu/chất liệu
[3. Bối cảnh] địa điểm + ánh sáng + thời điểm
[4. Chất ảnh] DSLR, 85mm f/1.4, shallow DOF, realistic skin pores, RAW photo, natural color tones
```

### 5. Provider

Mặc định mọi node chạy **Self-host** (GPU local) — **không kiểm duyệt nội dung, không cần key**. Gemini (admin-only) cần key qua node **API Key** (nối cổng hoặc đặt rời trên canvas).

> Provider HuggingFace đã **gỡ 11/06/2026**: fal content checker chặn nội dung lingerie (use-case chính), không có motion-transfer, credit $2/tháng quá nhỏ. Code backend còn ngủ đông — cần bật lại thì thêm lại tile trong các Inspector.

### 6. Cặp input mẫu chuẩn (Self-host)

**Prompt:**

```
A beautiful young East Asian woman with a SHORT WAVY BLACK BOB haircut, chin-length hair (this exact hairstyle is mandatory), wearing round black glasses, soft fair skin, natural makeup, large brown eyes, gentle smile, looking at camera.

Wearing an elegant red lace nightgown with delicate floral lace details, tasteful and classy.

Close-up portrait, sitting near the bed in a cozy modern bedroom, soft bed linens, warm golden hour window light.

Professional DSLR photography, 85mm lens, f/1.4, shallow depth of field, creamy bokeh, realistic skin pores, detailed hair strands, photorealistic RAW photo, natural color tones, sharp focus on eyes.
```

**Negative prompt:**

```
long hair, very long hair, shoulder-length hair, ponytail, braid, twin tails, hair covering ears below chin, slicked back hair, hair bun
```

<!-- #endregion -->
