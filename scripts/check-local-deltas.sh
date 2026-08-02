#!/usr/bin/env bash
# Canh các bản sửa CỤC BỘ mà fork này đặt lên file UPSTREAM.
#
# Vì sao cần: scripts/sync-upstream.sh rsync upstream → đây và CỐ Ý không dùng --delete (nếu dùng
# thì mọi file riêng của fork bị xoá sạch). Đánh đổi là file upstream ĐÃ SỬA thì bị ghi đè — âm
# thầm, không một dòng cảnh báo. Trước script này, cách duy nhất để không mất delta là con người
# nhớ ra và tự áp dụng lại; và mỗi lần quên thì triệu chứng quay về đúng dạng khó truy nhất:
# image không build được kèm một bức tường chữ về pipx, hoặc Postgres chết ở bước rsync PGDATA.
#
# Cách dùng:
#   bash scripts/check-local-deltas.sh          # kiểm, in ra cái nào mất
#   make sync-upstream                          # tự gọi ở bước cuối
#
# Thêm một delta mới: thêm một dòng vào DELTAS. Chọn MARKER là chuỗi ổn định nhất trong bản sửa
# (dòng lệnh thật, không phải dòng bình luận — bình luận dễ bị viết lại hơn).
set -uo pipefail
cd "$(dirname "$0")/.."

# file<TAB>chuỗi phải còn<TAB>vì sao, và mất thì hỏng ra sao
DELTAS=$(cat <<'EOF'
motions-studio/comfyui/Dockerfile	ENV PIP_BREAK_SYSTEM_PACKAGES=1	PEP 668 — base Ubuntu 24.04 chặn pip; mất dòng này là image KHÔNG build được
motions-studio/worker/runpod/Dockerfile	ENV PIP_BREAK_SYSTEM_PACKAGES=1	PEP 668 — như trên, cho image Task Cloud
motions-studio/setup/pod-volume.sh	VOLUME_PGDATA:-1	MooseFS không cho chown → PGDATA phải ở container disk; mất là bootstrap chết ở rsync PGDATA
motions-studio/setup/pod-volume.sh	trỏ thẳng volume, không cần symlink	MinIO từ chối symlink làm drive → --check báo đỏ oan cho cấu hình đúng
EOF
)

MISSING=0
COUNT=0
while IFS=$'\t' read -r f marker why; do
  [ -z "${f:-}" ] && continue
  COUNT=$((COUNT + 1))
  if [ ! -f "$f" ]; then
    printf '\033[31m ✗ \033[0m%s — KHÔNG TỒN TẠI\n' "$f"
    MISSING=$((MISSING + 1))
    continue
  fi
  if grep -qF -- "$marker" "$f"; then
    printf '\033[32m ✓ \033[0m%s — %s\n' "$f" "$marker"
  else
    printf '\033[31m ✗ \033[0m%s — MẤT: %s\n' "$f" "$marker"
    printf '     %s\n' "$why"
    printf '     Lấy lại:  git log --oneline -S "%s" -- %s\n' "$marker" "$f"
    MISSING=$((MISSING + 1))
  fi
done <<< "$DELTAS"

printf '\n'
if [ "$MISSING" -gt 0 ]; then
  printf '\033[31m ✗ \033[0m%d/%d delta cục bộ đã MẤT — gần như chắc chắn do một lần sync upstream ghi đè.\n' "$MISSING" "$COUNT"
  printf '   Áp dụng lại rồi chạy lại lệnh này. ĐỪNG commit khi còn đỏ: thứ hỏng sẽ không lộ ra\n'
  printf '   cho tới lần build hoặc lần dựng pod tiếp theo.\n'
  exit 1
fi
printf '\033[32m ✓ \033[0m%d/%d delta cục bộ còn nguyên.\n' "$COUNT" "$COUNT"
