#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
# scrub-secrets.sh — Gỡ credentials và PII của bên thứ ba khỏi source, để fork
# này an toàn khi đặt repo public.
#
#   ./setup/scrub-secrets.sh          # scrub + kiểm tra
#   ./setup/scrub-secrets.sh --check  # CHỈ kiểm tra, không sửa (cổng chặn CI)
#
# IDEMPOTENT: chạy lại bao nhiêu lần cũng ra cùng kết quả.
#
# VÌ SAO PHẢI CÓ SCRIPT NÀY thay vì sửa tay một lần:
#   Ban đầu là vì rsync của sync-upstream.sh ghi đè các file đã scrub, đưa mọi
#   secret quay lại. Từ 02/08/2026 fork này KHÔNG lấy bản mới từ upstream nữa
#   (xem README §Nguồn gốc code), nên vai trò đó hết. Cái CÒN lại mới là lý do
#   thật để giữ nó: `--check` là cổng chặn trước mỗi commit lên một repo PUBLIC.
#   Secret không chỉ về theo đường sync — nó về theo đường ai đó dán một key vào
#   .env.example, hay thêm một seed có email thật. Cổng theo pattern bắt được cả
#   hai, và bắt được ở lúc còn sửa được.
#
# HAI NGUYÊN TẮC BẮT BUỘC:
#   1. Dò theo PATTERN, không theo số dòng — upstream đổi thứ tự dòng vẫn bắt được.
#   2. FILE NÀY KHÔNG ĐƯỢC CHỨA SECRET NÀO. Không hardcode key hay email thật để
#      làm chuỗi tìm kiếm, vì chính nó cũng đi vào repo public. Mọi thứ đều là
#      regex tổng quát. Bản đầu đã sai đúng chỗ này và cổng chặn cuối bắt được.
#
# Xem: docs/superpowers/specs/2026-07-31-toi-uu-khoi-tao-pod-design.md §3.2
# ════════════════════════════════════════════════════════════════════════════
set -uo pipefail
cd "$(dirname "$0")/.."; ROOT="$(pwd)"

CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

ok()   { printf '\033[1;32m  ✓ %s\033[0m\n' "$*"; }
act()  { printf '\033[1;36m  → %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m  ! %s\033[0m\n' "$*"; }
bad()  { printf '\033[1;31m  ✗ %s\033[0m\n' "$*"; }

command -v python3 >/dev/null 2>&1 || { bad "Cần python3."; exit 2; }

FAIL=0; CHANGED=0

# ── _apply FILE REGEX THAY NHÃN ─────────────────────────────────────────────
# Thay bằng python3 (an toàn với $ ( ) | \ trong chuỗi thay). Chỉ ghi khi nội
# dung THẬT SỰ đổi, nên chạy lại không báo "đã thay" giả.
_apply() {
  local f="$1" pat="$2" rep="$3" label="$4" n
  [ -f "$ROOT/$f" ] || { warn "$f không có (bỏ qua)"; return 0; }

  if [ "$CHECK_ONLY" = 1 ]; then
    if python3 -c 'import re,sys; sys.exit(0 if re.search(sys.argv[1], open(sys.argv[2],encoding="utf-8").read()) else 1)' \
         "$pat" "$ROOT/$f" 2>/dev/null; then
      bad "$f — CÒN $label"; FAIL=1
    fi
    return 0
  fi

  n="$(python3 - "$ROOT/$f" "$pat" "$rep" <<'PY'
import re, sys
path, pat, rep = sys.argv[1], sys.argv[2], sys.argv[3]
src = open(path, encoding="utf-8").read()
new, n = re.subn(pat, lambda _m: rep, src)
if new != src:
    open(path, "w", encoding="utf-8").write(new)
    print(n)
else:
    print(0)
PY
)"
  if [ "${n:-0}" -gt 0 ] 2>/dev/null; then act "$f — thay $n chỗ ($label)"; CHANGED=1
  else ok "$f — sạch ($label)"; fi
}

printf '\n\033[1;36m▶ scrub-secrets%s\033[0m\n' "$([ "$CHECK_ONLY" = 1 ] && echo ' --check')"

# ── 1. templates.json — credentials nhiều khách của upstream ────────────────
# File này KHÔNG được tồn tại trong repo. Bản mẫu: setup/templates.example.json.
# File thật đặt ở /workspace/secrets/templates.json (xem setup/pod.env.example).
if [ -f "$ROOT/setup/templates.json" ]; then
  if [ "$CHECK_ONLY" = 1 ]; then
    bad "setup/templates.json — CÒN TỒN TẠI (CF token + Gmail App Password + HF token của bên thứ ba)"
    FAIL=1
  else
    rm -f "$ROOT/setup/templates.json"; act "setup/templates.json — đã xóa"; CHANGED=1
  fi
else
  ok "setup/templates.json — không có"
fi

# ── 2. Admin key của Motion Task Cloud API ──────────────────────────────────
_apply setup/fullstack-setup.sh \
  'mtc_setup_[a-f0-9]{20,}' 'CHANGE_ME_YOUR_OWN_SETUP_KEY' 'admin key Motion Task Cloud'

# ── 3. DEFAULT_API_KEY — secret CHIA SẺ cho mọi bản deploy của source này ───
# Chỗ nguy hiểm nhất. setup-pm2.sh:495 gán nó làm API_KEY cho mọi bản cài mới,
# nên ai đọc được thì gọi được /api/motion/* của BẤT KỲ box nào chưa đổi key.
#
# `tr -dc a-f0-9` (giữ đúng ký tự hex) chứ KHÔNG phải `tr -d ' \n'`: biểu thức
# không chứa backslash nào nên không phải escape khi ghi vào file. Bản đầu dùng
# `tr -d ' \n'` đã sinh key LẪN NEWLINE giữa chuỗi (52 ký tự thay vì 51), đủ để
# làm vỡ parser .env.
#
# Không dùng được hàm rnd() có sẵn: nó định nghĩa ở dòng 42, SAU dòng gán 34.
RAND_EXPR='mk_$(head -c 24 /dev/urandom | od -An -tx1 | tr -dc a-f0-9)'
for f in setup/setup-pm2.sh setup/lib-feature.sh; do
  # (a) literal hex bất kỳ của upstream → biểu thức sinh lúc chạy
  _apply "$f" 'mk_[a-f0-9]{40,}' "$RAND_EXPR" 'DEFAULT_API_KEY dùng chung'
  # (b) chuẩn hóa mọi biểu thức mk_$(...) về canonical → tự sửa nếu lần scrub
  #     trước ghi ra biểu thức lỗi
  [ "$CHECK_ONLY" = 1 ] || _apply "$f" 'mk_\$\(head[^)]*\)' "$RAND_EXPR" 'canonical mk_$()'
done

# ── 4. Cùng key đó trong tài liệu → placeholder, không phải biểu thức shell ──
for f in DEPLOY.md README.md; do
  _apply "$f" 'mk_[a-f0-9]{40,}' 'mk_<sinh-tu-dong-khi-cai-dat>' 'key trong tài liệu'
done

# ── 5. Email cá nhân của bên thứ ba (PII) ──────────────────────────────────
# .env.example đặt SUPER_ADMIN = email thật của dev gốc: ai chạy setup mà không
# đổi thì email đó thành admin box của mình. Các file seed thì chèn thẳng user.
#
# KHÔNG liệt kê email thật ở đây (file này đi vào repo public). Thay vào đó:
# thay MỌI địa chỉ mail thường dùng trong các file dưới, TRỪ danh sách
# local-part đã biết là placeholder.
_scrub_emails() {
  local f="$1" fallback="$2" n
  [ -f "$ROOT/$f" ] || { warn "$f không có (bỏ qua)"; return 0; }
  n="$(CHECK_ONLY="$CHECK_ONLY" python3 - "$ROOT/$f" "$fallback" <<'PY'
import os, re, sys
path, fallback = sys.argv[1], sys.argv[2]
ALLOWED = {"you", "ban", "email", "admin", "user", "test",
           "doi-thanh-email-cua-ban", "admin-mau", "user-mau-1", "user-mau-2"}
RE = re.compile(r"[A-Za-z0-9._%+-]+@(?:gmail|outlook|yahoo|hotmail)\.com")
src = open(path, encoding="utf-8").read()

def repl(m):
    local = m.group(0).split("@")[0]
    return m.group(0) if local in ALLOWED else fallback

new, n = RE.subn(repl, src)
hits = sum(1 for m in RE.finditer(src) if m.group(0).split("@")[0] not in ALLOWED)
if os.environ.get("CHECK_ONLY") == "1":
    print(hits); sys.exit(0)
if new != src:
    open(path, "w", encoding="utf-8").write(new)
print(hits)
PY
)"
  if [ "${n:-0}" -gt 0 ] 2>/dev/null; then
    if [ "$CHECK_ONLY" = 1 ]; then bad "$f — CÒN $n email cá nhân"; FAIL=1
    else act "$f — thay $n email cá nhân"; CHANGED=1; fi
  else
    ok "$f — sạch (email cá nhân)"
  fi
}

_scrub_emails .env.example                     'doi-thanh-email-cua-ban@gmail.com'
_scrub_emails db/seed_users.sql                'user-mau-1@example.com'
_scrub_emails db/seeds/face_motion_workflow.sql 'admin-mau@example.com'

# ── 5b. Cưỡng chế .gitignore ────────────────────────────────────────────────
# Hai dòng này từng biến mất một lần thật: rsync của sync-upstream.sh ghi đè
# .gitignore bằng bản upstream, và lớp bảo vệ mất IM LẶNG — lần sau ai vô tình
# tạo setup/templates.json là nó lọt vào git. Đường sync đó đã bỏ (02/08/2026),
# nhưng cưỡng chế vẫn giữ: .gitignore là thứ người ta dọn dẹp mà không nghĩ, và
# hậu quả của việc mất đúng hai dòng này là một secret vào repo public.
GITIGNORE_MUST=(setup/templates.json setup/pod.env)
GI="$ROOT/.gitignore"
_missing_gi=""
for _e in "${GITIGNORE_MUST[@]}"; do
  grep -qxF "$_e" "$GI" 2>/dev/null || _missing_gi="${_missing_gi} $_e"
done
if [ -n "$_missing_gi" ]; then
  if [ "$CHECK_ONLY" = 1 ]; then
    bad ".gitignore THIẾU:${_missing_gi}"
    FAIL=1
  else
    { printf '\n# Fork: secrets + config runtime (KHÔNG commit) — scrub-secrets.sh cưỡng chế\n'
      for _e in $_missing_gi; do printf '%s\n' "$_e"; done
    } >> "$GI"
    act ".gitignore — thêm lại:${_missing_gi}"
    CHANGED=1
  fi
else
  ok ".gitignore — đủ mục bắt buộc"
fi

# ── 6. Cổng chặn cuối ───────────────────────────────────────────────────────
# Mọi mục dưới đây là REGEX TỔNG QUÁT, không phải secret — nên file này tự pass
# được cổng chặn của chính nó. Kiểm lại điều đó mỗi khi thêm pattern mới.
printf '\n\033[1;36m▶ Quét lần cuối\033[0m\n'
PATTERNS='cfut[A-Za-z0-9_-]{20,}|hf_[A-Za-z0-9]{30,}|mtc_setup_[a-f0-9]{20,}|mtcw_[A-Za-z0-9]{10,}|mk_[a-f0-9]{40,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----'
if git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  HITS="$(git -C "$ROOT" ls-files -z | xargs -0 grep -nEl "$PATTERNS" 2>/dev/null || true)"
else
  warn "Chưa có git repo → quét toàn bộ cây file thay vì file được track."
  HITS="$(grep -rEl "$PATTERNS" "$ROOT" --exclude-dir=.git --exclude-dir=node_modules --exclude-dir=venv 2>/dev/null || true)"
fi
if [ -n "$HITS" ]; then
  bad "CÒN SECRET — KHÔNG được commit/push:"
  printf '      %s\n' $HITS
  FAIL=1
else
  ok "Không còn secret nào đã biết."
fi

# ── 6b. Email cá nhân ở BẤT KỲ file nào được track ─────────────────────────
# Mục 5 chỉ sửa 3 file đã biết. Nhưng PII rò được qua chỗ không ai ngờ: bản đầu
# của spec trong docs/ đã chép nguyên văn email của dev gốc vào bảng phát hiện.
# Nên quét TOÀN BỘ, đừng dựa vào việc nhớ đường dẫn.
EMAIL_HITS="$(
  { git -C "$ROOT" ls-files 2>/dev/null || find "$ROOT" -type f -not -path '*/.git/*' -not -path '*/node_modules/*'; } \
  | python3 - "$ROOT" <<'PY'
import os, re, sys
root = sys.argv[1]
ALLOWED = {"you", "ban", "email", "admin", "user", "test",
           "doi-thanh-email-cua-ban", "admin-mau", "user-mau-1", "user-mau-2"}
RE = re.compile(r"[A-Za-z0-9._%+-]+@(?:gmail|outlook|yahoo|hotmail)\.com")
for line in sys.stdin:
    rel = line.strip()
    if not rel:
        continue
    p = rel if os.path.isabs(rel) else os.path.join(root, rel)
    try:
        txt = open(p, encoding="utf-8", errors="ignore").read()
    except (OSError, IsADirectoryError):
        continue
    for m in RE.finditer(txt):
        if m.group(0).split("@")[0] not in ALLOWED:
            print(f"{rel}: {m.group(0).split('@')[0][:3]}***@...")
PY
)"
if [ -n "$EMAIL_HITS" ]; then
  bad "CÒN EMAIL CÁ NHÂN của bên thứ ba:"
  printf '      %s\n' "$EMAIL_HITS"
  FAIL=1
else
  ok "Không còn email cá nhân nào."
fi

printf '\n'
if [ "$FAIL" = 1 ]; then bad "THẤT BẠI. Sửa các chỗ trên rồi chạy lại."; exit 1; fi
if [ "$CHECK_ONLY" = 1 ]; then ok "--check: source sạch."
elif [ "$CHANGED" = 1 ]; then ok "Đã scrub xong."
else ok "Không có gì phải scrub (đã sạch từ trước)."; fi

# ── Việc PHẢI làm ngoài code ────────────────────────────────────────────────
cat <<'NOTE'

  Ba việc script này KHÔNG làm được thay bạn:

  1. Token đã bị phát tán rồi — chúng nằm trong clone trên máy và có thể trong
     clone của người khác. Xóa khỏi source không thu hồi được. Chủ sở hữu nên
     rotate: tạo CF API token mới rồi revoke cái cũ, đổi Gmail App Password,
     revoke HF token, đổi NUXT_SETUP_API_KEY phía Motion Task Cloud.

  2. Box ĐANG CHẠY vẫn dùng API_KEY cũ (đã công khai), vì setup-pm2.sh giữ giá
     trị có sẵn trong .env. Đổi tay:
         API_KEY=mk_$(head -c 24 /dev/urandom | od -An -tx1 | tr -dc a-f0-9)
         # ghi vào .env backend VÀ NUXT_MOTION_API_KEY trong .env frontend
         pm2 restart api && pm2 restart motions

  3. Repo public sẽ không tự chạy được với người ngoài: MOTIONS_REPO mặc định
     trỏ tới một repo riêng tư. Đó là hệ quả, không phải lỗi.

NOTE
