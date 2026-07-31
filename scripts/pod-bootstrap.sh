#!/usr/bin/env bash
#
# rsync motions-studio/ onto the pod and run its OWN setup/setup-motion-transfer.sh there —
# idempotent (safe to re-run after an interruption, same guarantee the script itself documents).
# Then pulls the FE .env block the script prints at the end and writes it straight into
# motions/.env, so the local frontend points at the freshly-deployed backend.
#
#   make gpu-bootstrap
#
set -uo pipefail

log()  { printf '\033[36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33m !!\033[0m %s\n' "$*"; }
die()  { printf '\033[31m ✗ \033[0m%s\n' "$*" >&2; exit 1; }

env_get() { grep -E "^$1=" .env 2>/dev/null | cut -d= -f2- | sed -E 's/[[:space:]]*#.*$//' | tr -d '"'; }

HOST="$(env_get GPU_SSH_HOST)"
PORT="$(env_get GPU_SSH_PORT)"
[ -n "$HOST" ] && [ -n "$PORT" ] || die "GPU_SSH_HOST/GPU_SSH_PORT missing from .env — run: make gpu-wait (or fill by hand for RunPod)"

DOMAIN="$(env_get DOMAIN)"
SUPER_ADMIN="$(env_get SUPER_ADMIN)"
[ -n "$DOMAIN" ] && [ -n "$SUPER_ADMIN" ] || die "DOMAIN/SUPER_ADMIN missing from .env — run: make gpu-preflight"
GMAIL_USER="$(env_get GMAIL_USER)"
GMAIL_APP_PASSWORD="$(env_get GMAIL_APP_PASSWORD)"
CF_API_TOKEN="$(env_get CF_API_TOKEN)"
CF_TUNNEL_TOKEN="$(env_get CF_TUNNEL_TOKEN)"
CORS_ORIGINS="$(env_get CORS_ORIGINS)"
[ -n "$CF_API_TOKEN" ] || [ -n "$CF_TUNNEL_TOKEN" ] || die "CF_API_TOKEN or CF_TUNNEL_TOKEN missing from .env — run: make gpu-preflight"

SSH_OPTS=(-o StrictHostKeyChecking=accept-new -p "$PORT")
REMOTE_DIR="motion-backend"

remote() { ssh "${SSH_OPTS[@]}" "root@$HOST" "$1"; }

log "syncing motions-studio/ → root@$HOST:$PORT:~/$REMOTE_DIR (code only — models download later, in-app)"
remote "mkdir -p ~/$REMOTE_DIR"
rsync -az --delete \
  --exclude='.git' --exclude='node_modules' --exclude='.venv' --exclude='__pycache__' \
  --exclude='.env' --exclude='.env.*' --exclude='.data' --exclude='data' --exclude='*.mp4' \
  --exclude='ltx-ss-prebuilt/*.safetensors' \
  -e "ssh ${SSH_OPTS[*]}" \
  motions-studio/ "root@$HOST:~/$REMOTE_DIR/" \
  || die "rsync failed"

log "running setup/setup-motion-transfer.sh on the pod — installs Postgres/MinIO/PM2, detects the"
log "GPU/driver, installs ComfyUI + matching PyTorch/CUDA, and wires up the Cloudflare Tunnel. First"
log "run takes a while (no models downloaded yet — that's a separate manual step, see below)."

TS="$(env_get GPU_INSTANCE_ID)"; TS="${TS:-run}"
LOG="/tmp/motion-clone-bootstrap-${TS}.log"

# stdin redirected from /dev/null: every OPTIONAL prompt in setup-motion-transfer.sh is a single
# `read` that returns empty on EOF and moves on. SUPER_ADMIN must be exported (it's a `while`
# loop that would otherwise spin forever reading empty lines).
ssh "${SSH_OPTS[@]}" "root@$HOST" "cd ~/$REMOTE_DIR && chmod +x setup/*.sh && \
DOMAIN='$DOMAIN' SUPER_ADMIN='$SUPER_ADMIN' GMAIL_USER='$GMAIL_USER' \
GMAIL_APP_PASSWORD='$GMAIL_APP_PASSWORD' CF_API_TOKEN='$CF_API_TOKEN' \
CF_TUNNEL_TOKEN='$CF_TUNNEL_TOKEN' CORS_ORIGINS='$CORS_ORIGINS' HF_TOKEN='' \
./setup/setup-motion-transfer.sh" < /dev/null | tee "$LOG"
STATUS=${PIPESTATUS[0]}
[ "$STATUS" -eq 0 ] || die "setup-motion-transfer.sh exited $STATUS — full log: $LOG"

log "checking the Cloudflare Tunnel actually came up…"
if ! curl -sf "https://$DOMAIN/health" >/dev/null 2>&1; then
  warn "https://$DOMAIN/health not answering — trying the no-systemd cloudflared fallback"
  warn "(many vast.ai/RunPod containers have no systemd, so 'cloudflared service install' writes"
  warn "the unit file but nothing ever starts it — this is a best-effort workaround, not guaranteed)."
  remote "pgrep -f 'cloudflared tunnel' >/dev/null 2>&1 || { \
    TOK=\$(grep -oE -- '--token[= ][^ ]+' /etc/systemd/system/cloudflared.service 2>/dev/null | head -1 | sed -E 's/--token[= ]//'); \
    if [ -n \"\$TOK\" ]; then nohup cloudflared tunnel run --token \"\$TOK\" >/tmp/cloudflared.log 2>&1 & disown; fi; \
  }"
  sleep 8
  if curl -sf "https://$DOMAIN/health" >/dev/null 2>&1; then
    log "tunnel up via the manual fallback."
  else
    warn "still not answering — SSH in and check:"
    warn "  ssh -p $PORT root@$HOST 'cat /etc/systemd/system/cloudflared.service; cat /tmp/cloudflared.log'"
  fi
fi

log "pulling the FE .env block out of the setup log…"
FE_BLOCK="$(grep -E '^(NUXT_MOTION_API_URL|NUXT_MOTION_API_KEY|NUXT_PUBLIC_MOTION_BACKEND_URL)=' "$LOG")"
[ -n "$FE_BLOCK" ] || die "couldn't find the FE .env block in $LOG — setup may have failed partway; check the log"

FE_ENV="motions/.env"
[ -f "$FE_ENV" ] || cp motions/.env.example "$FE_ENV"
while IFS='=' read -r key val; do
  [ -n "$key" ] || continue
  if grep -qE "^$key=" "$FE_ENV" 2>/dev/null; then
    sed -i.bak -E "s#^$key=.*#$key=$val#" "$FE_ENV" && rm -f "$FE_ENV.bak"
  else
    printf '%s=%s\n' "$key" "$val" >> "$FE_ENV"
  fi
done <<< "$FE_BLOCK"

echo
log "done. motions/.env now points at https://$DOMAIN"
echo
echo "$FE_BLOCK"
echo

DASHBOARD_STEP="$(grep -A1 'CÒN 1 BƯỚC DASHBOARD' "$LOG" || true)"
if [ -n "$DASHBOARD_STEP" ]; then
  warn "CF_TUNNEL_TOKEN path — one manual Cloudflare step still needed before $DOMAIN answers:"
  echo "$DASHBOARD_STEP"
  echo
fi

echo "  restart the FE dev server to pick it up:   make down && make dev"
echo "  then load the model group (NOT downloaded by setup, ~33GB):"
echo "    http://localhost:2030 → login as $SUPER_ADMIN → Settings → Models AI → 'Wan 2.2 Animate' → Cài cả nhóm"
echo
