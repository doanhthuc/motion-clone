#!/usr/bin/env bash
# pgdump-test.sh — test pod-pgdump.sh trên MÁY DEV, không cần thuê pod.
#
#   bash motions-studio/setup/tests/pgdump-test.sh
#
# Cần docker (dựng Postgres thật) và psql/pg_dump ở PATH. Dùng postgres:18 cho KHỚP
# client psql 18 của homebrew: cặp client/server lệch major có thể làm restore hỏng theo
# cách không liên quan gì tới code đang test. Trên pod cả hai đều là PG 16 từ CÙNG một lần
# `apt install postgresql`, nên ở đó cặp này khớp sẵn.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/../pod-pgdump.sh"
CTR=mtc-pgdump-test
PORT=55432
PASS=testpass

PASSED=0; FAILED=0
ok()   { printf '\033[32m ✓ \033[0m%s\n' "$*"; PASSED=$((PASSED+1)); }
bad()  { printf '\033[31m ✗ \033[0m%s\n' "$*"; FAILED=$((FAILED+1)); }
info() { printf '\033[36m==>\033[0m %s\n' "$*"; }

assert_eq() { # assert_eq <mong đợi> <thực tế> <nhãn>
  if [ "$1" = "$2" ]; then ok "$3"; else bad "$3 — mong đợi [$1], nhận [$2]"; fi
}
assert_ok() { # assert_ok <nhãn> -- <lệnh...>
  local label="$1"; shift; shift
  if "$@" >/dev/null 2>&1; then ok "$label"; else bad "$label — lệnh trả khác 0: $*"; fi
}
assert_fail() {
  local label="$1"; shift; shift
  if "$@" >/dev/null 2>&1; then bad "$label — lệnh trả 0 nhưng phải hỏng"; else ok "$label"; fi
}

docker info >/dev/null 2>&1 || { echo "docker chưa chạy — mở Docker Desktop rồi thử lại"; exit 2; }
command -v psql >/dev/null || { echo "thiếu psql ở PATH"; exit 2; }

cleanup() { docker rm -f "$CTR" >/dev/null 2>&1; [ -n "${VOL:-}" ] && rm -rf "$VOL"; }
trap cleanup EXIT

info "dựng Postgres trong docker…"
docker rm -f "$CTR" >/dev/null 2>&1
docker run -d --name "$CTR" -e POSTGRES_PASSWORD="$PASS" -e POSTGRES_USER=motion \
  -e POSTGRES_DB=motion -p "$PORT:5432" postgres:18 >/dev/null || exit 2
for _ in $(seq 1 60); do
  PGPASSWORD="$PASS" psql -h 127.0.0.1 -p "$PORT" -U motion -d motion -c 'SELECT 1' >/dev/null 2>&1 && break
  sleep 1
done

VOL="$(mktemp -d)"
export POD_VOLUME="$VOL" POSTGRES_USER=motion POSTGRES_PASSWORD="$PASS" \
       POSTGRES_DB=motion POSTGRES_PORT="$PORT" PGHOST=127.0.0.1

q() { PGPASSWORD="$PASS" psql -h 127.0.0.1 -p "$PORT" -U motion -d motion -tAc "$1"; }

seed() {
  q "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" >/dev/null
  q "CREATE TABLE jobs (id int primary key, kind text);
     INSERT INTO jobs SELECT g, 'motion' FROM generate_series(1,7) g;
     CREATE TABLE users (id int primary key);
     INSERT INTO users SELECT g FROM generate_series(1,3) g;" >/dev/null
}

# ── Task 1 ────────────────────────────────────────────────────────────────
info "Task 1 — --dump"
seed
assert_ok "--dump trả 0" -- bash "$SCRIPT" --dump
DUMP="$(ls "$VOL"/pg/dumps/motion-*.sql.gz 2>/dev/null | head -1)"
[ -n "$DUMP" ] && ok "tạo ra file .sql.gz" || bad "không thấy file .sql.gz"
[ -f "${DUMP%.sql.gz}.meta" ] && ok "tạo ra .meta" || bad "không thấy .meta"
[ -L "$VOL/pg/latest" ] && ok "tạo ra symlink latest" || bad "không thấy symlink latest"
assert_eq "7" "$(grep '^jobs=' "${DUMP%.sql.gz}.meta" | cut -d= -f2)" ".meta đếm đúng bảng jobs"
assert_eq "3" "$(grep '^users=' "${DUMP%.sql.gz}.meta" | cut -d= -f2)" ".meta đếm đúng bảng users"
assert_eq "700" "$(stat -f '%Lp' "$VOL/pg/dumps")" "thư mục dumps quyền 700"
assert_eq "600" "$(stat -f '%Lp' "$DUMP")" "file dump quyền 600"

# umask rộng của caller không được rò vào quyền file dump — bịt bằng umask 077
# trong chính do_dump(), không chỉ dựa vào chmod chạy sau khi file đã tồn tại.
( umask 000; bash "$SCRIPT" --dump >/dev/null )
D2="$(readlink "$VOL/pg/latest")"
assert_eq "600" "$(stat -f '%Lp' "$D2")" "dump vẫn 600 kể cả khi umask của caller là 000"
assert_eq "700" "$(stat -f '%Lp' "$VOL/pg/dumps")" "thư mục vẫn 700 kể cả khi umask của caller là 000"

# ── Task 2 ────────────────────────────────────────────────────────────────
info "Task 2 — prune"
rm -rf "$VOL/pg"; seed
PG_DUMP_KEEP=2 bash "$SCRIPT" --dump >/dev/null; sleep 1
PG_DUMP_KEEP=2 bash "$SCRIPT" --dump >/dev/null; sleep 1
PG_DUMP_KEEP=2 bash "$SCRIPT" --dump >/dev/null
assert_eq "2" "$(ls "$VOL"/pg/dumps/*.sql.gz | wc -l | tr -d ' ')" "PG_DUMP_KEEP=2 giữ đúng 2 bản"
assert_eq "2" "$(ls "$VOL"/pg/dumps/*.meta   | wc -l | tr -d ' ')" "prune xoá .meta theo cùng"
[ -e "$(readlink "$VOL/pg/latest")" ] && ok "latest vẫn trỏ file có thật sau prune" \
                                      || bad "latest trỏ file đã bị prune xoá"
# KEEP=1 mà chỉ có 1 bản: không được xoá sạch
rm -rf "$VOL/pg"; PG_DUMP_KEEP=1 bash "$SCRIPT" --dump >/dev/null
assert_eq "1" "$(ls "$VOL"/pg/dumps/*.sql.gz | wc -l | tr -d ' ')" "KEEP=1 vẫn giữ lại bản duy nhất"
# KEEP=0 (gõ nhầm trong .env) KHÔNG được xoá sạch. Đây là bất biến quan trọng nhất của _prune:
# mất một bản cũ chỉ là mất tiện; mất bản CUỐI CÙNG là mất hẳn dữ liệu.
rm -rf "$VOL/pg"; seed
PG_DUMP_KEEP=0 bash "$SCRIPT" --dump >/dev/null; sleep 1
PG_DUMP_KEEP=0 bash "$SCRIPT" --dump >/dev/null; sleep 1
PG_DUMP_KEEP=0 bash "$SCRIPT" --dump >/dev/null
N0="$(ls "$VOL"/pg/dumps/*.sql.gz 2>/dev/null | wc -l | tr -d ' ')"
[ "$N0" -ge 1 ] && ok "KEEP=0 vẫn giữ lại ít nhất 1 bản (không xoá sạch)" \
                || bad "KEEP=0 đã xoá SẠCH backup — còn $N0 bản"
[ -e "$(readlink "$VOL/pg/latest")" ] && ok "KEEP=0: latest vẫn trỏ file có thật" \
                                      || bad "KEEP=0: latest thành symlink treo"
# KEEP không phải số cũng không được xoá sạch
rm -rf "$VOL/pg"; seed
PG_DUMP_KEEP=abc bash "$SCRIPT" --dump >/dev/null; sleep 1
PG_DUMP_KEEP=abc bash "$SCRIPT" --dump >/dev/null
NA="$(ls "$VOL"/pg/dumps/*.sql.gz 2>/dev/null | wc -l | tr -d ' ')"
[ "$NA" -ge 1 ] && ok "KEEP không phải số vẫn giữ lại ít nhất 1 bản" \
                || bad "KEEP=abc đã xoá sạch backup"

echo
info "$PASSED xanh · $FAILED đỏ"
[ "$FAILED" -eq 0 ]
