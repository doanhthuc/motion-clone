#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
# lib-feature.sh — Thư viện cài đặt NATIVE (PM2) cho box motion, dùng chung bởi:
#   setup/setup-create-image.sh · setup/setup-motion-transfer.sh · setup/setup-tryon.sh
#   setup/setup-full.sh
#
# Thư viện KHÔNG tự quyết phạm vi — profile gọi nó quyết, qua các biến dưới đây. Ba profile
# đầu là box CHUYÊN, khoá cứng vào một nhóm type; setup-full.sh mở hết. Cơ chế giống nhau,
# chỉ khác giá trị:
#   • Chỉ clone đúng custom node profile khai (biến COMFY_NODES).
#   • JOB_TYPES = đúng nhóm type profile khai (biến JOB_TYPE) → worker không nhận job khác.
#   • MODEL_CATALOG_PATH trỏ catalog của profile (biến CATALOG_FILE) → Settings → Models AI
#     chỉ thấy/tải được model trong catalog đó.
#   • KHÔNG tải model trong lúc cài (tải riêng qua Settings → Models AI).
#   • KHÔNG cài ComfyUI-Manager (không có UI cài node/model tuỳ ý) — kể cả profile full:
#     catalog đã mở hết model rồi, Manager chỉ thêm một đường cài node ngoài tầm kiểm soát.
#   • Chỉ bật đúng PM2 app cần (biến PM2_APPS).
#
# Script gọi PHẢI export sẵn (trước khi source file này):
#   ROOT          gốc repo (cd "$(dirname "$0")/.." ; pwd)
#   FEATURE       slug, vd "create-image" | "motion-transfer"
#   FEATURE_TITLE tên hiển thị, vd "Create-Image (Qwen-Image-Edit)"
#   JOB_TYPE      JOB_TYPES verbatim, vd "create-image" | "motion,teen-flycam"
#   CATALOG_FILE  đường dẫn tuyệt đối catalog feature
#   COMFY_NODES   danh sách repo custom node (cách nhau bởi khoảng trắng)
#   MODEL_GROUP   tên nhóm model trong catalog (in hướng dẫn tải), vd "Qwen-Image-Edit"
#   NEED_OLLAMA   1 = cài Ollama server (create-image dịch VN→EN) · 0 = bỏ
#   PM2_APPS      danh sách app PM2 bật (vd "minio,api,wf-worker,worker,comfyui")
#
# Tuỳ chọn:
#   NEED_BG_REMOVER 1 = dựng venv bg-remover (rembg) · mặc định 0. PHẢI đặt 1 nếu PM2_APPS
#                   có "bg-remover", nếu không app đó khởi động rồi crash vòng lặp vì thiếu venv.
#   DEFAULT_DOMAIN domain backend mặc định gợi ý
#
# Rồi gọi:  feature_main
#
# Cờ env tuỳ chọn (giống setup-pm2.sh): DOMAIN, SUPER_ADMIN, GMAIL_USER,
#   GMAIL_APP_PASSWORD, CF_API_TOKEN, CF_TUNNEL_TOKEN, CORS_ORIGINS, HF_TOKEN,
#   SKIP_COMFY=1, SKIP_DRIVER=1, SKIP_HTTPS=1, COMFY_DIR=...
# ════════════════════════════════════════════════════════════════════════════
set -uo pipefail   # KHÔNG set -e: nhiều bước best-effort, tự xử lý lỗi cục bộ.

# API key MẶC ĐỊNH = đúng key FE (motions) đang dùng → FE kết nối được ngay.
DEFAULT_API_KEY="${DEFAULT_API_KEY:-mk_$(head -c 24 /dev/urandom | od -An -tx1 | tr -dc a-f0-9)}"

# ── Helpers log ─────────────────────────────────────────────────────────────
say()  { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m  ✓ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m  ! %s\033[0m\n' "$*"; }
die()  { printf '\n\033[1;31m  ✗ %s\033[0m\n' "$*"; exit 1; }
rnd()  { head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n'; }

# set_kv KEY VALUE — sửa (hoặc thêm) dòng KEY=... trong .env
set_kv() {
  local k="$1" v="$2"
  if grep -qE "^${k}=" .env 2>/dev/null; then
    local ev="${v//\\/\\\\}"; ev="${ev//|/\\|}"
    sed -i "s|^${k}=.*|${k}=${ev}|" .env
  else
    printf '%s=%s\n' "$k" "$v" >> .env
  fi
}
get_kv() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2-; }

# ALD 13/07/2026 - Dùng cùng baseline GPU với fullstack installer.
# shellcheck disable=SC1091
source "$ROOT/setup/lib-gpu.sh"

# ensure_secret KEY — sinh secret ngẫu nhiên nếu đang trống / còn placeholder change-*
ensure_secret() {
  local cur; cur="$(get_kv "$1")"
  case "$cur" in ""|change-*|doi-thanh-*) set_kv "$1" "$(rnd)";; esac
}

require_ubuntu() {
  command -v apt-get >/dev/null 2>&1 || die "Script này cho Ubuntu/Debian (không thấy apt-get)."
  [ "$(uname -s)" = "Linux" ] || die "Chỉ chạy trên Linux (VPS). Trên macOS dùng ./setup/setup.sh."
}

# pg — chạy psql/createdb dưới user postgres ở ĐÚNG port cluster ($PG_PORT, mặc định 5432; xem phase_postgres).
pg() { ( cd /tmp && sudo -u postgres env PGPORT="${PG_PORT:-5432}" "$@" ); }

# send_setup_email — gửi thông tin kết nối FE cho SUPER_ADMIN qua Gmail SMTP (App Password).
send_setup_email() {
  local guser gpass
  guser="$(get_kv GMAIL_USER)"; gpass="$(get_kv GMAIL_APP_PASSWORD)"
  { [ -z "$guser" ] || [ -z "$gpass" ]; } && return 1
  M_USER="$guser" M_PASS="$gpass" M_TO="$SUPER_ADMIN" M_BASE="${BE_URL:-https://$DOMAIN}" \
  M_KEY="$API_KEY_VAL" M_APP="$(get_kv APP_NAME)" M_FEAT="$FEATURE_TITLE" python3 - <<'PY'
import os, smtplib, ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr
user=os.environ["M_USER"].strip(); pwd=os.environ["M_PASS"].replace(" ","")
to=os.environ["M_TO"].strip(); base=os.environ["M_BASE"].strip().rstrip("/")
key=os.environ["M_KEY"].strip(); app=(os.environ.get("M_APP") or "Motions").strip()
feat=(os.environ.get("M_FEAT") or "").strip()
env_block=(f"NUXT_MOTION_API_URL={base}\n"
           f"NUXT_MOTION_API_KEY={key}\n"
           f"NUXT_PUBLIC_MOTION_BACKEND_URL={base}")
subject=f"[{app}] Backend ({feat}) đã sẵn sàng — thông tin kết nối Frontend"
htitle=f"🚀 Backend đã sẵn sàng"
text=(f"Motion Backend (box chuyên: {feat}) đã sẵn sàng.\n\n"
      f"Dán vào .env của Frontend (motions):\n{env_block}\n\n"
      f"Health: {base}/health\nĐăng nhập admin: {to} (mở FE, bấm gửi OTP).\n"
      f"LƯU Ý: box này CHỈ chạy chức năng '{feat}' — tải model qua Settings → Models AI.\n")
inner=(f'<p style="margin:0 0 8px;color:#334155;font-size:15px;line-height:1.6;">Chào Admin, Motion Backend (box chuyên <b>{feat}</b>) đã cài xong &amp; đang chạy (PM2).</p>'
       f'<p style="margin:0 0 18px;color:#64748b;font-size:13px;">Box này KHOÁ — chỉ chạy chức năng <b>{feat}</b>. Tải model qua Settings → Models AI.</p>'
       f'<div style="font-size:12px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:1px;margin:0 0 8px;">Dán vào file .env của Frontend (motions)</div>'
       f'<pre style="margin:0 0 22px;background:#0f172a;color:#e2e8f0;padding:16px 18px;border-radius:10px;font-family:SFMono-Regular,Consolas,monospace;font-size:13px;line-height:1.7;white-space:pre-wrap;word-break:break-all;border:1px solid #1e293b;">{env_block}</pre>'
       f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 22px;">'
       f'<tr><td style="padding:10px 0;border-top:1px solid #e2e8f0;color:#64748b;font-size:13px;width:120px;">Health check</td><td style="padding:10px 0;border-top:1px solid #e2e8f0;"><a href="{base}/health" style="color:#6366f1;font-size:13px;text-decoration:none;">{base}/health</a></td></tr>'
       f'<tr><td style="padding:10px 0;border-top:1px solid #e2e8f0;color:#64748b;font-size:13px;">Đăng nhập admin</td><td style="padding:10px 0;border-top:1px solid #e2e8f0;color:#334155;font-size:13px;">{to} <span style="color:#94a3b8;">— mở FE, bấm gửi OTP</span></td></tr></table>'
       f'<a href="{base}" style="display:inline-block;background:#6366f1;color:#ffffff;text-decoration:none;font-weight:600;font-size:14px;padding:12px 24px;border-radius:10px;">Mở Backend →</a>')
html=f"""<!doctype html><html><body style="margin:0;padding:0;background:#0f172a;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#0f172a;padding:32px 12px;"><tr><td align="center">
    <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;background:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 10px 40px rgba(0,0,0,.35);">
      <tr><td style="background:linear-gradient(135deg,#6366f1,#8b5cf6);padding:28px 32px;">
        <div style="font-size:13px;letter-spacing:2px;color:#e0e7ff;text-transform:uppercase;">{app} · {feat}</div>
        <div style="font-size:24px;font-weight:700;color:#ffffff;margin-top:6px;">{htitle}</div></td></tr>
      <tr><td style="padding:28px 32px 8px;">{inner}</td></tr>
      <tr><td style="padding:18px 32px 26px;"><p style="margin:0;color:#94a3b8;font-size:12px;line-height:1.6;border-top:1px solid #e2e8f0;padding-top:16px;">Email tự động gửi bởi setup. Nếu bạn không yêu cầu cài đặt, hãy bỏ qua email này.</p></td></tr>
    </table></td></tr></table>
</body></html>"""
msg=MIMEMultipart("alternative")
msg["Subject"]=subject
msg["From"]=formataddr((app, user)); msg["To"]=to
msg.attach(MIMEText(text, "plain", "utf-8"))
msg.attach(MIMEText(html, "html", "utf-8"))
with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as s:
    s.starttls(context=ssl.create_default_context()); s.login(user, pwd)
    s.sendmail(user, [to], msg.as_string())
print("sent")
PY
}

# setup_cloudflare_tunnel_auto — TỰ ĐỘNG 100% qua Cloudflare API (cần CF_API_TOKEN):
# tìm zone → tạo/lấy tunnel → ingress thẳng http://localhost:8080 → DNS CNAME proxied → chạy cloudflared.
setup_cloudflare_tunnel_auto() {
  local api="https://api.cloudflare.com/client/v4" dom="$DOMAIN" tok="$CF_API_TOKEN"
  cfget()  { curl -s -H "Authorization: Bearer $tok" "$api$1"; }
  cfjson() { curl -s -X "$1" -H "Authorization: Bearer $tok" -H "Content-Type: application/json" --data "$3" "$api$2"; }

  local zinfo zid acc zname
  zinfo="$(cf_zone_lookup "$tok" "$dom")"
  zid="${zinfo%% *}"; zname="${zinfo##* }"; acc="$(printf '%s' "$zinfo" | awk '{print $2}')"
  if [ -z "$zid" ]; then
    warn "Không thấy zone Cloudflare cho '$dom'. Token cần: Account.Cloudflare Tunnel:Edit + Zone.DNS:Edit + Zone.Zone:Read; Zone Resources = Include domain gốc."
    return 1
  fi
  ok "Zone: $zname"

  local tname="motion-${dom//./-}" tid ttoken
  tid="$(cfget "/accounts/$acc/cfd_tunnel?name=$tname&is_deleted=false" | python3 -c 'import sys,json;r=json.load(sys.stdin).get("result") or [];print(r[0]["id"] if r else "")' 2>/dev/null)"
  if [ -z "$tid" ]; then
    tid="$(cfjson POST "/accounts/$acc/cfd_tunnel" "{\"name\":\"$tname\",\"config_src\":\"cloudflare\"}" | python3 -c 'import sys,json;print((json.load(sys.stdin).get("result") or {}).get("id",""))' 2>/dev/null)"
  fi
  [ -z "$tid" ] && { warn "Tạo/lấy tunnel lỗi — token thiếu quyền Account.Cloudflare Tunnel:Edit?"; return 1; }
  ttoken="$(cfget "/accounts/$acc/cfd_tunnel/$tid/token" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("result") or "")' 2>/dev/null)"
  [ -z "$ttoken" ] && { warn "Lấy tunnel token lỗi."; return 1; }
  ok "Tunnel: $tname"

  cf_ok()  { python3 -c 'import sys,json;sys.exit(0 if json.load(sys.stdin).get("success") else 1)' 2>/dev/null; }
  cf_err() { python3 -c 'import sys,json;e=json.load(sys.stdin).get("errors") or [];print(e[0].get("message","?") if e else "?")' 2>/dev/null; }

  local apip="${API_PORT:-8080}"
  local pairs="$dom:$apip"
  [ -n "${CF_FE_DOMAIN:-}" ] && pairs="$pairs ${CF_FE_DOMAIN}:${CF_FE_PORT:-2030}"
  local ing="" p h pt
  for p in $pairs; do h="${p%%:*}"; pt="${p##*:}"; ing="${ing}{\"hostname\":\"$h\",\"service\":\"http://localhost:$pt\"},"; done
  local ingresp
  ingresp="$(cfjson PUT "/accounts/$acc/cfd_tunnel/$tid/configurations" "{\"config\":{\"ingress\":[${ing}{\"service\":\"http_status:404\"}]}}")"
  printf '%s' "$ingresp" | cf_ok || { warn "Cấu hình ingress lỗi: $(printf '%s' "$ingresp" | cf_err)"; return 1; }
  ok "Ingress: ${dom}→:$apip"

  cf_zone_for_host() { cf_zone_lookup "$tok" "$1" | awk '{print $1, $3}'; }
  local rdom recid dnsresp body hzinfo hzid hzname
  for p in $pairs; do
    rdom="${p%%:*}"
    hzinfo="$(cf_zone_for_host "$rdom")"; hzid="${hzinfo%% *}"; hzname="${hzinfo##* }"
    if [ -z "$hzid" ]; then
      warn "Không thấy zone Cloudflare cho DNS '$rdom' — token chưa có Zone.Zone:Read cho domain này."
      return 1
    fi
    body="{\"type\":\"CNAME\",\"name\":\"$rdom\",\"content\":\"$tid.cfargotunnel.com\",\"proxied\":true,\"ttl\":1}"
    recid="$(cfget "/zones/$hzid/dns_records?name=$rdom" | python3 -c 'import sys,json;r=json.load(sys.stdin).get("result") or [];print(r[0]["id"] if r else "")' 2>/dev/null)"
    if [ -n "$recid" ]; then dnsresp="$(cfjson PUT "/zones/$hzid/dns_records/$recid" "$body")"
    else dnsresp="$(cfjson POST "/zones/$hzid/dns_records" "$body")"; fi
    if ! printf '%s' "$dnsresp" | cf_ok; then
      warn "Tạo DNS '$rdom' lỗi ($(printf '%s' "$dnsresp" | cf_err)) — token thường THIẾU Zone · DNS · Edit (Zone = $hzname)."
      return 1
    fi
    ok "DNS: $rdom → CNAME ${tid:0:8}….cfargotunnel.com (proxied, SSL Cloudflare tự cấp)"
  done

  if ! command -v cloudflared >/dev/null 2>&1; then
    $SUDO curl -fsSL -o /usr/local/bin/cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 && $SUDO chmod +x /usr/local/bin/cloudflared
  fi
  $SUDO cloudflared service uninstall >/dev/null 2>&1 || true
  $SUDO cloudflared service install "$ttoken" >/dev/null 2>&1
  $SUDO systemctl enable --now cloudflared >/dev/null 2>&1 || true
  sleep 5
  systemctl is-active cloudflared >/dev/null 2>&1 && ok "cloudflared đang chạy (kết nối edge)." || warn "cloudflared chưa active — xem journalctl -u cloudflared -n 30"
  return 0
}

cf_zone_lookup() {
  local tok="$1" host="$2" api="https://api.cloudflare.com/client/v4" cand resp out
  host="${host#http://}"; host="${host#https://}"; host="${host%%/*}"; host="${host%.}"
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

cf_api_preflight() {
  [ -z "${CF_API_TOKEN:-}" ] && return 0
  local api="https://api.cloudflare.com/client/v4" tok="$CF_API_TOKEN" host zinfo zid acc zname verify dnsok tunnelok
  verify="$(curl -s -H "Authorization: Bearer $tok" "$api/user/tokens/verify")"
  if ! printf '%s' "$verify" | python3 -c 'import sys,json;sys.exit(0 if json.load(sys.stdin).get("success") else 1)' 2>/dev/null; then
    warn "Cloudflare API Token không verify được. Hãy nhập token API, không phải Tunnel token."
    return 1
  fi
  for host in "$DOMAIN" ${CF_FE_DOMAIN:+$CF_FE_DOMAIN}; do
    zinfo="$(cf_zone_lookup "$tok" "$host")"
    zid="${zinfo%% *}"; zname="${zinfo##* }"; acc="$(printf '%s' "$zinfo" | awk '{print $2}')"
    if [ -z "$zid" ]; then
      warn "Cloudflare token OK nhưng không thấy zone cho '$host'."
      warn "  Domain gốc cần nằm trong Zone Resources của token (vd datools.info hoặc All zones)."
      return 1
    fi
    ok "Cloudflare zone preflight: $host → $zname"
    dnsok="$(curl -s -H "Authorization: Bearer $tok" "$api/zones/$zid/dns_records?per_page=1")"
    if ! printf '%s' "$dnsok" | python3 -c 'import sys,json;sys.exit(0 if json.load(sys.stdin).get("success") else 1)' 2>/dev/null; then
      warn "Token đọc DNS record của zone '$zname' không được — cần Zone.DNS:Edit."
      return 1
    fi
    tunnelok="$(curl -s -H "Authorization: Bearer $tok" "$api/accounts/$acc/cfd_tunnel?per_page=1&is_deleted=false")"
    if ! printf '%s' "$tunnelok" | python3 -c 'import sys,json;sys.exit(0 if json.load(sys.stdin).get("success") else 1)' 2>/dev/null; then
      warn "Token không truy cập Cloudflare Tunnel trong account của '$zname' — cần Account.Cloudflare Tunnel:Edit đúng account."
      return 1
    fi
  done
  ok "Cloudflare API Token preflight OK."
}

# ════════════════════════════════════════════════════════════════════════════
# PHASES
# ════════════════════════════════════════════════════════════════════════════
phase_bootstrap() {
  say "0/11 · Kiểm tra môi trường — box chuyên: $FEATURE_TITLE"
  require_ubuntu
  if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
    command -v sudo >/dev/null 2>&1 || { apt-get update -y -qq && apt-get install -y -qq sudo; }
  else
    SUDO="sudo"
    command -v sudo >/dev/null 2>&1 || die "Cần cài sudo (hoặc chạy bằng root)."
    sudo -v || die "Cần quyền sudo."
  fi
  USER_NAME="$(id -un)"; HOME_DIR="$HOME"
  ok "User=$USER_NAME · Home=$HOME_DIR · $(lsb_release -ds 2>/dev/null || echo Linux)"
  ok "profile khai JOB_TYPES=$JOB_TYPE (có thể bị thu hẹp bằng JOB_TYPES_OVERRIDE ở phase_dotenv) · catalog=$(basename "$CATALOG_FILE") · KHÔNG tải model lúc cài"
}

phase_prompt() {
  say "1/11 · Hỏi cấu hình (domain backend, admin, Gmail gửi OTP)"
  if [ -z "${DOMAIN:-}" ]; then
    printf '  Domain backend (chỉ tên miền, KHÔNG kèm https:// hay /) [%s]: ' "$DEFAULT_DOMAIN"
    read -r _d || true; DOMAIN="${_d:-$DEFAULT_DOMAIN}"
  fi
  DOMAIN="${DOMAIN#http://}"; DOMAIN="${DOMAIN#https://}"; DOMAIN="${DOMAIN%%/*}"; DOMAIN="${DOMAIN// /}"
  [ -z "$DOMAIN" ] && DOMAIN="$DEFAULT_DOMAIN"
  if [ -z "${SUPER_ADMIN:-}" ]; then
    while [ -z "${SUPER_ADMIN:-}" ]; do
      printf '  Email ADMIN gốc (đăng nhập quản trị, BẮT BUỘC): '
      read -r SUPER_ADMIN || true
    done
  fi
  if [ -z "${GMAIL_USER:-}" ]; then
    printf '  Gmail GỬI OTP (vd you@gmail.com) [Enter = bỏ qua, cấu hình sau]: '
    read -r GMAIL_USER || true
  fi
  if [ -n "${GMAIL_USER:-}" ] && [ -z "${GMAIL_APP_PASSWORD:-}" ]; then
    printf '  Gmail App Password (16 ký tự, KHÔNG phải mật khẩu thường): '
    read -rs GMAIL_APP_PASSWORD || true; echo
  fi
  GMAIL_APP_PASSWORD="${GMAIL_APP_PASSWORD:-}"; GMAIL_APP_PASSWORD="${GMAIL_APP_PASSWORD// /}"
  if [ "${SKIP_HTTPS:-0}" != "1" ]; then
    if [ -z "${CF_API_TOKEN:-}" ] && [ -z "${CF_TUNNEL_TOKEN:-}" ]; then
      printf '  Cloudflare API Token (TỰ ĐỘNG hết, khuyên dùng — Enter để bỏ qua): '
      read -r CF_API_TOKEN || true
    fi
    if [ -z "${CF_API_TOKEN:-}" ] && [ -z "${CF_TUNNEL_TOKEN:-}" ]; then
      printf '  Cloudflare Tunnel token (bán tự động — Enter = dùng nginx+certbot): '
      read -r CF_TUNNEL_TOKEN || true
    fi
    if [ -n "${CF_API_TOKEN:-}" ]; then
      while ! cf_api_preflight; do
        if [ -t 0 ]; then
          warn "Cloudflare API Token chưa dùng được cho domain '$DOMAIN'. Nhập token khác để thử lại, hoặc Enter để bỏ API token."
          printf '  Cloudflare API Token mới: '
          read -r CF_API_TOKEN || true
          if [ -z "${CF_API_TOKEN:-}" ]; then
            warn "Bỏ Cloudflare API token. Script sẽ hỏi/ dùng Tunnel token hoặc nginx+certbot."
            break
          fi
        else
          die "Cloudflare API Token preflight lỗi ngay bước 1 — dừng sớm để bạn đổi token/quyền zone."
        fi
      done
    fi
  fi
  if [ -z "${HF_TOKEN:-}" ]; then
    printf '  HuggingFace Token (tải model gated; Enter = bỏ qua): '
    read -r HF_TOKEN || true
  fi
  if [ -z "${CORS_ORIGINS:-}" ]; then
    printf '  URL Frontend cho CORS (vd https://motions.cong-ty.com, Enter = cho tất cả): '
    read -r CORS_ORIGINS || true
  fi
  if [ -n "${CORS_ORIGINS:-}" ]; then
    _co=""; IFS=',' read -ra _arr <<< "$CORS_ORIGINS"
    for u in "${_arr[@]}"; do u="${u// /}"; [ -z "$u" ] && continue
      case "$u" in http://*|https://*) ;; *) u="https://$u";; esac
      proto="${u%%://*}"; rest="${u#*://}"; host="${rest%%/*}"; u="$proto://$host"
      _co="${_co:+$_co,}$u"
    done
    CORS_ORIGINS="$_co"
  fi
  _https="nginx+certbot"; [ -n "${CF_TUNNEL_TOKEN:-}" ] && _https="CF-Tunnel(token)"; [ -n "${CF_API_TOKEN:-}" ] && _https="CF-Tunnel(API,tự động)"
  ok "DOMAIN=$DOMAIN · ADMIN=$SUPER_ADMIN · GMAIL=${GMAIL_USER:-(chưa đặt)} · HTTPS=$_https · CORS=${CORS_ORIGINS:-*}"
}

phase_apt() {
  say "2/11 · Cài gói hệ thống (apt) + Node 20 + PM2 + MinIO"
  export DEBIAN_FRONTEND=noninteractive
  $SUDO apt-get update -y -qq
  # ALD 04/07/2026 - zstd: ollama.com/install.sh bản mới cần zstd để giải nén, thiếu sẽ "ERROR: This version requires zstd".
  $SUDO apt-get install -y -qq \
    ca-certificates curl gnupg git build-essential pkg-config \
    ffmpeg python3 python3-venv python3-pip python3-dev \
    jq unzip zstd lsb-release aria2 \
    nginx postgresql postgresql-contrib \
    certbot python3-certbot-nginx \
    || die "apt-get install lỗi."
  ok "Gói hệ thống xong"

  NODE_MAJOR="$(node -v 2>/dev/null | sed 's/^v\([0-9]*\).*/\1/')"
  if [ -z "$NODE_MAJOR" ] || [ "$NODE_MAJOR" -lt 20 ]; then
    say "    Cài Node.js 20 (NodeSource)…"
    curl -fsSL https://deb.nodesource.com/setup_20.x | $SUDO bash - >/dev/null
    $SUDO apt-get install -y -qq nodejs || die "Cài nodejs lỗi."
  fi
  command -v pm2 >/dev/null 2>&1 || $SUDO npm install -g pm2 >/dev/null 2>&1 || die "Cài pm2 lỗi."
  ok "Node $(node -v) · npm $(npm -v) · pm2 $(pm2 -v 2>/dev/null | head -1)"

  if ! command -v minio >/dev/null 2>&1; then
    say "    Cài MinIO server (binary)…"
    $SUDO curl -fsSL -o /usr/local/bin/minio https://dl.min.io/server/minio/release/linux-amd64/minio || die "Tải minio lỗi."
    $SUDO chmod +x /usr/local/bin/minio
  fi
  ok "MinIO: $(command -v minio)"
}

phase_dotenv() {
  say "3/11 · Tạo/đồng bộ .env (KHOÁ feature)"
  IP="$(curl -fsS https://api.ipify.org 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}')"
  if [ "${SKIP_HTTPS:-0}" = "1" ]; then BE_URL="${PUBLIC_BASE_URL:-http://$IP:${API_PORT:-8080}}"; else BE_URL="https://$DOMAIN"; fi
  if [ ! -f .env ]; then cp .env.example .env; ok ".env tạo từ .env.example"; else ok ".env đã có — giữ secret cũ, cập nhật cấu hình."; fi
  ensure_secret POSTGRES_PASSWORD
  ensure_secret MINIO_ROOT_PASSWORD
  ensure_secret SESSION_JWT_SECRET
  cur="$(get_kv WORKER_TOKEN)"; case "$cur" in ""|change-*) set_kv WORKER_TOKEN "wt_$(rnd)";; esac
  cur="$(get_kv API_KEY)";      case "$cur" in ""|change-*) set_kv API_KEY "$DEFAULT_API_KEY";; esac
  set_kv COMPOSE_PROFILES ""
  set_kv DOMAIN "$DOMAIN"
  set_kv PUBLIC_BASE_URL "$BE_URL"
  [ -n "${IP:-}" ] && set_kv S3_PUBLIC_ENDPOINT "http://$IP:9000"
  set_kv COMFY_URL "http://127.0.0.1:8188"
  set_kv INTERNAL_API_URL "http://127.0.0.1:8080"
  # ── KHOÁ #1: catalog RIÊNG của feature (Settings → Models AI chỉ thấy model này) ──
  set_kv MODEL_CATALOG_PATH "$CATALOG_FILE"
  # ── KHOÁ #2: JOB_TYPES = ĐÚNG nhóm type của feature → worker không nhận job khác ──
  # ALD 05/08/2026 - JOB_TYPES_OVERRIDE hẹp hơn JOB_TYPE của profile: dùng khi volume chưa có
  # model cho hết số type profile khai. Chỉ CẮT BỚT, không thêm — type ngoài profile nghĩa là
  # thiếu custom node, worker sẽ chết ở /prompt chứ không phải chỉ thiếu model.
  _JT="$JOB_TYPE"
  if [ -n "${JOB_TYPES_OVERRIDE:-}" ]; then
    _bad=""
    for _t in $(echo "$JOB_TYPES_OVERRIDE" | tr ',' ' '); do
      case ",$JOB_TYPE," in *",$_t,"*) ;; *) _bad="$_bad $_t" ;; esac
    done
    if [ -n "$_bad" ]; then
      die "JOB_TYPES_OVERRIDE có type ngoài profile $FEATURE:$_bad — profile khai: $JOB_TYPE"
    fi
    _JT="$JOB_TYPES_OVERRIDE"
    # Chỉ báo "bị thu hẹp" khi thật sự khác — trùng y hệt profile (vd. người vận hành điền
    # nguyên JOB_TYPE vào JOB_TYPES_OVERRIDE cho rõ ràng) thì đây không phải một cú cắt, im lặng.
    [ "$_JT" = "$JOB_TYPE" ] || warn "JOB_TYPES bị thu hẹp bằng JOB_TYPES_OVERRIDE: $_JT (profile khai $(echo "$JOB_TYPE" | tr ',' '\n' | wc -l | tr -d ' ') type)"
  fi
  set_kv JOB_TYPES "$_JT"
  if [ "${NEED_OLLAMA:-0}" = "1" ]; then set_kv OLLAMA_URL "http://127.0.0.1:11434"; else set_kv OLLAMA_URL ""; fi
  [ -n "${CORS_ORIGINS:-}" ] && set_kv CORS_ORIGINS "$CORS_ORIGINS"
  [ -n "${HF_TOKEN:-}" ] && set_kv HF_TOKEN "$HF_TOKEN"
  # ALD 20/08/2026 - fallback key/model Gemini (try-on provider='gemini' + teaser/TTS) — node vẫn ưu
  # tiên tuyệt đối, đây chỉ để khỏi nhập tay key mỗi node. Trống = không ghi dòng (worker dùng default
  # GEMINI_IMAGE_MODEL hard-code trong linux.py; thiếu GEMINI_API_KEY thì bắt buộc key ở node).
  [ -n "${GEMINI_API_KEY:-}" ] && set_kv GEMINI_API_KEY "$GEMINI_API_KEY"
  [ -n "${GEMINI_IMAGE_MODEL:-}" ] && set_kv GEMINI_IMAGE_MODEL "$GEMINI_IMAGE_MODEL"
  set_kv SUPER_ADMIN "$SUPER_ADMIN"
  if [ -n "${GMAIL_USER:-}" ]; then
    set_kv IS_USED_GMAIL "true"; set_kv GMAIL_USER "$GMAIL_USER"
    [ -n "$GMAIL_APP_PASSWORD" ] && set_kv GMAIL_APP_PASSWORD "$GMAIL_APP_PASSWORD"
  else
    warn "Chưa đặt Gmail → login OTP CHƯA gửi được. Sửa GMAIL_USER/GMAIL_APP_PASSWORD trong .env rồi 'pm2 restart api'."
  fi
  chmod 600 .env
  ok ".env sẵn sàng (JOB_TYPES=$_JT)"

  PG_USER="$(get_kv POSTGRES_USER)";  PG_USER="${PG_USER:-motion}"
  PG_PASS="$(get_kv POSTGRES_PASSWORD)"
  PG_DB="$(get_kv POSTGRES_DB)";      PG_DB="${PG_DB:-motion}"
  API_KEY_VAL="$(get_kv API_KEY)"
}

phase_postgres() {
  say "4/11 · Postgres (native) — role + database"
  # ALD 16/06/2026 - NHẬN PORT ĐỘNG: nếu host 5432 đã bị chiếm (vd .165 có postgres-server Docker bind 5432),
  # cluster native tự tạo ở 5433. Lấy port THẬT từ pg_lsclusters (cột 3) thay vì giả định 5432 → khỏi báo "không
  # thấy socket" oan. Ghi POSTGRES_PORT vào .env để api/ecosystem + seed dùng đúng port (KHÔNG cứng 5432).
  $SUDO mkdir -p /var/run/postgresql && $SUDO chown postgres:postgres /var/run/postgresql 2>/dev/null || true
  PG_PORT="$(pg_lsclusters -h 2>/dev/null | awk 'NR==1{print $3}')"; PG_PORT="${PG_PORT:-5432}"
  $SUDO systemctl enable --now postgresql >/dev/null 2>&1 || true
  # ALD 27/07/2026 - Kiểm bằng BẮT TAY THẬT (pg_isready), không nhìn file socket.
  # Image dựng sẵn mang theo socket mồ côi từ lúc `apt install postgresql` trong lúc build → điều kiện
  # [ -S socket ] PASS trong khi KHÔNG có tiến trình nào nghe → script bỏ qua bước khởi động rồi psql
  # báo "Connection refused", còn màn hình vẫn in "✓ Postgres port 5432". Gặp đúng cảnh này trên pod
  # RunPod ngày 27/07. Socket là dấu vết, pg_isready mới là bằng chứng.
  _pg_up() { pg_isready -h /var/run/postgresql -p "$PG_PORT" >/dev/null 2>&1; }
  if ! _pg_up; then
    # Dọn socket mồ côi trước, nếu không postgres mới sẽ từ chối bind.
    $SUDO rm -f "/var/run/postgresql/.s.PGSQL.$PG_PORT" "/var/run/postgresql/.s.PGSQL.$PG_PORT.lock" 2>/dev/null || true
    # ALD 27/07/2026 - Tự sửa quyền data dir trước khi start.
    # Base image chạy mọi lệnh build với `umask 002` (xem history của vastai/comfy), nên
    # /var/lib/postgresql/<ver>/main bị tạo group-writable. Postgres TỪ CHỐI khởi động khi data dir
    # không phải 0700/0750 và chỉ ghi lý do vào /var/log/postgresql/ — nhìn ngoài chỉ thấy "down".
    for _d in /var/lib/postgresql/*/main; do
      [ -d "$_d" ] || continue
      $SUDO chown -R postgres:postgres "$_d" 2>/dev/null || true
      $SUDO chmod 0700 "$_d" 2>/dev/null || true
    done
    $SUDO chown postgres:postgres /var/run/postgresql 2>/dev/null || true
    _pgver="$(pg_lsclusters -h 2>/dev/null | awk 'NR==1{print $1}')"
    _pgclu="$(pg_lsclusters -h 2>/dev/null | awk 'NR==1{print $2}')"
    # Image có thể cài postgres mà CHƯA có cluster nào (apt trong Docker hay bỏ qua initdb) → tự tạo.
    if [ -z "$_pgver" ]; then
      _pgver="$(ls /usr/lib/postgresql 2>/dev/null | sort -Vr | head -1)"
      [ -n "$_pgver" ] && $SUDO pg_createcluster "$_pgver" main --start >/dev/null 2>&1 || true
    fi
    [ -n "$_pgver" ] && $SUDO pg_ctlcluster "$_pgver" "${_pgclu:-main}" start >/dev/null 2>&1 || true
    $SUDO service postgresql start >/dev/null 2>&1 || true
    for _i in $(seq 1 30); do _pg_up && break; sleep 1; done
  fi
  _pg_up || die "Postgres không khởi động được (pg_isready thất bại). Thử tay: pg_lsclusters · sudo pg_ctlcluster <ver> main start · xem /var/log/postgresql/."
  set_kv POSTGRES_PORT "$PG_PORT"
  [ "$PG_PORT" = "5432" ] && ok "Postgres port 5432" || warn "Host 5432 bận → Postgres native ở port $PG_PORT (đã ghi POSTGRES_PORT=$PG_PORT vào .env)."
  pg psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='${PG_USER}'" | grep -q 1 \
    || pg psql -qc "CREATE ROLE ${PG_USER} LOGIN PASSWORD '${PG_PASS}'" || die "Tạo role postgres lỗi."
  pg psql -qc "ALTER ROLE ${PG_USER} WITH PASSWORD '${PG_PASS}'" >/dev/null
  # CREATEDB để setup/pod-pgdump.sh --verify dựng được DB tạm mà diễn tập nạp lại. Role này
  # vốn đã sở hữu toàn bộ database của app trên một pod dùng riêng, nên quyền tạo thêm một DB
  # không mở ra gì mới — đổi lại ta có đường chứng minh backup nạp được, thay vì chỉ tin.
  pg psql -qc "ALTER ROLE ${PG_USER} CREATEDB" >/dev/null
  pg psql -tAc "SELECT 1 FROM pg_database WHERE datname='${PG_DB}'" | grep -q 1 \
    || pg createdb -O "${PG_USER}" "${PG_DB}" || die "Tạo database lỗi."
  ok "Postgres: role=${PG_USER} db=${PG_DB} @127.0.0.1:${PG_PORT} (schema tự nạp khi api khởi động)"
}

# bg-remover (rembg: tách nền, crop sản phẩm cho tryon — ecosystem.config.cjs:195). Box chuyên
# không bật app này nên không dựng venv: rembg kéo theo onnxruntime, mất vài phút cài cho một
# tiến trình sẽ không bao giờ nhận request. Profile full thì cần, và cần ở CẢ hai đường cài —
# image dựng sẵn (worker-image/Dockerfile) từ 05/08/2026 CÓ bake venv này; ensure_bg_remover()
# symlink vào đó (kiểm import chạy được trước khi tin), và phase_prebuilt_deps() cũng gọi cùng
# hàm để dùng chung một đường kiểm.
ensure_bg_remover() {
  [ "${NEED_BG_REMOVER:-0}" = "1" ] || return 0
  # ALD 05/08/2026 - Image dựng sẵn có venv bg-remover → symlink thay vì pip lại vài phút mỗi boot.
  # Kiểm chạy được chứ không tin nó có mặt là đủ, cùng cách phase_prebuilt_deps kiểm api-node_modules.
  local _pre="${MTC_PREBUILT_DIR:-/opt/mtc-prebuilt}/bg-remover-venv"
  if [ "${MTC_PREBUILT:-0}" = "1" ] && [ -x "$_pre/bin/python" ]; then
    if "$_pre/bin/python" -c "import rembg" >/dev/null 2>&1; then
      rm -rf "$ROOT/bg-remover/venv"
      ln -s "$_pre" "$ROOT/bg-remover/venv"
      ok "bg-remover: dùng venv dựng sẵn từ image"
      return 0
    fi
    warn "venv bg-remover dựng sẵn không import được rembg → dựng lại tại chỗ."
  fi
  [ -x "$ROOT/bg-remover/venv/bin/python" ] || python3 -m venv "$ROOT/bg-remover/venv"
  "$ROOT/bg-remover/venv/bin/pip" install -q --upgrade pip >/dev/null
  "$ROOT/bg-remover/venv/bin/pip" install -q -r "$ROOT/bg-remover/requirements.txt" \
    || warn "pip bg-remover có cảnh báo — tryon vẫn chạy, phần tách nền/crop sản phẩm tự tắt."
  ok "bg-remover: venv xong"
}

phase_app_deps() {
  if [ "${NEED_BG_REMOVER:-0}" = "1" ]; then
    say "5/11 · Cài deps app (api npm, worker venv, bg-remover venv)"
  else
    say "5/11 · Cài deps app (api npm, worker venv) — KHÔNG bg-remover (box chuyên không dùng)"
  fi
  ln -sfn ../db "$ROOT/api/db"
  ( cd api && npm install --omit=dev --no-audit --no-fund >/dev/null 2>&1 ) || die "npm install (api) lỗi."
  ok "api: node_modules xong"
  [ -x "$ROOT/worker/venv/bin/python" ] || python3 -m venv "$ROOT/worker/venv"
  "$ROOT/worker/venv/bin/pip" install -q --upgrade pip >/dev/null
  "$ROOT/worker/venv/bin/pip" install -q -r "$ROOT/worker/requirements.txt" || warn "pip worker có cảnh báo."
  ok "worker: venv xong"
  ensure_bg_remover
  mkdir -p "$ROOT/.data/minio"
}

# #region ALD 19/07/2026 - Fast boot từ image dựng sẵn (MTC_PREBUILT=1).
# Image chỉ chứa dependency/runtime (không chứa token). Source vẫn clone bằng credential read-only
# ở bootstrap. Link các dependency dựng sẵn thay vì apt/npm/pip lại ở mỗi lần bật instance.
# ALD 26/07/2026 - Không gắn nhà cung cấp nào: điều kiện duy nhất là có marker
# $MTC_PREBUILT_DIR/.ready. Hiện dùng cho pod RunPod (image worker-image/Dockerfile).
phase_prebuilt_deps() {
  say "2–7/11 · Fast boot từ image dựng sẵn (bỏ apt, Node, PyTorch và ComfyUI install)"
  local prebuilt="${MTC_PREBUILT_DIR:-/opt/mtc-prebuilt}"
  [ -f "$prebuilt/.ready" ] || die "MTC_PREBUILT=1 nhưng image không có $prebuilt/.ready"

  # ALD 27/07/2026 - KIỂM node_modules dựng sẵn có dùng được không, đừng tin nó có mặt là đủ.
  # Sự cố 27/07 trên pod RunPod: image có /opt/mtc-prebuilt/api-node_modules nhưng THIẾU dependency
  # bắc cầu (pg có, pg-types không) → task-cloud-auto crash "Cannot find module 'pg-types'" ngay lúc
  # nạp, PM2 restart vòng lặp, máy cài xong 100% mà không bao giờ ping được Task Cloud.
  # Nguyên nhân gốc là layer npm install trong image hỏng/cache dở; ở đây chỉ cần phát hiện và tự dựng lại.
  if [ -d "$prebuilt/api-node_modules" ]; then
    rm -rf "$ROOT/api/node_modules"
    ln -s "$prebuilt/api-node_modules" "$ROOT/api/node_modules"
    if ! (cd "$ROOT/api" && node -e "require('pg');require('pg-types');require('express')" >/dev/null 2>&1); then
      warn "node_modules dựng sẵn thiếu dependency → cài lại tại chỗ (chậm hơn vài phút nhưng chắc)."
      rm -f "$ROOT/api/node_modules"
      (cd "$ROOT/api" && npm install --omit=dev --no-audit --no-fund >/dev/null 2>&1) \
        || die "npm install cho api/ thất bại — xem 'cd $ROOT/api && npm install'."
      ok "api/node_modules cài lại xong"
    fi
  fi
  if [ -d "$prebuilt/worker-venv" ]; then
    rm -rf "$ROOT/worker/venv"
    ln -s "$prebuilt/worker-venv" "$ROOT/worker/venv"
  fi
  # Image dựng sẵn ĐÃ bake venv bg-remover (worker-image/Dockerfile) — ensure_bg_remover() symlink
  # vào đó khi import chạy được. Chỉ dựng tại chỗ (chậm hơn fast-boot vài phút) khi venv dựng sẵn
  # thiếu/hỏng; thiếu cả hai đường thì PM2 app bg-remover crash vòng lặp và tryon mất phần crop.
  ensure_bg_remover

  COMFY_DIR="${COMFY_DIR:-$prebuilt/ComfyUI}"
  [ -f "$COMFY_DIR/main.py" ] || die "Image dựng sẵn thiếu ComfyUI tại $COMFY_DIR"
  [ -x "$COMFY_DIR/venv/bin/python" ] || die "Image dựng sẵn thiếu Python venv của ComfyUI"
  mkdir -p "$ROOT/.data/minio" "$COMFY_DIR/models/uploads"/{loras,checkpoints,unet,vae,text_encoders,clip_vision}

  # Seed models của chính ComfyUI (configs/*.yaml + placeholder) do image dời sang comfy-models-seed
  # để pod-volume.sh nối được $COMFY_DIR/models sang volume. Chép phần CÒN THIẾU sang volume:
  # `cp -rn` không bao giờ ghi đè, nên model người dùng đã tải an toàn tuyệt đối.
  # Đặt SAU khi COMFY_DIR đã set và sau mkdir -p models/uploads ở trên, để chắc chắn $COMFY_DIR/models
  # đã là symlink trỏ vào volume: scripts/pod-bootstrap.sh chạy setup/pod-volume.sh (nối volume)
  # TRƯỚC khi gọi setup-full.sh → feature_main() → phase_prebuilt_deps() này. Nếu cp chạy trước khi
  # có symlink, nó ghi vào container disk và mất sạch, im lặng, lúc gpu-destroy.
  # Bỏ qua im lặng nếu không có thư mục seed (image cũ, hoặc đường không-prebuilt).
  local _seed="$prebuilt/comfy-models-seed"
  if [ -d "$_seed" ]; then
    cp -rn "$_seed/." "$COMFY_DIR/models/" \
      || warn "gieo seed models từ $_seed sang $COMFY_DIR/models lỗi (hết chỗ trên volume? quyền ghi?) — model người dùng không mất, nhưng thiếu placeholder/configs của ComfyUI."
  fi

  set_kv COMFY_LOCAL "1"
  set_kv COMFY_DIR "$COMFY_DIR"
  set_kv COMFY_MODELS_DIR "$COMFY_DIR/models"
  set_kv MODEL_UPLOADS_DIR "$COMFY_DIR/models/uploads"
  GPU_OK=1
  ok "Runtime dựng sẵn sàng: Node $(node -v) · ComfyUI · worker venv · custom nodes"
}
# #endregion

phase_ollama() {
  if [ "${NEED_OLLAMA:-0}" != "1" ]; then
    say "6/11 · Ollama — BỎ QUA ($FEATURE không cần LLM)"
    return 0
  fi
  say "6/11 · Ollama server (dịch VN→EN cho create-image) — KHÔNG pull model (tải riêng qua Settings)"
  if ! command -v ollama >/dev/null 2>&1; then
    curl -fsSL https://ollama.com/install.sh | sh || warn "Cài Ollama lỗi — dịch prompt sẽ tắt (tải/cài lại sau)."
  fi
  if command -v ollama >/dev/null 2>&1; then
    $SUDO systemctl enable --now ollama >/dev/null 2>&1 || true
    if ! curl -s --max-time 3 http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
      ( nohup ollama serve >/tmp/ollama.log 2>&1 & )
      for _i in $(seq 1 25); do curl -s --max-time 2 http://127.0.0.1:11434/api/version >/dev/null 2>&1 && break; sleep 1; done
    fi
    ok "Ollama server sẵn sàng — pull 'qwen2.5:7b-instruct' qua Settings → Models AI (nhóm Ollama)."
  fi
}

phase_comfyui() {
  say "7/11 · GPU / ComfyUI native — chỉ custom node của $FEATURE, KHÔNG tải model"
  COMFY_DIR="${COMFY_DIR:-$HOME_DIR/ComfyUI}"
  set_kv COMFY_MODELS_DIR "$COMFY_DIR/models"
  GPU_OK=0
  if [ "${SKIP_COMFY:-0}" = "1" ]; then
    warn "SKIP_COMFY=1 → KHÔNG cài ComfyUI. Set COMFY_URL trong .env trỏ ComfyUI máy khác."
  elif command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
    GPU_OK=1; ok "GPU: $(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)"
  elif lspci 2>/dev/null | grep -qi nvidia; then
    warn "Thấy card NVIDIA nhưng CHƯA có driver."
    if [ "${SKIP_DRIVER:-0}" != "1" ]; then
      say "    Cài NVIDIA driver (graphics-drivers PPA + ubuntu-drivers)…"
      $SUDO add-apt-repository -y ppa:graphics-drivers/ppa >/dev/null 2>&1 || true
      $SUDO apt-get update -y -qq
      $SUDO apt-get install -y -qq ubuntu-drivers-common >/dev/null 2>&1 || true
      $SUDO ubuntu-drivers autoinstall || warn "ubuntu-drivers autoinstall lỗi — cài driver tay."
      warn "⚠ ĐÃ cài driver — PHẢI REBOOT rồi chạy lại script để hoàn tất ComfyUI:  sudo reboot"
    fi
  else
    warn "Không có GPU NVIDIA → bỏ ComfyUI. Set COMFY_URL trong .env trỏ box GPU khác."
  fi

  # Chống NVML "Driver/library version mismatch": chốt gói nvidia khỏi unattended-upgrade.
  if command -v apt-get >/dev/null 2>&1 && { [ "$GPU_OK" = "1" ] || lspci 2>/dev/null | grep -qi nvidia; }; then
    $SUDO mkdir -p /etc/apt/apt.conf.d
    printf '%s\n' \
      '// Pebsteel: KHÔNG auto-upgrade driver NVIDIA (tránh lệch kernel-module ↔ NVML userspace).' \
      'Unattended-Upgrade::Package-Blacklist {' '  "nvidia-";' '  "libnvidia-";' '};' \
      | $SUDO tee /etc/apt/apt.conf.d/51-pebsteel-no-nvidia-auto >/dev/null 2>&1 || true
    _nv="$(dpkg -l 2>/dev/null | awk '/^ii/ && $2 ~ /^(nvidia-|libnvidia-)/ {print $2}')"
    [ -n "$_nv" ] && $SUDO apt-mark hold $_nv >/dev/null 2>&1 || true
    ok "Đã chốt driver NVIDIA khỏi auto-upgrade."
  fi

  if [ "$GPU_OK" = "1" ]; then
    # Clone ROBUST: nếu thiếu main.py mà dir non-empty → tách models/venv/custom_nodes, clone full, trả lại.
    if [ ! -f "$COMFY_DIR/main.py" ]; then
      if [ -d "$COMFY_DIR" ] && [ -n "$(ls -A "$COMFY_DIR" 2>/dev/null)" ]; then
        say "    ComfyUI dir có sẵn nhưng thiếu code → clone lại, giữ models/venv/custom_nodes…"
        _keep="$(mktemp -d)"
        for x in models venv custom_nodes hf-cache extra_model_paths.yaml; do [ -e "$COMFY_DIR/$x" ] && mv "$COMFY_DIR/$x" "$_keep/" 2>/dev/null; done
        rm -rf "$COMFY_DIR"
        git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git "$COMFY_DIR" || warn "clone ComfyUI lỗi."
        for x in models venv custom_nodes hf-cache extra_model_paths.yaml; do [ -e "$_keep/$x" ] && { rm -rf "$COMFY_DIR/$x"; mv "$_keep/$x" "$COMFY_DIR/"; }; done
        rm -rf "$_keep"
      else
        git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git "$COMFY_DIR" || warn "clone ComfyUI lỗi."
      fi
    fi
    [ -x "$COMFY_DIR/venv/bin/python" ] || python3 -m venv "$COMFY_DIR/venv"
    CPIP="$COMFY_DIR/venv/bin/pip"
    "$CPIP" install -q --upgrade pip wheel >/dev/null
    motion_install_best_pytorch "$COMFY_DIR" || warn "GPU stack chưa đạt baseline; ComfyUI có thể không dùng được GPU."
    "$CPIP" install -q -r "$COMFY_DIR/requirements.txt" || warn "pip ComfyUI requirements có cảnh báo."

    # ── Custom nodes: CHỈ của feature (COMFY_NODES) — KHÔNG cài ComfyUI-Manager (khoá) ──
    CN="$COMFY_DIR/custom_nodes"; mkdir -p "$CN"
    for repo in $COMFY_NODES; do
      d="$CN/$(basename "$repo")"
      [ -d "$d/.git" ] || git clone --depth 1 "$repo" "$d" || warn "clone $(basename "$repo") lỗi."
      # ALD 24/07/2026 - FlashVSR được cài dependency runtime tường minh bên dưới; tránh option pip
      # nội dòng trong requirements upstream làm setup feature bỏ sót cả node Enhance.
      if [ "$(basename "$repo")" != "ComfyUI-FlashVSR_Stable" ]; then
        [ -f "$d/requirements.txt" ] && "$CPIP" install -q -r "$d/requirements.txt" >/dev/null 2>&1 || true
      fi
      # ALD 22/06/2026 - ComfyUI-Frame-Interpolation (RIFE VFI cho enhance 30/60fps) dùng requirements-no-cupy.txt
      # (không có requirements.txt thuần; RIFE không cần cupy). rife47.pth node tự tải lần đầu.
      [ -f "$d/requirements-no-cupy.txt" ] && "$CPIP" install -q -r "$d/requirements-no-cupy.txt" >/dev/null 2>&1 || true
    done
    # ── Vá LTXVideo: shim kornia 'pad' (kornia mới bỏ 'pad' khỏi geometry.transform.pyramid → pyramid_blending
    #    import lỗi làm CẢ pack LTXVideo không nạp → Teaser motionMode='ltx' âm thầm fallback Ken Burns).
    #    Cùng shim với setup-pm2.sh:739-749, idempotent (grep trước khi chèn) — feature "full" mới có node này.
    LTXI="$CN/ComfyUI-LTXVideo/__init__.py"
    if [ -f "$LTXI" ] && ! grep -q "ALD kornia pad shim" "$LTXI"; then
      { printf '%s\n' \
          '# ALD kornia pad shim (14/06/2026) - kornia moi bo pad khoi geometry.transform.pyramid' \
          'try:' \
          '    import kornia.geometry.transform.pyramid as _kp, torch.nn.functional as _F' \
          '    if not hasattr(_kp, "pad"): _kp.pad = _F.pad' \
          'except Exception: pass'; cat "$LTXI"; } > "$LTXI.tmp" && mv "$LTXI.tmp" "$LTXI" && ok "vá LTXVideo (kornia pad shim)"
    fi
    if [ -d "$CN/ComfyUI-FlashVSR_Stable" ]; then
      "$CPIP" install -q einops safetensors tqdm pillow huggingface_hub psutil "opencv-python>=4.8.1.78" pyyaml \
        >/dev/null 2>&1 || warn "cài dependency runtime FlashVSR có cảnh báo."
    fi
    # ALD 18/08/2026 - QUAY LẠI: node này ĐÃ CẦN. Chốt gỡ 11/07 bị vượt bởi fix
    # "chu mỏ" 21/07 — worker_runtime/linux.py:1657 dựng node 26 PoseAndFaceDetection,
    # nên thiếu nó là mọi job motion âm thầm fallback DWPose pad128 (đo 17/08 + 18/08).
    # Node đã vào COMFY_NODES của cả hai setup profile, nên vòng cài node lo deps.
    # Bộ dep ComfyUI tương thích (transformers/diffusers mới — diffusers cũ dùng cached_download đã bị HF bỏ → Wan/Qwen gãy).
    "$CPIP" install -q -U "transformers>=4.50,<4.57" "diffusers>=0.31" >/dev/null 2>&1 || warn "nâng transformers/diffusers có cảnh báo."
    motion_install_best_pytorch "$COMFY_DIR" || warn "GPU stack hậu cài đặt không đạt baseline."
    [ -z "$(get_kv PYTORCH_ALLOC_CONF)" ] && set_kv PYTORCH_ALLOC_CONF "expandable_segments:True"
    [ -z "$(get_kv CUDA_MODULE_LOADING)" ] && set_kv CUDA_MODULE_LOADING "LAZY"
    [ -z "$(get_kv MOTION_TORCH_COMPILE)" ] && set_kv MOTION_TORCH_COMPILE "0"
    motion_autoset_vram_gate   # cổng model-on-VRAM theo VRAM thật (lib-gpu.sh) — card 24GB phải ép offload
    # Feature motion/I2V dùng cùng SageAttention với fullstack; GPU cũ tự giữ sdpa.
    if "$CPIP" install -q sageattention >/dev/null 2>&1 \
      && "$COMFY_DIR/venv/bin/python" -c 'import sageattention, torch; cc=torch.cuda.get_device_capability(0); raise SystemExit(0 if cc[0] >= 8 else 1)' >/dev/null 2>&1; then
      [ -z "$(get_kv MOTION_ATTENTION)" ] && set_kv MOTION_ATTENTION "sageattn"
      ok "SageAttention OK → MOTION_ATTENTION=$(get_kv MOTION_ATTENTION)"
    else
      warn "SageAttention không phù hợp GPU này → giữ sdpa."
    fi
    ok "ComfyUI + custom node ($(echo $COMFY_NODES | wc -w) node) ở $COMFY_DIR"

    # Thư mục uploads (model user tự upload) + extra_model_paths.
    UP="$COMFY_DIR/models/uploads"; mkdir -p "$UP"/{loras,checkpoints,unet,vae,text_encoders,clip_vision} "$UP/.tmp"
    set_kv MODEL_UPLOADS_DIR "$UP"
    EMP="$COMFY_DIR/extra_model_paths.yaml"
    if [ ! -f "$EMP" ]; then
      cat > "$EMP" <<YML
# Model do user upload qua Settings (route /models). Gom 1 nơi → dễ dọn.
uploads:
  base_path: $UP
  loras: loras/
  checkpoints: checkpoints/
  unet: unet/
  diffusion_models: unet/
  vae: vae/
  text_encoders: text_encoders/
  clip_vision: clip_vision/
YML
      ok "tạo extra_model_paths.yaml (uploads → $UP)"
    fi

    # ── KHÔNG TẢI MODEL trong setup (yêu cầu cốt lõi) — tải riêng qua Settings → Models AI ──
    warn "KHÔNG tải model lúc cài. Sau khi xong: Settings → Models AI → nhóm '${MODEL_GROUP:-$FEATURE}' → 'Cài cả nhóm'."

    set_kv COMFY_LOCAL "1"
    set_kv COMFY_DIR "$COMFY_DIR"
  else
    set_kv COMFY_LOCAL "0"
  fi
}

phase_pm2() {
  say "8/11 · Khởi động PM2 — CHỈ app cần: $PM2_APPS"
  pm2 start "$ROOT/ecosystem.config.cjs" --only "$PM2_APPS" >/dev/null 2>&1 \
    || pm2 restart "$ROOT/ecosystem.config.cjs" --only "$PM2_APPS" >/dev/null 2>&1
  pm2 save >/dev/null 2>&1 || true
  $SUDO env PATH="$PATH" "$(command -v pm2)" startup systemd -u "$USER_NAME" --hp "$HOME_DIR" >/dev/null 2>&1 \
    && pm2 save >/dev/null 2>&1 || warn "pm2 startup chưa cài được — chạy tay: pm2 startup"
  ok "PM2: $(pm2 ls --no-color 2>/dev/null | grep -cE ' online ') tiến trình online"
  say "    Chờ API sẵn sàng…"
  for i in $(seq 1 30); do
    curl -fsS "http://127.0.0.1:8080/health" >/dev/null 2>&1 && { ok "API /health OK"; break; }
    sleep 2
    [ "$i" = 30 ] && warn "API chưa trả /health sau 60s — xem 'pm2 logs api'."
  done
}

phase_seed() {
  say "9/11 · Seed workflow mẫu + API key (FE)"
  DATABASE_URL="postgres://${PG_USER}:${PG_PASS}@127.0.0.1:${PG_PORT:-5432}/${PG_DB}" \
    node "$ROOT/scripts/apply-workflow-seed.mjs" >/dev/null 2>&1 && ok "seed workflow_templates" || warn "seed workflow_templates lỗi (chạy tay sau)."
  pg psql -d "$PG_DB" -qc "INSERT INTO api_keys (id,user_id,key,is_active) SELECT gen_random_uuid(), u.id, '${API_KEY_VAL}', true FROM users u WHERE u.email='${SUPER_ADMIN}' AND NOT EXISTS (SELECT 1 FROM api_keys WHERE key='${API_KEY_VAL}')" >/dev/null 2>&1 \
    && ok "seed api_key (FE) → $SUPER_ADMIN" || warn "seed api_key lỗi — seed tay sau."
}

phase_nginx() {
  if [ "${SKIP_HTTPS:-0}" = "1" ]; then
    say "10/11 · Bỏ qua nginx (local/IP mode → API trực tiếp $BE_URL)"
    if command -v ufw >/dev/null 2>&1 && $SUDO ufw status 2>/dev/null | grep -qi "Status: active"; then
      $SUDO ufw allow "${API_PORT:-8080}"/tcp >/dev/null 2>&1; $SUDO ufw allow OpenSSH >/dev/null 2>&1
      ok "ufw: mở cổng ${API_PORT:-8080}."
    fi
  elif [ -n "${CF_API_TOKEN:-}" ] || [ -n "${CF_TUNNEL_TOKEN:-}" ]; then
    say "10/11 · Bỏ qua nginx (Cloudflare Tunnel trỏ thẳng API :8080)"
    ok "cloudflared/ingress trỏ thẳng 'http://localhost:8080' (xem phase 11)."
  else
    say "10/11 · nginx reverse proxy cho $DOMAIN"
    NGINX_SITE="/etc/nginx/sites-available/$DOMAIN"
    if [ -f "$NGINX_SITE" ]; then
      ok "nginx site đã có ($NGINX_SITE) — giữ nguyên (gồm SSL certbot)."
    else
$SUDO tee "$NGINX_SITE" >/dev/null <<NGINX
server {
    listen 80;
    server_name $DOMAIN;
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        client_max_body_size 250M;
        proxy_read_timeout 86400;
        proxy_buffering off;
    }
}
NGINX
    fi
    $SUDO ln -sfn "$NGINX_SITE" "/etc/nginx/sites-enabled/$DOMAIN"
    $SUDO rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true
    $SUDO nginx -t >/dev/null 2>&1 && $SUDO systemctl reload nginx && ok "nginx reload OK" || warn "nginx -t lỗi — kiểm tra config."
    if command -v ufw >/dev/null 2>&1 && $SUDO ufw status 2>/dev/null | grep -qi "Status: active"; then
      $SUDO ufw allow 80/tcp >/dev/null 2>&1; $SUDO ufw allow 443/tcp >/dev/null 2>&1; $SUDO ufw allow OpenSSH >/dev/null 2>&1
      ok "ufw: đã mở 80/443."
    fi
  fi
}

phase_https() {
  if [ "${SKIP_HTTPS:-0}" = "1" ]; then
    say "11/11 · Bỏ qua HTTPS (local/IP mode)"
    ok "API truy cập trực tiếp tại $BE_URL — nhớ mở cổng ${API_PORT:-8080} ở firewall nhà cung cấp."
  elif [ -n "${CF_API_TOKEN:-}" ]; then
    say "11/11 · HTTPS qua Cloudflare Tunnel — TỰ ĐỘNG (API, không cần dashboard)"
    if setup_cloudflare_tunnel_auto; then
      ok "Xong — https://$DOMAIN chạy sau ~1-2 phút (Cloudflare tự cấp SSL)."
    else
      die "Tự động tunnel CHƯA xong — xem cảnh báo trên (token thiếu quyền / domain chưa thuộc Cloudflare / DNS record xung đột)."
    fi
  elif [ -n "${CF_TUNNEL_TOKEN:-}" ]; then
    say "11/11 · HTTPS qua Cloudflare Tunnel (cloudflared, bán tự động)"
    if ! command -v cloudflared >/dev/null 2>&1; then
      $SUDO curl -fsSL -o /usr/local/bin/cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
        && $SUDO chmod +x /usr/local/bin/cloudflared || warn "Tải cloudflared lỗi."
    fi
    $SUDO cloudflared service uninstall >/dev/null 2>&1 || true
    if $SUDO cloudflared service install "$CF_TUNNEL_TOKEN" >/dev/null 2>&1; then
      $SUDO systemctl enable --now cloudflared >/dev/null 2>&1 || true
      sleep 4
      systemctl is-active cloudflared >/dev/null 2>&1 && ok "cloudflared đang chạy." || warn "cloudflared chưa active — journalctl -u cloudflared -n 30"
      warn "CÒN 1 BƯỚC DASHBOARD: Zero Trust → tunnel → Public Hostname:"
      warn "  $DOMAIN  Type=HTTP  URL=localhost:${API_PORT:-8080} → Save."
    else
      warn "cloudflared service install lỗi — kiểm tra token."
    fi
  else
    say "11/11 · HTTPS bằng certbot (Let's Encrypt)"
    CERT_EMAIL="${SUPER_ADMIN:-${GMAIL_USER:-admin@$DOMAIN}}"
    if $SUDO certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$CERT_EMAIL" --redirect >/dev/null 2>&1; then
      ok "Cert cấp xong → https://$DOMAIN"
    else
      warn "certbot CHƯA cấp được cert. Cần DNS '$DOMAIN' trỏ về IP public ($IP) + mở port 80/443 ở firewall nhà cung cấp."
      warn "  Mở xong chạy lại:  sudo certbot --nginx -d $DOMAIN --redirect"
    fi
  fi
}

phase_done() {
  say "XONG — box chuyên: $FEATURE_TITLE"
  echo
  printf '\033[1;32m════════ Motion Backend (%s) đã chạy (PM2) ════════\033[0m\n' "$FEATURE"
  echo "  Health   : curl $BE_URL/health   (local: http://127.0.0.1:8080/health)"
  echo "  Admin    : $SUPER_ADMIN  → FE bấm gửi OTP để đăng nhập"
  echo "  Khoá     : JOB_TYPES=$_JT · catalog=$(basename "$CATALOG_FILE") · KHÔNG có ComfyUI-Manager"
  echo "  PM2      : pm2 ls   ·   pm2 logs api   ·   pm2 restart ecosystem.config.cjs --only $PM2_APPS"
  [ "${GPU_OK:-0}" = "1" ] && echo "  ComfyUI  : pm2 logs comfyui   (native ở ${COMFY_DIR:-?})" || echo "  ComfyUI  : KHÔNG cài local — set COMFY_URL trỏ box GPU rồi 'pm2 restart worker'"
  echo
  printf '\033[1;33m──────── TẢI MODEL (BẮT BUỘC, làm sau khi cài) ────────\033[0m\n'
  echo "  Mở FE → Settings → Models AI → nhóm '${MODEL_GROUP:-$FEATURE}' → bấm 'Cài cả nhóm'."
  echo "  (Box này CHỈ tải được model của '$FEATURE' — đã khoá qua catalog $(basename "$CATALOG_FILE").)"
  echo
  printf '\033[1;36m──────── DÁN VÀO .env CỦA FE (motions) ────────\033[0m\n'
  cat <<FEENV
NUXT_MOTION_API_URL=$BE_URL
NUXT_MOTION_API_KEY=$API_KEY_VAL
NUXT_PUBLIC_MOTION_BACKEND_URL=$BE_URL
FEENV
  echo
  if [ -n "$(get_kv GMAIL_USER)" ]; then
    say "    Gửi email thông tin kết nối FE tới ${SUPER_ADMIN}…"
    if send_setup_email >/dev/null 2>&1; then ok "Đã gửi email tới $SUPER_ADMIN (kiểm tra cả Spam)."
    else warn "Gửi email thất bại — kiểm tra GMAIL_USER/GMAIL_APP_PASSWORD. Thông tin vẫn in ở trên."; fi
  fi
  echo
}

# Khôi phục DB từ volume — PHẢI nằm giữa phase_postgres và bất cứ thứ gì tạo schema.
#   sau phase_postgres: dump chứa ALTER TABLE ... OWNER TO và GRANT nên role + database
#     phải tồn tại trước.
#   trước phase_pm2: api khởi động sẽ chạy api/src/migrate.js tạo bảng từ db/init/*.sql;
#     nạp dump có CREATE TABLE vào DB đã có bảng rỗng là lỗi trùng.
# Không có volume thì đi qua như không có gì.
phase_pg_restore() {
  # POD_VOLUME đọc theo khuôn env-trước-.env-sau (giống pod-volume.sh:188 và pod-pgdump.sh).
  # Có ĐÚNG HAI nguồn, và cần cả hai vì mỗi nguồn hụt ở một đường vào khác nhau:
  #   1. Biến môi trường qua ssh — scripts/pod-bootstrap.sh có hai lệnh ssh và cả hai đều mang
  #      POD_VOLUME theo (lệnh chạy pod-volume.sh, và lệnh chạy setup dẫn tới đây — :173).
  #      Nhưng nó CHỈ có mặt khi phase này chạy từ pod-bootstrap.sh. Người gõ tay
  #      `./setup/setup-motion-transfer.sh` trên pod, hoặc bất kỳ caller nào khác, không có nó.
  #   2. `.env` trên pod qua get_kv — luôn có mặt, không phụ thuộc lệnh gọi. Nhưng trên pod MỚI
  #      thì `.env` chưa tồn tại lúc pod-volume.sh chạy (rsync loại trừ .env và .env.*), nên
  #      khối set_kv_local của nó bị bỏ qua và key này chưa vào được `.env` ở lần dựng đầu tiên.
  # Nguồn (1) bịt đúng lỗ của nguồn (2) ở lần dựng đầu, và `set_kv` ngay dưới đây bịt lỗ của
  # nguồn (1) cho mọi lần chạy sau. Bỏ một trong hai là phase này lặng lẽ không chạy ở đúng
  # kịch bản nó tồn tại để phục vụ — người dùng mất DB mà vẫn thấy mọi thứ xanh.
  local _vol="${POD_VOLUME:-}"
  [ -n "$_vol" ] || _vol="$(get_kv POD_VOLUME)"
  [ -n "$_vol" ] || return 0

  # Ghi lại vào `.env` CỦA POD — nguồn thứ hai, độc lập với biến môi trường.
  # pod-volume.sh:309 gác cả khối ghi `.env` bằng `[ -f "$ROOT/.env" ]`, và trên pod MỚI file
  # đó chưa tồn tại lúc nó chạy (rsync loại trừ `.env` và `.env.*`), nên POD_VOLUME không bao
  # giờ vào được `.env`. Tới đây thì phase_dotenv đã dựng xong `.env`, nên ghi được. Nhờ vậy
  # `make gpu-down` / `gpu-destroy` / `gpu-db-dump` / `gpu-db-check` / `pod-smoke.sh` — và cả
  # người gõ tay `./setup/pod-pgdump.sh --dump` trên pod — đều thấy volume, kể cả khi lệnh ssh
  # của họ quên truyền biến. KHÔNG được thay bằng cách gỡ guard ở pod-volume.sh:309: làm vậy
  # thì pod-volume.sh tạo một `.env` cụt vài key, phase_dotenv thấy `[ ! -f .env ]` sai nên bỏ
  # qua bước dựng `.env` đầy đủ — hỏng nặng hơn nhiều lỗi đang sửa.
  set_kv POD_VOLUME "$_vol"

  say "4b/11 · Khôi phục database từ Network Volume (nếu có bản dump)"
  POD_VOLUME="$_vol" bash "$ROOT/setup/pod-pgdump.sh" --restore \
    || warn "khôi phục DB không thành công — setup vẫn chạy tiếp với DB trống."
}

# feature_main — chạy toàn bộ phase theo thứ tự. Script gọi: set profile rồi `feature_main`.
feature_main() {
  : "${ROOT:?cần ROOT}"; : "${FEATURE:?cần FEATURE}"; : "${JOB_TYPE:?cần JOB_TYPE}"
  : "${CATALOG_FILE:?cần CATALOG_FILE}"; : "${COMFY_NODES:?cần COMFY_NODES}"; : "${PM2_APPS:?cần PM2_APPS}"
  [ -f "$CATALOG_FILE" ] || die "Không thấy catalog feature: $CATALOG_FILE"
  cd "$ROOT"
  phase_bootstrap
  phase_prompt
  if [ "${MTC_PREBUILT:-0}" = "1" ]; then
    # Image đã làm các bước tốn thời gian ở CI. Runtime chỉ tạo secret/DB và bật service.
    phase_dotenv
    phase_postgres
    phase_pg_restore
    phase_prebuilt_deps
    phase_ollama
  else
    phase_apt
    phase_dotenv
    phase_postgres
    phase_pg_restore
    phase_app_deps
    phase_ollama
    phase_comfyui
  fi
  phase_pm2
  phase_seed
  phase_nginx
  phase_https
  phase_done
}
\
