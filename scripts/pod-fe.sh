#!/usr/bin/env bash
#
# rsync motions/ onto the pod, build it THERE, run it under PM2 on :2030 — so end-users open
# one HTTPS link instead of needing your laptop running. This is the frontend half of what
# motions-studio/setup/fullstack-setup.sh does for a plain VPS.
#
#   make gpu-fe
#
# The Cloudflare side (ingress FE_DOMAIN → localhost:2030 plus its DNS record) is created by
# setup-pm2.sh during `make gpu-bootstrap`, which passes CF_FE_DOMAIN. This script only deals
# with the app itself, so it is safe to re-run every time you change frontend code.
#
# Why build on the pod instead of rsyncing a local .output/: @nuxt/image pulls in sharp, and
# motions/node_modules/@img/ holds sharp-darwin-arm64 — a macOS native binary. Copying that to
# a Linux x64 pod produces a frontend that builds fine and dies at runtime.
set -uo pipefail

log()  { printf '\033[36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33m !!\033[0m %s\n' "$*"; }
die()  { printf '\033[31m ✗ \033[0m%s\n' "$*" >&2; exit 1; }

env_get() { grep -E "^$1=" .env 2>/dev/null | cut -d= -f2- | sed -E 's/[[:space:]]*#.*$//' | tr -d '"'; }

HOST="$(env_get GPU_SSH_HOST)"
PORT="$(env_get GPU_SSH_PORT)"
[ -n "$HOST" ] && [ -n "$PORT" ] || die "GPU_SSH_HOST/GPU_SSH_PORT missing from .env — run: make gpu-wait"

DOMAIN="$(env_get DOMAIN)"
FE_DOMAIN="$(env_get FE_DOMAIN)"
FE_PORT="$(env_get FE_PORT)"; FE_PORT="${FE_PORT:-2030}"
[ -n "$DOMAIN" ] || die "DOMAIN missing from .env — run: make gpu-preflight"
[ -n "$FE_DOMAIN" ] || die "FE_DOMAIN missing from .env (e.g. app.yourdomain.com) — see docs/gpu-pod.md#frontend-on-the-pod"

SSH_OPTS=(-o StrictHostKeyChecking=accept-new -p "$PORT")
remote() { ssh "${SSH_OPTS[@]}" "root@$HOST" "$1" < /dev/null; }

# ── Preflight: fail before rsync, not 4 minutes into a build ──────────────────
# Nuxt 4 needs Node ^20.19 || >=22.12. setup-pm2.sh installs Node 20 from nodesource, which is
# new enough today — but an older pod image can carry an older 20.x. Do NOT auto-upgrade: the
# backend `api` process runs on this same Node, and swapping it under a live pod is not our call.
NODE_V="$(remote 'node -v 2>/dev/null || true' | tr -d '\r')"
[ -n "$NODE_V" ] || die "no node on the pod — the backend isn't installed yet. Run: make gpu-bootstrap"
NODE_MAJ="${NODE_V#v}"; NODE_MIN="${NODE_MAJ#*.}"; NODE_MIN="${NODE_MIN%%.*}"; NODE_MAJ="${NODE_MAJ%%.*}"
if [ "$NODE_MAJ" -lt 20 ] || { [ "$NODE_MAJ" -eq 20 ] && [ "$NODE_MIN" -lt 19 ]; }; then
  warn "pod has Node $NODE_V — Nuxt 4 needs ^20.19 or >=22.12."
  warn "upgrade it yourself (this also restarts the backend, so pick your moment):"
  warn "  ssh -p $PORT root@$HOST 'curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \\"
  warn "    && apt-get install -y nodejs && pm2 update && pm2 restart all'"
  die "aborting — nothing has been copied to the pod yet"
fi
remote "test -f ~/motion-backend/.env" \
  || die "~/motion-backend/.env not on the pod — the backend isn't deployed. Run: make gpu-bootstrap"
log "pod: Node $NODE_V · backend .env present"

# ── FE_BUILD: build ở CI (mặc định) hay build trên pod ────────────────────────
# ci   tải .output đã build sẵn từ artifact của workflow build-frontend.yml rồi rsync lên pod.
#      Pod KHÔNG npm install, KHÔNG build → đỉnh RAM về 0 (build đo được 2,49 GB) nên box CPU
#      4 GB $0,06/giờ dùng được, và bước này từ ~2-4 phút xuống ~15 giây.
# pod  build trên pod như trước. Đường thoát khi bạn đang sửa FE mà chưa muốn push.
FE_BUILD="${FE_BUILD:-$(env_get FE_BUILD)}"; FE_BUILD="${FE_BUILD:-ci}"
case "$FE_BUILD" in
  ci|pod) ;;
  *) die "FE_BUILD=$FE_BUILD không hợp lệ — chỉ nhận: ci | pod" ;;
esac

CI_OUTPUT=""
if [ "$FE_BUILD" = ci ]; then
  command -v gh >/dev/null || die "FE_BUILD=ci cần GitHub CLI:  brew install gh && gh auth login
  (hoặc FE_BUILD=pod bash scripts/pod-fe.sh để build trên pod như trước)"

  # motions/ có thay đổi chưa commit thì artifact KHÔNG chứa chúng — và deploy ra một bản FE khác
  # cái bạn đang sửa là loại lỗi mất hàng giờ mới nhận ra. Chặn thẳng.
  if [ -n "$(git status --porcelain -- motions/ 2>/dev/null)" ]; then
    die "motions/ có thay đổi chưa commit — artifact CI build từ commit, nên nó KHÔNG chứa các thay đổi đó.
  Commit + push rồi chờ CI, hoặc:  FE_BUILD=pod bash scripts/pod-fe.sh"
  fi

  SHA="$(git rev-parse HEAD 2>/dev/null)"
  [ -n "$SHA" ] || die "không đọc được HEAD — đây có phải git repo?"
  log "tìm artifact FE cho commit ${SHA:0:8}…"
  RUNS_JSON="$(gh run list --workflow build-frontend.yml --limit 40 \
                 --json databaseId,headSha,conclusion,status 2>&1)"
  # Phân biệt "workflow chưa tồn tại trên default branch" với "có workflow nhưng chưa run nào xong".
  # Lần dùng đầu tiên LUÔN rơi vào ca thứ nhất, và nếu gộp hai ca thì message chỉ đường sai.
  case "$RUNS_JSON" in
    *"not found on the default branch"*|*"HTTP 404"*)
      die "GitHub chưa biết workflow build-frontend.yml — nó phải nằm trên DEFAULT BRANCH mới chạy được.
  Push nó lên:  git push
  Rồi chờ CI:   gh run watch
  Hoặc build trên pod ngay bây giờ:  FE_BUILD=pod bash scripts/pod-fe.sh" ;;
  esac
  RUN_ID="$(printf '%s' "$RUNS_JSON" | SHA="$SHA" python3 -c '
import sys, json, os
sha = os.environ["SHA"]
try: runs = json.load(sys.stdin)
except Exception: runs = []
for r in runs:
    if r.get("headSha") == sha and r.get("conclusion") == "success":
        print(r["databaseId"]); break
' 2>/dev/null)"
  if [ -z "$RUN_ID" ]; then
    # Nói rõ commit này ĐÃ push chưa — hai nguyên nhân khác nhau, hai cách sửa khác nhau.
    if git branch -r --contains "$SHA" >/dev/null 2>&1 && [ -n "$(git branch -r --contains "$SHA" 2>/dev/null)" ]; then
      PUSHED="commit này đã push"
    else
      PUSHED="commit này CHƯA push — đó gần như chắc chắn là nguyên nhân"
    fi
    die "không có lần chạy build-frontend.yml THÀNH CÔNG cho commit ${SHA:0:8} ($PUSHED).
  Ba cách:
    1. push rồi chờ CI:              git push && gh run watch
    2. chạy tay cho branch hiện tại: gh workflow run build-frontend.yml --ref \$(git rev-parse --abbrev-ref HEAD)
    3. build trên pod (chậm, cần RAM): FE_BUILD=pod bash scripts/pod-fe.sh
  Xem trạng thái:  gh run list --workflow build-frontend.yml --limit 5"
  fi

  CI_TMP="$(mktemp -d)"
  trap 'rm -rf "$CI_TMP"' EXIT
  log "tải artifact từ run $RUN_ID…"
  gh run download "$RUN_ID" -n motions-output -D "$CI_TMP" \
    || die "gh run download thất bại — artifact có thể đã hết hạn (giữ 90 ngày).
  Chạy lại CI:  gh workflow run build-frontend.yml --ref \$(git rev-parse --abbrev-ref HEAD)"
  tar -xzf "$CI_TMP/motions-output.tar.gz" -C "$CI_TMP" || die "giải nén artifact thất bại"
  [ -d "$CI_TMP/.output/server" ] || die "artifact không có .output/server — workflow đổi cấu trúc?"
  # Cổng kiểm cuối, ở phía client: sharp phải là linux. CI đã kiểm, nhưng artifact có thể là bản cũ
  # từ trước khi cổng đó tồn tại, và hỏng ở đây thì triệu chứng là FE chết trên pod.
  if ! find "$CI_TMP/.output" -path '*@img/sharp-linux*' -type d 2>/dev/null | grep -q .; then
    die "artifact không chứa @img/sharp-linux* → sẽ chết trên pod Linux.
  Build lại: gh workflow run build-frontend.yml --ref \$(git rev-parse --abbrev-ref HEAD)"
  fi
  CI_OUTPUT="$CI_TMP/.output"
  log "artifact OK ($(du -sh "$CI_OUTPUT" | cut -f1), sharp linux-x64)"
fi

# ── Ship the source ───────────────────────────────────────────────────────────
# No .env: the pod's copy is generated below from the backend's own API_KEY, and the local one
# points at the public URL (right for `make dev`, wrong for a process sitting next to the API).
log "syncing motions/ → root@$HOST:$PORT:~/motions"
rsync -az --delete \
  --exclude='.git' --exclude='node_modules' --exclude='.nuxt' --exclude='.output' \
  --exclude='.env' --exclude='.env.*' --exclude='.data' \
  -e "ssh ${SSH_OPTS[*]}" \
  motions/ "root@$HOST:~/motions/" \
  || die "rsync failed"

# .output đi riêng một lần rsync, vì lần trên --delete và exclude nó. --delete ở đây nữa để bản
# build cũ trên pod không để lại file mồ côi mà nitro vẫn phục vụ.
if [ -n "$CI_OUTPUT" ]; then
  log "đẩy .output từ CI lên pod ($(du -sh "$CI_OUTPUT" | cut -f1))…"
  rsync -az --delete -e "ssh ${SSH_OPTS[*]}" \
    "$CI_OUTPUT/" "root@$HOST:~/motions/.output/" \
    || die "rsync .output thất bại"
fi

# ── Build + run, entirely on the pod ──────────────────────────────────────────
# API_KEY is read from the backend's .env ON THE POD and never crosses the wire to this machine,
# so it can't land in a local shell history, a log file, or a stray commit.
log "installing deps + building Nuxt on the pod (first run takes a few minutes)…"
ssh "${SSH_OPTS[@]}" "root@$HOST" 'bash -s' <<REMOTE
set -uo pipefail
cd ~/motions || exit 1

KEY="\$(grep -E '^API_KEY=' ~/motion-backend/.env | head -1 | cut -d= -f2-)"
[ -n "\$KEY" ] || { echo "API_KEY empty in ~/motion-backend/.env — backend setup did not finish"; exit 1; }
APIP="\$(grep -E '^API_PORT=' ~/motion-backend/.env | head -1 | cut -d= -f2-)"; APIP="\${APIP:-8080}"

# NUXT_MOTION_API_URL is loopback on purpose: the FE's server-side proxy sits on the same box as
# the API, so routing it back out through Cloudflare would add latency and a failure mode for
# nothing. NUXT_PUBLIC_* must stay public — that one runs in the visitor's browser.
umask 077
cat > .env <<FEENV
# Generated by scripts/pod-fe.sh — do not edit by hand, it is overwritten on every deploy.
NUXT_MOTION_API_URL=http://127.0.0.1:\$APIP
NUXT_MOTION_API_KEY=\$KEY
NUXT_PUBLIC_MOTION_BACKEND_URL=https://$DOMAIN
FEENV
umask 022
echo "  .env written: server-side→127.0.0.1:\$APIP · client-side→https://$DOMAIN"

if [ "$FE_BUILD" = ci ]; then
  # .output đã được rsync từ artifact CI. Không npm install (nitro nhúng sẵn server deps vào
  # .output/server/node_modules, 26MB, kể cả sharp), không build → không có đỉnh RAM nào.
  [ -d .output/server ] || { echo ".output/server không có trên pod — rsync artifact thất bại?"; exit 1; }
  echo "  FE_BUILD=ci → dùng .output build sẵn từ CI, KHÔNG npm install, KHÔNG build"
  node -e 'require("./.output/server/node_modules/sharp")' 2>/dev/null \
    && echo "  sharp nạp được trên pod ✓" \
    || echo "  !! sharp KHÔNG nạp được — kiến trúc binary sai, FE sẽ lỗi khi xử lý ảnh"
else

npm install --no-audit --no-fund || { echo "npm install failed"; exit 1; }

# Chốt heap V8 theo RAM THỰC của container, không theo RAM của host.
# Vì sao cần: node tự chọn max-old-space theo RAM nó THẤY, và trong container nó thấy RAM của HOST
# (box CPU RunPod báo 755 GB) chứ không thấy cgroup limit 4-8 GB. Node vì thế cho phép heap phình
# tới hàng chục GB rồi bị OOM-killer của cgroup giết — triệu chứng là "Killed" trần trụi, không
# stack trace, không dòng nào nói tới RAM.
# Đo 04/08/2026: build này đỉnh 2,49 GB RSS. Chừa ~1,2 GB cho Postgres/MinIO/api đang chạy song song
# cộng phần non-heap của node, nên lấy (limit - 1200MB) và kẹp trong [1536, 6144].
# MỌI $ dưới đây phải escape: heredoc là <<REMOTE (không đóng ngoặc) nên $x không escape sẽ giãn
# ở MÁY LOCAL thành rỗng, không phải trên pod. Bản trước của khối này thiếu escape và vì thế
# `[ -r "$CG" ]` trở thành `[ -r "" ]` — chưa ai thấy vì nó chưa từng chạy trên pod nào.
CG=/sys/fs/cgroup/memory.max
[ -r "\$CG" ] || CG=/sys/fs/cgroup/memory/memory.limit_in_bytes
LIM_MB=\$(awk '{ if (\$1 ~ /^[0-9]+\$/) printf "%.0f", \$1/1048576; else print 0 }' "\$CG" 2>/dev/null || echo 0)
if [ "\${LIM_MB:-0}" -gt 0 ] && [ "\$LIM_MB" -lt 200000 ]; then
  HEAP=\$(( LIM_MB - 1200 ))
  [ "\$HEAP" -lt 1536 ] && HEAP=1536
  [ "\$HEAP" -gt 6144 ] && HEAP=6144
  echo "  RAM container \${LIM_MB}MB → NODE_OPTIONS=--max-old-space-size=\$HEAP (build đỉnh ~2.5GB)"
  export NODE_OPTIONS="\${NODE_OPTIONS:-} --max-old-space-size=\$HEAP"
else
  echo "  không đọc được cgroup memory limit — để node tự chọn heap"
fi

npm run build || {
  echo "nuxt build failed"
  echo "  Nếu chỉ thấy 'Killed' mà không có stack: OOM. Build này cần ~2.5GB đỉnh, và Postgres/"
  echo "  MinIO/api đang chạy song song chiếm thêm ~0.7GB. Trên box 4GB là sát trần."
  echo "  Sửa: tăng CPU_VCPU (RAM = vCPU × hệ số flavor) hoặc đổi CPU_FLAVOR sang bản nhiều RAM hơn"
  echo "  (c=×2 · g=×4 · m=×8), hoặc bỏ hẳn build khỏi pod bằng FE_BUILD=ci (mặc định)."
  echo "  Xem docs/gpu-pod.md#box-cpu-ram."
  exit 1
}

fi   # hết nhánh FE_BUILD=pod

chmod +x .run.sh
pm2 delete motions >/dev/null 2>&1
PORT=$FE_PORT pm2 start ~/motions/.run.sh --name motions --interpreter bash --update-env \
  || { echo "pm2 start failed"; exit 1; }
pm2 save >/dev/null 2>&1
REMOTE
[ "$?" -eq 0 ] || die "frontend deploy failed on the pod — see the output above, then: ssh -p $PORT root@$HOST 'pm2 logs motions --lines 50 --nostream'"

# ── Prove it, from outside the pod ────────────────────────────────────────────
log "waiting for https://$FE_DOMAIN to answer…"
for _ in $(seq 1 30); do
  if curl -fsS -o /dev/null "https://$FE_DOMAIN/"; then
    echo
    log "frontend live → https://$FE_DOMAIN"
    echo "  log in as $(env_get SUPER_ADMIN) — OTP goes to that address"
    echo "  pm2 ls on the pod now shows 'motions' next to api/worker/comfyui"
    echo
    exit 0
  fi
  sleep 2
done

# A 404 here is the tunnel's catch-all, not the frontend: it means cloudflared has no ingress
# rule for FE_DOMAIN, which only setup-pm2.sh (via CF_FE_DOMAIN) creates.
CODE="$(curl -s -o /dev/null -w '%{http_code}' "https://$FE_DOMAIN/" 2>/dev/null)"
warn "https://$FE_DOMAIN not serving the app yet (HTTP ${CODE:-no response})."
if [ "$CODE" = "404" ]; then
  warn "404 is the Cloudflare Tunnel catch-all → no ingress rule for $FE_DOMAIN."
  warn "Run 'make gpu-bootstrap' once: it passes CF_FE_DOMAIN to setup-pm2.sh, which adds the"
  warn "ingress rule and the DNS record. This script only deploys the app, not the tunnel."
else
  warn "check the app itself:  ssh -p $PORT root@$HOST 'pm2 logs motions --lines 50 --nostream'"
  warn "and the tunnel:        ssh -p $PORT root@$HOST 'systemctl status cloudflared --no-pager'"
fi
exit 1
