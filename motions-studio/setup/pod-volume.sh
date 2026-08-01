#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
# pod-volume.sh — Nối dữ liệu nặng của pod sang Network Volume, để dựng lại pod
# KHÔNG phải tải lại model và KHÔNG mất database.
#
# Chạy TRÊN POD, bằng root, TRƯỚC setup-motion-transfer.sh.
# scripts/pod-bootstrap.sh gọi tự động khen POD_VOLUME=/workspace được đặt.
#
#   ./setup/pod-volume.sh                # nối (idempotent — chạy mỗi lần dựng pod)
#   ./setup/pod-volume.sh --check        # chỉ kiểm tra, không sửa
#   ./setup/pod-volume.sh --adopt        # LẦN ĐẦU: dời dữ liệu đang có lên volume
#
# Bốn thứ được đưa lên volume:
#   comfy-models/  ← $COMFY_DIR/models      (nặng nhất: 33-55GB, tải trong app)
#   hf-cache/      ← $COMFY_DIR/hf-cache
#   pgdata/        ← data_directory Postgres (nếu không, DB mất mỗi Stop/Start)
#   minio/         ← $ROOT/.data/minio
#
# CỐ Ý KHÔNG đưa lên volume: ComfyUI code + venv. Volume là network storage;
# `import torch` đọc hàng nghìn file nhỏ. Mà run_enhance gọi comfy_recycle giữa
# MỖI chunk RIFE (worker_runtime/linux.py:9611) → ComfyUI restart nhiều lần
# trong một job. Phần mềm để image dựng sẵn lo (MTC_PREBUILT=1).
#
# Xem: docs/superpowers/specs/2026-07-31-toi-uu-khoi-tao-pod-design.md
# ════════════════════════════════════════════════════════════════════════════
set -uo pipefail
cd "$(dirname "$0")/.."; ROOT="$(pwd)"

MODE=link
case "${1:-}" in
  --check) MODE=check ;;
  --adopt) MODE=adopt ;;
  "")      MODE=link ;;
  *) printf 'Dùng: %s [--check|--adopt]\n' "$0" >&2; exit 2 ;;
esac

say()  { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m  ✓ %s\033[0m\n' "$*"; }
act()  { printf '\033[1;36m  → %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m  ! %s\033[0m\n' "$*"; }
die()  { printf '\n\033[1;31m  ✗ %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] && SUDO="" || SUDO="sudo"

# --info=progress2 chỉ có ở rsync ≥3.1; openrsync trên macOS không có. Dò một lần.
RSYNC_PROG=()
rsync --info=progress2 --version >/dev/null 2>&1 && RSYNC_PROG=(--info=progress2)

VOL="${POD_VOLUME:-/workspace}"
MIN_GB="${MODELS_MIN_GB:-20}"

get_kv() { grep -E "^$1=" "$ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2-; }

# COMFY_DIR: ưu tiên env → .env đã có → prebuilt → default. Phải khớp cái mà
# lib-feature.sh sẽ dùng, nếu không symlink trỏ sai chỗ và setup tải lại model.
if [ -z "${COMFY_DIR:-}" ]; then
  COMFY_DIR="$(get_kv COMFY_DIR)"
fi
if [ -z "${COMFY_DIR:-}" ]; then
  if [ "${MTC_PREBUILT:-0}" = "1" ]; then
    COMFY_DIR="${MTC_PREBUILT_DIR:-/opt/mtc-prebuilt}/ComfyUI"
  else
    COMFY_DIR="$HOME/ComfyUI"
  fi
fi

say "pod-volume ($MODE) · volume=$VOL · COMFY_DIR=$COMFY_DIR"

# ── 1. Preflight ───────────────────────────────────────────────────────────
# Chặn quan trọng nhất: volume KHÔNG mount được thì $VOL chỉ là thư mục rỗng
# trên container disk. Không chặn thì setup chạy tiếp, bạn tải lại 33GB model
# vào đó, trả tiền lần nữa, rồi mất hết khi pod bị hủy.
if ! mountpoint -q "$VOL" 2>/dev/null; then
  if [ "${ALLOW_UNMOUNTED_VOLUME:-0}" = "1" ]; then
    warn "$VOL KHÔNG phải mount point — chạy tiếp vì ALLOW_UNMOUNTED_VOLUME=1."
    warn "Dữ liệu sẽ nằm trên container disk và MẤT khi pod bị hủy."
  else
    die "$VOL không phải mount point → Network Volume chưa attach.
      Kiểm tra: mount | grep $VOL   ·   df -h $VOL
      RunPod: pod phải được tạo với network volume mount vào $VOL, CÙNG region.
      Cố tình chạy không có volume (chỉ để test): ALLOW_UNMOUNTED_VOLUME=1"
  fi
fi

MODELS="$VOL/comfy-models"
HFCACHE="$VOL/hf-cache"
OLLAMA="$VOL/ollama-models"
PGDATA="$VOL/pgdata"
MINIO="$VOL/minio"
MANIFEST="$MODELS/.manifest.tsv"
SENTINEL="$VOL/.motion-volume"

# ── 2. Layout ──────────────────────────────────────────────────────────────
if [ "$MODE" != check ]; then
  mkdir -p "$MODELS" "$HFCACHE" "$OLLAMA" "$MINIO" || die "Không tạo được layout trong $VOL (quyền?)"
  if [ ! -f "$SENTINEL" ]; then
    { echo "created=$(date -u +%FT%TZ)"; echo "host=$(hostname)"; echo "layout=1"; } > "$SENTINEL"
    act "tạo sentinel $SENTINEL"
  fi
fi
[ -f "$SENTINEL" ] || warn "Thiếu $SENTINEL — volume này có thể chưa từng được init."

# ── 3. link_dir SRC DST — biến SRC thành symlink tới DST ────────────────────
# Nếu SRC đang là thư mục THẬT có dữ liệu: --adopt thì dời dữ liệu sang DST rồi
# thay bằng symlink; chế độ link thì TỪ CHỐI (không tự xóa dữ liệu của ai).
link_dir() {
  local src="$1" dst="$2" label="$3"
  if [ -L "$src" ]; then
    local cur; cur="$(readlink -f "$src" 2>/dev/null)"
    if [ "$cur" = "$(readlink -f "$dst")" ]; then ok "$label → đã trỏ đúng volume"; return 0; fi
    if [ "$MODE" = check ]; then warn "$label → symlink trỏ SAI: $cur"; return 1; fi
    ln -sfn "$dst" "$src"; act "$label → sửa symlink về $dst"; return 0
  fi

  if [ -d "$src" ]; then
    local n; n="$(find "$src" -mindepth 1 -maxdepth 1 2>/dev/null | head -1)"
    if [ -z "$n" ]; then
      [ "$MODE" = check ] && { warn "$label → là thư mục rỗng, chưa nối"; return 1; }
      rmdir "$src" 2>/dev/null && ln -sfn "$dst" "$src" && { act "$label → nối (thư mục rỗng)"; return 0; }
      warn "$label → không nối được"; return 1
    fi
    # Có dữ liệu thật
    if [ "$MODE" = adopt ]; then
      act "$label → dời dữ liệu sang $dst (rsync, giữ nguồn)…"
      # ${arr[@]+"${arr[@]}"}: bash < 4.4 dưới `set -u` coi "${arr[@]}" của array
      # RỖNG là unbound variable và chết ngay giữa bước dời dữ liệu.
      rsync -a ${RSYNC_PROG[@]+"${RSYNC_PROG[@]}"} "$src/" "$dst/" \
        || { warn "$label → rsync lỗi, GIỮ NGUYÊN nguồn"; return 1; }
      mv "$src" "$src.bak-$(date -u +%Y%m%d%H%M%S)" || { warn "$label → không đổi tên được nguồn"; return 1; }
      ln -sfn "$dst" "$src"; act "$label → đã nối; nguồn giữ ở $src.bak-*"
      return 0
    fi
    if [ "$MODE" = check ]; then warn "$label → là thư mục THẬT có dữ liệu, chưa nối"; return 1; fi
    warn "$label ($src) là thư mục THẬT đang có dữ liệu → KHÔNG tự động xóa."
    warn "    Dời lên volume một lần bằng:  ./setup/pod-volume.sh --adopt"
    return 1
  fi

  [ "$MODE" = check ] && { warn "$label → chưa tồn tại"; return 1; }
  mkdir -p "$(dirname "$src")"
  ln -sfn "$dst" "$src"; act "$label → nối mới"
}

say "Nối thư mục"
LINK_FAIL=0
mkdir -p "$COMFY_DIR" 2>/dev/null || true
link_dir "$COMFY_DIR/models"   "$MODELS"  "ComfyUI models"  || LINK_FAIL=1
link_dir "$COMFY_DIR/hf-cache" "$HFCACHE" "HF cache"        || LINK_FAIL=1
# ── ALD-local 01/08/2026 - MinIO có thể ở trên volume theo HAI cách hợp lệ ────────────────────
# MinIO TỪ CHỐI symlink làm drive ("Drives are not directories") nên trên RunPod nó được trỏ
# thẳng vào volume bằng MINIO_DATA_DIR trong .env, không qua .data/minio. Nếu chỉ kiểm symlink
# thì cấu hình đúng lại bị báo đỏ — và đỏ-mà-không-hỏng cũng làm người ta mất niềm tin vào
# --check y như xanh-mà-hỏng. Sửa CỤC BỘ trên file upstream: làm lại sau mỗi make sync-upstream.
_MINIO_DD="$(get_kv MINIO_DATA_DIR)"
case "${_MINIO_DD:-}" in
  "$VOL"/*) ok "MinIO data → MINIO_DATA_DIR=$_MINIO_DD (trỏ thẳng volume, không cần symlink)" ;;
  *)        link_dir "$ROOT/.data/minio" "$MINIO" "MinIO data" || LINK_FAIL=1 ;;
esac
# Ollama đọc OLLAMA_MODELS từ env; symlink là lớp dự phòng khi env không tới được
# tiến trình (setup-pm2/lib-feature start bằng `nohup ollama serve`).
link_dir "$HOME/.ollama/models" "$OLLAMA" "Ollama models"   || LINK_FAIL=1

# ── 4. Postgres data_directory ─────────────────────────────────────────────
# Sửa data_directory trong postgresql.conf, KHÔNG symlink /var/lib/postgresql/<ver>/main:
# `chown -R` mặc định không đi xuyên symlink ở tham số gốc → data dir sai owner →
# Postgres từ chối start và chỉ ghi lý do vào /var/log/postgresql/. Đúng cái bẫy
# mà lib-feature.sh:430-440 đã ghi lại.
say "Postgres data_directory"
if ! command -v pg_lsclusters >/dev/null 2>&1; then
  if [ "$MODE" = check ]; then warn "chưa cài postgresql → bỏ qua"
  else
    act "cài postgresql (cần trước setup để nó thấy cluster đã chạy)…"
    $SUDO apt-get update -y -qq >/dev/null 2>&1
    $SUDO apt-get install -y -qq postgresql postgresql-contrib >/dev/null 2>&1 \
      || warn "apt cài postgresql lỗi — setup sẽ tự cài, nhưng data_directory sẽ KHÔNG nằm trên volume."
  fi
fi

# ── ALD-local 01/08/2026 - VOLUME_PGDATA=0 → để PGDATA trên container disk ────────────────────
# Không phải tuỳ chọn phong cách: RunPod mount Network Volume bằng MooseFS với user_id=0,group_id=0
# và CHẶN chown kể cả khi là root ("Operation not permitted"). Postgres từ chối khởi động nếu
# PGDATA không thuộc user postgres mode 0700, nên PGDATA KHÔNG SỐNG ĐƯỢC trên volume đó — rsync
# chết ở chown, script die, và không cài được gì. Đo trên pod thật 01/08/2026.
# Đánh đổi: DB vẫn sống qua gpu-down/gpu-up (container disk còn), MẤT khi gpu-destroy. Models và
# MinIO vẫn nằm trên volume nên vẫn không phải tải lại 33GB — đó mới là khoản tiết kiệm lớn.
# Đây là sửa đổi CỤC BỘ trên file upstream: chạy lại sau mỗi `make sync-upstream`.
# Đọc từ .env khi caller không truyền, để --check / --adopt / chạy tay đều thấy cùng quyết định
# như lúc bootstrap. Không có dòng này thì `make gpu-smoke` báo đỏ một cấu hình cố ý.
[ -z "${VOLUME_PGDATA:-}" ] && VOLUME_PGDATA="$(get_kv VOLUME_PGDATA)"
if [ "${VOLUME_PGDATA:-1}" = "0" ]; then
  warn "VOLUME_PGDATA=0 → PGDATA ở lại container disk (volume MooseFS không cho chown)."
  warn "  DB sống qua gpu-down/gpu-up, MẤT khi gpu-destroy. Models + MinIO vẫn trên volume."
elif command -v pg_lsclusters >/dev/null 2>&1; then
  PGVER="$(pg_lsclusters -h 2>/dev/null | awk 'NR==1{print $1}')"
  PGCLU="$(pg_lsclusters -h 2>/dev/null | awk 'NR==1{print $2}')"
  PGCONF="/etc/postgresql/$PGVER/${PGCLU:-main}/postgresql.conf"

  if [ -z "$PGVER" ] || [ ! -f "$PGCONF" ]; then
    warn "không thấy cluster/postgresql.conf → bỏ qua bước PGDATA."
  else
    CUR_DD="$($SUDO grep -oE "^[[:space:]]*data_directory[[:space:]]*=[[:space:]]*'[^']*'" "$PGCONF" 2>/dev/null | sed "s/.*'\(.*\)'/\1/")"

    # Khóa theo MAJOR VERSION: pgdata tạo bởi PG 16 thì PG 17 không mở được.
    if [ -f "$PGDATA/PG_VERSION" ]; then
      HAVE="$(cat "$PGDATA/PG_VERSION" 2>/dev/null | tr -d ' \n')"
      if [ "$HAVE" != "$PGVER" ]; then
        die "PGDATA trên volume là Postgres $HAVE, pod này có Postgres $PGVER.
      Cluster sẽ KHÔNG start. Hai lối ra:
        • dùng lại base image có Postgres $HAVE (khuyến nghị — pin image)
        • hoặc pg_upgrade dữ liệu trên volume lên $PGVER"
      fi
      ok "PG_VERSION khớp ($PGVER)"
    fi

    if [ "$CUR_DD" = "$PGDATA" ]; then
      ok "data_directory đã trỏ $PGDATA"
    elif [ "$MODE" = check ]; then
      warn "data_directory đang là ${CUR_DD:-?}, chưa phải $PGDATA"
    else
      # Chưa có pgdata trên volume → nhận cluster hiện tại (initdb của apt) sang
      if [ ! -f "$PGDATA/PG_VERSION" ]; then
        if [ -n "$CUR_DD" ] && [ -f "$CUR_DD/PG_VERSION" ]; then
          act "dời cluster $CUR_DD → $PGDATA…"
          $SUDO pg_ctlcluster "$PGVER" "${PGCLU:-main}" stop >/dev/null 2>&1 || true
          for _i in $(seq 1 30); do [ -f "$CUR_DD/postmaster.pid" ] || break; sleep 1; done
          $SUDO rsync -a "$CUR_DD/" "$PGDATA/" || die "rsync PGDATA lỗi — cluster nguồn còn nguyên, không mất gì."
        else
          warn "không thấy cluster nguồn → PGDATA trên volume sẽ do Postgres tự initdb."
        fi
      fi
      $SUDO chown -R postgres:postgres "$PGDATA" 2>/dev/null || true
      $SUDO chmod 0700 "$PGDATA" 2>/dev/null || true
      $SUDO python3 - "$PGCONF" "$PGDATA" <<'PY'
import re, sys
conf, pgdata = sys.argv[1], sys.argv[2]
src = open(conf, encoding="utf-8").read()
line = "data_directory = '%s'" % pgdata
new, n = re.subn(r"(?m)^[#\s]*data_directory\s*=\s*'[^']*'.*$", line, src, count=1)
if n == 0:
    new = src.rstrip("\n") + "\n\n# pod-volume.sh — PGDATA trên Network Volume\n" + line + "\n"
if new != src:
    open(conf, "w", encoding="utf-8").write(new)
PY
      act "data_directory → $PGDATA"
      $SUDO pg_ctlcluster "$PGVER" "${PGCLU:-main}" start >/dev/null 2>&1 || true
      for _i in $(seq 1 30); do pg_isready -h /var/run/postgresql >/dev/null 2>&1 && break; sleep 1; done
      if pg_isready -h /var/run/postgresql >/dev/null 2>&1; then
        ok "Postgres đang chạy với PGDATA trên volume"
      else
        warn "Postgres chưa lên — xem /var/log/postgresql/. Cluster nguồn vẫn còn ở ${CUR_DD:-?}."
      fi
    fi
  fi
fi

# ── 5. Manifest model — bằng chứng chống tải lại ───────────────────────────
# Kẻ thù là THÀNH CÔNG GIẢ: box lên xanh, /health ok, nhưng models/ là thư mục
# rỗng nên app im lặng tải lại 33GB. Đo bằng số, không nhìn "chạy được".
say "Manifest model"
# du -sk (KB) chứ không -sb: -sb là GNU-only, thiếu nó thì BYTES rỗng và ngưỡng
# kiểm tra âm thầm luôn pass — mất đúng cái bảo vệ mình vừa dựng.
_models_bytes() { du -sk "$MODELS" 2>/dev/null | awk '{print $1 * 1024}'; }
_models_files() { find "$MODELS" -type f ! -name '.manifest.tsv' 2>/dev/null | wc -l | tr -d ' '; }

BYTES="$(_models_bytes)"; FILES="$(_models_files)"
GB=$(( ${BYTES:-0} / 1000000000 ))
printf '  %s file · %s GB\n' "${FILES:-0}" "$GB"

if [ -f "$MANIFEST" ]; then
  OLD_B="$(awk -F'\t' '$1=="total_bytes"{print $2}' "$MANIFEST" | tail -1)"
  OLD_F="$(awk -F'\t' '$1=="total_files"{print $2}' "$MANIFEST" | tail -1)"
  printf '  manifest trước: %s file · %s GB\n' "${OLD_F:-?}" "$(( ${OLD_B:-0} / 1000000000 ))"
  # HỒI QUY là lỗi cứng, không phải cảnh báo. Mức tuyệt đối thì chỉ advisory: lần
  # đầu volume rỗng là bình thường. Nhưng số file GIẢM thì luôn nghĩa là mất dữ
  # liệu — và đó chính là ca "thành công giả" mà --check tồn tại để bắt.
  if [ "${OLD_F:-0}" -gt "${FILES:-0}" ] 2>/dev/null; then
    warn "SỐ FILE GIẢM (${OLD_F} → ${FILES}) — có model bị mất khỏi volume."
    warn "    Manifest KHÔNG bị ghi đè để bạn còn số cũ mà đối chiếu."
    MODELS_REGRESSED=1
  fi
fi
MODELS_REGRESSED="${MODELS_REGRESSED:-0}"

if [ "$MODE" != check ] && [ "$MODELS_REGRESSED" = 0 ]; then
  { printf 'total_bytes\t%s\n' "${BYTES:-0}"
    printf 'total_files\t%s\n' "${FILES:-0}"
    printf 'updated\t%s\n' "$(date -u +%FT%TZ)"
  } > "$MANIFEST"
fi

if [ "$GB" -lt "$MIN_GB" ]; then
  warn "Chỉ $GB GB model (< ngưỡng ${MIN_GB} GB)."
  warn "    Lần đầu thì bình thường — tải qua Settings → Models AI, lần sau sẽ có sẵn."
  warn "    Nếu ĐÃ tải rồi mà vẫn thấy dòng này: symlink đang trỏ vào thư mục rỗng."
else
  ok "Model có sẵn trên volume — KHÔNG cần tải lại"
fi

# ── 6. Ghi .env để lib-feature.sh dùng đúng đường dẫn ──────────────────────
if [ "$MODE" != check ] && [ -f "$ROOT/.env" ]; then
  set_kv_local() {
    local k="$1" v="$2"
    if grep -qE "^$k=" "$ROOT/.env"; then
      python3 - "$ROOT/.env" "$k" "$v" <<'PY'
import re, sys
p, k, v = sys.argv[1], sys.argv[2], sys.argv[3]
s = open(p, encoding="utf-8").read()
new = re.sub(r"(?m)^%s=.*$" % re.escape(k), "%s=%s" % (k, v), s, count=1)
if new != s: open(p, "w", encoding="utf-8").write(new)
PY
    else
      printf '%s=%s\n' "$k" "$v" >> "$ROOT/.env"
    fi
  }
  set_kv_local COMFY_DIR "$COMFY_DIR"
  set_kv_local COMFY_MODELS_DIR "$COMFY_DIR/models"
  set_kv_local OLLAMA_MODELS "$OLLAMA"
  ok ".env: COMFY_DIR · COMFY_MODELS_DIR · OLLAMA_MODELS"
fi

# ── 7. Kết luận ────────────────────────────────────────────────────────────
printf '\n'
if [ "$MODE" = check ]; then
  [ "$MODELS_REGRESSED" = 1 ] && die "--check: MODEL BỊ MẤT khỏi volume (số file giảm so với manifest)."
  [ "$LINK_FAIL" = 0 ] && { ok "--check: mọi thứ đã nối đúng volume."; exit 0; }
  die "--check: còn thứ chưa nối (xem cảnh báo trên)."
fi
if [ "$LINK_FAIL" != 0 ]; then
  warn "Có thư mục chưa nối được. Nếu vì 'thư mục THẬT đang có dữ liệu' → chạy: ./setup/pod-volume.sh --adopt"
  exit 1
fi
ok "Xong. Chạy setup-motion-transfer.sh tiếp được rồi."
