#!/usr/bin/env bash
# Entrypoint image serverless cho stack tự chủ: sinh WORKER_ID duy nhất → ComfyUI nền → handler.
set -euo pipefail

# WORKER_ID PHẢI khác nhau giữa các container. api/src/routes/jobs.js:219 reclaim mọi job
# 'running' của cùng worker_id mỗi lần có ai gọi /worker/claim, nên hai container dùng chung id
# nghĩa là container B chuyển job đang render của container A sang 'error'. Với max workers >= 2
# lỗi này xảy ra ngay job thứ hai, và triệu chứng ("Worker khởi động lại giữa chừng") không hề
# gợi ý nguyên nhân. Ưu tiên id RunPod cấp; không có thì sinh ngẫu nhiên.
export WORKER_ID="${WORKER_ID:-serverless-${RUNPOD_POD_ID:-$(head -c 8 /dev/urandom | od -An -tx1 | tr -d ' \n')}}"

VOL="${MOTION_VOLUME:-/runpod-volume}"

# ── Log ra Network Volume ─────────────────────────────────────────────────────
# Container serverless là hộp đen: RunPod KHÔNG có API log (không `runpodctl serverless logs`, REST
# v1 chỉ có /endpoints, GraphQL tắt introspection) — log chỉ xem được bằng mắt trên dashboard. Khi
# một request trả về "job timed out" thì không có cách nào bằng lệnh để biết container chết ở đâu.
# Volume thì pod đọc được, nên đổ toàn bộ stdout/stderr vào đó luôn.
# Đặt TRƯỚC cổng kiểm comfy-models bên dưới, để chính thông báo lỗi của cổng đó cũng được ghi lại.
# Bản thân việc file log có xuất hiện hay không đã là một tín hiệu: không có file nào nghĩa là
# volume không mount được, chưa cần đọc nội dung.
if [ -d "$VOL" ] && mkdir -p "$VOL/serverless-logs" 2>/dev/null; then
  LOGF="$VOL/serverless-logs/$WORKER_ID.log"
  exec > >(tee -a "$LOGF") 2>&1
  echo "[entrypoint] ---- khởi động $(date -u +%FT%TZ) ----"
  echo "[entrypoint] log → $LOGF"
else
  echo "[entrypoint] CẢNH BÁO: $VOL không ghi được → không có log trên volume, chỉ còn dashboard."
fi

echo "[entrypoint] WORKER_ID=$WORKER_ID"

export COMFY_URL="${COMFY_URL:-http://127.0.0.1:8188}"

# ── Network Volume ────────────────────────────────────────────────────────────
# RunPod Serverless mount Network Volume ở /runpod-volume CỐ ĐỊNH: template Serverless KHÔNG có ô
# "Volume Mount Path" như template Pod (pod của ta dùng /workspace). Xác nhận trên
# docs.runpod.io/serverless/storage/network-volumes, 02/08/2026. Vì thế image này KHÔNG được giả
# định đường dẫn giống pod — phải tự nối vào layout mà setup/pod-volume.sh:85-91 đã tạo sẵn trên
# volume: comfy-models/ (42GB model đã tải) và hf-cache/.
#
# Không nối thì ComfyUI vẫn khởi động bình thường với models/ rỗng, rồi MỌI job fail ở bước load
# checkpoint — thất bại lộ ra ở tận cuối, sau khi đã trả tiền cho cold start và cho GPU.
if [ ! -d "$VOL/comfy-models" ]; then
  echo "[entrypoint] LỖI: không thấy $VOL/comfy-models — endpoint chưa gắn Network Volume, hoặc"
  echo "[entrypoint]   volume chưa từng chạy setup/pod-volume.sh để dựng layout."
  echo "[entrypoint]   Sửa: Serverless → Endpoint → Manage → Edit Endpoint → Advanced → Network Volumes."
  echo "[entrypoint]   Nội dung $VOL hiện có: $(ls -A "$VOL" 2>/dev/null | tr '\n' ' ' || echo '<không đọc được>')"
  exit 1
fi
# Cả hai thư mục này là cache CHỈ-ĐỌC trên thực tế (model đã tải sẵn), nên cảnh báo "ghi đồng thời
# từ nhiều worker có thể hỏng dữ liệu" của RunPod không áp dụng: worker chỉ đọc, và lần ghi duy nhất
# là tải nguyên một file mới vào đường dẫn riêng.
for pair in "models:comfy-models" "hf-cache:hf-cache"; do
  src="/app/ComfyUI/${pair%%:*}"; dst="$VOL/${pair##*:}"
  [ -d "$dst" ] || continue
  [ -L "$src" ] && [ "$(readlink "$src")" = "$dst" ] && continue
  # ComfyUI clone kèm models/ chứa toàn file placeholder ("put_checkpoints_here"), xóa được.
  rm -rf "$src"
  ln -sfn "$dst" "$src"
  echo "[entrypoint] $src → $dst"
done

cd /app/ComfyUI
echo "[entrypoint] starting ComfyUI on 127.0.0.1:8188 ..."
python -u main.py --listen 127.0.0.1 --port 8188 ${COMFY_EXTRA_ARGS:-} > /tmp/comfyui.log 2>&1 &

cd /app/worker
echo "[entrypoint] starting serverless handler ..."
exec python -u runpod/mc_handler.py
