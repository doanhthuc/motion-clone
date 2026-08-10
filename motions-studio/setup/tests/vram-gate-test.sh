#!/usr/bin/env bash
# vram-gate-test.sh — test motion_autoset_vram_gate() (setup/lib-gpu.sh) trên MÁY DEV, không cần GPU.
#
#   bash motions-studio/setup/tests/vram-gate-test.sh
#
# Không cần gì ngoài bash. nvidia-smi được giả bằng một script 3 dòng trên PATH, nên test được
# cả những card không có trong tay (A40 48GB) và cả ca "không có nvidia-smi".
#
# Vì sao đáng test: hàm này quyết định clip NGẮN đi đường model-on-VRAM (cần 29,9GB, đo trên 5090)
# hay đường offload. Chọn sai trên card 24GB = CUDA OOM. Mà ca đó chỉ xảy ra khi bạn đang thuê đúng
# một con 4090 — tức đúng lúc không muốn phát hiện ra bug. Xem docs/gpu-pod.md#gpu-4090.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
PASS=0; FAIL=0
OUTFILE="$(mktemp)"
trap 'rm -f "$OUTFILE"' EXIT

# Stub các hàm mà installer cung cấp cho lib-gpu.sh (set_kv ghi vào .env thật trên pod).
ok()     { echo "    ok: $*" >&2; }
warn()   { echo "    warn: $*" >&2; }
set_kv() { echo "$1=$2" >> "$OUTFILE"; }
get_kv() { grep -E "^$1=" "$OUTFILE" 2>/dev/null | tail -1 | cut -d= -f2-; }

# shellcheck source=../lib-gpu.sh
source "$ROOT/setup/lib-gpu.sh"

# $1 = MiB mà nvidia-smi giả trả về ('' = không có nvidia-smi trên PATH)
# $2 = MOTION_VRAM_MAX_FRAMES ép tay từ .env gốc ('' = không ép)
run_gate() {
  : > "$OUTFILE"
  local d; d="$(mktemp -d)"
  if [ -n "$1" ]; then
    printf '#!/bin/sh\necho %s\n' "$1" > "$d/nvidia-smi"
    chmod +x "$d/nvidia-smi"
  fi
  if [ -n "$2" ]; then
    ( export MOTION_VRAM_MAX_FRAMES="$2"; PATH="$d:/usr/bin:/bin"; motion_autoset_vram_gate ) 2>/dev/null
  else
    ( unset MOTION_VRAM_MAX_FRAMES; PATH="$d:/usr/bin:/bin"; motion_autoset_vram_gate ) 2>/dev/null
  fi
  rm -rf "$d"
  local v; v="$(grep -E '^MOTION_VRAM_MAX_FRAMES=' "$OUTFILE" 2>/dev/null | tail -1 | cut -d= -f2-)"
  [ -n "$v" ] && echo "$v" || echo "<không ghi>"
}

check() {  # $1 nhãn, $2 mong đợi, $3 thực tế
  if [ "$2" = "$3" ]; then printf 'PASS  %-46s (=%s)\n' "$1" "$3"; PASS=$((PASS+1))
  else printf 'FAIL  %-46s mong đợi=%s thực tế=%s\n' "$1" "$2" "$3"; FAIL=$((FAIL+1)); fi
}

# Số MiB là số THẬT nvidia-smi báo, không phải số tròn: 4090 và 5090 đo trên pod 10/08/2026.
check "4090 24081MiB → ép offload"          "0"            "$(run_gate 24081 '')"
check "5090 32109MiB → giữ đường nhanh"     "250"          "$(run_gate 32109 '')"
check "A40 49140MiB → giữ đường nhanh"      "250"          "$(run_gate 49140 '')"
check "sát ngưỡng 30999MiB → ép offload"    "0"            "$(run_gate 30999 '')"
check "sát ngưỡng 31000MiB → đường nhanh"   "250"          "$(run_gate 31000 '')"
check "ép tay thắng card to (0 trên 32GB)"  "0"            "$(run_gate 32109 0)"
check "ép tay thắng card nhỏ (999/24GB)"    "999"          "$(run_gate 24081 999)"
check "không có nvidia-smi → không ghi gì"  "<không ghi>"  "$(run_gate '' '')"

echo
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
