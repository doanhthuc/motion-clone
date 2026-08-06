#!/usr/bin/env bash
#
# rsync motions-studio/ onto the pod and run its OWN setup/setup-<SETUP_PROFILE>.sh there —
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
# ALD 05/08/2026 - Khoá JOB_TYPES hẹp hơn profile khai. Profile nói phần mềm chạy được gì;
# biến này nói VOLUME NÀY có model cho gì. Bỏ trống = dùng nguyên JOB_TYPE của profile.
JOB_TYPES_OVERRIDE="$(env_get JOB_TYPES_OVERRIDE)"

# ── Hình dạng deploy: cài gì, và ai chạy job ─────────────────────────────────
# Hai câu hỏi độc lập, hai biến. Cách suy ra nằm ở scripts/lib-deploy-shape.sh vì
# `make gpu-preflight` cũng phải trả lời được đúng hai câu đó — TRƯỚC khi đồng hồ tiền chạy, chứ
# không phải sau 30 phút bootstrap. Một bản logic, hai người đọc.
#
# SETUP_PROFILE — box cài gì (chạy setup/setup-<profile>.sh trên pod):
#   motion-transfer  (mặc định) 4 type Wan · catalog khoá · không Ollama — nhanh, rẻ, ít thứ hỏng
#   full             21 type · catalog KHÔNG lọc (có Qwen/Flux/LTX) · Ollama + bg-remover
#   create-image · tryon                                            — xem motions-studio/setup/README.md
#
# WORKER_SOURCE — ai claim job:
#   local       worker trên pod. GPU pod đã trả tiền 24/7 nên worker này MIỄN PHÍ và không có
#               cold start. Dispatcher không bật.
#   serverless  dừng worker local, RunPod Serverless claim (scale-to-zero, trả theo giây GPU).
#   both        cả hai. CHỈ hợp lý khi hai bên nhận NHÓM TYPE RỜI NHAU — xem cảnh báo ở khối
#               dispatcher cuối file về việc trùng type làm ta trả tiền cold start cho không.
#
# Không đặt WORKER_SOURCE thì suy ra như cũ: có đủ RUNPOD_* → serverless, không thì local.
# shellcheck source=lib-deploy-shape.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib-deploy-shape.sh"
resolve_deploy_shape
for _w in ${DEPLOY_SHAPE_WARNINGS+"${DEPLOY_SHAPE_WARNINGS[@]}"}; do warn "$_w"; done
for _e in ${DEPLOY_SHAPE_ERRORS+"${DEPLOY_SHAPE_ERRORS[@]}"}; do die "$_e"; done
log "hình dạng deploy: SETUP_PROFILE=$SETUP_PROFILE · WORKER_SOURCE=$WORKER_SOURCE"

SSH_OPTS=(-o StrictHostKeyChecking=accept-new -p "$PORT")
REMOTE_DIR="motion-backend"

remote() { ssh "${SSH_OPTS[@]}" "root@$HOST" "$1"; }

log "syncing motions-studio/ → root@$HOST:$PORT:~/$REMOTE_DIR (code only — models download later, in-app)"
remote "mkdir -p ~/$REMOTE_DIR"
# --exclude='venv' (bên cạnh '.venv'): worker/runpod/venv (~27MB, dùng để chạy test_mc_handler.py cục
# bộ) không phải tên ẩn — thiếu exclude này thì mỗi `make gpu-bootstrap` lại đẩy nó lên pod dù pod tự
# tạo venv riêng của nó (setup-motion-transfer.sh), tốn băng thông/ thời gian rsync vô ích mỗi lần.
rsync -az --delete \
  --exclude='.git' --exclude='node_modules' --exclude='.venv' --exclude='venv' --exclude='__pycache__' \
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

if [ "$SETUP_PROFILE" = "cpu-box" ]; then
  # Nói đúng cái sẽ xảy ra. Profile cpu-box đặt SKIP_COMFY=1 nên GPU_OK=0, và cả khối ComfyUI
  # (kể cả motion_install_best_pytorch) bị bỏ qua — lib-feature.sh:597. Nhanh hơn nhiều.
  log "running $SETUP_SCRIPT on the pod — installs Postgres/MinIO/PM2 and wires up the"
  log "Cloudflare Tunnel. NO ComfyUI, NO PyTorch/CUDA, NO worker: đây là box CPU, GPU do RunPod"
  log "Serverless lo. Nhanh hơn profile GPU đáng kể vì bỏ hẳn phần cài torch."
else
  log "running $SETUP_SCRIPT on the pod — installs Postgres/MinIO/PM2, detects the"
  log "GPU/driver, installs ComfyUI + matching PyTorch/CUDA, and wires up the Cloudflare Tunnel. First"
  log "run takes a while (no models downloaded yet — that's a separate manual step, see below)."
fi

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
${JOB_TYPES_OVERRIDE:+JOB_TYPES_OVERRIDE='$JOB_TYPES_OVERRIDE'} \
./$SETUP_SCRIPT" < /dev/null | tee "$LOG"
STATUS=${PIPESTATUS[0]}
[ "$STATUS" -eq 0 ] || die "$SETUP_SCRIPT exited $STATUS — full log: $LOG"

# ── MinIO must point AT the volume, not at a symlink to it ────────────────────
# pod-volume.sh wires storage by symlinking .data/minio → $POD_VOLUME/minio, which works for every
# other consumer and fails for exactly one: MinIO refuses a symlinked drive and dies on boot with
#   FATAL Unable to initialize backend ... HINT: Drives are not directories
# then PM2 restarts it forever. Seen on a live pod 2026-08-01 (37 restarts before it was noticed).
# `mount --bind` would fix the symlink but the container denies it (permission denied).
# ecosystem.config.cjs:58 reads MINIO_DATA_DIR, so pointing MinIO straight at the volume path
# sidesteps the symlink entirely. Done after setup because setup rewrites .env.
#
# `pm2 delete` + `pm2 start`, NOT `pm2 restart --update-env`. --update-env refreshes environment
# variables but leaves `args` frozen at whatever `pm2 start` computed the first time, and the data
# directory is an ARGUMENT (`server <dir>`), not an env var. The first attempt used --update-env
# and looked like it worked — pm2 said online, /minio/health/live said 200 — while MinIO quietly
# kept writing to the container disk. Only `ls /workspace/minio` showed the truth: empty.
# Verify by the presence of .minio.sys ON THE VOLUME, never by pm2 status.
if [ -n "$POD_VOLUME" ]; then
  log "pointing MinIO at $POD_VOLUME/minio directly (it rejects symlinked drives)"
  remote "cd ~/$REMOTE_DIR && \
    { rmdir .data/minio 2>/dev/null || rm -f .data/minio; } ; \
    grep -q '^MINIO_DATA_DIR=' .env \
      && sed -i 's#^MINIO_DATA_DIR=.*#MINIO_DATA_DIR=$POD_VOLUME/minio#' .env \
      || echo 'MINIO_DATA_DIR=$POD_VOLUME/minio' >> .env ; \
    grep -q '^VOLUME_PGDATA=' .env \
      || echo 'VOLUME_PGDATA=$VOLUME_PGDATA' >> .env ; \
    pm2 delete minio >/dev/null 2>&1 ; \
    pm2 start ecosystem.config.cjs --only minio >/dev/null 2>&1 ; \
    pm2 save >/dev/null 2>&1 ; sleep 8 ; \
    if [ -d '$POD_VOLUME/minio/.minio.sys' ]; then \
      echo 'MinIO backend on the volume: OK' ; \
    else \
      echo 'MinIO did NOT initialise on $POD_VOLUME/minio — it is writing somewhere else' ; \
      pm2 describe minio | grep 'script args' ; exit 1 ; \
    fi" \
    || warn "MinIO is not using the volume — objects will be lost on gpu-destroy. Check 'pm2 logs minio'"
fi

# ── Dispatcher serverless (tuỳ chọn) ──────────────────────────────────────────
# Đăng ký bằng `pm2 start <script>` chứ không thêm vào ecosystem.config.cjs: dispatcher là tuỳ chọn,
# bật/tắt theo .env, nên đừng nhồi vào file khai báo mọi tiến trình bắt buộc.
#
# Cổng kiểm PHẢI đòi thấy dòng "bắt đầu", không chỉ đòi VẮNG dòng lỗi. Phiên bản cũ chỉ grep
# 'tick lỗi:' và vì thế XANH khi log RỖNG HOÀN TOÀN — đúng cái đã xảy ra 02/08/2026: pm2 đặt
# argv[1]=ProcessContainerFork.js nên main() không bao giờ chạy, pm2 báo online, không một dòng log,
# cổng này qua. Vắng lỗi không phải là bằng chứng chạy được.
#
# DATABASE_URL: ecosystem.config.cjs:9 ghi rõ api/wf-worker chỉ đọc process.env (không dotenv), và
# ecosystem.config.cjs:62 tự DẪN XUẤT DATABASE_URL từ POSTGRES_USER/PASSWORD/PORT/DB trong .env —
# biến này KHÔNG có sẵn trong .env, chỉ tồn tại sau khi ecosystem.config.cjs tính ra. Thiếu nó thì
# pg.Pool trong db.js rơi về default connection (localhost, user hệ điều hành, không mật khẩu), lỗi
# liên tục nhưng bị catch trong tick() nuốt — trong khi `pm2 status` vẫn báo online mãi mãi. Lấy
# đúng giá trị bằng cách require lại chính ecosystem.config.cjs TRÊN POD (nó tự đọc
# ~/motion-backend/.env, khác .env gốc ở máy local) thay vì chép công thức ra đây lần hai — lệch
# một chi tiết (thứ tự host/port, tên biến…) là lại thêm một lỗi im lặng khác.
if [ "$WORKER_SOURCE" = "local" ]; then
  log "WORKER_SOURCE=local → không bật dispatcher; worker trên pod claim mọi job trong JOB_TYPES"
else
  # Năm biến DISPATCH_* phải được CHUYỂN sang pm2 bằng tay. `pm2 start <script>` không đọc file .env
  # nào cả (chỉ ecosystem.config.cjs mới có `env:`), nên trước đây đặt chúng trong .env gốc là một
  # no-op im lặng: .env.example mô tả chúng đầy đủ, cổng kiểm ở dưới còn đọc DISPATCH_JOB_TYPES để
  # so khớp, trong khi dispatcher trên pod luôn chạy bằng default gốc. Nguy nhất là
  # DISPATCH_ORPHAN_SEC=0 ("tắt hẳn" theo .env.example) vẫn reclaim job ở 900 giây.
  # Chỉ chuyển biến THỰC SỰ được đặt — để trống nghĩa là dùng default của mc-dispatcher.js, và
  # default phải sống đúng một chỗ. Với DISPATCH_ORPHAN_SEC thì "0" và "" là hai ý khác nhau.
  DISPATCH_ENV=""
  for _k in DISPATCH_JOB_TYPES DISPATCH_MAX_INFLIGHT DISPATCH_ORPHAN_SEC DISPATCH_POLL_SEC DISPATCH_COOLDOWN_SEC; do
    eval "_v=\${$_k}"
    [ -n "$_v" ] || continue
    # Giá trị đi thẳng vào một lệnh chạy qua ssh. Chỉ nhận chữ-số-phẩy để không có đường nào cho
    # dấu nháy hay ; đi lạc vào shell trên pod.
    case "$_v" in
      *[!A-Za-z0-9,_.-]*) die "$_k='$_v' có ký tự không hợp lệ — chỉ nhận chữ, số, và , _ . -" ;;
    esac
    DISPATCH_ENV="$DISPATCH_ENV $_k='$_v'"
    log "  dispatcher: $_k=$_v"
  done

  log "starting mc-dispatcher (serverless) — endpoint $RUNPOD_ENDPOINT_ID"
  remote "cd ~/$REMOTE_DIR && \
    DBURL=\$(node -e \"console.log(require('./ecosystem.config.cjs').apps.find(a=>a.name==='api').env.DATABASE_URL)\" 2>/dev/null) ; \
    if [ -z \"\$DBURL\" ]; then echo 'mc-dispatcher: không tính được DATABASE_URL từ ecosystem.config.cjs — bỏ qua'; exit 1; fi ; \
    pm2 delete mc-dispatcher >/dev/null 2>&1 ; \
    DATABASE_URL=\"\$DBURL\" RUNPOD_ENDPOINT_ID='$RUNPOD_ENDPOINT_ID' RUNPOD_API_KEY='$RUNPOD_API_KEY_ENV'$DISPATCH_ENV \
    pm2 start api/src/mc-dispatcher.js --name mc-dispatcher --update-env >/dev/null 2>&1 ; \
    pm2 save >/dev/null 2>&1 ; sleep 6 ; \
    if ! pm2 logs mc-dispatcher --lines 100 --nostream 2>/dev/null | grep -q '\[mc-dispatcher\] bắt đầu'; then \
      echo 'mc-dispatcher: KHÔNG thấy dòng \"bắt đầu\" trong log — tiến trình lên nhưng main() chưa chạy.' ; \
      pm2 logs mc-dispatcher --lines 100 --nostream ; \
      exit 1 ; \
    fi ; \
    if pm2 logs mc-dispatcher --lines 100 --nostream 2>/dev/null | grep -q 'tick lỗi:'; then \
      echo 'mc-dispatcher: log có \"tick lỗi:\" — DATABASE_URL sai hoặc Postgres không kết nối được. Log gần nhất:' ; \
      pm2 logs mc-dispatcher --lines 100 --nostream ; \
      exit 1 ; \
    fi ; \
    pm2 jlist | python3 -c \"import sys,json;m=[p for p in json.load(sys.stdin) if p['name']=='mc-dispatcher'];print('mc-dispatcher', m[0]['pm2_env']['status'] if m else 'MISSING')\"" \
    || warn "mc-dispatcher không start được hoặc không kết nối được database — xem 'pm2 logs mc-dispatcher'"

  # MỘT nguồn worker cho MỘT job type (spec §Kiến trúc). Để hai nguồn cùng claim CÙNG một type
  # thì hỏng theo kiểu tốn tiền mà không ai thấy: worker local trên pod đang chạy sẵn nhặt job
  # trong vài mili-giây, container serverless vẫn tỉnh dậy sau 1-3 phút cold start, thấy hàng đợi
  # rỗng, thoát — và ta trả tiền cold start đó cho không. Hoá đơn tăng, log hai bên đều sạch.
  # `pm2 stop` chứ không `pm2 delete`: giữ nguyên trong danh sách để bật lại bằng một lệnh.
  if [ "$WORKER_SOURCE" = "both" ]; then
    log "WORKER_SOURCE=both → giữ worker local chạy song song dispatcher"
    warn "both chỉ an toàn khi hai bên nhận nhóm type RỜI NHAU. Kiểm bằng tay:"
    warn "  pod:        JOB_TYPES trong ~/$REMOTE_DIR/.env"
    warn "  serverless: DISPATCH_JOB_TYPES (.env) và ENV JOB_TYPES bake trong image endpoint đang dùng"
    warn "  Giao nhau khác rỗng = trả tiền cold start cho những lần serverless dậy tay không."
  else
    log "dừng worker local — serverless là nguồn worker (WORKER_SOURCE=both để giữ cả hai)"
    remote "cd ~/$REMOTE_DIR && pm2 stop worker >/dev/null 2>&1 ; pm2 save >/dev/null 2>&1 ; \
      pm2 jlist | python3 -c \"import sys,json;m=[p for p in json.load(sys.stdin) if p['name']=='worker'];print('worker', m[0]['pm2_env']['status'] if m else 'MISSING')\"" \
      || warn "không dừng được worker local — kiểm 'pm2 status' rồi 'pm2 stop worker' bằng tay"
  fi

  # Cổng chặn lỗi im lặng: type nào pod claim được mà serverless KHÔNG, thì với WORKER_SOURCE=
  # serverless nó nằm 'queued' vĩnh viễn — không lỗi, không log, chỉ là không ai nhận. Đúng cái
  # xảy ra nếu mở SETUP_PROFILE=full mà vẫn trỏ endpoint dùng image 4 type.
  if [ "$WORKER_SOURCE" = "serverless" ]; then
    POD_TYPES="$(remote "grep -E '^JOB_TYPES=' ~/$REMOTE_DIR/.env | cut -d= -f2-" 2>/dev/null | tr -d '\r')"
    # Default lấy từ lib chứ không gõ lại: cổng này so khớp cái mà dispatcher THẬT SỰ dùng, nên
    # một danh sách chép tay lệch đi sẽ làm cổng báo "khớp" trong khi thực tế không khớp.
    DISPATCH_TYPES="${DISPATCH_JOB_TYPES:-$DS_DEFAULT_JOB_TYPES}"
    MISSING="$(python3 -c "
import sys
pod=[t for t in sys.argv[1].split(',') if t.strip()]
sv={t.strip() for t in sys.argv[2].split(',') if t.strip()}
print(','.join(t for t in pod if t.strip() not in sv))" "$POD_TYPES" "$DISPATCH_TYPES" 2>/dev/null)"
    if [ -n "$MISSING" ]; then
      warn "JOB_TYPES của pod có type mà dispatcher KHÔNG gửi đi: $MISSING"
      warn "  Job thuộc các type đó sẽ nằm 'queued' mãi mãi (không worker nào nhận, không báo lỗi)."
      warn "  Sửa một trong hai: dùng image serverless bản full và mở DISPATCH_JOB_TYPES cho khớp,"
      warn "  hoặc đặt WORKER_SOURCE=both để worker trên pod gánh đúng những type còn lại."
    fi
  fi
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
# Deliberately NOT deployed here anymore. build-frontend.yml only triggers on changes under
# motions/, so a commit that only touches backend/infra has no successful artifact for its SHA —
# scripts/pod-fe.sh would then fail every single time, making gpu-bootstrap exit non-zero even
# though the backend just finished installing cleanly. A command that always ends in red teaches
# you to ignore the red, and the one time the backend is actually broken slips right through.
# Deploy the frontend on its own with: make gpu-fe
if [ -n "$FE_DOMAIN" ]; then
  APP_URL="https://$FE_DOMAIN"
  echo "  backend is up. FE_DOMAIN is set — deploy the frontend with:   make gpu-fe"
else
  APP_URL="http://localhost:2030"
  echo "  restart the FE dev server to pick it up:   make down && make dev"
fi

echo "  then load the model group (NOT downloaded by setup, ~33GB):"
echo "    $APP_URL → login as $SUPER_ADMIN → Settings → Models AI → 'Wan 2.2 Animate' → Cài cả nhóm"
echo
