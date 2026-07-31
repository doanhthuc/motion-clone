#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
# fullstack-setup.sh — Cài CẢ backend (motion-backend) + frontend (motions) trên
# CÙNG 1 VPS, 1 lệnh. Hai chế độ:
#
#   • Có Cloudflare API Token → tự tạo Cloudflare Tunnel + DNS cho CẢ FE + BE.
#       End-user chỉ mở 1 link https://<FE-domain> — KHÔNG port/tunnel/cài gì.
#       Chạy cả trên máy NAT (vast.ai/RunPod). ⭐ Khuyên dùng.
#   • Có Cloudflare Tunnel Token (cloudflared tunnel run --token ...) → dùng tunnel đã tạo sẵn.
#       Bạn phải tạo Public Hostname trên Cloudflare trước/sau: FE→localhost:2030, BE→localhost:8080.
#   • Không có token → chạy qua IP VPS (http://<IP>:2030). Cần VPS có IP public + mở cổng.
#
#   git clone git@github.com:ALD-Project/motion-backend.git && cd motion-backend
#   ./setup/fullstack-setup.sh
#
# Hỏi: URL Frontend · URL Backend · Cloudflare API/Tunnel Token · SuperAdmin email · Gmail · App Password.
#
# Cloudflare API Token cần 3 quyền (xem DEPLOY.md → Bước 2):
#   Account · Cloudflare Tunnel · Edit   +   Zone · DNS · Edit   +   Zone · Zone · Read
#
# Cờ env tuỳ chọn: FE_DOMAIN= BE_DOMAIN= CF_API_TOKEN= CF_TUNNEL_TOKEN= SUPER_ADMIN= GMAIL_USER= GMAIL_APP_PASSWORD=
#                  FE_PORT=2030 API_PORT=8080 MOTIONS_DIR=~/motions MOTIONS_BRANCH=development
#                  SKIP_COMFY=1 / SKIP_MODELS=1
#
# ALD 02/07/2026 - PORTABLE THEO USER: mọi path đều tính từ $HOME của user đang chạy (KHÔNG hardcode
# /home/ubuntu) — VPS user root/debian/paperspace… đều chạy đúng. setup-pm2.sh giờ cài thêm:
#   • yt-dlp (binary standalone → $HOME/.local/bin + YTDLP_BIN vào .env) — nút "Lấy video" URL
#     Facebook/TikTok/YouTube; thiếu nó FE báo "Server chưa cài yt-dlp. Cài yt-dlp hoặc set YTDLP_BIN".
#   • PyTorch 2.12.1 + CUDA 13.0 stable khi driver/GPU hỗ trợ; tự fallback an toàn cho GPU cũ.
#   • sageattention (ComfyUI venv) + MOTION_ATTENTION=sageattn khi GPU ≥ sm_80.
# ════════════════════════════════════════════════════════════════════════════
set -uo pipefail
# ALD 14/06/2026 - script nằm trong setup/ → ROOT = gốc repo (chứa setup-pm2.sh, .env, comfyui/…).
cd "$(dirname "$0")/.."; ROOT="$(pwd)"

say()  { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m  ✓ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m  ! %s\033[0m\n' "$*"; }
die()  { printf '\n\033[1;31m  ✗ %s\033[0m\n' "$*"; exit 1; }
host_of() { local h="$1"; h="${h#http://}"; h="${h#https://}"; h="${h%%/*}"; printf '%s' "${h// /}"; }

cf_zone_lookup() {
  local tok="$1" host="$2" api="https://api.cloudflare.com/client/v4" cand resp out
  host="$(host_of "$host")"; host="${host%.}"
  while [ -n "$host" ] && [[ "$host" == *.* ]]; do
    cand="$host"
    resp="$(curl -s -G -H "Authorization: Bearer $tok" --data-urlencode "name=$cand" "$api/zones")"
    out="$(printf '%s' "$resp" | python3 -c '
import sys,json
try:
    r=(json.load(sys.stdin).get("result") or [])
    z=r[0] if r else {}
    print(z.get("id",""), (z.get("account") or {}).get("id",""), z.get("name",""))
except Exception:
    print("", "", "")
' 2>/dev/null)"
    [ -n "$(printf '%s' "$out" | awk '{print $1}')" ] && { printf '%s\n' "$out"; return 0; }
    host="${host#*.}"
  done
  printf '\n'
}

cf_json_success() {
  python3 -c 'import sys,json; sys.exit(0 if json.load(sys.stdin).get("success") else 1)' 2>/dev/null
}

cf_visible_zones() {
  local tok="$1" api="https://api.cloudflare.com/client/v4" resp
  resp="$(curl -s -H "Authorization: Bearer $tok" "$api/zones?per_page=20")"
  printf '%s' "$resp" | python3 -c '
import sys,json
try:
    zones=(json.load(sys.stdin).get("result") or [])
    print(", ".join(z.get("name","") for z in zones if z.get("name")) or "(không có zone nào)")
except Exception:
    print("(không đọc được danh sách zone)")
'
}

cf_api_preflight_fullstack() {
  [ -z "${CF_API_TOKEN:-}" ] && return 0
  local api="https://api.cloudflare.com/client/v4" tok="$CF_API_TOKEN" host zinfo zid acc zname verify dnsok tunnelok zones
  verify="$(curl -s -H "Authorization: Bearer $tok" "$api/user/tokens/verify")"
  if ! printf '%s' "$verify" | cf_json_success; then
    warn "Cloudflare API Token không verify được. Hãy nhập API token, không phải Tunnel token."
    return 1
  fi
  for host in "$BE_HOST" "$FE_HOST"; do
    [ -z "$host" ] && continue
    zinfo="$(cf_zone_lookup "$tok" "$host")"
    zid="${zinfo%% *}"; zname="${zinfo##* }"; acc="$(printf '%s' "$zinfo" | awk '{print $2}')"
    if [ -z "$zid" ]; then
      zones="$(cf_visible_zones "$tok")"
      warn "Cloudflare token OK nhưng không thấy zone cho '$host'."
      warn "  Domain gốc phải nằm trong Zone Resources của token (vd datools.info hoặc All zones)."
      warn "  Nếu list zones trống, domain chưa thuộc account/token này."
      warn "  Các zone token hiện nhìn thấy: $zones"
      return 1
    fi
    ok "Cloudflare zone preflight: $host → $zname"
    dnsok="$(curl -s -H "Authorization: Bearer $tok" "$api/zones/$zid/dns_records?per_page=1")"
    if ! printf '%s' "$dnsok" | cf_json_success; then
      warn "Token không đọc được DNS records của zone '$zname' — cần Zone.DNS:Edit."
      return 1
    fi
    tunnelok="$(curl -s -H "Authorization: Bearer $tok" "$api/accounts/$acc/cfd_tunnel?per_page=1&is_deleted=false")"
    if ! printf '%s' "$tunnelok" | cf_json_success; then
      warn "Token không truy cập Cloudflare Tunnel trong account của '$zname' — cần Account.Cloudflare Tunnel:Edit đúng account."
      return 1
    fi
  done
  ok "Cloudflare API Token preflight OK cho FE+BE."
}

MOTIONS_REPO="${MOTIONS_REPO:-git@github.com:ALD-Project/motions.git}"
MOTIONS_DIR="${MOTIONS_DIR:-$HOME/motions}"
MOTIONS_BRANCH="${MOTIONS_BRANCH:-development}"
FE_PORT="${FE_PORT:-2030}"
API_PORT="${API_PORT:-8080}"

[ -f "$ROOT/setup/setup-pm2.sh" ] || die "Không thấy setup-pm2.sh — chạy fullstack-setup.sh TRONG thư mục motion-backend."
[ "$(id -u)" -eq 0 ] && SUDO="" || SUDO="sudo"

IP="$(curl -fsS https://api.ipify.org 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}')"
[ -z "${IP:-}" ] && die "Không phát hiện được IP public."

# ── 1/4 · Hỏi cấu hình ───────────────────────────────────────────────────────
say "1/4 · Cấu hình"

# ── ALD 26/06/2026 - Cấu hình THEO MẪU ──────────────────────────────────────
# Hỏi TRƯỚC khi nhập Cloudflare: dùng mẫu có sẵn hay tự nhập. Mẫu quản lý ở setup/templates.json (mỗi khách 1
# object). Chọn mẫu → nạp sẵn CF token/FE/BE/SuperAdmin/Gmail/HF vào env → mọi prompt phía dưới (đều check
# [ -z ]) tự bỏ qua, người dùng KHÔNG phải gõ gì. Không file mẫu / chọn tự nhập / non-interactive → như cũ.
# Cũng chạy không-hỏi được: TEMPLATE=vanhoang ./setup/fullstack-setup.sh
TEMPLATES_FILE="${TEMPLATES_FILE:-$ROOT/setup/templates.json}"
# ── ALD 22/07/2026 - Lấy mẫu từ Motion Task Cloud API (nếu có SETUP_API_KEY). Không có → dùng file local như cũ.
# Ưu điểm: quản lý mẫu tập trung ở admin MTC, không cần phân phối templates.json (chứa secrets) qua git.
SETUP_API_URL="${SETUP_API_URL:-https://tasks.datools.info/api/setup/templates}"
# Key GẮN CỨNG: chỉ người được cấp quyền vào source mới thấy file này nên an toàn. Ghi đè bằng env SETUP_API_KEY nếu cần.
# Server MTC phải đặt NUXT_SETUP_API_KEY = đúng giá trị này thì API mới trả danh sách.
SETUP_API_KEY="${SETUP_API_KEY:-CHANGE_ME_YOUR_OWN_SETUP_KEY}"
if [ -n "${SETUP_API_KEY:-}" ]; then
  _tpl_api_tmp="$(mktemp 2>/dev/null || echo "/tmp/mtc-templates.$$.json")"
  if curl -fsS -H "x-setup-key: $SETUP_API_KEY" "$SETUP_API_URL" -o "$_tpl_api_tmp" 2>/dev/null \
     && python3 -c "import json,sys; sys.exit(0 if json.load(open(sys.argv[1])).get('templates') else 1)" "$_tpl_api_tmp" 2>/dev/null; then
    TEMPLATES_FILE="$_tpl_api_tmp"
    ok "Đã lấy mẫu cấu hình từ Motion Task Cloud ($SETUP_API_URL)."
  else
    warn "Không lấy được mẫu từ API (kiểm tra SETUP_API_URL/SETUP_API_KEY) → dùng file local nếu có."
    rm -f "$_tpl_api_tmp" 2>/dev/null || true; unset _tpl_api_tmp
  fi
fi
_tpl_load() {   # in các lệnh `export VAR=...` cho mẫu tên $1 (shlex.quote an toàn cả mật khẩu có dấu cách)
  python3 - "$TEMPLATES_FILE" "$1" <<'PY'
import sys, json, shlex
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    sys.exit(2)
t = next((x for x in data.get("templates", []) if x.get("name") == sys.argv[2]), None)
if not t:
    sys.exit(3)
MAP = {"cf_api_token": "CF_API_TOKEN", "cf_tunnel_token": "CF_TUNNEL_TOKEN", "fe_domain": "FE_DOMAIN",
       "be_domain": "BE_DOMAIN", "super_admin": "SUPER_ADMIN", "gmail_user": "GMAIL_USER",
       "gmail_app_password": "GMAIL_APP_PASSWORD", "hf_token": "HF_TOKEN"}
for k, var in MAP.items():
    v = t.get(k)
    if v:
        print("export %s=%s" % (var, shlex.quote(str(v))))
PY
}
# Đã nạp TEMPLATE qua env (không-hỏi) → áp luôn.
if [ -n "${TEMPLATE:-}" ] && [ -z "${CF_API_TOKEN:-}" ] && [ -z "${FE_DOMAIN:-}" ]; then
  _tpl_cmds="$(_tpl_load "$TEMPLATE")"
  if [ -n "$_tpl_cmds" ]; then eval "$_tpl_cmds"; ok "Đã nạp mẫu '$TEMPLATE' (CF/FE/BE/admin/Gmail/HF)."
  else warn "Mẫu '$TEMPLATE' không có trong $TEMPLATES_FILE → nhập tay."; fi
fi
# Hỏi chọn mẫu (chỉ khi tương tác + có file mẫu + chưa set token/domain sẵn).
if [ -t 0 ] && [ -f "$TEMPLATES_FILE" ] \
   && [ -z "${CF_API_TOKEN:-}" ] && [ -z "${CF_TUNNEL_TOKEN:-}" ] && [ -z "${FE_DOMAIN:-}" ]; then
  _TPL_ROWS="$(python3 - "$TEMPLATES_FILE" <<'PY'
import sys, json
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    sys.exit(0)
for t in data.get("templates", []):
    nm = t.get("name", "")
    if nm:
        print("%s\t%s" % (nm, t.get("label") or nm))
PY
)"
  if [ -n "$_TPL_ROWS" ]; then
    printf '  Cấu hình: [1] Theo MẪU có sẵn  ·  [2] Tự nhập tay  (Enter=1): '
    read -r _tpl_choice || true
    if [ -z "${_tpl_choice:-}" ] || [ "${_tpl_choice}" = 1 ]; then
      echo "  Mẫu có sẵn (setup/templates.json):"
      _i=0; _TPL_NAMES=()
      while IFS="$(printf '\t')" read -r _nm _lb; do
        [ -z "$_nm" ] && continue
        _i=$((_i + 1)); _TPL_NAMES+=("$_nm")
        printf '    %d) %s\n' "$_i" "$_lb"
      done <<< "$_TPL_ROWS"
      printf '  Chọn mẫu (1-%d, Enter=1): ' "$_i"
      read -r _sel || true
      [ -z "${_sel:-}" ] && _sel=1
      if [[ "$_sel" =~ ^[0-9]+$ ]] && [ "$_sel" -ge 1 ] && [ "$_sel" -le "$_i" ]; then
        TEMPLATE="${_TPL_NAMES[$((_sel - 1))]}"
        _tpl_cmds="$(_tpl_load "$TEMPLATE")"
        if [ -n "$_tpl_cmds" ]; then
          eval "$_tpl_cmds"
          ok "Đã nạp mẫu '$TEMPLATE' (CF token/FE/BE/admin/Gmail/HF) — bỏ qua nhập tay."
        else
          warn "Không nạp được mẫu '$TEMPLATE' → nhập tay."
        fi
      else
        warn "Lựa chọn không hợp lệ → nhập tay."
      fi
    fi
  fi
fi

# ALD 22/07/2026 - Dọn file mẫu tạm tải từ API (chứa secrets) ngay sau khi đã nạp xong template.
[ -n "${_tpl_api_tmp:-}" ] && rm -f "$_tpl_api_tmp" 2>/dev/null && unset _tpl_api_tmp || true

# Cloudflare token quyết định chế độ (API auto, Tunnel token có sẵn, hay IP trực tiếp).
if [ -z "${CF_API_TOKEN:-}" ] && [ -z "${CF_TUNNEL_TOKEN:-}" ]; then
  printf '  Cloudflare API Token (tự tạo tunnel/DNS; Enter để dùng Tunnel Token hoặc IP): '
  read -r CF_API_TOKEN || true
fi
if [ -n "${CF_API_TOKEN:-}" ] && [[ "$CF_API_TOKEN" == eyJ* ]]; then
  warn "Token vừa nhập giống Cloudflare Tunnel Token, không phải API Token → chuyển sang chế độ tunnel đã tạo sẵn."
  CF_TUNNEL_TOKEN="$CF_API_TOKEN"
  CF_API_TOKEN=""
fi
if [ -z "${CF_API_TOKEN:-}" ] && [ -z "${CF_TUNNEL_TOKEN:-}" ]; then
  printf '  Cloudflare Tunnel Token (tunnel đã tạo sẵn; Enter = dùng IP trực tiếp): '
  read -r CF_TUNNEL_TOKEN || true
fi
if [ -n "${CF_API_TOKEN:-}" ] || [ -n "${CF_TUNNEL_TOKEN:-}" ]; then
  MODE=cf
  CF_MODE="api"; [ -n "${CF_TUNNEL_TOKEN:-}" ] && CF_MODE="tunnel-token"
  if [ -z "${FE_DOMAIN:-}" ]; then printf '  URL Frontend (end-user mở, vd app.datools.info): '; read -r FE_DOMAIN || true; fi
  if [ -z "${BE_DOMAIN:-}" ]; then printf '  URL Backend  (vd api.datools.info): '; read -r BE_DOMAIN || true; fi
  FE_HOST="$(host_of "$FE_DOMAIN")"; BE_HOST="$(host_of "$BE_DOMAIN")"
  [ -z "$FE_HOST" ] || [ -z "$BE_HOST" ] && die "Cần cả URL Frontend lẫn Backend khi dùng Cloudflare."
  if [ -n "${CF_API_TOKEN:-}" ]; then
    while ! cf_api_preflight_fullstack; do
      if [ -t 0 ]; then
        warn "Cloudflare API Token chưa dùng được cho FE/BE domain. Nhập token khác để thử lại, hoặc Enter để bỏ API token."
        printf '  Cloudflare API Token mới: '
        read -r CF_API_TOKEN || true
        if [ -z "${CF_API_TOKEN:-}" ]; then
          warn "Bỏ Cloudflare API token. Chuyển sang hỏi Tunnel token hoặc IP trực tiếp."
          if [ -z "${CF_TUNNEL_TOKEN:-}" ]; then
            printf '  Cloudflare Tunnel Token (Enter = dùng IP trực tiếp): '
            read -r CF_TUNNEL_TOKEN || true
          fi
          break
        fi
      else
        die "Cloudflare API Token preflight lỗi ngay bước 1/4 — đổi token/quyền zone rồi chạy lại."
      fi
    done
  fi
  if [ -z "${CF_API_TOKEN:-}" ] && [ -z "${CF_TUNNEL_TOKEN:-}" ]; then
    MODE=ip
    CF_MODE="none"
    FE_PUBLIC="http://$IP:$FE_PORT"
    BE_PUBLIC="http://$IP:$API_PORT"
  else
    CF_MODE="api"; [ -n "${CF_TUNNEL_TOKEN:-}" ] && CF_MODE="tunnel-token"
    FE_PUBLIC="https://$FE_HOST"; BE_PUBLIC="https://$BE_HOST"
  fi
else
  MODE=ip
  CF_MODE="none"
  FE_PUBLIC="http://$IP:$FE_PORT"; BE_PUBLIC="http://$IP:$API_PORT"
fi
# SuperAdmin + Gmail (gửi OTP)
if [ -z "${SUPER_ADMIN:-}" ]; then while [ -z "${SUPER_ADMIN:-}" ]; do printf '  SuperAdmin email (đăng nhập, BẮT BUỘC): '; read -r SUPER_ADMIN || true; done; fi
if [ -z "${GMAIL_USER:-}" ]; then printf '  Gmail gửi OTP (Enter = bỏ qua): '; read -r GMAIL_USER || true; fi
if [ -n "${GMAIL_USER:-}" ] && [ -z "${GMAIL_APP_PASSWORD:-}" ]; then printf '  Gmail App Password: '; read -rs GMAIL_APP_PASSWORD || true; echo; fi
ok "Mode=$MODE${CF_MODE:+/$CF_MODE} · Frontend=$FE_PUBLIC · Backend=$BE_PUBLIC · Admin=$SUPER_ADMIN"

# ── 2/4 · SSH key kết nối GitHub (clone repo frontend private) ───────────────
say "2/4 · SSH key kết nối GitHub"
command -v git >/dev/null 2>&1 || { $SUDO apt-get update -y -qq && $SUDO apt-get install -y -qq git; }
mkdir -p ~/.ssh; chmod 700 ~/.ssh
KEY="$HOME/.ssh/id_ed25519"
[ -f "$KEY" ] || { ssh-keygen -t ed25519 -C "motion-fullstack-$(hostname)" -f "$KEY" -N "" >/dev/null; ok "Đã tạo SSH key mới."; }
ssh-keyscan -t ed25519,rsa github.com >> ~/.ssh/known_hosts 2>/dev/null; sort -u ~/.ssh/known_hosts -o ~/.ssh/known_hosts 2>/dev/null || true
# ssh -T git@github.com LUÔN exit 1 → dưới pipefail phải capture rồi mới grep.
gh_ok() { local o; o="$(ssh -T -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 git@github.com 2>&1)"; printf '%s' "$o" | grep -q "successfully authenticated"; }
CONNECTED=0; for _ in 1 2 3 4 5; do gh_ok && { CONNECTED=1; break; }; sleep 3; done
if [ "$CONNECTED" = 1 ]; then
  ok "GitHub SSH đã kết nối."
else
  echo
  echo "  ➜ Thêm PUBLIC KEY sau vào GitHub (https://github.com/settings/keys → New SSH key, hoặc Deploy key repo motions):"
  echo "    ────────────────────────────────────────────────────────────"
  echo "    $(cat "$KEY.pub")"
  echo "    ────────────────────────────────────────────────────────────"
  if [ -t 0 ]; then
    while ! gh_ok; do printf '  Đã thêm key chưa? (Enter để test lại · Ctrl-C để dừng): '; read -r _ || die "Dừng — cần SSH key GitHub."; done
    ok "GitHub SSH đã kết nối."
  else
    die "Chưa nối được GitHub (non-interactive). Add PUBLIC KEY ở trên vào GitHub rồi chạy lại."
  fi
fi

# ── 3/4 · Backend (motion-backend) qua setup-pm2.sh ─────────────────────────
say "3/4 · Cài BACKEND (motion-backend)"
# export biến chung (env-assignment từ mảng/expansion KHÔNG được bash áp dụng; export thì kế thừa an toàn,
# kể cả GMAIL_APP_PASSWORD có dấu cách).
export SUPER_ADMIN GMAIL_USER="${GMAIL_USER:-}" GMAIL_APP_PASSWORD="${GMAIL_APP_PASSWORD:-}"
export HF_TOKEN="${HF_TOKEN:-}"   # ALD 26/06 - token HF (từ mẫu) → setup-pm2.sh kế thừa, bỏ qua bước hỏi
export SKIP_COMFY="${SKIP_COMFY:-0}" SKIP_MODELS="${SKIP_MODELS:-0}" SKIP_OLLAMA_MODELS="${SKIP_OLLAMA_MODELS:-0}"
if [ "$MODE" = cf ]; then
  # Cloudflare API Token: tự tạo tunnel/DNS cho CẢ BE+FE.
  # Cloudflare Tunnel Token: cài service cho tunnel đã tạo sẵn; Public Hostname phải trỏ BE→API_PORT, FE→FE_PORT.
  DOMAIN="$BE_HOST" CF_API_TOKEN="${CF_API_TOKEN:-}" CF_TUNNEL_TOKEN="${CF_TUNNEL_TOKEN:-}" \
    CF_FE_DOMAIN="$FE_HOST" CF_FE_PORT="$FE_PORT" API_PORT="$API_PORT" \
    CORS_ORIGINS="$FE_PUBLIC" FRONTEND_URL="$FE_PUBLIC" \
    bash "$ROOT/setup/setup-pm2.sh" || die "Cài backend lỗi."
else
  DOMAIN="$IP" SKIP_HTTPS=1 PUBLIC_BASE_URL="$BE_PUBLIC" CORS_ORIGINS="$FE_PUBLIC" \
    FRONTEND_URL="$FE_PUBLIC" FE_SSH_PORT="${VAST_TCP_PORT_22:-22}" API_PORT="$API_PORT" \
    bash "$ROOT/setup/setup-pm2.sh" || die "Cài backend lỗi."
fi
API_KEY_VAL="$(grep -E '^API_KEY=' "$ROOT/.env" | head -1 | cut -d= -f2-)"
[ -z "${API_KEY_VAL:-}" ] && die "Không đọc được API_KEY từ $ROOT/.env"

# ── 4/4 · Frontend (motions) — clone + .env + build + PM2 ───────────────────
say "4/4 · Cài FRONTEND (motions)"
#region ALD 13/07/2026 - Luôn cài đúng nhánh development thay vì remote HEAD (main)
if [ -d "$MOTIONS_DIR/.git" ]; then
  if ( cd "$MOTIONS_DIR" \
    && git fetch origin "$MOTIONS_BRANCH" \
    && git switch "$MOTIONS_BRANCH" \
    && git merge --ff-only "origin/$MOTIONS_BRANCH" ); then
    ok "motions đã cập nhật origin/$MOTIONS_BRANCH."
  else
    warn "Không cập nhật được motions từ origin/$MOTIONS_BRANCH (giữ working tree hiện tại)."
  fi
else
  git clone --branch "$MOTIONS_BRANCH" --single-branch "$MOTIONS_REPO" "$MOTIONS_DIR" \
    || die "Clone motions nhánh $MOTIONS_BRANCH lỗi — kiểm tra SSH key GitHub có quyền đọc repo."
fi
#endregion
ok "motions ở $MOTIONS_DIR"

# .env FE — CHỈ 3 key motion-backend (bỏ Supabase/Comfy).
#  - NUXT_MOTION_API_URL (server-side proxy /api/motion/*): 127.0.0.1 (cùng VPS → luôn chạy).
#  - NUXT_PUBLIC_MOTION_BACKEND_URL (client-side, browser gọi trực tiếp): URL PUBLIC của BE.
cat > "$MOTIONS_DIR/.env" <<FEENV
# Sinh bởi fullstack-setup.sh ($MODE)
NUXT_MOTION_API_URL=http://127.0.0.1:$API_PORT
NUXT_MOTION_API_KEY=$API_KEY_VAL
NUXT_PUBLIC_MOTION_BACKEND_URL=$BE_PUBLIC
FEENV
chmod 600 "$MOTIONS_DIR/.env"
ok ".env FE: server-side→127.0.0.1:$API_PORT · client-side→$BE_PUBLIC"

say "    npm install + build (Nuxt) — lần đầu hơi lâu…"
( cd "$MOTIONS_DIR" && npm install --no-audit --no-fund && npm run build ) || die "Build motions lỗi."

# Wrapper chạy Nuxt server (Nitro) dưới PM2 — đọc .env + nghe 0.0.0.0:$FE_PORT
cat > "$MOTIONS_DIR/.run.sh" <<'RUN'
#!/usr/bin/env bash
cd "$(dirname "$0")"
set -a; [ -f .env ] && . ./.env; set +a
export PORT="${PORT:-2030}" HOST=0.0.0.0 NITRO_PORT="${PORT:-2030}" NITRO_HOST=0.0.0.0
exec node .output/server/index.mjs
RUN
chmod +x "$MOTIONS_DIR/.run.sh"
pm2 delete motions >/dev/null 2>&1 || true
PORT="$FE_PORT" pm2 start "$MOTIONS_DIR/.run.sh" --name motions --interpreter bash --update-env >/dev/null 2>&1 \
  && ok "PM2: motions online (:$FE_PORT)" || warn "PM2 start motions lỗi — xem 'pm2 logs motions'."
pm2 save >/dev/null 2>&1 || true

# Mode IP: mở firewall OS cho FE+API (CF mode không cần — tunnel outbound).
if [ "$MODE" = ip ] && command -v ufw >/dev/null 2>&1 && $SUDO ufw status 2>/dev/null | grep -qi "Status: active"; then
  $SUDO ufw allow "$API_PORT"/tcp >/dev/null 2>&1; $SUDO ufw allow "$FE_PORT"/tcp >/dev/null 2>&1; $SUDO ufw allow OpenSSH >/dev/null 2>&1
  ok "ufw: mở $API_PORT + $FE_PORT."
fi
for i in $(seq 1 20); do curl -fsS "http://127.0.0.1:$FE_PORT/" >/dev/null 2>&1 && break; sleep 2; done

# ── XONG ─────────────────────────────────────────────────────────────────────
echo
printf '\033[1;32m════════ Fullstack đã chạy (PM2) ════════\033[0m\n'
echo "  🖥  Frontend (end-user mở) : $FE_PUBLIC"
echo "  🔌 Backend  API           : $BE_PUBLIC/health"
echo "  👤 Admin                  : $SUPER_ADMIN  → mở FE, bấm gửi OTP"
echo "  📋 PM2                     : pm2 ls   (motions + minio/api/wf-worker/worker/bg-remover/comfyui)"
# ALD 02/07/2026 - postflight yt-dlp: xác nhận nút "Lấy video" (import URL social) dùng được ngay sau setup.
_YTDLP_ENV="$(grep -E '^YTDLP_BIN=' "$ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2-)"
if [ -x "${_YTDLP_ENV:-}" ] || command -v yt-dlp >/dev/null 2>&1 || [ -x "$HOME/.local/bin/yt-dlp" ]; then
  echo "  🎞  yt-dlp (Lấy video URL) : OK (${_YTDLP_ENV:-$(command -v yt-dlp || echo "$HOME/.local/bin/yt-dlp")})"
else
  warn "yt-dlp CHƯA có → nút 'Lấy video' từ URL sẽ lỗi. Cài: curl -fsSL -o \$HOME/.local/bin/yt-dlp https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp && chmod +x \$HOME/.local/bin/yt-dlp, rồi thêm YTDLP_BIN vào .env + pm2 restart api."
fi
echo
if [ "$MODE" = cf ]; then
  ok "End-user chỉ cần mở: $FE_PUBLIC  (HTTPS qua Cloudflare, không cần port/tunnel)."
  warn "Cloudflare cấp SSL cho hostname mới ~1-2 phút. Trên dashboard tunnel sẽ thấy 2 Public Hostname (FE+BE)."
else
  warn "Mở cổng $API_PORT + $FE_PORT ở FIREWALL/Security Group NHÀ CUNG CẤP. Máy NAT (vast.ai) IP không vào được → dùng Cloudflare Token."
fi
echo
