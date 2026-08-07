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

cleanup() {
  docker rm -f "$CTR" >/dev/null 2>&1
  # Gỡ ảnh FAT TRƯỚC khi xoá thư mục: còn mount mà rm -rf thì vừa không xoá được thư mục
  # gốc, vừa để lại một disk image gắn lơ lửng cho lần chạy sau vấp phải.
  [ -n "${FATMNT:-}" ] && hdiutil detach "$FATMNT" -force >/dev/null 2>&1
  [ -n "${FATDMG:-}" ] && rm -f "$FATDMG"
  [ -n "${FATDIR:-}" ] && rm -rf "$FATDIR"
  [ -n "${SYMV:-}"   ] && rm -rf "$SYMV"
  [ -n "${VOL:-}"    ] && rm -rf "$VOL"
  return 0
}
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

# `latest` là symlink TƯƠNG ĐỐI ("dumps/motion-….sql.gz") nên `readlink` trần trả về một
# đường dẫn chỉ có nghĩa khi giải theo thư mục CHỨA link, không theo cwd của test.
latest_target() {
  local t
  t="$(readlink "$VOL/pg/latest" 2>/dev/null)" || return 1
  [ -n "$t" ] || return 1
  case "$t" in /*) printf '%s' "$t" ;; *) printf '%s' "$VOL/pg/$t" ;; esac
}

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
D2="$(latest_target)"
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
[ -e "$(latest_target)" ] && ok "latest vẫn trỏ file có thật sau prune" \
                                      || bad "latest trỏ file đã bị prune xoá"
# KEEP=1 mà chỉ có 1 bản: không được xoá sạch
rm -rf "$VOL/pg"; PG_DUMP_KEEP=1 bash "$SCRIPT" --dump >/dev/null
assert_eq "1" "$(ls "$VOL"/pg/dumps/*.sql.gz | wc -l | tr -d ' ')" "KEEP=1 vẫn giữ lại bản duy nhất"
# KEEP=0 (gõ nhầm trong .env) KHÔNG được xoá sạch. Đây là bất biến quan trọng nhất của _prune:
# mất một bản cũ chỉ là mất tiện; mất bản CUỐI CÙNG là mất hẳn dữ liệu.
rm -rf "$VOL/pg"; seed
PG_DUMP_KEEP=0 bash "$SCRIPT" --dump >/dev/null; sleep 1
PG_DUMP_KEEP=0 bash "$SCRIPT" --dump >/dev/null
N0="$(ls "$VOL"/pg/dumps/*.sql.gz 2>/dev/null | wc -l | tr -d ' ')"
[ "$N0" -ge 1 ] && ok "KEEP=0 vẫn giữ lại ít nhất 1 bản (không xoá sạch)" \
                || bad "KEEP=0 đã xoá SẠCH backup — còn $N0 bản"
[ -e "$(latest_target)" ] && ok "KEEP=0: latest vẫn trỏ file có thật" \
                                      || bad "KEEP=0: latest thành symlink treo"
# KEEP không phải số. Thứ phân biệt code vá với chưa vá ở đây KHÔNG phải exit code và cũng
# không phải số file còn lại — cả hai đều giống nhau ở hai bên:
#   - $((n - keep)) với keep="abc" làm bash coi "abc" là TÊN BIẾN chưa đặt → set -u báo lỗi,
#     nhưng lỗi đó xảy ra khi mở rộng tham số cho MỘT PHẦN TỬ PIPELINE nên chỉ giết subshell
#     của `head`. Shell cha sống tiếp, không có set -e, _prune không được kiểm return → script
#     vẫn thoát 0 và vẫn còn nguyên file. Đo thật: PIPESTATUS=0 0 0.
# Dấu hiệu quan sát được duy nhất là STDERR: chưa vá thì có "unbound variable", vá rồi thì sạch.
rm -rf "$VOL/pg"; seed
PG_DUMP_KEEP=abc bash "$SCRIPT" --dump >/dev/null 2>"$VOL/abc-stderr.txt"; sleep 1
PG_DUMP_KEEP=abc bash "$SCRIPT" --dump >/dev/null 2>>"$VOL/abc-stderr.txt"
if grep -q "unbound variable" "$VOL/abc-stderr.txt"; then
  bad "KEEP=abc: script báo 'unbound variable' — keep chưa được kẹp trước khi vào \$(( ))"
else
  ok "KEEP=abc: không có 'unbound variable' trên stderr"
fi
NA="$(ls "$VOL"/pg/dumps/*.sql.gz 2>/dev/null | wc -l | tr -d ' ')"
[ "$NA" -ge 1 ] && ok "KEEP=abc vẫn giữ lại ít nhất 1 bản" || bad "KEEP=abc đã xoá sạch backup"
# KEEP âm là trường hợp XẤU NHẤT, và là trường hợp DUY NHẤT không tự lộ ra: khác "abc",
# số âm là số nguyên hợp lệ nên không sập ở $(( )). Trên code chưa vá, head -n $((n+2))
# lặng lẽ trả về TẤT CẢ dòng đang có → xoá sạch, không lỗi, không cảnh báo.
rm -rf "$VOL/pg"; seed
PG_DUMP_KEEP=-2 bash "$SCRIPT" --dump >/dev/null 2>&1; sleep 1
PG_DUMP_KEEP=-2 bash "$SCRIPT" --dump >/dev/null 2>&1
NN="$(ls "$VOL"/pg/dumps/*.sql.gz 2>/dev/null | wc -l | tr -d ' ')"
[ "$NN" -ge 1 ] && ok "KEEP âm vẫn giữ lại ít nhất 1 bản" || bad "KEEP=-2 đã xoá sạch backup"
[ -e "$(latest_target)" ] && ok "KEEP âm: latest vẫn trỏ file có thật" \
                                      || bad "KEEP âm: latest thành symlink treo"

# ── Task 3 ────────────────────────────────────────────────────────────────
info "Task 3 — --restore"
rm -rf "$VOL/pg"; seed
bash "$SCRIPT" --dump >/dev/null
q "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" >/dev/null   # giả lập pod mới: DB trống
assert_ok "--restore trả 0 khi DB trống" -- bash "$SCRIPT" --restore
assert_eq "7" "$(q 'SELECT count(*) FROM jobs')"  "restore trả lại đủ dòng bảng jobs"
assert_eq "3" "$(q 'SELECT count(*) FROM users')" "restore trả lại đủ dòng bảng users"

# Chạy lại trên DB ĐÃ CÓ dữ liệu: phải bỏ qua, KHÔNG nhân đôi, và KHÔNG coi là lỗi
assert_ok "--restore trả 0 khi DB đã có dữ liệu" -- bash "$SCRIPT" --restore
assert_eq "7" "$(q 'SELECT count(*) FROM jobs')" "restore không nạp đè khi DB đã có bảng"

# In tuổi bản dump ra stdout
#
# Bắt buộc chạy XONG rồi mới grep (không phải pipe sống): `grep -q` thoát ngay khi khớp
# dòng ĐẦU, và nếu do_restore còn ghi thêm dòng log sau đó (ví dụ "DB đã có ... Bỏ qua."),
# writer nhận SIGPIPE giữa lúc chạy → bash thoát 141 → với `pipefail` đang bật, TOÀN BỘ
# pipeline báo lỗi dù grep ĐÃ khớp — sai ngẫu nhiên, không liên quan gì tới do_restore().
RESTORE_OUT="$(bash "$SCRIPT" --restore 2>&1)"
printf '%s' "$RESTORE_OUT" | grep -qi "tuổi" && ok "restore in tuổi bản dump" \
                                              || bad "restore không in tuổi bản dump"

# Chưa có dump nào → bỏ qua, không phải lỗi
rm -rf "$VOL/pg"
assert_ok "--restore trả 0 khi chưa có dump nào" -- bash "$SCRIPT" --restore

# File dump HỎNG → phải đỏ, và DB phải còn TRỐNG (rollback sạch, không nạp nửa chừng).
#
# Làm hỏng bằng cách NỐI THÊM một câu SQL sai vào CUỐI dump, không phải cắt cụt đầu file.
# Cắt cụt ở 400 byte đầu chỉ lấy được phần comment + SET của pg_dump — toàn statement hợp lệ,
# psql chạy xong trả 0, và test sẽ đỏ vì lý do sai hoàn toàn. Nối lỗi vào cuối thì psql tạo
# xong hết bảng RỒI mới gặp lỗi, nên nó kiểm đúng thứ ta cần kiểm: --single-transaction có
# thật sự rollback những bảng đã tạo hay không.
rm -rf "$VOL/pg"; seed; bash "$SCRIPT" --dump >/dev/null
q "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" >/dev/null
D="$(latest_target)"
{ gzip -dc "$D"; echo "SELECT * FROM khong_ton_tai_bang_nay;"; } | gzip -c > "$D.part"
mv "$D.part" "$D"
assert_fail "--restore đỏ khi dump có statement lỗi" -- bash "$SCRIPT" --restore
assert_eq "0" "$(q "SELECT count(*) FROM pg_tables WHERE schemaname='public'")" \
  "dump lỗi KHÔNG để lại bảng nào — chứng minh --single-transaction rollback thật"

# Guard "DB đã có bảng" tồn tại để chặn ĐÚNG kịch bản này, và đây là kịch bản duy nhất chứng
# minh được nó. Nạp lại chính bản dump của DB đó KHÔNG chứng minh gì: pg_dump sinh CREATE TABLE
# trước COPY, nên nó chết ở "relation already exists" — tức bị chặn bởi một cơ chế khác hẳn,
# xoá guard đi thì test vẫn xanh.
# Ở đây DB có sẵn một bảng KHÁC hoàn toàn với bảng trong dump, nên không tên nào đụng nhau:
# gỡ guard là dump chảy thẳng vào DB đang có dữ liệu, exit 0, không một lời cảnh báo.
rm -rf "$VOL/pg"; seed
bash "$SCRIPT" --dump >/dev/null                       # dump chứa jobs + users
q "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" >/dev/null
q "CREATE TABLE sessions (id int primary key);
   INSERT INTO sessions VALUES (1);" >/dev/null        # DB có dữ liệu, nhưng bảng KHÁC
assert_ok "--restore trả 0 khi DB có bảng rời rạc" -- bash "$SCRIPT" --restore
assert_eq "1" "$(q "SELECT count(*) FROM pg_tables WHERE schemaname='public'")" \
  "restore KHÔNG đổ dump vào DB đang có bảng khác (vẫn đúng 1 bảng)"
assert_eq "" "$(q "SELECT to_regclass('public.jobs')")" \
  "bảng jobs từ dump KHÔNG được tạo ra"

# ── Task 4 ────────────────────────────────────────────────────────────────
info "Task 4 — --check và --verify"
rm -rf "$VOL/pg"; seed; bash "$SCRIPT" --dump >/dev/null

assert_ok "--check trả 0 khi có dump" -- bash "$SCRIPT" --check
bash "$SCRIPT" --check 2>&1 | grep -q "jobs=7" && ok "--check in số dòng từ .meta" \
                                               || bad "--check không in số dòng"
assert_eq "7" "$(q 'SELECT count(*) FROM jobs')" "--check không sửa DB thật"

assert_ok "--verify trả 0 khi dump khớp .meta" -- bash "$SCRIPT" --verify
assert_eq "" "$(q "SELECT 1 FROM pg_database WHERE datname='motion_verify'")" \
  "--verify xoá DB tạm sau khi xong"

# .meta bị sửa lệch → verify PHẢI đỏ. Đây là lý do .meta tồn tại.
M="$(ls "$VOL"/pg/dumps/*.meta | head -1)"
sed -i '' 's/^jobs=7$/jobs=999/' "$M"
assert_fail "--verify đỏ khi số dòng lệch .meta" -- bash "$SCRIPT" --verify
# Vế NGƯỢC của chẩn đoán có điều kiện: .meta này KHÔNG mang cờ meta_incomplete (nó bị sửa tay),
# nên chẩn đoán "file hỏng / .meta bị sửa tay" ở đây là ĐÚNG hướng và phải còn nguyên. Không có
# assertion này thì bản vá finding 4 có thể lặng lẽ nuốt mất nhánh else mà không ai biết.
printf '%s' "$(bash "$SCRIPT" --verify 2>&1)" | grep -q "nghi ngờ file hỏng" \
  && ok ".meta sửa tay (không có cờ): --verify vẫn chẩn đoán 'file hỏng / .meta bị sửa tay'" \
  || bad ".meta sửa tay: MẤT chẩn đoán đúng hướng — bản vá nuốt luôn nhánh else"
assert_eq "" "$(q "SELECT 1 FROM pg_database WHERE datname='motion_verify'")" \
  "--verify vẫn xoá DB tạm khi hỏng"

rm -rf "$VOL/pg"
assert_fail "--check đỏ khi chưa có dump nào" -- bash "$SCRIPT" --check

# ── Khử đua: .meta phải đếm từ FILE DUMP, không phải từ một truy vấn DB thứ hai ──────────
# pg_dump chụp snapshot riêng. Nếu .meta lấy số bằng cách hỏi DB lần nữa SAU khi pg_dump xong
# thì một INSERT xen vào giữa làm .meta lệch với nội dung file — và lệch VĨNH VIỄN, nên mọi
# --verify sau này trên bản dump ấy đều đỏ dù dump hoàn toàn tốt. gpu-down dump khi api/worker
# vẫn online, và lớp 6 pod-smoke.sh dùng `bad`, nên đây là đường làm gpu-smoke đỏ trên pod khoẻ.
#
# CÁCH DỰNG ĐUA CHO TẤT ĐỊNH — quan trọng, đừng đơn giản hoá:
# Ghi vào DB *sau khi* `--dump` đã trả về thì KHÔNG tái hiện được gì (thử rồi: xanh trên cả code
# vá lẫn chưa vá), vì lúc đó cả pg_dump lẫn lần đếm .meta đều đã chạy xong. Cửa sổ đua nằm ĐÚNG
# giữa hai mốc đó, và bình thường nó rộng vài mili-giây.
# Mở nó ra bằng khoá: một session giữ ACCESS EXCLUSIVE trên `users` làm pg_dump chặn ở bước
# `LOCK TABLE`, mà bước đó chạy SAU khi pg_dump đã lấy snapshot REPEATABLE READ. Ghi vào `jobs`
# trong lúc pg_dump đang chờ ⇒ file dump giữ 7 dòng (snapshot cũ), còn DB thật thành 14.
#   - cách tính CŨ (_row_counts sau pg_dump): .meta ghi 14, file có 7 → --verify ĐỎ vĩnh viễn.
#   - cách tính MỚI (_dump_row_counts đọc file): .meta ghi 7 → --verify XANH.
info "Khử đua — .meta đếm từ chính file dump"
rm -rf "$VOL/pg"; seed
( q "BEGIN; LOCK TABLE users IN ACCESS EXCLUSIVE MODE; SELECT pg_sleep(8); COMMIT;" ) >/dev/null 2>&1 &
LOCKER=$!
sleep 1
bash "$SCRIPT" --dump >/dev/null 2>&1 &
DUMPER=$!
# Tiền đề PHẢI kiểm: nếu pg_dump chưa kịp lấy snapshot và chưa chờ khoá thì INSERT bên dưới lọt
# VÀO trong snapshot, file dump có 14 dòng, và test xanh một cách vô nghĩa trên cả hai bản code.
BLOCKED=0
for _ in $(seq 1 60); do
  W="$(q "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type='Lock' AND query ILIKE 'LOCK TABLE%'")"
  [ "${W:-0}" -ge 1 ] 2>/dev/null && { BLOCKED=1; break; }
  sleep 0.5
done
[ "$BLOCKED" = 1 ] && ok "tiền đề: pg_dump đã lấy snapshot và đang chờ khoá" \
                   || bad "tiền đề KHÔNG dựng được: pg_dump không chờ khoá — hai assertion sau vô nghĩa"
q "INSERT INTO jobs SELECT g, 'motion' FROM generate_series(100,106) g;" >/dev/null
wait "$LOCKER" 2>/dev/null; wait "$DUMPER" 2>/dev/null
M3="$(ls "$VOL"/pg/dumps/*.meta | head -1)"
assert_eq "14" "$(q 'SELECT count(*) FROM jobs')" "DB thật ĐÃ đổi trong lúc pg_dump chạy (7→14)"
assert_eq "7" "$(grep '^jobs=' "$M3" | cut -d= -f2)" \
  ".meta ghi số dòng CỦA FILE DUMP (7), không phải của DB tại lúc đếm (14)"
assert_ok "--verify XANH dù có ghi xen giữa snapshot pg_dump và lúc ghi .meta" -- bash "$SCRIPT" --verify

# Bảng rỗng vẫn phải có mặt trong .meta: khối COPY của nó tồn tại với 0 dòng. Thiếu dòng này
# thì `want` cụt so với `got` (_row_counts liệt kê MỌI bảng public) và verify đỏ oan.
rm -rf "$VOL/pg"; seed
q "CREATE TABLE trong_rong (id int);" >/dev/null
bash "$SCRIPT" --dump >/dev/null
M4="$(ls "$VOL"/pg/dumps/*.meta | head -1)"
assert_eq "0" "$(grep '^trong_rong=' "$M4" | cut -d= -f2)" "bảng rỗng vào .meta với 0 dòng"
assert_ok "--verify xanh khi có bảng rỗng" -- bash "$SCRIPT" --verify

# Tên bảng bị pg_dump trích dẫn kép (từ khoá, hoặc có dấu cách/ngoặc) phải cắt được về tên TRẦN
# cho khớp _row_counts. pg_dump sinh thật `COPY public."weird name (x)" (id) FROM stdin;` —
# cắt tên bảng từ dấu "(" ĐẦU TIÊN là hỏng ngay ở đây.
# Dữ liệu cũng cài hai cái bẫy: một ô đúng bằng `\.` (pg_dump thoát thành `\\.`, không được
# tính là hết khối) và một ô trông y hệt một dòng header COPY.
rm -rf "$VOL/pg"; seed
q 'CREATE TABLE "order" (id int primary key, note text)' >/dev/null
q $'INSERT INTO "order" VALUES (1, E\'\\\\.\'), (2, \'COPY public.gia_mao (x) FROM stdin;\')' >/dev/null
q 'CREATE TABLE "weird name (x)" (id int); INSERT INTO "weird name (x)" VALUES (9);' >/dev/null
bash "$SCRIPT" --dump >/dev/null
M5="$(ls "$VOL"/pg/dumps/*.meta | head -1)"
assert_eq "2" "$(grep '^order=' "$M5" | cut -d= -f2)" \
  'tên bảng trích dẫn kép ("order") vào .meta ở dạng trần, đếm đúng'
assert_eq "1" "$(grep '^weird name (x)=' "$M5" | cut -d= -f2)" \
  'tên bảng có dấu cách + ngoặc ("weird name (x)") đếm đúng'
assert_ok "--verify xanh với tên bảng trích dẫn + dữ liệu chứa bẫy \\. và COPY" -- bash "$SCRIPT" --verify
q 'DROP TABLE "order"; DROP TABLE "weird name (x)";' >/dev/null

# ── Ba điểm mù của parser header COPY ─────────────────────────────────────
# Cả ba đều đo bằng pg_dump THẬT (postgres:18), không bằng chuỗi tự bịa: chính pg_dump quyết
# định header trông ra sao, và ở hai dạng đầu nó trông khác hẳn hình dung ban đầu.
#
# Bất biến chung, quan trọng hơn cả việc đếm đúng: KHÔNG BAO GIỜ phát ra KHOÁ RÁC. Một khoá rác
# trong .meta làm --verify ĐỎ trên một bản dump hoàn toàn tốt, tức phá đúng cái cổng bằng-chứng
# mà cả tính năng này dựa vào. Vì thế mỗi khối dưới đây đều có một assertion kiểm bất biến đó.
#
# Kiểm ĐÚNG BẤT BIẾN, không kiểm hình dạng rác CŨ. Bản trước dùng `grep -c 'FROM stdin;' == 0`,
# tức neo vào đúng hai khoá rác mà hai lỗi đã sửa từng phát ra — nên nó mù với mọi hình dạng rác
# khác. Đo bằng mutant: bỏ ` > "/dev/stderr"` ở khối END của _dump_row_counts (in dòng lỗi thẳng
# vào stdout, mà stdout của hàm đó CHÍNH LÀ .meta — đúng thứ ba dòng comment ở pod-pgdump.sh:174
# nói mình đang bịt) làm .meta chứa khoá rác thật, mà TOÀN BỘ 93 assertion vẫn xanh.
# Bất biến thật đang khai là: MỌI dòng trong .meta phải là `<khoá>=<số>` — hoặc khoá hệ thống,
# hoặc `<tên bảng>=<số dòng>`. Đếm số dòng KHÔNG khớp; phải bằng 0, bất kể rác trông ra sao.
meta_junk() {
  grep -cvE '^(created|pg_version|dump_bytes|meta_incomplete)=|^[^=]+=[0-9]+$' "$1" | tr -d ' '
}

# (1) Bảng 0 CỘT: pg_dump phát `COPY public.t0  FROM stdin;` — KHÔNG có danh sách cột, chỉ còn
#     một dấu cách thừa. Regex cũ đòi `\(…\)` nên trượt → khoá `t0  FROM stdin;=2`.
# (2) Tên CỘT chứa ngoặc: `COPY public.parencol ("col (x)") FROM stdin;` — lớp `[^()]*` của regex
#     cũ không nhảy qua nổi cặp ngoặc lồng bên trong → khoá `parencol ("col (x)") FROM stdin;=2`.
rm -rf "$VOL/pg"; seed
q 'CREATE TABLE t0 (); INSERT INTO t0 DEFAULT VALUES; INSERT INTO t0 DEFAULT VALUES;' >/dev/null
q 'CREATE TABLE parencol ("col (x)" int); INSERT INTO parencol VALUES (1),(2);' >/dev/null
assert_ok "bảng 0 cột + tên cột chứa ngoặc: --dump trả 0" -- bash "$SCRIPT" --dump
M7="$(ls "$VOL"/pg/dumps/*.meta | head -1)"
assert_eq "2" "$(grep '^t0=' "$M7" | cut -d= -f2)"       "bảng 0 CỘT vào .meta với khoá TRẦN t0="
assert_eq "2" "$(grep '^parencol=' "$M7" | cut -d= -f2)" "tên cột chứa ngoặc: khoá TRẦN parencol="
assert_eq "0" "$(meta_junk "$M7")" \
  ".meta chỉ chứa dòng <khoá>=<số> — không một dòng rác nào, dưới MỌI hình dạng"
assert_ok "--verify xanh với bảng 0 cột và tên cột chứa ngoặc" -- bash "$SCRIPT" --verify
q 'DROP TABLE t0; DROP TABLE parencol;' >/dev/null

# (3) Identifier chứa XUỐNG DÒNG. pg_dump phát header vỡ làm hai dòng vật lý; awk đọc theo dòng
#     nên không ghép lại được ở dạng hiện tại.
#     ĐÍNH CHÍNH 2026-08-07: comment cũ ở đây nói "sửa ở parser cũng vô ích vì định dạng .meta
#     không biểu diễn nổi dạng này". SAI — đo rồi: `_row_counts` (vế `got`) vỡ thành `new` +
#     `line=1`, nhưng vế `want` đọc từ .meta cũng vỡ Y HỆT và cả hai đều `sort`, nên chúng trùng
#     khít; nối đúng hai dòng đó vào .meta rồi `--verify` → XANH, rc=0. Sửa ở parser thôi cũng đủ.
#     Quyết định BÁO LỖI TO vẫn giữ, vì lý do khác: biểu diễn ấy NHẬP NHẰNG — verify xanh một
#     cách tình cờ (hai lỗi vỡ khử nhau) và đếm "3 bảng" cho một DB có 2 bảng.
#     Yêu cầu ở đây vì thế KHÔNG phải "đếm đúng" mà là: BÁO LỖI TO thay vì đoán.
rm -rf "$VOL/pg"; seed
q 'CREATE TABLE "new
line" (id int); INSERT INTO "new
line" VALUES (1);' >/dev/null
NL_OUT="$(bash "$SCRIPT" --dump 2>&1)"; NL_RC=$?
assert_eq "1" "$NL_RC" "identifier có xuống dòng: --dump trả khác 0 — không bỏ qua trong im lặng"
printf '%s' "$NL_OUT" | grep -q "header COPY không parse được" \
  && ok "identifier có xuống dòng: in ra ĐÚNG dòng header không parse được" \
  || bad "identifier có xuống dòng: không nói gì — parser nuốt lỗi"
printf '%s' "$NL_OUT" | grep -q ".meta THIẾU BẢNG" \
  && ok "identifier có xuống dòng: nói rõ hệ quả (.meta cụt ⇒ --verify sau này đỏ)" \
  || bad "identifier có xuống dòng: không giải thích hệ quả — người đọc không truy ra được"
M8="$(ls "$VOL"/pg/dumps/*.meta | head -1)"
assert_eq "0" "$(meta_junk "$M8")" \
  "identifier có xuống dòng: .meta thiếu bảng NHƯNG mọi dòng còn lại vẫn là <khoá>=<số>"
# Đỏ ở đây là cảnh báo về .meta, KHÔNG phải mất backup — bản dump vẫn phải ghi xong và hợp lệ.
D8="$(ls "$VOL"/pg/dumps/motion-*.sql.gz 2>/dev/null | head -1)"
{ [ -n "$D8" ] && gzip -t "$D8" 2>/dev/null; } \
  && ok "identifier có xuống dòng: bản dump VẪN ghi xong và hợp lệ (gzip -t xanh)" \
  || bad "identifier có xuống dòng: mất bản dump — đỏ ở .meta không được kéo theo mất backup"

# …và LỜI GIẢI THÍCH phải đi theo BẢN DUMP, không chỉ theo lần chạy này. Cảnh báo stderr của
# --dump biến mất khi terminal cuộn qua; hệ quả (.meta cụt ⇒ mọi --verify sau này đỏ) thì sống
# cùng file. Không có cờ trong .meta, ba tuần sau lớp 6 gpu-smoke đỏ và --verify chẩn đoán
# "nghi ngờ file hỏng, hoặc .meta bị sửa tay" — CHỈ SAI HƯỚNG: file dump hoàn toàn lành, người
# đọc đi soi gzip/mtime vô ích. Đúng cái bẫy mà khối cảnh báo cuối do_dump tồn tại để chặn.
assert_eq "1" "$(grep -c '^meta_incomplete=1' "$M8" | tr -d ' ')" \
  "identifier có xuống dòng: .meta mang CỜ meta_incomplete=1 đi cùng bản dump"
CHK8="$(bash "$SCRIPT" --check 2>&1)"
printf '%s' "$CHK8" | grep -q 'meta_incomplete' \
  && bad "cờ meta_incomplete lọt vào danh sách bảng của --check — bị đếm nhầm thành một bảng" \
  || ok "cờ meta_incomplete KHÔNG lọt vào danh sách bảng của --check"
printf '%s' "$CHK8" | grep -q "THIẾU BẢNG" \
  && ok "--check nói thẳng danh sách bảng là KHÔNG ĐẦY ĐỦ" \
  || bad "--check in danh sách cụt mà không nói nó cụt"
V8="$(bash "$SCRIPT" --verify 2>&1)"; V8_RC=$?
assert_eq "1" "$V8_RC" "identifier có xuống dòng: --verify đỏ (đúng như --dump đã báo trước)"
printf '%s' "$V8" | grep -q "ĐÁNH DẤU KHÔNG ĐẦY ĐỦ" \
  && ok "--verify đỏ nhắc lại LÝ DO THẬT (.meta cụt từ lúc --dump)" \
  || bad "--verify đỏ không nhắc lý do thật — người đọc phải tự truy ngược"
printf '%s' "$V8" | grep -q "nghi ngờ file hỏng" \
  && bad "--verify đỏ vẫn chẩn đoán 'file hỏng / .meta bị sửa tay' — CHỈ SAI HƯỚNG" \
  || ok "--verify đỏ KHÔNG chẩn đoán sai hướng 'file hỏng / .meta bị sửa tay'"
q 'DROP TABLE "new
line";' >/dev/null

# (4) Dòng mở đầu bằng `COPY ` mà KHÔNG PHẢI header — pg_dump chép nguyên văn thân
#     `CREATE FUNCTION $$…$$`, nên một dòng SQL hướng dẫn nằm trong đó bắt đầu ngay ở cột 0.
#     Đây là hình dạng THẬT (đo bằng pg_dump 18): dump chứa cả
#         COPY public.jobs FROM /tmp/x.csv CSV;      ← trong thân function, KHÔNG phải header
#         COPY public.jobs (id, kind) FROM stdin;    ← header thật
#     Nhận diện header quá rộng ⇒ dòng đầu bị coi là "header không parse được" ⇒ --dump trả 1
#     kèm thông điệp SAI SỰ THẬT (".meta THIẾU BẢNG" trong khi .meta đầy đủ, --verify sau đó xanh).
#     Hệ quả vận hành: `make gpu-down` in "sao lưu DB thất bại" mỗi lần và `make gpu-db-dump`
#     (Makefile:156-161, không có `|| echo`) hỏng thẳng — báo động giả thường trực trên dump tốt.
#     Chữ ký của header VỠ DÒNG là số dấu " LẺ; dòng này có 0 dấu " nên phải được bỏ qua như cũ.
rm -rf "$VOL/pg"; seed
q 'CREATE FUNCTION docnote() RETURNS text LANGUAGE sql AS $fn$
SELECT $doc$huong dan nap lai:
COPY public.jobs FROM /tmp/x.csv CSV;
xong$doc$::text
$fn$;' >/dev/null
FN_OUT="$(bash "$SCRIPT" --dump 2>&1)"; FN_RC=$?
assert_eq "0" "$FN_RC" "dòng COPY trong thân function: --dump trả 0 (KHÔNG phải header vỡ)"
printf '%s' "$FN_OUT" | grep -q ".meta THIẾU BẢNG" \
  && bad "dòng COPY trong thân function: báo '.meta THIẾU BẢNG' — sai sự thật, .meta vẫn đầy đủ" \
  || ok "dòng COPY trong thân function: không báo động giả '.meta THIẾU BẢNG'"
M9="$(ls "$VOL"/pg/dumps/*.meta | head -1)"
assert_eq "7" "$(grep '^jobs=' "$M9" | cut -d= -f2)" \
  "dòng COPY trong thân function: .meta VẪN đếm đủ bảng jobs"
assert_ok "dòng COPY trong thân function: --verify xanh" -- bash "$SCRIPT" --verify
q 'DROP FUNCTION docnote();' >/dev/null

# Bảng ở schema KHÁC public không được lọt vào .meta: _row_counts (vế `got` của verify) chỉ
# đếm schema public, nên thêm vào là verify đỏ oan. Nhưng khối COPY của nó vẫn phải được ĐỌC
# HẾT, nếu không các dòng dữ liệu của nó bị tính nhầm sang bảng sau.
#
# BẢNG MỘT CỘT TEXT, và dòng dữ liệu đầu là một header COPY GIẢ của `public.jobs` — cả hai
# chi tiết đều cần thiết, đừng đơn giản hoá:
#   - một cột: dòng dữ liệu của pg_dump là các ô nối bằng TAB, nên chuỗi bẫy chỉ nằm ở CỘT ĐẦU
#     mới bắt đầu ở cột 0 của dòng. Bảng hai cột cho `2<TAB>COPY public.jobs …` — không khớp
#     `/^COPY /` và cái bẫy im lặng vô hiệu.
#   - bẫy đặt ở bảng NON-PUBLIC (không phải ở `"order"` như khối trên): hành vi cần bảo vệ là
#     "khối non-public vẫn phải được ĐỌC HẾT". Nếu code coi khối non-public là không-vào-khối
#     (`intbl = pub`), dòng bẫy này được nhận nhầm làm header và sinh thêm một khoá `jobs=`.
rm -rf "$VOL/pg"; seed
q "CREATE SCHEMA khac; CREATE TABLE khac.thing (note text);
   INSERT INTO khac.thing VALUES ('COPY public.jobs (id, kind) FROM stdin;'), ('x');" >/dev/null
bash "$SCRIPT" --dump >/dev/null
M6="$(ls "$VOL"/pg/dumps/*.meta | head -1)"
# grep theo MỌI dạng khoá có thể phát ra, không neo `^thing=`: nhánh non-public của
# _dump_row_counts KHÔNG cắt tiền tố schema, nên khi bộ lọc `pub` hỏng nó ghi ra `khac.thing=2`
# chứ không bao giờ ghi `thing=2` — neo `^thing=` là một assertion không thể đỏ.
assert_eq "0" "$(grep -cE '(^|\.)thing=' "$M6" | tr -d ' ')" \
  "bảng ngoài schema public KHÔNG vào .meta (dưới MỌI dạng khoá, kể cả khac.thing=)"
assert_eq "7" "$(grep '^jobs=' "$M6" | cut -d= -f2)" \
  "khối COPY non-public được ĐỌC HẾT — dòng bẫy trong nó không sinh ra khoá jobs thứ hai"
# --verify là đường bắt thứ hai, độc lập với hai grep trên: khoá thừa (khac.thing=2) hay khoá
# lặp (jobs=1 + jobs=7) đều làm `want` lệch `got` và verify đỏ. Không có nó thì cả khối này
# không chạy verify lần nào, và một .meta rác vẫn lọt.
assert_ok "--verify xanh khi có bảng ở schema khác public" -- bash "$SCRIPT" --verify
q "DROP SCHEMA khac CASCADE;" >/dev/null

# Dump từ một DB CHƯA CÓ BẢNG NÀO vẫn là bản dump hợp lệ, và --check phải nói đúng như vậy.
# Đây là đường mà các test trước không chạm tới: mọi seed đều tạo sẵn bảng, nên .meta luôn có
# dòng bảng và cái bẫy exit-code của pipeline cuối hàm không bao giờ lộ ra.
rm -rf "$VOL/pg"
q "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" >/dev/null   # DB rỗng, không bảng nào
bash "$SCRIPT" --dump >/dev/null
assert_ok "--check trả 0 với dump từ DB chưa có bảng" -- bash "$SCRIPT" --check

# ── Fix C2: `latest` KHÔNG được là điểm hỏng đơn ──────────────────────────
# Volume trên pod là MooseFS, và CÙNG mount đó đã chặn `chown` (pod-volume.sh:179-191).
# Chưa ai đo `symlink()` trên nó. Nếu `ln -s` im lặng hỏng thì chuỗi cũ là: --dump vẫn in
# "ok" và trả 0 → --restore chỉ nhìn $LATEST, thấy vắng, nói "đây là phiên đầu" và trả 0
# → lib-feature.sh không warn gì. Mất dữ liệu hoàn chỉnh với exit 0 ở MỌI bước.
info "Fix C2 — latest vắng/treo không được nuốt mất bản dump"

# latest phải là symlink TƯƠNG ĐỐI: pod sau mount volume ở path khác thì link tuyệt đối
# thành treo, còn link tương đối vẫn đúng vì nó nằm CÙNG cây thư mục với đích.
rm -rf "$VOL/pg"; seed; bash "$SCRIPT" --dump >/dev/null
LNK="$(readlink "$VOL/pg/latest")"
case "$LNK" in
  /*) bad "latest là symlink TUYỆT ĐỐI ($LNK) — treo ngay khi volume mount ở path khác" ;;
  "") bad "latest không phải symlink" ;;
  *)  ok "latest là symlink tương đối ($LNK)" ;;
esac

# `ln -s` hỏng → --dump PHẢI đỏ. Giả lập bằng cách biến $PGDIR/latest thành một thư mục
# chỉ-đọc: `ln -sfn <out> <thư-mục>` sẽ cố tạo link BÊN TRONG nó và bị từ chối. do_dump()
# chỉ chmod $PGDIR và $PGDIR/dumps nên không tự gỡ được mode 500 này.
# Lưu ý: bản dump và .meta VẪN được ghi xong trước khi die — đỏ ở đây là cảnh báo
# "latest không tin được nữa", không phải mất dump. Đường fallback bên dưới chứng minh.
rm -rf "$VOL/pg"; seed; bash "$SCRIPT" --dump >/dev/null
rm -f "$VOL/pg/latest"; mkdir -p "$VOL/pg/latest"; chmod 500 "$VOL/pg/latest"
assert_fail "--dump đỏ khi không tạo được latest" -- bash "$SCRIPT" --dump
chmod 700 "$VOL/pg/latest"; rm -rf "$VOL/pg/latest"

# …và cái đỏ đó KHÔNG được chặn ba thứ nằm sau nó trong do_dump.
# Trên code cũ (`ln -sfn … || die` đặt ngay tại chỗ) thì: _prune không bao giờ chạy nên
# PG_DUMP_KEEP mất tác dụng hoàn toàn trên đúng volume đang hỏng symlink, khối kiểm quyền
# không chạy nên mất cảnh báo "dump đang lộ quyền rộng", và dòng `ok "dump: …"` không in.
# Cả hai assertion dưới đây đỏ trên code cũ, xanh trên code đã vá.
rm -rf "$VOL/pg"; seed
PG_DUMP_KEEP=1 bash "$SCRIPT" --dump >/dev/null; sleep 1
rm -f "$VOL/pg/latest"; mkdir -p "$VOL/pg/latest"; chmod 500 "$VOL/pg/latest"
LN_OUT="$(PG_DUMP_KEEP=1 bash "$SCRIPT" --dump 2>&1)"
assert_eq "1" "$(ls "$VOL"/pg/dumps/motion-*.sql.gz 2>/dev/null | wc -l | tr -d ' ')" \
  "ln hỏng: _prune VẪN chạy (PG_DUMP_KEEP=1 giữ đúng 1 bản)"
printf '%s' "$LN_OUT" | grep -q "dump: motion-" \
  && ok "ln hỏng: vẫn in dòng ok 'dump: …'" \
  || bad "ln hỏng: mất dòng ok 'dump: …' — die chặn mất phần cuối do_dump"
chmod 700 "$VOL/pg/latest"; rm -rf "$VOL/pg/latest"

# latest VẮNG nhưng $DUMPS còn dump hợp lệ → --restore phải NẠP, không được nói "phiên đầu".
rm -rf "$VOL/pg"; seed; bash "$SCRIPT" --dump >/dev/null
rm -f "$VOL/pg/latest"
q "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" >/dev/null
R_OUT="$(bash "$SCRIPT" --restore 2>&1)"; R_RC=$?
assert_eq "0" "$R_RC" "latest vắng: --restore trả 0"
assert_eq "7" "$(q 'SELECT count(*) FROM jobs')" \
  "latest vắng nhưng dumps còn bản hợp lệ → --restore VẪN nạp được"
printf '%s' "$R_OUT" | grep -q "phiên đầu" \
  && bad "latest vắng: --restore nói 'phiên đầu' dù dumps còn dump — mất dữ liệu im lặng" \
  || ok "latest vắng: --restore KHÔNG nói 'phiên đầu'"

# latest TREO (trỏ file đã bị xoá) — cùng đường, khác triệu chứng.
rm -rf "$VOL/pg"; seed; bash "$SCRIPT" --dump >/dev/null
ln -sfn "dumps/motion-khong-ton-tai.sql.gz" "$VOL/pg/latest"
q "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" >/dev/null
assert_ok "latest treo: --restore trả 0" -- bash "$SCRIPT" --restore
assert_eq "7" "$(q 'SELECT count(*) FROM jobs')" "latest treo → --restore vẫn nạp từ dumps/"

# --check và --verify cũng phải đi cùng đường fallback, nếu không lớp 6 của gpu-smoke
# sẽ báo đỏ trên một volume có backup hoàn toàn tốt.
rm -rf "$VOL/pg"; seed; bash "$SCRIPT" --dump >/dev/null; rm -f "$VOL/pg/latest"
assert_ok "latest vắng: --check vẫn trả 0" -- bash "$SCRIPT" --check
assert_ok "latest vắng: --verify vẫn trả 0" -- bash "$SCRIPT" --verify

# Fallback lấy bản MỚI NHẤT theo tên (tên mang timestamp UTC), không phải bản bất kỳ.
rm -rf "$VOL/pg"; seed
bash "$SCRIPT" --dump >/dev/null; sleep 1
q "INSERT INTO jobs SELECT g, 'motion' FROM generate_series(8,9) g;" >/dev/null
bash "$SCRIPT" --dump >/dev/null
rm -f "$VOL/pg/latest"
q "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" >/dev/null
bash "$SCRIPT" --restore >/dev/null 2>&1
assert_eq "9" "$(q 'SELECT count(*) FROM jobs')" "fallback lấy bản MỚI NHẤT, không phải bản cũ"

# Chỉ khi $DUMPS THẬT SỰ rỗng mới được nói "phiên đầu".
rm -rf "$VOL/pg"
E_OUT="$(bash "$SCRIPT" --restore 2>&1)"; E_RC=$?
assert_eq "0" "$E_RC" "volume rỗng thật: --restore trả 0"
printf '%s' "$E_OUT" | grep -q "phiên đầu" && ok "volume rỗng thật: vẫn nói 'phiên đầu'" \
                                           || bad "volume rỗng thật: mất thông điệp 'phiên đầu'"

# ── Quyền dump: fs BỎ QUA chmod ≠ lỗi thật ────────────────────────────────
# Nghiệm thu trên pod RunPod 2026-08-07: MooseFS bỏ qua chmod HOÀN TOÀN (file 666, thư mục
# 777 cố định, chmod trả 0 nhưng không đổi gì) — y như nó đã chặn chown. Khối kiểm quyền cũ
# vì thế ĐỎ ở MỌI lần dump trên pod, nên `make gpu-down` in "sao lưu DB thất bại" mỗi lần dù
# bản dump hoàn hảo. Báo động giả thường trực dạy người dùng bỏ qua cảnh báo.
# Bản vá: DÒ xem fs có tôn trọng mode không rồi mới quyết — không tôn trọng thì nói một dòng
# và trả 0; có tôn trọng mà mode vẫn sai thì giữ nguyên cảnh báo to + khác 0.
#
# Nhánh "fs bỏ qua mode" là nhánh MỚI. Kiểm nó bằng một thư mục tạm bình thường thì assertion
# KHÔNG THỂ ĐỎ (APFS luôn tôn trọng chmod), nên ở đây dựng một filesystem THẬT SỰ bỏ qua
# chmod: ảnh đĩa FAT. Mount bằng -mountpoint vào thư mục tạm, không thả vào /Volumes, để
# hai lần chạy song song không giành nhau cái tên.
info "Quyền dump — phân biệt 'fs bỏ qua chmod' với lỗi thật"

FATDIR="$(mktemp -d)"; FATMNT="$FATDIR/mnt"; FATDMG="$FATDIR/fat.dmg"
mkdir -p "$FATMNT"
FAT_OK=0
if hdiutil create -size 20m -fs MS-DOS -volname MTCPGFAT -o "$FATDMG" >/dev/null 2>&1 \
   && hdiutil attach -mountpoint "$FATMNT" "$FATDMG" >/dev/null 2>&1; then
  FAT_OK=1
else
  FATMNT=""    # không mount được thì cleanup không được gọi detach
fi

# Gọi THẲNG _fs_honors_modes trong script thật, không chép lại logic vào test — chép lại thì
# test chỉ kiểm bản chép. Source được là nhờ khối điều phối cuối pod-pgdump.sh có rào
# ${BASH_SOURCE[0]} = $0. Chạy trong subshell vì script có `set -u` và `cd` ở đầu file.
honors() { ( POD_VOLUME="$1"; export POD_VOLUME; . "$SCRIPT" >/dev/null 2>&1; _fs_honors_modes "$1" ) }

if [ "$FAT_OK" = 1 ]; then
  # TIỀN ĐỀ, phải kiểm chứ không được tin sẵn: nếu chmod lại ĂN trên ảnh FAT này (đổi phiên
  # bản macOS, đổi driver msdos) thì cả nhóm assertion dưới xanh một cách vô nghĩa.
  : > "$FATMNT/probe"; chmod 600 "$FATMNT/probe" 2>/dev/null
  FATMODE="$(stat -f '%Lp' "$FATMNT/probe")"; rm -f "$FATMNT/probe"
  [ "$FATMODE" != "600" ] \
    && ok "tiền đề: FAT thật sự bỏ qua chmod (xin 600, nhận $FATMODE)" \
    || bad "tiền đề KHÔNG dựng được: chmod ĂN trên ảnh FAT — nhóm assertion FAT vô nghĩa"

  assert_fail "_fs_honors_modes: trên FAT trả 'KHÔNG tôn trọng mode'" -- honors "$FATMNT"

  # Assertion trung tâm của bản vá. Trên code CHƯA vá, dòng này đỏ: --dump trả 1.
  rm -rf "$FATMNT/pg"; seed
  FAT_OUT="$(POD_VOLUME="$FATMNT" bash "$SCRIPT" --dump 2>&1)"; FAT_RC=$?
  assert_eq "0" "$FAT_RC" "FAT (fs bỏ qua chmod): --dump trả 0 — hết báo động giả"
  printf '%s' "$FAT_OUT" | grep -q "bỏ qua chmod" \
    && ok "FAT: in dòng thông tin giải thích vì sao không đặt được 600" \
    || bad "FAT: thiếu dòng thông tin — người dùng không biết vì sao mode không đúng"
  printf '%s' "$FAT_OUT" | grep -q "QUYỀN SAI" \
    && bad "FAT: VẪN in cảnh báo to 'QUYỀN SAI' — báo động giả chưa được gỡ" \
    || ok "FAT: không in cảnh báo to 'QUYỀN SAI'"

  # Trả 0 chưa đủ — phải chứng minh bản dump THẬT SỰ tốt, nếu không ta chỉ vừa đổi một
  # báo động giả lấy một thành công giả.
  FATD="$(ls "$FATMNT"/pg/dumps/motion-*.sql.gz 2>/dev/null | head -1)"
  { [ -n "$FATD" ] && gzip -t "$FATD" 2>/dev/null; } \
    && ok "FAT: bản dump ghi ra hợp lệ (gzip -t xanh)" \
    || bad "FAT: không có bản dump hợp lệ — exit 0 đang che một dump hỏng"
  assert_eq "7" "$(grep '^jobs=' "${FATD%.sql.gz}.meta" 2>/dev/null | cut -d= -f2)" \
    "FAT: .meta vẫn đếm đúng bảng jobs"
  assert_ok "FAT: --verify vẫn xanh" -- env POD_VOLUME="$FATMNT" bash "$SCRIPT" --verify
else
  # KHÔNG bỏ qua im lặng. Không dựng được fs bỏ-qua-chmod thì nhánh mới CHƯA được kiểm,
  # và bộ test phải nói thẳng điều đó thay vì báo xanh.
  bad "không dựng được ảnh FAT (hdiutil) — nhánh 'fs bỏ qua chmod' CHƯA ĐƯỢC KIỂM"
fi

# Vế ngược lại: fs CÓ tôn trọng mode thì tín hiệu thật phải còn nguyên.
assert_ok "_fs_honors_modes: trên thư mục tạm (APFS) trả 'CÓ tôn trọng mode'" -- honors "$VOL"

# …và khi đó mà mode vẫn sai thì phải cảnh báo to + khác 0.
#
# ÉP MODE SAI CHO TẤT ĐỊNH — đừng đơn giản hoá thành `chmod 666` lên file dump: file dump CŨ
# không tới được khối kiểm, vì do_dump luôn ghi file MỚI (umask 077 → 600) rồi kiểm chính
# file mới đó. Trên một fs tôn trọng chmod, không cách nào làm chmod của do_dump trượt.
# Cách ép được: cho $PGDIR là một SYMLINK. `chmod 700 $PGDIR` đi XUYÊN link nên sửa thư mục
# ĐÍCH, còn `_mode` dùng `stat` không -L nên đọc mode của chính cái LINK — trên macOS luôn
# là 755. Dựng ra đúng trạng thái cần: fs tôn trọng chmod, nhưng khối kiểm đọc ra mode SAI.
seed
SYMV="$(mktemp -d)"; mkdir -p "$SYMV/pgreal"; ln -s pgreal "$SYMV/pg"
S_OUT="$(POD_VOLUME="$SYMV" bash "$SCRIPT" --dump 2>&1)"; S_RC=$?
assert_eq "1" "$S_RC" "fs tôn trọng mode + mode SAI: --dump vẫn trả khác 0"
printf '%s' "$S_OUT" | grep -q "QUYỀN SAI" \
  && ok "fs tôn trọng mode + mode SAI: vẫn in cảnh báo to 'QUYỀN SAI'" \
  || bad "fs tôn trọng mode + mode SAI: MẤT cảnh báo to — bản vá nuốt luôn tín hiệu thật"
printf '%s' "$S_OUT" | grep -q "bỏ qua chmod" \
  && bad "fs tôn trọng mode: viện cớ 'bỏ qua chmod' — hàm dò trả sai chiều" \
  || ok "fs tôn trọng mode: không viện cớ 'bỏ qua chmod'"
rm -rf "$SYMV"; SYMV=""

echo
info "$PASSED xanh · $FAILED đỏ"
[ "$FAILED" -eq 0 ]
