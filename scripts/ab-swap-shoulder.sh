#!/usr/bin/env bash
#
# A/B trị "vai nhún lên xuống liên tục" của Character Swap (22/08/2026):
#   triệu chứng — cặp nhanvat-1 + dandong6, vai bên TRÁI MÀN HÌNH nhún nhanh suốt ~3s đầu rồi tự
#   lặng. Đo trên output 544×960: frame 0-90 rung RMS 11,7px / |Δy| 7,2px mỗi frame / đỉnh-đỉnh
#   63px / 3,5Hz; từ frame 150 trở đi chỉ còn RMS 4,0px. Vai bên phải màn hình không có triệu chứng.
#
#   ĐÃ LOẠI — "pose detect sai": driver ở đúng 3s đó là đoạn TĨNH NHẤT cả clip (năng lượng
#   frame-diff 0,8-2,3 so với 3-5 về sau), nền phẳng nên không phải rung máy. Chạy pose estimator
#   từng frame lên driver: keypoint vai nhiễu ~7px RMS nhưng ĐỐI XỨNG hai bên, và tương quan với
#   đường vai được render chỉ r=+0,06 (lag 0), tốt nhất +0,21 trong ±8 frame. Xương không rung.
#
# ══ KẾT QUẢ ĐÃ CHẠY 22/08/2026 (7 job, cùng driver dandong6, preset drv-10s, 540p) ══════════════
#
#   ĐÃ BÁC BỎ — WINDOW 1, ĐỪNG THỬ LẠI. Nhánh A (nối 3s = 91 frame đóng băng vào đầu driver để
#   window 1 rơi trọn vào đoạn tĩnh vứt đi) KHÔNG đổi gì: |Δy| 4,44 so với BASE 4,73. Lead-in thật
#   sự có trong output (năng lượng chuyển động 0,61/0,37/0,42 ở 3 giây đầu rồi mới lên 1,3-3,1) nên
#   thí nghiệm hợp lệ, không phải hỏng thiết lập. Ghép thêm vào nhánh tóc (AB) cũng không cộng dồn.
#   → Cấu trúc cửa sổ autoregressive KHÔNG phải nguyên nhân. Giả thuyết (2) dưới đây đã chết.
#
#   CÓ TÁC DỤNG — TÓC (giả thuyết 1). Gom 5 lần chạy theo ảnh mẫu, thước |Δy| mỗi frame:
#     ref gốc (vai trần)      BASE 4,73 · A 4,44          → 4,44-4,73
#     ref tóc xoã trước 2 vai B 3,03 · AB 3,23 · Brep 3,23 → 3,03-3,23
#   Hai nhóm không chồng lấn qua 2 seed và 2 driver. Cải thiện thật ≈ 30% rung khung-trên-khung.
#
#   CẠM BẪY ĐO ĐẠC — ĐỌC TRƯỚC KHI TIN BẤT KỲ SỐ NÀO:
#     · RMS (sau high-pass 15 frame) KHÔNG tái lập được: B=5,86 nhưng Brep=8,19 với CÙNG ảnh mẫu,
#       chỉ khác seed — nhiễu seed nuốt gần trọn khoảng cách tới BASE 9,19. Chỉ |Δy| mỗi frame mới
#       tách bạch. LUÔN chạy kèm một job cùng-input-khác-seed làm chuẩn nhiễu.
#     · Khi ảnh mẫu có tóc DÀY (B2/B3, hairgraft): tóc phủ kín vai → bộ bám mép nhảy lên bám mép
#       tóc↔má, số ra đẹp giả tạo (B2 3,62 / B3 2,54) và VÔ NGHĨA. Muốn đo nhóm này phải viết bộ
#       bám VIỀN NGOÀI người↔nền. Chưa làm.
#     · Bộ đo bằng keypoint pose của output (chuẩn hoá theo khoảng cách 2 vai) ĐÃ THỬ VÀ VÔ DỤNG:
#       cả 4 nhánh đều ra 2,0-2,4%, nhiễu bộ dò lấn át hết tín hiệu.
#
# ══ GIẢ THUYẾT BAN ĐẦU (giữ lại để hiểu vì sao ma trận có 4 nhánh) ═══════════════════════════════
#   Xung đột ref ↔ mask, gói gọn trong window 1:
#     (1) TÓC. Trong dandong6, vai trái màn hình của driver bị màn tóc phủ KÍN suốt đoạn đầu nên
#         vị trí vai thật không quan sát được. Mask người lấy từ driver (node 202→205: SAM3 →
#         GrowMask 10 → Blockify 16, xem _apply_swap_to_wan_workflow) phủ tới hết chỗ tóc đó.
#         Ảnh mẫu thì vai ấy để TRẦN, tóc hất ra sau. Wan phải đặt bờ vai trần vào trong một hình
#         bóng mà đáy là tóc người khác → không có mốc neo độ cao → mỗi frame đoán lại.
#     (2) WINDOW 1. character-swap chạy autoregressive, frame_window_size ép cứng 81
#         (linux.py:2546-2549 — API không ép cho type này vì normalizeMotionDriverSegment chỉ
#         lọc type=="motion", nhưng worker thì ép vô điều kiện). Window 1 = frame 0-80 = 0-2,7s,
#         ĐÚNG khúc hỏng: nó chỉ có ảnh ref làm neo, window 2+ thừa hưởng frame cuối window trước
#         nên độ cao vai bị ghim lại → triệu chứng tự lặng.
#
#   KHÔNG phải bệnh hệ thống: cùng ảnh mẫu, output dandong8 chỉ rung 1,1px ở cùng cửa sổ.
#
# MA TRẬN (cùng seed mặc định 42, cùng preset/quality → so sánh sạch, mỗi lần đổi ĐÚNG 1 biến):
#   BASE  ref gốc          + driver gốc        — đối chứng, dựng lại đúng bộ số để so bằng
#   A     ref gốc          + driver lead-in 3s — biến số WINDOW 1 (giả thuyết 2)
#   B     ref tóc khớp     + driver gốc        — biến số TÓC     (giả thuyết 1)
#   AB    ref tóc khớp     + driver lead-in 3s — cả hai, xem có cộng dồn không
#
#   Lead-in = 3s đóng băng frame 0 nối vào đầu driver (91 frame > 81) nên window 1 rơi TRỌN vào
#   đoạn tĩnh vứt đi: Wan chốt một cấu hình vai trên ảnh đứng yên, chuyển động thật bắt đầu ở
#   window 2 và thừa hưởng cấu hình đã chốt. Cắt 3s đầu khi so sánh.
#   preset drv-10s cho CẢ BỐN: A/AB mất 3s đầu cho lead-in, vẫn còn 7s thật để so với BASE/B.
#
# DÙNG (pod đã bootstrap — make gpu-provision CONFIRM=yes && make gpu-wait && make gpu-bootstrap):
#   scripts/ab-swap-shoulder.sh                       # cả 4 job
#   AB_ONLY="A B" scripts/ab-swap-shoulder.sh         # chỉ vài nhánh
#   AB_PRESET=drv-5s scripts/ab-swap-shoulder.sh      # rẻ hơn, nhưng A/AB chỉ còn 2s thật
#   AB_TIMEOUT_MIN=60 ...                             # trần chờ mỗi job (mặc định 30)
#   AB_MATRIX="tag:ref:driver:seed ..." ...           # thay hẳn ma trận (đường dẫn tương đối .smoke/)
#     vd: AB_MATRIX="B2:.smoke/nhanvat-1-hair-thick.jpeg:.smoke/dandong6.mp4:42"
#
# Kết quả tải về ab-results/swap-shoulder-<timestamp>/ kèm manifest.tsv.
# ĐO LẠI sau khi có kết quả: bám mép tóc↔da ở cột x=14..52 (khung 272×480), high-pass 15 frame,
# lấy RMS trên frame 10-90 của phần chuyển động THẬT. BASE ~11,7px là mốc phải hạ xuống.
#
set -uo pipefail
cd "$(dirname "$0")/.."; ROOT="$(pwd)"

log()  { printf '\n\033[36m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[32m  ✓\033[0m %s\n' "$*"; }
warn() { printf '\033[33m !!\033[0m %s\n' "$*"; }
bad()  { printf '\033[31m ✗ \033[0m%s\n' "$*"; }

env_get() { grep -E "^$1=" "$ROOT/.env" 2>/dev/null | cut -d= -f2- | sed -E 's/[[:space:]]*#.*$//' | tr -d '"'; }

DOMAIN="$(env_get DOMAIN)"
HOST="$(env_get GPU_SSH_HOST)"; PORT="$(env_get GPU_SSH_PORT)"
TIMEOUT_MIN="${AB_TIMEOUT_MIN:-30}"
PRESET="${AB_PRESET:-drv-10s}"
ONLY="${AB_ONLY:-BASE A B AB}"

[ -n "$DOMAIN" ] || { bad "DOMAIN trống trong .env — pod đã bootstrap chưa? (make gpu-bootstrap)"; exit 1; }

REF_ORIG="$ROOT/.smoke/nhanvat-1.jpeg"
REF_HAIR="$ROOT/.smoke/nhanvat-1-hair.jpeg"
DRV_ORIG="$ROOT/.smoke/dandong6.mp4"
DRV_LEAD="$ROOT/.smoke/dandong6-lead3s.mp4"
for f in "$REF_ORIG" "$REF_HAIR" "$DRV_ORIG" "$DRV_LEAD"; do
  [ -f "$f" ] || { bad "thiếu input: $f"; exit 1; }
done

remote() { ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -p "$PORT" "root@$HOST" "$1" 2>/dev/null; }

# API key: giống pod-smoke — ưu tiên motions/.env, fallback đọc từ pod qua SSH.
KEY="$(grep -E '^NUXT_MOTION_API_KEY=' "$ROOT/motions/.env" 2>/dev/null | cut -d= -f2- | tr -d '"')"
if [ -z "$KEY" ] && [ -n "$HOST" ] && [ -n "$PORT" ]; then
  KEY="$(remote "grep -E '^API_KEY=' ~/motion-backend/.env | cut -d= -f2-" | tr -d '\r\n')"
fi
[ -n "$KEY" ] || { bad "không lấy được API key (motions/.env NUXT_MOTION_API_KEY hoặc pod ~/motion-backend/.env API_KEY)"; exit 1; }

OUTDIR="$ROOT/ab-results/swap-shoulder-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUTDIR"
MANIFEST="$OUTDIR/manifest.tsv"
printf 'tag\tref\tdriver\tpreset\tjob_id\tseconds\tstatus\tfile\n' > "$MANIFEST"

# POST /jobs → poll → download. $1 tag, $2 ref, $3 driver.
run_job() {
  local tag="$1" ref="$2" drv="$3" seed="${4:-}"
  local params out_path t0 t1
  params="{\"engine\":\"wananimate\",\"preset\":\"$PRESET\",\"quality\":\"540p\""
  [ -n "$seed" ] && params="$params,\"seed\":$seed"
  params="$params}"
  out_path="$OUTDIR/$tag.mp4"
  log "$tag — ref=$(basename "$ref") driver=$(basename "$drv") preset=$PRESET"
  t0=$(date +%s)
  local job job_id
  job="$(curl -s --max-time 300 -X POST "https://$DOMAIN/jobs" -H "x-api-key: $KEY" \
        -F "type=character-swap" -F "params=$params" -F "ref=@$ref" -F "video=@$drv")"
  job_id="$(printf '%s' "$job" | python3 -c 'import json,sys; print((json.load(sys.stdin) or {}).get("id",""))' 2>/dev/null)"
  if [ -z "$job_id" ]; then
    bad "POST /jobs không trả id: $(printf '%s' "$job" | head -c 300)"
    printf '%s\t%s\t%s\t%s\t-\t-\tpost-failed\t-\n' "$tag" "$(basename "$ref")" "$(basename "$drv")" "$PRESET" >> "$MANIFEST"
    return
  fi
  ok "job $job_id queued — chờ tối đa ${TIMEOUT_MIN}m"
  local deadline last st pr step r sz
  deadline=$(( t0 + TIMEOUT_MIN * 60 )); last=""; st="(chưa poll)"
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
        t1=$(date +%s)
        sz="$(curl -s -o "$out_path" -w '%{size_download}' --max-time 600 \
              -H "x-api-key: $KEY" "https://$DOMAIN/jobs/$job_id/download")"
        if [ "${sz:-0}" -gt 100000 ] 2>/dev/null; then
          ok "$tag xong sau $((t1-t0))s → $out_path ($((sz/1024)) KB)"
          printf '%s\t%s\t%s\t%s\t%s\t%s\tdone\t%s\n' "$tag" "$(basename "$ref")" "$(basename "$drv")" "$PRESET" "$job_id" "$((t1-t0))" "$tag.mp4" >> "$MANIFEST"
        else
          bad "$tag: job done nhưng tải về chỉ ${sz:-0} byte"
          printf '%s\t%s\t%s\t%s\t%s\t%s\tempty-download\t-\n' "$tag" "$(basename "$ref")" "$(basename "$drv")" "$PRESET" "$job_id" "$((t1-t0))" >> "$MANIFEST"
        fi
        return ;;
      error|cancelled)
        t1=$(date +%s)
        bad "$tag: job $st — $(printf '%s' "$r" | python3 -c 'import json,sys; print((json.load(sys.stdin) or {}).get("error","?"))' 2>/dev/null)"
        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t-\n' "$tag" "$(basename "$ref")" "$(basename "$drv")" "$PRESET" "$job_id" "$((t1-t0))" "$st" >> "$MANIFEST"
        return ;;
    esac
  done
  bad "$tag: quá ${TIMEOUT_MIN}m vẫn chưa xong (đang $st)"
  printf '%s\t%s\t%s\t%s\t%s\t-\ttimeout\t-\n' "$tag" "$(basename "$ref")" "$(basename "$drv")" "$PRESET" "$job_id" >> "$MANIFEST"
}

has() { case " $ONLY " in *" $1 "*) return 0 ;; *) return 1 ;; esac; }

log "A/B vai nhún — pod $DOMAIN — kết quả vào $OUTDIR"
if [ -n "${AB_MATRIX:-}" ]; then
  # Ma trận tự khai: "tag:ref:driver:seed" cách nhau bởi khoảng trắng. Dùng cho vòng đo tiếp theo
  # (vd đẩy độ dày tóc) mà không phải sửa script — ma trận 4 nhánh gốc ở dưới giữ nguyên làm chuẩn.
  for spec in $AB_MATRIX; do
    IFS=: read -r _t _r _d _s <<<"$spec"
    [ -f "$ROOT/$_r" ] || { bad "thiếu ref: $ROOT/$_r"; continue; }
    [ -f "$ROOT/$_d" ] || { bad "thiếu driver: $ROOT/$_d"; continue; }
    run_job "$_t" "$ROOT/$_r" "$ROOT/$_d" "$_s"
  done
else
  has BASE && run_job BASE "$REF_ORIG" "$DRV_ORIG"
  has A    && run_job A    "$REF_ORIG" "$DRV_LEAD"
  has B    && run_job B    "$REF_HAIR" "$DRV_ORIG"
  has AB   && run_job AB   "$REF_HAIR" "$DRV_LEAD"
fi

log "Xong. manifest:"
column -t -s $'\t' "$MANIFEST"
echo
echo "So sánh: A và AB có 3s lead-in đóng băng ở đầu — cắt đi trước khi xem/đo."
echo "  ffmpeg -i $OUTDIR/A.mp4 -ss 3 -c copy $OUTDIR/A-trim.mp4"
