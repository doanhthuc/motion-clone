#!/usr/bin/env bash
# gpu-fallback-test.sh — test vòng xoay GPU_FALLBACK của pod-provision.sh trên MÁY DEV, KHÔNG tốn tiền.
#
#   bash scripts/tests/gpu-fallback-test.sh
#
# `runpodctl` được giả hoàn toàn, và script chạy trong bản sao repo ở /tmp nên KHÔNG ghi vào .env thật
# (pod-provision.sh gọi env_set GPU_INSTANCE_ID khi thuê được).
#
# Vì sao đáng test: nhánh này chỉ chạy khi EU-RO-1 hết 5090 — không gọi ra được theo ý muốn, và lúc nó
# chạy thật thì bạn đang cần dựng pod để làm việc, không phải để debug. Hai ca ÂM quan trọng hơn ca
# dương: (a) còn máy thì KHÔNG được xoay, (b) lỗi create không-phải-hết-máy phải nổi lên chứ không
# được che bằng cách âm thầm đổi GPU. Xem docs/gpu-pod.md#gpu-4090.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
PASS=0; FAIL=0

SANDBOX="$(mktemp -d)"; BIN="$(mktemp -d)"
trap 'rm -rf "$SANDBOX" "$BIN"' EXIT

mkdir -p "$SANDBOX/scripts"
cp "$ROOT/scripts/pod-provision.sh" "$SANDBOX/scripts/"
[ -f "$ROOT/scripts/lib-deploy-shape.sh" ] && cp "$ROOT/scripts/lib-deploy-shape.sh" "$SANDBOX/scripts/"

# .env tối thiểu để pod-provision.sh đi được tới bước create (không lấy .env thật — có secrets).
cat > "$SANDBOX/.env" <<'ENVEOF'
GPU_PROVIDER=runpod
GPU=NVIDIA GeForce RTX 5090
DISK=100
POD_VOLUME=/workspace
POD_VOLUME_ID=wfe86wzkpm
MIN_CUDA_VERSION=13.0
MTC_PREBUILT=1
POD_MAX_HOURS=8
COMPUTE_TYPE=gpu
SETUP_PROFILE=full
WORKER_SOURCE=local
GPU_INSTANCE_ID=
GPU_SSH_HOST=
GPU_SSH_PORT=
POD_IMAGE=ghcr.io/doanhthuc/motion-prebuilt:test
ENVEOF

# runpodctl giả. $STUB_OOS = danh sách chuỗi GPU bị coi là HẾT MÁY. $STUB_CREATE_ERR = lỗi create khác.
write_stub() {
  cat > "$BIN/runpodctl" <<'STUB'
#!/usr/bin/env bash
ARGS="$*"
case "$ARGS" in
  *--help*)                       echo "--network-volume-id --volume-mount-path --data-center-ids --min-cuda-version"; exit 0;;
  "user -o json"*)                echo '{"id":"u1"}'; exit 0;;
  "network-volume list -o json"*) echo '[{"id":"wfe86wzkpm","name":"motion","dataCenterId":"EU-RO-1","size":100}]'; exit 0;;
  "network-volume get"*)          echo '{"id":"wfe86wzkpm","dataCenterId":"EU-RO-1"}'; exit 0;;
  "pod create"*)
    if [ -n "${STUB_CREATE_ERR:-}" ]; then echo "$STUB_CREATE_ERR" >&2; exit 1; fi
    for g in ${STUB_OOS:-}; do
      case "$ARGS" in *"$g"*) echo "there are no instances available with the requested specifications" >&2; exit 1;; esac
    done
    echo '{"id":"stubpod-abc123"}'; exit 0;;
esac
echo "stub runpodctl: chưa xử lý '$ARGS'" >&2; exit 3
STUB
  chmod +x "$BIN/runpodctl"
}
write_stub

run_provision() {  # $1 = GPU hết máy, $2 = GPU_FALLBACK, $3 = lỗi create khác
  ( cd "$SANDBOX" || exit 9
    STUB_OOS="$1" GPU_FALLBACK="$2" STUB_CREATE_ERR="${3:-}" CONFIRM=yes \
      PATH="$BIN:$PATH" bash scripts/pod-provision.sh 2>&1 )
}

has()    { if echo "$2" | grep -qF -- "$3"; then printf 'PASS  %s\n' "$1"; PASS=$((PASS+1))
           else printf 'FAIL  %s — không thấy %s\n' "$1" "'$3'"; echo "$2" | tail -8 | sed 's/^/      /'; FAIL=$((FAIL+1)); fi; }
hasnt()  { if echo "$2" | grep -qF -- "$3"; then printf 'FAIL  %s — KHÔNG được có %s\n' "$1" "'$3'"; FAIL=$((FAIL+1))
           else printf 'PASS  %s\n' "$1"; PASS=$((PASS+1)); fi; }

echo "── 1 · Còn 5090 → thuê nó, KHÔNG xoay ──"
O="$(run_provision '' 'NVIDIA GeForce RTX 4090' '')"
has   "thuê được"                "$O" "rented"
hasnt "không xoay oan"           "$O" "thử dự phòng"

echo "── 2 · Hết 5090 → xoay sang 4090, cảnh báo đủ ──"
O="$(run_provision '5090' 'NVIDIA GeForce RTX 4090' '')"
has   "có thử dự phòng"          "$O" "thử dự phòng"
has   "thuê được card dự phòng"  "$O" "rented"
has   "nói rõ đang dùng dự phòng" "$O" "DỰ PHÒNG"
has   "nhắc ép offload"          "$O" "MOTION_VRAM_MAX_FRAMES=0"

echo "── 3 · Hết cả hai → chết có thông báo, không im lặng ──"
O="$(run_provision '5090 4090' 'NVIDIA GeForce RTX 4090' '')"
has   "báo hết máy mọi GPU"      "$O" "hết máy ở MỌI GPU"

echo "── 4 · GPU_FALLBACK trống → không thử gì khác ──"
O="$(run_provision '5090' '' '')"
has   "vẫn chết đúng"            "$O" "hết máy ở MỌI GPU"
hasnt "không lén thử 4090"       "$O" "4090"

echo "── 5 · Lỗi create KHÁC → phải nổi lên, không che bằng cách xoay ──"
O="$(run_provision '' 'NVIDIA GeForce RTX 4090' 'invalid image name: unauthorized')"
has   "báo lỗi thật"             "$O" "pod create failed"
hasnt "không xoay khi lỗi khác"  "$O" "thử dự phòng"

echo
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
