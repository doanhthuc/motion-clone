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
# CUDA-capable image with apt/sudo access.
#
# The image's own torch is never used: setup-pm2.sh builds ComfyUI a private venv with
# `python3 -m venv` (no --system-site-packages), then lib-gpu.sh's motion_install_best_pytorch()
# pip-installs a torch matching the DRIVER it finds via nvidia-smi:
#     driver >= R580          → cu130 + torch 2.12.1   (CUDA 13.0, what we want)
#     Blackwell, driver < 580 → cu128 + torch 2.11.0   (works, warns, slower)
# So the tag below does not decide the CUDA version — the host's driver does. It is pinned to
# cuda13.0/2.12.1 anyway so the declared CUDA matches what the setup targets, which is what keeps
# you from being scheduled onto an R570 host and silently landing in the cu128 fallback.
IMAGE="${IMAGE:-$(env_get POD_IMAGE)}"; IMAGE="${IMAGE:-pytorch/pytorch:2.12.1-cuda13.0-cudnn9-devel}"

# Same precedence as every other knob here: environment overrides .env.
POD_VOLUME="${POD_VOLUME:-$(env_get POD_VOLUME)}"
POD_VOLUME_ID="${POD_VOLUME_ID:-$(env_get POD_VOLUME_ID)}"
MIN_CUDA_VERSION="${MIN_CUDA_VERSION:-$(env_get MIN_CUDA_VERSION)}"; MIN_CUDA_VERSION="${MIN_CUDA_VERSION:-13.0}"

env_set() {
  local key="$1" val="$2"
  if grep -qE "^$key=" .env 2>/dev/null; then
    sed -i.bak -E "s#^$key=.*#$key=$val#" .env && rm -f .env.bak
  else
    printf '%s=%s\n' "$key" "$val" >> .env
  fi
}

# --- Network Volume is RunPod-only ------------------------------------------------------------
# vast.ai has no network volume that survives destroying the instance, so POD_VOLUME there is a
# lie: the wiring would "work" and then vanish with the pod.
if [ -n "$POD_VOLUME" ] && [ "$GPU_PROVIDER" != "runpod" ]; then
  die "POD_VOLUME=$POD_VOLUME but GPU_PROVIDER=$GPU_PROVIDER.
    Network Volumes are a RunPod feature. vast.ai storage dies with the instance, so models would
    still be re-downloaded (~33GB) on every rent.
    Either set GPU_PROVIDER=runpod, or clear POD_VOLUME to accept re-downloading."
fi

if [ "$GPU_PROVIDER" = "runpod" ]; then
  # --- RunPod branch ---------------------------------------------------------------------------
  # runpodctl 2.8 attaches a Network Volume at create time (--network-volume-id) and can constrain
  # the host's CUDA (--min-cuda-version), so the whole flow is one command. Older runpodctl could
  # do neither, which is why this used to send you to the dashboard.
  #
  # Gate on the FLAG, not on a version string: `runpodctl version` output has changed shape across
  # releases, and what actually matters is whether this binary supports the flag we are about to pass.
  command -v runpodctl >/dev/null \
    || die "runpodctl not found —  brew install runpod/runpodctl/runpodctl"

  RP_CREATE_HELP="$(runpodctl pod create --help 2>&1)"
  case "$RP_CREATE_HELP" in
    *--network-volume-id*) ;;
    *) die "this runpodctl cannot attach a Network Volume ('runpodctl pod create' has no
    --network-volume-id). Upgrade:  brew upgrade runpodctl
    Or clear POD_VOLUME in .env to rent without one (models re-download every pod, ~33GB)." ;;
  esac

  runpodctl user -o json >/dev/null 2>&1 \
    || die "runpodctl has no API key. Get one at runpod.io/console/user/settings, then:  runpodctl doctor"

  # rp_pick FIELD — read one field out of runpodctl JSON without caring which envelope this
  # release used. Different subcommands have wrapped their payload in {"pods":[…]}, {"data":{…}},
  # or returned a bare array; walking the tree for the first object that has the key survives all
  # three, and survives the next rename too.
  rp_pick() {
    RP_KEYS="$1" python3 -c '
import sys, json, os
keys = os.environ["RP_KEYS"].split(",")
def walk(node):
    if isinstance(node, dict):
        for k in keys:
            if node.get(k) not in (None, ""):
                return node[k]
        for v in node.values():
            r = walk(v)
            if r is not None:
                return r
    elif isinstance(node, list):
        for v in node:
            r = walk(v)
            if r is not None:
                return r
    return None
try:
    r = walk(json.load(sys.stdin))
except Exception:
    r = None
print(r if r is not None else "")
' 2>/dev/null
  }

  DC_ARG=()
  VOL_ARGS=()
  if [ -n "$POD_VOLUME" ]; then
    # A pod can only mount a volume in its OWN datacenter, and that is the single most common way
    # this fails. So resolve the volume's datacenter and pin the pod to it rather than hoping.
    if [ -z "$POD_VOLUME_ID" ]; then
      VOL_JSON="$(runpodctl network-volume list -o json 2>/dev/null)"
      VOL_COUNT="$(printf '%s' "$VOL_JSON" | python3 -c '
import sys, json
def vols(node, out):
    if isinstance(node, dict):
        if "id" in node and ("size" in node or "dataCenterId" in node or "datacenterId" in node):
            out.append(node); return
        for v in node.values(): vols(v, out)
    elif isinstance(node, list):
        for v in node: vols(v, out)
out = []
try: vols(json.load(sys.stdin), out)
except Exception: pass
for v in out:
    print("%s\t%s\t%s\t%s" % (v.get("id",""), v.get("name",""),
          v.get("dataCenterId") or v.get("datacenterId") or "?", v.get("size","?")))
' 2>/dev/null)"
      if [ -z "$VOL_COUNT" ]; then
        die "POD_VOLUME=$POD_VOLUME is set but you have no Network Volume yet.
    Create one (~100GB, in a datacenter that stocks your GPU):
        runpodctl network-volume create --name motion --size 100 --data-center-id <DC>
    Pick the datacenter with:  runpodctl datacenter list
    Then re-run. Or clear POD_VOLUME to rent without one (models re-download every pod)."
      fi
      if [ "$(printf '%s\n' "$VOL_COUNT" | wc -l | tr -d ' ')" -gt 1 ]; then
        warn "More than one Network Volume — pick one and put it in .env as POD_VOLUME_ID:"
        printf '%s\n' "$VOL_COUNT" | while IFS=$'\t' read -r vid vname vdc vsize; do
          printf '    %s  %-20s dc=%s  %sGB\n' "$vid" "$vname" "$vdc" "$vsize"
        done
        exit 1
      fi
      POD_VOLUME_ID="$(printf '%s' "$VOL_COUNT" | cut -f1)"
      VOL_DC="$(printf '%s' "$VOL_COUNT" | cut -f3)"
      log "using your only Network Volume: $POD_VOLUME_ID (dc=$VOL_DC)"
      env_set POD_VOLUME_ID "$POD_VOLUME_ID"
    else
      VOL_DC="$(runpodctl network-volume get "$POD_VOLUME_ID" -o json 2>/dev/null | rp_pick dataCenterId,datacenterId)"
      [ -n "$VOL_DC" ] || warn "could not read the datacenter of volume $POD_VOLUME_ID — the pod may land in a datacenter that cannot mount it."
    fi
    VOL_ARGS=(--network-volume-id "$POD_VOLUME_ID" --volume-mount-path "$POD_VOLUME")
    [ -n "${VOL_DC:-}" ] && [ "$VOL_DC" != "?" ] && DC_ARG=(--data-center-ids "$VOL_DC")
  else
    warn "POD_VOLUME empty — this pod re-downloads ~33GB of models and loses its database when destroyed."
  fi

  # --min-cuda-version is the real lever for the cu130 path in lib-gpu.sh: it keeps you off hosts
  # whose driver is too old, instead of finding out from a warn line 30 minutes into setup.
  CREATE=(runpodctl pod create
    --name motion-transfer
    --gpu-id "$GPU" --gpu-count 1
    --image "$IMAGE"
    --container-disk-in-gb "$DISK"
    --ports "22/tcp"
    --min-cuda-version "$MIN_CUDA_VERSION"
    "${VOL_ARGS[@]}" "${DC_ARG[@]}")

  # %q not [*]: the gpu id has spaces ("NVIDIA GeForce RTX 5090"), and this line is meant to be
  # copy-pasteable. [*] would print it unquoted and the paste would be parsed as four arguments.
  echo
  printf '  '; printf '%q ' "${CREATE[@]}"; printf '\n'
  echo

  if [ "${CONFIRM:-}" != "yes" ]; then
    cat <<EOF
$(warn "Dry run — nothing rented.")

  GPU='$GPU' must be a RunPod gpu id — check it against:  runpodctl gpu list
  Read the command above, then:   CONFIRM=yes bash scripts/pod-provision.sh

  After it rents (GPU_INSTANCE_ID is saved to .env for you automatically):
    1. make gpu-wait          # polls runpodctl, writes GPU_SSH_HOST/GPU_SSH_PORT into .env
    2. make gpu-bootstrap     # rsyncs motions-studio + runs setup-motion-transfer.sh on the pod
    3. make gpu-status        # confirm the backend answers at https://\$DOMAIN

  A RunPod Network Volume bills monthly whether or not a pod exists — 'make gpu-destroy' does not
  stop that meter, and is not supposed to. See docs/gpu-pod.md#costs.
EOF
    exit 0
  fi

  log "renting via runpodctl…"
  RAW="$("${CREATE[@]}" 2>&1)" || die "runpodctl pod create failed:
$RAW"
  NEW_ID="$(printf '%s' "$RAW" | rp_pick id,podId)"
  if [ -n "$NEW_ID" ]; then
    env_set GPU_INSTANCE_ID "$NEW_ID"
    log "rented — pod $NEW_ID (saved to .env as GPU_INSTANCE_ID). Next: make gpu-wait"
  else
    warn "rented, but could not parse the pod id out of the response — find it with 'runpodctl pod list'"
    echo "$RAW"
    echo
    log "put the pod id in .env as GPU_INSTANCE_ID, then: make gpu-wait"
  fi
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
