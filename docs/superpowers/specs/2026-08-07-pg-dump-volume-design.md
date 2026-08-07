# Sao lưu DB sang Network Volume bằng `pg_dump` — Design

**Ngày:** 07/08/2026 · **Trạng thái:** thiết kế, chưa triển khai.

## Mục tiêu

Làm cho database sống sót qua `make gpu-destroy`.

Hiện tại PGDATA nằm trên **container disk**, nên `gpu-destroy` xoá luôn database: toàn bộ `jobs`,
`users`, `workflows`, `api_keys`, `social_accounts`. Đó là lý do `POD_MAX_HOURS` phải dùng
`--stop-after` chứ không `--terminate-after` (`.env.example:24-26`) — lưới an toàn không được tự
phá dữ liệu. Cái giá của ràng buộc đó là **pod dừng vẫn tính tiền container disk**
([gpu-pod.md#costs](../../gpu-pod.md#costs)), và pod nằm dừng ~95% thời gian (nhịp dùng thật 1,19
giờ/ngày).

Spec này gỡ ràng buộc đó: khi DB dựng lại được từ volume, `gpu-destroy` thành thao tác bình thường
thay vì thao tác phá dữ liệu.

**Ngoài phạm vi:** hạ `DISK` xuống mức hợp lý. Đó là bài toán riêng, gốc rễ khác hẳn (ghi tạm của
job, không phải PGDATA — xem §Bài toán kế tiếp), và sẽ có spec riêng. Spec này là điều kiện cần cho
nó, vì bước thu hoạch của bài đó — dựng pod mới với `DISK` nhỏ — chính là thao tác làm mất DB hôm
nay.

## Vì sao không phải "đưa PGDATA lên volume"

Đường đó đã thử và đã đóng, bằng đo thật trên pod ngày 01/08/2026 (commit `d2a9ffc`):

- RunPod mount Network Volume bằng **MooseFS với `user_id=0,group_id=0` và chặn `chown` kể cả khi
  là root** ("Operation not permitted").
- Postgres **từ chối khởi động** nếu PGDATA không thuộc user `postgres` mode `0700`.
- Cách vòng tiêu chuẩn — file ext4 loopback đặt trên volume — **bất khả thi: container không có
  `/dev/loop*`**.

Mọi hướng cùng họ (bindfs / FUSE remap uid) nhiều khả năng chết vì cùng một lý do: container không
được cấp device đặc biệt. Spec này **không đào lại** hướng đó.

Kết luận: `VOLUME_PGDATA=0` giữ nguyên. PGDATA ở lại container disk — nơi `chown` chạy được — còn
tính bền đến từ một bản sao **logic** trên volume. Không đụng gì tới thứ MooseFS đang cấm, vì ghi
file thường lên volume thì hoàn toàn bình thường; chỉ `chown` bị chặn.

## Vì sao `pg_dump` chứ không WAL archiving

| | `pg_dump` (chọn) | WAL archiving |
|---|---|---|
| RPO | mất tối đa từ lần dump cuối | gần 0 |
| Bộ phận động | 1 script | `archive_command`, base backup, prune, promote |
| Khoá major version | **không** — dump là bản sao logic | có: base backup PG 16 cần PG 16 |
| Rủi ro làm đầy container disk | không | **có**: `archive_command` hỏng thì Postgres giữ WAL lại trong `PGDATA/pg_wal` và thử lại mãi |

Dòng cuối là dòng quyết định. WAL archiving tạo ra một đường mà đĩa nhỏ bị lấp đầy rồi Postgres
dừng hẳn — tức nó **đặt sàn cho `DISK`**, đúng thứ bài toán kế tiếp muốn hạ. `pg_dump` không có
đường đó.

Dòng "khoá major version" cũng đáng giá: cái bẫy `PG_VERSION` đang canh ở `pod-volume.sh:202-212`
(pgdata tạo bởi PG 16 thì PG 17 không mở được) **không áp dụng** cho dump logic. Đổi base image lên
Postgres mới hơn vẫn khôi phục được.

**Dữ liệu hợp với cách này:** DB là metadata thuần — `jobs`, `users`, `user_sessions`, `workflows`,
`storage_files` (chỉ trỏ vào MinIO), `api_keys`, `social_accounts`. Không có cột `bytea`. Media nằm
trong MinIO **trên volume**, nên khi mất vài phút metadata cuối thì file vẫn còn nguyên.

## Kiến trúc

### Trạng thái trên volume

```
$POD_VOLUME/pg/
  dumps/motion-<YYYYmmdd-HHMMSS>.sql.gz    bản dump
  dumps/motion-<YYYYmmdd-HHMMSS>.meta      số dòng từng bảng + version + kích thước
  latest -> dumps/motion-<YYYYmmdd-HHMMSS>.sql.gz
```

File `.meta` không phải trang trí. Một dump rỗng hoặc cụt vẫn qua được `gzip -t` và vẫn có kích
thước khác 0; chỉ khi so số dòng mới phân biệt được "dump chạy xong" với "dump đúng". Đây là loại
**thành công giả** mà cả repo đang phòng — cùng họ với cổng chặn manifest model ở
`pod-volume.sh` §5.

Thư mục `700`, file `600`: dump chứa `api_keys`, `user_sessions.token_hash` và token của
`social_accounts`.

### Khi nào dump

**Không có dump định kỳ.** Đường duy nhất làm mất DB là đường tự điều khiển: `gpu-down` và lưới
`POD_MAX_HOURS` đều là **stop** (container disk còn nguyên, DB sống), chỉ `gpu-destroy` mới xoá. Và
`pod-provision.sh` không dùng cờ spot/interruptible nào, nên không có chuyện RunPod thu hồi pod
giữa chừng. Một tiến trình nền chạy suốt phiên để phòng một sự kiện không tồn tại là thuế vô ích.

| Chỗ | Vai trò |
|---|---|
| `make gpu-down` | **Điểm dump chính** — thời điểm cuối cùng còn ssh được vào pod |
| `make gpu-destroy` | Cố dump nếu pod đang chạy; pod đã dừng thì bỏ qua |
| `make gpu-db-dump` | Thủ công, khi muốn chốt một mốc |

`gpu-down` là điểm chính vì sau đó pod im lặng cho tới khi bật lại: `gpu-destroy` chạy **trên máy
dev**, còn dump phải chạy **trên pod**, và volume chỉ mount được qua pod. Pod đã dừng thì không ssh
được, không dump được, và cũng không đọc được volume để biết có dump hay chưa.

### Khi nào khôi phục

Trong `feature_main()` (`setup/lib-feature.sh:868-894`), **ngay sau `phase_postgres`**:

```
phase_postgres        role + database đã có, chưa ai tạo schema
   │
   ├─ pod-pgdump.sh --restore
   │     DB trống + có dump  → nạp, IN TO tuổi bản dump
   │     DB đã có dữ liệu    → không làm gì
   │     chưa có dump nào    → bỏ qua, phiên đầu
   │
phase_pm2 → api khởi động → migrate.js chạy db/init/*.sql
```

Vị trí này bị ràng buộc hai đầu, không phải chọn cho gọn:

- **Sau `phase_postgres`**: dump chứa `ALTER TABLE ... OWNER TO motion` và `GRANT`, nên role
  `motion` và database phải tồn tại trước (`lib-feature.sh:428-430, 479`).
- **Trước khi có gì tạo schema**: `api/src/migrate.js` chạy lúc api khởi động và tạo bảng từ
  `db/init/*.sql`. Nếu restore chạy sau đó, nạp dump có `CREATE TABLE` vào DB đã có bảng rỗng sẽ
  lỗi trùng.

Migration chạy sau restore là vô hại vì `db/init/*.sql` đều idempotent (`CREATE TABLE IF NOT
EXISTS` / `ON CONFLICT`) — chính `migrate.js` được viết ra để chạy được trên volume cũ.

## Thành phần

### Mới: `motions-studio/setup/pod-pgdump.sh`

Chạy **trên pod**. Là nơi duy nhất biết bố cục thư mục dump; mọi caller chỉ truyền chế độ và đọc
exit code.

| Chế độ | Làm gì |
|---|---|
| `--dump` | dump → gzip → ghi `.meta` → đổi `latest` → prune giữ `PG_DUMP_KEEP` bản |
| `--restore` | nạp `latest` **chỉ khi DB trống**; in tuổi bản dump |

"DB trống" định nghĩa là **không có bảng nào trong schema `public`**, không phải "có bảng nhưng
0 dòng". Phân biệt này quan trọng: nếu vì lý do nào đó `migrate.js` đã chạy trước, DB sẽ có đủ bảng
rỗng — lúc đó nạp dump có `CREATE TABLE` vào sẽ lỗi trùng, và bỏ qua là hành vi đúng chứ không phải
bỏ sót. Kiểm bằng `SELECT count(*) FROM pg_tables WHERE schemaname='public'`.
| `--check` | báo cáo tuổi + số dòng, **không sửa gì** |
| `--verify` | nạp thử vào DB tạm, so số dòng với `.meta`, xoá DB tạm |

**Giao diện:** đọc `POD_VOLUME` và `POSTGRES_USER/PASSWORD/DB/PORT` theo đúng khuôn đang dùng ở
`pod-volume.sh:188` — ưu tiên biến môi trường, thiếu thì `get_kv` từ `.env`. Trả `0` khi thành
công, khác `0` kèm lý do khi hỏng.

**Không làm:** không đụng PGDATA, không quản mount volume, không biết gì về MinIO hay models. Một
mục đích.

Đây là file mới chứ không phải thêm mục vào `pod-volume.sh`, vì file đó đã dài ~330 dòng và làm 5
việc không liên quan nhau (models, hf-cache, MinIO, Ollama, PGDATA, manifest). Thêm việc thứ 6 vào
đó thì không ai kiểm riêng được phần backup nữa.

### Sửa

| File | Sửa gì | Vì sao |
|---|---|---|
| `setup/pod-volume.sh` | thêm `set_kv_local POD_VOLUME "$VOL"` | **`POD_VOLUME` hiện không tới được `lib-feature.sh`**: nó không nằm trong danh sách env truyền qua ssh (`pod-bootstrap.sh:160-167`) và cũng không được ghi vào `.env` trên pod. `pod-volume.sh:324-326` đã ghi `COMFY_DIR`/`COMFY_MODELS_DIR`/`OLLAMA_MODELS` theo đúng cách này — chỉ thiếu mỗi key này |
| `setup/lib-feature.sh` | gọi `--restore` sau `phase_postgres` trong `feature_main()`, sau cổng `[ -n "$POD_VOLUME" ]` | profile không có volume phải đi qua như không có gì |
| `Makefile` | thêm `gpu-db-dump`, `gpu-db-check`; sửa `gpu-down` (dòng 64-70) và `gpu-destroy` (dòng 76-101) | ba điểm dump |
| `scripts/pod-smoke.sh` | thêm một lớp: có `latest`, tuổi bao nhiêu, `--verify` xanh không | đặt ở nhóm lớp rẻ, không cần GPU |
| `.env.example` | `PG_DUMP_KEEP=20` | số bản giữ lại |
| `docs/gpu-pod.md` | mục mới, và cập nhật mục `#costs` | `gpu-destroy` không còn là thao tác phá dữ liệu |

## Xử lý lỗi

Nguyên tắc: **script trả exit code thật, Makefile không dừng vì nó.** Không có cổng chặn nào. Cách
này giữ thông tin cho `gpu-smoke` và cho người đọc mà không cản đường ai.

| Tình huống | Xử lý |
|---|---|
| `--dump`: volume không mount / hết chỗ / `pg_dump` lỗi | in lý do, exit ≠ 0. `gpu-down` và `gpu-destroy` cảnh báo rồi **vẫn chạy tiếp** |
| `--restore`: DB đã có dữ liệu | **không phải lỗi** — bỏ qua, in rõ "DB đã có dữ liệu, không nạp đè" |
| `--restore`: chưa có dump nào | bỏ qua, in rõ đây là phiên đầu |
| `--restore`: file dump hỏng | cảnh báo, không nạp một phần |
| Hai lần dump chồng nhau | ghi ra file tạm rồi `mv` trong cùng filesystem (nguyên tử) — `latest` không bao giờ trỏ file dở |

**`gpu-down` không được chặn vì dump hỏng.** Pod GPU đang chạy là $0,99/giờ, mà `gpu-down` không hề
làm mất DB (container disk còn nguyên). Chặn việc dừng để bảo vệ một bản backup là đốt tiền thật để
giữ thứ chưa bị đe doạ.

**Chỗ duy nhất phải thật cẩn thận là `--restore`**, vì nạp nửa chừng tệ hơn không nạp: bạn được một
DB có vài bảng, app chạy lên bình thường, và không biết mình đang thiếu gì. Hai cờ
`psql --single-transaction -v ON_ERROR_STOP=1` mua hai thứ khác nhau, cần cả hai: `--single-transaction`
cho tính nguyên tử — lỗi ở bất kỳ statement nào thì Postgres tự biến COMMIT cuối thành ROLLBACK,
rollback sạch về DB trống, tức quay về đúng trạng thái "chưa nạp" chứ không phải một trạng thái lai.
`ON_ERROR_STOP=1` cho exit code trung thực — thiếu nó, psql vẫn thoát 0 sau một lần nạp đã hỏng và
đã rollback, khiến script báo "khôi phục xong" trên một DB thật ra vẫn trống: đúng loại thành công giả.

**Tuổi bản dump luôn in ra lúc restore**, cỡ chữ ngang các dòng `✓` khác. Không từ chối vì cũ —
nhưng nạp âm thầm một bản ba tuần tuổi rồi để người dùng tưởng là dữ liệu hôm qua thì đúng là thành
công giả.

**Prune** chỉ chạy sau khi bản mới đã ghi xong và `.meta` đã khớp, và không bao giờ xoá bản cuối
cùng còn lại — kể cả khi `PG_DUMP_KEEP=1` và bản mới lỗi.

## Kiểm chứng

**`--verify` là bằng chứng, phần còn lại chỉ là dấu vết.** Nó nạp `latest` vào một DB tạm, so số
dòng từng bảng với `.meta`, rồi xoá DB tạm. Không đụng DB thật. Nó trả lời được câu "backup này có
nạp lại được không" — câu mà một hệ thống backup không có diễn tập chỉ trả lời được lần đầu vào
đúng lúc tệ nhất.

**Lớp mới trong `make gpu-smoke`:** có `latest` không, tuổi bao nhiêu, `--verify` xanh không.

**Diễn tập thật một lần, lúc nghiệm thu:** dump → `gpu-destroy` → dựng pod mới → xác nhận dữ liệu
quay về. Tốn một vòng pod (~5 phút theo số đo prebuilt: 284 giây từ pull tới bootstrap xong). Đây
là lần duy nhất chứng minh được toàn bộ đường dây, và số đo sẽ ghi vào `docs/gpu-pod.md` như các
mục khác.

## Rủi ro còn lại

- **RPO = từ lần `gpu-down` gần nhất.** Nếu người dùng xoá pod từ web console mà không qua Makefile,
  phần metadata sinh ra sau lần dump cuối sẽ mất. Media không mất (MinIO trên volume). Đây là lựa
  chọn có ý thức: không dump định kỳ.
- **Volume đầy làm dump hỏng im lặng ở lần sau.** Volume 100GB đang giữ ~42GB model; dump metadata
  cỡ vài MB nên nguy cơ thấp, nhưng `--check` phải báo được để `gpu-smoke` bắt.
- **Dump chứa bí mật.** Nằm trên volume riêng của tài khoản RunPod, quyền `600`. Không rời khỏi
  volume, nhưng ai truy cập được volume thì đọc được token.

## Bài toán kế tiếp (spec riêng)

Hạ `DISK` từ 100-120 GB xuống mức có căn cứ. Lý do ghi trong code hiện tại đã lỗi thời —
`pod-provision.sh:14` vẫn viết *"~33GB model group + OS"*, con số của thời chưa có Network Volume.
Nghi vấn chính, cần đo trên pod thật trước khi thiết kế: `worker_runtime/linux.py` có **24 lần
`tempfile.mkdtemp()` nhưng chỉ 1 `shutil.rmtree`** và 1 `TemporaryDirectory`, không có janitor,
không có `rm -rf /tmp` lúc boot, không đặt `TMPDIR` ở đâu — nghĩa là thư mục tạm của gần như mọi
job có thể đang tích luỹ vĩnh viễn. Nếu đúng, `DISK=100` đang che một con rò rỉ chứ không phản ánh
nhu cầu thật.
