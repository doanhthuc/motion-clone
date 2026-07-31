#!/usr/bin/env bash
#
# Wait for a freshly rented pod to accept SSH, and give up if it does not. Auto-fills
# GPU_SSH_HOST / GPU_SSH_PORT into .env once Vast assigns them, so you never copy-paste them
# from the dashboard by hand.
#
#   make gpu-wait            # default: 25 minutes
#   TIMEOUT=40 make gpu-wait
#
# On the timeout, and why it is not short: first boot pulls the base image (several GB) before
# SSH even answers, and a mediocre host can take a while. The timeout exists to catch a genuinely
# dead offer, not to rush a slow-but-working one — check `vastai logs <id>` before destroying
# anything on a timeout.
#
# RunPod: this polling loop is vast-only (uses `vastai show instance`). If GPU_PROVIDER=runpod,
# fill GPU_SSH_HOST/GPU_SSH_PORT into .env by hand from `runpodctl get pod` or the dashboard, then
# skip straight to `make gpu-bootstrap`.
set -uo pipefail

env_get() { grep -E "^$1=" .env 2>/dev/null | cut -d= -f2- | tr -d '"' | sed 's/[[:space:]]*#.*//'; }
env_set() {
  local key="$1" val="$2"
  if grep -qE "^$key=" .env 2>/dev/null; then
    sed -i.bak -E "s#^$key=.*#$key=$val#" .env && rm -f .env.bak
  else
    printf '%s=%s\n' "$key" "$val" >> .env
  fi
}

PROVIDER="$(env_get GPU_PROVIDER)"
if [ "$PROVIDER" = "runpod" ]; then
  echo "GPU_PROVIDER=runpod — this script only knows how to poll vast.ai."
  echo "Fill GPU_SSH_HOST / GPU_SSH_PORT into .env by hand (runpodctl get pod / RunPod dashboard),"
  echo "then run: make gpu-bootstrap"
  exit 1
fi

TIMEOUT="${TIMEOUT:-25}"   # minutes

ID="$(env_get GPU_INSTANCE_ID)"
[ -n "$ID" ] || { echo "set GPU_INSTANCE_ID in .env (from 'CONFIRM=yes make gpu-provision')"; exit 1; }

RATE="$(env_get GPU_HOURLY)"; RATE="${RATE:-0.40}"
DEADLINE=$(( $(date +%s) + TIMEOUT * 60 ))
START=$(date +%s)

printf 'waiting for instance %s (giving up after %s min)\n' "$ID" "$TIMEOUT"

while :; do
  NOW=$(date +%s)
  MINS=$(( (NOW - START) / 60 ))

  RAW="$(vastai show instance "$ID" --raw 2>/dev/null)"
  STATUS="$(printf '%s' "$RAW" | python3 -c 'import sys,json
try: print(json.load(sys.stdin).get("actual_status") or "?")
except Exception: print("?")' 2>/dev/null)"
  HOST="$(printf '%s' "$RAW" | python3 -c 'import sys,json
try: print(json.load(sys.stdin).get("ssh_host") or "")
except Exception: print("")' 2>/dev/null)"
  PORT="$(printf '%s' "$RAW" | python3 -c 'import sys,json
try: print(json.load(sys.stdin).get("ssh_port") or "")
except Exception: print("")' 2>/dev/null)"

  if [ "$STATUS" = "running" ] && [ -n "$HOST" ] && [ -n "$PORT" ]; then
    if ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8 -p "$PORT" "root@$HOST" true 2>/dev/null; then
      env_set GPU_SSH_HOST "$HOST"
      env_set GPU_SSH_PORT "$PORT"
      echo
      echo "✓ up after ${MINS}m — root@${HOST}:${PORT} (saved to .env) — next: make gpu-bootstrap"
      exit 0
    fi
  fi

  if [ "$NOW" -ge "$DEADLINE" ]; then
    SPENT="$(python3 -c "print(f'{($NOW - $START) / 3600 * $RATE:.2f}')")"
    cat <<EOF

✗ still "$STATUS" after ${TIMEOUT} min (\$${SPENT} burned).

  Check before destroying — a slow image pull and a dead host look identical from here:
      vastai logs $ID

  "Pull complete" lines still advancing → it is working, just slow. Wait, or TIMEOUT=40 make gpu-wait.
  Every layer parked on "Waiting" for many minutes → actually wedged. Only then:
      vastai destroy instance $ID -y
      SKIP=$ID CONFIRM=yes make gpu-provision
EOF
    exit 1
  fi

  printf '\r  %s  %dm  \$%s' "$STATUS" "$MINS" \
    "$(python3 -c "print(f'{($NOW - $START) / 3600 * $RATE:.3f}')")"
  sleep 15
done
