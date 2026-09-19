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
# Works on both providers: vast via `vastai show instance`, RunPod via `runpodctl pod get`.
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

# shellcheck source=lib-gpu-provider.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib-gpu-provider.sh"
PROVIDER="$(gpu_provider)"
if [ "$PROVIDER" = "runpod" ]; then
  command -v runpodctl >/dev/null || { echo "runpodctl not found — brew install runpod/runpodctl/runpodctl"; exit 1; }
fi

# vast_rent.py's own path, resolved ONCE before the loop (not re-computed, unquoted, on every
# single poll — F8/I7, 2026-09-19): a $(cd "$(dirname ...)" && pwd) inline in the middle of an
# `if` condition word-splits on any path containing a space and reruns two subshells every 15s
# for no reason.
VAST_RENT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/vast_rent.py"

# DIRECT_FAILS / USING_DIRECT (F8/I7): a bogus "direct" address (vast_rent.py's parse_ssh_url
# rejects the worst offenders now, but a real address that just never answers is not something
# it can detect) used to lock this loop onto it for the rest of the run, with no way back to the
# sshX.vast.ai proxy. After 4 consecutive ssh probes fail against a direct address, stop asking
# for it and fall back to the proxy values from `show instance` for the remaining polls.
DIRECT_FAILS=0
USING_DIRECT=0

# probe — sets STATUS / HOST / PORT for the current pod. One function, two providers, so the
# retry/timeout/billing logic below stays provider-agnostic.
#
# RunPod's JSON has moved around across releases (portMappings map vs runtime.ports list), and a
# pod that is still starting has NO port block at all rather than an empty one. So: try both
# shapes, treat "not there yet" as normal, and never guess a port number.
probe() {
  local raw
  if [ "$PROVIDER" = "runpod" ]; then
    # `runpodctl ssh info` is the canonical source for the SSH endpoint — `pod get` does not carry
    # one. While the pod is still pulling its image both return {"error": "pod not ready"}, which
    # is normal, not a failure. Verified against a live pod 2026-08-01.
    #
    # Key names are searched permissively rather than pinned: this CLI renamed `get pod` to
    # `pod get` and `ssh connect` to `ssh info` between releases, so pinning a key is how this
    # breaks silently on the next upgrade.
    raw="$(runpodctl ssh info "$ID" -o json 2>/dev/null)"
    case "$raw" in *'"error"'*|"") raw="$(runpodctl pod get "$ID" -o json 2>/dev/null)";; esac
    read -r STATUS HOST PORT <<<"$(printf '%s' "$raw" | python3 -c '
import sys, json
def walk(node, want):
    if isinstance(node, dict):
        for k in want:
            if node.get(k) not in (None, ""):
                return node[k]
        for v in node.values():
            r = walk(v, want)
            if r is not None: return r
    elif isinstance(node, list):
        for v in node:
            r = walk(v, want)
            if r is not None: return r
    return None

def ssh_endpoint(node):
    # shape A — what `runpodctl ssh info` returns once the pod is up: a host and a port under
    # some pair of these names. Tried first because it is the documented source.
    host = walk(node, ["host", "hostname", "publicIp", "ip"])
    port = walk(node, ["port", "sshPort", "publicPort"])
    if host and port:
        return host, port
    # shape B: {"portMappings": {"22": 40123}, "publicIp": "1.2.3.4"}
    pm = walk(node, ["portMappings"])
    if isinstance(pm, dict) and pm.get("22") and host:
        return host, pm["22"]
    # shape C: {"runtime": {"ports": [{"privatePort":22,"publicPort":40123,"ip":"1.2.3.4"}]}}
    # NOT the same as the "ports" field of `pod get`, which is a list of STRINGS like ["22/tcp"] —
    # hence the isinstance guard, without which this silently matches nothing.
    # (No apostrophes in here: this block lives inside python3 -c with single quotes.)
    ports = walk(node, ["ports"])
    if isinstance(ports, list):
        for p in ports:
            if isinstance(p, dict) and str(p.get("privatePort")) == "22":
                if p.get("publicPort") and p.get("ip"):
                    return p["ip"], p["publicPort"]
    return None, None

try:
    d = json.load(sys.stdin)
except Exception:
    print("? \x00 \x00"); raise SystemExit
st = walk(d, ["desiredStatus", "status", "lastStatus"]) or "?"
ip, port = ssh_endpoint(d)
print(st, ip or "\x00", port or "\x00")
' 2>/dev/null)"
    [ "$HOST" = $'\x00' ] && HOST=""
    [ "$PORT" = $'\x00' ] && PORT=""
    # RunPod says RUNNING the moment the container is scheduled, well before sshd is listening.
    # The ssh probe below is what actually decides, so normalise the word and let it do its job.
    [ "$STATUS" = "RUNNING" ] && STATUS="running"
    return
  fi

  raw="$(vastai show instance "$ID" --raw 2>/dev/null)"
  STATUS="$(printf '%s' "$raw" | python3 -c 'import sys,json
try: print(json.load(sys.stdin).get("actual_status") or "?")
except Exception: print("?")' 2>/dev/null)"
  HOST="$(printf '%s' "$raw" | python3 -c 'import sys,json
try: print(json.load(sys.stdin).get("ssh_host") or "")
except Exception: print("")' 2>/dev/null)"
  PORT="$(printf '%s' "$raw" | python3 -c 'import sys,json
try: print(json.load(sys.stdin).get("ssh_port") or "")
except Exception: print("")' 2>/dev/null)"

  # Prefer the DIRECT address (`vastai ssh-url`) over the sshX.vast.ai proxy above: measured
  # 2026-09-19, the proxy rejected the registered key on one host while the direct ip:port
  # accepted it. `ssh-url` may not answer while the instance is still loading — then the proxy
  # values stay, and the ssh probe in the main loop decides whether anything is reachable.
  #
  # But only while it has not already failed 4 times in a row (F8/I7): a direct address that
  # LOOKS valid but never answers (parse_ssh_url rejects the obvious garbage, not a real
  # unreachable host) must not lock this loop out of the proxy for the rest of the timeout.
  USING_DIRECT=0
  local direct
  if [ "$DIRECT_FAILS" -lt 4 ] \
     && direct="$(python3 "$VAST_RENT" --ssh-target "$ID" 2>/dev/null)" \
     && [ -n "$direct" ]; then
    HOST="${direct% *}"
    PORT="${direct#* }"
    USING_DIRECT=1
  fi
}

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

  probe

  # Gate on the endpoint and a real SSH handshake, NOT on the status string.
  #
  # Status used to be part of this condition and it deadlocked the RunPod path: once the pod is
  # ready `runpodctl ssh info` returns {id, ip, name, port, ssh_command, ssh_key} and NO status
  # field at all, so STATUS fell back to "?" and `[ "$STATUS" = running ]` was never true — the
  # loop waited out its full timeout while holding a perfectly good host and port. Diagnosed on a
  # live pod 2026-08-01 after four wrong guesses.
  #
  # Status is display-only now. The ssh probe below is the real test and cannot be fooled by a
  # provider renaming a JSON field, which both of these CLIs have already done more than once.
  if [ -n "$HOST" ] && [ -n "$PORT" ]; then
    if ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8 -p "$PORT" "root@$HOST" true 2>/dev/null; then
      env_set GPU_SSH_HOST "$HOST"
      env_set GPU_SSH_PORT "$PORT"
      echo
      echo "✓ up after ${MINS}m — root@${HOST}:${PORT} (saved to .env) — next: make gpu-bootstrap"
      exit 0
    elif [ "$USING_DIRECT" = 1 ]; then
      DIRECT_FAILS=$((DIRECT_FAILS + 1))
    fi
  fi

  if [ "$NOW" -ge "$DEADLINE" ]; then
    SPENT="$(python3 -c "print(f'{($NOW - $START) / 3600 * $RATE:.2f}')")"
    printf '\n✗ still "%s" after %s min ($%s burned).\n\n' "$STATUS" "$TIMEOUT" "$SPENT"
    echo "  Check before destroying — a slow image pull and a dead host look identical from here:"
    if [ "$PROVIDER" = "runpod" ]; then
      cat <<EOF
      runpodctl pod get $ID
      runpodctl pod logs $ID     # if your runpodctl has it; otherwise read logs in the console

  Still pulling the image → it works, just slow. Wait, or TIMEOUT=40 make gpu-wait.
  Wedged for many minutes → only then:
      runpodctl pod delete $ID
      CONFIRM=yes make gpu-provision

  A pod with no SSH port block usually means it was created without '--ports 22/tcp'.
EOF
    else
      cat <<EOF
      vastai logs $ID

  "Pull complete" lines still advancing → it is working, just slow. Wait, or TIMEOUT=40 make gpu-wait.
  Every layer parked on "Waiting" for many minutes → actually wedged. Only then:
      vastai destroy instance $ID -y
      CONFIRM=yes make gpu-provision

  No SKIP= here (F10, 2026-09-19): SKIP filters OFFER ids, and the id above is an INSTANCE id —
  the scoreboard already blacklists a slow machine on its own after this destroy.
EOF
    fi
    exit 1
  fi

  printf '\r  %s  %dm  \$%s' "$STATUS" "$MINS" \
    "$(python3 -c "print(f'{($NOW - $START) / 3600 * $RATE:.3f}')")"
  sleep 15
done
