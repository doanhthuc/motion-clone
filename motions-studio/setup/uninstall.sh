#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
# uninstall.sh — GỠ TẬN GỐC motion-backend (+ frontend motions) khỏi VPS.
#
#   Đảo ngược TOÀN BỘ những gì setup-pm2.sh / fullstack-setup.sh đã cài:
#     • 7 process PM2  : motions · comfyui · worker · wf-worker · bg-remover · api · minio
#     • Models AI nặng : ComfyUI (+venv) ~47GB · HF cache · viXTTS · OmniVoice
#     • Dữ liệu        : Postgres DB+role `motion` · MinIO data · output/input ComfyUI
#     • Repo           : ~/motion-backend · ~/motions
#     • Hạ tầng riêng  : cloudflared (binary+systemd+tunnel+DNS) · nginx site · minio binary
#
#   ⛔ KHÔNG đụng tới (theo yêu cầu):  Ollama + models của nó  ·  Supabase  ·
#      các daemon dùng CHUNG (postgres-server, nginx, nodejs) — chỉ gỡ DB/site của dự án.
#
#   Cách chạy (TRÊN VPS, trong thư mục motion-backend):
#       ./setup/uninstall.sh --dry-run     # XEM TRƯỚC, không xoá gì (khuyên chạy lần đầu)
#       ./setup/uninstall.sh               # xoá thật (hỏi gõ xác nhận)
#       ./setup/uninstall.sh --yes         # xoá thật, KHÔNG hỏi (dùng cho script)
#
#   Gỡ luôn tunnel + DNS trên Cloudflare (tuỳ chọn — cần token có quyền như lúc setup):
#       CF_API_TOKEN=xxxx DOMAIN=api.datools.info FE_DOMAIN=app.datools.info ./setup/uninstall.sh
#
#   ⚠️  KHÔNG THỂ HOÀN TÁC. Postgres `motion` (user/job/lịch sử) và MinIO (kết quả render)
#       sẽ mất sạch. Models ~47GB phải tải lại nếu muốn dựng lại.
# ════════════════════════════════════════════════════════════════════════════
set -uo pipefail

# Toàn bộ logic gói trong main() để bash NẠP HẾT script vào RAM trước khi chạy →
# xoá chính thư mục repo (chứa file này) giữa chừng vẫn an toàn.
main() {
  cd "$(dirname "$0")/.." 2>/dev/null || true
  ROOT="$(pwd)"

  say()  { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }
  ok()   { printf '\033[1;32m  ✓ %s\033[0m\n' "$*"; }
  warn() { printf '\033[1;33m  ! %s\033[0m\n' "$*"; }
  die()  { printf '\n\033[1;31m  ✗ %s\033[0m\n' "$*"; exit 1; }
  step() { printf '   • %s\n' "$*"; }

  # ── Cờ dòng lệnh ───────────────────────────────────────────────────────────
  DRY=0; FORCE=0
  for a in "$@"; do
    case "$a" in
      --dry-run|-n) DRY=1 ;;
      --yes|-y|--force) FORCE=1 ;;
      -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
      *) warn "Bỏ qua cờ lạ: $a" ;;
    esac
  done

  [ "$(id -u)" -eq 0 ] && SUDO="" || SUDO="sudo"
  HOME_DIR="${HOME:-/home/ubuntu}"
  ENV_FILE="$ROOT/.env"

  # ── Đọc cấu hình từ .env (resolve TRƯỚC khi xoá repo) ──────────────────────
  # get_kv KEY: lấy value, bỏ comment đuôi dòng + nháy bao ngoài.
  get_kv() {
    [ -f "$ENV_FILE" ] || return 0
    sed -n "s/^$1=//p" "$ENV_FILE" | head -1 \
      | sed 's/[[:space:]]*#.*$//; s/[[:space:]]*$//; s/^"//; s/"$//; s/^'\''//; s/'\''$//'
  }

  PG_USER="$(get_kv POSTGRES_USER)"; PG_USER="${PG_USER:-motion}"
  PG_DB="$(get_kv POSTGRES_DB)";     PG_DB="${PG_DB:-motion}"
  COMFY_DIR="$(get_kv COMFY_DIR)";   COMFY_DIR="${COMFY_DIR:-$HOME_DIR/ComfyUI}"
  MINIO_DATA="$(get_kv MINIO_DATA_DIR)"; MINIO_DATA="${MINIO_DATA:-$ROOT/.data}"
  DOMAIN="${DOMAIN:-$(get_kv DOMAIN)}"
  MOTIONS_DIR="${MOTIONS_DIR:-$HOME_DIR/motions}"
  AI_DIR="$HOME_DIR/ai"

  # ── Liệt kê đối tượng sẽ xoá ───────────────────────────────────────────────
  PM2_APPS="motions comfyui worker wf-worker bg-remover api minio"

  # Thư mục models/repo/data (chỉ những cái TỒN TẠI). MinIO data tách riêng nếu nằm ngoài repo.
  DIRS=()
  for d in "$COMFY_DIR" "$AI_DIR/ComfyUI" "$AI_DIR/vixtts" "$AI_DIR/omnivoice" \
           "$AI_DIR/hf-cache" "$MOTIONS_DIR" "$MINIO_DATA"; do
    case "$d" in "$ROOT"|"$ROOT"/) continue ;; esac   # repo xoá riêng ở bước cuối
    [ -e "$d" ] && DIRS+=("$d")
  done

  sizeof() { du -sh "$1" 2>/dev/null | awk '{print $1}'; }

  # ── BẢNG KẾ HOẠCH ──────────────────────────────────────────────────────────
  echo
  printf '\033[1;31m╔══════════════════════════════════════════════════════════════╗\033[0m\n'
  printf '\033[1;31m║   GỠ TẬN GỐC motion-backend + motions  (KHÔNG THỂ HOÀN TÁC)   ║\033[0m\n'
  printf '\033[1;31m╚══════════════════════════════════════════════════════════════╝\033[0m\n'
  echo
  echo "  Host       : $(hostname) ($(whoami))   HOME=$HOME_DIR"
  echo "  Chế độ     : $([ "$DRY" = 1 ] && echo 'DRY-RUN (xem trước, KHÔNG xoá)' || echo 'XOÁ THẬT')"
  echo
  echo "  PM2 sẽ delete : $PM2_APPS"
  echo "  Postgres drop : DB '$PG_DB' + role '$PG_USER'  (mất user/job/lịch sử)"
  echo "  Thư mục xoá   :"
  for d in "${DIRS[@]:-}"; do [ -n "$d" ] && echo "      $(printf '%-42s' "$d") $(sizeof "$d")"; done
  echo "      $(printf '%-42s' "$ROOT (repo)") $(sizeof "$ROOT")"
  echo "  Hạ tầng       : cloudflared (service+binary) · minio binary · nginx site${DOMAIN:+ ($DOMAIN)}"
  echo
  printf '  \033[1;32mGIỮ NGUYÊN\033[0m : Ollama + models · Supabase · postgres-server · nginx · nodejs\n'
  echo

  if [ "$DRY" = 1 ]; then warn "DRY-RUN — không có gì bị xoá. Bỏ --dry-run để chạy thật."; fi

  # ── Xác nhận ───────────────────────────────────────────────────────────────
  if [ "$DRY" != 1 ] && [ "$FORCE" != 1 ]; then
    if [ ! -t 0 ]; then die "Không phải terminal mà thiếu --yes → dừng cho an toàn."; fi
    printf '  Gõ chính xác \033[1;31mXOA TAN GOC\033[0m để xác nhận xoá: '
    read -r reply || true
    [ "$reply" = "XOA TAN GOC" ] || die "Chuỗi xác nhận sai — HUỶ, không xoá gì."
  fi

  # del DIR — xoá thư mục (tôn trọng dry-run)
  del() { step "rm -rf $1"; [ "$DRY" = 1 ] || $SUDO rm -rf "$1"; }
  sh_do() { step "$1"; [ "$DRY" = 1 ] || eval "$2"; }

  # ── 1/8 · PM2 — dừng + xoá 7 process ───────────────────────────────────────
  say "1/8 · PM2 process"
  if command -v pm2 >/dev/null 2>&1; then
    for app in $PM2_APPS; do
      if pm2 describe "$app" >/dev/null 2>&1; then sh_do "pm2 delete $app" "pm2 delete '$app' >/dev/null 2>&1 || true"
      else step "($app không chạy — bỏ qua)"; fi
    done
    sh_do "pm2 save (ghi lại danh sách rỗng)" "pm2 save --force >/dev/null 2>&1 || true"
    # Gỡ PM2 khỏi startup CHỈ KHI không còn app nào khác.
    if [ "$DRY" != 1 ] && [ "$(pm2 jlist 2>/dev/null | tr ',' '\n' | grep -c '"name"')" = 0 ]; then
      sh_do "pm2 unstartup (không còn app PM2 nào)" "$SUDO env PATH=\$PATH pm2 unstartup systemd >/dev/null 2>&1 || true; pm2 kill >/dev/null 2>&1 || true"
    else
      step "(còn app PM2 khác → GIỮ pm2 startup)"
    fi
  else warn "pm2 không có — bỏ qua."; fi

  # ── 2/8 · Service TTS rớt lại (viXTTS :8090 / OmniVoice :8091) + ComfyUI lẻ ─
  say "2/8 · Tiến trình TTS/ComfyUI rớt lại (ngoài PM2)"
  sh_do "kill uvicorn viXTTS/OmniVoice + ComfyUI python lẻ" \
        "pkill -f 'uvicorn service:app' 2>/dev/null; pkill -f '$AI_DIR/vixtts' 2>/dev/null; pkill -f '$AI_DIR/omnivoice' 2>/dev/null; pkill -f 'ComfyUI/main.py' 2>/dev/null; true"

  # ── 3/8 · cloudflared (service + binary + tunnel + DNS) ─────────────────────
  say "3/8 · cloudflared"
  if command -v cloudflared >/dev/null 2>&1 || systemctl list-unit-files 2>/dev/null | grep -q cloudflared; then
    sh_do "systemctl stop/disable cloudflared" "$SUDO systemctl disable --now cloudflared >/dev/null 2>&1 || true"
    sh_do "cloudflared service uninstall (gỡ systemd unit)" "$SUDO cloudflared service uninstall >/dev/null 2>&1 || true"
    del "/usr/local/bin/cloudflared"
    del "$HOME_DIR/.cloudflared"
    del "/etc/cloudflared"
    # Gỡ tunnel + DNS phía Cloudflare (best-effort) nếu có token + domain.
    if [ -n "${CF_API_TOKEN:-}" ] && [ -n "$DOMAIN" ]; then
      cf_cleanup
    else
      warn "Tunnel + DNS trên Cloudflare CHƯA gỡ (cần CF_API_TOKEN+DOMAIN)."
      [ -n "$DOMAIN" ] && warn "  → Dashboard Cloudflare: xoá tunnel 'motion-${DOMAIN//./-}' + DNS record của domain."
    fi
  else step "(cloudflared không cài — bỏ qua)"; fi

  # ── 4/8 · nginx site (chỉ khi deploy kiểu nginx+certbot) ────────────────────
  say "4/8 · nginx site"
  if [ -n "$DOMAIN" ] && [ -e "/etc/nginx/sites-available/$DOMAIN" ]; then
    del "/etc/nginx/sites-available/$DOMAIN"
    del "/etc/nginx/sites-enabled/$DOMAIN"
    sh_do "nginx -t && reload" "$SUDO nginx -t >/dev/null 2>&1 && $SUDO systemctl reload nginx >/dev/null 2>&1 || true"
  else step "(không có nginx site cho dự án — bỏ qua, giữ nguyên nginx)"; fi

  # ── 5/8 · Postgres: drop DB + role 'motion' ────────────────────────────────
  say "5/8 · Postgres (drop DB '$PG_DB' + role '$PG_USER')"
  if command -v psql >/dev/null 2>&1 || $SUDO -u postgres true 2>/dev/null; then
    pg() { ( cd /tmp && $SUDO -u postgres "$@" ); }
    sh_do "DROP DATABASE $PG_DB (ngắt kết nối đang mở)" \
          "pg psql -qc \"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$PG_DB' AND pid<>pg_backend_pid()\" >/dev/null 2>&1; pg psql -qc 'DROP DATABASE IF EXISTS \"$PG_DB\"' >/dev/null 2>&1 || true"
    sh_do "DROP ROLE $PG_USER" "pg psql -qc 'DROP ROLE IF EXISTS \"$PG_USER\"' >/dev/null 2>&1 || true"
  else warn "Không gọi được postgres (sudo -u postgres) — bỏ qua DB."; fi

  # ── 6/8 · MinIO binary ─────────────────────────────────────────────────────
  say "6/8 · MinIO binary"
  if [ -e /usr/local/bin/minio ]; then del "/usr/local/bin/minio"; else step "(minio binary không có — bỏ qua)"; fi

  # ── 7/8 · Models + thư mục nặng + frontend ─────────────────────────────────
  say "7/8 · Models / data / frontend"
  if [ "${#DIRS[@]}" -gt 0 ]; then for d in "${DIRS[@]}"; do del "$d"; done
  else step "(không thấy thư mục models/data nào)"; fi
  # Dọn nốt ~/ai nếu đã rỗng (giữ lại nếu còn thứ khác của bạn trong đó).
  if [ "$DRY" != 1 ] && [ -d "$AI_DIR" ] && [ -z "$(ls -A "$AI_DIR" 2>/dev/null)" ]; then rmdir "$AI_DIR" 2>/dev/null && step "rmdir $AI_DIR (đã rỗng)" || true; fi

  # ── 8/8 · Repo motion-backend (XOÁ CUỐI — chứa chính script này) ────────────
  say "8/8 · Repo motion-backend"
  del "$ROOT"

  # ── XONG ───────────────────────────────────────────────────────────────────
  echo
  if [ "$DRY" = 1 ]; then
    printf '\033[1;33m════════ DRY-RUN xong — CHƯA xoá gì. Bỏ --dry-run để chạy thật. ════════\033[0m\n'
  else
    printf '\033[1;32m════════ ĐÃ GỠ TẬN GỐC motion-backend + motions ════════\033[0m\n'
    echo "  Đã giữ nguyên : Ollama + models · Supabase · postgres-server · nginx · nodejs · PM2 (nếu còn app khác)"
    [ -n "$DOMAIN" ] && [ -z "${CF_API_TOKEN:-}" ] && echo "  Còn sót (xoá tay): tunnel 'motion-${DOMAIN//./-}' + DNS trên dashboard Cloudflare"
    echo "  Kiểm tra      : pm2 ls   ·   ls $HOME_DIR   ·   sudo -u postgres psql -l | grep $PG_DB"
  fi
  echo
}

# ── (hàm phụ) Gỡ tunnel + DNS qua Cloudflare API — best-effort, không fatal ──
cf_cleanup() {
  step "Cloudflare API: xoá DNS + tunnel 'motion-${DOMAIN//./-}'"
  [ "$DRY" = 1 ] && return 0
  local cfapi="https://api.cloudflare.com/client/v4" tname="motion-${DOMAIN//./-}"
  local H1="Authorization: Bearer $CF_API_TOKEN" H2="Content-Type: application/json"
  # Account id
  local acc; acc="$(curl -fsS -H "$H1" "$cfapi/accounts" 2>/dev/null \
    | python3 -c 'import sys,json;r=json.load(sys.stdin).get("result") or [];print(r[0]["id"] if r else "")' 2>/dev/null)"
  [ -z "$acc" ] && { warn "  Không lấy được account — bỏ qua gỡ remote."; return 0; }
  # Xoá DNS record cho cả BE (DOMAIN) lẫn FE (FE_DOMAIN nếu có)
  local host
  for host in "$DOMAIN" "${FE_DOMAIN:-}"; do
    [ -z "$host" ] && continue
    local root="${host#*.}"
    local zid; zid="$(curl -fsS -H "$H1" "$cfapi/zones?name=$root" 2>/dev/null \
      | python3 -c 'import sys,json;r=json.load(sys.stdin).get("result") or [];print(r[0]["id"] if r else "")' 2>/dev/null)"
    [ -z "$zid" ] && { warn "  Không thấy zone cho $host — bỏ qua DNS."; continue; }
    local rid; rid="$(curl -fsS -H "$H1" "$cfapi/zones/$zid/dns_records?name=$host" 2>/dev/null \
      | python3 -c 'import sys,json;r=json.load(sys.stdin).get("result") or [];print(r[0]["id"] if r else "")' 2>/dev/null)"
    [ -n "$rid" ] && curl -fsS -X DELETE -H "$H1" "$cfapi/zones/$zid/dns_records/$rid" >/dev/null 2>&1 \
      && step "  DNS xoá: $host" || warn "  DNS $host không xoá được (xoá tay trên dashboard)."
  done
  # Xoá tunnel theo tên (cleanup connections trước rồi delete)
  local tid; tid="$(curl -fsS -H "$H1" "$cfapi/accounts/$acc/cfd_tunnel?name=$tname&is_deleted=false" 2>/dev/null \
    | python3 -c 'import sys,json;r=json.load(sys.stdin).get("result") or [];print(r[0]["id"] if r else "")' 2>/dev/null)"
  if [ -n "$tid" ]; then
    curl -fsS -X DELETE -H "$H1" "$cfapi/accounts/$acc/cfd_tunnel/$tid/connections" >/dev/null 2>&1 || true
    curl -fsS -X DELETE -H "$H1" "$cfapi/accounts/$acc/cfd_tunnel/$tid" >/dev/null 2>&1 \
      && step "  Tunnel xoá: $tname" || warn "  Tunnel $tname chưa xoá được (còn connection? thử lại sau ~1' hoặc xoá tay)."
  else step "  (không thấy tunnel $tname — có thể đã xoá)"; fi
}

main "$@"
