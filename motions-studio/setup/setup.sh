#!/usr/bin/env bash
# ============================================================================
# setup.sh — Cài Motion Backend bằng 1 lệnh.
#
#   • Linux + NVIDIA: tự cài Docker + GPU toolkit + bật cả stack (ComfyUI trong Docker).
#   • macOS: KHÔNG hỗ trợ (đã bỏ nhánh MPS native 20/07/2026).
#
# Dùng (chạy từ thư mục gốc repo HOẶC từ setup/):
#   ./setup/setup.sh                       # Linux only
#   ./setup/setup.sh motion.example.com    # (Linux) kèm domain → bật HTTPS (Caddy)
# ============================================================================
set -euo pipefail
# ALD 14/06/2026 - script nằm trong setup/ → về gốc repo (mọi path .env/docker-compose/comfyui tính từ đây).
cd "$(dirname "$0")/.."

DOMAIN="${1:-}"
say()  { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m  ! %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m  ✓ %s\033[0m\n' "$*"; }

# sed in-place chạy được cả Linux lẫn macOS
set_kv() { sed -i.bak "s|^$1=.*|$1=$2|" .env && rm -f .env.bak; }
# ALD 24/06/2026 - upsert: sửa nếu key có sẵn, THÊM nếu chưa (vài biến Mac không có trong .env.example).
put_kv() { if grep -q "^$1=" .env; then set_kv "$1" "$2"; else printf '%s=%s\n' "$1" "$2" >> .env; fi; }
rnd()    { openssl rand -hex 24 2>/dev/null || LC_ALL=C tr -dc 'a-zA-Z0-9' </dev/urandom | head -c 40; }
install_ytdlp_native() {
  if command -v yt-dlp >/dev/null 2>&1; then
    say "Cập nhật yt-dlp…"
    if command -v python3 >/dev/null 2>&1 && python3 -m pip install --user -U --pre 'yt-dlp[default,curl-cffi]' >/dev/null 2>&1; then
      ok "yt-dlp nightly OK ($HOME/.local/bin/yt-dlp)"
      return 0
    fi
    if yt-dlp --update-to nightly >/dev/null 2>&1; then
      ok "yt-dlp OK ($(command -v yt-dlp))"
      return 0
    fi
    ok "yt-dlp OK ($(command -v yt-dlp))"
    return 0
  fi
  say "Cài yt-dlp (import URL Facebook/TikTok/YouTube)…"
  if command -v python3 >/dev/null 2>&1; then
    python3 -m pip install --user -U --pre 'yt-dlp[default,curl-cffi]' >/dev/null 2>&1 && {
      warn "yt-dlp nightly đã cài qua pip user. Nếu shell chưa thấy binary, thêm ~/.local/bin vào PATH hoặc set YTDLP_BIN."
      return 0
    }
  fi
  if command -v brew >/dev/null 2>&1; then
    brew install yt-dlp >/dev/null 2>&1 && { ok "yt-dlp OK"; return 0; }
  fi
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -y >/dev/null 2>&1 || true
    sudo apt-get install -y yt-dlp >/dev/null 2>&1 && { ok "yt-dlp OK"; return 0; }
  fi
  warn "Không cài được yt-dlp tự động — cài tay hoặc set YTDLP_BIN trong .env."
}

prompt_admin() {
  # ── Hỏi thông tin đăng nhập admin (bắt buộc để vào được hệ thống) ──
  printf '\n'
  printf '\033[1;33m  ──────────────────────────────────────────────────────────────────────────────────────────────\033[0m\n'
  printf '\033[1;33m  Cấu hình đăng nhập Admin & Gửi mã OTP\033[0m\n'
  printf '\033[1;33m  ──────────────────────────────────────────────────────────────────────────────────────────────\033[0m\n'
  printf '\n'

  local _cur_admin _cur_gmail_user _cur_gmail_pass
  _cur_admin=$(grep -E '^SUPER_ADMIN=' .env 2>/dev/null | cut -d= -f2 || true)
  _cur_gmail_user=$(grep -E '^GMAIL_USER=' .env 2>/dev/null | cut -d= -f2 || true)
  _cur_gmail_pass=$(grep -E '^GMAIL_APP_PASSWORD=' .env 2>/dev/null | cut -d= -f2 || true)

  # Email admin
  local _admin_email=""
  if [ -n "${SUPER_ADMIN:-}" ]; then
    _admin_email="$SUPER_ADMIN"
  else
    while [ -z "$_admin_email" ]; do
      if [ -n "$_cur_admin" ]; then
        printf '  Email đăng nhập admin [Mặc định: %s]: ' "$_cur_admin"
        read -r _admin_email || true
        _admin_email="$(printf '%s' "$_admin_email" | tr -d '[:space:]')"
        [ -z "$_admin_email" ] && _admin_email="$_cur_admin"
      else
        printf '  Email đăng nhập admin: '
        read -r _admin_email || true
        _admin_email="$(printf '%s' "$_admin_email" | tr -d '[:space:]')"
        [ -z "$_admin_email" ] && printf '  Không được để trống.\n'
      fi
    done
  fi
  put_kv SUPER_ADMIN "$_admin_email"
  ok "SUPER_ADMIN = $_admin_email"

  # Gmail (tài khoản gửi OTP)
  local _gmail_user=""
  if [ -n "$_cur_gmail_user" ]; then
    printf '  Gmail gửi OTP [Mặc định: %s]: ' "$_cur_gmail_user"
    read -r _gmail_user || true
    _gmail_user="$(printf '%s' "$_gmail_user" | tr -d '[:space:]')"
    [ -z "$_gmail_user" ] && _gmail_user="$_cur_gmail_user"
  else
    printf '  Gmail gửi OTP (Enter = dùng email admin): '
    read -r _gmail_user || true
    _gmail_user="$(printf '%s' "$_gmail_user" | tr -d '[:space:]')"
    [ -z "$_gmail_user" ] && _gmail_user="$_admin_email"
  fi

  # Gmail App Password
  local _gmail_pass=""
  if [ -n "$_cur_gmail_pass" ]; then
    printf '  Gmail App Password (16 ký tự) [Mặc định: %s]: ' "$_cur_gmail_pass"
    read -r _gmail_pass || true
    _gmail_pass="$(printf '%s' "$_gmail_pass" | tr -d '[:space:]')"
    [ -z "$_gmail_pass" ] && _gmail_pass="$_cur_gmail_pass"
  else
    printf '  Gmail App Password (16 ký tự, không có dấu cách): '
    read -r _gmail_pass || true
    _gmail_pass="$(printf '%s' "$_gmail_pass" | tr -d '[:space:]')"
  fi

  if [ -n "$_gmail_pass" ]; then
    put_kv IS_USED_GMAIL        "true"
    put_kv GMAIL_USER           "$_gmail_user"
    put_kv GMAIL_APP_PASSWORD   "$_gmail_pass"
    ok "Gmail OTP = $_gmail_user (đã cấu hình)"
  else
    warn "Bỏ qua Gmail — chỉnh sửa .env sau và restart docker compose để gửi được OTP."
  fi

  # ── HuggingFace Token (tải models từ Hub) ──
  local _cur_hf_token
  _cur_hf_token=$(grep -E '^HF_TOKEN=' .env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || true)
  printf '\n'
  printf '  ─── HuggingFace Token (để tải models AI về ComfyUI) ───────────────────────────────────\n'
  printf '  Tạo tại: https://huggingface.co/settings/tokens  (loại: Read / Inference Providers)\n'
  if [ -n "$_cur_hf_token" ]; then
    printf '  HF Token [Mặc định: %s…]: ' "$(printf '%s' "$_cur_hf_token" | head -c 10)"
    local _hf_token=""
    read -r _hf_token || true
    _hf_token="$(printf '%s' "$_hf_token" | tr -d '[:space:]')"
    [ -z "$_hf_token" ] && _hf_token="$_cur_hf_token"
  else
    printf '  HF Token (hf_..., Enter để bỏ qua): '
    local _hf_token=""
    read -r _hf_token || true
    _hf_token="$(printf '%s' "$_hf_token" | tr -d '[:space:]')"
  fi
  if [ -n "$_hf_token" ]; then
    put_kv HF_TOKEN "$_hf_token"
    ok "HF_TOKEN đã lưu ($(printf '%s' "$_hf_token" | head -c 10)…)"
  else
    warn "Bỏ qua HF_TOKEN — có thể không tải được models gated (Qwen-Image-Edit, Wan…)."
    warn "Thêm sau: sửa HF_TOKEN trong .env rồi 'docker compose restart api'."
  fi
  printf '\n'
}


# ════════════════════════════════════════════════════════════════════════════
# macOS đã BỎ HỖ TRỢ (ALD 20/07/2026) — worker chỉ còn Linux/CUDA.
# ════════════════════════════════════════════════════════════════════════════
if [ "$(uname -s)" = "Darwin" ]; then
  warn "macOS không còn được hỗ trợ — worker chỉ chạy Linux/CUDA. Deploy lên box Linux (xem DEPLOY.md)."
  exit 1
fi

# ════════════════════════════════════════════════════════════════════════════
# Linux + NVIDIA — cả stack trong Docker (gồm ComfyUI --profile gpu)
# ════════════════════════════════════════════════════════════════════════════
# ── 1. Docker ───────────────────────────────────────────────────────────────
if ! command -v docker >/dev/null 2>&1; then
  say "Cài Docker Engine…"
  curl -fsSL https://get.docker.com | sh
  sudo usermod -aG docker "$USER" 2>/dev/null || true
  warn "Đã thêm $USER vào nhóm docker — lần sau logout/login để khỏi cần sudo."
fi
# Phiên hiện tại chưa có quyền docker → tạm dùng sudo cho cả lần chạy này
DOCKER="docker"; docker info >/dev/null 2>&1 || DOCKER="sudo docker"
install_ytdlp_native

# ── 2. .env (chỉ tạo nếu chưa có) ───────────────────────────────────────────
if [ -f .env ]; then
  say ".env đã tồn tại — kiểm tra cấu hình..."
else
  say "Tạo .env với secret ngẫu nhiên…"
  cp .env.example .env
  set_kv POSTGRES_PASSWORD   "$(rnd)"
  set_kv MINIO_ROOT_PASSWORD "$(rnd)"
  set_kv API_KEY             "$(rnd)"
  set_kv WORKER_TOKEN        "$(rnd)"
  set_kv SESSION_JWT_SECRET  "$(rnd)"
  IP="$(curl -fss https://api.ipify.org 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}')"
  [ -n "${IP:-}" ] && set_kv S3_PUBLIC_ENDPOINT "http://$IP:9000"
fi

prompt_admin


# ── 3. Domain (tuỳ chọn) ────────────────────────────────────────────────────
PROFILES=""
if [ -n "$DOMAIN" ]; then
  say "Bật HTTPS tự động cho: $DOMAIN  (cần port 80+443 mở, DNS trỏ về máy này)"
  set_kv DOMAIN "$DOMAIN"
  set_kv PUBLIC_BASE_URL "https://$DOMAIN"
  PROFILES="--profile proxy"
fi

# ── 4. GPU? (tự cài container-toolkit nếu thiếu) ────────────────────────────
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
  if ! $DOCKER run --rm --gpus all nvidia/cuda:13.0.2-base-ubuntu22.04 true >/dev/null 2>&1; then
    if command -v apt-get >/dev/null 2>&1; then
      say "Cài NVIDIA Container Toolkit (để Docker dùng được GPU)…"
      curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
        | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
      curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
        | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
        | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null
      sudo apt-get update -y && sudo apt-get install -y nvidia-container-toolkit
      sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker
    else
      warn "Có GPU nhưng Docker chưa dùng được, và máy không phải Ubuntu/Debian."
      warn "Cài nvidia-container-toolkit thủ công rồi chạy lại; tạm thời chạy KHÔNG GPU."
    fi
  fi
  if $DOCKER run --rm --gpus all nvidia/cuda:13.0.2-base-ubuntu22.04 true >/dev/null 2>&1; then
    say "GPU OK → bật ComfyUI trong stack (lần đầu tải ~25GB models)."
    PROFILES="--profile gpu $PROFILES"
  fi
else
  warn "Không thấy GPU NVIDIA → KHÔNG bật ComfyUI nội bộ."
  warn "Trỏ COMFY_URL trong .env tới ComfyUI sẵn có (vd http://<ip>:8188) rồi chạy lại."
fi

# ── 5. Khởi động ────────────────────────────────────────────────────────────
say "Khởi động stack…"
$DOCKER compose $PROFILES up -d --build

PORT="$(grep -E '^API_PORT=' .env | cut -d= -f2 || true)"; PORT="${PORT:-8080}"

# Chờ API khởi động để seed DB
_api_ok=0
for _i in 1 2 3 4 5; do
  sleep 4
  if curl -fsS "http://localhost:$PORT/health" >/dev/null 2>&1; then
    _api_ok=1; break
  fi
done

if [ "$_api_ok" = "1" ]; then
  _pg_user=$(grep -E '^POSTGRES_USER=' .env 2>/dev/null | cut -d= -f2 || true); _pg_user="${_pg_user:-motion}"
  _pg_db=$(grep -E '^POSTGRES_DB=' .env 2>/dev/null | cut -d= -f2 || true); _pg_db="${_pg_db:-motion}"
  say "Seed workflow templates..."
  $DOCKER compose exec -T postgres psql -U "$_pg_user" -d "$_pg_db" < db/seeds/workflow_templates.sql >/dev/null 2>&1 \
    && ok "seed workflow_templates thành công" || warn "seed workflow_templates thất bại."
else
  warn "API không phản hồi sau nhiều lần thử. Bỏ qua seed workflow_templates."
fi

KEY="$(grep -E '^API_KEY=' .env | cut -d= -f2 || true)"
ADMIN="$(grep -E '^SUPER_ADMIN=' .env | cut -d= -f2 || true)"
say "Xong!"
echo "   Health : curl http://localhost:$PORT/health"
[ -n "$DOMAIN" ] && echo "   Public : https://$DOMAIN"
echo "   API key: $KEY   (header X-API-Key khi gọi /jobs)"
echo "   Admin  : ${ADMIN:-(chưa đặt — sửa SUPER_ADMIN trong .env)}   → POST /auth/send-otp để đăng nhập"

echo "   Frontend Config (.env):"
if [ -n "$DOMAIN" ]; then
  echo "     NUXT_MOTION_API_URL=https://$DOMAIN"
  echo "     NUXT_MOTION_API_KEY=$KEY"
  echo "     NUXT_PUBLIC_MOTION_BACKEND_URL=https://$DOMAIN"
else
  echo "     NUXT_MOTION_API_URL=http://localhost:$PORT"
  echo "     NUXT_MOTION_API_KEY=$KEY"
  echo "     NUXT_PUBLIC_MOTION_BACKEND_URL=http://localhost:$PORT"
fi

echo "   Log    : $DOCKER compose logs -f api worker"
