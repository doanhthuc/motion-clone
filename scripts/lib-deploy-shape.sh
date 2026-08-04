# shellcheck shell=bash
#
# Nguồn sự thật DUY NHẤT cho hình dạng deploy: SETUP_PROFILE (cài gì) và WORKER_SOURCE (ai claim job).
#
# Được source bởi HAI script với hai mục đích khác nhau:
#   scripts/pod-bootstrap.sh   thi hành  — dựng pod theo hình dạng này
#   scripts/gpu-preflight.sh   báo trước — nói hình dạng này ra TRƯỚC khi đồng hồ tiền chạy
#
# Vì sao tách file: trước đây chỉ pod-bootstrap.sh biết cách suy ra WORKER_SOURCE, nên
# `make gpu-preflight` in ra toàn màu xanh mà không hề nhắc tới hai biến quyết định pod sẽ dựng ra
# hình gì. Bạn thuê máy, chờ 30 phút bootstrap, rồi mới phát hiện worker local đã bị `pm2 stop`.
# Chép logic sang preflight là cách hiển nhiên và là cách sai: lệch một chi tiết giữa hai bản chép
# thì cổng kiểm báo một đằng, pod dựng một nẻo — đúng loại lỗi im lặng mà cả repo này đang chống.
#
# ĐẦU VÀO:  .env ở thư mục hiện tại · biến shell KEEP_LOCAL_WORKER (tên cũ, còn đỡ một nhịp)
# ĐẦU RA (biến toàn cục):
#   SETUP_PROFILE SETUP_SCRIPT SETUP_PROFILES_AVAILABLE
#   WORKER_SOURCE WORKER_SOURCE_ORIGIN        origin: env | suy-ra | keep-local-worker
#   RUNPOD_ENDPOINT_ID RUNPOD_API_KEY_ENV
#   DISPATCH_JOB_TYPES DISPATCH_MAX_INFLIGHT DISPATCH_ORPHAN_SEC DISPATCH_POLL_SEC DISPATCH_COOLDOWN_SEC
#     ^ giá trị THÔ từ .env: rỗng nghĩa là "không đặt", để mc-dispatcher.js tự dùng default của nó.
#       Đừng điền default vào đây rồi chuyển đi — làm thế là dựng chỗ thứ hai định nghĩa default.
#   DEPLOY_SHAPE_ERRORS[]   lỗi chặn: pod-bootstrap.sh sẽ chết vì những cái này
#   DEPLOY_SHAPE_WARNINGS[] cảnh báo: chạy được nhưng nhiều khả năng không như bạn tưởng
#
# Hàm này KHÔNG in gì và KHÔNG exit — caller quyết định trình bày thế nào và có chết hay không.

# Default HIỂN THỊ, chỉ để nói "không đặt thì sẽ là bao nhiêu". Bản gốc nằm trong
# motions-studio/api/src/mc-dispatcher.js; deploy_shape_check_drift() bên dưới canh hai bên khớp nhau.
DS_DEFAULT_JOB_TYPES="motion,teen-flycam,trend-tiktok,enhance"
DS_DEFAULT_MAX_INFLIGHT=3
DS_DEFAULT_ORPHAN_SEC=900
DS_DEFAULT_POLL_SEC=5
DS_DEFAULT_COOLDOWN_SEC=180

_ds_env_get() {
  grep -E "^$1=" .env 2>/dev/null | cut -d= -f2- | sed -E 's/[[:space:]]*#.*$//' | tr -d '"'
}

resolve_deploy_shape() {
  DEPLOY_SHAPE_ERRORS=()
  DEPLOY_SHAPE_WARNINGS=()

  # ── COMPUTE_TYPE: box có GPU hay không ──────────────────────────────────────
  # Câu hỏi thứ ba, độc lập với hai câu dưới. Mặc định gpu = đường cũ, không đổi gì.
  COMPUTE_TYPE="$(_ds_env_get COMPUTE_TYPE)"
  COMPUTE_TYPE="$(printf '%s' "${COMPUTE_TYPE:-gpu}" | tr 'A-Z' 'a-z')"
  case "$COMPUTE_TYPE" in
    gpu|cpu) ;;
    *) DEPLOY_SHAPE_ERRORS+=("COMPUTE_TYPE=$COMPUTE_TYPE không hợp lệ — chỉ nhận: gpu | cpu") ;;
  esac
  if [ "$COMPUTE_TYPE" = "cpu" ] && [ "$(_ds_env_get GPU_PROVIDER)" != "runpod" ]; then
    DEPLOY_SHAPE_ERRORS+=("COMPUTE_TYPE=cpu chỉ có trên RunPod — vast.ai là chợ GPU, không bán máy CPU thuần.")
  fi

  # ── SETUP_PROFILE ───────────────────────────────────────────────────────────
  # Chỉ nhận profile đi qua lib-feature.sh. setup-pm2.sh cũng nằm cùng thư mục và cũng cài được cả
  # stack, nhưng nó là monolith cũ đi đường KHÁC: không có chuỗi phase, không hiểu MTC_PREBUILT, tự
  # quản JOB_TYPES/catalog theo cách riêng. Cho nó lọt vào thì `make gpu-bootstrap` lặng lẽ chạy một
  # cài đặt khác hẳn cái mọi cổng kiểm phía sau giả định. setup-full.sh là bản lib-feature của nó.
  SETUP_PROFILE="$(_ds_env_get SETUP_PROFILE)"; SETUP_PROFILE="${SETUP_PROFILE:-motion-transfer}"
  SETUP_SCRIPT="setup/setup-${SETUP_PROFILE}.sh"
  SETUP_PROFILES_AVAILABLE="$(cd motions-studio/setup 2>/dev/null && \
    grep -l 'lib-feature.sh' setup-*.sh 2>/dev/null | sed 's/^setup-//;s/\.sh$//' | paste -sd' ' -)"
  case " $SETUP_PROFILES_AVAILABLE " in
    *" $SETUP_PROFILE "*) ;;
    *) DEPLOY_SHAPE_ERRORS+=("SETUP_PROFILE=$SETUP_PROFILE không dùng được. Profile có sẵn: $SETUP_PROFILES_AVAILABLE") ;;
  esac
  [ -f "motions-studio/$SETUP_SCRIPT" ] || \
    DEPLOY_SHAPE_ERRORS+=("thiếu motions-studio/$SETUP_SCRIPT")

  # ── WORKER_SOURCE ───────────────────────────────────────────────────────────
  RUNPOD_ENDPOINT_ID="$(_ds_env_get RUNPOD_ENDPOINT_ID)"
  RUNPOD_API_KEY_ENV="$(_ds_env_get RUNPOD_API_KEY)"
  WORKER_SOURCE="$(_ds_env_get WORKER_SOURCE)"
  WORKER_SOURCE_ORIGIN=env

  if [ -n "${KEEP_LOCAL_WORKER:-}" ]; then
    # KEEP_LOCAL_WORKER là tên cũ, và nó có một lỗi: chỉ đọc được từ shell env, nên đặt trong .env
    # (đúng như .env.example từng hướng dẫn) bị bỏ qua IM LẶNG. WORKER_SOURCE đọc từ .env.
    DEPLOY_SHAPE_WARNINGS+=("KEEP_LOCAL_WORKER đã bỏ — dùng WORKER_SOURCE=both trong .env. Đang tạm ánh xạ giá trị cũ.")
    if [ "$KEEP_LOCAL_WORKER" = "1" ] && [ -z "$WORKER_SOURCE" ]; then
      WORKER_SOURCE=both; WORKER_SOURCE_ORIGIN=keep-local-worker
    fi
  fi

  if [ -z "$WORKER_SOURCE" ]; then
    WORKER_SOURCE_ORIGIN=suy-ra
    if [ -n "$RUNPOD_ENDPOINT_ID" ] && [ -n "$RUNPOD_API_KEY_ENV" ]; then
      WORKER_SOURCE=serverless
      # Đây là chỗ đắt nhất của việc để trống: hai biến RUNPOD_* điền từ đời nào cũng đủ để
      # bootstrap `pm2 stop worker`. Không ai đọc .env mà đoán ra được điều đó.
      DEPLOY_SHAPE_WARNINGS+=("WORKER_SOURCE trống → suy ra 'serverless' vì .env có đủ RUNPOD_ENDPOINT_ID + RUNPOD_API_KEY. Bootstrap sẽ DỪNG worker local trên pod. Đặt WORKER_SOURCE=local nếu muốn GPU của pod chạy job.")
    else
      WORKER_SOURCE=local
    fi
  fi

  case "$WORKER_SOURCE" in
    local|serverless|both) ;;
    *) DEPLOY_SHAPE_ERRORS+=("WORKER_SOURCE=$WORKER_SOURCE không hợp lệ — chỉ nhận: local | serverless | both") ;;
  esac

  if [ "$WORKER_SOURCE" != "local" ] && { [ -z "$RUNPOD_ENDPOINT_ID" ] || [ -z "$RUNPOD_API_KEY_ENV" ]; }; then
    DEPLOY_SHAPE_ERRORS+=("WORKER_SOURCE=$WORKER_SOURCE cần RUNPOD_ENDPOINT_ID và RUNPOD_API_KEY trong .env — xem docs/gpu-pod.md#serverless. (Muốn chạy job bằng GPU của chính pod thì đặt WORKER_SOURCE=local.)")
  fi

  # Cặp chết người: box không GPU mà lại giao job cho worker local. Không ai chạy được job nào, và
  # KHÔNG có triệu chứng nào — box lên xanh, /health trả lời, job nằm 'queued' vĩnh viễn. Chặn, chứ
  # không cảnh báo: `local` trên box CPU không có cách đọc nào là hợp lý.
  if [ "$COMPUTE_TYPE" = "cpu" ] && [ "$WORKER_SOURCE" = "local" ]; then
    DEPLOY_SHAPE_ERRORS+=("COMPUTE_TYPE=cpu + WORKER_SOURCE=local: box không có GPU mà job lại giao cho worker local → không job nào chạy được, và không báo lỗi gì. Đặt WORKER_SOURCE=serverless.")
  fi
  # Ngược lại thì chỉ là tiền, không phải lỗi — nhưng là tiền thật, nên phải nói ra.
  if [ "$COMPUTE_TYPE" = "gpu" ] && [ "$WORKER_SOURCE" = "serverless" ]; then
    DEPLOY_SHAPE_WARNINGS+=("Box CÓ GPU nhưng job đẩy sang serverless: GPU vừa thuê nằm không, và serverless đắt hơn pod 1,33-1,58× mỗi giây GPU. Chỉ hợp lý khi đang TEST đường serverless. Xem docs/gpu-pod.md#deploy-shapes.")
  fi
  # Profile cpu-box cài đúng cho box không GPU (bỏ comfyui/worker/task-cloud-auto). Ghép lệch với
  # COMPUTE_TYPE là hai kiểu hỏng khác nhau, cả hai đều im lặng.
  if [ "$COMPUTE_TYPE" = "cpu" ] && [ "$SETUP_PROFILE" != "cpu-box" ]; then
    DEPLOY_SHAPE_WARNINGS+=("COMPUTE_TYPE=cpu nhưng SETUP_PROFILE=$SETUP_PROFILE — profile đó bật PM2 app cần ComfyUI/GPU (task-cloud-auto sẽ crash-loop). Dùng SETUP_PROFILE=cpu-box.")
  fi
  if [ "$COMPUTE_TYPE" = "gpu" ] && [ "$SETUP_PROFILE" = "cpu-box" ]; then
    DEPLOY_SHAPE_WARNINGS+=("SETUP_PROFILE=cpu-box trên box CÓ GPU: profile này SKIP_COMFY=1 và không bật worker, nên GPU vừa thuê sẽ không được dùng.")
  fi

  # ── Tham số dispatcher ──────────────────────────────────────────────────────
  # Giá trị thô, KHÔNG điền default: pod-bootstrap.sh chỉ chuyển sang pm2 những biến thực sự được
  # đặt, để default sống đúng một chỗ là mc-dispatcher.js. Quan trọng với DISPATCH_ORPHAN_SEC, nơi
  # "0" (tắt hẳn) và "" (không đặt → 900) là hai ý khác nhau mà default hoá sẽ trộn làm một.
  DISPATCH_JOB_TYPES="$(_ds_env_get DISPATCH_JOB_TYPES)"
  DISPATCH_MAX_INFLIGHT="$(_ds_env_get DISPATCH_MAX_INFLIGHT)"
  DISPATCH_ORPHAN_SEC="$(_ds_env_get DISPATCH_ORPHAN_SEC)"
  DISPATCH_POLL_SEC="$(_ds_env_get DISPATCH_POLL_SEC)"
  DISPATCH_COOLDOWN_SEC="$(_ds_env_get DISPATCH_COOLDOWN_SEC)"
}

# Canh default hiển thị ở trên khớp với default thật trong mc-dispatcher.js. In ra tên biến nào
# lệch, không in gì nghĩa là khớp. Tồn tại vì bản sao dùng để hiển thị trôi khỏi bản gốc là chuyện
# xảy ra trong im lặng: cổng kiểm nói "mặc định 900" trong khi dispatcher đã đổi sang số khác.
deploy_shape_check_drift() {
  local f=motions-studio/api/src/mc-dispatcher.js
  [ -f "$f" ] || return 0
  local drift=""
  grep -q "DISPATCH_POLL_SEC || $DS_DEFAULT_POLL_SEC)"          "$f" || drift="$drift DISPATCH_POLL_SEC"
  grep -q "DISPATCH_MAX_INFLIGHT || $DS_DEFAULT_MAX_INFLIGHT)"  "$f" || drift="$drift DISPATCH_MAX_INFLIGHT"
  grep -q "DISPATCH_COOLDOWN_SEC || $DS_DEFAULT_COOLDOWN_SEC)"  "$f" || drift="$drift DISPATCH_COOLDOWN_SEC"
  grep -q "DEFAULT_ORPHAN_SEC = $DS_DEFAULT_ORPHAN_SEC"         "$f" || drift="$drift DISPATCH_ORPHAN_SEC"
  grep -q "DEFAULT_JOB_TYPES = \"$DS_DEFAULT_JOB_TYPES\""       "$f" || drift="$drift DISPATCH_JOB_TYPES"
  [ -n "$drift" ] && echo "${drift# }"
  return 0
}
