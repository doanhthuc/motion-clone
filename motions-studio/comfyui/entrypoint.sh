#!/usr/bin/env bash
# ALD 15/06/2026 - Container ComfyUI CHỈ tự tải BỘ CORE tối thiểu (catalog.json tier=core) để chạy được ngay.
# Model nặng/optional cài on-demand qua app: Settings → Models AI → Download (api ghi vào volume models này).
# SKIP nếu file đã có (mount sẵn volume comfymodels). Đặt SKIP_MODELS=1 để bỏ qua hoàn toàn.
set -u
MODELS_DIR="/app/ComfyUI/models"
CATALOG="/app/catalog.json"

if [ "${SKIP_MODELS:-0}" != "1" ] && [ -f "$CATALOG" ]; then
  DL="wget"; command -v aria2c >/dev/null 2>&1 && DL="aria2c"
  echo "═══ Tải BỘ CORE model (catalog.json tier=core) bằng $DL — phần còn lại cài qua Settings → Models AI ═══"
  CORE_LIST="$(python - "$CATALOG" <<'PY'
import json,sys
try: c=json.load(open(sys.argv[1]))
except Exception: sys.exit(0)
for e in c.get("comfy",[]):
    if e.get("tier")=="core" and e.get("url") and e.get("type") and e.get("filename"):
        print("%s\t%s\t%s" % (e["type"], e["filename"], e["url"]))
PY
)"
  printf '%s\n' "$CORE_LIST" | while IFS="$(printf '\t')" read -r dest fname url; do
    [ -z "${dest:-}" ] || [ -z "${fname:-}" ] || [ -z "${url:-}" ] && continue
    mkdir -p "$MODELS_DIR/$dest"; out="$MODELS_DIR/$dest/$fname"
    if [ -f "$out" ] && [ -s "$out" ]; then echo "  ✓ skip $dest/$fname"; continue; fi
    echo "  ↓ tải $dest/$fname …"
    if [ "$DL" = "aria2c" ]; then
      AR=(-x16 -s16 -k1M --file-allocation=none --auto-file-renaming=false --allow-overwrite=true --console-log-level=warn -d "$MODELS_DIR/$dest" -o "$fname.part")
      [ -n "${HF_TOKEN:-}" ] && case "$url" in *huggingface.co*) AR+=(--header "Authorization: Bearer ${HF_TOKEN}");; esac
      if aria2c "${AR[@]}" "$url"; then mv "$out.part" "$out"; else echo "  ✗ LỖI $fname"; rm -f "$out.part"; fi
    else
      AUTH=(); [ -n "${HF_TOKEN:-}" ] && AUTH=(--header "Authorization: Bearer ${HF_TOKEN}")
      if wget -q --show-progress "${AUTH[@]}" -O "$out.part" "$url"; then mv "$out.part" "$out"; else echo "  ✗ LỖI $fname"; rm -f "$out.part"; fi
    fi
  done
  echo "═══ Core xong — khởi động ComfyUI (model khác: Settings → Models AI) ═══"
else
  echo "═══ Bỏ tải models (SKIP_MODELS / không có catalog) — khởi động ComfyUI ═══"
fi

exec python main.py --listen 0.0.0.0 --port 8188 --disable-auto-launch
