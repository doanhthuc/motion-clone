// ecosystem.config.cjs — Bản đồ tiến trình PM2 cho Motion Backend chạy NATIVE (KHÔNG Docker).
// Sinh kèm bởi setup-pm2.sh. Đọc .env (biến thô) rồi DẪN XUẤT các biến mà docker-compose.yml
// vốn tính sẵn (DATABASE_URL, S3_ENDPOINT, S3_ACCESS_KEY…) để app chạy y hệt bản Docker.
//
//   pm2 start ecosystem.config.cjs       # khởi động tất cả
//   pm2 restart ecosystem.config.cjs     # nạp lại sau khi sửa code/.env
//   pm2 logs api                         # xem log 1 service
//
// LƯU Ý: api/wf-worker chỉ đọc process.env (không có dotenv), nên TẤT CẢ env phải truyền qua đây.
const fs = require("fs")
const path = require("path")

const ROOT = __dirname

// ── Parser .env tối giản (KEY=VALUE, bỏ qua comment/blank, strip nháy bao ngoài) ──
function loadEnv(file) {
  const out = {}
  let raw = ""
  try { raw = fs.readFileSync(file, "utf8") } catch { return out }
  for (const line of raw.split("\n")) {
    const s = line.trim()
    if (!s || s.startsWith("#")) continue
    const i = s.indexOf("=")
    if (i < 0) continue
    const k = s.slice(0, i).trim()
    let v = s.slice(i + 1).trim()
    // bỏ comment đuôi dòng kiểu `value   # ghi chú` (chỉ khi value không nằm trong nháy)
    if (!(v.startsWith('"') || v.startsWith("'"))) {
      if (v.startsWith("#")) v = ""
      else {
        const h = v.indexOf(" #")
        if (h >= 0) v = v.slice(0, h).trim()
      }
    }
    if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) v = v.slice(1, -1)
    out[k] = v
  }
  return out
}

const E = loadEnv(path.join(ROOT, ".env"))
// ALD 02/07/2026 - fallback theo os.homedir() thay hardcode /home/ubuntu: VPS chạy user khác (root, debian,
// paperspace…) vẫn trỏ đúng home thật của user đang chạy PM2.
const HOME = process.env.HOME || require("os").homedir()

// ── Cổng/host native (mỗi service nghe localhost; chỉ nginx mới expose ra ngoài) ──
const API_PORT = E.API_PORT || "8080"
const MINIO_API_PORT = E.MINIO_API_PORT || "9000"
const MINIO_CONSOLE_PORT = E.MINIO_CONSOLE_PORT || "9001"
const COMFY_PORT = E.COMFY_PORT || "8188"
const COMFY_URL = E.COMFY_URL || `http://127.0.0.1:${COMFY_PORT}`
const OLLAMA_URL = E.OLLAMA_URL || "http://127.0.0.1:11434"
// ALD 16/06/2026 - cổng bg-remover cấu hình được (box chuyên dùng 8000; box dùng chung supabase đổi qua .env
// vì supabase-kong giữ 8000 + 8001-8004). Đặt BG_REMOVER_PORT trong .env để né đụng.
const BG_REMOVER_PORT = E.BG_REMOVER_PORT || "8000"
const BG_REMOVER_URL = E.BG_REMOVER_URL || `http://127.0.0.1:${BG_REMOVER_PORT}`
const COMFY_DIR = E.COMFY_DIR || path.join(HOME, "ComfyUI")
const MINIO_DATA = E.MINIO_DATA_DIR || path.join(ROOT, ".data", "minio")

// ── Biến DẪN XUẤT (compose tính sẵn; bản native phải tự dựng) ──
const DERIVED = {
  DATABASE_URL: `postgres://${E.POSTGRES_USER || "motion"}:${E.POSTGRES_PASSWORD || ""}@127.0.0.1:${E.POSTGRES_PORT || "5432"}/${E.POSTGRES_DB || "motion"}`,
  S3_ENDPOINT: `http://127.0.0.1:${MINIO_API_PORT}`,
  S3_PUBLIC_ENDPOINT: E.S3_PUBLIC_ENDPOINT || `http://127.0.0.1:${MINIO_API_PORT}`,
  S3_REGION: E.S3_REGION || "us-east-1",
  S3_ACCESS_KEY: E.MINIO_ROOT_USER || "motionminio",
  S3_SECRET_KEY: E.MINIO_ROOT_PASSWORD || "",
  STORAGE_BUCKET: E.STORAGE_BUCKET || "motion",
  BG_REMOVER_URL,
  COMFY_URL,
  OLLAMA_URL,
  // Luôn truyền đường dẫn tuyệt đối cho PM2. PATH của process nền thường không có
  // ~/.local/bin dù binary đã được setup-pm2.sh cài đúng ở đó.
  YTDLP_BIN: E.YTDLP_BIN || path.join(HOME, ".local", "bin", "yt-dlp"),
}

// base = .env thô + biến dẫn xuất. App đọc qua process.env.
const base = { ...E, ...DERIVED }

const COMMON = { autorestart: true, max_restarts: 50, restart_delay: 4000, time: true }

const apps = []

// ── MinIO (object storage S3-compatible — thay Supabase Storage) ──
apps.push({
  ...COMMON,
  name: "minio",
  script: "/usr/local/bin/minio",
  interpreter: "none",
  args: `server ${MINIO_DATA} --address :${MINIO_API_PORT} --console-address :${MINIO_CONSOLE_PORT}`,
  env: {
    MINIO_ROOT_USER: E.MINIO_ROOT_USER || "motionminio",
    MINIO_ROOT_PASSWORD: E.MINIO_ROOT_PASSWORD || "",
  },
})

// ── API NodeJS (REST tạo/poll job + upload/serve file + auth OTP) ──
apps.push({
  ...COMMON,
  name: "api",
  cwd: path.join(ROOT, "api"),
  script: "src/server.js",
  // ALD 14/06/2026 - MODEL_UPLOADS_DIR: native (PM2) ghi thẳng vào search-path ComfyUI (KHÁC docker = /model-uploads).
  // = $COMFY_DIR/models/uploads (khớp extra_model_paths.yaml setup-pm2.sh tạo). Route /models dùng để lưu/list/xoá.
  env: { ...base, PORT: API_PORT, APP_NAME: E.APP_NAME || "Motions",
         MODEL_UPLOADS_DIR: E.MODEL_UPLOADS_DIR || path.join(COMFY_DIR, "models", "uploads") },
})

// ── wf-worker (engine workflow: chat/ocr/image/http…) ──
apps.push({
  ...COMMON,
  name: "wf-worker",
  cwd: path.join(ROOT, "api"),
  script: "src/wf-worker/worker.js",
  // WF_CONCURRENCY=1 mặc định: chạy TUẦN TỰ (1 run, còn lại vô hàng đợi). Tăng qua .env nếu muốn song song.
  env: { ...base, WF_POLL_INTERVAL_SEC: E.WF_POLL_INTERVAL_SEC || "2", WF_CONCURRENCY: E.WF_CONCURRENCY || "1" },
})

// #region ALD 16/07/2026 - Auto Task Cloud chạy nền, không phụ thuộc tab trình duyệt.
apps.push({
  ...COMMON,
  name: "task-cloud-auto",
  cwd: path.join(ROOT, "api"),
  script: "src/task-cloud/auto-worker.js",
  env: {
    ...base,
    TASK_CLOUD_AUTO_POLL_SEC: E.TASK_CLOUD_AUTO_POLL_SEC || "5",
  },
})
// #endregion

// ── worker Python (poll API → chạy ComfyUI → upload kết quả; toàn bộ pipeline GPU) ──
apps.push({
  ...COMMON,
  name: "worker",
  cwd: path.join(ROOT, "worker"),
  // ALD 16/06/2026 - chạy qua launcher bash (scripts/pm2-python.sh) thay vì interpreter=python tuyệt đối:
  // né bug PM2 6 + Node ≥24 (ProcessContainerForkBun.js → Python parse JS → crashloop). cwd=worker/ → ./venv đúng.
  script: path.join(ROOT, "scripts", "pm2-python.sh"),
  interpreter: "bash",
  env: {
    ...base,
    PM2_PY_ENTRY: "worker.py",
    WORKER_SCRIPT: "worker.py",
    API_URL: `http://127.0.0.1:${API_PORT}`,          // INTERNAL_API_URL của compose = http://api:8080 (tên docker)
    WORKER_TOKEN: E.WORKER_TOKEN || "",
    WORKER_ID: E.WORKER_ID || "worker-1",
    POLL_INTERVAL_SEC: E.POLL_INTERVAL_SEC || "3",
    // WORKER_CONCURRENCY=1 mặc định: 1 job GPU chạy/lúc, còn lại QUEUE (tránh 2 job tranh GPU/VRAM).
    // Tăng qua .env (WORKER_CONCURRENCY=2) nếu muốn song song + để ComfyUI tự serialize GPU.
    WORKER_CONCURRENCY: E.WORKER_CONCURRENCY || "1",
    WORKER_MIN_AVAIL_GB: E.WORKER_MIN_AVAIL_GB || "12",
    WORKER_MAX_SWAP_PCT: E.WORKER_MAX_SWAP_PCT || "70",
    // ALD 13/07/2026 - Baseline fullstack GPU: SageAttention đã được installer verify trong Comfy venv.
    // torch.compile vẫn opt-in vì warmup/VRAM spike; các cap 540p giữ job Wan 14B trong 32GB của RTX 5090.
    MOTION_ATTENTION: E.MOTION_ATTENTION || "sageattn",
    MOTION_TORCH_COMPILE: E.MOTION_TORCH_COMPILE || "0",
    MOTION_SHORT: E.MOTION_SHORT || "540",
    MOTION_VRAM_MAX_EDGE: E.MOTION_VRAM_MAX_EDGE || "968",
    MOTION_VRAM_MAX_FRAMES: E.MOTION_VRAM_MAX_FRAMES || "250",
    // ALD 24/07/2026 - Khóa profile FlashVSR nhanh đã đo trên RTX 5090:
    // chunk 100 giữ RAM ổn định nhưng KHÔNG spatial tile (tile 4 ô làm mỗi chunk chạy lặp 4 lần,
    // 480 frame tăng từ ~76 giây lên ~17 phút). Ghi tường minh để PM2 không giữ env cũ sau deploy.
    MOTION_FLASHVSR_MODE: E.MOTION_FLASHVSR_MODE || "tiny",
    MOTION_FLASHVSR_ATTENTION: E.MOTION_FLASHVSR_ATTENTION || "sparse_sage_attention",
    MOTION_FLASHVSR_TILED: E.MOTION_FLASHVSR_TILED || "0",
    MOTION_FLASHVSR_CHUNK: E.MOTION_FLASHVSR_CHUNK || "100",
    // ALD 01/07/2026 - GẮN CỨNG, KHÔNG đọc .env (E.JOB_TYPES): job type là DANH MỤC TÍNH NĂNG của code, không
    // phải cấu hình môi trường. Thêm node mới = sửa DÒNG NÀY + PIPELINES trong worker, KHỎI đụng .env từng box
    // (trước đây .env cũ thiếu type mới → job treo queued mãi). Muốn shard 1 box nhận 1 phần thì set env
    // JOB_TYPES thủ công lúc chạy (docker/systemd), hoặc thêm lại `E.JOB_TYPES ||` ở đây.
    // ALD 20/07 - wan-dancer (Wan-Dancer-14B music-to-dance): AN TOÀN để trong list chung — API hard-block VRAM≥90GB
    // nên box <90GB (vd .165) KHÔNG BAO GIỜ tạo được job wan-dancer ⇒ worker box đó chẳng có gì để claim. Trên VPS
    // full-stack ≥90GB thì API cho tạo + worker claim. Cần weights DiffSynth (worker/wan_dancer/download_models.sh) + WAN_DANCER_MODEL.
    JOB_TYPES: "motion,bds,tryon,create-image,edit-image,product-overlay,teaser,video,text-to-video,ss,talk,face-motion,concat,story-film,subtitle,voiceover,wan-i2v,teen-flycam,enhance,trend-tiktok,reveal,wan-dancer",  // ALD 08/07 - reveal (đè lộ) ffmpeg nhẹ
    // ALD 20/07 - Wan-Dancer (DiffSynth raw, KHÔNG ComfyUI). Trống = node khóa/không chạy. Set trong .env trên VPS ≥90GB.
    WAN_DANCER_MODEL: E.WAN_DANCER_MODEL || "",  // thư mục weights Wan-Dancer-14B (global+local+encoder+VAE)
    WAN_DANCER_PY: E.WAN_DANCER_PY || "",        // python env có DiffSynth-Studio (rỗng = dùng python worker)
    VISION_MODEL: E.VISION_MODEL || "qwen2.5vl:7b",
    TRYON_SHOES_MP: E.TRYON_SHOES_MP || "2.0",
    TRYON_PRODUCT_AUTOCROP: E.TRYON_PRODUCT_AUTOCROP || "1",
    // ALD 15/06/2026 - TTS/ASR services (native trên GPU box). OMNIVOICE_URL trống = tắt (về viXTTS/Gemini).
    // OMNIVOICE /asr dùng cho node "Phụ đề + Dịch"; /tts cho lồng tiếng. Đặt URL trong .env để bật.
    OMNIVOICE_URL: E.OMNIVOICE_URL || "",
    OMNIVOICE_REF: E.OMNIVOICE_REF || "",
    OMNIVOICE_LANG: E.OMNIVOICE_LANG || "vietnamese",
    OMNIVOICE_ASR_MODEL: E.OMNIVOICE_ASR_MODEL || "medium",
    VIXTTS_URL: E.VIXTTS_URL || "",
  },
})

// ── bg-remover (rembg: tách nền model + crop sản phẩm tryon) ──
apps.push({
  ...COMMON,
  name: "bg-remover",
  cwd: path.join(ROOT, "bg-remover"),
  // ALD 16/06/2026 - launcher bash (né bug PM2 6 + Node ≥24 với interpreter=python). cwd=bg-remover/ → ./venv đúng.
  script: path.join(ROOT, "scripts", "pm2-python.sh"),
  interpreter: "bash",
  env: { PORT: BG_REMOVER_PORT, PM2_PY_ENTRY: "app.py" },
})

// ── ComfyUI native (bật khi COMFY_LOCAL=1) ──
if ((E.COMFY_LOCAL || "") === "1") {
  apps.push({
    ...COMMON,
    name: "comfyui",
    cwd: ROOT,
    script: path.join(ROOT, "scripts", "comfy-start-native.sh"),
    interpreter: "bash",
    min_uptime: 30000,     // ComfyUI load model lâu — đừng coi crash sớm là loop
    // ALD 13/07/2026 - Forward tuning từ .env; PYTORCH_ALLOC_CONF là tên chuẩn mới
    // (PYTORCH_CUDA_ALLOC_CONF chỉ còn alias tương thích ngược trong PyTorch 2.12+).
    env: {
      COMFY_PORT,
      COMFY_DIR,
      PYTORCH_ALLOC_CONF: E.PYTORCH_ALLOC_CONF || E.PYTORCH_CUDA_ALLOC_CONF || "expandable_segments:True",
      CUDA_MODULE_LOADING: E.CUDA_MODULE_LOADING || "LAZY",
      // ALD 10/08/2026 - Chiến lược cache node của ComfyUI. Mặc định --cache-none vì đây là WORKER:
      // mỗi job là prompt mới nên cache gần như không trúng, chỉ ôm tensor ảnh tới lúc bị cgroup
      // OOM-kill. Để A/B: COMFY_CACHE_ARGS="--cache-ram 4 24" (hoặc rỗng = mặc định ComfyUI).
      ...(E.COMFY_CACHE_ARGS === undefined ? {} : { COMFY_CACHE_ARGS: E.COMFY_CACHE_ARGS }),
    },
  })
}

module.exports = { apps }
