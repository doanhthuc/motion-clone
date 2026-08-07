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
assert_eq "" "$(q "SELECT 1 FROM pg_database WHERE datname='motion_verify'")" \
  "--verify vẫn xoá DB tạm khi hỏng"

rm -rf "$VOL/pg"
assert_fail "--check đỏ khi chưa có dump nào" -- bash "$SCRIPT" --check

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

echo
info "$PASSED xanh · $FAILED đỏ"
[ "$FAILED" -eq 0 ]
