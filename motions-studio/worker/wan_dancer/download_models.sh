#!/usr/bin/env bash
# #region ALD 20/07/2026 - Tải weights Wan-Dancer-14B (music-to-dance) cho node "Vũ đạo theo nhạc".
# Model chạy raw qua DiffSynth-Studio (KHÔNG ComfyUI) ⇒ KHÔNG nằm trong comfyui/catalog.json (catalog đó chỉ tải
# vào thư mục ComfyUI). Script này tải TRỰC TIẾP repo HF về $WAN_DANCER_MODEL. CHẠY TAY trên VPS GPU ≥ 90GB —
# KHÔNG gọi trong fullstack-setup.sh (.165 32GB không chạy nổi, không nên tải ~86GB vô ích).
#
#   WAN_DANCER_MODEL=/data/models/Wan-Dancer-14B ./download_models.sh
# #endregion
set -euo pipefail

REPO="Wan-AI/Wan-Dancer-14B"
DEST="${WAN_DANCER_MODEL:-}"

if [[ -z "$DEST" ]]; then
  echo "LỖI: set WAN_DANCER_MODEL trỏ tới thư mục đích trước. Vd:" >&2
  echo "  WAN_DANCER_MODEL=/data/models/Wan-Dancer-14B $0" >&2
  exit 2
fi

mkdir -p "$DEST"
echo "[wan-dancer] tải $REPO → $DEST"

# hf_transfer tăng tốc tải file lớn (global/local ~34.5GB mỗi cái).
export HF_HUB_ENABLE_HF_TRANSFER=1

# huggingface-cli (gói huggingface_hub). Cài nếu thiếu: pip install -U "huggingface_hub[hf_transfer]"
if command -v huggingface-cli >/dev/null 2>&1; then
  huggingface-cli download "$REPO" --local-dir "$DEST" --local-dir-use-symlinks False
elif command -v hf >/dev/null 2>&1; then
  hf download "$REPO" --local-dir "$DEST"
else
  echo "LỖI: thiếu huggingface-cli. Cài: pip install -U 'huggingface_hub[hf_transfer]'" >&2
  exit 3
fi

# Kiểm tra 2 file model chính có mặt + đủ lớn (chống tải hụt).
for f in global_model.safetensors local_model.safetensors; do
  p="$DEST/$f"
  if [[ ! -f "$p" ]]; then echo "LỖI: thiếu $f sau khi tải" >&2; exit 4; fi
  sz=$(stat -c%s "$p" 2>/dev/null || stat -f%z "$p")
  if [[ "$sz" -lt 30000000000 ]]; then echo "CẢNH BÁO: $f chỉ ${sz} byte (<30GB), có thể tải hụt" >&2; fi
done

echo "[wan-dancer] XONG. Nhớ set trong .env trên VPS:"
echo "  WAN_DANCER_MODEL=$DEST"
echo "  (tùy chọn) WAN_DANCER_PY=/path/venv/bin/python  # env có DiffSynth-Studio"
echo "Rồi: pm2 restart worker --update-env"
