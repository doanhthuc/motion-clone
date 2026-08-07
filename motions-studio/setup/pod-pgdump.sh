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

get_kv() { grep -E "^$1=" "$ROOT/.env" 2>/dev/null | cut -d= -f2- | sed -E 's/[[:space:]]*#.*$//' | tr -d '"'; }
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

# stat khác cú pháp giữa BSD (macOS, máy dev) và GNU (Ubuntu, pod) — thử cả hai.
_mode() { stat -f '%Lp' "$1" 2>/dev/null || stat -c '%a' "$1" 2>/dev/null; }

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
  ln -sfn "$out" "$LATEST"

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

case "${1:-}" in
  --dump)  do_dump ;;
  *) echo "dùng: $0 --dump|--restore|--check|--verify" >&2; exit 2 ;;
esac
