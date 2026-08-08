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
#
# CHỈ do_verify() còn dùng hàm này, và chỉ trên DB TẠM sau khi đã nạp xong bản dump — đó là vế
# "bằng chứng thật" của verify. `.meta` KHÔNG còn lấy số từ đây (xem _dump_row_counts).
_row_counts() {
  local db="${1:-$PG_DB}" q
  q="$(_psql -d "$db" -tAc \
    "SELECT string_agg(format('SELECT %L||''=''||count(*)::text FROM public.%I', tablename, tablename), ' UNION ALL ')
     FROM pg_tables WHERE schemaname='public'")"
  [ -n "$q" ] || return 0
  _psql -d "$db" -tAc "$q" | sed '/^$/d' | sort
}

# Đếm số dòng từng bảng TỪ CHÍNH FILE DUMP, không hỏi DB lần thứ hai.
#
# VÌ SAO không dùng _row_counts cho .meta: `pg_dump` chụp snapshot riêng của nó, nên một
# INSERT/DELETE xen vào giữa lúc pg_dump xong và lúc _row_counts chạy làm .meta lệch với nội
# dung file dump — và lệch VĨNH VIỄN, vì nó nằm trong file. Mọi `--verify` sau đó trên bản dump
# ấy đều đỏ dù bản dump hoàn toàn tốt. Không phải giả thuyết: `gpu-down` dump khi api/worker vẫn
# online, và lớp 6 của `pod-smoke.sh` dùng `bad`, nên cái đua này làm `make gpu-smoke` đỏ trên
# một pod khoẻ mạnh. Đọc từ file dump thì hai vế cùng một nguồn, đua biến mất theo định nghĩa.
#
# Điều này KHÔNG làm --verify thành vô nghĩa. Sau khi đổi, --verify so "số dòng file dump tự
# khai" với "số dòng thật sau khi nạp chính file đó vào Postgres". Vế thứ hai vẫn là bằng chứng
# thật — nó chứng minh file nạp lại được và nạp ra đúng nội dung, tức đúng câu hỏi mà một hệ
# backup cần trả lời. Chỉ vế "so với DB gốc tại MỘT THỜI ĐIỂM KHÁC" bị bỏ, và đó chính là vế
# gây đua, đồng thời cũng là vế không chứng minh được gì về bản dump.
#
# Định dạng plain của pg_dump, mỗi bảng một khối:
#     COPY public.jobs (id, kind) FROM stdin;
#     <dòng dữ liệu>…
#     \.
# Bảng rỗng vẫn có khối COPY với 0 dòng, nên không bảng nào bị bỏ sót.
#
# Một pass `awk`, không `grep -c` nhiều lần: nhiều pass vừa chậm (giải nén lại cả file mỗi lần)
# vừa không đếm nổi ranh giới khối.
#
# Giới hạn đã biết — cập nhật 2026-08-07 sau khi ĐO bằng pg_dump thật (postgres:18) trên một DB
# dựng riêng chứa mọi dạng tên khó, chạy dưới CẢ awk của macOS (BWK) lẫn mawk (awk mặc định của
# Ubuntu, tức của pod — repo đã có một lỗi mawk-vs-BWK ở pod-volume.sh:264):
#
#   XỬ ĐÚNG (đã đo, cả hai awk):
#     - tên trích dẫn kép: "order", "weird name (x)", "we""ird", "public.x"
#     - bảng 0 CỘT: pg_dump phát `COPY public.t0  FROM stdin;` — KHÔNG có danh sách cột
#     - tên CỘT chứa ngoặc: `COPY public.parencol ("col (x)") FROM stdin;`
#     - bảng rỗng (khối COPY 0 dòng), bảng ở schema khác public (đọc hết khối, không in)
#     - dòng mở đầu bằng `COPY ` nhưng KHÔNG phải header: pg_dump chép nguyên văn thân
#       `CREATE FUNCTION $$…$$`, `COMMENT ON`, định nghĩa view, nên một dòng như
#       `COPY public.jobs FROM '/tmp/x.csv' CSV;` xuất hiện thật ở cột 0 → BỎ QUA im lặng.
#       ĐO 2026-08-08 bằng pg_dump 18, cả ba dạng dưới đây có ĐÚNG MỘT dấu `"` (tức LẺ) nên
#       vòng trước — chỉ đếm dấu nháy — tố oan cả ba trên một bản dump hoàn hảo:
#           COPY jobs FROM /tmp/x.csv WITH (FORMAT csv, QUOTE ");   (thân CREATE FUNCTION)
#           COPY jobs FROM stdin -- doi dau " o day                 (COMMENT ON nhiều dòng)
#           COPY public.jobs FROM x " y                             (thân CREATE VIEW)
#       Xem chữ ký PHÂN BIỆT ĐƯỢC ở khối `pend` trong _dump_row_counts.
#
#   CHỌN BÁO LỖI TO thay vì đoán:
#     - identifier chứa XUỐNG DÒNG (`CREATE TABLE "new<LF>line"`, hoặc cột `"c<LF>c"`). pg_dump
#       phát header vỡ làm hai dòng vật lý; awk đọc theo dòng nên dòng đầu (`COPY public."new`)
#       không parse được ở dạng hiện tại.
#
#       ĐÍNH CHÍNH 2026-08-07 (review bắt được): bản trước ghi ở đây rằng "sửa ở parser cũng vô
#       ích vì ĐỊNH DẠNG .meta không biểu diễn nổi dạng tên này". SAI, và đã đo để bác:
#         `_row_counts` (vế `got`) đúng là vỡ — nó phát ra hai dòng `new` và `line=1`.
#         NHƯNG vế `want` đọc từ .meta cũng vỡ Y HỆT, và cả hai vế đều đi qua `sort`, nên chúng
#         TRÙNG KHÍT. Nối đúng hai dòng đó vào .meta rồi chạy `--verify`: XANH, rc=0.
#       Tức sửa Ở PARSER THÔI CŨNG ĐỦ để verify khớp; định dạng .meta KHÔNG phải ràng buộc chặn.
#
#       Vẫn giữ nguyên quyết định báo lỗi to, nhưng vì lý do KHÁC: biểu diễn ấy NHẬP NHẰNG. Cùng
#       lần đo trên, `--verify` xanh một cách TÌNH CỜ (hai lỗi vỡ giống nhau khử nhau, không phải
#       vì ai đó chứng minh được gì) và nó đếm "3 bảng" cho một DB có 2 bảng. Một cổng bằng-chứng
#       xanh nhờ trùng hợp còn tệ hơn một cổng đỏ: nó dạy người đọc tin vào thứ không kiểm gì.
#       Sửa cho đúng sẽ phải đổi định dạng .meta sang thứ thoát được <LF> (mỗi bảng vẫn một dòng,
#       nhưng tên được escape) — việc lớn hơn, và không có gì trong schema hiện tại đòi nó.
#       Nên: gom vào mảng `bad`, in ra STDERR, và `exit 1` ở END → _dump_row_counts trả khác 0
#       → do_dump cảnh báo to và trả 1. .meta THIẾU bảng đó (chứ không chứa khoá rác), nên
#       --verify sau đó sẽ đỏ — và người đọc đã có sẵn lời giải thích từ lúc --dump.
#     - dòng KẾT THÚC `FROM stdin;` nhưng NHÁY KÉP KHÔNG CÂN, ví dụ `COPY jobs " FROM stdin;`
#       trong thân CREATE FUNCTION (đo bằng pg_dump 18 — hình dạng THẬT, không bịa). Ở đây báo
#       to là ĐÚNG dù .meta vẫn đầy đủ: nhánh này không thể im, vì nếu để dòng ấy parse tiếp thì
#       `s` = `jobs "` mở một khối giả và ghi khoá rác `jobs "=N` — đúng thứ đoạn NGUYÊN TẮC
#       dưới cấm. Nhưng nó là nguyên nhân THỨ HAI làm cờ meta_incomplete được ghi, và ở trường
#       hợp này .meta ĐẦY ĐỦ và --verify XANH. Thông điệp ở cuối do_dump() nói cả hai; đừng viết
#       lại thành "nguyên nhân duy nhất là identifier có xuống dòng" — đã sai một lần rồi.
#     - bảng phân vùng (parent trong pg_tables nhưng dữ liệu ở các leaf) vẫn đếm khác _row_counts.
#
#   Schema hiện tại không chạm tới dạng nào ở nhóm hai (đã grep motions-studio/db/: không
#   PARTITION BY, không INHERITS, không bảng 0 cột, không identifier có ngoặc/xuống dòng).
#
# NGUYÊN TẮC XẾP THEO ƯU TIÊN: không bao giờ phát ra KHOÁ RÁC trong im lặng. Một khoá rác
# (`t0  FROM stdin;=0`, `parencol ("col (x)") FROM stdin;=2` — đúng những gì bản trước phát ra)
# làm --verify ĐỎ trên một bản dump hoàn toàn tốt, tức phá chính cái cổng bằng-chứng mà cả tính
# năng này dựa vào. Thà đỏ TO ở --dump còn hơn đỏ khó hiểu ở --verify ba tuần sau.
#
# LC_ALL=C: awk xử lý theo BYTE. Không có nó, awk trên một số nền tảng abort giữa chừng khi gặp
# byte không hợp lệ theo locale, và .meta sẽ cụt lặng lẽ.
_dump_row_counts() {
  gzip -dc "$1" | LC_ALL=C awk '
    # Đếm dấu nháy kép của một chuỗi. Bản sao cục bộ vì gsub sửa tại chỗ.
    function qcount(s,   t) { t = s; return gsub(/"/, "\"", t) }

    # Đang TRONG một khối COPY thì mọi dòng là DỮ LIỆU cho tới dòng CHỈ chứa \. — kiểm điều này
    # TRƯỚC khi thử nhận diện header, nếu không một ô text chứa đúng chuỗi "COPY … FROM stdin;"
    # sẽ mở một khối giả (pg_dump sinh thật dòng dữ liệu như vậy nếu người dùng nhập chuỗi đó).
    # Dòng dữ liệu KHÔNG BAO GIỜ bằng đúng \. vì pg_dump thoát backslash: giá trị \. ghi ra \\.
    intbl {
      if ($0 == "\\.") { if (pub) printf "%s=%d\n", tbl, n; intbl = 0 } else n++
      next
    }
    # ── HÀNG CHỜ: một dòng KHẢ NGHI chưa đủ để tố, phải NHÌN TIẾP ───────────────────────────
    # Đặt SAU luật intbl (dòng dữ liệu không bao giờ chạm hàng chờ được) và TRƯỚC luật /^COPY/.
    #
    # CHỮ KÝ PHÂN BIỆT ĐƯỢC, đo bằng pg_dump 18 (xem khối "Giới hạn đã biết" ở trên): một header
    # vỡ dòng LUÔN có dòng nối kết thúc bằng `FROM stdin;`, còn dòng COPY nằm trong thân
    # function/comment/view thì không. Nên khi gặp dòng khả nghi ta KHÔNG kết luận ngay mà treo
    # nó lại và đọc tiếp; chỉ tố khi thấy dòng nối.
    #
    # HAI CỔNG, phải qua CẢ HAI mới tố — một cổng thôi vẫn tố oan:
    #   1. dòng hiện tại kết thúc `FROM stdin;`
    #   2. TỔNG số dấu " của cả cụm (dòng khả nghi + các dòng đã nối) là CHẴN — tức identifier
    #      bị cắt giữa chừng đã ĐÓNG lại. Đây là cổng mạnh hơn: mọi header THẬT của pg_dump có
    #      số dấu " CHẴN, mà dòng khả nghi có số LẺ, nên "lẻ + chẵn = lẻ" ⇒ một header hợp lệ ở
    #      phía sau KHÔNG BAO GIỜ bị nuốt làm dòng nối. Đo trên dạng view ở trên: dòng khả nghi
    #      `COPY public.jobs FROM x " y` cách header thật `COPY public.jobs (id, kind) FROM
    #      stdin;` đúng 10 dòng, và tổng dấu " là 1 ⇒ LẺ ⇒ không tố. Đúng như phải thế.
    #
    # CỬA SỔ 8 DÒNG: một header vỡ dòng trải 1 + k dòng vật lý với k = số ký tự xuống dòng trong
    # identifier; bốn dạng đã đo (`"new<LF>line"`, `"a""b<LF>c"`, cột `"c<LF>c"`, bảng
    # `"x<LF>COPY y"`) đều có k=1, tức dòng nối là dòng NGAY SAU.
    #
    # Con số 8 là ĐO, không phải cảm tính. Đã chạy nhóm ba dạng tố oan và một bảng k=5 qua cửa
    # sổ 4/8/20/100:
    #     cửa sổ  4  → nhóm tố oan SẠCH, k=5 BỎ LỌT TRONG IM LẶNG (rc=0, .meta thiếu bảng, KHÔNG cờ)
    #     cửa sổ  8  → nhóm tố oan SẠCH, k=5 báo to (rc=1)
    #     cửa sổ 20/100 → y hệt 8
    # Tức 4 không mua thêm an toàn nào so với 8, mà đổi một lỗi ỒN lấy một lỗi IM — sai chiều so
    # với nguyên tắc của file này. Chọn 8 vì nó là giá trị nhỏ nhất đo được là đủ cho k=5, và vì
    # cửa sổ hẹp vẫn là cổng thứ ba độc lập với parity nếu sau này parity bị bào mòn.
    #
    # Vì sao cửa sổ rộng KHÔNG làm nhóm tố oan hỏng, dù dạng COMMENT ON cách header thật 20 dòng
    # và có một dấu `"` xen giữa (đủ để parity thành chẵn): dòng `"` xen giữa ấy CŨNG mở đầu bằng
    # `COPY ` và cũng lẻ, nên nó RƠI XUỐNG và TREO LẠI hàng chờ từ đầu, đặt lại pendbuf. Hành vi
    # đúng, nhưng là hệ quả PHỤ của việc treo-đè chứ không phải thứ được thiết kế ra để chặn —
    # nên đừng bỏ cửa sổ đi chỉ vì nó "có vẻ thừa".
    #
    # Không khớp thì RƠI XUỐNG (không `next`): dòng đang xét vẫn phải được xử như dòng thường —
    # nó có thể chính là một header COPY hợp lệ.
    pend {
      pendbuf = pendbuf "\n" $0
      if ($0 ~ /(^|[ \t])FROM stdin;$/ && qcount(pendbuf) % 2 == 0) {
        bad[++nbad] = pendline
        pend = 0
        next                                 # nuốt dòng nối: nó là ĐUÔI của header vỡ, không phải dòng riêng
      }
      if (++pendn >= 8) pend = 0              # hết cửa sổ ⇒ dòng khả nghi là dòng thường, KHÔNG tố
    }
    # Bắt MỌI dòng mở đầu bằng "COPY ", không chỉ dòng parse được — nhưng KHÔNG phải dòng nào
    # không khớp cũng là lỗi. Xem hàng chờ ngay trên: chỉ dòng mang CHỮ KÝ của một header
    # bị vỡ mới bị tố, phần còn lại đi tiếp trong im lặng đúng như bản trước nữa.
    /^COPY[ \t]/ {
      s = $0
      sub(/^COPY[ \t]+/, "", s)
      # ĐUÔI TRƯỚC, danh sách cột sau. Ngược lại (một regex nuốt cả `\(…\)[ \t]+FROM stdin;$`)
      # là chỗ hỏng của bản trước: bảng 0 cột không có `(…)` nên regex trượt, và tên cột chứa
      # ngoặc làm lớp `[^()]*` không nhảy qua nổi. Cả hai đều thành khoá rác.
      #
      # Không kết thúc bằng `FROM stdin;` thì đây KHÔNG phải header COPY của pg_dump. Hai khả năng:
      #   (a) dòng SQL bình thường mở đầu bằng `COPY ` mà pg_dump chép nguyên văn — thân
      #       `CREATE FUNCTION $$…$$`, `COMMENT ON`, định nghĩa view. Bình thường, phải BỎ QUA.
      #   (b) dòng VẬT LÝ ĐẦU của một header bị vỡ vì identifier chứa xuống dòng
      #       (`COPY public."new`). Đây mới là lỗi cần tố.
      # Điều kiện CẦN của (b): số dấu " LẺ. Identifier trích dẫn bị cắt giữa chừng luôn để lại
      # 1 + 2k dấu " trên dòng (dấu mở chưa đóng, mọi `""` bên trong đi theo cặp) ⇒ LẺ. Còn
      # một câu COPY … FROM <file> CSV; bình thường có 0 dấu " ⇒ CHẴN ⇒ bỏ qua ngay.
      #
      # Nhưng LẺ là điều kiện CẦN chứ KHÔNG ĐỦ — vòng trước dừng ở đây và tố oan cả ba dạng đã
      # đo bằng pg_dump thật (thân function có `QUOTE ")`, COMMENT ON nhiều dòng có một dấu ",
      # thân view có một dấu "). Cả ba đều làm `--dump` trả 1 với .meta ĐẦY ĐỦ và `--verify` sau
      # đó XANH — tức thông điệp "BẢN DUMP TỐT NHƯNG .meta THIẾU BẢNG" sai sự thật,
      # `make gpu-down` in "sao lưu DB thất bại" mỗi lần, và `make gpu-db-dump`
      # (Makefile:156-161, không có `|| echo`) hỏng thẳng.
      #
      # Nên dòng lẻ chỉ được TREO vào hàng chờ, chưa tố. Việc tố do khối `pend` ở trên quyết,
      # sau khi thấy dòng nối kết thúc `FROM stdin;`.
      if (s !~ /[ \t]FROM stdin;$/) {
        if (qcount($0) % 2 == 1) { pend = 1; pendline = $0; pendbuf = $0; pendn = 0 }
        next
      }
      sub(/[ \t]+FROM stdin;$/, "", s)
      # Cắt danh sách cột: dấu "(" ĐẦU TIÊN NẰM NGOÀI nháy kép. Không cắt từ dấu "(" đầu tiên
      # tuyệt đối — tên bảng có thể chứa nó (`public."weird name (x)"`); nhưng khi đó nó BẮT BUỘC
      # nằm trong nháy kép, vì identifier trần của Postgres không chứa được ngoặc. Quét trái sang
      # phải, `""` bên trong nháy chỉ là tắt-rồi-bật nên không cần xử riêng.
      q = 0; cut = 0
      for (i = 1; i <= length(s); i++) {
        c = substr(s, i, 1)
        if (c == "\"") q = !q
        else if (!q && c == "(") { cut = i; break }
      }
      if (q) { bad[++nbad] = $0; next }        # nháy kép không đóng ⇒ identifier vỡ dòng
      if (cut) s = substr(s, 1, cut - 1)
      sub(/[ \t]+$/, "", s)                    # bảng 0 cột để lại đúng một dấu cách thừa ở đây
      if (s == "") { bad[++nbad] = $0; next }
      # Chỉ schema public, khớp đúng phạm vi của _row_counts (nó lọc schemaname=public). Bảng ở
      # schema khác vẫn phải ĐỌC HẾT khối để không đếm nhầm dòng, chỉ là không in ra.
      if (s ~ /^public\./)                      { s = substr(s, 8);  pub = 1 }
      else if (s ~ /^"public"\./)               { s = substr(s, 10); pub = 1 }
      else if (s ~ /^[^".]+\./)                 { pub = 0 }
      else if (s ~ /^"/ && index(s, "\".") > 0) { pub = 0 }
      else                                      { pub = 1 }   # không có tiền tố schema
      # Bỏ trích dẫn kép để khớp _row_counts, vốn in tên TRẦN (format %L trên tablename).
      if (s ~ /^".*"$/) { s = substr(s, 2, length(s) - 2); gsub(/""/, "\"", s) }
      # `pend = 0`: một header HỢP LỆ đóng hàng chờ lại. Không có dòng này thì trạng thái "đang
      # chờ" sống xuyên qua cả khối COPY (luật intbl `next` trước khi tới hàng chờ, nên bộ đếm
      # cửa sổ đứng im) rồi bật lại ở dòng đầu tiên sau `\.` — tức nhìn tiếp vào một chỗ cách
      # dòng khả nghi hàng nghìn dòng. Đóng ở đây là chỗ duy nhất đúng.
      tbl = s; n = 0; intbl = 1; pend = 0; next
    }
    # STDERR + exit 1, KHÔNG in vào stdout: stdout của hàm này chảy thẳng vào .meta, nên báo lỗi
    # ở đó cũng chính là ghi khoá rác — đúng thứ ta đang bịt. mawk 1.3.4 hỗ trợ "/dev/stderr"
    # (đã đo trong container ubuntu:24.04), BWK awk cũng vậy.
    # Cắt ở 5 dòng: một dump lỗi cả loạt không được biến thành mấy nghìn dòng cảnh báo.
    END {
      if (nbad > 0) {
        for (i = 1; i <= nbad && i <= 5; i++)
          printf "  header COPY không parse được: %s\n", bad[i] > "/dev/stderr"
        if (nbad > 5) printf "  … và %d dòng nữa\n", nbad - 5 > "/dev/stderr"
        exit 1
      }
    }
  ' | LC_ALL=C sort
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

# Filesystem ở thư mục này có TÔN TRỌNG chmod không? Trả 0 = có, khác 0 = không.
#
# Không phải câu hỏi lý thuyết. Nghiệm thu trên pod RunPod ngày 2026-08-07:
#     chmod 600 <file trên /workspace>  → stat vẫn ra 666
#     chmod 600 <file trên /tmp>        → stat ra 600
#     thư mục trên volume               → 777 cố định
#     mount: mfs#euro-3.runpod.net:9421 on /workspace type fuse
#            (rw,nosuid,nodev,relatime,user_id=0,group_id=0,allow_other)
# MooseFS BỎ QUA chmod hoàn toàn — y như nó đã chặn chown (pod-volume.sh:179-191).
# Nên khối kiểm quyền cuối do_dump() ĐỎ ở MỌI lần dump trên pod, dù bản dump hoàn toàn
# tốt: `make gpu-down` in "sao lưu DB thất bại" mỗi lần. Báo động giả thường trực dạy
# người dùng bỏ qua cảnh báo — đúng thứ ngược lại với thứ khối kiểm đó tồn tại để làm.
#
# Đo thay vì đoán: mode SAI trên fs bỏ qua chmod là chuyện bình thường không sửa được;
# mode SAI trên fs bình thường là lỗi thật. Hai chuyện khác hẳn nhau, phải xử khác nhau.
#
# Chiều hỏng của hàm này CỐ Ý nghiêng về phía ồn: không tạo được file dò (thư mục lạ,
# hết chỗ) ⇒ trả 0 = "có tôn trọng" ⇒ giữ nguyên cảnh báo to. Thà cảnh báo thừa một lần
# vì không đo được, còn hơn im lặng nuốt một mode sai thật.
# Cũng vậy nếu fs ép sẵn file về đúng 600 khi tạo: hàm nói "có tôn trọng" → vẫn ồn.
#
# Tên file dò bắt đầu bằng dấu chấm nên không lọt vào glob motion-*.sql.gz của _prune.
_fs_honors_modes() {
  local d="$1" probe rc=1
  probe="$(mktemp "$d/.mode-probe.XXXXXX" 2>/dev/null)" || return 0
  chmod 600 "$probe" 2>/dev/null
  [ "$(_mode "$probe")" = "600" ] && rc=0
  rm -f "$probe" 2>/dev/null      # dọn KỂ CẢ khi hỏng — chạy trước mọi đường return
  return "$rc"
}

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

  # Số dòng lấy từ CHÍNH FILE DUMP vừa ghi ($tmp), KHÔNG truy vấn DB lần hai — xem
  # _dump_row_counts() để biết vì sao. Tóm tắt: hỏi DB lần hai mở ra một cửa sổ đua với
  # pg_dump, và lệch sinh ra ở đó nằm vĩnh viễn trong .meta.
  #
  # $tmp chứ không phải $out: `mv` xuống dưới mới chạy. Cả hai là cùng một file, nhưng đọc
  # $tmp giữ được thứ tự "ghi xong hết mới công bố".
  # `{ … }` KHÔNG phải subshell, nên $meta_rc gán bên trong vẫn thấy được ở ngoài. Cần đúng
  # tính chất đó: _dump_row_counts trả khác 0 khi nó gặp một dòng `COPY …` không parse nổi,
  # và tín hiệu ấy phải sống sót ra khỏi khối chuyển hướng để do_dump nói ra được.
  local meta_rc=0
  {
    echo "created=$(date -u +%s)"
    # server_version_num, KHÔNG dùng server_version: bản Ubuntu đóng gói trả chuỗi kiểu
    # "16.4 (Ubuntu 16.4-0ubuntu0.24.04.1)", sau tr -d ' ' dính thành một token xấu
    # trong .meta. server_version_num là số nguyên (vd 160004), không cần parse gì thêm.
    echo "pg_version=$(_psql -d "$PG_DB" -tAc 'SHOW server_version_num' | tr -d ' ')"
    echo "dump_bytes=$(wc -c < "$tmp" | tr -d ' ')"
    _dump_row_counts "$tmp" || meta_rc=1
    # CỜ ĐI CÙNG FILE, không chỉ đi cùng lần chạy này. Cảnh báo `--dump` in ra stderr biến mất
    # ngay khi terminal cuộn qua, nhưng hệ quả (`.meta` cụt ⇒ mọi `--verify` sau này đỏ) sống
    # cùng bản dump. Không có cờ, ba tuần sau lớp 6 của gpu-smoke đỏ và do_verify chẩn đoán
    # "nghi ngờ file hỏng hoặc .meta bị sửa tay" — CHỈ SAI HƯỚNG, đúng cái bẫy mà khối cảnh báo
    # cuối do_dump() tồn tại để chặn. Ghi vào .meta thì lời giải thích đi theo đúng vật thể mà
    # người đọc đang cầm.
    # Dạng `<khoá>=<số>` để không phá bất biến định dạng của .meta; lọc ra ở mọi nơi liệt kê
    # bảng (do_check, do_verify), nếu không nó bị đếm nhầm thành một bảng tên meta_incomplete.
    [ "$meta_rc" = 0 ] || echo "meta_incomplete=1"
  } > "$meta"
  chmod 600 "$meta" 2>/dev/null || true

  # mv trong CÙNG filesystem là nguyên tử → latest không bao giờ trỏ một file ghi dở.
  mv "$tmp" "$out" || { rm -f "$tmp" "$meta"; die "mv dump thất bại"; }

  # Link TƯƠNG ĐỐI, không tuyệt đối. `latest` và `dumps/` nằm cùng một cây thư mục trên volume,
  # nên link tương đối đúng bất kể volume được mount ở đâu. Link tuyệt đối chốt cứng $POD_VOLUME
  # của POD ĐÃ TẠO RA nó — pod sau mount ở path khác (hoặc người dùng đổi POD_VOLUME) là link
  # treo ngay, đúng vào lúc ta cần nó nhất.
  #
  # Hỏng symlink PHẢI làm --dump đỏ: chưa ai đo `symlink()` trên MooseFS, mà cùng mount đó đã
  # chặn `chown`. Không kiểm thì --dump in "ok" và trả 0 trên một volume không có `latest`.
  #
  # Nhưng GHI CỜ rồi đỏ ở CUỐI HÀM, không `|| die` ngay tại đây. `die` ở đúng dòng này chặn mất
  # ba thứ nằm phía sau nó, và cả ba đều quan trọng hơn cái symlink:
  #   1. `_prune` không bao giờ chạy → dump tích tụ vô hạn, PG_DUMP_KEEP mất tác dụng hoàn toàn
  #      trên đúng cái volume đang hỏng symlink (tức volume ta chạy mọi lần dump lên đó);
  #   2. khối kiểm quyền 700/600 không chạy → mất cảnh báo "dump chứa api_keys đang lộ quyền rộng";
  #   3. dòng `ok "dump: …"` không in → mất tín hiệu tích cực duy nhất cho biết dump ĐÃ ghi xong.
  # Dump và .meta đã nằm trên đĩa từ trước dòng này, nên hoãn phần đỏ lại không mất gì thêm.
  local _ln_rc=0
  ln -sfn "dumps/$(basename "$out")" "$LATEST" || _ln_rc=1
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

  # Mode lệch: DÒ xem filesystem có tôn trọng chmod không rồi mới quyết. Trên MooseFS của
  # RunPod thì không, và ở đó mode lệch là chuyện bình thường không sửa được — báo động ở
  # đấy là báo động giả, mà báo động giả thường trực làm hỏng mọi cảnh báo thật còn lại.
  if [ -n "$bad_perm" ] && ! _fs_honors_modes "$DUMPS"; then
    log "volume này bỏ qua chmod (đo thật: MooseFS của RunPod mount user_id=0,group_id=0), nên không đặt được 600 —$bad_perm; dump vẫn tốt. Bảo mật ở đây dựa vào volume là riêng của tài khoản và pod đơn-người-thuê, không dựa vào mode."
    bad_perm=""
  fi

  if [ -n "$bad_perm" ]; then
    warn "QUYỀN SAI trên dump —$bad_perm (cần thư mục=700, file=600)."
    warn "Dump chứa dữ liệu nhạy cảm (api_keys, user_sessions.token_hash, token social_accounts)"
    warn "và có thể đang lộ rộng hơn dự tính. KHÔNG xoá — mất hẳn backup còn tệ hơn một bản backup"
    warn "quyền rộng. Filesystem này CÓ tôn trọng chmod (đã dò), nên đây là lỗi thật sửa được:"
    warn "tự kiểm tra và chmod tay, rồi tìm hiểu vì sao chmod trong do_dump không ăn."
  fi

  # .meta KHÔNG ĐẦY ĐỦ — dòng chi tiết đã do awk in ra stderr ngay trên. Nói to ở đây vì hệ quả
  # nằm ở TƯƠNG LAI, không ở lần chạy này: bản dump vẫn nạp lại được, nhưng .meta thiếu bảng nên
  # MỌI `--verify` sau này trên bản dump ấy đều đỏ. Không báo bây giờ thì ba tuần nữa gpu-smoke
  # đỏ ở lớp 6 và không ai truy ra được vì sao.
  if [ "$meta_rc" != 0 ]; then
    warn "BẢN DUMP TỐT — nhưng có dòng COPY parser không xử được (chi tiết ở trên)."
    # ĐÍNH CHÍNH 2026-08-08: bản trước nói "nguyên nhân đã biết DUY NHẤT: identifier chứa XUỐNG
    # DÒNG" và khẳng định thẳng ".meta THIẾU BẢNG". Cả hai đều SAI, đã đo bằng pg_dump 18: một
    # dòng `COPY jobs " FROM stdin;` nằm trong thân CREATE FUNCTION cũng tới được đây, và ở đó
    # .meta ĐẦY ĐỦ, --verify XANH. Nói chắc là thiếu bảng khi nó không thiếu chính là kiểu sai
    # hướng mà khối này tồn tại để chặn.
    warn "HAI nguyên nhân đã biết, phân biệt bằng chính dòng in ở trên:"
    warn " 1. identifier chứa ký tự XUỐNG DÒNG (tên bảng hoặc tên cột): pg_dump phát header vỡ"
    warn "    làm nhiều dòng vật lý, awk đọc theo dòng nên không ghép lại được. .meta THẬT SỰ"
    warn "    thiếu bảng đó ⇒ --verify trên bản này sẽ ĐỎ, và đỏ vì .meta cụt chứ không phải"
    warn "    vì file hỏng. Dòng in ở trên khi đó là một header CỤT (kết thúc giữa chừng)."
    warn " 2. một dòng SQL bình thường pg_dump chép NGUYÊN VĂN (thân CREATE FUNCTION, COMMENT ON,"
    warn "    định nghĩa view) trông đủ giống header: mở đầu \`COPY \`, kết thúc \`FROM stdin;\`,"
    warn "    nhưng nháy kép không cân hoặc tên bảng rỗng. Khi đó .meta VẪN ĐẦY ĐỦ và --verify"
    warn "    XANH. Parser báo to thay vì đoán, vì đoán sai ở đây là ghi KHOÁ RÁC vào .meta —"
    warn "    thứ làm --verify đỏ khó hiểu ba tuần sau trên một bản dump hoàn toàn tốt."
    warn "Chạy \`--verify\` để biết mình đang ở trường hợp nào. Dump ĐÃ ghi xong ở $out và vẫn"
    warn "--restore được — KHÔNG xoá nó, cả hai trường hợp."
  fi

  # Phần đỏ của symlink, hoãn từ trên xuống. In SAU cảnh báo quyền để không nuốt mất nó khi cả
  # hai cùng hỏng — hai chuyện độc lập, người đọc cần thấy cả hai chứ không phải cái nào tới trước.
  [ "$_ln_rc" = 0 ] || die "không tạo được symlink $LATEST — bản dump ĐÃ ghi xong ở $out, không mất gì. --restore/--check vẫn tìm được nó bằng cách quét $DUMPS."

  [ -z "$bad_perm" ] || return 1
  [ "$meta_rc" = 0 ] || return 1
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
  grep -vE '^(created|pg_version|dump_bytes|meta_incomplete)=' "$meta" 2>/dev/null | sed 's/^/    /'

  # Nói ngay ở --check, đừng đợi --verify đỏ rồi mới đoán: danh sách ngay trên CÓ THỂ thiếu bảng.
  # "CÓ THỂ", không phải "THIẾU" — xem đính chính 2026-08-08 ở cuối do_dump: cờ này cũng được ghi
  # cho một dòng SQL chép nguyên văn trông giống header, và ở trường hợp đó danh sách ĐẦY ĐỦ.
  # --check không đọc lại file dump nên nó KHÔNG phân biệt được hai trường hợp; chỉ --verify mới.
  if grep -q '^meta_incomplete=1' "$meta" 2>/dev/null; then
    warn "…và danh sách trên CÓ THỂ THIẾU BẢNG: lúc --dump có dòng COPY parser không xử được."
    warn "Bản dump vẫn tốt và vẫn --restore được. Chạy --verify để biết chắc: XANH nghĩa là danh"
    warn "sách trên vẫn đủ (dòng kia chỉ là SQL trông giống header); ĐỎ nghĩa là .meta thật sự cụt."
  fi

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
    # Bộ lọc `meta_incomplete` ở đây KHÔNG phải phòng thủ suông — nó tới được, và có test đỏ
    # được. Đo 2026-08-08: một dòng `COPY jobs " FROM stdin;` trong thân CREATE FUNCTION làm
    # --dump ghi cờ NHƯNG .meta vẫn ĐẦY ĐỦ. Bỏ `meta_incomplete` khỏi bộ lọc này thì `want` thừa
    # đúng một dòng `meta_incomplete=1`, và --verify ĐỎ trên một bản dump hoàn hảo với phần diff
    # vô nghĩa. Đã dựng mutant đúng như vậy và assertion tương ứng trong pgdump-test.sh đỏ.
    want="$(grep -vE '^(created|pg_version|dump_bytes|meta_incomplete)=' "$meta" 2>/dev/null | sort)"
    got="$(_row_counts "$vdb")"
    if [ "$want" = "$got" ]; then
      ok "verify: nạp lại được và số dòng khớp .meta ($(printf '%s\n' "$got" | wc -l | tr -d ' ') bảng)"
    else
      warn "verify ĐỎ — số dòng lệch so với .meta:"
      diff <(printf '%s\n' "$want") <(printf '%s\n' "$got") | sed 's/^/    /'
      # Lệch ở đây giờ NGHIÊM TRỌNG HƠN trước. `.meta` được đếm từ chính nội dung file dump
      # (_dump_row_counts), không phải từ một truy vấn DB thứ hai, nên cái đua cũ — INSERT chen
      # vào giữa pg_dump và lần đếm — không còn sinh ra triệu chứng này được nữa.
      warn "Hai vế đều đọc từ CÙNG bản dump này: vế trái là số dòng ghi trong file, vế phải là"
      warn "số dòng đếm được sau khi nạp thật file đó vào một DB tạm."
      # Đọc CỜ trước khi chẩn đoán. Nếu cờ có mặt thì .meta đã được đánh dấu không đầy đủ NGAY
      # TỪ LÚC GHI. Không hỏi cờ mà đọc thẳng câu "nghi ngờ file hỏng hoặc .meta bị sửa tay" là
      # chỉ SAI HƯỚNG đúng vào kịch bản phổ biến nhất — người đọc sẽ đi soi gzip và mtime của
      # một bản dump hoàn toàn lành.
      #
      # Cờ + verify ĐỎ ⇒ gần như chắc chắn là nguyên nhân (1) ở cuối do_dump (identifier có
      # xuống dòng): nguyên nhân (2) để lại .meta ĐẦY ĐỦ nên nó làm verify XANH, không tới đây.
      # "Gần như" chứ không "chắc chắn": (2) cộng thêm một lệch thật sự khác vẫn rơi vào nhánh
      # này. Vì thế câu dưới nói KHẢ NĂNG CAO NHẤT và vẫn chỉ đường kiểm lại, không chốt cứng.
      if grep -q '^meta_incomplete=1' "$meta" 2>/dev/null; then
        warn "KHẢ NĂNG CAO NHẤT: .meta này ĐÃ ĐƯỢC ĐÁNH DẤU KHÔNG ĐẦY ĐỦ ngay lúc --dump — lúc"
        warn "đó có dòng COPY parser không xử được, điển hình là identifier chứa ký tự XUỐNG DÒNG."
        warn "Khi đó .meta thiếu hẳn bảng chứ file dump KHÔNG hỏng. Đọc phần diff trên: nếu nó chỉ"
        warn "gồm những dòng CHỈ CÓ Ở VẾ PHẢI thì đúng là vậy — vế phải (nạp lại thật) mới là số"
        warn "đúng, và đừng đi soi file dump, nó vẫn --restore được bình thường. Còn nếu diff có"
        warn "bảng CÓ Ở CẢ HAI VẾ mà số đếm lệch, thì cờ này KHÔNG giải thích được nó — đó là một"
        warn "lệch thật, phải truy riêng và không được bỏ qua."
      else
        warn "Lệch nghĩa là nạp lại KHÔNG ra đúng nội dung — nghi ngờ file hỏng, hoặc .meta bị"
        warn "sửa tay. Đây KHÔNG còn là đua ghi-trong-lúc-dump; đừng bỏ qua nó như trước."
      fi
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

# Chỉ điều phối khi file được CHẠY, không khi được `source`. Test cần gọi thẳng
# _fs_honors_modes() để kiểm nhánh "filesystem bỏ qua chmod" như một đơn vị, chứ không
# chỉ suy ra nó qua exit code của --dump; mà `source` file này khi vẫn còn `case` trần
# thì nhánh `*)` sẽ `exit 2` và giết luôn shell của test.
# Khi chạy bình thường ${BASH_SOURCE[0]} == $0 nên hành vi không đổi một chút nào.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  case "${1:-}" in
    --dump)    do_dump ;;
    --restore) do_restore ;;
    --check)   do_check ;;
    --verify)  do_verify ;;
    *) echo "dùng: $0 --dump|--restore|--check|--verify" >&2; exit 2 ;;
  esac
fi
