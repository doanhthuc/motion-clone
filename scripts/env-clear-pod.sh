#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
# env-clear-pod.sh — xoá GIÁ TRỊ của ba khoá định danh pod trong .env sau khi pod đã bị xoá.
#
#   bash scripts/env-clear-pod.sh [.env]
#
# Gọi từ `make gpu-destroy`, và CHỈ ở nhánh đã verify pod biến mất thật khỏi `runpodctl pod list`
# / `vastai show instances`. Chạy nó khi pod còn sống là tự cắt đường ssh tới chính pod đang tính
# tiền của mình.
#
# VÌ SAO TỒN TẠI: từ 08/08/2026 `gpu-destroy` là thao tác MẶC ĐỊNH khi xong việc
# (docs/gpu-pod.md#destroy-first), không còn là ngoại lệ. Trước đó target chỉ IN RA lời nhắc xoá
# tay ba dòng — chấp nhận được khi mỗi tháng làm một lần, nhưng thành ba lần sửa tay mỗi phiên khi
# nó là thói quen. Và quên một lần thì `make gpu-wait` phiên sau bám vào một pod đã chết.
#
# VÌ SAO LÀ SCRIPT RIÊNG chứ không sed inline trong Makefile: `sed -i` khác nhau giữa macOS và GNU
# (macOS đòi `-i ''`), Makefile này chạy trên máy dev macOS còn phần lớn repo chạy trên Linux. Và
# cả hai nhánh runpod/vast đều cần, nên inline là chép đôi.
# ════════════════════════════════════════════════════════════════════════════
set -uo pipefail
cd "$(dirname "$0")/.."

ENV_FILE="${1:-.env}"
KEYS='GPU_INSTANCE_ID|GPU_SSH_HOST|GPU_SSH_PORT'

[ -f "$ENV_FILE" ] || { echo " !! không thấy $ENV_FILE — bỏ qua, không có gì để xoá"; exit 0; }

# Đọc mode thật để trả lại nguyên vẹn sau khi mv. `stat` khác cú pháp giữa BSD và GNU — cùng khuôn
# đã dùng ở setup/pod-pgdump.sh. Không đọc được thì thôi, đừng vì thế mà bỏ cả việc xoá.
_mode() { stat -f '%Lp' "$1" 2>/dev/null || stat -c '%a' "$1" 2>/dev/null; }
ORIG_MODE="$(_mode "$ENV_FILE")"

# mktemp CẠNH file đích, không phải /tmp: mv chỉ nguyên tử trong cùng filesystem, và .env có thể
# nằm trên một mount khác /tmp. Nguyên tử là điều kiện để .env không bao giờ ở trạng thái viết dở
# — nó giữ POSTGRES_PASSWORD, API_KEY và mọi thứ khác của pod.
TMP="$(mktemp "$ENV_FILE.XXXXXX")" || { echo " ✗ không tạo được file tạm cạnh $ENV_FILE — xoá tay ba khoá: $KEYS" >&2; exit 1; }
trap 'rm -f "$TMP"' EXIT

# Xoá PHẦN GIÁ TRỊ, giữ lại dòng `KEY=`. Xoá cả dòng sẽ làm `make gpu-preflight` báo "thiếu khoá"
# thay vì "chưa có pod" — hai chuyện khác nhau, và cái sau mới đúng.
if ! sed -E "s/^($KEYS)=.*/\1=/" "$ENV_FILE" > "$TMP"; then
  echo " ✗ sed lỗi — $ENV_FILE giữ nguyên. Xoá tay ba khoá: $KEYS" >&2
  exit 1
fi

# Cổng chặn: nếu file mới rỗng hoặc mất dòng so với bản gốc thì có gì đó sai — thà không xoá còn
# hơn ghi đè .env bằng một file cụt.
OLD_N="$(wc -l < "$ENV_FILE" | tr -d ' ')"
NEW_N="$(wc -l < "$TMP" | tr -d ' ')"
if [ ! -s "$TMP" ] || [ "$NEW_N" != "$OLD_N" ]; then
  echo " ✗ file kết quả lệch dòng ($OLD_N → $NEW_N) — KHÔNG ghi đè. Xoá tay ba khoá: $KEYS" >&2
  exit 1
fi

[ -n "$ORIG_MODE" ] && chmod "$ORIG_MODE" "$TMP" 2>/dev/null
mv "$TMP" "$ENV_FILE" || { echo " ✗ mv thất bại — $ENV_FILE giữ nguyên. Xoá tay ba khoá: $KEYS" >&2; exit 1; }
trap - EXIT

echo " ✓ đã xoá GPU_INSTANCE_ID / GPU_SSH_HOST / GPU_SSH_PORT trong $ENV_FILE"
