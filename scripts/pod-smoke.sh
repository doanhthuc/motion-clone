#!/usr/bin/env bash
#
# Prove a freshly-bootstrapped pod actually works — end to end, by numbers.
#
#   make gpu-smoke                                   # readiness checks only
#   SMOKE_REF=a.jpg SMOKE_DRIVER=b.mp4 make gpu-smoke  # + a real motion job
#   SMOKE_REF=a.jpg SMOKE_PRODUCT=c.jpg make gpu-smoke # + a real tryon job
#   SMOKE_PROMPT="a red car" make gpu-smoke            # + a real create-image job
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
#   6 DB backup on the volume   pod-pgdump.sh --check && --verify (restores into a temp DB)
#   7 a real motion job         Wan Animate pipeline runs end to end   (optional)
#   8 a real tryon job          qwen2.5vl:7b auto-detect + bg-remover venv work (optional)
#   9 a real create-image job   Qwen-Image-Edit runs prompt-only        (opt-in)
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
# Layer 7 (mp4) and layers 8-9 (PNG) need different size floors. A real photographic PNG from
# Qwen-Image-Edit compresses far worse than the >100KB mp4 threshold below would suggest is needed,
# but 5 KB is still comfortably above "0 bytes" or a JSON error body saved to the output path by
# mistake, and comfortably below any real image output — so it catches a broken download without
# false-failing on a working one. Measured real outputs (06/08/2026 pod run): tryon 1378 KB,
# create-image 1785 KB — both far above 5000 bytes, so the default stays right; override lets a
# future run with a genuinely tiny expected output turn the floor down without editing the script.
SMOKE_IMAGE_MIN_BYTES="${SMOKE_IMAGE_MIN_BYTES:-5000}"
[ -n "$DOMAIN" ] || { bad "DOMAIN missing from .env"; exit 1; }

SSH_OK=0
if [ -n "$HOST" ] && [ -n "$PORT" ]; then SSH_OK=1; fi
remote() { ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -p "$PORT" "root@$HOST" "$1" 2>/dev/null; }

# ── 1. Tunnel + api process ───────────────────────────────────────────────────
log "1/9 tunnel + api process"
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
log "2/9 API key + Postgres"
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
log "3/9 PM2 processes"
if [ "$SSH_OK" != 1 ]; then
  skip "no GPU_SSH_HOST/GPU_SSH_PORT in .env → skipping every SSH-based check (3,4,5,6)"
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
log "4/9 ComfyUI custom nodes"
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
log "5/9 Network Volume"
if [ "$SSH_OK" != 1 ]; then
  skip "needs SSH"
elif [ -z "$POD_VOLUME" ]; then
  skip "POD_VOLUME not set → models re-download every pod, DB lost every Stop/Start
      See docs/gpu-pod.md#network-volume"
else
  if remote "cd ~/motion-backend && POD_VOLUME='$POD_VOLUME' MODELS_MIN_GB='${MODELS_MIN_GB:-20}' ./setup/pod-volume.sh --check" ; then
    # Deliberately does not claim PGDATA is on the volume: on RunPod it cannot be (MooseFS blocks
    # chown, Postgres refuses), so VOLUME_PGDATA=0 is the normal case. pod-volume.sh --check prints
    # exactly where PGDATA lives; repeating a guess here would be the kind of confidently-wrong
    # green line this whole script exists to replace.
    ok "volume wired: models + caches on the volume, model count not regressed (PGDATA: see above)"
  else
    bad "pod-volume.sh --check failed — read its output above BEFORE re-downloading models"
  fi
fi

# ── Shared job runner (layers 7-9) ────────────────────────────────────────────
# POST /jobs, poll until done/error/cancelled or TIMEOUT_MIN, download the output and check its
# size. Extracted so layers 7, 8 and 9 don't each carry their own ~45-line copy of queue → poll →
# download → size-check that would drift out of sync.
#   $1        output file path to download into (pick the extension for the job's real content type)
#   $2        minimum acceptable download size in bytes
#   $3        what a passing download proves, printed in the final ok() line
#   $4...     curl -F arguments for the POST /jobs body (varies per job type)
run_and_check_job() {
  local out_path="$1" min_bytes="$2" proves="$3"
  shift 3
  # SMOKE_TIMEOUT_MIN<=0 is a nonsensical config (DEADLINE is already in the past before the
  # first poll) — block it here, before POSTing the job, rather than let it burn a real job slot
  # and surface as a confusing instant "timeout" below. This check lives per-layer (not once at
  # the top of the script) because TIMEOUT_MIN only matters to layers 7-9, which share this
  # function; layers 1-6 don't touch it and shouldn't be blocked by a var they never read.
  if [ "$TIMEOUT_MIN" -le 0 ] 2>/dev/null; then
    bad "SMOKE_TIMEOUT_MIN=$TIMEOUT_MIN is meaningless — the deadline would already be in the past
       before the first poll. Set a positive number of minutes (default is 20)."
    return
  fi
  JOB="$(curl -s --max-time 120 -X POST "https://$DOMAIN/jobs" -H "x-api-key: $KEY" "$@")"
  JOB_ID="$(printf '%s' "$JOB" | python3 -c 'import json,sys; print((json.load(sys.stdin) or {}).get("id",""))' 2>/dev/null)"
  if [ -z "$JOB_ID" ]; then
    bad "POST /jobs did not return an id: $(printf '%s' "$JOB" | head -c 300)"
    return
  fi
  ok "job $JOB_ID queued — polling up to ${TIMEOUT_MIN}m"
  DEADLINE=$(( $(date +%s) + TIMEOUT_MIN * 60 ))
  LAST=""
  # ST is only assigned inside the while loop below. With the TIMEOUT_MIN<=0 guard above this
  # can no longer happen via that path, but ST is initialized here anyway as defense in depth —
  # any other way the loop body never runs (clock skew, a future refactor) must not hit
  # `set -u`'s "unbound variable" on the "$ST" reference in the bad() call after the loop.
  # A truthful placeholder, not "": "(still )" reads as a truncated, broken message.
  ST="(never polled)"
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
        SZ="$(curl -s -o "$out_path" -w '%{size_download}' --max-time 300 \
              -H "x-api-key: $KEY" "https://$DOMAIN/jobs/$JOB_ID/download")"
        if [ "${SZ:-0}" -gt "$min_bytes" ] 2>/dev/null; then
          ok "output downloaded: $((SZ / 1024)) KB → $out_path ($proves)"
        else
          bad "output only ${SZ:-0} bytes — job says done but MinIO gave nothing usable"
        fi
        return ;;
      error|cancelled)
        bad "job $ST: $(printf '%s' "$R" | python3 -c 'import json,sys; print((json.load(sys.stdin) or {}).get("error","?"))' 2>/dev/null)"
        return ;;
    esac
  done
  bad "job did not finish within ${TIMEOUT_MIN}m (still $ST) — raise SMOKE_TIMEOUT_MIN or check pm2 logs worker"
}

# ── 6. Database backup on the volume ──────────────────────────────────────────
# Chứng minh: có bản dump trên volume, và nó NẠP LẠI ĐƯỢC. Chỉ kiểm "có file" thì vô
# nghĩa — một file .sql.gz rỗng vẫn là một file. --verify nạp thật vào DB tạm rồi so
# số dòng với .meta, nên nó là bằng chứng chứ không phải dấu vết. Cùng nhóm "rẻ" với
# các lớp 1-5: chạy trước lớp cần GPU đầu tiên (motion, bên dưới).
log "6/9 Sao lưu database trên volume"
if [ "$SSH_OK" != 1 ]; then
  skip "needs SSH"
elif [ -z "$POD_VOLUME" ]; then
  # skip chứ không bad: pod không gắn volume là cấu hình hợp lệ, không phải hỏng hóc.
  skip "bỏ qua — không đặt POD_VOLUME"
elif remote "cd ~/motion-backend && POD_VOLUME='$POD_VOLUME' bash ./setup/pod-pgdump.sh --check && POD_VOLUME='$POD_VOLUME' bash ./setup/pod-pgdump.sh --verify"; then
  ok "có bản dump, và nạp lại được (đã diễn tập vào DB tạm)"
else
  # warn chứ không bad: thiếu backup không làm pod sai chức năng, nhưng phải nói to.
  warn "chưa có bản dump nạp được — chạy 'make gpu-db-dump'"
fi

# ── 7. A real motion job ──────────────────────────────────────────────────────
# The checks above prove each piece works. Only a real job proves they work
# TOGETHER: Postgres → worker claim → ComfyUI loads Wan Animate FROM THE VOLUME →
# DWPose → sampling → VAE decode → MinIO upload → API hands back a URL.
log "7/9 real motion job"
if [ -z "${SMOKE_REF:-}" ] || [ -z "${SMOKE_DRIVER:-}" ]; then
  skip "set SMOKE_REF=<character image> and SMOKE_DRIVER=<driver video> to run one.
      No sample driver video ships with the repo, so this stays opt-in."
elif [ -z "${KEY:-}" ]; then
  skip "no API key (see layer 2)"
else
  # Check-only flags, not reassigning SMOKE_REF/SMOKE_DRIVER to "": layer 8 below reuses
  # SMOKE_REF, and clobbering the shared var here would make its check misreport "not set".
  ref_ok=1; driver_ok=1
  [ -f "$SMOKE_REF" ]    || { bad "SMOKE_REF not found: $SMOKE_REF"; ref_ok=0; }
  [ -f "$SMOKE_DRIVER" ] || { bad "SMOKE_DRIVER not found: $SMOKE_DRIVER"; driver_ok=0; }
  if [ "$ref_ok" = 1 ] && [ "$driver_ok" = 1 ]; then
    # Smallest job that still exercises the whole pipeline: 540p (the real default,
    # see build_wan_workflow) and few frames so it finishes in minutes not hours.
    # 100000 bytes: the mp4 floor this layer always used — a video that thin means MinIO
    # handed back a near-empty file even though the job reported "done".
    run_and_check_job /tmp/smoke-out.mp4 100000 "whole pipeline works" \
      -F "type=motion" \
      -F 'params={"quality":"540p","frames":33,"render_fps":16}' \
      -F "ref=@$SMOKE_REF" \
      -F "motion=@$SMOKE_DRIVER"
  fi
fi

# ── 8. A real tryon job ───────────────────────────────────────────────────────
# Proves qwen2.5vl:7b (just downloaded onto the volume) and the bg-remover venv baked into the
# image both actually run — the only path that exercises either one. SETUP_PROFILE=full added
# them without any layer above checking either.
log "8/9 real tryon job"
if [ -z "${SMOKE_REF:-}" ] || [ -z "${SMOKE_PRODUCT:-}" ]; then
  skip "set SMOKE_REF=<person image> (reused from layer 7) and SMOKE_PRODUCT=<garment image> to run one."
elif [ -z "${KEY:-}" ]; then
  skip "no API key (see layer 2)"
else
  ref_ok=1; product_ok=1
  [ -f "$SMOKE_REF" ]     || { bad "SMOKE_REF not found: $SMOKE_REF"; ref_ok=0; }
  [ -f "$SMOKE_PRODUCT" ] || { bad "SMOKE_PRODUCT not found: $SMOKE_PRODUCT"; product_ok=0; }
  if [ "$ref_ok" = 1 ] && [ "$product_ok" = 1 ]; then
    # No garment_type/garmentType in params, on purpose: leaving it unset routes through the
    # Auto garment-detection path, which is what actually calls qwen2.5vl:7b. Hardcoding a
    # garment type would keep this layer green without ever exercising the model that just
    # got downloaded — exactly the "silent success" this whole script exists to catch.
    run_and_check_job /tmp/smoke-out-tryon.png "$SMOKE_IMAGE_MIN_BYTES" \
      "tryon + qwen2.5vl auto-detect + bg-remover all ran" \
      -F "type=tryon" \
      -F "model=@$SMOKE_REF" \
      -F "product=@$SMOKE_PRODUCT"
  fi
fi

# ── 9. A real create-image job ────────────────────────────────────────────────
# Proves the Qwen-Image-Edit path runs prompt-only (no files), the other half of what
# SETUP_PROFILE=full added. Opt-in only (SMOKE_PROMPT) so plain `make gpu-smoke` keeps its
# existing contract of "readiness checks only, never spends GPU time".
log "9/9 real create-image job"
if [ -z "${SMOKE_PROMPT:-}" ]; then
  skip "set SMOKE_PROMPT=<English prompt> to run one. Opt-in only: this is the one layer that
      make gpu-smoke must NOT run by default, so it needs its own trigger var."
elif [ -z "${KEY:-}" ]; then
  skip "no API key (see layer 2)"
else
  # No files: run_create_image only requires an image when params.domain=architecture, and this
  # layer never sets that. json.dumps quotes the prompt safely for the multipart params field.
  PARAMS_JSON="$(python3 -c 'import json,sys; print(json.dumps({"prompt": sys.argv[1]}))' "$SMOKE_PROMPT")"
  run_and_check_job /tmp/smoke-out-create.png "$SMOKE_IMAGE_MIN_BYTES" \
    "create-image / Qwen-Image-Edit ran" \
    -F "type=create-image" \
    -F "params=$PARAMS_JSON"
fi

# ── Verdict ───────────────────────────────────────────────────────────────────
printf '\n'
if [ "$FAILED" -eq 0 ]; then
  ok "smoke test passed"
  exit 0
fi
printf '\033[31m ✗ \033[0m%s check(s) failed — see above\n' "$FAILED"
exit 1
