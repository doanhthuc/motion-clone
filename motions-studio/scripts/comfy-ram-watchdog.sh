#!/usr/bin/env bash
# comfy-ram-watchdog.sh — ALD 15/06/2026 (v3: RECYCLE ComfyUI sau mỗi job để TRẢ RAM về OS)
# VẤN ĐỀ: xong job ComfyUI KHÔNG nhả RAM (torch/CUDA leak) — idle vẫn ôm 8-22GB; ComfyUI /free chỉ nhả VRAM,
#         RAM tiến trình vẫn giữ. Chỉ RESTART tiến trình mới trả RAM về OS. Để vậy → job sau cộng dồn → tràn RAM.
# GIẢI:  khi hàng đợi RỖNG (xong job, không còn job chạy/chờ) + ComfyUI đang ôm RAM (RSS ≥ RECYCLE_GB)
#        → comfy-stop.sh → supervisor (comfy-supervise.sh) tự dựng lại TƯƠI (~1.2GB) trong ~10s, sẵn cho job kế.
#        Job LIÊN TIẾP: queue BẬN → BỎ QUA (giữ instance ấm, TUYỆT ĐỐI không chém job). Vừa hết job → recycle.
# Chạy qua cron MỖI PHÚT (release nhanh sau job). comfy-stop reap CẢ orphan. Chỉ log khi RSS đáng kể (đỡ rác).
# Cài qua GIT: deploy.yml (block EOSSH) copy file này vào ~/ai/scripts/ + cron "* * * * *". comfy-stop.sh có sẵn trên host.
set -u
RECYCLE_GB="${COMFY_RECYCLE_GB:-6}"          # RSS ComfyUI (GB) idle ≥ mức này = đã nạp model cho job → recycle. Fresh ~1.2GB. Tunable.
COMFY="${COMFY_URL:-http://127.0.0.1:8188}"
LOG="${HOME}/ai/logs/comfy-ram-watchdog.log"
mkdir -p "$(dirname "$LOG")"
log(){ echo "$(date '+%F %T') $*" >> "$LOG"; }
# cap log ~800 dòng → giữ gần đây
if [ -f "$LOG" ] && [ "$(wc -l < "$LOG" 2>/dev/null || echo 0)" -gt 800 ]; then
  tail -n 500 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"
fi

# pgrep -f khớp CẢ bash wrapper (RSS thấp) lẫn python thật → LẤY process RSS LỚN NHẤT (python ComfyUI), gồm orphan.
read -r rss_kb pid < <(for p in $(pgrep -f "ComfyUI.*main.py"); do
  r=$(ps -o rss= -p "$p" 2>/dev/null | tr -d ' '); [ -n "$r" ] && echo "$r $p"
done | sort -rn | head -1)
{ [ -z "${pid:-}" ] || [ -z "${rss_kb:-}" ]; } && exit 0   # ComfyUI không chạy → supervisor lo
rss_gb=$(( rss_kb / 1048576 ))

# CHƯA ôm RAM nhiều (fresh/idle ~1-3GB) → không cần làm gì, khỏi log cho đỡ rác.
[ "$rss_gb" -lt "$RECYCLE_GB" ] && exit 0

# Đang ôm RAM (≥ RECYCLE_GB) → CHỈ recycle khi queue RỖNG. Parse lỗi/không đọc được → coi BẬN (an toàn, giữ job).
busy=$(curl -s --max-time 6 "$COMFY/queue" 2>/dev/null | python3 -c "import sys,json
try:
  d=json.load(sys.stdin); print(1 if (d.get('queue_running') or d.get('queue_pending')) else 0)
except Exception: print(1)" 2>/dev/null)
[ -z "$busy" ] && busy=1

if [ "$busy" = "0" ]; then
  log "IDLE + RSS=${rss_gb}GB ≥ ${RECYCLE_GB}GB (xong job, ôm RAM) → comfy-stop; supervisor dựng lại fresh (~1.2GB)"
  bash "${HOME}/ai/scripts/comfy-stop.sh" >> "$LOG" 2>&1
  log "→ đã recycle; RAM trả về OS"
else
  log "RSS=${rss_gb}GB cao nhưng queue BẬN → BỎ QUA (giữ job, dùng instance ấm)"
fi
