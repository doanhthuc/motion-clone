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

# ── Frontend on the pod (optional) ────────────────────────────────────────────
# FE_DOMAIN set → setup-pm2.sh adds a SECOND public hostname to the same Cloudflare Tunnel
# (CF_FE_DOMAIN → localhost:FE_PORT, plus its DNS record), and scripts/pod-fe.sh deploys the
# Nuxt app behind it at the end. End-users then open one HTTPS link and your laptop can be off.
# Leave it empty to keep the old shape: backend on the pod, frontend local via `make dev`.
FE_DOMAIN="$(env_get FE_DOMAIN)"
FE_PORT="$(env_get FE_PORT)"; FE_PORT="${FE_PORT:-2030}"
if [ -n "$FE_DOMAIN" ]; then
  [ -n "$CF_API_TOKEN" ] || warn "FE_DOMAIN set but only CF_TUNNEL_TOKEN available — an existing tunnel's second Public Hostname ($FE_DOMAIN → localhost:$FE_PORT) has to be added by hand on the Cloudflare dashboard."
  case ",$CORS_ORIGINS," in
    *",https://$FE_DOMAIN,"*) ;;
    *) warn "CORS_ORIGINS does not list https://$FE_DOMAIN — the frontend will load and every API call will fail CORS. Fix .env, then re-run." ;;
  esac
fi

# ── Network Volume + prebuilt image (both optional, both big time savers) ──────
# POD_VOLUME: mount path of a RunPod Network Volume on the pod. When set, models /
#   PGDATA / MinIO get symlinked onto it, so re-creating a pod does NOT re-download
#   the ~33GB model set and does NOT lose the database. See docs/gpu-pod.md#volume.
# MTC_PREBUILT=1: the pod image already ships /opt/mtc-prebuilt (ComfyUI + venv +
#   api node_modules). Skips ~20-35 min of installing. Requires the image built from
#   motions-studio/worker-image/Dockerfile — the setup script dies if .ready is absent.
POD_VOLUME="$(env_get POD_VOLUME)"
MTC_PREBUILT="$(env_get MTC_PREBUILT)"
MODELS_MIN_GB="$(env_get MODELS_MIN_GB)"

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

# ── Wire the Network Volume BEFORE setup runs ─────────────────────────────────
# Order matters. pod-volume.sh installs Postgres and points data_directory at the
# volume, so by the time setup-motion-transfer.sh reaches its Postgres phase its
# own _pg_up() already returns true and it skips starting a second cluster on the
# container disk. Same for models: the symlink must exist before setup creates
# $COMFY_DIR/models/uploads, otherwise those land on the container disk.
if [ -n "$POD_VOLUME" ]; then
  # VOLUME_PGDATA defaults to 0 because of how RunPod mounts a Network Volume: MooseFS with
  # user_id=0,group_id=0 and chown blocked even for root. Postgres refuses to start unless PGDATA
  # is owned by the postgres user at mode 0700, so putting PGDATA there does not merely degrade —
  # rsync dies at the first chown and nothing gets installed at all. Measured on a live pod
  # 2026-08-01. Models and MinIO are unaffected: both run as root and only need write access,
  # so the ~33GB no-re-download win is kept. The cost is that the database now lives on the
  # container disk: it survives gpu-down/gpu-up, and is lost on gpu-destroy.
  # Set VOLUME_PGDATA=1 in .env on a provider whose volume honours chown.
  VOLUME_PGDATA="$(env_get VOLUME_PGDATA)"; VOLUME_PGDATA="${VOLUME_PGDATA:-0}"
  log "wiring Network Volume $POD_VOLUME (models · MinIO$([ "$VOLUME_PGDATA" = 1 ] && echo ' · PGDATA')) — no 33GB re-download"
  [ "$VOLUME_PGDATA" = 1 ] || warn "PGDATA stays on the container disk — DB survives gpu-down/up, lost on gpu-destroy"
  ssh "${SSH_OPTS[@]}" "root@$HOST" "cd ~/$REMOTE_DIR && chmod +x setup/*.sh && \
POD_VOLUME='$POD_VOLUME' MTC_PREBUILT='${MTC_PREBUILT:-0}' MODELS_MIN_GB='${MODELS_MIN_GB:-20}' \
VOLUME_PGDATA='$VOLUME_PGDATA' \
./setup/pod-volume.sh" < /dev/null || {
    warn "pod-volume.sh exited non-zero."
    warn "If it said a directory is a REAL dir with data (first run on a pod that already"
    warn "downloaded models), adopt them onto the volume once:"
    warn "  ssh -p $PORT root@$HOST 'cd ~/$REMOTE_DIR && POD_VOLUME=$POD_VOLUME ./setup/pod-volume.sh --adopt'"
    die "aborting before setup — fix the volume first, nothing has been installed yet"
  }
else
  warn "POD_VOLUME not set in .env → models re-download (~33GB, in-app) and the database is"
  warn "lost every time the pod is re-created. See docs/gpu-pod.md#volume to set it up."
fi

log "running setup/setup-motion-transfer.sh on the pod — installs Postgres/MinIO/PM2, detects the"
log "GPU/driver, installs ComfyUI + matching PyTorch/CUDA, and wires up the Cloudflare Tunnel. First"
log "run takes a while (no models downloaded yet — that's a separate manual step, see below)."

TS="$(env_get GPU_INSTANCE_ID)"; TS="${TS:-run}"
LOG="/tmp/motion-clone-bootstrap-${TS}.log"

# stdin redirected from /dev/null: every OPTIONAL prompt in setup-motion-transfer.sh is a single
# `read` that returns empty on EOF and moves on. SUPER_ADMIN must be exported (it's a `while`
# loop that would otherwise spin forever reading empty lines).
# CF_FE_DOMAIN/CF_FE_PORT are what turn one tunnel into two public hostnames — setup-pm2.sh
# already handles them (ingress rule, DNS CNAME, and its token preflight covers both zones).
# FRONTEND_URL only changes the "it's ready" email into a single clickable link.
ssh "${SSH_OPTS[@]}" "root@$HOST" "cd ~/$REMOTE_DIR && chmod +x setup/*.sh && \
DOMAIN='$DOMAIN' SUPER_ADMIN='$SUPER_ADMIN' GMAIL_USER='$GMAIL_USER' \
GMAIL_APP_PASSWORD='$GMAIL_APP_PASSWORD' CF_API_TOKEN='$CF_API_TOKEN' \
CF_TUNNEL_TOKEN='$CF_TUNNEL_TOKEN' CORS_ORIGINS='$CORS_ORIGINS' HF_TOKEN='' \
${FE_DOMAIN:+CF_FE_DOMAIN='$FE_DOMAIN' CF_FE_PORT='$FE_PORT' FRONTEND_URL='https://$FE_DOMAIN'} \
MTC_PREBUILT='${MTC_PREBUILT:-0}' \
./setup/setup-motion-transfer.sh" < /dev/null | tee "$LOG"
STATUS=${PIPESTATUS[0]}
[ "$STATUS" -eq 0 ] || die "setup-motion-transfer.sh exited $STATUS — full log: $LOG"

# ── MinIO must point AT the volume, not at a symlink to it ────────────────────
# pod-volume.sh wires storage by symlinking .data/minio → $POD_VOLUME/minio, which works for every
# other consumer and fails for exactly one: MinIO refuses a symlinked drive and dies on boot with
#   FATAL Unable to initialize backend ... HINT: Drives are not directories
# then PM2 restarts it forever. Seen on a live pod 2026-08-01 (37 restarts before it was noticed).
# `mount --bind` would fix the symlink but the container denies it (permission denied).
# ecosystem.config.cjs:58 reads MINIO_DATA_DIR, so pointing MinIO straight at the volume path
# sidesteps the symlink entirely. Done after setup because setup rewrites .env.
if [ -n "$POD_VOLUME" ]; then
  log "pointing MinIO at $POD_VOLUME/minio directly (it rejects symlinked drives)"
  remote "cd ~/$REMOTE_DIR && \
    { rmdir .data/minio 2>/dev/null || rm -f .data/minio; } ; \
    grep -q '^MINIO_DATA_DIR=' .env \
      && sed -i 's#^MINIO_DATA_DIR=.*#MINIO_DATA_DIR=$POD_VOLUME/minio#' .env \
      || echo 'MINIO_DATA_DIR=$POD_VOLUME/minio' >> .env ; \
    pm2 restart minio --update-env >/dev/null 2>&1 ; sleep 6 ; \
    pm2 jlist | python3 -c \"import sys,json;m=[p for p in json.load(sys.stdin) if p['name']=='minio'];print('minio', m[0]['pm2_env']['status'] if m else 'MISSING')\"" \
    || warn "could not repoint MinIO — check 'pm2 logs minio'"
fi

# ── Gate: prove the volume is actually in use ─────────────────────────────────
# The failure mode this catches is SILENT SUCCESS: the box comes up green, /health
# answers, you log in fine — but models/ is a real empty dir on the container disk
# and the app will happily re-download 33GB. Numbers, not vibes.
if [ -n "$POD_VOLUME" ]; then
  log "verifying the volume is really wired (models on volume · PGDATA on volume)…"
  ssh "${SSH_OPTS[@]}" "root@$HOST" "cd ~/$REMOTE_DIR && \
POD_VOLUME='$POD_VOLUME' MODELS_MIN_GB='${MODELS_MIN_GB:-20}' ./setup/pod-volume.sh --check" \
    < /dev/null || warn "volume check failed — read the output above BEFORE downloading models again"
fi

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

# ── Frontend on the pod ───────────────────────────────────────────────────────
# Last, deliberately: the tunnel ingress it depends on was just created above, and it reads the
# backend's API_KEY off the pod's own .env, which only exists once setup has finished.
if [ -n "$FE_DOMAIN" ]; then
  echo
  log "deploying the frontend onto the pod (rsync + build + PM2)…"
  bash scripts/pod-fe.sh || die "frontend deploy failed — the backend is up, re-run just the frontend with: make gpu-fe"
  APP_URL="https://$FE_DOMAIN"
else
  APP_URL="http://localhost:2030"
  echo "  restart the FE dev server to pick it up:   make down && make dev"
fi

echo "  then load the model group (NOT downloaded by setup, ~33GB):"
echo "    $APP_URL → login as $SUPER_ADMIN → Settings → Models AI → 'Wan 2.2 Animate' → Cài cả nhóm"
echo
