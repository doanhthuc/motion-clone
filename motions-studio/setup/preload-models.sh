#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
# preload-models.sh — tải model thẳng vào Network Volume, KHÔNG cần GPU và không cần app chạy.
#
# Chạy TRÊN một pod có volume mount (pod CPU rẻ tiền là đủ — tải model là việc mạng + đĩa, GPU
# nằm không cũng vẫn tính tiền). Xem docs/gpu-pod.md#preload cho cách thuê pod CPU.
#
# VOLUME_GB = quota Network Volume (runpodctl network-volume list → size). Thiếu nó thì cổng chặn
# "đủ chỗ không" TẮT — xem ghi chú df/MooseFS ở phần kế hoạch bên dưới.
#
#   POD_VOLUME=/workspace VOLUME_GB=100 ./setup/preload-models.sh --list
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
tot=0
for kind,key in (("ComfyUI","comfy"),("Ollama","ollama")):
    g=collections.OrderedDict()
    for e in d.get(key,[]):
        g.setdefault(e["group"], [0,0])
        g[e["group"]][0]+=e.get("sizeBytes",0); g[e["group"]][1]+=1
    if not g: continue
    print(f'\n{kind}\n{"GB":>8}  {"file":>4}  nhóm')
    for name,(b,n) in sorted(g.items(), key=lambda x:-x[1][0]):
        print(f"{b/1e9:8.1f}  {n:4d}  {name}"); tot+=b
print(f'\n{"-"*8}\n{tot/1e9:8.1f}  TỔNG (cả hai)')
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
import json,sys,os,base64,urllib.request

# Cỡ THẬT lấy từ Content-Length của chính máy chủ, KHÔNG tin sizeBytes trong catalog.
# Đo 02/08/2026: catalog lệch thật, và lệch cả hai chiều — qwen-vae ghi 257698037 còn HF trả
# 253806246 (thiếu 3.9MB), qwen-vl-7b ghi 9384497971 còn HF trả 9384670680 (THỪA 172KB). Dùng
# sizeBytes làm mốc kiểm thì 5 file tải hoàn chỉnh bị báo hỏng và giữ nguyên .part — đúng chuyện
# đã xảy ra. sizeBytes giờ chỉ còn dùng để ƯỚC LƯỢNG chỗ trống và để bắt trang lỗi (dưới).
def remote_size(url):
    try:
        req=urllib.request.Request(url, method="HEAD", headers={"User-Agent":"preload-models"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return int(r.headers.get("Content-Length") or -1)
    except Exception:
        return -1

cat, models, mode = sys.argv[1], sys.argv[2], sys.argv[3]
groups={x for x in base64.b64decode(sys.argv[4]).decode().split("\n") if x.strip()}
ids   ={x for x in base64.b64decode(sys.argv[5]).decode().split("\n") if x.strip()}
d=json.load(open(cat))
comfy, olla = d.get("comfy",[]), d.get("ollama",[])
pick = lambda e: mode=="all" or e["group"] in groups or e["id"] in ids
sel_c=[e for e in comfy if pick(e)]
sel_o=[e for e in olla  if pick(e)]
if not sel_c and not sel_o:
    sys.exit(3)   # không in gì — shell in thông báo tiếng Việt kèm gợi ý --list
known_g={e["group"] for e in comfy+olla}; known_i={e["id"] for e in comfy+olla}
for u in sorted((groups-known_g) | (ids-known_i)): print(f"UNKNOWN\t{u}")
for e in sel_c:
    dest=os.path.join(models, e["type"], e["filename"])
    have=os.path.getsize(dest) if os.path.exists(dest) else -1
    cat_sz=e.get("sizeBytes",0)
    want=remote_size(e["url"])
    if want <= 0:
        # HEAD hỏng (mạng, hoặc máy chủ không trả Content-Length): vẫn tải, nhưng nói rõ là lần
        # này KHÔNG kiểm được cỡ — im lặng bỏ kiểm mới là thứ nguy hiểm.
        print(f"NOSIZE\t{e['id']}\t-\t{e['filename']}\t0\t-1\t-")
        want, cat_sz = 0, cat_sz
    elif cat_sz > 0 and want < cat_sz * 0.5:
        # Content-Length nhỏ hơn nửa số catalog = gần như chắc chắn không phải model: HuggingFace
        # trả 200 kèm trang HTML lỗi thì Content-Length khớp với thân HTML, nên so mình nó với
        # chính nó luôn "đúng". Catalog dù cũ vẫn đủ chính xác để phát hiện chuyện đó.
        print(f"BOGUS\t{e['id']}\t{e['type']}\t{e['filename']}\t{want}\t{cat_sz}\t{e['url']}")
        continue
    state = "SKIP" if want>0 and have==want else ("PARTIAL" if have>=0 else "GET")
    print(f"{state}\t{e['id']}\t{e['type']}\t{e['filename']}\t{want}\t{have}\t{e['url']}")
# Ollama đi đường HOÀN TOÀN khác: `ollama pull` chứ không aria2c, ghi vào ollama-models/ chứ không
# comfy-models/, và không so cỡ file được (ollama chia model thành nhiều blob). Trạng thái do
# `ollama list` quyết ở dưới, nên ở đây luôn phát ra OLLAMA và để shell lọc.
for e in sel_o:
    print(f"OLLAMA\t{e['id']}\tollama\t{e['model']}\t{e.get('sizeBytes',0)}\t-1\t-")
PY
rc=$?
[ "$rc" -eq 3 ] && die "không mục nào khớp lựa chọn — xem './setup/preload-models.sh --list'"
[ "$rc" -eq 0 ] || die "đọc catalog lỗi"

awk -F'\t' '$1=="UNKNOWN"{print "  ! không có trong catalog: " $2}' "$PLAN"
awk -F'\t' '$1=="NOSIZE"{print "  ! " $4 ": máy chủ không trả Content-Length → tải nhưng KHÔNG kiểm được cỡ"}' "$PLAN"

# BOGUS = URL còn sống nhưng trả về thứ bé hơn nửa cỡ mong đợi. Dừng cả mẻ chứ không bỏ qua một
# file: nó thường nghĩa là repo HuggingFace đã đổi/xoá đường dẫn, và các file khác cùng nhóm cũng sẽ
# sai. Tải tiếp chỉ tạo ra một volume trông đầy đủ mà job đầu tiên mới phát hiện hỏng.
if awk -F'\t' '$1=="BOGUS"{exit 1}' "$PLAN"; then :; else
  awk -F'\t' '$1=="BOGUS"{printf "  ✗ %s: máy chủ trả %s byte, catalog nói ~%s — gần như chắc chắn là trang lỗi, không phải model\n    %s\n", $4, $5, $6, $7}' "$PLAN"
  die "URL hỏng — sửa comfyui/catalog.json trước khi tải tiếp"
fi

NEED=$(awk -F'\t' '$1=="GET"||$1=="PARTIAL"||$1=="OLLAMA"||$1=="NOSIZE"{s+=$5-($6>0?$6:0)} END{printf "%.0f", s+0}' "$PLAN")
NGET=$(awk -F'\t' '$1=="GET"||$1=="PARTIAL"||$1=="OLLAMA"||$1=="NOSIZE"{n++} END{print n+0}' "$PLAN")
NSKIP=$(awk -F'\t' '$1=="SKIP"{n++} END{print n+0}' "$PLAN")
# KHÔNG dùng `df` ở đây. Đo thật trên pod RunPod 02/08/2026: `df /workspace` báo
#   mfs#euro-3.runpod.net:9421  1.4P  1.1P  344T  76%
# tức dung lượng CẢ CỤM MooseFS, không phải quota 100GB của Network Volume. Một cổng kiểm dựa vào
# df sẽ không bao giờ kích — nó báo "còn 344TB" ngay cả khi volume đã đầy, rồi aria2c chết vì hết
# quota ở phút thứ 40. Đúng loại "xanh mà hỏng" đã cắn nhiều lần.
# Nguồn duy nhất đúng là quota do người gọi khai (runpodctl network-volume list → size), trừ đi mức
# dùng thật đo bằng du.
# %.0f chứ KHÔNG %d, và KHÔNG `$1+0`. Hai bẫy awk chồng nhau, cả hai đều làm cổng chặn TỰ TẮT
# trong im lặng (bắt được khi chạy thật trên pod 02/08/2026):
#   • `$1+0` in theo %.6g → 42803400000 thành "4.28034e+10", shell so sánh integer là gãy.
#   • `%d` trong mawk (awk mặc định Ubuntu) là int 32-bit → 42803400000 bị KẸP thành 2147483647,
#     tức 42.8GB hiện ra thành "2.1GB đã dùng" — sai mà trông vẫn hợp lý, loại tệ nhất.
# %.0f dùng double, đúng tới 2^53 byte.
USED=$(du -sb "$VOL" 2>/dev/null | awk '{printf "%.0f", $1}')
if [ -n "${VOLUME_GB:-}" ] && [ "${USED:-0}" -gt 0 ]; then
  AVAIL=$(( VOLUME_GB * 1000000000 - USED ))
  [ "$AVAIL" -lt 0 ] && AVAIL=0
else
  AVAIL=-1   # -1 = KHÔNG kiểm được, khác hẳn 0 = hết chỗ
fi

say "kế hoạch"
printf '  cần tải : %d file, %.1f GB\n' "$NGET" "$(echo "$NEED" | awk '{print $1/1e9}')"
printf '  đã có   : %d file\n' "$NSKIP"
if [ "$AVAIL" -ge 0 ]; then
  printf '  volume  : %s GB quota · %.1f GB đã dùng · %.1f GB trống\n' \
    "$VOLUME_GB" "$(echo "$USED" | awk '{print $1/1e9}')" "$(echo "$AVAIL" | awk '{print $1/1e9}')"
else
  warn "KHÔNG kiểm được chỗ trống: thiếu VOLUME_GB (quota volume, xem 'runpodctl network-volume list')."
  warn "Đặt VOLUME_GB=100 để bật cổng chặn. Chạy tiếp mà không có nó là chấp nhận rủi ro đầy volume giữa chừng."
fi

if [ "$AVAIL" -ge 0 ] && [ "$NEED" -gt "$AVAIL" ]; then
  die "KHÔNG ĐỦ CHỖ: cần $(echo "$NEED"|awk '{printf "%.1f", $1/1e9}')GB, còn $(echo "$AVAIL"|awk '{printf "%.1f", $1/1e9}')GB.
  Nới volume:  runpodctl network-volume update <id> --size <GB lớn hơn hiện tại>
  (chỉ tăng được, và tăng rồi thì tính tiền theo mức mới hằng tháng — xem docs/gpu-pod.md#costs)
  Hoặc chọn ít nhóm hơn: ./setup/preload-models.sh --list"
fi
[ "$NGET" -eq 0 ] && { ok "không có gì để tải — tất cả đã nằm trên volume"; exit 0; }
[ "$DRY" = 1 ] && { ok "--dry-run: dừng ở đây"; exit 0; }

# ── Tải ──────────────────────────────────────────────────────────────────────
# Ollama: cài server một lần, trỏ kho model vào volume. Cùng layout pod-volume.sh:87 tạo
# (ollama-models/), nên pod sau `ollama list` là thấy sẵn, không pull lại.
OLLAMA_READY=0
ollama_setup() {
  [ "$OLLAMA_READY" = 1 ] && return 0
  export OLLAMA_MODELS="$VOL/ollama-models"
  mkdir -p "$OLLAMA_MODELS" || die "không tạo được $OLLAMA_MODELS"
  command -v ollama >/dev/null 2>&1 || {
    say "cài Ollama …"
    curl -fsSL https://ollama.com/install.sh | sh >/dev/null 2>&1 || die "cài Ollama lỗi"
  }
  # Container thường KHÔNG có systemd → `systemctl start ollama` vô nghĩa. Chạy nền rồi chờ API.
  if ! curl -s --max-time 3 http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
    ( OLLAMA_MODELS="$OLLAMA_MODELS" nohup ollama serve >/tmp/ollama-preload.log 2>&1 & )
    for _ in $(seq 1 30); do
      curl -s --max-time 2 http://127.0.0.1:11434/api/version >/dev/null 2>&1 && break; sleep 1
    done
  fi
  curl -s --max-time 3 http://127.0.0.1:11434/api/version >/dev/null 2>&1 \
    || die "ollama serve không lên — xem /tmp/ollama-preload.log"
  OLLAMA_READY=1
}

FAIL=0; DONE=0
while IFS=$'\t' read -r state id type filename want have url; do
  case "$state" in
    SKIP)            ok "bỏ qua $filename (đã có, khớp Content-Length)"; continue ;;
    UNKNOWN|BOGUS)   continue ;;
  esac
  if [ "$state" = OLLAMA ]; then
    ollama_setup
    if ollama list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$filename"; then
      ok "bỏ qua $filename (ollama đã có)"; NSKIP=$((NSKIP+1)); continue
    fi
    say "$id → ollama pull $filename  ($(echo "$want" | awk '{printf "%.1f", $1/1e9}') GB)"
    if ollama pull "$filename"; then ok "xong $filename"; DONE=$((DONE+1))
    else warn "ollama pull $filename lỗi"; FAIL=$((FAIL+1)); fi
    continue
  fi
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
  # $want ở đây là Content-Length của máy chủ, KHÔNG phải sizeBytes của catalog. Dùng catalog làm
  # mốc là sai: 02/08/2026 nó lệch cả hai chiều trên 5/5 file và chặn nhầm 5 bản tải hoàn chỉnh.
  if [ "$want" -gt 0 ] && [ "$got" != "$want" ]; then
    warn "$filename tải xong nhưng cỡ SAI: $got ≠ $want (Content-Length) — giữ .part, KHÔNG rename"
    FAIL=$((FAIL+1)); continue
  fi
  [ "$want" -gt 0 ] || warn "$filename: không có Content-Length để đối chiếu — nhận mà không kiểm được"
  mv -f "$tmp" "$dest" && ok "xong $filename" && DONE=$((DONE+1))
done < "$PLAN"

say "kết quả"
printf '  tải xong: %d · lỗi: %d · đã có sẵn: %d\n' "$DONE" "$FAIL" "$NSKIP"
printf '  tổng comfy-models: %s\n' "$(du -sh "$MODELS" 2>/dev/null | cut -f1)"
[ "$FAIL" -eq 0 ] || die "$FAIL file chưa xong — chạy lại script, nó bỏ qua file đã đủ cỡ"
ok "volume sẵn sàng — pod sau dựng lên là có model, không phải tải lại"
