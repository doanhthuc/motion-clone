#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
# preload-models.sh — tải model thẳng vào Network Volume, KHÔNG cần GPU và không cần app chạy.
#
# Chạy TRÊN một pod có volume mount (pod CPU rẻ tiền là đủ — tải model là việc mạng + đĩa, GPU
# nằm không cũng vẫn tính tiền). Xem docs/gpu-pod.md#preload cho cách thuê pod CPU.
#
#   POD_VOLUME=/workspace ./setup/preload-models.sh --list
#   POD_VOLUME=/workspace ./setup/preload-models.sh --group "Qwen-Image-Edit"
#   POD_VOLUME=/workspace ./setup/preload-models.sh --id qwen-edit-q8 --id qwen-vae
#   POD_VOLUME=/workspace ./setup/preload-models.sh --all          # 245GB — đọc cảnh báo dung lượng
#
# Vì sao script này tồn tại thay vì bấm Settings → Models AI: đường trong app đòi cả stack chạy
# (Postgres cho bảng model_downloads, api, và một GPU pod đang tính tiền $1/giờ chỉ để ngồi tải).
# Đường tải thì y hệt — models-install.js:93-95 ghi vào <COMFY_MODELS_DIR>/<type>/<filename> bằng
# aria2c, đúng ba thứ script này làm. Cùng đích đến, không cần GPU.
#
# CÙNG catalog với app (comfyui/catalog.json) nên không có danh sách URL thứ hai để lệch nhau.
# ════════════════════════════════════════════════════════════════════════════
set -uo pipefail
cd "$(dirname "$0")/.."; ROOT="$(pwd)"

say()  { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m  ✓ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m  ! %s\033[0m\n' "$*"; }
die()  { printf '\n\033[1;31m  ✗ %s\033[0m\n' "$*"; exit 1; }

CATALOG="${CATALOG:-$ROOT/comfyui/catalog.json}"
VOL="${POD_VOLUME:-}"
MODE=""; SEL_GROUPS=(); SEL_IDS=(); DRY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --list)  MODE=list ;;
    --all)   MODE=all ;;
    --group) shift; SEL_GROUPS+=("${1:-}"); MODE="${MODE:-select}" ;;
    --id)    shift; SEL_IDS+=("${1:-}");    MODE="${MODE:-select}" ;;
    --dry-run) DRY=1 ;;
    *) die "tham số lạ: $1" ;;
  esac
  shift
done
[ -n "$MODE" ] || die "cần một trong: --list | --all | --group <tên> | --id <id>"
[ -f "$CATALOG" ] || die "không thấy catalog: $CATALOG"

# ── Liệt kê: không đụng volume, chạy được cả trên máy local ──────────────────
if [ "$MODE" = list ]; then
  python3 - "$CATALOG" <<'PY'
import json,sys,collections
d=json.load(open(sys.argv[1]))
g=collections.OrderedDict()
for e in d["comfy"]:
    g.setdefault(e["group"], [0,0])
    g[e["group"]][0]+=e.get("sizeBytes",0); g[e["group"]][1]+=1
tot=0
print(f'{"GB":>8}  {"file":>4}  nhóm')
for name,(b,n) in sorted(g.items(), key=lambda x:-x[1][0]):
    print(f"{b/1e9:8.1f}  {n:4d}  {name}"); tot+=b
print(f'{"-"*8}')
print(f"{tot/1e9:8.1f}  TỔNG")
PY
  exit 0
fi

[ -n "$VOL" ] || die "cần POD_VOLUME=<đường mount volume>, vd POD_VOLUME=/workspace"
[ -d "$VOL" ] || die "$VOL không tồn tại — volume chưa mount?"
command -v aria2c >/dev/null 2>&1 || {
  say "cài aria2 …"
  (apt-get update -qq && apt-get install -y -qq aria2) >/dev/null 2>&1 \
    || die "không cài được aria2c (apt-get install aria2)"
}

MODELS="$VOL/comfy-models"
mkdir -p "$MODELS" || die "không tạo được $MODELS"

# ── Chọn mục cần tải, và CHECK DUNG LƯỢNG TRƯỚC ─────────────────────────────
# Kiểm trước vì hỏng giữa chừng ở đây rất đắt: aria2c chạy 40 phút rồi chết vì đầy đĩa để lại
# một đống .part, và lần chạy sau không biết file nào dở.
PLAN="$(mktemp)"; trap 'rm -f "$PLAN"' EXIT
python3 - "$CATALOG" "$MODELS" "$MODE" "$(printf '%s\n' "${SEL_GROUPS[@]:-}" | base64)" "$(printf '%s\n' "${SEL_IDS[@]:-}" | base64)" > "$PLAN" <<'PY'
import json,sys,os,base64
cat, models, mode = sys.argv[1], sys.argv[2], sys.argv[3]
groups={x for x in base64.b64decode(sys.argv[4]).decode().split("\n") if x.strip()}
ids   ={x for x in base64.b64decode(sys.argv[5]).decode().split("\n") if x.strip()}
d=json.load(open(cat))
sel=[e for e in d["comfy"] if mode=="all" or e["group"] in groups or e["id"] in ids]
if not sel:
    sys.exit(3)   # không in gì — shell in thông báo tiếng Việt kèm gợi ý --list
unknown = (groups - {e["group"] for e in d["comfy"]}) | (ids - {e["id"] for e in d["comfy"]})
for u in sorted(unknown): print(f"UNKNOWN\t{u}")
for e in sel:
    dest=os.path.join(models, e["type"], e["filename"])
    have=os.path.getsize(dest) if os.path.exists(dest) else -1
    want=e.get("sizeBytes",0)
    state = "SKIP" if have==want and want>0 else ("PARTIAL" if have>=0 else "GET")
    print(f"{state}\t{e['id']}\t{e['type']}\t{e['filename']}\t{want}\t{have}\t{e['url']}")
PY
rc=$?
[ "$rc" -eq 3 ] && die "không mục nào khớp lựa chọn — xem './setup/preload-models.sh --list'"
[ "$rc" -eq 0 ] || die "đọc catalog lỗi"

awk -F'\t' '$1=="UNKNOWN"{print "  ! không có trong catalog: " $2}' "$PLAN"

NEED=$(awk -F'\t' '$1=="GET"||$1=="PARTIAL"{s+=$5-($6>0?$6:0)} END{print s+0}' "$PLAN")
NGET=$(awk -F'\t' '$1=="GET"||$1=="PARTIAL"{n++} END{print n+0}' "$PLAN")
NSKIP=$(awk -F'\t' '$1=="SKIP"{n++} END{print n+0}' "$PLAN")
AVAIL=$(df -B1 --output=avail "$MODELS" 2>/dev/null | tail -1 | tr -d ' ')
AVAIL="${AVAIL:-0}"

say "kế hoạch"
printf '  cần tải : %d file, %.1f GB\n' "$NGET" "$(echo "$NEED" | awk '{print $1/1e9}')"
printf '  đã có   : %d file\n' "$NSKIP"
printf '  trống   : %.1f GB trên %s\n' "$(echo "$AVAIL" | awk '{print $1/1e9}')" "$VOL"

if [ "$AVAIL" -gt 0 ] && [ "$NEED" -gt "$AVAIL" ]; then
  die "KHÔNG ĐỦ CHỖ: cần $(echo "$NEED"|awk '{printf "%.1f", $1/1e9}')GB, còn $(echo "$AVAIL"|awk '{printf "%.1f", $1/1e9}')GB.
  Nới volume:  runpodctl network-volume update <id> --size <GB lớn hơn hiện tại>
  (chỉ tăng được, và tăng rồi thì tính tiền theo mức mới hằng tháng — xem docs/gpu-pod.md#costs)
  Hoặc chọn ít nhóm hơn: ./setup/preload-models.sh --list"
fi
[ "$NGET" -eq 0 ] && { ok "không có gì để tải — tất cả đã nằm trên volume"; exit 0; }
[ "$DRY" = 1 ] && { ok "--dry-run: dừng ở đây"; exit 0; }

# ── Tải ──────────────────────────────────────────────────────────────────────
FAIL=0; DONE=0
while IFS=$'\t' read -r state id type filename want have url; do
  case "$state" in SKIP) ok "bỏ qua $filename (đã có, đúng cỡ)"; continue ;; UNKNOWN) continue ;; esac
  [ "$state" = PARTIAL ] && warn "$filename có sẵn nhưng SAI CỠ ($have ≠ $want) → tải lại"
  dir="$MODELS/$type"; mkdir -p "$dir"
  dest="$dir/$filename"; tmp="$dest.part"
  say "$id → $type/$filename  ($(echo "$want" | awk '{printf "%.1f", $1/1e9}') GB)"
  # -x16 -s16: đúng tham số app dùng. --continue: chạy lại sau khi đứt không mất phần đã tải.
  if ! aria2c -x16 -s16 --continue=true --auto-file-renaming=false --allow-overwrite=true \
        --summary-interval=30 --console-log-level=warn \
        -d "$dir" -o "$(basename "$tmp")" "$url"; then
    warn "aria2c lỗi ở $filename — bỏ qua, chạy lại script sau"; FAIL=$((FAIL+1)); continue
  fi
  # Kiểm CỠ trước khi rename. Đây là chỗ dễ hỏng im lặng nhất: HF trả 200 kèm trang HTML lỗi thì
  # aria2c vẫn exit 0 và ta có một file 'model' vài KB — ComfyUI chỉ báo lỗi ở job đầu tiên.
  got=$(stat -c %s "$tmp" 2>/dev/null || echo 0)
  if [ "$want" -gt 0 ] && [ "$got" != "$want" ]; then
    warn "$filename tải xong nhưng cỡ SAI: $got ≠ $want — giữ .part, KHÔNG rename"; FAIL=$((FAIL+1)); continue
  fi
  mv -f "$tmp" "$dest" && ok "xong $filename" && DONE=$((DONE+1))
done < "$PLAN"

say "kết quả"
printf '  tải xong: %d · lỗi: %d · đã có sẵn: %d\n' "$DONE" "$FAIL" "$NSKIP"
printf '  tổng comfy-models: %s\n' "$(du -sh "$MODELS" 2>/dev/null | cut -f1)"
[ "$FAIL" -eq 0 ] || die "$FAIL file chưa xong — chạy lại script, nó bỏ qua file đã đủ cỡ"
ok "volume sẵn sàng — pod sau dựng lên là có model, không phải tải lại"
