#!/usr/bin/env bash
#
# A/B trị "biểu cảm mặt kém" của Motion Control (17/08/2026):
#   triệu chứng — nhân vật há mồm to khi driver chỉ nhép theo nhạc; mắt nhắm tịt khi driver
#   chỉ hạ mắt nhìn xuống. Tăng faceStrength trên UI không đỡ.
#   giả thuyết chính — distill 4-step (lightx2v 1.0) bám conditioning mặt yếu → biểu cảm rơi
#   về prior hai cực đóng/mở (đúng họ bệnh "chu mỏ" đã trị bằng 20-step, xem linux.py ~2003).
#   giả thuyết phụ — mặt quá nhỏ ở 540p (mí mắt ~1-2px latent) và 16fps alias khẩu hình.
#
# MA TRẬN (cùng ref + driver, seed cố định 42 trong builder → so sánh sạch):
#   A  fast-baseline   540p 16fps 81f            — đối chứng
#   C  fast-720p       720p 16fps 81f            — biến số RESOLUTION
#   D  fast-30fps      540p 30fps 161f           — biến số FPS (alias khẩu hình)
#   E  fast-facelock   540p 16fps 81f fS=1.0+faceLock — bám nhịp mạnh + đắp identity (cần ~/facelock trên pod)
#   B  max20           540p 16fps 81f            — biến số STEPS/DISTILL (giả thuyết chính)
#   F  max20-720p      720p 16fps 81f            — opt-in (AB_MAX720=1), chỉ chạy nếu B khá hơn A
#
# VÌ SAO CÓ 2 PHASE: API ép renderProfile="fast" cho MỌI job motion (api/src/routes/jobs.js:36)
#   → không thể bật 20-step per-job qua payload. Đường A/B duy nhất là env MOTION_FORCE_QUALITY=1
#   trên worker (worker/worker_runtime/linux.py ~2015, env vào worker qua .env → ecosystem base).
#   Script tự SSH bật env → chạy phase max → TỰ TẮT lại (trap EXIT) để job thường sau đó không
#   dính 20-step ngoài ý muốn. Worker đọc env mỗi job nên chỉ cần startOrReload, không cần đợi lâu.
#
# DÙNG (pod đã bootstrap, giống make gpu-smoke):
#   AB_REF=ref.jpg AB_DRIVER=driver.mp4 scripts/ab-face-expression.sh          # cả 2 phase (A,C,D,E rồi B[,F])
#   AB_REF=... AB_DRIVER=... scripts/ab-face-expression.sh fast               # chỉ phase fast (A,C,D,E)
#   AB_REF=... AB_DRIVER=... scripts/ab-face-expression.sh max                # chỉ phase max20 (B[,F])
#   AB_MAX720=1 ... scripts/ab-face-expression.sh                             # thêm job F
#   AB_TIMEOUT_MIN=60 ...                                                     # trần chờ mỗi job (mặc định 45)
#
# YÊU CẦU driver ≥ 6s (job D lấy 161 frame @30fps ≈ 5.4s). Kết quả tải về ab-results/<timestamp>/
# kèm manifest.tsv (job id, params, thời gian chạy) — job B phải LÂU hơn A rõ rệt (~4-6×);
# nếu B xong nhanh ngang A tức env chưa vào worker, script sẽ cảnh báo.
#
set -uo pipefail
cd "$(dirname "$0")/.."; ROOT="$(pwd)"

log()  { printf '\n\033[36m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[32m  ✓\033[0m %s\n' "$*"; }
warn() { printf '\033[33m !!\033[0m %s\n' "$*"; }
bad()  { printf '\033[31m ✗ \033[0m%s\n' "$*"; }

env_get() { grep -E "^$1=" "$ROOT/.env" 2>/dev/null | cut -d= -f2- | sed -E 's/[[:space:]]*#.*$//' | tr -d '"'; }

PHASE="${1:-all}"
case "$PHASE" in all|fast|max) ;; *) bad "phase phải là: all | fast | max"; exit 1 ;; esac

DOMAIN="$(env_get DOMAIN)"
HOST="$(env_get GPU_SSH_HOST)"; PORT="$(env_get GPU_SSH_PORT)"
TIMEOUT_MIN="${AB_TIMEOUT_MIN:-45}"
[ -n "$DOMAIN" ] || { bad "DOMAIN trống trong .env — pod đã bootstrap chưa? (make gpu-bootstrap)"; exit 1; }
[ -n "${AB_REF:-}" ]    && [ -f "$AB_REF" ]    || { bad "AB_REF chưa set hoặc file không tồn tại"; exit 1; }
[ -n "${AB_DRIVER:-}" ] && [ -f "$AB_DRIVER" ] || { bad "AB_DRIVER chưa set hoặc file không tồn tại"; exit 1; }

remote() { ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -p "$PORT" "root@$HOST" "$1" 2>/dev/null; }

# API key: giống pod-smoke — ưu tiên motions/.env, fallback đọc từ pod qua SSH.
KEY="$(grep -E '^NUXT_MOTION_API_KEY=' "$ROOT/motions/.env" 2>/dev/null | cut -d= -f2- | tr -d '"')"
if [ -z "$KEY" ] && [ -n "$HOST" ] && [ -n "$PORT" ]; then
  KEY="$(remote "grep -E '^API_KEY=' ~/motion-backend/.env | cut -d= -f2-" | tr -d '\r\n')"
fi
[ -n "$KEY" ] || { bad "không tìm được API key (motions/.env NUXT_MOTION_API_KEY, hoặc SSH đọc pod)"; exit 1; }

OUT_DIR="$ROOT/ab-results/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUT_DIR"
MANIFEST="$OUT_DIR/manifest.tsv"
printf 'label\tjob_id\tstatus\telapsed_sec\tparams\n' > "$MANIFEST"

# Thời gian job A (fast baseline) — dùng làm mốc kiểm tra env max20 có thật sự vào worker không.
FAST_BASELINE_SEC=""

# submit_and_wait <label> <params-json> — submit, poll tới done/error, tải mp4 về OUT_DIR/<label>.mp4
submit_and_wait() {
  local label="$1" params="$2"
  log "job $label — $params"
  local t0; t0=$(date +%s)
  local job job_id
  job="$(curl -s --max-time 120 -X POST "https://$DOMAIN/jobs" -H "x-api-key: $KEY" \
        -F "type=motion" -F "params=$params" -F "ref=@$AB_REF" -F "motion=@$AB_DRIVER")"
  job_id="$(printf '%s' "$job" | python3 -c 'import json,sys; print((json.load(sys.stdin) or {}).get("id",""))' 2>/dev/null)"
  if [ -z "$job_id" ]; then
    bad "POST /jobs không trả id: $(printf '%s' "$job" | head -c 300)"
    printf '%s\t-\tsubmit_failed\t0\t%s\n' "$label" "$params" >> "$MANIFEST"
    return 1
  fi
  ok "queued $job_id — chờ tối đa ${TIMEOUT_MIN}m"
  local deadline=$(( $(date +%s) + TIMEOUT_MIN * 60 )) last="" st="?" pr step r
  while [ "$(date +%s)" -lt "$deadline" ]; do
    sleep 10
    r="$(curl -s --max-time 20 -H "x-api-key: $KEY" "https://$DOMAIN/jobs/$job_id")"
    read -r st pr step <<EOF2
$(printf '%s' "$r" | python3 -c '
import json,sys
try: d=json.load(sys.stdin) or {}
except Exception: d={}
print(d.get("status","?"), round((d.get("progress") or 0)*100), (d.get("current_step") or "-").replace(" ","_"))' 2>/dev/null)
EOF2
    [ "$st$pr" != "$last" ] && { printf '     %s %s%% %s\n' "$st" "$pr" "$step"; last="$st$pr"; }
    case "$st" in
      done)
        local elapsed=$(( $(date +%s) - t0 ))
        local sz
        sz="$(curl -s -o "$OUT_DIR/$label.mp4" -w '%{size_download}' --max-time 300 \
              -H "x-api-key: $KEY" "https://$DOMAIN/jobs/$job_id/download")"
        if [ "${sz:-0}" -gt 100000 ] 2>/dev/null; then
          ok "$label xong sau ${elapsed}s → $OUT_DIR/$label.mp4 ($((sz/1024)) KB)"
        else
          bad "$label: job done nhưng file chỉ ${sz:-0} bytes"
        fi
        printf '%s\t%s\tdone\t%s\t%s\n' "$label" "$job_id" "$elapsed" "$params" >> "$MANIFEST"
        [ "$label" = "A-fast-baseline" ] && FAST_BASELINE_SEC="$elapsed"
        return 0 ;;
      error|cancelled)
        bad "$label $st: $(printf '%s' "$r" | python3 -c 'import json,sys; print((json.load(sys.stdin) or {}).get("error","?"))' 2>/dev/null)"
        printf '%s\t%s\t%s\t%s\t%s\n' "$label" "$job_id" "$st" "$(( $(date +%s) - t0 ))" "$params" >> "$MANIFEST"
        return 1 ;;
    esac
  done
  bad "$label quá ${TIMEOUT_MIN}m (đang $st) — xem pm2 logs worker; job vẫn chạy tiếp trên pod"
  printf '%s\t%s\ttimeout\t%s\t%s\n' "$label" "$job_id" "$(( $(date +%s) - t0 ))" "$params" >> "$MANIFEST"
  return 1
}

# ── Bật/tắt MOTION_FORCE_QUALITY trên pod (đường A/B duy nhất — API scrub renderProfile) ──
set_force_quality() {
  local v="$1"
  [ -n "$HOST" ] && [ -n "$PORT" ] || { bad "thiếu GPU_SSH_HOST/GPU_SSH_PORT trong .env — không SSH bật env được"; return 1; }
  remote "cd ~/motion-backend && { grep -q '^MOTION_FORCE_QUALITY=' .env && sed -i 's/^MOTION_FORCE_QUALITY=.*/MOTION_FORCE_QUALITY=$v/' .env || echo 'MOTION_FORCE_QUALITY=$v' >> .env; } && pm2 startOrReload ecosystem.config.cjs --update-env >/dev/null 2>&1; grep '^MOTION_FORCE_QUALITY=' .env"
}

FQ_ON=0
cleanup() {
  if [ "$FQ_ON" = 1 ]; then
    log "cleanup — tắt lại MOTION_FORCE_QUALITY (job thường không được dính 20-step)"
    if [ "$(set_force_quality 0)" = "MOTION_FORCE_QUALITY=0" ]; then ok "đã tắt + reload worker"; else
      bad "KHÔNG tắt được qua SSH — TẮT TAY trên pod: sửa ~/motion-backend/.env rồi pm2 startOrReload ecosystem.config.cjs --update-env"
    fi
  fi
  log "kết quả: $OUT_DIR"
  column -t -s $'\t' "$MANIFEST" 2>/dev/null || cat "$MANIFEST"
}
trap cleanup EXIT

# ── Phase FAST (env mặc định — 4 bước distill): A đối chứng + 3 biến số phụ ──
if [ "$PHASE" != "max" ]; then
  log "PHASE FAST (4-step distill, env nguyên trạng)"
  # Kiểm tra queue rỗng để timing sạch (job khác chen giữa làm elapsed vô nghĩa).
  submit_and_wait "A-fast-baseline" '{"quality":"540p","frames":81,"render_fps":16}'
  submit_and_wait "C-fast-720p"     '{"quality":"720p","frames":81,"render_fps":16}'
  submit_and_wait "D-fast-30fps"    '{"quality":"540p","frames":161,"render_fps":30}'
  # E: faceStrength 1.0 = bám nhịp driver tốt nhất (A/B 21/07) nhưng lộ nét driver → faceLock đắp lại
  # identity mẫu (inswapper, linux.py ~413). Pod chưa cài ~/facelock thì worker tự warn + bỏ qua swap
  # → output = fast + faceStrength 1.0 trần, vẫn là một data point dùng được.
  submit_and_wait "E-fast-fs1-facelock" '{"quality":"540p","frames":81,"render_fps":16,"faceStrength":1.0,"faceLock":1}'
fi

# ── Phase MAX20 (giả thuyết chính): bật env → B (+F) → trap tự tắt ──
if [ "$PHASE" != "fast" ]; then
  log "PHASE MAX20 — bật MOTION_FORCE_QUALITY=1 (20 bước · lightx2v 0 · unipc, linux.py ~2016)"
  if [ "$(set_force_quality 1)" = "MOTION_FORCE_QUALITY=1" ]; then
    FQ_ON=1; ok "env đã bật + worker reload — job motion submit TỪ GIỜ chạy 20-step (kể cả từ UI!)"
  else
    bad "không bật được env qua SSH — bỏ phase max20"; exit 1
  fi
  submit_and_wait "B-max20-540p" '{"quality":"540p","frames":81,"render_fps":16}'
  # Env là công tắc toàn worker, không nằm trong params → xác minh bằng thời gian chạy:
  # 20 bước phải lâu hơn 4 bước rõ rệt. B ≈ A nghĩa là env KHÔNG vào worker (reload hụt).
  if [ -n "$FAST_BASELINE_SEC" ]; then
    B_SEC="$(awk -F'\t' '$1=="B-max20-540p"{print $4}' "$MANIFEST")"
    if [ -n "$B_SEC" ] && [ "$B_SEC" -lt $(( FAST_BASELINE_SEC * 2 )) ] 2>/dev/null; then
      warn "B (${B_SEC}s) không lâu hơn hẳn A (${FAST_BASELINE_SEC}s) — NGHI env chưa vào worker, kết quả B không tin được"
    fi
  fi
  if [ "${AB_MAX720:-0}" = "1" ]; then
    submit_and_wait "F-max20-720p" '{"quality":"720p","frames":81,"render_fps":16}'
  fi
fi
