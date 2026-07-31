#!/usr/bin/env bash
#
# Prove a freshly-bootstrapped pod actually works — end to end, by numbers.
#
#   make gpu-smoke                                   # readiness checks only
#   SMOKE_REF=a.jpg SMOKE_DRIVER=b.mp4 make gpu-smoke  # + a real motion job
#
# WHY NOT JUST `make gpu-status`
#   /health is a static JSON handler (api/src/server.js:55) — it answers 200 even
#   when Postgres is unreachable, ComfyUI never loaded its custom nodes, or models/
#   is an empty dir on the container disk. The failure mode that costs you money is
#   SILENT SUCCESS: everything green, and the app quietly re-downloads 33GB.
#
# Layers, cheapest first. Each prints what it PROVES, not just OK/FAIL.
#   1 tunnel + api process      https://$DOMAIN/health
#   2 auth + Postgres           GET /jobs?limit=1  (hits the DB, unlike /health)
#   3 PM2 processes             every app online, none in restart-loop
#   4 ComfyUI custom nodes      /object_info/WanVideoModelLoader
#   5 volume really in use      setup/pod-volume.sh --check
#   6 a real motion job         POST /jobs → poll → download output   (optional)
#
set -uo pipefail
cd "$(dirname "$0")/.."; ROOT="$(pwd)"

log()  { printf '\n\033[36m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[32m  ✓\033[0m %s\n' "$*"; }
skip() { printf '\033[90m  ~\033[0m %s\n' "$*"; }
warn() { printf '\033[33m !!\033[0m %s\n' "$*"; }
bad()  { printf '\033[31m ✗ \033[0m%s\n' "$*"; FAILED=$((FAILED + 1)); }

FAILED=0
env_get() { grep -E "^$1=" "$ROOT/.env" 2>/dev/null | cut -d= -f2- | sed -E 's/[[:space:]]*#.*$//' | tr -d '"'; }

DOMAIN="$(env_get DOMAIN)"
HOST="$(env_get GPU_SSH_HOST)"; PORT="$(env_get GPU_SSH_PORT)"
POD_VOLUME="$(env_get POD_VOLUME)"
MODELS_MIN_GB="$(env_get MODELS_MIN_GB)"
TIMEOUT_MIN="${SMOKE_TIMEOUT_MIN:-20}"
[ -n "$DOMAIN" ] || { bad "DOMAIN missing from .env"; exit 1; }

SSH_OK=0
if [ -n "$HOST" ] && [ -n "$PORT" ]; then SSH_OK=1; fi
remote() { ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -p "$PORT" "root@$HOST" "$1" 2>/dev/null; }

# ── 1. Tunnel + api process ───────────────────────────────────────────────────
log "1/6 tunnel + api process"
if curl -sf --max-time 15 "https://$DOMAIN/health" >/dev/null 2>&1; then
  ok "https://$DOMAIN/health answers → Cloudflare Tunnel up, api process alive"
else
  bad "https://$DOMAIN/health not answering"
  warn "    Container without systemd? 'cloudflared service install' writes the unit but"
  warn "    nothing starts it. pod-bootstrap.sh has a fallback; check /tmp/cloudflared.log"
  exit 1
fi

# ── 2. Auth + Postgres ────────────────────────────────────────────────────────
# GET /jobs runs a real query, so a 200 proves api → Postgres → auth all at once.
log "2/6 API key + Postgres"
KEY="$(grep -E '^NUXT_MOTION_API_KEY=' "$ROOT/motions/.env" 2>/dev/null | cut -d= -f2- | tr -d '"')"
if [ -z "$KEY" ] && [ "$SSH_OK" = 1 ]; then
  KEY="$(remote "grep -E '^API_KEY=' ~/motion-backend/.env | cut -d= -f2-" | tr -d '\r\n')"
  [ -n "$KEY" ] && warn "API key read from the pod (motions/.env has none — run make gpu-bootstrap?)"
fi
if [ -z "$KEY" ]; then
  bad "no API key found (motions/.env NUXT_MOTION_API_KEY empty, and no SSH to read the pod's .env)"
else
  CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 \
          -H "x-api-key: $KEY" "https://$DOMAIN/jobs?limit=1")"
  case "$CODE" in
    200) ok "GET /jobs → 200 → api + Postgres + API key all good" ;;
    401) bad "GET /jobs → 401: key mismatch. motions/.env NUXT_MOTION_API_KEY ≠ pod .env API_KEY.
       Fix: make gpu-bootstrap re-pastes it, or copy it across by hand." ;;
    5*)  bad "GET /jobs → $CODE: api is up but the query failed — almost always Postgres.
       Check: ssh -p $PORT root@$HOST 'pm2 logs api --lines 40 --nostream'" ;;
    *)   bad "GET /jobs → $CODE (unexpected)" ;;
  esac
fi

# ── 3. PM2 processes ──────────────────────────────────────────────────────────
log "3/6 PM2 processes"
if [ "$SSH_OK" != 1 ]; then
  skip "no GPU_SSH_HOST/GPU_SSH_PORT in .env → skipping every SSH-based check (3,4,5)"
else
  PM2="$(remote "pm2 jlist 2>/dev/null")"
  if [ -z "$PM2" ]; then
    bad "cannot read pm2 jlist over SSH"
  else
    # Process substitution, NOT a pipe into `while`: a piped while runs in a subshell,
    # so every FAILED++ inside it evaporates and the final verdict says "passed" while
    # comfyui is down. Verified: N=0 with a pipe, N=2 with < <(...).
    while IFS=$'\t' read -r verdict name extra; do
      case "$verdict" in
        OK)         ok "pm2 $name online" ;;
        DOWN)       bad "pm2 $name is '$extra' — pm2 logs $name" ;;
        MISSING)    bad "pm2 $name not registered — setup did not start it" ;;
        FLAPPING)   bad "pm2 $name crash-looping ($extra) — pm2 logs $name" ;;
        PARSE_FAIL) bad "pm2 jlist unparseable" ;;
      esac
    done < <(printf '%s' "$PM2" | python3 -c '
import json, sys
try:
    apps = json.load(sys.stdin)
except Exception:
    print("PARSE_FAIL"); sys.exit(0)
want = {"api", "worker", "wf-worker", "comfyui", "minio"}
seen = {}
for a in apps:
    pm = a.get("pm2_env") or {}
    seen[a.get("name")] = (pm.get("status"), pm.get("restart_time", 0))
for n in sorted(want | set(seen)):
    st, rs = seen.get(n, (None, 0))
    if st is None:
        print("MISSING\t%s" % n)
    elif st != "online":
        print("DOWN\t%s\t%s" % (n, st))
    elif rs and rs > 10:
        print("FLAPPING\t%s\t%s restarts" % (n, rs))
    else:
        print("OK\t%s" % n)
')
  fi
fi

# ── 4. ComfyUI custom nodes ───────────────────────────────────────────────────
# ComfyUI answering /system_stats only proves it is breathing. The workflow dies
# with HTTP 400 "node type not found" if the custom nodes did not load, so check a
# node the motion workflow actually uses — same mechanism as _comfy_has_node().
log "4/6 ComfyUI custom nodes"
if [ "$SSH_OK" != 1 ]; then
  skip "needs SSH"
else
  for node in WanVideoModelLoader WanVideoAnimateEmbeds; do
    if remote "curl -sf --max-time 10 http://127.0.0.1:8188/object_info/$node" | grep -q "$node"; then
      ok "$node loaded"
    else
      bad "$node NOT loaded — motion jobs will fail with 400 'node type not found'
       Check: ssh -p $PORT root@$HOST 'pm2 logs comfyui --lines 60 --nostream'"
    fi
  done
fi

# ── 5. Volume really in use ───────────────────────────────────────────────────
log "5/6 Network Volume"
if [ "$SSH_OK" != 1 ]; then
  skip "needs SSH"
elif [ -z "$POD_VOLUME" ]; then
  skip "POD_VOLUME not set → models re-download every pod, DB lost every Stop/Start
      See docs/gpu-pod.md#network-volume"
else
  if remote "cd ~/motion-backend && POD_VOLUME='$POD_VOLUME' MODELS_MIN_GB='${MODELS_MIN_GB:-20}' ./setup/pod-volume.sh --check" ; then
    ok "volume wired: symlinks correct, PGDATA on volume, model count not regressed"
  else
    bad "pod-volume.sh --check failed — read its output above BEFORE re-downloading models"
  fi
fi

# ── 6. A real motion job ──────────────────────────────────────────────────────
# The five checks above prove each piece works. Only a real job proves they work
# TOGETHER: Postgres → worker claim → ComfyUI loads Wan Animate FROM THE VOLUME →
# DWPose → sampling → VAE decode → MinIO upload → API hands back a URL.
log "6/6 real motion job"
if [ -z "${SMOKE_REF:-}" ] || [ -z "${SMOKE_DRIVER:-}" ]; then
  skip "set SMOKE_REF=<character image> and SMOKE_DRIVER=<driver video> to run one.
      No sample driver video ships with the repo, so this stays opt-in."
elif [ -z "${KEY:-}" ]; then
  skip "no API key (see layer 2)"
else
  [ -f "$SMOKE_REF" ]    || { bad "SMOKE_REF not found: $SMOKE_REF"; SMOKE_REF=""; }
  [ -f "$SMOKE_DRIVER" ] || { bad "SMOKE_DRIVER not found: $SMOKE_DRIVER"; SMOKE_DRIVER=""; }
  if [ -n "$SMOKE_REF" ] && [ -n "$SMOKE_DRIVER" ]; then
    # Smallest job that still exercises the whole pipeline: 540p (the real default,
    # see build_wan_workflow) and few frames so it finishes in minutes not hours.
    JOB="$(curl -s --max-time 120 -X POST "https://$DOMAIN/jobs" \
      -H "x-api-key: $KEY" \
      -F "type=motion" \
      -F 'params={"quality":"540p","frames":33,"render_fps":16}' \
      -F "ref=@$SMOKE_REF" \
      -F "motion=@$SMOKE_DRIVER")"
    JOB_ID="$(printf '%s' "$JOB" | python3 -c 'import json,sys; print((json.load(sys.stdin) or {}).get("id",""))' 2>/dev/null)"
    if [ -z "$JOB_ID" ]; then
      bad "POST /jobs did not return an id: $(printf '%s' "$JOB" | head -c 300)"
    else
      ok "job $JOB_ID queued — polling up to ${TIMEOUT_MIN}m"
      DEADLINE=$(( $(date +%s) + TIMEOUT_MIN * 60 ))
      LAST=""
      while [ "$(date +%s)" -lt "$DEADLINE" ]; do
        sleep 10
        R="$(curl -s --max-time 20 -H "x-api-key: $KEY" "https://$DOMAIN/jobs/$JOB_ID")"
        read -r ST PR STEP <<EOF2
$(printf '%s' "$R" | python3 -c '
import json,sys
try: d=json.load(sys.stdin) or {}
except Exception: d={}
print(d.get("status","?"), round((d.get("progress") or 0)*100), (d.get("current_step") or "-").replace(" ","_"))' 2>/dev/null)
EOF2
        [ "$ST$PR" != "$LAST" ] && { printf '     %s %s%% %s\n' "$ST" "$PR" "$STEP"; LAST="$ST$PR"; }
        case "$ST" in
          done)
            ok "job finished"
            SZ="$(curl -s -o /tmp/smoke-out.mp4 -w '%{size_download}' --max-time 300 \
                  -H "x-api-key: $KEY" "https://$DOMAIN/jobs/$JOB_ID/download")"
            if [ "${SZ:-0}" -gt 100000 ] 2>/dev/null; then
              ok "output downloaded: $((SZ / 1024)) KB → /tmp/smoke-out.mp4 (whole pipeline works)"
            else
              bad "output only ${SZ:-0} bytes — job says done but MinIO gave nothing usable"
            fi
            break ;;
          error|cancelled)
            bad "job $ST: $(printf '%s' "$R" | python3 -c 'import json,sys; print((json.load(sys.stdin) or {}).get("error","?"))' 2>/dev/null)"
            break ;;
        esac
      done
      [ "$(date +%s)" -ge "$DEADLINE" ] && bad "job did not finish within ${TIMEOUT_MIN}m (still $ST) — raise SMOKE_TIMEOUT_MIN or check pm2 logs worker"
    fi
  fi
fi

# ── Verdict ───────────────────────────────────────────────────────────────────
printf '\n'
if [ "$FAILED" -eq 0 ]; then
  ok "smoke test passed"
  exit 0
fi
printf '\033[31m ✗ \033[0m%s check(s) failed — see above\n' "$FAILED"
exit 1
