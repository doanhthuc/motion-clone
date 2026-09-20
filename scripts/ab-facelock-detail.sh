#!/usr/bin/env bash
#
# A/B faceLockDetailKeep (20/09/2026): how much of Wan's own face texture the identity swap should
# leave alone.
#
#   symptom — with faceLock on, the face comes back flatter than Wan drew it, and with
#   faceLockRestore (CodeFormer) on top it comes back etched instead. Measured on the frame-aligned
#   pair in .smoke/ab-face/ (original-02-camera-motion.mp4 vs facelock-02.mp4, the same 452 frames
#   with and without the swap): the swap costs the face region 39% of its Laplacian variance,
#   150.7 -> 92.4 as a mean over frames 100-300.
#   the knob — swap_video.py --detail-keep SIGMA lowpasses the swap's own change, so the identity
#   shift keeps its full amplitude while Wan's high frequencies survive. Simulated offline over all
#   452 frames: 150.2 at sigma 2.0, 151.0 at sigma 3.0. That simulation is why this A/B exists — it
#   is not proof, and the thing to look for on the pod is what a still frame cannot show: ghosting
#   (Wan's old eye/lip edges surviving next to the swap's new ones) and frame-to-frame stability.
#
# WHY IT IS CHEAP: every arm swaps the SAME already-rendered Wan video, so no arm pays for a Wan
# render (~8 min) — each is one pass of inswapper, ~22s on a 5090 for 452 frames. The arms are
# therefore also frame-aligned with each other, which a re-render with the same seed would not be.
#
# USE (pod up and reachable — make gpu-wait):
#   scripts/ab-facelock-detail.sh                       # .smoke/ab-face/ defaults, sigmas 0 2 3
#   AB_SIGMAS="0 1.5 2 3 4" scripts/ab-facelock-detail.sh
#   AB_VIDEO=out/…/02-camera-motion.mp4 AB_REF=out/…/01-camera-tryon.png scripts/ab-facelock-detail.sh
#
# Results land in .smoke/ab-facelock-detail/<timestamp>/ (gitignored): one mp4 per sigma, plus
# compare.mp4 — the arms side by side, cropped to the face, which is the only way to judge this.
set -uo pipefail
cd "$(dirname "$0")/.."; ROOT="$(pwd)"

log()  { printf '\n\033[36m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[32m  ✓\033[0m %s\n' "$*"; }
die()  { printf '\033[31m ✗ \033[0m%s\n' "$*" >&2; exit 1; }

env_get() { grep -E "^$1=" .env 2>/dev/null | cut -d= -f2- | sed -E 's/[[:space:]]*#.*$//' | tr -d '"'; }

HOST="$(env_get GPU_SSH_HOST)"; PORT="$(env_get GPU_SSH_PORT)"
[ -n "$HOST" ] && [ -n "$PORT" ] || die "GPU_SSH_HOST/GPU_SSH_PORT missing from .env — run: make gpu-wait"

VIDEO="${AB_VIDEO:-$ROOT/.smoke/ab-face/original-02-camera-motion.mp4}"
REF="${AB_REF:-$ROOT/.smoke/ab-face/prepared-camera-tryon.png}"
SIGMAS="${AB_SIGMAS:-0 2 3}"
[ -f "$VIDEO" ] || die "no Wan render at $VIDEO (set AB_VIDEO=…)"
[ -f "$REF" ]   || die "no reference image at $REF (set AB_REF=…)"

SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -p "$PORT")
# scp spells the port -P; -p means "preserve mtimes" there, so reusing SSH_OPTS silently turns the
# port number into a filename ("stat local 46226: No such file or directory").
SCP_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -P "$PORT")
remote() { ssh "${SSH_OPTS[@]}" "root@$HOST" "$1" < /dev/null; }

# The flag only exists in swap_video.py from 20/09/2026, and /root/facelock holds a COPY made at
# install time — a pod installed earlier would reject --detail-keep and hand back the input
# unchanged. setup.sh is idempotent and re-copies the script, so just run it.
log "refreshing faceLock on the pod (idempotent)"
bash scripts/pod-facelock.sh >/dev/null || die "make gpu-facelock failed — run it directly to see why"
ok "swap_video.py on the pod is current"

STAMP="$(date +%Y-%m-%d-%H%M)"
OUT="$ROOT/.smoke/ab-facelock-detail/$STAMP"; mkdir -p "$OUT"
remote "mkdir -p /root/ab-detail && rm -f /root/ab-detail/*" || die "cannot reach the pod"
log "uploading the Wan render + reference"
scp "${SCP_OPTS[@]}" -q "$VIDEO" "root@$HOST:/root/ab-detail/in.mp4" || die "upload of the video failed"
scp "${SCP_OPTS[@]}" -q "$REF"   "root@$HOST:/root/ab-detail/ref.png" || die "upload of the reference failed"

ARMS=()
for SG in $SIGMAS; do
  log "swap with --detail-keep $SG"
  START=$(date +%s)
  remote "/root/facelock/venv/bin/python /root/facelock/swap_video.py \
            --ref /root/ab-detail/ref.png --inp /root/ab-detail/in.mp4 \
            --out /root/ab-detail/s$SG.mp4 --detail-keep $SG 2>&1 | tail -3" \
    || die "the swap failed at sigma $SG"
  scp "${SCP_OPTS[@]}" -q "root@$HOST:/root/ab-detail/s$SG.mp4" "$OUT/detailkeep-$SG.mp4" \
    || die "download of sigma $SG failed"
  ok "detailkeep-$SG.mp4 ($(( $(date +%s) - START ))s)"
  ARMS+=("$OUT/detailkeep-$SG.mp4")
done

# Judged by eye, on video, side by side — a still frame sent the 19/09 faceLockBlend default the
# wrong way, and the face is small enough at 544x960 that a full-frame view hides the difference.
log "building compare.mp4 (Wan | $SIGMAS)"
INPUTS=(-i "$VIDEO"); for A in "${ARMS[@]}"; do INPUTS+=(-i "$A"); done
N=$(( ${#ARMS[@]} + 1 ))
FILTER=""
for ((i = 0; i < N; i++)); do FILTER+="[$i:v]crop=250:320:150:110[c$i];"; done
for ((i = 0; i < N; i++)); do FILTER+="[c$i]"; done
FILTER+="hstack=inputs=$N[v]"
ffmpeg -nostdin -y -v error "${INPUTS[@]}" -filter_complex "$FILTER" -map "[v]" \
  -c:v libx264 -preset fast -crf 16 -pix_fmt yuv420p "$OUT/compare.mp4" \
  || die "ffmpeg could not build the comparison"

ok "$OUT/compare.mp4  (left: Wan, then $SIGMAS)"
echo
echo "Watch compare.mp4 before deciding. What would make a sigma the wrong choice:"
echo "  · doubled edges around the eyes or lips (Wan's old edge left next to the swap's new one)"
echo "  · the face shimmering between frames where the plain swap sat still"
echo "  · identity drifting back toward the driver — that would mean the lowpass ate the swap"
echo "Then set it per job: camera-motion: { faceLockDetailKeep: <sigma> }"
