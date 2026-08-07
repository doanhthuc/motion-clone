# Sao lưu DB sang Network Volume bằng `pg_dump` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Làm cho database sống sót qua `make gpu-destroy`, bằng một bản sao logic (`pg_dump`) đặt trên Network Volume.

**Architecture:** Một script duy nhất trên pod (`setup/pod-pgdump.sh`) với bốn chế độ `--dump/--restore/--check/--verify`; nó là nơi duy nhất biết bố cục thư mục dump. Mọi caller (Makefile, `feature_main()`, `pod-smoke.sh`) chỉ truyền chế độ và đọc exit code. PGDATA **không** đổi chỗ — vẫn ở container disk.

**Tech Stack:** bash, `pg_dump`/`psql` (PostgreSQL 16 trên pod, theo apt của Ubuntu 24.04), gzip. Test chạy ở máy dev với Docker + `psql`.

**Spec:** [`docs/superpowers/specs/2026-08-07-pg-dump-volume-design.md`](../specs/2026-08-07-pg-dump-volume-design.md)

## Global Constraints

Mọi task đều ngầm chịu các ràng buộc này:

- **`VOLUME_PGDATA=0` giữ nguyên.** Không có task nào được đưa PGDATA lên volume — MooseFS chặn `chown` và container không có `/dev/loop*` (commit `d2a9ffc`).
- **Không có cổng chặn nào.** Dump hỏng thì cảnh báo và trả exit code khác 0, nhưng `gpu-down`/`gpu-destroy` **vẫn chạy tiếp**.
- **Không dump định kỳ.** Không thêm app PM2, không cron.
- **Quyền:** thư mục dump `700`, file `600`.
- **"DB trống" = không có bảng nào trong schema `public`** (`SELECT count(*) FROM pg_tables WHERE schemaname='public'`), không phải "có bảng nhưng 0 dòng".
- **`--restore` phải dùng `psql --single-transaction -v ON_ERROR_STOP=1`.** Hai cờ mua hai thứ khác nhau: `--single-transaction` cho tính nguyên tử (lỗi giữa chừng thì Postgres tự biến COMMIT cuối thành ROLLBACK, không bao giờ có DB nạp nửa vời); `ON_ERROR_STOP=1` cho exit code trung thực (thiếu nó, psql thoát 0 sau một lần nạp đã hỏng và đã rollback — báo "khôi phục xong" trên một DB rỗng). Đo thật: cùng một dump lỗi, không `ON_ERROR_STOP` → exit 0 / 0 bảng; có `ON_ERROR_STOP` → exit khác 0 / 0 bảng; bỏ luôn `--single-transaction` → bảng sống sót thật.
- **Không bao giờ xoá bản dump cuối cùng còn lại**, kể cả khi `PG_DUMP_KEEP=1`.
- **Đọc cấu hình theo khuôn `pod-volume.sh:188`**: ưu tiên biến môi trường, thiếu thì `get_kv` từ `.env`.
- **Kết nối qua TCP** bằng `POSTGRES_USER/PASSWORD/DB/PORT`, không dùng `sudo -u postgres`. Nhờ vậy script chạy y hệt nhau trên pod và trong test ở local.

---

### Task 1: Bộ khung test + `--dump`

**Files:**
- Create: `motions-studio/setup/pod-pgdump.sh`
- Create: `motions-studio/setup/tests/pgdump-test.sh`

**Interfaces:**
- Consumes: không có (task đầu).
- Produces:
  - `motions-studio/setup/pod-pgdump.sh --dump` → exit `0` khi tạo được `$POD_VOLUME/pg/dumps/motion-<STAMP>.sql.gz` + `.meta` + symlink `latest`; exit `1` kèm lý do khi hỏng.
  - `_row_counts()` → in ra các dòng `<tên bảng>=<số dòng>` đã `sort`.
  - Định dạng `.meta`: các dòng `key=value`, gồm `created` (giây epoch UTC), `pg_version`, `dump_bytes`, rồi các dòng `<bảng>=<số dòng>`.
  - `motions-studio/setup/tests/pgdump-test.sh` → chạy toàn bộ test, exit `0` khi tất cả xanh.

- [ ] **Step 1: Viết bộ khung test + test đầu tiên (sẽ đỏ)**

Tạo `motions-studio/setup/tests/pgdump-test.sh`:

```bash
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

echo
info "$PASSED xanh · $FAILED đỏ"
[ "$FAILED" -eq 0 ]
```

- [ ] **Step 2: Chạy để chắc chắn nó ĐỎ**

Run: `bash motions-studio/setup/tests/pgdump-test.sh`
Expected: FAIL — `--dump trả 0` đỏ vì `pod-pgdump.sh` chưa tồn tại.

- [ ] **Step 3: Viết `pod-pgdump.sh` với đúng `--dump`**

Tạo `motions-studio/setup/pod-pgdump.sh`:

```bash
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
```

- [ ] **Step 4: Chạy lại để chắc chắn nó XANH**

Run: `chmod +x motions-studio/setup/pod-pgdump.sh && bash motions-studio/setup/tests/pgdump-test.sh`
Expected: PASS — 7 dòng xanh, 0 đỏ.

- [ ] **Step 5: Commit**

```bash
git add motions-studio/setup/pod-pgdump.sh motions-studio/setup/tests/pgdump-test.sh
git commit -m "pod-pgdump.sh --dump + bộ test chạy được ở local bằng docker

Đếm số dòng CHÍNH XÁC chứ không đọc n_live_tup: đó là số ước lượng do autovacuum
cập nhật, mà .meta tồn tại đúng để bắt lệch. Ghi ra file tạm rồi mv (nguyên tử)
nên latest không bao giờ trỏ một file ghi dở."
```

---

### Task 2: Prune (`PG_DUMP_KEEP`)

**Files:**
- Modify: `motions-studio/setup/pod-pgdump.sh` (thêm `_prune()`, gọi cuối `do_dump()`)
- Modify: `motions-studio/setup/tests/pgdump-test.sh` (thêm khối test Task 2)
- Modify: `.env.example`

**Interfaces:**
- Consumes: `do_dump()` và bố cục `$DUMPS/motion-<STAMP>.sql.gz` từ Task 1.
- Produces: `_prune()` — giữ `$KEEP` bản mới nhất, xoá cả `.sql.gz` lẫn `.meta` của bản bị loại, **không bao giờ xoá bản cuối cùng còn lại**.

- [ ] **Step 1: Viết test (sẽ đỏ)**

Chèn vào `pgdump-test.sh`, ngay trước dòng `info "$PASSED xanh · $FAILED đỏ"`:

```bash
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
# KEEP=0 xoá sạch nếu không có guard
rm -rf "$VOL/pg"; seed
PG_DUMP_KEEP=0 bash "$SCRIPT" --dump >/dev/null; sleep 1
PG_DUMP_KEEP=0 bash "$SCRIPT" --dump >/dev/null
N0="$(ls "$VOL"/pg/dumps/*.sql.gz 2>/dev/null | wc -l | tr -d ' ')"
[ "$N0" -ge 1 ] && ok "KEEP=0 vẫn giữ lại ít nhất 1 bản (không xoá sạch)" || bad "KEEP=0 đã xoá sạch"
[ -e "$(readlink "$VOL/pg/latest")" ] && ok "KEEP=0: latest vẫn trỏ file có thật" || bad "KEEP=0: latest treo"
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
[ "$NA" -ge 1 ] && ok "KEEP=abc vẫn giữ lại ít nhất 1 bản" || bad "KEEP=abc xoá sạch"
# KEEP âm: nguy hiểm nhất, không sập nhưng xoá sạch
rm -rf "$VOL/pg"; seed
PG_DUMP_KEEP=-2 bash "$SCRIPT" --dump >/dev/null 2>&1; sleep 1
PG_DUMP_KEEP=-2 bash "$SCRIPT" --dump >/dev/null 2>&1
NN="$(ls "$VOL"/pg/dumps/*.sql.gz 2>/dev/null | wc -l | tr -d ' ')"
[ "$NN" -ge 1 ] && ok "KEEP âm vẫn giữ lại ít nhất 1 bản" || bad "KEEP=-2 đã xoá sạch"
[ -e "$(readlink "$VOL/pg/latest")" ] && ok "KEEP âm: latest vẫn trỏ file có thật" || bad "KEEP âm: latest treo"
```

- [ ] **Step 2: Chạy để chắc chắn nó ĐỎ**

Run: `bash motions-studio/setup/tests/pgdump-test.sh`
Expected: FAIL — `PG_DUMP_KEEP=2 giữ đúng 2 bản` nhận `3`.

- [ ] **Step 3: Viết `_prune()`**

Thêm vào `pod-pgdump.sh` ngay trước `do_dump()`:

```bash
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
```

Và thêm dòng cuối trong `do_dump()`, ngay sau `ln -sfn`:

```bash
  _prune "$KEEP"
```

- [ ] **Step 4: Chạy lại để chắc chắn nó XANH**

Run: `bash motions-studio/setup/tests/pgdump-test.sh`
Expected: PASS — 20 xanh, 0 đỏ (10 Task 1 + 10 Task 2 tests).

- [ ] **Step 5: Ghi `PG_DUMP_KEEP` vào `.env.example`**

Chèn ngay dưới dòng `POD_VOLUME_ID=` trong `.env.example`:

```bash
# Số bản dump database giữ lại trên volume (setup/pod-pgdump.sh). Dump là metadata thuần,
# cỡ vài MB, nên giữ nhiều gần như miễn phí. Đặt 1 cũng KHÔNG xoá sạch: script không bao
# giờ xoá bản cuối cùng còn lại.
PG_DUMP_KEEP=20
```

- [ ] **Step 6: Commit**

```bash
git add motions-studio/setup/pod-pgdump.sh motions-studio/setup/tests/pgdump-test.sh .env.example
git commit -m "prune bản dump cũ, giữ PG_DUMP_KEEP bản

sort theo TÊN chứ không ls -t: tên đã mang timestamp nên thứ tự chữ cái trùng thứ tự
thời gian, còn ls -t đọc mtime của MooseFS và parse output ls thì vỡ khi có tên lạ.
Không bao giờ xoá bản cuối cùng còn lại, kể cả KEEP=1."
```

---

### Task 3: `--restore`

**Files:**
- Modify: `motions-studio/setup/pod-pgdump.sh` (thêm `do_restore()`)
- Modify: `motions-studio/setup/tests/pgdump-test.sh`

**Interfaces:**
- Consumes: `$LATEST`, định dạng `.meta` (khoá `created`) từ Task 1.
- Produces: `pod-pgdump.sh --restore` → exit `0` khi nạp xong **hoặc** khi cố ý bỏ qua (DB đã có bảng / chưa có dump); exit `1` khi file dump hỏng hoặc nạp lỗi. Luôn in tuổi bản dump khi nạp.

- [ ] **Step 1: Viết test (sẽ đỏ)**

Chèn vào `pgdump-test.sh` trước dòng tổng kết:

```bash
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
bash "$SCRIPT" --restore 2>&1 | grep -qi "tuổi" && ok "restore in tuổi bản dump" \
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
D="$(readlink "$VOL/pg/latest")"
{ gzip -dc "$D"; echo "SELECT * FROM khong_ton_tai_bang_nay;"; } | gzip -c > "$D.part"
mv "$D.part" "$D"
assert_fail "--restore đỏ khi dump có statement lỗi" -- bash "$SCRIPT" --restore
assert_eq "0" "$(q "SELECT count(*) FROM pg_tables WHERE schemaname='public'")" \
  "dump lỗi KHÔNG để lại bảng nào — chứng minh --single-transaction rollback thật"
```

- [ ] **Step 2: Chạy để chắc chắn nó ĐỎ**

Run: `bash motions-studio/setup/tests/pgdump-test.sh`
Expected: FAIL — `--restore trả 0 khi DB trống` đỏ vì `--restore` chưa có (script thoát 2).

- [ ] **Step 3: Viết `do_restore()`**

Thêm vào `pod-pgdump.sh` sau `do_dump()`, và thêm nhánh `--restore)  do_restore ;;` vào `case`:

```bash
do_restore() {
  local tables meta created age_h target
  if [ ! -L "$LATEST" ] && [ ! -f "$LATEST" ]; then
    log "chưa có bản dump nào trên volume — bỏ qua (đây là phiên đầu)."
    return 0
  fi
  target="$(readlink "$LATEST" 2>/dev/null || printf '%s' "$LATEST")"
  [ -f "$target" ] || { warn "latest trỏ file không tồn tại: $target"; return 1; }

  tables="$(_table_count)"
  [ -n "$tables" ] || { warn "không hỏi được danh sách bảng — DB có chạy không?"; return 1; }
  if [ "$tables" != "0" ]; then
    log "DB đã có $tables bảng trong schema public — KHÔNG nạp đè. Bỏ qua."
    return 0
  fi

  gzip -t "$target" 2>/dev/null || { warn "file dump hỏng (gzip -t đỏ): $target"; return 1; }

  meta="${target%.sql.gz}.meta"
  created="$(grep -m1 '^created=' "$meta" 2>/dev/null | cut -d= -f2)"
  if [ -n "$created" ]; then
    age_h=$(( ( $(date -u +%s) - created ) / 3600 ))
    log "tuổi bản dump: ${age_h} giờ ($(basename "$target"))"
    [ "$age_h" -gt 168 ] && warn "bản dump này CŨ HƠN 7 NGÀY — dữ liệu sau mốc đó không có trong này."
  else
    warn "tuổi bản dump: không đọc được (thiếu .meta) — $(basename "$target")"
  fi

  # --single-transaction VÀ ON_ERROR_STOP=1 phải đi CÙNG NHAU. Thiếu ON_ERROR_STOP thì psql
  # chạy tiếp qua statement lỗi rồi COMMIT — cho ra một DB nạp dở mà app vẫn chạy lên được,
  # và không ai biết đang thiếu gì. Có cả hai thì lỗi bất kỳ đâu cũng rollback về DB trống.
  if ! gzip -dc "$target" | _psql -d "$PG_DB" --single-transaction -v ON_ERROR_STOP=1 -q >/dev/null; then
    warn "nạp dump thất bại — đã rollback, DB vẫn trống như trước."
    return 1
  fi
  ok "khôi phục xong từ $(basename "$target")"
}
```

- [ ] **Step 4: Chạy lại để chắc chắn nó XANH**

Run: `bash motions-studio/setup/tests/pgdump-test.sh`
Expected: PASS — 20 xanh, 0 đỏ.

- [ ] **Step 5: Commit**

```bash
git add motions-studio/setup/pod-pgdump.sh motions-studio/setup/tests/pgdump-test.sh
git commit -m "--restore: chỉ nạp khi DB trống, rollback sạch khi dump hỏng

--single-transaction VÀ ON_ERROR_STOP=1 phải đi cùng nhau. Thiếu cái sau thì psql
chạy tiếp qua statement lỗi rồi COMMIT một DB nạp dở — app vẫn lên, không ai biết
thiếu gì. Test chứng minh: dump bị cắt cụt để lại ĐÚNG 0 bảng.

'DB trống' = không có bảng nào trong schema public, không phải 'có bảng nhưng 0
dòng': migrate.js chạy trước sẽ tạo đủ bảng rỗng, lúc đó bỏ qua mới là đúng."
```

---

### Task 4: `--check` và `--verify`

**Files:**
- Modify: `motions-studio/setup/pod-pgdump.sh` (thêm `do_check()`, `do_verify()`)
- Modify: `motions-studio/setup/tests/pgdump-test.sh`

**Interfaces:**
- Consumes: `_row_counts()`, `$LATEST`, `.meta` từ Task 1.
- Produces:
  - `--check` → exit `0` khi có bản dump đọc được, `1` khi không. Không sửa gì.
  - `--verify` → exit `0` khi nạp thử vào DB tạm và số dòng khớp `.meta`, `1` khi lệch. DB tạm tên `${PG_DB}_verify`, luôn bị xoá kể cả khi hỏng.
- **Yêu cầu quyền:** role `$PG_USER` phải có `CREATEDB` (Task 5 cấp trên pod; container test đã có sẵn vì `motion` là superuser của image).

- [ ] **Step 1: Viết test (sẽ đỏ)**

Chèn vào `pgdump-test.sh` trước dòng tổng kết:

```bash
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
```

- [ ] **Step 2: Chạy để chắc chắn nó ĐỎ**

Run: `bash motions-studio/setup/tests/pgdump-test.sh`
Expected: FAIL — `--check trả 0 khi có dump` đỏ (script thoát 2, chưa có chế độ này).

- [ ] **Step 3: Viết `do_check()` và `do_verify()`**

Thêm vào `pod-pgdump.sh`, và thêm hai nhánh `--check)  do_check ;;` / `--verify) do_verify ;;` vào `case`:

```bash
do_check() {
  local target meta created age_h
  if [ ! -L "$LATEST" ] && [ ! -f "$LATEST" ]; then
    warn "chưa có bản dump nào trên volume ($PGDIR)."
    return 1
  fi
  target="$(readlink "$LATEST" 2>/dev/null || printf '%s' "$LATEST")"
  [ -f "$target" ] || { warn "latest trỏ file không tồn tại: $target"; return 1; }
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
  target="$(readlink "$LATEST" 2>/dev/null || printf '%s' "$LATEST")"
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
```

- [ ] **Step 4: Chạy lại để chắc chắn nó XANH**

Run: `bash motions-studio/setup/tests/pgdump-test.sh`
Expected: PASS — 27 xanh, 0 đỏ.

- [ ] **Step 5: Commit**

```bash
git add motions-studio/setup/pod-pgdump.sh motions-studio/setup/tests/pgdump-test.sh
git commit -m "--check báo cáo, --verify diễn tập nạp lại vào DB tạm

--verify là bằng chứng, phần còn lại chỉ là dấu vết: nó nạp thật rồi so số dòng với
.meta. Test chứng minh nó ĐỎ khi .meta bị sửa lệch — nếu không nó chỉ là một lệnh
luôn xanh. DB tạm bị xoá kể cả khi hỏng, kẻo lần verify sau đỏ vì lý do khác hẳn."
```

---

### Task 5: Nối vào setup — `POD_VOLUME`, `CREATEDB`, gọi `--restore`

**Files:**
- Modify: `motions-studio/setup/pod-volume.sh:324-326` (thêm một dòng `set_kv_local`)
- Modify: `motions-studio/setup/lib-feature.sh` (hàm `phase_postgres`, và `feature_main()` dòng 868-894)
- Modify: `scripts/pod-bootstrap.sh` (lệnh ssh thứ hai, chạy `./$SETUP_SCRIPT` — thêm `POD_VOLUME`
  vào danh sách env truyền theo, xem "Fix round 2" bên dưới. Bổ sung sau khi review độc lập phát
  hiện: trên pod MỚI, `.env` chưa tồn tại lúc `pod-volume.sh` chạy nên khối ghi `.env` của nó bị
  bỏ qua, và lệnh ssh chạy setup không mang `POD_VOLUME` theo — `phase_pg_restore` không có cách
  nào thấy được giá trị này ở lần dựng pod đầu tiên nếu không sửa ở đây.)

**Interfaces:**
- Consumes: `pod-pgdump.sh --restore` từ Task 3.
- Produces: sau `make gpu-bootstrap` trên pod có volume, DB được khôi phục tự động nếu trống và có dump. Key `POD_VOLUME` có mặt trong `.env` trên pod.

- [ ] **Step 1: Ghi `POD_VOLUME` vào `.env` trên pod**

`POD_VOLUME` hiện **không** tới được `lib-feature.sh`: nó không nằm trong danh sách env truyền qua ssh (`scripts/pod-bootstrap.sh:160-167`) và cũng không được ghi vào `.env`. Thêm vào `pod-volume.sh`, ngay cạnh ba dòng `set_kv_local` đang có (dòng 324-326):

```bash
  # pod-pgdump.sh (chạy từ feature_main và từ Makefile) đọc key này. Không truyền qua ssh
  # được vì pod-bootstrap.sh:160-167 không mang nó theo, và ghi vào .env thì mọi caller —
  # setup, Makefile, chạy tay — đều thấy cùng một giá trị.
  set_kv_local POD_VOLUME "$VOL"
```

- [ ] **Step 2: Cấp `CREATEDB` cho role app**

`--verify` cần tạo một database tạm. Trong `lib-feature.sh`, ngay sau dòng `pg psql -qc "ALTER ROLE ${PG_USER} WITH PASSWORD '${PG_PASS}'"` trong `phase_postgres()`:

```bash
  # CREATEDB để setup/pod-pgdump.sh --verify dựng được DB tạm mà diễn tập nạp lại. Role này
  # vốn đã sở hữu toàn bộ database của app trên một pod dùng riêng, nên quyền tạo thêm một DB
  # không mở ra gì mới — đổi lại ta có đường chứng minh backup nạp được, thay vì chỉ tin.
  pg psql -qc "ALTER ROLE ${PG_USER} CREATEDB" >/dev/null
```

- [ ] **Step 3: Gọi `--restore` đúng chỗ trong `feature_main()`**

Trong `feature_main()` (`lib-feature.sh:868-894`), nhánh `MTC_PREBUILT=1` và nhánh thường đều gọi `phase_postgres`. Thêm hàm mới và chèn lời gọi sau `phase_postgres` ở **cả hai** nhánh:

```bash
# Khôi phục DB từ volume — PHẢI nằm giữa phase_postgres và bất cứ thứ gì tạo schema.
#   sau phase_postgres: dump chứa ALTER TABLE ... OWNER TO và GRANT nên role + database
#     phải tồn tại trước.
#   trước phase_pm2: api khởi động sẽ chạy api/src/migrate.js tạo bảng từ db/init/*.sql;
#     nạp dump có CREATE TABLE vào DB đã có bảng rỗng là lỗi trùng.
# Không có volume thì đi qua như không có gì.
phase_pg_restore() {
  # POD_VOLUME đọc theo khuôn env-trước-.env-sau (giống pod-volume.sh:188 và pod-pgdump.sh):
  # scripts/pod-bootstrap.sh có HAI lệnh ssh, và chỉ lệnh đầu (chạy pod-volume.sh) mang
  # POD_VOLUME theo. Lệnh thứ hai — lệnh chạy setup và dẫn tới đây — thì không. Chỉ dựa vào
  # biến môi trường là cổng dưới đây luôn đóng, phase này lặng lẽ không chạy, và người dùng
  # mất DB mà vẫn thấy mọi thứ xanh. pod-volume.sh đã ghi POD_VOLUME vào .env chính vì vậy.
  local _vol="${POD_VOLUME:-}"
  [ -n "$_vol" ] || _vol="$(get_kv POD_VOLUME)"
  [ -n "$_vol" ] || return 0

  say "4b/11 · Khôi phục database từ Network Volume (nếu có bản dump)"
  POD_VOLUME="$_vol" bash "$ROOT/setup/pod-pgdump.sh" --restore \
    || warn "khôi phục DB không thành công — setup vẫn chạy tiếp với DB trống."
}
```

**Fix round 1 (nghiệm thu phát hiện, sửa ngay trong Task 5):** bản đầu cổng bằng
`[ -n "${POD_VOLUME:-}" ]` — chỉ đọc biến môi trường. `scripts/pod-bootstrap.sh` có HAI lệnh
ssh: lệnh đầu (chạy `pod-volume.sh`) truyền `POD_VOLUME=...`, lệnh thứ hai (chạy
`./$SETUP_SCRIPT` → `feature_main` → `phase_pg_restore`) thì KHÔNG — và `lib-feature.sh`
không có chỗ nào `source .env` vào shell. Kết quả: trên đường `make gpu-bootstrap` chuẩn,
`${POD_VOLUME:-}` luôn rỗng ở đây dù `.env` trên pod đã có key đó, cổng luôn đóng,
`phase_pg_restore` lặng lẽ no-op — tính năng không bao giờ chạy, không ai biết. Sửa bằng
khuôn env-trước-`.env`-sau đã dùng ở `pod-volume.sh:188` và `pod-pgdump.sh`: đọc biến môi
trường trước, rỗng thì fallback qua `get_kv POD_VOLUME` (hàm đã có sẵn trong
`lib-feature.sh`, đọc đúng `.env` mà `pod-volume.sh` ghi — cả hai cùng tính `ROOT` bằng
`cd "$(dirname "$0")/.."; ROOT="$(pwd)"` nên là cùng một file).

Rồi trong `feature_main()`:

```bash
  if [ "${MTC_PREBUILT:-0}" = "1" ]; then
    phase_dotenv
    phase_postgres
    phase_pg_restore
    phase_prebuilt_deps
    phase_ollama
  else
    phase_apt
    phase_dotenv
    phase_postgres
    phase_pg_restore
    phase_app_deps
    phase_ollama
    phase_comfyui
  fi
```

**Fix round 2 (review độc lập phát hiện, Critical, sửa ngay trong Task 5):** fix round 1 vẫn
không đủ trên một pod MỚI. `pod-volume.sh:309` cổng cả khối ghi `.env` (kể cả dòng
`set_kv_local POD_VOLUME` của Step 1) bằng `[ -f "$ROOT/.env" ]`. Trên pod MỚI, `rsync` ở
`pod-bootstrap.sh:99-100` loại trừ `.env` VÀ `.env.*` (khớp cả `.env.example`), nên khi lệnh
ssh đầu chạy `pod-volume.sh`, `$ROOT/.env` CHƯA TỒN TẠI → cổng `:309` sai → toàn bộ khối ghi
`.env` bị bỏ qua, `POD_VOLUME` không được ghi. `.env` chỉ hình thành dần sau đó, ở lệnh ssh
THỨ HAI, qua `phase_dotenv` (`cp .env.example .env` — không có nguồn vì `.env.example` cũng
bị rsync loại trừ — rồi các lệnh `set_kv` dựng dần từng key). Đến lúc `phase_pg_restore` gọi
`get_kv POD_VOLUME`, nó đọc rỗng và `return 0` — đúng kịch bản trung tâm của cả tính năng
(pod mới + volume cũ có dump) lại là kịch bản im lặng bỏ khôi phục.

KHÔNG sửa bằng cách bỏ guard `[ -f "$ROOT/.env" ]` ở `pod-volume.sh:309` — làm vậy thì
`pod-volume.sh` tự tạo một `.env` chỉ có vài key, rồi `phase_dotenv` thấy `[ ! -f .env ]` sai
nên bỏ qua bước dựng `.env` đầy đủ, hỏng nặng hơn.

Sửa đúng chỗ: `scripts/pod-bootstrap.sh`, lệnh ssh thứ hai (chạy `./$SETUP_SCRIPT`) — thêm
`POD_VOLUME` vào danh sách env truyền theo, giống cách lệnh ssh thứ nhất đã làm. Giá trị này
đã có sẵn trong biến shell `POD_VOLUME` của `pod-bootstrap.sh` (đọc từ `.env` CỦA MÁY DEV ở
dòng 55, không đổi giữa đó và lệnh ssh thứ hai — không hàm/vòng lặp nào ghi đè), nên đường
env-trước hoạt động ngay từ lần bootstrap ĐẦU TIÊN, không phụ thuộc `.env` trên pod đã tồn tại
hay chưa:

```bash
# POD_VOLUME phải có mặt ở CẢ lệnh ssh này, không chỉ lệnh chạy pod-volume.sh ở trên.
# feature_main() → phase_pg_restore đọc nó để quyết có khôi phục DB từ volume hay không, và
# trên một pod MỚI thì .env chưa tồn tại lúc pod-volume.sh chạy (rsync loại trừ .env và .env.*,
# nên cả .env.example cũng không có) → khối set_kv_local của pod-volume.sh bị bỏ qua, .env
# không có POD_VOLUME, và phase_pg_restore lặng lẽ không chạy đúng vào lần dựng pod đầu tiên —
# tức đúng kịch bản mà tính năng này tồn tại để phục vụ.
ssh "${SSH_OPTS[@]}" "root@$HOST" "cd ~/$REMOTE_DIR && chmod +x setup/*.sh && \
DOMAIN='$DOMAIN' SUPER_ADMIN='$SUPER_ADMIN' GMAIL_USER='$GMAIL_USER' \
GMAIL_APP_PASSWORD='$GMAIL_APP_PASSWORD' CF_API_TOKEN='$CF_API_TOKEN' \
CF_TUNNEL_TOKEN='$CF_TUNNEL_TOKEN' CORS_ORIGINS='$CORS_ORIGINS' HF_TOKEN='' \
${FE_DOMAIN:+CF_FE_DOMAIN='$FE_DOMAIN' CF_FE_PORT='$FE_PORT' FRONTEND_URL='https://$FE_DOMAIN'} \
MTC_PREBUILT='${MTC_PREBUILT:-0}' \
${JOB_TYPES_OVERRIDE:+JOB_TYPES_OVERRIDE='$JOB_TYPES_OVERRIDE'} \
${POD_VOLUME:+POD_VOLUME='$POD_VOLUME'} \
./$SETUP_SCRIPT" < /dev/null | tee "$LOG"
```

**Lưu ý khi cấy đoạn này:** cả khối `ssh "..." "..."` là MỘT chuỗi double-quote trải nhiều
dòng, nối bằng `\` cuối dòng — đây là cú pháp `VAR=val ... lệnh` (prefix biến môi trường cho
MỘT lệnh), nên toàn bộ phải giữ nguyên trên một "dòng logic". Đặt đoạn comment giải thích NGAY
TRƯỚC dòng `ssh ...` (ở ngoài chuỗi, cùng chỗ với khối comment "stdin redirected..." đã có sẵn
phía trên) — TUYỆT ĐỐI không chèn comment (`#...`) vào GIỮA các dòng bên trong chuỗi: một dòng
`# ...` không có `\` cuối dòng sẽ giữ nguyên newline thật bên trong chuỗi, cắt lệnh thành
nhiều statement riêng ở phía remote và làm toàn bộ `DOMAIN/SUPER_ADMIN/GMAIL_USER/...` KHÔNG
còn được truyền cho `./$SETUP_SCRIPT` nữa — một hồi quy còn nặng hơn lỗi đang sửa. Đã kiểm
bằng cách mô phỏng chuỗi này trong một script `bash` riêng trước khi áp dụng.

Giữ nguyên fallback `get_kv` trong `phase_pg_restore` (fix round 1) — vẫn có giá trị cho lần
bootstrap thứ hai trở đi (khi `.env` trên pod đã tồn tại) và khi chạy setup bằng tay.

- [ ] **Step 4: Kiểm cú pháp cả bốn file**

Run: `bash -n motions-studio/setup/pod-volume.sh motions-studio/setup/lib-feature.sh motions-studio/setup/pod-pgdump.sh scripts/pod-bootstrap.sh && echo "cú pháp OK"`
Expected: in ra `cú pháp OK`, không có thông báo lỗi.

- [ ] **Step 5: Kiểm `phase_pg_restore` được gọi ở CẢ HAI nhánh**

Run: `grep -c "phase_pg_restore" motions-studio/setup/lib-feature.sh`
Expected: `3` (một định nghĩa hàm + hai lời gọi). Nếu ra `2` thì đã quên một nhánh — đúng loại lỗi làm đường prebuilt hoặc đường thường im lặng bỏ khôi phục.

- [ ] **Step 5b: Kiểm `POD_VOLUME` có mặt ở lệnh ssh chạy setup**

Run: `grep -c "POD_VOLUME=" scripts/pod-bootstrap.sh`
Expected: `5` — dòng đọc từ `.env` máy dev (1), lệnh ssh chạy `pod-volume.sh` (1), một dòng
ví dụ trong thông báo `warn` cho `--adopt` (1, không phải lệnh ssh thật), lệnh ssh chạy
`./$SETUP_SCRIPT` (1, MỚI thêm ở fix round 2), lệnh ssh chạy `pod-volume.sh --check` (1, đã
có từ trước). Nếu KHÔNG thấy dòng mới ở lệnh ssh chạy `./$SETUP_SCRIPT` thì fix round 2 chưa
vào đúng chỗ.

- [ ] **Step 6: Commit**

```bash
git add motions-studio/setup/pod-volume.sh motions-studio/setup/lib-feature.sh
git commit -m "Nối khôi phục DB vào feature_main, và ghi POD_VOLUME vào .env trên pod

POD_VOLUME trước giờ KHÔNG tới được lib-feature.sh: không nằm trong env truyền qua
ssh (pod-bootstrap.sh:160-167) và không được ghi vào .env. pod-volume.sh đã ghi
COMFY_DIR/OLLAMA_MODELS theo đúng cách này, chỉ thiếu mỗi key đó.

phase_pg_restore chèn giữa phase_postgres và phase_pm2, và chèn ở CẢ HAI nhánh
prebuilt/thường — bỏ sót một nhánh là một đường im lặng không khôi phục."
```

---

### Task 6: Makefile — hai target mới, sửa `gpu-down` và `gpu-destroy`

> **Đã sửa thêm sau review toàn nhánh** (xem mục "Đợt sửa sau review TOÀN NHÁNH" ngay trước Task 8):
> cả bốn lệnh ssh giờ truyền `POD_VOLUME` (C1) và ba lệnh dump truyền `PG_DUMP_KEEP` (I4);
> `gpu-volume*` chuyển sang `bash ./setup/…` (M9); comment `$0,99` → `$$0,99` (M10).

**Files:**
- Modify: `Makefile` (dòng 2 `.PHONY`, dòng 64-70 `gpu-down`, dòng 76-101 `gpu-destroy`, thêm hai target)

**Interfaces:**
- Consumes: `pod-pgdump.sh --dump` (Task 1), `--check` (Task 4).
- Produces: `make gpu-db-dump`, `make gpu-db-check`; `gpu-down` dump trước khi dừng; `gpu-destroy` cố dump nếu pod còn ssh được.

- [ ] **Step 1: Thêm hai target mới**

Chèn vào `Makefile` ngay trước target `gpu-smoke`:

```makefile
gpu-db-dump: ## Sao lưu database sang Network Volume (pod phải đang chạy)
	@ssh -o StrictHostKeyChecking=accept-new -p $(call env,GPU_SSH_PORT) root@$(call env,GPU_SSH_HOST) \
		"cd ~/motion-backend && bash ./setup/pod-pgdump.sh --dump"

gpu-db-check: ## Bản dump mới nhất bao lâu rồi, có nạp lại được không (chạy --check + --verify)
	@ssh -o StrictHostKeyChecking=accept-new -p $(call env,GPU_SSH_PORT) root@$(call env,GPU_SSH_HOST) \
		"cd ~/motion-backend && bash ./setup/pod-pgdump.sh --check && bash ./setup/pod-pgdump.sh --verify"
```

Chạy qua `bash ./setup/pod-pgdump.sh` (không phải `./setup/pod-pgdump.sh` trần) để KHÔNG phụ thuộc
bit thực thi: `chmod +x setup/*.sh` chỉ chạy trong lệnh ssh của `pod-bootstrap.sh`, còn bốn target
Makefile này ssh riêng, không đi qua bootstrap. Có `bash` ở đầu thì file thiếu +x vẫn chạy được.

Và thêm `gpu-db-dump gpu-db-check` vào dòng `.PHONY` (dòng 2).

- [ ] **Step 2: Dump trước khi dừng pod trong `gpu-down`**

Thay phần đầu target `gpu-down` (dòng 64-70) thành:

```makefile
gpu-down: ## Stop the pod (DO NOT FORGET — an idle pod bills by the hour)
	@# Điểm dump CHÍNH: đây là lúc cuối cùng còn ssh được vào pod. Sau khi dừng, pod im lặng
	@# cho tới khi bật lại, mà volume thì chỉ mount được qua pod — nên không còn đường nào
	@# sao lưu hay kiểm tra nữa.
	@# `|| echo` là CỐ Ý: dump hỏng KHÔNG được chặn việc dừng một pod $0,99/giờ, và gpu-down
	@# vốn không làm mất DB (container disk còn nguyên). Chặn ở đây là đốt tiền thật để giữ
	@# thứ chưa bị đe doạ.
	@ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 \
		-p $(call env,GPU_SSH_PORT) root@$(call env,GPU_SSH_HOST) \
		"cd ~/motion-backend && bash ./setup/pod-pgdump.sh --dump" \
		|| echo "!! sao lưu DB thất bại — vẫn dừng pod. DB còn trên container disk, chỉ mất nếu gpu-destroy."
```

(giữ nguyên phần `ifeq`/`runpodctl pod stop`/`echo` phía dưới)

- [ ] **Step 3: Cố dump trong `gpu-destroy` khi pod còn sống**

Chèn vào đầu target `gpu-destroy`, ngay sau dòng `@test -n "$(call env,GPU_INSTANCE_ID)" || ...`:

```makefile
	@# Cố sao lưu lần cuối. KHÔNG nuốt stderr: pod-pgdump.sh báo lỗi nghiêm trọng qua die() ra
	@# stderr, và đây là ngay trước một thao tác không hoàn tác được. Nếu pod đã dừng thì ssh tự
	@# in lỗi kết nối — ồn hơn một chút, nhưng đó là tiếng ồn TRUNG THỰC. Nuốt hết rồi đoán
	@# "pod đã dừng?" là khẳng định một nguyên nhân ta không biết, ngay lúc người dùng cần biết nhất.
	@ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 \
		-p $(call env,GPU_SSH_PORT) root@$(call env,GPU_SSH_HOST) \
		"cd ~/motion-backend && bash ./setup/pod-pgdump.sh --dump" \
		|| echo "!! sao lưu lần cuối KHÔNG thành công (lý do ở ngay trên) — vẫn XOÁ pod theo yêu cầu."
```

(Fix round 1: bỏ `2>/dev/null` — nó nuốt cả thông báo lỗi thật từ `die()` trong pod-pgdump.sh khi
pod còn sống nhưng dump hỏng vì lý do khác (volume đầy, Postgres chưa lên...), rồi câu fallback cũ
"pod đã dừng?" khẳng định sai nguyên nhân ngay trước một thao tác không hoàn tác được.)

- [ ] **Step 4: Kiểm Makefile parse được và target hiện trong help**

Run: `make -n gpu-db-dump >/dev/null && make help | grep -E "gpu-db-(dump|check)"`
Expected: hai dòng mô tả của `gpu-db-dump` và `gpu-db-check`.

- [ ] **Step 5: Commit**

```bash
git add Makefile
git commit -m "Makefile: gpu-db-dump/gpu-db-check, dump ở gpu-down và gpu-destroy

gpu-down là điểm dump CHÍNH vì đó là lúc cuối còn ssh được: sau khi dừng, volume
chỉ mount qua pod nên không còn đường nào sao lưu.

|| echo ở cả hai chỗ là cố ý, không phải cẩu thả: dump hỏng không được chặn việc
dừng một pod \$0,99/giờ, mà gpu-down vốn chẳng làm mất DB."
```

---

### Task 7: Lớp kiểm mới trong `pod-smoke.sh`

> **Đã sửa thêm sau review toàn nhánh** (xem mục ngay trước Task 8): lớp 6 dùng `bad` thay `warn`
> khi `POD_VOLUME` đã đặt (I3), truyền `POD_VOLUME` qua ssh (C1), và gọi `ssh` trực tiếp thay vì
> `remote()` để không nuốt stderr của `die()` (M11). Đoạn code mẫu bên dưới là bản TRƯỚC các sửa đó.

**Files:**
- Modify: `scripts/pod-smoke.sh`

**Interfaces:**
- Consumes: `pod-pgdump.sh --check` và `--verify` (Task 4).
- Produces: `make gpu-smoke` có thêm một lớp báo tình trạng backup.

- [ ] **Step 1: Xác định chỗ chèn**

Run: `grep -n 'log "' scripts/pod-smoke.sh | head -12`
Expected: thấy các lớp in tiêu đề bằng `log "…"`. Chèn lớp mới **trước lớp motion** (lớp 6, lớp đầu tiên cần GPU), để nó nằm trong nhóm rẻ chạy trước.

Helper có sẵn ở `pod-smoke.sh:29-36,55`, dùng đúng chúng, không định nghĩa mới:
`log` (tiêu đề lớp) · `ok` · `skip` (bỏ qua có chủ ý) · `warn` · `bad` (tăng `FAILED`) · `remote "<lệnh>"` (chạy qua ssh) · biến `$POD_VOLUME` **đã** được đọc sẵn ở dòng 40.

- [ ] **Step 2: Thêm lớp backup**

Bản triển khai thật (sửa hai điểm so với mẫu ở trên, xem "Fix round 1" dưới Task 7 report):
1. Thêm nhánh `[ "$SSH_OK" != 1 ]` **trước** nhánh `POD_VOLUME`, cùng thứ tự với lớp 5 (Network
   Volume). Mẫu gốc thiếu nhánh này — nếu không có SSH, `remote()` vẫn chạy (với `ssh` tới
   host/port rỗng), lỗi ra là lỗi ssh chung, và layer sẽ báo "chưa có bản dump" — SAI, lý do
   thật là "không có SSH", không phải "chưa có backup".
2. Gọi `bash ./setup/pod-pgdump.sh` (không phải `./setup/pod-pgdump.sh`), đồng bộ với quyết định
   ở Task 6: không dựa vào bit thực thi trên pod.

Vì chèn trước lớp motion (giờ đã đổi số vì có thêm 1 lớp: 6/9, không phải 6/8), toàn bộ số
`N/8` của các lớp 1-5 và số của motion/tryon/create-image (6→7, 7→8, 8→9) cũng được cập nhật
trong `scripts/pod-smoke.sh`, cùng các dòng comment nhắc số lớp cũ (header 1-8 layers, "layers
6-8" ở shared job runner, "layer 6/7" ở các dòng skip của tryon/motion).

```bash
# Lớp: sao lưu DB. Chứng minh: có bản dump trên volume, và nó NẠP LẠI ĐƯỢC.
# Chỉ kiểm "có file" thì vô nghĩa — một file .sql.gz rỗng vẫn là một file. --verify nạp
# thật vào DB tạm rồi so số dòng với .meta, nên nó là bằng chứng chứ không phải dấu vết.
log "6/9 Sao lưu database trên volume"
if [ "$SSH_OK" != 1 ]; then
  skip "needs SSH"
elif [ -z "$POD_VOLUME" ]; then
  # skip chứ không bad: pod không gắn volume là cấu hình hợp lệ, không phải hỏng hóc.
  skip "bỏ qua — không đặt POD_VOLUME"
elif remote "cd ~/motion-backend && bash ./setup/pod-pgdump.sh --check && bash ./setup/pod-pgdump.sh --verify"; then
  ok "có bản dump, và nạp lại được (đã diễn tập vào DB tạm)"
else
  # warn chứ không bad: thiếu backup không làm pod sai chức năng, nhưng phải nói to.
  warn "chưa có bản dump nạp được — chạy 'make gpu-db-dump'"
fi
```

Ghi chú cho Task 8: `docs/gpu-pod.md` (dòng ~1231, ~1253-1281) mô tả `make gpu-smoke` là
"8/8 lớp" với "lớp 6 motion, lớp 7 tryon, lớp 8 create-image" — số này giờ SAI (đã thành 9
lớp: 7 motion, 8 tryon, 9 create-image). Task 7 không sửa `docs/gpu-pod.md` — đó là việc của
Task 8 ("Nghiệm thu trên pod thật + docs").

- [ ] **Step 3: Kiểm cú pháp**

Run: `bash -n scripts/pod-smoke.sh && echo "cú pháp OK"`
Expected: in ra `cú pháp OK`.

- [ ] **Step 4: Commit**

```bash
git add scripts/pod-smoke.sh
git commit -m "gpu-smoke: thêm lớp chứng minh backup DB nạp lại được

Kiểm 'có file dump' là vô nghĩa — file .sql.gz rỗng vẫn là một file. Lớp này chạy
--verify, tức nạp thật vào DB tạm rồi so số dòng, nên nó chứng minh chứ không chỉ
ghi nhận. Bỏ qua khi không có POD_VOLUME: pod không volume là cấu hình hợp lệ."
```

---

### Đợt sửa sau review TOÀN NHÁNH (sau khi Task 1-7 xong, trước Task 8)

Vòng review toàn nhánh chạy sau khi cả bảy task đã vào. Bảy finding dưới đây đã sửa; ghi lại ở
đây để plan không mô tả một nhánh đã không còn tồn tại.

**C1 (Critical) — `POD_VOLUME` không tới được bốn target Makefile và lớp smoke.** Task 5 chỉ vá
đường `phase_pg_restore`. Bốn lệnh ssh trong `Makefile` (`gpu-down`, `gpu-destroy`,
`gpu-db-dump`, `gpu-db-check`) và lệnh ssh của lớp 6 `pod-smoke.sh` không truyền gì, mà `.env`
CỦA POD không có `POD_VOLUME` trên pod đầu tiên (`pod-volume.sh:309` gác khối ghi bằng
`[ -f "$ROOT/.env" ]`, và `.env` chưa tồn tại lúc đó vì rsync loại trừ `.env` + `.env.*`). Kết
quả: `pod-pgdump.sh:39` `die "POD_VOLUME trống…"` ở mọi điểm gọi — **cả tính năng là no-op trên
vòng đời pod đầu tiên**, và `make gpu-down` in một câu trấn an sai. Sửa bằng **hai nguồn độc
lập**: (1) truyền qua ssh ở cả năm chỗ, dùng đúng khuôn `$(call env,POD_VOLUME)` của ba target
`gpu-volume*`; (2) `phase_pg_restore` ghi `set_kv POD_VOLUME "$_vol"` vào `.env` sau khi
`phase_dotenv` đã dựng xong file — chỗ duy nhất `pod-volume.sh` không với tới được.
**KHÔNG gỡ guard `[ -f "$ROOT/.env" ]` ở `pod-volume.sh:309`** (lý do đã ghi ở fix round 2 của
Task 5: gỡ ra thì `phase_dotenv` bỏ qua bước dựng `.env` đầy đủ — hỏng nặng hơn).

**C2 (Critical) — `ln -sfn` không được kiểm; `latest` vắng ⇒ "phiên đầu" + rc=0.** Chuỗi mất dữ
liệu hoàn chỉnh với exit 0 ở MỌI bước: `ln -sfn` không kiểm exit code nên `do_dump` vẫn in `ok`;
`do_restore` chỉ nhìn `$LATEST`, vắng thì nói "đây là phiên đầu" và trả **0**, nên
`lib-feature.sh` không `warn` gì. Không phải giả định: volume là MooseFS, cùng mount đã chặn
`chown`, chưa ai đo `symlink()` ở đó. Ba thay đổi trong `pod-pgdump.sh`:

1. `ln -sfn … || die`. Dump và `.meta` đã ghi xong trước dòng đó, nên `die` **không mất backup**
   — nó nói "latest không tin được nữa", và fallback bên dưới lo phần còn lại.
2. `_latest_dump()`: `$LATEST` là ĐƯỜNG NHANH, không phải nguồn sự thật. Vắng hoặc treo thì quét
   `$DUMPS`, sort theo TÊN (timestamp UTC độ dài cố định — cùng khuôn `_prune`, không phải tin
   vào mtime của MooseFS). "Phiên đầu" chỉ được nói khi `$DUMPS` **thật sự** rỗng.
   `do_restore` / `do_check` / `do_verify` dùng chung một đường phân giải.
3. **Symlink TƯƠNG ĐỐI** (`dumps/motion-….sql.gz`) thay vì tuyệt đối. Lý do: `latest` và `dumps/`
   nằm cùng một cây thư mục trên volume nên link tương đối đúng bất kể volume mount ở đâu; link
   tuyệt đối chốt cứng `$POD_VOLUME` của pod ĐÃ TẠO RA nó, và pod sau mount ở path khác là link
   treo ngay. Hệ quả cho test: `readlink` trần không còn dùng được, thêm helper `latest_target()`
   thay cho năm chỗ cũ.

12 assertion mới trong `pgdump-test.sh`. **Bằng chứng ĐỎ trên code chưa vá: 44 xanh · 9 đỏ**,
gồm đúng đường "`latest` vắng nhưng `$DUMPS` còn dump hợp lệ" → `--restore` nhận `[]` thay vì
`[7]` và in "phiên đầu". Sau khi vá: **53 xanh · 0 đỏ**.

**I3 + M11 — lớp 6 của `pod-smoke.sh` không thể làm smoke đỏ.** Lớp 6 dùng `warn` nên
`make gpu-smoke` vẫn in "passed" khi không có backup nào — đây đúng là cổng lẽ ra phải bắt C1,
và nó tự tắt tiếng. Đổi sang `bad` khi `POD_VOLUME` **đã đặt** mà `--check`/`--verify` hỏng. Giữ
hai đường `skip` (không có `POD_VOLUME`; không ssh được), và thêm hẳn một probe `ssh … true` để
phân biệt "không tới được pod" với "thiếu backup". Bỏ `remote()` cho riêng lệnh này vì nó có
`2>/dev/null` mà `pod-pgdump.sh` báo lỗi nghiêm trọng qua `die()` ra stderr.

**I4 — `PG_DUMP_KEEP` là núm xoay chết.** `.env.example` ở gốc repo quảng cáo nó, nhưng
`pod-pgdump.sh:37` đọc từ `.env` CỦA POD, không caller nào truyền qua ssh, và
`motions-studio/.env.example` bị rsync loại trừ. Truyền ở `gpu-down` / `gpu-destroy` /
`gpu-db-dump` (`gpu-db-check` không dump nên không cần), dạng chỉ-chèn-khi-có-giá-trị bằng hàm
`if` của Make để không ghi đè mặc định 20 bằng chuỗi rỗng.

**I5 — tài liệu chỉ người dùng ghi backup vào thư mục mà restore không thấy.**
`docs/gpu-pod.md:613-623` hướng dẫn dump tay vào `/workspace/pg-backup`, khác hẳn
`$POD_VOLUME/pg/dumps`. Thay bằng `make gpu-db-dump` / `gpu-db-check`, và sửa dòng bảng
"DB sống qua `gpu-destroy` ❌ mất" cho đúng hiện trạng. Phần renumber "8/8 lớp" (`:1245`,
`:1295`) **để nguyên cho Task 8** sửa cùng số đo thật.

**M8** — `get_kv` của `pod-pgdump.sh` thêm `head -1`, khớp khuôn `pod-volume.sh:52`. Sau C1,
`.env` là nguồn chính của `POD_VOLUME` nên giá trị nhiều dòng ở đó là lỗi cực khó truy.

**M9** — ba target `gpu-volume*` chuyển sang `bash ./setup/pod-volume.sh`, thống nhất với bốn
target mới (không phụ thuộc bit `+x`, vì `chmod +x setup/*.sh` chỉ chạy trong `pod-bootstrap.sh`).

**M10** — comment ở `gpu-down` chứa `$0,99` bị Make nở thành rỗng khi `make -n`; đổi thành
`$$0,99`. Cùng bẫy đã tái phát ngay trong đợt này: một comment mới viết `$(if …)` dạng trần làm
`make -n gpu-down` chết với ``insufficient number of arguments (1) to function `if'`` — comment
giờ viết `$$(if …)` và nói rõ cái bẫy.

**I1 (không chặn merge, chỉ ghi chú)** — `pg_dump` chạy trước, `_row_counts` chạy sau; một INSERT
chen giữa làm `.meta` lệch **vĩnh viễn** với nội dung file dump, nên mọi `--verify` sau đó trên
bản dump ấy đều đỏ dù dump tốt (`gpu-down` dump khi api/worker vẫn online). **Không sửa cơ chế
trong đợt này.** Chỉ thêm comment ở chỗ tính `.meta` mô tả cái đua + hướng sửa đúng (đọc cả hai
từ CÙNG một snapshot), và mấy dòng ở nhánh verify-đỏ nói rõ nguyên nhân có thể là ghi xen giữa,
kèm dấu hiệu phân biệt với dump thật sự hỏng.

---

### Task 8: Nghiệm thu trên pod thật + tài liệu

**Files:**
- Modify: `docs/gpu-pod.md` (mục mới + cập nhật mục `#costs`)
- Modify: `docs/superpowers/specs/2026-08-07-pg-dump-volume-design.md` (đổi Trạng thái ở đầu file)

**Interfaces:**
- Consumes: toàn bộ Task 1-7.
- Produces: số đo thật thay cho lời hứa.

- [ ] **Step 1: Chạy bộ test local lần cuối**

Run: `bash motions-studio/setup/tests/pgdump-test.sh`
Expected: **53 xanh · 0 đỏ** (41 từ Task 1-4, +12 từ đợt sửa C2). Không đi tiếp nếu còn đỏ —
bước sau tốn tiền thuê pod.

- [ ] **Step 2: Diễn tập thật một vòng đầy đủ trên pod**

Đây là lần duy nhất chứng minh được cả đường dây, và nó tốn một vòng pod (~5 phút dựng lại theo số đo prebuilt: 284 giây). Chạy đúng thứ tự:

```bash
make gpu-up
make gpu-bootstrap          # kỳ vọng: "chưa có bản dump nào — bỏ qua (phiên đầu)"
# tạo vài dữ liệu thật qua UI, hoặc chạy: make gpu-smoke
make gpu-db-dump
make gpu-db-check           # ghi lại: tuổi, số bảng, số dòng
make gpu-down               # kỳ vọng: dump chạy lần nữa trước khi dừng
make gpu-destroy
# sửa .env: xoá GPU_INSTANCE_ID / GPU_SSH_HOST / GPU_SSH_PORT
make gpu-provision CONFIRM=yes
make gpu-wait
make gpu-bootstrap          # kỳ vọng: "tuổi bản dump: N giờ" rồi "khôi phục xong"
make gpu-db-check           # kỳ vọng: số dòng KHỚP số đã ghi ở trên
```

- [ ] **Step 3: Ghi số đo vào `docs/gpu-pod.md`**

Đợt sửa sau review đã thay đoạn "dump tay vào `/workspace/pg-backup`" (I5) bằng hướng dẫn
`make gpu-db-dump` / `gpu-db-check` và sửa dòng bảng "DB sống qua `gpu-destroy`". Task 8 còn
lại: **số đo thật**, và phần renumber "8/8 lớp" (`:1245`, `:1295`) mà I5 cố ý không đụng.

Thêm một mục mới `### Sao lưu database (pg_dump sang volume)` với: bố cục thư mục trên volume, ba điểm dump, chỗ khôi phục trong `feature_main()`, và **bảng số đo thật** từ Step 2 (thời gian dump, kích thước dump, số bảng/số dòng, thời gian khôi phục). Viết theo đúng khuôn các mục khác — số đo thật kèm ngày, không viết "nhanh"/"nhỏ" chung chung.

- [ ] **Step 4: Cập nhật mục `#costs`**

Mục `#costs` hiện nói `gpu-destroy` KHÔNG dừng đồng hồ volume, và `#pod-max-hours` giải thích vì sao phải `--stop-after` chứ không `--terminate-after` (vì `VOLUME_PGDATA=0` → mất database). Ràng buộc đó **vẫn đúng về mặt cơ chế** — `terminate` vẫn xoá DB — nhưng giờ DB dựng lại được từ volume. Sửa hai mục đó cho khớp: nói rõ mất tối đa những gì kể từ lần dump cuối, và vì sao vẫn giữ `--stop-after` (mất metadata từ lần dump cuối là mất thật, dù không còn là mất sạch).

- [ ] **Step 5: Đổi Trạng thái của spec**

Sửa dòng `**Trạng thái:** thiết kế, chưa triển khai.` ở đầu file spec thành trạng thái đã nghiệm thu, kèm ngày và tóm tắt số đo Step 2 — đúng khuôn spec `2026-08-05-mtc-prebuilt-runpod-design.md` đang dùng.

- [ ] **Step 6: Commit**

```bash
git add docs/gpu-pod.md docs/superpowers/specs/2026-08-07-pg-dump-volume-design.md
git commit -m "Nghiệm thu sao lưu DB trên pod thật, ghi số đo vào docs

Diễn tập đầy đủ: dump → gpu-destroy → dựng pod mới → dữ liệu quay về, số dòng khớp.
Đây là lần duy nhất chứng minh được cả đường dây; trước đó mọi thứ chỉ là test local
với Postgres trong docker."
```

---

## Đợt sửa cuối trước merge (2026-08-07)

**F1 — `ln -sfn … || die` đặt sai chỗ, chặn ba thứ phía sau.** Fix C2 đặt `|| die` ngay tại dòng
`ln`, tức TRƯỚC `_prune "$KEEP"`, trước khối kiểm quyền 700/600, và trước dòng `ok "dump: …"`.
Trên một volume không cho `symlink()` (chưa ai đo MooseFS, mà cùng mount đó đã chặn `chown`) thì
cả ba đều không bao giờ chạy: dump tích tụ vô hạn nên `PG_DUMP_KEEP` mất tác dụng hoàn toàn, mất
cảnh báo "dump chứa `api_keys` đang lộ quyền rộng", và mất tín hiệu tích cực duy nhất.

Sửa: ghi cờ `_ln_rc` tại chỗ, chạy hết phần sau, rồi `die` ở CUỐI hàm — sau khối `warn` quyền
(để hai chuyện độc lập không nuốt nhau khi cùng hỏng), trước `return 1` của `bad_perm`. Exit khác
0 giữ nguyên, nên assertion `--dump đỏ khi không tạo được latest` vẫn xanh.

2 assertion mới. **Bằng chứng ĐỎ trên code chưa vá: 53 xanh · 2 đỏ** — `_prune` không chạy nên còn
2 bản dù `PG_DUMP_KEEP=1`, và stdout không có dòng `dump: motion-…`.

## Sau khi xong

Bài toán còn lại (spec riêng, chưa viết): **hạ `DISK`**. Spec này là điều kiện cần cho nó — bước thu hoạch của bài đó là dựng pod mới với `DISK` nhỏ, tức đúng thao tác `gpu-destroy` mà tới giờ mới an toàn. Nghi vấn chính cần đo trước: `worker_runtime/linux.py` có 24 lần `tempfile.mkdtemp()` nhưng chỉ 1 `shutil.rmtree`, không janitor, không `rm -rf /tmp` lúc boot, không đặt `TMPDIR` — thư mục tạm của gần như mọi job có thể đang tích luỹ vĩnh viễn.
