# Tối ưu thời gian dựng pod GPU — bản ghi thiết kế

Ngày: 2026-07-31 · Trạng thái: đã triển khai, chưa test trên pod thật
Cách dùng: `docs/gpu-pod.md#network-volume` (ở gốc monorepo)

## 1. Vấn đề

Mỗi lần dựng lại pod mất 45-90+ phút, chia ba phần:

| Phần | Chi phí | Nguồn |
|---|---|---|
| Tải model **trong app** | ~33GB (bộ Wan 2.2 Animate) | `lib-feature.sh` cố ý không tải model; user tự bấm Settings → Models AI |
| Cài phần mềm | 20-35 phút | `setup-motion-transfer.sh` cài ComfyUI + torch + 10 custom node từ đầu |
| Mất database | users/jobs/workflows/workflow_runs | Postgres ở `/var/lib/postgresql` trên container disk, RunPod dựng lại mỗi Stop/Start |

`docs/gpu-pod.md` đã tự nhận diện đúng phần một: *"phần tốn thời gian là cài PyTorch CUDA + tải
model (không tránh được... vì model không nằm trong ổ đĩa gốc trừ khi bạn tự backup)"*. Network
Volume chính là cái "tự backup" đó.

## 2. Bốn giả định ban đầu đã sai

Ghi lại vì chúng định hình bản đầu của thiết kế, và bản đầu đó sai đáng kể.

| Giả định | Thực tế | Nguồn |
|---|---|---|
| Chạy `fullstack-setup.sh`, FE + BE cùng trên pod | **FE chạy local trên Mac** (`make dev` :2030), chỉ BE lên pod | `docs/gpu-pod.md` |
| Pod `git clone` repo về | `make gpu-bootstrap` **rsync** `motions-studio/` lên pod | `scripts/pod-bootstrap.sh:40` |
| Config từ `setup/templates.json` (9 khách) | Config từ `.env` gốc monorepo — **token của chính mình** | `scripts/pod-bootstrap.sh:19-33` |
| Cần thiết kế "giai đoạn 2: bake image" | **Đã có sẵn**: `MTC_PREBUILT=1` + `worker-image/Dockerfile` | `lib-feature.sh:470-510` |

Hệ quả: bỏ toàn bộ phần namespace theo tenant, mirror repo FE, và bake image — đều là thiết kế cho
một kiến trúc không tồn tại. Phần còn lại và là phần đáng giá nhất: **model + PGDATA + MinIO lên
Network Volume**.

Một ràng buộc cũng bị hiểu sai: kỷ luật "chỉ thêm file mới" **chỉ áp dụng cho `motions-studio/`**
(code upstream của ALD, còn `git pull` về). `Makefile` + `scripts/` ở gốc monorepo là code của
người vận hành — sửa thoải mái, không có conflict.

## 3. Thiết kế

### 3.1 Cái gì lên volume, cái gì không

```
/workspace/                    ← RunPod Network Volume (~100GB, region cố định)
├── .motion-volume             sentinel
├── comfy-models/              ← $COMFY_DIR/models      (symlink)  ~33GB
│   └── .manifest.tsv          total_bytes · total_files · updated
├── hf-cache/                  ← $COMFY_DIR/hf-cache    (symlink)
├── ollama-models/             ← ~/.ollama/models       (symlink)
├── pgdata/                    ← data_directory Postgres
└── minio/                     ← $ROOT/.data/minio      (symlink)

$COMFY_DIR (container disk hoặc /opt/mtc-prebuilt/ComfyUI)
└── venv/ main.py custom_nodes/    ← CỐ Ý KHÔNG lên volume
```

**Vì sao ComfyUI code + venv ở lại container disk.** Volume là network storage: đọc một model 18GB
tuần tự thì nhanh, nhưng `import torch` đọc hàng nghìn file nhỏ thì chậm rõ rệt. Mà `run_enhance`
gọi `comfy_recycle` giữa **mỗi chunk RIFE** (`worker/worker_runtime/linux.py:9611`) → ComfyUI khởi
động lại nhiều lần trong một job. Đặt venv lên network storage là phạt đúng chỗ đau nhất. Phần mềm
để `MTC_PREBUILT=1` lo, không phải volume.

**Vì sao PGDATA đổi `data_directory` chứ không symlink** `/var/lib/postgresql/<ver>/main`:
`chown -R` mặc định không đi xuyên symlink ở tham số gốc → data dir sai owner → Postgres từ chối
start và chỉ ghi lý do vào `/var/log/postgresql/`. Đúng cái bẫy mà `lib-feature.sh:430-440` đã ghi
lại từ sự cố pod RunPod ngày 27/07.

### 3.2 Thứ tự thực thi — mấu chốt của việc không sửa code upstream

`scripts/pod-bootstrap.sh` gọi `pod-volume.sh` **giữa** rsync và setup:

```
rsync motions-studio/ → pod
  ↓
./setup/pod-volume.sh            ← MỚI: cài postgres, đổi data_directory, symlink
  ↓
./setup/setup-motion-transfer.sh ← không đổi một dòng nào
  ↓
./setup/pod-volume.sh --check    ← MỚI: cổng chặn bằng số
  ↓
cloudflared fallback → dán .env sang FE
```

Ba chỗ ghép vào đúng khớp có sẵn của `lib-feature.sh`:

| Khớp | Vị trí | Hiệu quả |
|---|---|---|
| `_pg_up()` / `pg_isready` | `lib-feature.sh:422-447` | Postgres đã chạy sẵn với PGDATA trên volume → setup bỏ qua toàn bộ khối start, đi thẳng tới tạo role/db, thấy đã tồn tại → bỏ qua nốt |
| keep-list clone ComfyUI | `lib-feature.sh:574-577` | danh sách giữ lại là `models venv custom_nodes hf-cache extra_model_paths.yaml` — trùng đúng hai symlink; nó `mv` ra chỗ tạm rồi `mv` trả lại. Kể cả `rm -rf "$COMFY_DIR"` cũng chỉ xoá symlink, không chạm volume |
| `[ -x venv/bin/python ]` | `lib-feature.sh:463` | venv gãy do đổi base image → tự dựng lại |

Vì vậy **không sửa một dòng nào trong `motions-studio/setup/*` đang có**. Chỉ thêm
`setup/pod-volume.sh` (file mới) và sửa `scripts/` + `Makefile` (code của người vận hành).

### 3.3 An toàn

- **Không bao giờ tự xoá dữ liệu.** `$COMFY_DIR/models` đang là thư mục thật có dữ liệu → script
  DỪNG và yêu cầu `--adopt` một lần. `--adopt` thì rsync sang volume rồi đổi tên nguồn thành
  `.bak-<timestamp>`, không xoá.
- **Chặn volume chưa mount.** `mountpoint -q` thất bại → `die`. Đây là chặn quan trọng nhất: volume
  không attach được thì `/workspace` chỉ là thư mục rỗng trên container disk, không chặn thì setup
  chạy tiếp, tải lại 33GB vào đó, trả tiền lần nữa, rồi mất hết khi pod bị huỷ. Muốn cố tình chạy
  không volume (chỉ để test): `ALLOW_UNMOUNTED_VOLUME=1`.
- **Khoá theo major version Postgres.** Đọc `pgdata/PG_VERSION`, lệch thì `die` kèm hai lối ra
  (pin base image, hoặc `pg_upgrade`) thay vì để cluster chết âm thầm.
- **Idempotent.** Chạy lại bao nhiêu lần cũng ra cùng kết quả; `ln -sfn`, và `sed` vào
  `postgresql.conf` chỉ ghi khi nội dung thật sự đổi.

### 3.4 Kiểm chứng — chống "thành công giả"

Kẻ thù không phải lỗi ồn ào mà là: pod lên, `/health` ok, login được — nhưng `models/` là thư mục
rỗng trên container disk và app lặng lẽ tải lại 33GB. Nên `--check` đo bằng số:

| Kiểm | Cách |
|---|---|
| symlink trỏ đúng volume | `readlink -f` so với đích |
| PGDATA trên volume | `data_directory` trong `postgresql.conf` |
| model còn nguyên | **số file so với `.manifest.tsv`** |
| mức tuyệt đối | tổng GB so với `MODELS_MIN_GB` |

**Số file giảm là lỗi cứng** (exit 1), không phải cảnh báo — và manifest cố ý **không** bị ghi đè
lúc đó, để số cũ còn lại mà đối chiếu. Mức tuyệt đối chỉ advisory, vì lần đầu volume rỗng là bình
thường.

## 4. Kết quả mong đợi

| Hạng mục | Trước | `POD_VOLUME` | `POD_VOLUME` + `MTC_PREBUILT=1` |
|---|---|---|---|
| Cài phần mềm | 20-35 phút | 20-35 phút | **~1-2 phút** (symlink vào `/opt/mtc-prebuilt`) |
| Tải model (trong app) | ~33GB | **0** | 0 |
| Database qua Stop/Start | **mất** | **giữ** | giữ |
| Tổng tới lúc chạy được job | 45-90+ phút | ~20-35 phút | **~5 phút** |

Chi phí thêm: volume ~$0,07/GB/tháng → 100GB ≈ **$7/tháng**, tính cả khi không có pod nào.

## 5. Đã kiểm chứng những gì

Không có pod nên phần lõi được test trong sandbox (`/tmp`, volume giả, `COMFY_DIR` giả):

| Test | Kết quả |
|---|---|
| `--check` khi chưa nối | exit 1, liệt kê từng mục chưa nối |
| `link` khi models là thư mục thật có dữ liệu | TỪ CHỐI, dữ liệu nguồn nguyên vẹn, chỉ sang `--adopt` |
| `--adopt` | rsync sang volume · nguồn đổi tên `.bak-<ts>` · symlink đúng |
| chạy lại | idempotent, 4/4 mục báo "đã trỏ đúng volume" |
| `--check` sau khi nối | exit 0 |
| xoá model khỏi volume rồi `--check` | exit 1, "SỐ FILE GIẢM (1 → 0)", manifest giữ số cũ |

Hai bug thật do test bắt được:

1. `du -sb` là GNU-only. Thiếu nó thì `BYTES` rỗng → ngưỡng kiểm tra âm thầm luôn pass, mất đúng
   cái bảo vệ vừa dựng. Đổi sang `du -sk` × 1024.
2. `"${RSYNC_PROG[@]}"` với array RỖNG dưới `set -u` trên bash < 4.4 → `unbound variable`, chết
   ngay giữa bước dời dữ liệu. Đổi sang idiom `${arr[@]+"${arr[@]}"}`.

**Chưa test được trên pod thật:** nhánh Postgres (`pg_lsclusters`, đổi `data_directory`, dời
cluster) và nhánh `mountpoint`. Phải chạy `make gpu-bootstrap` trên một pod nháp trước khi tin.

## 6. Ngoài phạm vi

| Không làm | Lý do |
|---|---|
| Attach volume tự động khi tạo pod | `runpodctl create pod` chưa hỗ trợ network volume ổn định. Làm qua dashboard/API. |
| Backup volume | Volume là **một điểm chết duy nhất**: model tải lại được, `pgdata` và `minio` thì không. Đề xuất làm ngay sau: cron `pg_dumpall | gzip` → MinIO + một bản offsite. Không gộp vào đây. |
| Sửa `mediaJobTimeoutSec` (bug enhance 2K timeout 2700s) | Việc riêng, PR riêng. |
| Hỗ trợ đa region | Region cố định là đủ. |
