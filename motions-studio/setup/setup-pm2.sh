#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
# setup-pm2.sh — Cài Motion Backend NATIVE bằng PM2 trên VPS Ubuntu 22 (1 lệnh).
#
#   git clone <repo> motion-backend && cd motion-backend
#   ./setup/setup-pm2.sh
#
# Làm tất cả trên 1 máy trống: cài Node/PM2/Postgres/MinIO/Ollama (+ ComfyUI nếu
# có GPU), tạo .env (hỏi DOMAIN + email admin + Gmail gửi OTP), tải models cần
# thiết, chạy mọi service dưới PM2, dựng nginx + certbot HTTPS, rồi IN BLOCK .env
# cho FE (motions) kết nối.
#
# Idempotent: chạy lại an toàn (bước nào xong sẽ skip). KHÔNG cần Docker.
#
# Cờ tuỳ chọn (env):
#   DOMAIN=... SUPER_ADMIN=... GMAIL_USER=... GMAIL_APP_PASSWORD=...  → khỏi hỏi
#   CF_API_TOKEN=...      HTTPS Cloudflare Tunnel TỰ ĐỘNG 100% (tạo tunnel+DNS+ingress, khỏi đụng dashboard)
#   CF_TUNNEL_TOKEN=...   HTTPS Cloudflare Tunnel bán tự động (vẫn phải thêm Public Hostname trên dashboard)
#   CORS_ORIGINS=...      whitelist origin FE (vd https://motion.datools.info), bỏ trống = cho mọi origin
#   SKIP_COMFY=1   bỏ cài ComfyUI + models (dùng ComfyUI máy khác: set COMFY_URL trong .env)
#   SKIP_MODELS=1  cài ComfyUI nhưng KHÔNG tải ~25GB models (tự tải/mount sau)
#   SKIP_OLLAMA_MODELS=1   bỏ pull model Ollama
#   SKIP_DRIVER=1  bỏ tự cài NVIDIA driver
# ════════════════════════════════════════════════════════════════════════════
set -uo pipefail   # KHÔNG set -e: nhiều bước best-effort, tự xử lý lỗi cục bộ.

# ALD 14/06/2026 - script nằm trong setup/ → về gốc repo (mọi path .env/ecosystem/comfyui/db tính từ ROOT).
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

# ── Hằng số dự án ───────────────────────────────────────────────────────────
DEFAULT_DOMAIN="motion-server.datools.info"
# API key MẶC ĐỊNH = đúng key FE (motions) đang dùng → FE kết nối được ngay.
DEFAULT_API_KEY="mk_$(head -c 24 /dev/urandom | od -An -tx1 | tr -dc a-f0-9)"

# ── Helpers log ─────────────────────────────────────────────────────────────
say()  { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m  ✓ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m  ! %s\033[0m\n' "$*"; }
die()  { printf '\n\033[1;31m  ✗ %s\033[0m\n' "$*"; exit 1; }

rnd() { head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n'; }

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

# ALD 13/07/2026 - Một ma trận PyTorch/CUDA dùng chung cho fullstack + feature installers.
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

# send_setup_email — gửi thông tin kết nối FE cho SUPER_ADMIN qua Gmail SMTP (App Password đã cấu hình).
# Dùng python3 smtplib (giữ UTF-8 tiếng Việt). Trả về !=0 nếu thiếu cấu hình hoặc gửi lỗi.
send_setup_email() {
  local guser gpass
  guser="$(get_kv GMAIL_USER)"; gpass="$(get_kv GMAIL_APP_PASSWORD)"
  { [ -z "$guser" ] || [ -z "$gpass" ]; } && return 1
  M_USER="$guser" M_PASS="$gpass" M_TO="$SUPER_ADMIN" M_BASE="${BE_URL:-https://$DOMAIN}" \
  M_KEY="$API_KEY_VAL" M_APP="$(get_kv APP_NAME)" M_FE="${FRONTEND_URL:-}" \
  M_SSHP="${FE_SSH_PORT:-${VAST_TCP_PORT_22:-22}}" python3 - <<'PY'
import os, smtplib, ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr
user=os.environ["M_USER"].strip(); pwd=os.environ["M_PASS"].replace(" ","")
to=os.environ["M_TO"].strip(); base=os.environ["M_BASE"].strip().rstrip("/")
key=os.environ["M_KEY"].strip(); app=(os.environ.get("M_APP") or "Motions").strip()
env_block=(f"NUXT_MOTION_API_URL={base}\n"
           f"NUXT_MOTION_API_KEY={key}\n"
           f"NUXT_PUBLIC_MOTION_BACKEND_URL={base}")
fe=os.environ.get("M_FE","").strip().rstrip("/")          # FRONTEND_URL (fullstack/local) — vd http://<IP>:2030
sshp=(os.environ.get("M_SSHP","") or "22").strip()        # cổng SSH (vast.ai: VAST_TCP_PORT_22, thường 22)

def _hostport(u, defp):
    h=u.split("://",1)[-1].split("/",1)[0]
    return (h.split(":")[0], (h.split(":")[1] if ":" in h else defp))

if fe and fe.startswith("https://"):
    # ── FULLSTACK + Cloudflare Tunnel: end-user chỉ MỞ 1 LINK https, KHÔNG port/tunnel/cài gì ──
    subject=f"[{app}] Ứng dụng đã sẵn sàng — link truy cập"
    htitle="🚀 Ứng dụng đã sẵn sàng"
    text=(f"{app} đã chạy.\n\nMở ứng dụng: {fe}\nĐăng nhập: {to} (bấm gửi OTP, mã về email này).\nBackend API: {base}/health\n")
    inner=(f'<p style="margin:0 0 18px;color:#334155;font-size:15px;line-height:1.6;">Chào Admin, ứng dụng đã sẵn sàng. Bấm nút dưới để mở — không cần cài gì:</p>'
           f'<a href="{fe}" style="display:inline-block;background:#6366f1;color:#fff;text-decoration:none;font-weight:700;font-size:16px;padding:14px 30px;border-radius:10px;margin:0 0 22px;">Mở ứng dụng → {fe}</a>'
           f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0;">'
           f'<tr><td style="padding:10px 0;border-top:1px solid #e2e8f0;color:#64748b;font-size:13px;width:120px;">Đăng nhập</td><td style="padding:10px 0;border-top:1px solid #e2e8f0;color:#334155;font-size:13px;">{to} <span style="color:#94a3b8;">— bấm gửi OTP, mã về email này</span></td></tr>'
           f'<tr><td style="padding:10px 0;border-top:1px solid #e2e8f0;color:#64748b;font-size:13px;">Backend API</td><td style="padding:10px 0;border-top:1px solid #e2e8f0;"><a href="{base}/health" style="color:#6366f1;font-size:13px;text-decoration:none;">{base}/health</a></td></tr></table>')
elif fe:
    # ── FULLSTACK/LOCAL qua IP: mở trực tiếp hoặc SSH tunnel ──
    ip, feport = _hostport(fe, "2030")
    _, apip    = _hostport(base, "8080")
    tunnel=f"ssh -p {sshp} root@{ip} -L {apip}:localhost:{apip} -L {feport}:localhost:{feport}"
    subject=f"[{app}] Ứng dụng đã sẵn sàng — link truy cập"
    htitle="🚀 Ứng dụng đã sẵn sàng"
    text=(f"{app} đã cài xong & đang chạy trên VPS ({ip}).\n\n"
          f"MỞ ỨNG DỤNG — chọn 1 cách:\n"
          f"  ① Trực tiếp (VPS có IP public + đã mở cổng {feport}): {fe}\n"
          f"  ② Qua SSH tunnel (máy NAT / chưa mở cổng):\n"
          f"       {tunnel}\n"
          f"     rồi mở: http://localhost:{feport}\n\n"
          f"Đăng nhập: {to} (bấm gửi OTP, mã về email này).\nBackend API: {base}/health\n")
    inner=(f'<p style="margin:0 0 16px;color:#334155;font-size:15px;line-height:1.6;">Chào Admin, hệ thống đã cài xong &amp; đang chạy trên VPS <b>{ip}</b>. Mở ứng dụng theo 1 trong 2 cách:</p>'
           f'<div style="font-size:12px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:1px;margin:0 0 6px;">① Trực tiếp (VPS có IP public + mở cổng {feport})</div>'
           f'<a href="{fe}" style="display:inline-block;background:#6366f1;color:#fff;text-decoration:none;font-weight:700;font-size:15px;padding:12px 22px;border-radius:10px;margin:0 0 18px;">Mở {fe}</a>'
           f'<div style="font-size:12px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:1px;margin:0 0 6px;">② Qua SSH tunnel (máy NAT / chưa mở cổng)</div>'
           f'<pre style="margin:0 0 6px;background:#0f172a;color:#e2e8f0;padding:14px 16px;border-radius:10px;font-family:SFMono-Regular,Consolas,monospace;font-size:12px;line-height:1.6;white-space:pre-wrap;word-break:break-all;border:1px solid #1e293b;">{tunnel}</pre>'
           f'<p style="margin:0 0 20px;color:#475569;font-size:13px;">→ rồi mở <code style="color:#6366f1;">http://localhost:{feport}</code></p>'
           f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0;">'
           f'<tr><td style="padding:10px 0;border-top:1px solid #e2e8f0;color:#64748b;font-size:13px;width:120px;">Đăng nhập</td><td style="padding:10px 0;border-top:1px solid #e2e8f0;color:#334155;font-size:13px;">{to} <span style="color:#94a3b8;">— bấm gửi OTP, mã về email này</span></td></tr>'
           f'<tr><td style="padding:10px 0;border-top:1px solid #e2e8f0;color:#64748b;font-size:13px;">Backend API</td><td style="padding:10px 0;border-top:1px solid #e2e8f0;"><a href="{base}/health" style="color:#6366f1;font-size:13px;text-decoration:none;">{base}/health</a></td></tr></table>')
else:
    # ── BACKEND-ONLY (domain mode): block .env cho dev nối FE ──
    subject=f"[{app}] Backend đã sẵn sàng — thông tin kết nối Frontend"
    htitle="🚀 Backend đã sẵn sàng"
    text=(f"Motion Backend đã sẵn sàng.\n\nDán vào .env của Frontend (motions):\n{env_block}\n\n"
          f"Health: {base}/health\nĐăng nhập admin: {to} (mở FE, bấm gửi OTP).\n")
    inner=(f'<p style="margin:0 0 18px;color:#334155;font-size:15px;line-height:1.6;">Chào Admin, Motion Backend đã cài đặt xong và đang chạy (PM2) trên VPS. Dưới đây là thông tin để nối Frontend.</p>'
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
        <div style="font-size:13px;letter-spacing:2px;color:#e0e7ff;text-transform:uppercase;">{app}</div>
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
# tìm zone → tạo/lấy tunnel → ingress thẳng http://localhost:8080 → DNS CNAME proxied (GHI ĐÈ record cũ)
# → cài+chạy cloudflared. KHÔNG cần thao tác dashboard. Trả !=0 nếu lỗi (để fallback).
setup_cloudflare_tunnel_auto() {
  local api="https://api.cloudflare.com/client/v4" dom="$DOMAIN" tok="$CF_API_TOKEN"
  cfget()  { curl -s -H "Authorization: Bearer $tok" "$api$1"; }
  cfjson() { curl -s -X "$1" -H "Authorization: Bearer $tok" -H "Content-Type: application/json" --data "$3" "$api$2"; }

  # 1) Zone khớp domain: query exact từng suffix thay vì list per_page=50 rồi tự match.
  # Account nhiều zone có thể làm page đầu không chứa domain gốc → báo thiếu quyền giả.
  local zinfo zid acc zname
  zinfo="$(cf_zone_lookup "$tok" "$dom")"
  zid="${zinfo%% *}"; zname="${zinfo##* }"; acc="$(printf '%s' "$zinfo" | awk '{print $2}')"
  if [ -z "$zid" ]; then
    warn "Không thấy zone Cloudflare cho '$dom'. Token hợp lệ nhưng KHÔNG có quyền với zone này. Kiểm tra token:"
    warn "  • Có đủ 3 quyền: Account.Cloudflare Tunnel:Edit + Zone.DNS:Edit + Zone.Zone:Read"
    warn "  • Zone Resources = Include → domain gốc ('${dom#*.}' hoặc All zones)"
    warn "  • Account Resources = đúng tài khoản chứa domain (nếu bạn có nhiều account Cloudflare)"
    warn "  Kiểm nhanh:  curl -s -H \"Authorization: Bearer <TOKEN>\" https://api.cloudflare.com/client/v4/zones | grep -o '\"name\":\"[^\"]*\"'"
    return 1
  fi
  ok "Zone: $zname"

  # 2) Tạo/lấy tunnel theo tên (config_src=cloudflare để cấu hình ingress qua API)
  local tname="motion-${dom//./-}" tid ttoken
  tid="$(cfget "/accounts/$acc/cfd_tunnel?name=$tname&is_deleted=false" | python3 -c 'import sys,json;r=json.load(sys.stdin).get("result") or [];print(r[0]["id"] if r else "")' 2>/dev/null)"
  if [ -z "$tid" ]; then
    tid="$(cfjson POST "/accounts/$acc/cfd_tunnel" "{\"name\":\"$tname\",\"config_src\":\"cloudflare\"}" | python3 -c 'import sys,json;print((json.load(sys.stdin).get("result") or {}).get("id",""))' 2>/dev/null)"
  fi
  [ -z "$tid" ] && { warn "Tạo/lấy tunnel lỗi — token thiếu quyền Account.Cloudflare Tunnel:Edit?"; return 1; }
  ttoken="$(cfget "/accounts/$acc/cfd_tunnel/$tid/token" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("result") or "")' 2>/dev/null)"
  [ -z "$ttoken" ] && { warn "Lấy tunnel token lỗi."; return 1; }
  ok "Tunnel: $tname"

  # cf_ok: đọc JSON stdin → trả 0 nếu success=true (để check lỗi API, không giả success)
  cf_ok() { python3 -c 'import sys,json;sys.exit(0 if json.load(sys.stdin).get("success") else 1)' 2>/dev/null; }
  cf_err() { python3 -c 'import sys,json;e=json.load(sys.stdin).get("errors") or [];print(e[0].get("message","?") if e else "?")' 2>/dev/null; }

  # 3) Ingress: primary DOMAIN→API; (tuỳ chọn fullstack) CF_FE_DOMAIN→FE. Cuối là catch-all 404.
  # (tách 2 dòng local: `local a=x b=$a` trên cùng dòng → $a chưa set, vỡ với set -u)
  local apip="${API_PORT:-8080}"
  local pairs="$dom:$apip"
  [ -n "${CF_FE_DOMAIN:-}" ] && pairs="$pairs ${CF_FE_DOMAIN}:${CF_FE_PORT:-2030}"
  local ing="" p h pt
  for p in $pairs; do h="${p%%:*}"; pt="${p##*:}"; ing="${ing}{\"hostname\":\"$h\",\"service\":\"http://localhost:$pt\"},"; done
  local ingresp
  ingresp="$(cfjson PUT "/accounts/$acc/cfd_tunnel/$tid/configurations" "{\"config\":{\"ingress\":[${ing}{\"service\":\"http_status:404\"}]}}")"
  printf '%s' "$ingresp" | cf_ok || { warn "Cấu hình ingress lỗi: $(printf '%s' "$ingresp" | cf_err)"; return 1; }
  ok "Ingress: ${dom}→:$apip${CF_FE_DOMAIN:+ · ${CF_FE_DOMAIN}→:${CF_FE_PORT:-2030}}"

  # 4) DNS CNAME proxied cho TỪNG hostname (ghi đè record cũ → khỏi xoá tay)
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

  # 5) Cài + chạy cloudflared bằng tunnel token
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
      warn "  Kiểm nhanh: curl -s -H 'Authorization: Bearer <TOKEN>' 'https://api.cloudflare.com/client/v4/zones?name=datools.info'"
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
say "0/12 · Kiểm tra môi trường"
require_ubuntu
# Chạy được CẢ bằng root (VPS/container) LẪN user thường có sudo. SUDO = "" khi đã là root.
if [ "$(id -u)" -eq 0 ]; then
  SUDO=""
  command -v sudo >/dev/null 2>&1 || { apt-get update -y -qq && apt-get install -y -qq sudo; }  # cần cho 'sudo -u postgres'
else
  SUDO="sudo"
  command -v sudo >/dev/null 2>&1 || die "Cần cài sudo (hoặc chạy bằng root)."
  sudo -v || die "Cần quyền sudo."
fi
USER_NAME="$(id -un)"; HOME_DIR="$HOME"
ok "User=$USER_NAME · Home=$HOME_DIR · $(lsb_release -ds 2>/dev/null || echo Linux)"

# #region ALD 19/07/2026 - Giải phóng cổng chuẩn trước khi cài native
# Vast.ai thường khởi động sẵn Jupyter ở :8080; một lần setup cũ cũng có thể còn
# ComfyUI ở :8188. Bản native này sở hữu hai cổng chuẩn đó, nên cưỡng bức dừng
# listener cũ thay vì tự đổi sang cổng khác (đổi cổng làm connector/provider lệch
# cấu hình). Hàm được gọi thêm lần nữa sát lúc PM2 start để xử lý tiến trình bị
# supervisor của image tự khởi động lại trong lúc setup.
release_motion_ports() {
  local port pids
  for port in 8080 8188; do
    pids="$(lsof -t -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | sort -u | tr '\n' ' ')"
    if [ -n "$pids" ]; then
      warn "Cổng :$port đang bị chiếm bởi PID ${pids% } — kill -9 để Motion Backend sử dụng."
      # Tương đương: sudo kill -9 $(lsof -t -i:$port), nhưng chỉ lấy listener TCP
      # và không gọi kill khi danh sách PID rỗng.
      $SUDO kill -9 $pids >/dev/null 2>&1 || true
    fi
  done
}
# #endregion

# ════════════════════════════════════════════════════════════════════════════
say "1/12 · Hỏi cấu hình (domain backend, admin, Gmail gửi OTP)"
# Domain
if [ -z "${DOMAIN:-}" ]; then
  printf '  Domain backend (chỉ tên miền, KHÔNG kèm https:// hay /) [%s]: ' "$DEFAULT_DOMAIN"
  read -r _d || true; DOMAIN="${_d:-$DEFAULT_DOMAIN}"
fi
# Chuẩn hoá: bỏ scheme http(s)://, bỏ path + dấu / cuối, bỏ khoảng trắng → chỉ còn host thuần.
DOMAIN="${DOMAIN#http://}"; DOMAIN="${DOMAIN#https://}"; DOMAIN="${DOMAIN%%/*}"; DOMAIN="${DOMAIN// /}"
[ -z "$DOMAIN" ] && DOMAIN="$DEFAULT_DOMAIN"
# Super admin (BẮT BUỘC để đăng nhập được — chỉ email có trong DB mới login OTP)
if [ -z "${SUPER_ADMIN:-}" ]; then
  while [ -z "${SUPER_ADMIN:-}" ]; do
    printf '  Email ADMIN gốc (đăng nhập quản trị, BẮT BUỘC): '
    read -r SUPER_ADMIN || true
  done
fi
# Gmail gửi OTP (App Password — cần bật 2FA, tạo tại myaccount.google.com/apppasswords)
if [ -z "${GMAIL_USER:-}" ]; then
  printf '  Gmail GỬI OTP (vd you@gmail.com) [Enter = bỏ qua, cấu hình sau]: '
  read -r GMAIL_USER || true
fi
if [ -n "${GMAIL_USER:-}" ] && [ -z "${GMAIL_APP_PASSWORD:-}" ]; then
  printf '  Gmail App Password (16 ký tự, KHÔNG phải mật khẩu thường): '
  read -rs GMAIL_APP_PASSWORD || true; echo
fi
GMAIL_APP_PASSWORD="${GMAIL_APP_PASSWORD:-}"; GMAIL_APP_PASSWORD="${GMAIL_APP_PASSWORD// /}"  # bỏ space
# HTTPS — 3 đường, ưu tiên từ trên xuống:
#  (1) CF_API_TOKEN  → TỰ ĐỘNG 100%: script tạo tunnel + ingress thẳng :8080 + DNS CNAME proxied (ghi đè
#      record cũ) + chạy cloudflared. KHÔNG cần bấm dashboard. Tạo token 1 lần ở Cloudflare → My Profile →
#      API Tokens → Create, quyền: Account.Cloudflare Tunnel:Edit + Zone.DNS:Edit + Zone.Zone:Read.
#  (2) CF_TUNNEL_TOKEN → bán tự động (vẫn phải tự thêm Public Hostname trên dashboard).
#  (3) cả hai trống → nginx + certbot (cần mở port 80/443 công khai).
# SKIP_HTTPS=1 (fullstack-setup.sh, local/IP) → KHÔNG hỏi Cloudflare (không dùng tunnel/cert).
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
# ALD 13/06/2026 - HF Token (HuggingFace) để tải model GATED (vd SD3.5 Turbo, cần accept license + token).
# Enter để BỎ QUA: model gated sẽ KHÔNG tải, các model còn lại vẫn tải bình thường (setup không hỏng).
if [ -z "${HF_TOKEN:-}" ]; then
  printf '  HuggingFace Token (tải model gated như SD3.5 Turbo; Enter = bỏ qua model gated): '
  read -r HF_TOKEN || true
fi
# URL Frontend cho CORS — whitelist origin FE để tránh lỗi CORS (cách nhau dấu phẩy nếu nhiều).
# Enter để TRỐNG = '*' (cho mọi origin; API tự phản chiếu origin + cho credentials).
if [ -z "${CORS_ORIGINS:-}" ]; then
  printf '  URL Frontend cho CORS (vd https://motions.cong-ty.com, Enter = cho tất cả): '
  read -r CORS_ORIGINS || true
fi
# Chuẩn hoá mỗi URL: bỏ path + dấu / cuối (CORS so khớp theo origin scheme://host[:port]).
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

# ════════════════════════════════════════════════════════════════════════════
say "2/12 · Cài gói hệ thống (apt)"
export DEBIAN_FRONTEND=noninteractive
$SUDO apt-get update -y -qq
# ALD 04/07/2026 - zstd: ollama.com/install.sh bản mới cần zstd để giải nén, thiếu sẽ "ERROR: This version requires zstd".
$SUDO apt-get install -y -qq \
  ca-certificates curl gnupg git build-essential pkg-config \
  ffmpeg python3 python3-venv python3-pip python3-dev \
  jq unzip zstd lsb-release aria2 lsof \
  nginx postgresql postgresql-contrib \
  certbot python3-certbot-nginx \
  || die "apt-get install lỗi."
ok "Gói hệ thống xong"

# Dọn ngay Jupyter/ComfyUI có sẵn của image provider để quá trình setup không
# vô tình kiểm tra nhầm service cũ.
release_motion_ports

# ── Node 20 + PM2 ───────────────────────────────────────────────────────────
NODE_MAJOR="$(node -v 2>/dev/null | sed 's/^v\([0-9]*\).*/\1/')"
if [ -z "$NODE_MAJOR" ] || [ "$NODE_MAJOR" -lt 20 ]; then
  say "    Cài Node.js 20 (NodeSource)…"
  curl -fsSL https://deb.nodesource.com/setup_20.x | $SUDO bash - >/dev/null
  $SUDO apt-get install -y -qq nodejs || die "Cài nodejs lỗi."
fi
command -v pm2 >/dev/null 2>&1 || $SUDO npm install -g pm2 >/dev/null 2>&1 || die "Cài pm2 lỗi."
ok "Node $(node -v) · npm $(npm -v) · pm2 $(pm2 -v 2>/dev/null | head -1)"

# ── MinIO binary ────────────────────────────────────────────────────────────
if ! command -v minio >/dev/null 2>&1; then
  say "    Cài MinIO server (binary)…"
  $SUDO curl -fsSL -o /usr/local/bin/minio https://dl.min.io/server/minio/release/linux-amd64/minio || die "Tải minio lỗi."
  $SUDO chmod +x /usr/local/bin/minio
fi
ok "MinIO: $(command -v minio)"

# ── yt-dlp (import URL Facebook/TikTok/YouTube — api/src/routes/social-imports.js) ──
# ALD 02/07/2026 - Bản PM2-native trước đây THIẾU yt-dlp → FE báo "Server chưa cài yt-dlp. Cài yt-dlp hoặc set
# YTDLP_BIN". Cài binary standalone vào $HOME_DIR/.local/bin (theo user đang chạy setup — KHÔNG hardcode
# /home/ubuntu; api tự dò os.homedir()/.local/bin/yt-dlp nên user nào cũng đúng).
# Dùng nightly vì channel stable thường chậm hơn thay đổi extractor của TikTok/Facebook.
YTDLP_PATH="$HOME_DIR/.local/bin/yt-dlp"
mkdir -p "$HOME_DIR/.local/bin"
# pip nightly + curl-cffi cung cấp browser impersonation. Bản cài bằng pip
# không tự update được qua `yt-dlp -U`, nên luôn chạy lại đúng lệnh pip.
if python3 -m pip install --user --break-system-packages -q -U --pre 'yt-dlp[default,curl-cffi]' >/dev/null 2>&1 \
  || python3 -m pip install --user -q -U --pre 'yt-dlp[default,curl-cffi]' >/dev/null 2>&1; then
  ok "yt-dlp nightly + curl-cffi: $YTDLP_PATH"
elif [ -x "$YTDLP_PATH" ] && "$YTDLP_PATH" --update-to nightly >/dev/null 2>&1; then
  ok "yt-dlp standalone: đã chuyển sang nightly"
elif curl -fsSL -o "$YTDLP_PATH" https://github.com/yt-dlp/yt-dlp-nightly-builds/releases/latest/download/yt-dlp; then
  chmod +x "$YTDLP_PATH"; ok "yt-dlp nightly: $YTDLP_PATH"
else
  warn "Cài yt-dlp lỗi — import URL social sẽ báo thiếu. Cài tay rồi set YTDLP_BIN trong .env."
fi

# ════════════════════════════════════════════════════════════════════════════
say "3/12 · Tạo/đồng bộ .env"
IP="$(curl -fsS https://api.ipify.org 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}')"
# BE_URL = URL công khai của API. SKIP_HTTPS=1 (vd fullstack-setup.sh chạy local qua IP): dùng http://IP:8080,
# bỏ qua nginx/tunnel/certbot. Mặc định: https://<domain>. (SKIP_HTTPS chỉ qua env — KHÔNG thêm câu hỏi.)
if [ "${SKIP_HTTPS:-0}" = "1" ]; then BE_URL="${PUBLIC_BASE_URL:-http://$IP:${API_PORT:-8080}}"; else BE_URL="https://$DOMAIN"; fi
if [ ! -f .env ]; then
  cp .env.example .env
  ok ".env tạo từ .env.example"
else
  ok ".env đã có — giữ secret cũ, cập nhật cấu hình."
fi
# Secrets (chỉ sinh khi trống/placeholder)
ensure_secret POSTGRES_PASSWORD
ensure_secret MINIO_ROOT_PASSWORD
ensure_secret SESSION_JWT_SECRET
cur="$(get_kv WORKER_TOKEN)"; case "$cur" in ""|change-*) set_kv WORKER_TOKEN "wt_$(rnd)";; esac
cur="$(get_kv API_KEY)";      case "$cur" in ""|change-*) set_kv API_KEY "$DEFAULT_API_KEY";; esac
# Giá trị native (luôn đặt đúng cho bản PM2, không-Docker)
set_kv COMPOSE_PROFILES ""
set_kv DOMAIN "$DOMAIN"
set_kv PUBLIC_BASE_URL "$BE_URL"
[ -n "${IP:-}" ] && set_kv S3_PUBLIC_ENDPOINT "http://$IP:9000"
set_kv API_PORT "${API_PORT:-8080}"
set_kv COMFY_PORT "${COMFY_PORT:-8188}"
set_kv COMFY_URL "http://127.0.0.1:8188"
set_kv OLLAMA_URL "http://127.0.0.1:11434"
set_kv INTERNAL_API_URL "http://127.0.0.1:8080"
# ALD 30/06/2026 - Model AI cho nút "Dựng workflow từ kịch bản" (phân tích kịch bản → tự dựng node). CHỈ thêm
# nếu .env chưa có (re-run giữ giá trị user đã chọn). KHÔNG auto-pull ở đây (vd qwen3.6:35b ~24GB) — tải qua
# Settings → Models AI hoặc `ollama pull qwen3.6:35b`. Model tự unload khỏi VRAM ngay sau khi dựng xong.
[ -z "$(get_kv WORKFLOW_AI_MODEL)" ] && set_kv WORKFLOW_AI_MODEL "qwen3.6:35b"
# ALD 15/06/2026 - PM2-native: catalog model nằm ở repo ($ROOT/comfyui/catalog.json), KHÔNG phải /app/catalog.json
# (default Docker trong models-install.js). Thiếu dòng này → api log ENOENT '/app/catalog.json' → tab Settings →
# Models AI RỖNG (0 model, người dùng không có gì để tải).
set_kv MODEL_CATALOG_PATH "$ROOT/comfyui/catalog.json"
# ALD 02/07/2026 - trỏ API thẳng tới binary yt-dlp đã cài ở bước 2 (đường dẫn TUYỆT ĐỐI theo $HOME_DIR của user
# đang chạy — không phụ thuộc /home/ubuntu). Không có file (cài lỗi) → không ghi, API vẫn tự dò PATH.
[ -x "$HOME_DIR/.local/bin/yt-dlp" ] && set_kv YTDLP_BIN "$HOME_DIR/.local/bin/yt-dlp"
# ALD 15/06/2026 - đảm bảo JOB_TYPES có ĐỦ type (kể cả type mới: ss, text-to-video, subtitle, voiceover, wan-i2v,
# enhance) — UNION với .env cũ + dedupe (re-run setup tự thêm type mới vào .env cũ mà không mất type lạ user tự thêm).
# ALD 29/06/2026 - thêm teen-flycam + trend-tiktok vào danh sách chuẩn để worker full tự claim được node social-video mới.
REQ_JT="motion,bds,tryon,create-image,product-overlay,teaser,video,text-to-video,ss,talk,face-motion,concat,story-film,subtitle,voiceover,wan-i2v,teen-flycam,trend-tiktok,enhance"
JT="$(printf '%s,%s' "$REQ_JT" "$(get_kv JOB_TYPES)" | tr ',' '\n' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' | awk 'NF && !seen[$0]++' | paste -sd, -)"
set_kv JOB_TYPES "$JT"
[ -n "${CORS_ORIGINS:-}" ] && set_kv CORS_ORIGINS "$CORS_ORIGINS"
[ -n "${HF_TOKEN:-}" ] && set_kv HF_TOKEN "$HF_TOKEN"   # ALD 13/06/2026 - token tải model gated (Enter bỏ qua thì không ghi)
# Admin + Gmail OTP
set_kv SUPER_ADMIN "$SUPER_ADMIN"
if [ -n "${GMAIL_USER:-}" ]; then
  set_kv IS_USED_GMAIL "true"
  set_kv GMAIL_USER "$GMAIL_USER"
  [ -n "$GMAIL_APP_PASSWORD" ] && set_kv GMAIL_APP_PASSWORD "$GMAIL_APP_PASSWORD"
else
  warn "Chưa đặt Gmail → login OTP CHƯA gửi được. Sửa GMAIL_USER/GMAIL_APP_PASSWORD trong .env rồi 'pm2 restart api'."
fi
chmod 600 .env
ok ".env sẵn sàng"

# Biến local đọc lại từ .env để dùng tiếp
PG_USER="$(get_kv POSTGRES_USER)";  PG_USER="${PG_USER:-motion}"
PG_PASS="$(get_kv POSTGRES_PASSWORD)"
PG_DB="$(get_kv POSTGRES_DB)";      PG_DB="${PG_DB:-motion}"
API_KEY_VAL="$(get_kv API_KEY)"

# ════════════════════════════════════════════════════════════════════════════
say "4/12 · Postgres (native) — role + database"
# ALD 16/06/2026 - NHẬN PORT ĐỘNG: nếu host 5432 đã bị chiếm (vd box .165 có postgres-server Docker bind 5432),
# cluster native tự tạo ở 5433 → check cứng .s.PGSQL.5432 báo "không thấy socket" oan. Lấy port THẬT từ
# pg_lsclusters (cột 3); ghi POSTGRES_PORT vào .env để api/ecosystem + seed dùng đúng (KHÔNG cứng 5432).
$SUDO mkdir -p /var/run/postgresql && $SUDO chown postgres:postgres /var/run/postgresql 2>/dev/null || true
PG_PORT="$(pg_lsclusters -h 2>/dev/null | awk 'NR==1{print $3}')"; PG_PORT="${PG_PORT:-5432}"
$SUDO systemctl enable --now postgresql >/dev/null 2>&1 || true
# CONTAINER (vast.ai/docker) thường KHÔNG có systemd → systemctl im lặng fail → fallback pg_ctlcluster/service.
# ALD 27/07/2026 - Kiểm bằng pg_isready, KHÔNG nhìn file socket. Image container có thể mang theo
# socket mồ côi từ lúc apt cài postgres trong build → [ -S socket ] pass trong khi không tiến trình
# nào nghe → bỏ qua bước start rồi psql "Connection refused" (gặp trên pod RunPod 27/07).
_pg_up() { pg_isready -h /var/run/postgresql -p "$PG_PORT" >/dev/null 2>&1; }
if ! _pg_up; then
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
# Chạy từ /tmp: user 'postgres' không đọc được cwd /root (chmod 700) → tránh cảnh báo "could not change directory".
pg() { ( cd /tmp && sudo -u postgres env PGPORT="${PG_PORT:-5432}" "$@" ); }
pg psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='${PG_USER}'" | grep -q 1 \
  || pg psql -qc "CREATE ROLE ${PG_USER} LOGIN PASSWORD '${PG_PASS}'" \
  || die "Tạo role postgres lỗi."
pg psql -qc "ALTER ROLE ${PG_USER} WITH PASSWORD '${PG_PASS}'" >/dev/null
pg psql -tAc "SELECT 1 FROM pg_database WHERE datname='${PG_DB}'" | grep -q 1 \
  || pg createdb -O "${PG_USER}" "${PG_DB}" \
  || die "Tạo database lỗi."
ok "Postgres: role=${PG_USER} db=${PG_DB} (schema tự nạp khi api khởi động)"

# ════════════════════════════════════════════════════════════════════════════
say "5/12 · Cài deps app (api npm, worker/bg-remover venv) + symlink db"
# api đọc db/init qua đường api/db/init (compose mount sẵn) → symlink cho bản native.
ln -sfn ../db "$ROOT/api/db"
( cd api && npm install --omit=dev --no-audit --no-fund >/dev/null 2>&1 ) || die "npm install (api) lỗi."
ok "api: node_modules xong"

if [ ! -x "$ROOT/worker/venv/bin/python" ]; then
  python3 -m venv "$ROOT/worker/venv"
fi
"$ROOT/worker/venv/bin/pip" install -q --upgrade pip >/dev/null
"$ROOT/worker/venv/bin/pip" install -q -r "$ROOT/worker/requirements.txt" || warn "pip worker có cảnh báo."
ok "worker: venv xong"

if [ ! -x "$ROOT/bg-remover/venv/bin/python" ]; then
  python3 -m venv "$ROOT/bg-remover/venv"
fi
"$ROOT/bg-remover/venv/bin/pip" install -q --upgrade pip >/dev/null
"$ROOT/bg-remover/venv/bin/pip" install -q -r "$ROOT/bg-remover/requirements.txt" || warn "pip bg-remover có cảnh báo."
ok "bg-remover: venv xong"

# Thư mục data MinIO
mkdir -p "$ROOT/.data/minio"

# ════════════════════════════════════════════════════════════════════════════
say "6/12 · Ollama (dịch VN→EN cho create-image, vision tryon, embeddings)"
if ! command -v ollama >/dev/null 2>&1; then
  curl -fsSL https://ollama.com/install.sh | sh || warn "Cài Ollama lỗi — bỏ qua (tryon Auto + dịch prompt sẽ tắt)."
fi
if command -v ollama >/dev/null 2>&1; then
  $SUDO systemctl enable --now ollama >/dev/null 2>&1 || true
  # ALD 16/06/2026 - container KHÔNG systemd → ollama server chưa chạy → pull fail. Start nền + chờ API 11434.
  if ! curl -s --max-time 3 http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
    ( nohup ollama serve >/tmp/ollama.log 2>&1 & )
    for _i in $(seq 1 25); do curl -s --max-time 2 http://127.0.0.1:11434/api/version >/dev/null 2>&1 && break; sleep 1; done
  fi
  if [ "${SKIP_OLLAMA_MODELS:-0}" != "1" ]; then
    # ALD 16/06/2026 - CORE chỉ còn nomic-embed (RAG embeddings). qwen2.5 (dịch VN→EN) → 'cài sau' qua Settings → Models AI.
    #   Đổi danh sách core qua env OLLAMA_CORE_MODELS nếu cần.
    for m in ${OLLAMA_CORE_MODELS:-nomic-embed-text}; do
      ollama list 2>/dev/null | grep -q "^${m%%:*}" && { ok "ollama skip $m (đã có)"; continue; }
      say "    ollama pull $m …"; ollama pull "$m" || warn "pull $m lỗi (pull tay sau / qua Settings → Models AI)."
    done
  else
    warn "SKIP_OLLAMA_MODELS=1 → bỏ pull model Ollama."
  fi
fi

# ════════════════════════════════════════════════════════════════════════════
say "7/12 · GPU / ComfyUI native"
COMFY_DIR="${COMFY_DIR:-$HOME_DIR/ComfyUI}"
# ALD 16/06/2026 - PM2-native: api quét "đã cài" + TẢI model về dir này. Default trong code là /comfy-models (Docker)
# → không set thì tab Models AI báo "đã cài 0" dù có file, và "Cài model" tải sai chỗ. Trỏ đúng ComfyUI/models.
set_kv COMFY_MODELS_DIR "$COMFY_DIR/models"
GPU_OK=0
if [ "${SKIP_COMFY:-0}" = "1" ]; then
  warn "SKIP_COMFY=1 → KHÔNG cài ComfyUI. Nhớ set COMFY_URL trong .env trỏ ComfyUI máy khác."
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
    warn "⚠ ĐÃ cài driver — PHẢI REBOOT rồi chạy lại ./setup/setup-pm2.sh để hoàn tất ComfyUI:"
    warn "    sudo reboot   # sau đó: cd $ROOT && ./setup/setup-pm2.sh"
  fi
else
  warn "Không có GPU NVIDIA → bỏ ComfyUI. Set COMFY_URL trong .env trỏ box GPU khác để worker dùng."
fi

# #region ALD 13/06/2026 - Chống NVML "Driver/library version mismatch". unattended-upgrades của Ubuntu nâng
# userspace nvidia (vd 580.95.05→580.159.03) trong khi KERNEL MODULE đang chạy vẫn bản cũ → nvidia-smi chết
# ("Failed to initialize NVML: Driver/library version mismatch") và CUDA tiến trình MỚI chết (Error 804: forward
# compatibility) → lần restart ComfyUI kế tiếp là mất GPU. Chặn auto-upgrade riêng gói nvidia/libnvidia (kernel
# vẫn được vá bảo mật; reboot là dkms tự rebuild nvidia bản đang giữ cho kernel mới → kernel-module luôn KHỚP
# userspace). Muốn nâng driver: `sudo apt-mark unhold` rồi `apt install` rồi REBOOT. Gỡ hẳn: xoá file dưới.
if command -v apt-get >/dev/null 2>&1 && { [ "$GPU_OK" = "1" ] || lspci 2>/dev/null | grep -qi nvidia; }; then
  $SUDO mkdir -p /etc/apt/apt.conf.d
  printf '%s\n' \
    '// Pebsteel: KHÔNG auto-upgrade driver NVIDIA (tránh lệch kernel-module ↔ NVML userspace → nvidia-smi/CUDA chết).' \
    'Unattended-Upgrade::Package-Blacklist {' \
    '  "nvidia-";' \
    '  "libnvidia-";' \
    '};' | $SUDO tee /etc/apt/apt.conf.d/51-pebsteel-no-nvidia-auto >/dev/null 2>&1 || true
  _nv="$(dpkg -l 2>/dev/null | awk '/^ii/ && $2 ~ /^(nvidia-|libnvidia-)/ {print $2}')"
  [ -n "$_nv" ] && $SUDO apt-mark hold $_nv >/dev/null 2>&1 || true
  ok "Đã chốt driver NVIDIA khỏi auto-upgrade (chống lệch version → nvidia-smi/CUDA chết giữa chừng)."
fi
# #endregion

if [ "$GPU_OK" = "1" ]; then
  # ── Clone ComfyUI + venv + PyTorch CUDA stable phù hợp GPU/driver ──
  # ALD 16/06/2026 - Clone ROBUST: `git clone` vào dir KHÔNG rỗng (re-run: đã có venv/models/custom_nodes) sẽ FAIL
  # → thiếu main.py + requirements.txt → ComfyUI crashloop (ModuleNotFoundError). Mốc kiểm tra = main.py (không phải .git).
  # Thiếu main.py mà dir non-empty: TÁCH dir lớn ra → clone full → trả lại (giữ models/venv/custom_nodes).
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

  # ── Custom nodes (bộ lõi cho Wan 2.2 Animate + LTX + GGUF + DWPose) ──
  CN="$COMFY_DIR/custom_nodes"; mkdir -p "$CN"
  for repo in \
    "https://github.com/kijai/ComfyUI-KJNodes" \
    "https://github.com/kijai/ComfyUI-WanVideoWrapper" \
    "https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite" \
    "https://github.com/city96/ComfyUI-GGUF" \
    "https://github.com/Fannovel16/comfyui_controlnet_aux" \
    "https://github.com/Fannovel16/ComfyUI-Frame-Interpolation" \
    "https://github.com/Lightricks/ComfyUI-LTXVideo" \
    "https://github.com/numz/ComfyUI-SeedVR2_VideoUpscaler" \
    "https://github.com/naxci1/ComfyUI-FlashVSR_Stable" \
    "https://github.com/ltdrdata/ComfyUI-Manager"; do
    d="$CN/$(basename "$repo")"
    [ -d "$d/.git" ] || git clone --depth 1 "$repo" "$d" || warn "clone $(basename "$repo") lỗi."
    # ALD 24/07/2026 - requirements FlashVSR upstream có option pip nội dòng không ổn định giữa các bản pip.
    # Dependency runtime được cài tường minh ngay sau vòng lặp để không làm đổi lại torch/CUDA đang chạy.
    if [ "$(basename "$repo")" != "ComfyUI-FlashVSR_Stable" ]; then
      [ -f "$d/requirements.txt" ] && "$CPIP" install -q -r "$d/requirements.txt" >/dev/null 2>&1 || true
    fi
    # ALD 22/06/2026 - ComfyUI-Frame-Interpolation (node "RIFE VFI" cho enhance 30/60fps) KHÔNG có requirements.txt
    # thuần, chỉ có requirements-no-cupy.txt (RIFE không cần cupy). rife47.pth node tự tải vào ckpts/rife/ lần đầu.
    [ -f "$d/requirements-no-cupy.txt" ] && "$CPIP" install -q -r "$d/requirements-no-cupy.txt" >/dev/null 2>&1 || true
  done
  "$CPIP" install -q einops safetensors tqdm pillow huggingface_hub psutil "opencv-python>=4.8.1.78" pyyaml \
    >/dev/null 2>&1 || warn "cài dependency runtime FlashVSR có cảnh báo."
  # ── Dep-stack ComfyUI cho node SS (LTX-2.3) — ĐÃ VERIFY Wan/Qwen/LTX cùng chạy (ALD 14/06/2026):
  #    LTXVideo cần transformers≥4.50 (Gemma3Config) + diffusers mới (bản cũ dùng cached_download đã bị HF bỏ → Wan gãy).
  "$CPIP" install -q -U "transformers>=4.50,<4.57" "diffusers>=0.31" >/dev/null 2>&1 || warn "nâng transformers/diffusers có cảnh báo."
  # ── Vá LTXVideo: shim kornia 'pad' (kornia mới bỏ 'pad' khỏi geometry.transform.pyramid → pyramid_blending import
  #    lỗi làm CẢ pack LTXVideo không nạp → node SS hỏng). Chèn shim vào ĐẦU __init__.py (idempotent).
  LTXI="$CN/ComfyUI-LTXVideo/__init__.py"
  if [ -f "$LTXI" ] && ! grep -q "ALD kornia pad shim" "$LTXI"; then
    { printf '%s\n' \
        '# ALD kornia pad shim (14/06/2026) - kornia moi bo pad khoi geometry.transform.pyramid; gan tam torch pad' \
        'try:' \
        '    import kornia.geometry.transform.pyramid as _kp, torch.nn.functional as _F' \
        '    if not hasattr(_kp, "pad"): _kp.pad = _F.pad' \
        'except Exception: pass'; cat "$LTXI"; } > "$LTXI.tmp" && mv "$LTXI.tmp" "$LTXI" && ok "vá LTXVideo (kornia pad shim)"
  fi
  # ALD 11/07/2026 - WanAnimatePreprocess (pose retargeting) ĐÃ GỠ theo chốt của user — không cài, không vá.
  # Custom-node requirements có thể đổi torch sau bước đầu; chốt lại baseline trước khi cài kernel attention.
  motion_install_best_pytorch "$COMFY_DIR" || warn "GPU stack hậu cài đặt không đạt baseline."
  [ -z "$(get_kv PYTORCH_ALLOC_CONF)" ] && set_kv PYTORCH_ALLOC_CONF "expandable_segments:True"
  [ -z "$(get_kv CUDA_MODULE_LOADING)" ] && set_kv CUDA_MODULE_LOADING "LAZY"
  [ -z "$(get_kv MOTION_TORCH_COMPILE)" ] && set_kv MOTION_TORCH_COMPILE "0"

  # ── SageAttention (attention INT8 gần-lossless — Wan sampling nhanh hơn ~15-25%, VRAM attention thấp hơn) ──
  # ALD 02/07/2026 - cài bản triton từ PyPI (không compile CUDA, torch/triton đã có ở trên). CHỈ bật
  # MOTION_ATTENTION=sageattn khi GPU ≥ sm_80 (Ampere trở lên — GPU cũ hơn kernel triton không chạy) và import OK.
  # Worker nhận env này qua ecosystem (.env → env service) → build_wan_workflow đặt attention_mode=sageattn.
  # .env đã có MOTION_ATTENTION (user tự chỉnh) → GIỮ NGUYÊN, không ghi đè. Lỗi ở bất kỳ bước nào → giữ sdpa.
  if "$CPIP" install -q sageattention >/dev/null 2>&1; then
    if "$COMFY_DIR/venv/bin/python" -c "import sageattention, torch; cc = torch.cuda.get_device_capability(0); exit(0 if cc[0] >= 8 else 1)" >/dev/null 2>&1; then
      [ -z "$(get_kv MOTION_ATTENTION)" ] && set_kv MOTION_ATTENTION "sageattn"
      ok "SageAttention OK → MOTION_ATTENTION=$(get_kv MOTION_ATTENTION)"
    else
      warn "SageAttention cài được nhưng GPU < sm_80 hoặc import lỗi → giữ attention sdpa."
    fi
  else
    warn "Cài sageattention lỗi → giữ attention sdpa (chậm hơn, vẫn chạy đúng)."
  fi
  ok "ComfyUI + custom nodes ở $COMFY_DIR"

  # ── "Model AI (custom)": 1 thư mục uploads gom model user tự upload (Settings → Model AI) + extra_model_paths ──
  # ALD 14/06/2026 - Tách hẳn model user upload khỏi model hệ thống → muốn dọn chỉ xoá folder uploads/. api ghi
  # file vào đây (bind-mount MODEL_UPLOADS_HOST_DIR), ComfyUI tìm thấy qua extra_model_paths.yaml. Node SS dùng LoRA ở đây.
  UP="$COMFY_DIR/models/uploads"; mkdir -p "$UP"/{loras,checkpoints,unet,vae,text_encoders,clip_vision} "$UP/.tmp"
  set_kv MODEL_UPLOADS_DIR "$UP"   # ALD 15/06/2026 - PM2-native: api đọc/ghi model custom ở đây (mặc định /model-uploads là path Docker)
  EMP="$COMFY_DIR/extra_model_paths.yaml"
  if [ ! -f "$EMP" ]; then
    cat > "$EMP" <<YML
# Model do user upload qua Settings (route /models). Gom 1 nơi → dễ dọn (xoá trong uploads/). ALD 14/06/2026.
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

  # ── ALD 15/06/2026 - Setup CHỈ tải BỘ CORE tối thiểu (comfyui/catalog.json tier=core) để app chạy ngay.
  #    Model nặng/optional (Flux, Wan Animate/I2V/T2V, LTX, …) → cài on-demand trong Settings → Models AI → Download.
  #    aria2c tải song song (nhanh); fallback wget. Skip file đã có. Đặt SKIP_MODELS=1 để bỏ qua hoàn toàn. ──
  if [ "${SKIP_MODELS:-0}" != "1" ]; then
    MODELS_DIR="$COMFY_DIR/models"; HF_TOKEN="$(get_kv HF_TOKEN)"
    DL="wget"; command -v aria2c >/dev/null 2>&1 && DL="aria2c"
    PYBIN="python3"; command -v python3 >/dev/null 2>&1 || PYBIN="$COMFY_DIR/venv/bin/python"
    say "    Tải BỘ CORE model ComfyUI (catalog.json tier=core) bằng $DL — phần còn lại cài qua Settings → Models AI…"
    CORE_LIST="$(
"$PYBIN" - "$ROOT/comfyui/catalog.json" <<'PY'
import json,sys
try: c=json.load(open(sys.argv[1]))
except Exception: sys.exit(0)
for e in c.get("comfy",[]):
    if e.get("tier")=="core" and e.get("url") and e.get("type") and e.get("filename"):
        print("%s\t%s\t%s" % (e["type"], e["filename"], e["url"]))
PY
)"
    printf '%s\n' "$CORE_LIST" | while IFS="$(printf '\t')" read -r dest fname url; do
      [ -z "${dest:-}" ] || [ -z "${fname:-}" ] || [ -z "${url:-}" ] && continue
      mkdir -p "$MODELS_DIR/$dest"; out="$MODELS_DIR/$dest/$fname"
      if [ -f "$out" ] && [ -s "$out" ]; then ok "skip $dest/$fname"; continue; fi
      echo "    ↓ $dest/$fname"
      if [ "$DL" = "aria2c" ]; then
        # ALD 16/06/2026 - HF dùng backend Xet (URL ký byte-range CỐ ĐỊNH) → aria2 -x16 chia range bị 403.
        # HF → 1 luồng (-x1 -s1); host khác giữ 16 luồng. (Cùng lý do với models-install.js.)
        case "$url" in *huggingface.co*) XS=(-x1 -s1);; *) XS=(-x16 -s16);; esac
        AR=("${XS[@]}" -k1M --file-allocation=none --auto-file-renaming=false --allow-overwrite=true --console-log-level=warn -d "$MODELS_DIR/$dest" -o "$fname.part")
        [ -n "${HF_TOKEN:-}" ] && case "$url" in *huggingface.co*) AR+=(--header "Authorization: Bearer ${HF_TOKEN}");; esac
        if aria2c "${AR[@]}" "$url"; then mv "$out.part" "$out"; else warn "tải $fname lỗi"; rm -f "$out.part"; fi
      else
        AUTH=(); [ -n "${HF_TOKEN:-}" ] && AUTH=(--header "Authorization: Bearer ${HF_TOKEN}")
        if wget -q --show-progress "${AUTH[@]}" -O "$out.part" "$url"; then mv "$out.part" "$out"; else warn "tải $fname lỗi"; rm -f "$out.part"; fi
      fi
    done
    ok "Xong bộ core. Model khác (Flux/Wan/LTX/Ollama…) → Settings → Models AI → Download."
  else
    warn "SKIP_MODELS=1 → bỏ tải models (cài qua Settings → Models AI sau)."
  fi

  set_kv COMFY_LOCAL "1"
  set_kv COMFY_DIR "$COMFY_DIR"
else
  set_kv COMFY_LOCAL "0"
fi

# ════════════════════════════════════════════════════════════════════════════
say "8/12 · Khởi động stack bằng PM2"
# Một số image Vast có supervisor tự bật lại Jupyter sau khi bị kill. Dừng app
# PM2 cũ và giành lại :8080/:8188 ngay trước khi startOrReload để API/ComfyUI
# bind cổng trước, tránh vòng lặp EADDRINUSE.
pm2 delete api >/dev/null 2>&1 || true
pm2 delete comfyui >/dev/null 2>&1 || true
release_motion_ports
# ALD 30/06/2026 - PHẢI --update-env: nếu chỉ `pm2 restart` (env cũ) thì JOB_TYPES mới trong .env (vd product-overlay)
# KHÔNG được nạp → worker không claim node mới → job queue mãi. startOrReload: chưa chạy thì start, đang chạy thì
# reload kèm env mới. Sau đó pm2 save để dump giữ env ĐÚNG (lần `pm2 restart all` về sau không quay lại env cũ).
pm2 startOrReload "$ROOT/ecosystem.config.cjs" --update-env >/dev/null 2>&1 \
  || pm2 start "$ROOT/ecosystem.config.cjs" --update-env >/dev/null 2>&1
pm2 save >/dev/null 2>&1 || true
# Tự khởi động lại sau reboot (systemd)
$SUDO env PATH="$PATH" "$(command -v pm2)" startup systemd -u "$USER_NAME" --hp "$HOME_DIR" >/dev/null 2>&1 \
  && pm2 save >/dev/null 2>&1 || warn "pm2 startup chưa cài được — chạy tay: pm2 startup"
ok "PM2: $(pm2 ls --no-color 2>/dev/null | grep -cE ' online ' ) tiến trình online"

# Chờ API health (migrations + bucket chạy lúc boot)
say "    Chờ API sẵn sàng…"
for i in $(seq 1 30); do
  curl -fsS "http://127.0.0.1:8080/health" >/dev/null 2>&1 && { ok "API /health OK"; break; }
  sleep 2
  [ "$i" = 30 ] && warn "API chưa trả /health sau 60s — xem 'pm2 logs api'."
done

# ════════════════════════════════════════════════════════════════════════════
say "9/12 · Seed workflow mẫu (workflow_templates + face_motion)"
DATABASE_URL="postgres://${PG_USER}:${PG_PASS}@127.0.0.1:${PG_PORT:-5432}/${PG_DB}" \
  node "$ROOT/scripts/apply-workflow-seed.mjs" >/dev/null 2>&1 && ok "seed workflow_templates" || warn "seed workflow_templates lỗi (chạy tay sau)."
DATABASE_URL="postgres://${PG_USER}:${PG_PASS}@127.0.0.1:${PG_PORT:-5432}/${PG_DB}" \
  node "$ROOT/scripts/apply-workflow-seed.mjs" db/seeds/face_motion_workflow.sql >/dev/null 2>&1 && ok "seed face_motion" || warn "seed face_motion bỏ qua."
# ALD 16/06/2026 - Seed API_KEY (FE NUXT_MOTION_API_KEY) vào api_keys → super_admin. Thiếu bước này thì api_keys
# RỖNG → mọi call admin qua x-api-key bị 401 → tab Settings (Models AI…) RỖNG dù catalog đọc được. NOT EXISTS = idempotent.
pg psql -d "$PG_DB" -qc "INSERT INTO api_keys (id,user_id,key,is_active) SELECT gen_random_uuid(), u.id, '${API_KEY_VAL}', true FROM users u WHERE u.email='${SUPER_ADMIN}' AND NOT EXISTS (SELECT 1 FROM api_keys WHERE key='${API_KEY_VAL}')" >/dev/null 2>&1 \
  && ok "seed api_key (FE) → $SUPER_ADMIN" || warn "seed api_key lỗi — seed tay sau."

# ════════════════════════════════════════════════════════════════════════════
if [ "${SKIP_HTTPS:-0}" = "1" ]; then
  # Local/IP mode (fullstack-setup.sh): API expose thẳng qua $BE_URL, mở firewall, KHÔNG nginx/tunnel/certbot.
  say "10/12 · Bỏ qua nginx (local/IP mode → API trực tiếp $BE_URL)"
  if command -v ufw >/dev/null 2>&1 && $SUDO ufw status 2>/dev/null | grep -qi "Status: active"; then
    $SUDO ufw allow "${API_PORT:-8080}"/tcp >/dev/null 2>&1; $SUDO ufw allow OpenSSH >/dev/null 2>&1
    ok "ufw: mở cổng ${API_PORT:-8080}."
  fi
elif [ -n "${CF_API_TOKEN:-}" ] || [ -n "${CF_TUNNEL_TOKEN:-}" ]; then
  # Dùng Cloudflare Tunnel → cloudflared forward THẲNG vào API :8080, KHÔNG cần nginx/ufw.
  say "10/12 · Bỏ qua nginx (dùng Cloudflare Tunnel → trỏ thẳng API :8080)"
  ok "Không dựng nginx: cloudflared/ingress trỏ thẳng 'http://localhost:8080' (xem phase 11)."
else
  say "10/12 · nginx reverse proxy cho $DOMAIN"
  NGINX_SITE="/etc/nginx/sites-available/$DOMAIN"
  # Chỉ ghi block HTTP khi site CHƯA có — tránh ghi đè cấu hình SSL certbot đã thêm ở lần chạy trước.
  if [ -f "$NGINX_SITE" ]; then
    ok "nginx site đã có ($NGINX_SITE) — giữ nguyên (gồm cả phần SSL của certbot)."
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

        client_max_body_size 250M;     # upload ảnh/video lớn
        proxy_read_timeout 86400;      # SSE + job chạy lâu
        proxy_buffering off;           # SSE stream realtime
    }
}
NGINX
  fi
  $SUDO ln -sfn "$NGINX_SITE" "/etc/nginx/sites-enabled/$DOMAIN"
  # Gỡ default site của Ubuntu → tránh request Host lạ rơi vào trang "Welcome to nginx!".
  $SUDO rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true
  $SUDO nginx -t >/dev/null 2>&1 && $SUDO systemctl reload nginx && ok "nginx reload OK" || warn "nginx -t lỗi — kiểm tra config."

  # Mở firewall OS (ufw) cho web — CHỈ khi ufw đang bật (tránh tự kích hoạt firewall làm khoá SSH).
  # LƯU Ý: đây chỉ là firewall TRONG máy. Firewall/Security Group tầng NHÀ CUNG CẤP (AWS/DO/Vultr) hoặc
  # port-mapping vast.ai/RunPod phải tự mở 80+443 trên dashboard — script KHÔNG với tới được.
  if command -v ufw >/dev/null 2>&1 && $SUDO ufw status 2>/dev/null | grep -qi "Status: active"; then
    $SUDO ufw allow 80/tcp >/dev/null 2>&1; $SUDO ufw allow 443/tcp >/dev/null 2>&1; $SUDO ufw allow OpenSSH >/dev/null 2>&1
    ok "ufw: đã mở 80/443 (firewall OS)."
  fi
fi

# ════════════════════════════════════════════════════════════════════════════
if [ "${SKIP_HTTPS:-0}" = "1" ]; then
  say "11/12 · Bỏ qua HTTPS (local/IP mode)"
  ok "API truy cập trực tiếp tại $BE_URL — không cấp SSL. (Nhớ mở cổng ${API_PORT:-8080} ở firewall nhà cung cấp.)"
elif [ -n "${CF_API_TOKEN:-}" ]; then
  # ── HTTPS TỰ ĐỘNG 100% qua Cloudflare API: tạo tunnel + ingress :8080 + DNS proxied + chạy cloudflared. ──
  say "11/12 · HTTPS qua Cloudflare Tunnel — TỰ ĐỘNG (API, không cần dashboard)"
  if setup_cloudflare_tunnel_auto; then
    ok "Xong — https://$DOMAIN sẽ chạy sau ~1-2 phút (Cloudflare tự cấp SSL). KHÔNG phải làm gì thêm."
  else
    die "Tự động tunnel CHƯA xong — xem cảnh báo trên (token thiếu quyền / domain chưa thuộc Cloudflare / DNS record xung đột)."
  fi
elif [ -n "${CF_TUNNEL_TOKEN:-}" ]; then
  # ── HTTPS qua Cloudflare Tunnel (token thủ công) — KHÔNG cần mở port 80/443, KHÔNG cần certbot. ──
  say "11/12 · HTTPS qua Cloudflare Tunnel (cloudflared, bán tự động)"
  if ! command -v cloudflared >/dev/null 2>&1; then
    $SUDO curl -fsSL -o /usr/local/bin/cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
      && $SUDO chmod +x /usr/local/bin/cloudflared || warn "Tải cloudflared lỗi."
  fi
  # Cài lại sạch (idempotent): gỡ service cũ nếu có rồi cài bằng token.
  $SUDO cloudflared service uninstall >/dev/null 2>&1 || true
  if $SUDO cloudflared service install "$CF_TUNNEL_TOKEN" >/dev/null 2>&1; then
    $SUDO systemctl enable --now cloudflared >/dev/null 2>&1 || true
    sleep 4
    if systemctl is-active cloudflared >/dev/null 2>&1; then ok "cloudflared tunnel đang chạy (4 kết nối edge)."; else warn "cloudflared chưa active — xem: journalctl -u cloudflared -n 30"; fi
    warn "CÒN BƯỚC PUBLIC HOSTNAME TRÊN DASHBOARD: Zero Trust → tunnel → Public Hostname → Add:"
    warn "    Backend : $DOMAIN   Type=HTTP   URL=localhost:${API_PORT:-8080}   (Path để TRỐNG) → Save."
    if [ -n "${CF_FE_DOMAIN:-}" ]; then
      warn "    Frontend: $CF_FE_DOMAIN   Type=HTTP   URL=localhost:${CF_FE_PORT:-2030}   (Path để TRỐNG) → Save."
    fi
    warn "    (trỏ thẳng service local, KHÔNG qua nginx). Cloudflare tự tạo DNS proxied + SSL free."
    warn "    ⚠ Nếu domain có A record cũ trùng tên → xoá nó để Cloudflare ghi được CNAME tunnel."
  else
    warn "cloudflared service install lỗi — kiểm tra token. Chạy tay: cloudflared service install <token>"
  fi
else
  say "11/12 · HTTPS bằng certbot (Let's Encrypt)"
  CERT_EMAIL="${SUPER_ADMIN:-${GMAIL_USER:-admin@$DOMAIN}}"
  if $SUDO certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$CERT_EMAIL" --redirect >/dev/null 2>&1; then
    ok "Cert cấp xong → https://$DOMAIN"
  else
    warn "certbot CHƯA cấp được cert. Let's Encrypt cần TỪ NGOÀI gọi được http://$DOMAIN (port 80). Kiểm tra:"
    warn "  1) DNS '$DOMAIN' đã trỏ A record về IP PUBLIC của VPS ($IP) chưa (dig $DOMAIN)."
    warn "  2) Port 80+443 mở ở firewall NHÀ CUNG CẤP / security group (KHÔNG chỉ ufw). Máy NAT (vast.ai/RunPod)"
    warn "     phải map 80:80/443:443 lúc tạo instance, HOẶC dùng Cloudflare Tunnel (chạy lại với CF_TUNNEL_TOKEN=...)."
    warn "  Mở xong chạy lại:  sudo certbot --nginx -d $DOMAIN --redirect"
  fi
fi

# ════════════════════════════════════════════════════════════════════════════
say "12/12 · XONG"
echo
printf '\033[1;32m════════ Motion Backend đã chạy (PM2) ════════\033[0m\n'
echo "  Health   : curl $BE_URL/health   (local: http://127.0.0.1:8080/health)"
echo "  Admin    : $SUPER_ADMIN  → FE bấm gửi OTP để đăng nhập"
echo "  PM2      : pm2 ls   ·   pm2 logs api   ·   pm2 restart ecosystem.config.cjs"
[ "$GPU_OK" = "1" ] && echo "  ComfyUI  : pm2 logs comfyui   (native ở $COMFY_DIR)" || echo "  ComfyUI  : KHÔNG cài local — set COMFY_URL trong .env trỏ box GPU rồi 'pm2 restart worker'"
echo
printf '\033[1;36m──────── DÁN VÀO .env CỦA FE (motions) ────────\033[0m\n'
cat <<FEENV
# motion-backend (job motion gọi qua proxy /api/motion/*, không còn qua Supabase)
NUXT_MOTION_API_URL=$BE_URL
NUXT_MOTION_API_KEY=$API_KEY_VAL

# motion-backend public (FE gọi workflows/storage/ai-providers trực tiếp)
NUXT_PUBLIC_MOTION_BACKEND_URL=$BE_URL
FEENV
echo

# Gửi thông tin kết nối cho admin qua email (nếu đã cấu hình Gmail).
if [ -n "$(get_kv GMAIL_USER)" ]; then
  say "    Gửi email thông tin kết nối FE tới ${SUPER_ADMIN}…"
  if send_setup_email >/dev/null 2>&1; then
    ok "Đã gửi email tới $SUPER_ADMIN (kiểm tra hộp thư, kể cả Spam)."
  else
    warn "Gửi email thất bại — kiểm tra GMAIL_USER/GMAIL_APP_PASSWORD (App Password, bật 2FA). Thông tin vẫn in ở trên."
  fi
fi
echo
