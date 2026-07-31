#!/usr/bin/env bash
#
# Find and rent a GPU pod for the motion-transfer backend (motions-studio). Runs on YOUR LAPTOP.
#
#   bash scripts/pod-provision.sh              # search + print the exact create command (safe)
#   CONFIRM=yes bash scripts/pod-provision.sh  # actually rent it
#
# Never rents anything unless you pass CONFIRM=yes — renting bills by the hour from the moment
# the instance is created, so this shows you the command and lets you read it first.
#
#   GPU_PROVIDER=vast|runpod   from .env — picks which CLI/branch runs below.
#   GPU=RTX_4090     card filter. 24GB is the DEPLOY.md minimum for Wan 2.2 Animate + BlockSwap;
#                    the recommended card is RTX_5090 (32GB) — set GPU=RTX_5090 for it.
#   DISK=120         GB. DEPLOY.md minimum for the motion-transfer box (~33GB model group + OS).
#   MAX_DPH=0.60     $/hour ceiling.
#
set -uo pipefail

env_get() { grep -E "^$1=" .env 2>/dev/null | cut -d= -f2- | sed -E 's/[[:space:]]*#.*$//' | tr -d '"'; }

GPU_PROVIDER="${GPU_PROVIDER:-$(env_get GPU_PROVIDER)}"; GPU_PROVIDER="${GPU_PROVIDER:-vast}"
GPU="${GPU:-$(env_get GPU)}"; GPU="${GPU:-RTX_4090}"
DISK="${DISK:-$(env_get DISK)}"; DISK="${DISK:-120}"
MAX_DPH="${MAX_DPH:-$(env_get MAX_DPH)}"; MAX_DPH="${MAX_DPH:-0.60}"
RELIABILITY="${RELIABILITY:-$(env_get RELIABILITY)}"; RELIABILITY="${RELIABILITY:-0.95}"
OFFER="${OFFER:-}"
SKIP="${SKIP:-}"

log()  { printf '\033[36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33m !!\033[0m %s\n' "$*"; }
die()  { printf '\033[31m ✗ \033[0m%s\n' "$*" >&2; exit 1; }

# setup-motion-transfer.sh installs EVERYTHING native (apt + PM2) — Postgres and MinIO are plain
# binaries, not containers, so no Docker-in-Docker is needed inside the pod at all. Just needs a
# CUDA-capable image with apt/sudo access; PyTorch itself gets reinstalled by the script to match
# the detected driver, so the base image's own torch version doesn't matter much.
IMAGE="${IMAGE:-pytorch/pytorch:2.5.1-cuda12.4-cudnn9-devel}"

if [ "$GPU_PROVIDER" = "runpod" ]; then
  # --- RunPod branch — best-effort, LESS TESTED than the vast path below. -------------------
  # runpodctl's flags have moved across versions; verify with `runpodctl create pod --help`
  # before trusting this blindly. See docs/gpu-pod.md#runpod.
  command -v runpodctl >/dev/null || die "runpodctl not found — https://github.com/runpod/runpodctl"
  warn "GPU_PROVIDER=runpod is best-effort — double check the command below against 'runpodctl create pod --help'."

  CREATE=(runpodctl create pod --name motion-transfer --gpuType "$GPU" --gpuCount 1 \
    --imageName "$IMAGE" --containerDiskInGb "$DISK" --ports "22/tcp")

  echo
  echo "  ${CREATE[*]}"
  echo

  if [ "${CONFIRM:-}" != "yes" ]; then
    cat <<EOF
$(warn "Dry run — nothing rented.")

  Read the command above, then:   CONFIRM=yes GPU_PROVIDER=runpod bash scripts/pod-provision.sh

  After it rents (get the pod id + SSH details from 'runpodctl get pod' or the RunPod dashboard):
    1. put the pod id in .env as GPU_INSTANCE_ID
    2. put the SSH host/port in .env as GPU_SSH_HOST / GPU_SSH_PORT (make gpu-wait's auto-detect
       is vast-only — fill these by hand for RunPod)
    3. make gpu-bootstrap     # rsyncs motions-studio + runs setup-motion-transfer.sh on the pod
    4. make gpu-status        # confirm the backend answers at https://\$DOMAIN
EOF
    exit 0
  fi

  log "renting via runpodctl…"
  "${CREATE[@]}" || die "runpodctl create pod failed — check the flags against your runpodctl version"
  log "rented. Get the pod id + SSH host/port from 'runpodctl get pod', fill them into .env, then: make gpu-bootstrap"
  exit 0
fi

# --- vast.ai branch — the validated default path. --------------------------------------------
command -v vastai >/dev/null || die "vastai CLI not found:  pip install vastai  &&  vastai set api-key <key>"

QUERY="gpu_name=${GPU} num_gpus=1 disk_space>=${DISK} reliability>${RELIABILITY} rentable=true"
log "searching: $QUERY  (<= \$${MAX_DPH}/hr)"
OFFERS="$(vastai search offers "$QUERY" -o 'dph+' --raw 2>/dev/null)" || die "vastai search failed — is your API key set?"

command -v python3 >/dev/null || die "python3 needed to read the offer list"

# The offer list goes via a temp FILE, not a pipe: python needs stdin for its own heredoc, and
# inlining the script with -c means fighting two levels of shell quoting.
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
printf '%s' "$OFFERS" > "$TMP"

BEST="$(OFFERS_FILE="$TMP" MAX_DPH="$MAX_DPH" SKIP="$SKIP" OFFER="$OFFER" python3 - <<'PY'
import json, os, sys

try:
    offers = json.load(open(os.environ["OFFERS_FILE"]))
except Exception as e:
    sys.exit(f"could not parse the offer list: {e}")

if not isinstance(offers, list) or not offers:
    sys.exit("no offers matched the search at all — loosen GPU= or DISK=")

pinned = os.environ.get("OFFER", "").strip()
if pinned:
    if not any(str(o["id"]) == pinned for o in offers):
        sys.exit(f"offer {pinned} is not in the current results (gone, or filtered out)")
    print(pinned)
    raise SystemExit

skip = {s.strip() for s in os.environ.get("SKIP", "").split(",") if s.strip()}
cap = float(os.environ["MAX_DPH"])
rows = sorted(
    (o for o in offers if o.get("dph_total", 99) <= cap and str(o["id"]) not in skip),
    key=lambda o: o["dph_total"],
)

if not rows:
    cheapest = min(o.get("dph_total", 99) for o in offers)
    sys.exit(f"nothing at or under ${cap:.2f}/hr (cheapest was ${cheapest:.2f}) — retry with MAX_DPH={cheapest + 0.05:.2f}")

print(f"{len(rows)} offer(s) under the cap:\n", file=sys.stderr)
for o in rows[:5]:
    gb = o.get("gpu_ram", 0) / 1024
    print(
        f"  id={o['id']:<12} ${o['dph_total']:.3f}/hr  {o.get('gpu_name')}  "
        f"{gb:.0f}GB  down={o.get('inet_down', 0):.0f}Mbps  "
        f"rel={o.get('reliability2', 0):.3f}  {o.get('geolocation', '?')}",
        file=sys.stderr,
    )
print(rows[0]["id"])
PY
)" || die "could not pick an offer — see the message above"

echo
log "pick: offer $BEST"

CREATE=(vastai create instance "$BEST" --image "$IMAGE" --disk "$DISK" --ssh --direct)

echo
echo "  ${CREATE[*]}"
echo

if [ "${CONFIRM:-}" != "yes" ]; then
  cat <<EOF
$(warn "Dry run — nothing rented.")

  Read the command above, then:   CONFIRM=yes bash scripts/pod-provision.sh

  After it rents (GPU_INSTANCE_ID is saved to .env for you automatically):
    1. make gpu-wait          # waits for SSH, writes GPU_SSH_HOST/GPU_SSH_PORT into .env for you
    2. make gpu-bootstrap     # rsyncs motions-studio + runs setup-motion-transfer.sh on the pod
    3. make gpu-status        # confirm the backend answers at https://\$DOMAIN

  Also set a 15-minute idle auto-stop in the Vast UI — not to save money in the normal case, but
  as a net for the night you forget 'make gpu-down'. A stopped pod still bills for its disk every
  hour it exists (see docs/gpu-pod.md#costs).
EOF
  exit 0
fi

log "renting…"
RAW="$("${CREATE[@]}" --raw)" || die "create failed"
NEW_ID="$(printf '%s' "$RAW" | python3 -c 'import sys,json
try: d = json.load(sys.stdin)
except Exception: d = {}
print(d.get("new_contract") or d.get("id") or "")' 2>/dev/null)"

if [ -n "$NEW_ID" ]; then
  if grep -qE '^GPU_INSTANCE_ID=' .env 2>/dev/null; then
    sed -i.bak -E "s#^GPU_INSTANCE_ID=.*#GPU_INSTANCE_ID=$NEW_ID#" .env && rm -f .env.bak
  else
    printf 'GPU_INSTANCE_ID=%s\n' "$NEW_ID" >> .env
  fi
  log "rented — instance $NEW_ID (saved to .env as GPU_INSTANCE_ID). Next: make gpu-wait"
else
  warn "rented, but couldn't parse the instance id from the response — find it with 'vastai show instances'"
  echo "$RAW"
  echo
  log "put the instance id in .env as GPU_INSTANCE_ID, then: make gpu-wait"
fi
