#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
# pod-pgdump.sh — sao lưu/khôi phục database sang Network Volume.
#
# VÌ SAO: PGDATA nằm trên container disk nên `make gpu-destroy` xoá luôn database.
# Không đưa PGDATA lên volume được — RunPod mount MooseFS chặn chown kể cả khi là root,
# và container không có /dev/loop* nên file ext4 loopback cũng bất khả thi (commit d2a9ffc).
# Nên ta đặt một bản sao LOGIC lên volume: ghi file thường lên MooseFS thì bình thường.
#
#   ./setup/pod-pgdump.sh --dump      # sao lưu
#   ./setup/pod-pgdump.sh --restore   # khôi phục, CHỈ khi DB trống
#   ./setup/pod-pgdump.sh --check     # báo cáo, không sửa gì
#   ./setup/pod-pgdump.sh --verify    # diễn tập nạp lại vào DB tạm
#
# Đây là nơi DUY NHẤT biết bố cục thư mục dump. Caller chỉ truyền chế độ, đọc exit code.
# Xem: docs/superpowers/specs/2026-08-07-pg-dump-volume-design.md
# ════════════════════════════════════════════════════════════════════════════
set -uo pipefail
cd "$(dirname "$0")/.."; ROOT="$(pwd)"

log()  { printf '\033[36m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[32m ✓ \033[0m%s\n' "$*"; }
warn() { printf '\033[33m !!\033[0m %s\n' "$*"; }
die()  { printf '\033[31m ✗ \033[0m%s\n' "$*" >&2; exit 1; }

# `head -1` khớp khuôn pod-volume.sh:52, và không phải chuyện thẩm mỹ: `.env` trên pod giờ là
# nguồn CHÍNH của POD_VOLUME (lib-feature.sh phase_pg_restore ghi vào đó). Một key lặp hai lần
# — chuyện thường gặp với file người sửa tay — sẽ cho POD_VOLUME hai dòng, rồi `[ -d ... ]` đỏ
# với một thông báo vô nghĩa. Lấy dòng đầu, y như mọi nơi khác trong repo.
get_kv() { grep -E "^$1=" "$ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2- | sed -E 's/[[:space:]]*#.*$//' | tr -d '"'; }
# Khuôn của pod-volume.sh:188 — env trước, .env sau. Nhờ vậy chạy tay, chạy từ Makefile
# và chạy trong test đều thấy cùng một cấu hình.
cfg() { local v="${!1:-}"; [ -n "$v" ] && { printf '%s' "$v"; return; }; get_kv "$2"; }

POD_VOLUME="$(cfg POD_VOLUME POD_VOLUME)"
PG_USER="$(cfg POSTGRES_USER POSTGRES_USER)";  PG_USER="${PG_USER:-motion}"
PG_PASS="$(cfg POSTGRES_PASSWORD POSTGRES_PASSWORD)"
PG_DB="$(cfg POSTGRES_DB POSTGRES_DB)";        PG_DB="${PG_DB:-motion}"
PG_PORT="$(cfg POSTGRES_PORT POSTGRES_PORT)";  PG_PORT="${PG_PORT:-5432}"
PG_HOST="${PGHOST:-127.0.0.1}"
KEEP="$(cfg PG_DUMP_KEEP PG_DUMP_KEEP)";       KEEP="${KEEP:-20}"

[ -n "$POD_VOLUME" ] || die "POD_VOLUME trống — không có volume thì không có chỗ đặt bản sao."
[ -d "$POD_VOLUME" ] || die "POD_VOLUME=$POD_VOLUME không phải thư mục — volume chưa mount?"

PGDIR="$POD_VOLUME/pg"
DUMPS="$PGDIR/dumps"
LATEST="$PGDIR/latest"

_psql()    { PGPASSWORD="$PG_PASS" psql    -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" "$@"; }
_pg_dump() { PGPASSWORD="$PG_PASS" pg_dump -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" "$@"; }

# Đếm CHÍNH XÁC, không dùng n_live_tup của pg_stat_user_tables: đó là số ƯỚC LƯỢNG do
# autovacuum cập nhật, nên nó lệch thật sự — mà cả cơ chế .meta này tồn tại để bắt lệch.
#
# Nhận TÊN DATABASE làm tham số (mặc định $PG_DB) vì --verify sẽ gọi lại chính hàm này trên
# DB tạm. Không tham số hoá thì --verify phải chép lại cả đoạn truy vấn, và hai bản chép sẽ
# trôi khỏi nhau đúng lúc không ai để ý.
_row_counts() {
  local db="${1:-$PG_DB}" q
  q="$(_psql -d "$db" -tAc \
    "SELECT string_agg(format('SELECT %L||''=''||count(*)::text FROM public.%I', tablename, tablename), ' UNION ALL ')
     FROM pg_tables WHERE schemaname='public'")"
  [ -n "$q" ] || return 0
  _psql -d "$db" -tAc "$q" | sed '/^$/d' | sort
}

_table_count() { _psql -d "$PG_DB" -tAc "SELECT count(*) FROM pg_tables WHERE schemaname='public'"; }

# In ra đường dẫn bản dump mới nhất, hoặc trả khác 0 nếu KHÔNG có bản nào dùng được.
#
# $LATEST là ĐƯỜNG NHANH, KHÔNG phải nguồn sự thật. Volume trên pod là MooseFS và CÙNG mount
# đó đã chặn `chown` kể cả khi là root (pod-volume.sh:179-191) — chưa ai đo `symlink()` ở đó.
# Nếu chỉ nhìn $LATEST: link vắng hoặc treo ⇒ ta nói "đây là phiên đầu" và trả 0, trong khi
# $DUMPS đang nằm đầy dump tốt. Đó là mất dữ liệu hoàn chỉnh với exit 0 ở mọi bước.
# Nên: thử $LATEST trước, không được thì quét $DUMPS.
#
# Sort theo TÊN, không phải `ls -t`: tên dạng motion-YYYYmmdd-HHMMSS (UTC, độ dài cố định) nên
# thứ tự chữ cái TRÙNG thứ tự thời gian, đúng khuôn _prune đang dùng, và không phải tin vào
# mtime của MooseFS.
_latest_dump() {
  local t=""
  if [ -L "$LATEST" ] || [ -f "$LATEST" ]; then
    t="$(readlink "$LATEST" 2>/dev/null || printf '%s' "$LATEST")"
    # Link TƯƠNG ĐỐI (dumps/motion-….sql.gz) phải giải theo thư mục CHỨA link, không theo cwd.
    case "$t" in /*) ;; *) t="$PGDIR/$t" ;; esac
    [ -f "$t" ] || { warn "latest trỏ file không tồn tại: $t — tìm bản mới nhất trong $DUMPS" >&2; t=""; }
  fi
  [ -n "$t" ] || t="$(ls -1 "$DUMPS"/motion-*.sql.gz 2>/dev/null | sort | tail -1)"
  [ -n "$t" ] && [ -f "$t" ] || return 1
  printf '%s' "$t"
}

# stat khác cú pháp giữa BSD (macOS, máy dev) và GNU (Ubuntu, pod) — thử cả hai.
_mode() { stat -f '%Lp' "$1" 2>/dev/null || stat -c '%a' "$1" 2>/dev/null; }

# Tên file dạng motion-YYYYmmdd-HHMMSS nên thứ tự CHỮ CÁI trùng thứ tự THỜI GIAN —
# sort theo tên thay vì `ls -t`, vì `ls -t` trên volume mạng đọc mtime của MooseFS và
# parse output của ls là thứ vỡ ngay khi có tên lạ.
_prune() {
  local keep="$1" all n f
  # Làm sạch keep TRƯỚC mọi phép tính. `.env` là file người sửa tay, nên KEEP có thể rỗng,
  # có chữ, hoặc bằng 0. Với keep=0 thì `head -n $((n-keep))` xoá đúng TẤT CẢ, gồm cả bản
  # $LATEST đang trỏ vào — mất sạch lịch sử backup vì một ký tự gõ nhầm. Kẹp về 1 là diễn
  # giải đúng của ràng buộc: giữ ít nhất một bản, luôn luôn.
  case "$keep" in ''|*[!0-9]*) keep=1 ;; esac
  # Dòng dưới là chốt chặn duy nhất cho KEEP=0 (case trên không bắt được "0" vì nó toàn chữ số)
  [ "$keep" -ge 1 ] || keep=1
  all="$(ls -1 "$DUMPS"/motion-*.sql.gz 2>/dev/null | sort)"
  n="$(printf '%s\n' "$all" | sed '/^$/d' | wc -l | tr -d ' ')"
  [ "$n" -le 1 ] && return 0          # không bao giờ xoá bản cuối cùng còn lại
  [ "$n" -le "$keep" ] && return 0
  printf '%s\n' "$all" | head -n "$((n - keep))" | while read -r f; do
    [ -n "$f" ] || continue
    rm -f "$f" "${f%.sql.gz}.meta"
  done
}

do_dump() {
  # umask 077: mọi file/thư mục sinh ra TRONG HÀM NÀY đã đúng quyền ngay từ lúc
  # syscall tạo ra nó, không đợi chmod chạy sau mới sửa. Đây mới là chỗ bịt cửa sổ
  # thời gian thật sự: suốt lúc `pg_dump | gzip` đang ghi vào file tạm, dump đã
  # chứa api_keys, user_sessions.token_hash, token social_accounts — không thể để
  # cửa sổ đó trần trụi theo umask mặc định của caller. Các `chmod` bên dưới chỉ
  # còn là lớp phòng thủ thứ hai; xem kiểm KẾT QUẢ thật ở cuối hàm.
  umask 077
  mkdir -p "$DUMPS" || die "không tạo được $DUMPS"
  chmod 700 "$PGDIR" "$DUMPS" 2>/dev/null || true
  local stamp tmp out meta
  stamp="$(date -u +%Y%m%d-%H%M%S)"
  out="$DUMPS/motion-$stamp.sql.gz"
  meta="$DUMPS/motion-$stamp.meta"
  tmp="$DUMPS/.tmp-$stamp.sql.gz"

  # pipefail đã bật ở đầu file: pg_dump hỏng giữa chừng thì cả pipeline hỏng, không
  # để lại một file .gz hợp lệ chứa nửa database.
  # KHÔNG dùng --no-owner: dump phải GIỮ các câu ALTER TABLE ... OWNER TO, vì khôi phục
  # chạy sau phase_postgres nên role đã tồn tại, và giữ owner đúng thì app kết nối được ngay.
  # (Mặc định của pg_dump đã là giữ owner — chỉ cần không truyền cờ nào.)
  if ! _pg_dump -d "$PG_DB" | gzip -c > "$tmp"; then
    rm -f "$tmp"; die "pg_dump thất bại — DB có đang chạy không? (port $PG_PORT)"
  fi
  chmod 600 "$tmp" 2>/dev/null || true

  # ĐUA ĐÃ BIẾT, chưa sửa trong đợt này (finding I1 — không chặn merge):
  # `pg_dump` chạy ở trên, `_row_counts` chạy ngay dưới. Một INSERT chen vào giữa hai mốc đó
  # làm .meta ghi số dòng KHÁC với số dòng thật trong file dump — và lệch đó là VĨNH VIỄN, nên
  # mọi `--verify` sau này trên chính bản dump ấy đều đỏ dù bản dump hoàn toàn tốt. Chuyện này
  # có thật: `make gpu-down` dump khi api/worker vẫn đang online.
  # Sửa đúng cách là đọc cả hai từ CÙNG một snapshot (một transaction REPEATABLE READ dùng chung,
  # hoặc lấy số dòng từ chính nội dung dump) — việc của một đợt sau.
  {
    echo "created=$(date -u +%s)"
    # server_version_num, KHÔNG dùng server_version: bản Ubuntu đóng gói trả chuỗi kiểu
    # "16.4 (Ubuntu 16.4-0ubuntu0.24.04.1)", sau tr -d ' ' dính thành một token xấu
    # trong .meta. server_version_num là số nguyên (vd 160004), không cần parse gì thêm.
    echo "pg_version=$(_psql -d "$PG_DB" -tAc 'SHOW server_version_num' | tr -d ' ')"
    echo "dump_bytes=$(wc -c < "$tmp" | tr -d ' ')"
    _row_counts
  } > "$meta"
  chmod 600 "$meta" 2>/dev/null || true

  # mv trong CÙNG filesystem là nguyên tử → latest không bao giờ trỏ một file ghi dở.
  mv "$tmp" "$out" || { rm -f "$tmp" "$meta"; die "mv dump thất bại"; }

  # Link TƯƠNG ĐỐI, không tuyệt đối. `latest` và `dumps/` nằm cùng một cây thư mục trên volume,
  # nên link tương đối đúng bất kể volume được mount ở đâu. Link tuyệt đối chốt cứng $POD_VOLUME
  # của POD ĐÃ TẠO RA nó — pod sau mount ở path khác (hoặc người dùng đổi POD_VOLUME) là link
  # treo ngay, đúng vào lúc ta cần nó nhất.
  #
  # `|| die` chứ không bỏ qua: chưa ai đo `symlink()` trên MooseFS, mà cùng mount đó đã chặn
  # `chown`. Không kiểm thì --dump in "ok" và trả 0 trên một volume không có `latest`.
  # Dump và .meta ĐÃ ghi xong trước dòng này nên die ở đây KHÔNG mất backup — _latest_dump()
  # vẫn tìm thấy file qua $DUMPS. Đỏ ở đây nghĩa là "latest không tin được nữa", không phải
  # "mất dump"; nhưng nó phải đỏ, vì một cơ chế backup im lặng hỏng một nửa là cơ chế tồi nhất.
  ln -sfn "dumps/$(basename "$out")" "$LATEST" \
    || die "không tạo được symlink $LATEST (bản dump ĐÃ ghi xong ở $out — không mất gì, nhưng volume này không cho tạo symlink)"
  _prune "$KEEP"

  # Kiểm KẾT QUẢ thật bằng stat, KHÔNG kiểm exit code của chmod ở trên. Trên pod, volume
  # là MooseFS mount user_id=0,group_id=0 CHẶN chown kể cả khi là root (pod-volume.sh:179-191)
  # — chưa ai đo chmod ở đó có ăn hay không. Nếu mode vốn đã đúng (nhờ umask 077 phía trên)
  # thì chmod thất bại là vô hại, và `|| die` sẽ giết đường dump chính trên pod vì một lý do
  # không gây hại gì. Chỉ khi mode THẬT SỰ sai mới coi là lỗi.
  local m_dir m_dumps m_out m_meta bad_perm=""
  m_dir="$(_mode "$PGDIR")";   [ "$m_dir"   = "700" ] || bad_perm="$bad_perm $PGDIR=$m_dir"
  m_dumps="$(_mode "$DUMPS")"; [ "$m_dumps" = "700" ] || bad_perm="$bad_perm $DUMPS=$m_dumps"
  m_out="$(_mode "$out")";     [ "$m_out"   = "600" ] || bad_perm="$bad_perm $out=$m_out"
  m_meta="$(_mode "$meta")";   [ "$m_meta"  = "600" ] || bad_perm="$bad_perm $meta=$m_meta"

  ok "dump: $(basename "$out") ($(wc -c < "$out" | tr -d ' ') bytes, $(grep -c '=' "$meta") dòng meta)"

  if [ -n "$bad_perm" ]; then
    warn "QUYỀN SAI trên dump —$bad_perm (cần thư mục=700, file=600)."
    warn "Dump chứa dữ liệu nhạy cảm (api_keys, user_sessions.token_hash, token social_accounts)"
    warn "và có thể đang lộ rộng hơn dự tính. KHÔNG xoá — mất hẳn backup còn tệ hơn một bản backup"
    warn "quyền rộng. Tự kiểm tra và chmod tay, rồi tìm hiểu vì sao chmod không ăn trên volume này."
    return 1
  fi
}

do_restore() {
  local tables meta created age_h target
  # "phiên đầu" chỉ được nói khi $DUMPS THẬT SỰ rỗng — không phải khi mỗi symlink latest vắng.
  if ! target="$(_latest_dump)"; then
    log "chưa có bản dump nào trên volume — bỏ qua (đây là phiên đầu)."
    return 0
  fi

  # In tuổi TRƯỚC khi biết có nạp được hay không: operator chạy --restore trên một DB đã
  # có bảng (bị bỏ qua) vẫn cần biết bản dump gần nhất mới tới đâu, không chỉ khi thực sự
  # nạp. Tách khỏi nhánh nạp để không lặp lại logic đọc .meta ở cả hai nơi.
  meta="${target%.sql.gz}.meta"
  created="$(grep -m1 '^created=' "$meta" 2>/dev/null | cut -d= -f2)"
  if [ -n "$created" ]; then
    age_h=$(( ( $(date -u +%s) - created ) / 3600 ))
    log "tuổi bản dump: ${age_h} giờ ($(basename "$target"))"
    [ "$age_h" -gt 168 ] && warn "bản dump này CŨ HƠN 7 NGÀY — dữ liệu sau mốc đó không có trong này."
  else
    warn "tuổi bản dump: không đọc được (thiếu .meta) — $(basename "$target")"
  fi

  tables="$(_table_count)"
  [ -n "$tables" ] || { warn "không hỏi được danh sách bảng — DB có chạy không?"; return 1; }
  if [ "$tables" != "0" ]; then
    log "DB đã có $tables bảng trong schema public — KHÔNG nạp đè. Bỏ qua."
    return 0
  fi

  gzip -t "$target" 2>/dev/null || { warn "file dump hỏng (gzip -t đỏ): $target"; return 1; }

  # Hai cờ này mua hai thứ KHÁC NHAU, cần cả hai:
  #   --single-transaction  → tính nguyên tử. Lỗi giữa chừng thì transaction abort, và Postgres
  #     tự biến COMMIT cuối thành ROLLBACK, nên không bao giờ có DB nạp nửa vời.
  #   ON_ERROR_STOP=1       → exit code TRUNG THỰC. Thiếu nó, psql thoát 0 sau một lần nạp đã
  #     hỏng và đã rollback — ta báo "khôi phục xong" trên một DB rỗng. Đúng loại thành công giả.
  # Đo thật trong container: cùng một dump lỗi, không ON_ERROR_STOP → exit 0 / 0 bảng;
  # có ON_ERROR_STOP → exit 3 / 0 bảng; bỏ luôn --single-transaction → 2 bảng sống sót.
  if ! gzip -dc "$target" | _psql -d "$PG_DB" --single-transaction -v ON_ERROR_STOP=1 -q >/dev/null; then
    warn "nạp dump thất bại — đã rollback, DB vẫn trống như trước."
    return 1
  fi
  ok "khôi phục xong từ $(basename "$target")"
}

do_check() {
  local target meta created age_h
  target="$(_latest_dump)" || { warn "chưa có bản dump nào trên volume ($PGDIR)."; return 1; }
  gzip -t "$target" 2>/dev/null || { warn "file dump hỏng (gzip -t đỏ): $target"; return 1; }

  meta="${target%.sql.gz}.meta"
  created="$(grep -m1 '^created=' "$meta" 2>/dev/null | cut -d= -f2)"
  age_h="?"; [ -n "$created" ] && age_h=$(( ( $(date -u +%s) - created ) / 3600 ))
  ok "bản dump mới nhất: $(basename "$target") · ${age_h} giờ tuổi · $(wc -c < "$target" | tr -d ' ') bytes"
  log "số bản đang giữ: $(ls -1 "$DUMPS"/motion-*.sql.gz 2>/dev/null | wc -l | tr -d ' ')"
  log "số dòng ghi trong .meta:"
  grep -vE '^(created|pg_version|dump_bytes)=' "$meta" 2>/dev/null | sed 's/^/    /'

  # return 0 TƯỜNG MINH, đừng bỏ. Không có nó thì giá trị trả về của hàm là exit status của
  # pipeline `grep | sed` ngay trên: với pipefail, grep không khớp dòng nào (dump từ một DB
  # chưa có bảng) trả 1, và .meta mất hẳn trả 2 — tức --check báo "không đọc được" cho một
  # bản dump hoàn toàn hợp lệ, rồi kéo theo cả --verify vì nó dùng do_check làm cổng gác.
  return 0
}

# Bằng chứng, không phải dấu vết: nạp thật vào một DB tạm rồi so số dòng. Không có bước này
# thì cả cơ chế backup chỉ là niềm tin, và niềm tin đó được kiểm lần đầu vào đúng lúc tệ nhất.
do_verify() {
  local target meta vdb rc=0 got want
  do_check >/dev/null || { warn "không có bản dump đọc được để verify."; return 1; }
  # Cùng đường phân giải với do_check ở trên (kể cả fallback khi latest vắng/treo), nếu không
  # --verify sẽ đi kiểm một file KHÁC với file --check vừa báo xanh.
  target="$(_latest_dump)" || { warn "không có bản dump đọc được để verify."; return 1; }
  meta="${target%.sql.gz}.meta"
  vdb="${PG_DB}_verify"

  _psql -d postgres -q -c "DROP DATABASE IF EXISTS $vdb" >/dev/null 2>&1
  _psql -d postgres -q -c "CREATE DATABASE $vdb OWNER $PG_USER" >/dev/null 2>&1 \
    || { warn "không tạo được DB tạm $vdb — role $PG_USER có quyền CREATEDB chưa?"; return 1; }

  # trap: dọn DB tạm kể cả khi bị Ctrl-C hay bị kill giữa lúc nạp. Không có nó thì việc dọn
  # phụ thuộc vào luồng chạy tới được dòng DROP cuối hàm — mà ngắt giữa chừng là chuyện thật
  # khi gõ tay trên pod, và để lại motion_verify sẽ làm lần verify sau đỏ vì lý do khác hẳn.
  trap '_psql -d postgres -q -c "DROP DATABASE IF EXISTS '"$vdb"'" >/dev/null 2>&1' INT TERM

  if gzip -dc "$target" | PGPASSWORD="$PG_PASS" psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" \
       -d "$vdb" --single-transaction -v ON_ERROR_STOP=1 -q >/dev/null 2>&1; then
    want="$(grep -vE '^(created|pg_version|dump_bytes)=' "$meta" 2>/dev/null | sort)"
    got="$(_row_counts "$vdb")"
    if [ "$want" = "$got" ]; then
      ok "verify: nạp lại được và số dòng khớp .meta ($(printf '%s\n' "$got" | wc -l | tr -d ' ') bảng)"
    else
      warn "verify ĐỎ — số dòng lệch so với .meta:"
      diff <(printf '%s\n' "$want") <(printf '%s\n' "$got") | sed 's/^/    /'
      # Đừng để dòng trên bị đọc thành "bản dump hỏng" — thường thì không phải.
      warn "Lệch KHÔNG chắc là dump hỏng: .meta được đếm SAU khi pg_dump chạy xong, nên một"
      warn "INSERT xen vào giữa hai mốc đó cũng cho ra đúng triệu chứng này (gpu-down dump khi"
      warn "api/worker còn online). Dấu hiệu phân biệt: lệch vài dòng ở đúng các bảng đang ghi"
      warn "= gần như chắc chắn là đua; thiếu hẳn bảng, hoặc số vênh lớn = mới đáng ngờ dump."
      rc=1
    fi
  else
    warn "verify ĐỎ — không nạp lại được bản dump này."
    rc=1
  fi

  # Xoá DB tạm KỂ CẢ khi hỏng: để lại một DB rác tên motion_verify sẽ làm lần verify sau
  # đỏ vì lý do khác hẳn, và truy ra rất mất công.
  trap - INT TERM
  _psql -d postgres -q -c "DROP DATABASE IF EXISTS $vdb" >/dev/null 2>&1
  return "$rc"
}

case "${1:-}" in
  --dump)    do_dump ;;
  --restore) do_restore ;;
  --check)   do_check ;;
  --verify)  do_verify ;;
  *) echo "dùng: $0 --dump|--restore|--check|--verify" >&2; exit 2 ;;
esac
