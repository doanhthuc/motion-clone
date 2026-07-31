# Tối ưu thời gian khởi tạo pod GPU — Giai đoạn 1: Network Volume

Ngày: 2026-07-31
Trạng thái: chờ review (bản 2 — thêm bối cảnh fork + xử lý secrets)

## 1. Vấn đề

Mỗi lần dựng lại box trên RunPod, quy trình hiện tại mất **45-90+ phút**, chia làm ba phần:

| Phần | Chi phí | Ghi chú |
|---|---|---|
| Tải model ComfyUI | 45-55 GB cho box motion (catalog đầy đủ: **245 GB / 34 file**) | Chiếm phần lớn thời gian, gần như không có rủi ro kỹ thuật |
| Cài phần mềm | 20-35 phút | apt, Node 20, torch cu130, 10 custom node, npm build FE |
| Nhập config | vài phút gõ tay | Có sẵn cơ chế, nhưng **dữ liệu mẫu hiện tại không dùng được** — xem §3.2 |

Kèm hai vấn đề dữ liệu chưa được nhận diện:

- **Database mất mỗi lần Stop/Start pod.** Postgres đang ở `/var/lib/postgresql` trên container disk. RunPod dựng lại container disk từ image mỗi lần Start, chỉ `/workspace` (volume) là còn. `SUPER_ADMIN` được seed lại nên box trông vẫn bình thường, nhưng users, jobs, workflows, workflow_runs thì mất.
- **Cơ chế template thì có, dữ liệu thì không phải của mình.** `fullstack-setup.sh:135-221` hỗ trợ nạp mẫu cấu hình (bỏ hết prompt), nhưng `setup/templates.json` hiện tại chứa credentials của ALD-Project, không phải của người vận hành fork này. Chi tiết và cách xử lý ở §3.2.

## 2. Mục tiêu

- Loại bỏ hoàn toàn việc tải lại model.
- Giữ được database và MinIO qua các lần dựng lại.
- Không gõ tay config, bằng template **của mình**.
- Toàn bộ customization sống được qua `git pull` từ upstream.

**Phi mục tiêu (giai đoạn 1):** không đưa thời gian về 2 phút. Sau giai đoạn 1 còn khoảng **16-28 phút** cho phần cài phần mềm; đó là việc của giai đoạn 2.

## 3. Ràng buộc đã chốt

### 3.1 Vận hành

| Ràng buộc | Giá trị | Hệ quả thiết kế |
|---|---|---|
| Kịch bản lặp lại | Cả ba: Stop/Start cùng pod · dựng box khách mới · đổi pod/GPU | Phải dùng Network Volume (sống qua việc hủy pod), không dùng pod volume disk |
| Số box chạy song song | 1 | Network Volume khả thi (RunPod chỉ cho 1 volume gắn 1 pod tại một thời điểm) |
| Region | Cố định 1 region | Không cần mirror model đa region |
| Số tenant | 9 khách, luân phiên trên cùng volume | Phải tách namespace theo tenant trong volume |

### 3.2 Fork và secrets

Repo này là **fork của `ALD-Project/motions-studio`**, sẽ được push sang một repo **public** mới, và vẫn tiếp tục `git pull` bản mới từ upstream. Ba hệ quả:

**(a) Chỉ được thêm file mới, gần như không sửa file đang có.** Mỗi dòng sửa trong `setup-pm2.sh` hoặc `fullstack-setup.sh` là một điểm conflict vĩnh viễn ở mọi lần sync upstream. Thiết kế này sửa **đúng một dòng** (§3.2c) và có script tự tái áp.

**(b) Năm chỗ đang track chứa credentials hoặc PII của bên thứ ba.** Quét bằng pattern trên toàn bộ file được git track, xếp theo mức nghiêm trọng:

| # | Vị trí | Nội dung | Vì sao nghiêm trọng |
|---|---|---|---|
| 1 | `setup/setup-pm2.sh:34` và `setup/lib-feature.sh:36` | `DEFAULT_API_KEY="mk_508ae0…"` | **Nặng nhất.** Đây là secret **CHIA SẺ**: `setup-pm2.sh:495` gán nó làm `API_KEY` cho mọi bản cài mới. Ai đọc được thì gọi `/api/motion/*` của **bất kỳ box nào** cài bằng script này mà chưa đổi key — kể cả box của chính bạn. |
| 2 | `setup/templates.json` | 9 × (CF API token `cfut…` + Gmail App Password + HF token) | Token có quyền `Account.Cloudflare Tunnel:Edit` + `Zone.DNS:Edit` → trỏ được DNS toàn bộ zone. |
| 3 | `setup/fullstack-setup.sh:141` | `mtc_setup_…` | Admin key của `tasks.datools.info` → đọc được danh sách khách live. |
| 4 | `.env.example` | `SUPER_ADMIN=<email cá nhân của dev gốc>` | Ai chạy setup mà không đổi thì email đó thành admin box mình. |
| 5 | `db/seed_users.sql`, `db/seeds/face_motion_workflow.sql` | 3 email cá nhân thật | PII bên thứ ba. `seed_users.sql` chạy tay nên thay placeholder không đổi hành vi. |

**Tài liệu cũng phải scrub.** Bản đầu của spec này chép nguyên văn email đó vào bảng trên, và bản đầu của `scrub-secrets.sh` hardcode chính cái key nó đi tìm. Cả hai đều tự làm rò thứ mình đang dọn. Vì vậy cổng chặn cuối quét **mọi file được track**, kể cả `docs/`, và dùng regex tổng quát chứ không phải danh sách literal.

`.gitignore` hiện chỉ loại `.env` và `.env.*`, không loại `templates.json`.

Mục 1 là chỗ dễ bỏ sót nhất vì nó không giống secret: tên biến là `DEFAULT_API_KEY` và comment gốc còn giải thích nó là *"đúng key FE đang dùng → FE kết nối được ngay"*. Nhưng chính vì mọi bản deploy dùng cùng một giá trị nên công khai nó là mở cửa API của tất cả các box.

Với repo **public**, `git rm` là không đủ — history vẫn đọc được qua `git log -p` trên GitHub. Mà `git filter-repo` thì đổi toàn bộ SHA nên repo mới không còn commit chung với upstream, biến mọi lần `git pull upstream` thành "unrelated histories". Cách giải: **tách vai** — clone hiện tại giữ làm bản theo dõi upstream (private, local, không push); repo public khởi tạo lại từ snapshot sạch với một commit đầu.

```bash
cd ~/Desktop/motion-clone
cp -a motions-studio motions-studio-upstream      # bản theo dõi upstream, KHÔNG push

cd motions-studio
rm -rf .git
printf '%s\n' 'setup/templates.json' 'setup/pod.env' >> .gitignore
./setup/scrub-secrets.sh          # xử lý cả 5 chỗ, idempotent, exit≠0 nếu còn sót

git init -b main && git add -A
git commit -m "Initial snapshot: fork motion-backend + pod provisioning"
git remote add origin git@github.com:<ban>/<repo-moi>.git
git push -u origin main

# Cổng cuối TRƯỚC khi bật public — phải in ra "sach"
git grep -nE "cfut[A-Za-z0-9_-]{20,}|hf_[A-Za-z0-9]{30,}|mtc_setup_[a-f0-9]{20,}" || echo sach
```

**(c) Scrub gói trong một script, không rải `sed` khắp nơi:** `setup/scrub-secrets.sh`.

Lý do phải là script chứ không phải sửa tay một lần: `sync-upstream.sh` dùng `rsync` để lấy bản mới từ upstream, và rsync **ghi đè nguyên** các file đã scrub → cả 5 secret quay trở lại. Script được gọi lại sau mỗi lần sync (§5.3). Sửa tay là loại lỗi sẽ không nhớ ra.

Đặc tính của script:

- Dò theo **pattern**, không theo số dòng → upstream đổi thứ tự dòng vẫn bắt được.
- **Idempotent**, chỉ ghi file khi nội dung thật sự đổi.
- `--check` chỉ kiểm tra, không sửa — dùng làm cổng chặn trước khi commit/push.
- Exit ≠ 0 nếu quét lần cuối còn bắt được pattern nào.
- Tự chuẩn hóa biểu thức `mk_$(...)` về dạng canonical, nên sửa được cả trường hợp lần scrub trước ghi ra biểu thức lỗi.

`DEFAULT_API_KEY` được thay bằng biểu thức sinh ngẫu nhiên **lúc chạy**:

```bash
DEFAULT_API_KEY="mk_$(head -c 24 /dev/urandom | od -An -tx1 | tr -dc a-f0-9)"
```

Không dùng được hàm `rnd()` có sẵn vì nó định nghĩa ở dòng 42, **sau** dòng gán 34. Và dùng `tr -dc a-f0-9` thay vì `tr -d ' \n'` là có chủ đích: biểu thức không chứa backslash nào nên không phải escape khi ghi vào file. Bản đầu dùng `tr -d ' \n'` đã sinh ra key **lẫn newline giữa chuỗi** (52 ký tự thay vì 51), đủ để làm vỡ parser `.env`.

**Ngoài phạm vi code — ba việc script không làm thay được:**

1. **Token đã bị phát tán.** Chúng nằm trong clone trên máy và có thể trong clone của người khác. Xóa khỏi source không thu hồi được. Chủ sở hữu nên rotate: tạo CF API token mới rồi revoke cái cũ, đổi Gmail App Password, revoke HF token, đổi `NUXT_SETUP_API_KEY` phía Motion Task Cloud.
2. **Box đang chạy vẫn dùng `API_KEY` cũ** (đã công khai), vì `setup-pm2.sh:495` giữ giá trị có sẵn trong `.env`. Phải đổi tay ở `.env` backend **và** `NUXT_MOTION_API_KEY` ở `.env` frontend, rồi `pm2 restart api && pm2 restart motions`.
3. **Repo public không tự chạy được với người ngoài** vì `MOTIONS_REPO` mặc định trỏ tới repo riêng tư. Hệ quả, không phải lỗi.

### 3.3 Quyền với repo frontend

Không có quyền push `ALD-Project/motions`. **Thiết kế không cần quyền push** — `fullstack-setup.sh` chỉ đọc: `git clone --single-branch` (dòng 347) lần đầu, sau đó `git fetch` + `merge --ff-only` (dòng 338-341), và fetch thất bại chỉ `warn "giữ working tree hiện tại"` (dòng 344) chứ không `die`.

Sau giai đoạn 1, `motions/` kèm `.git` và `.output` nằm trên volume nên pod mới không clone lại gì. Để cắt hẳn phụ thuộc vào quyền đọc upstream, dùng mirror cục bộ trên volume — `MOTIONS_REPO` đã là env override sẵn (dòng 115) nên không cần sửa code:

```bash
git clone --mirror git@github.com:ALD-Project/motions.git /workspace/shared/mirrors/motions.git
# pod.env: MOTIONS_REPO=/workspace/shared/mirrors/motions.git
# cập nhật: git -C /workspace/shared/mirrors/motions.git remote update
```

**Lưu ý về repo public:** nó sẽ không tự chạy được với người ngoài, vì `MOTIONS_REPO` mặc định trỏ tới một repo riêng tư. Đây là hệ quả, không phải lỗi.

## 4. Kiến trúc

### 4.1 Bố cục volume

Dung lượng tùy nhóm model muốn giữ: chỉ bộ motion thì ~100 GB là đủ (45-55 GB model + 9 tenant × 2-5 GB); giữ cả catalog 245 GB thì cần ~280 GB.

```
/workspace/                          ← RunPod Network Volume (region cố định)
├── .motion-volume                   ← sentinel: UUID + region + ngày tạo
├── secrets/
│   └── templates.json               ← template CỦA BẠN, không bao giờ vào git
├── shared/                          ← dùng chung mọi tenant, trả tiền một lần
│   ├── comfy-models/                45-250 GB
│   │   └── .manifest.json           danh sách file + size + tổng byte
│   ├── hf-cache/
│   ├── ollama-models/
│   └── mirrors/
│       └── motions.git              ← bare mirror repo FE (§3.3)
└── tenants/
    ├── vanhoang/
    │   ├── motion-backend/          repo BE
    │   │   ├── .env                 secrets + config (chmod 600)
    │   │   ├── .data/minio/         object storage
    │   │   ├── api/node_modules/
    │   │   ├── worker/venv/
    │   │   └── bg-remover/venv/
    │   ├── motions/                 repo FE + .output đã build
    │   ├── pgdata/                  PostgreSQL data directory
    │   ├── pod.env                  env override + tên tenant (gitignored ở repo)
    │   ├── boot-log.tsv             thời gian từng phase, xuyên qua các pod
    │   └── LOCK                     hostname + timestamp pod đang giữ
    ├── Timmy/
    └── ...

$HOME/ComfyUI/                       ← container disk (NVMe local), dựng lại mỗi pod
├── venv/  main.py  custom_nodes/
├── models   → /workspace/shared/comfy-models   (symlink)
└── hf-cache → /workspace/shared/hf-cache       (symlink)
```

Model dùng chung nên chi phí lưu trữ không tăng theo số tenant: mỗi tenant thêm chừng 2-5 GB.

### 4.2 Ba quyết định thiết kế

**ComfyUI code + venv ở lại container disk, chỉ `models/` là symlink lên volume.**
Volume là network storage: đọc một model 18 GB tuần tự thì nhanh, nhưng `import torch` đọc hàng nghìn file nhỏ thì chậm rõ rệt. Pipeline enhance gọi `comfy_recycle` giữa **mỗi chunk RIFE** (`worker/worker_runtime/linux.py:9611`), tức ComfyUI khởi động lại nhiều lần trong một job. Đặt venv trên network storage sẽ phạt đúng chỗ đau nhất.

**Repo thì để nguyên trên volume**, kể cả `worker/venv` và `bg-remover/venv`. Hai venv này chỉ được import một lần lúc process khởi động rồi chạy suốt, không recycle liên tục như ComfyUI, nên phạt network storage không đáng kể — đổi lại tiết kiệm `npm install` + 2 lần `pip install`. Nếu đổi base image làm symlink python gãy thì `setup-pm2.sh:599` và `:606` kiểm `[ ! -x venv/bin/python ]` sẽ fail và tự dựng lại venv.

**PGDATA lên volume bằng cách đổi `data_directory`, không symlink.**
Sửa luôn lỗi mất database. Không symlink `/var/lib/postgresql/<ver>/main` vì `chown -R` mặc định không đi xuyên symlink ở tham số gốc, kết quả là data dir sai owner và Postgres từ chối khởi động — đúng cái bẫy mà comment ở `setup-pm2.sh:558-561` đã ghi lại. Thay vào đó sửa `data_directory` trong `postgresql.conf`.

**Ràng buộc kèm theo:** PGDATA khóa theo **major version** Postgres. Volume tạo với PG 16 mà pod mới có PG 17 thì cluster không start. Cách tránh: luôn deploy cùng một base image. Script sẽ chặn sớm (§6.2).

## 5. Thành phần

Năm script mới. Các file đang có bị sửa ở 5 chỗ, tất cả do `scrub-secrets.sh` quản lý (§3.2c).

| Script | Chạy khi nào | Trách nhiệm |
|---|---|---|
| `setup/scrub-secrets.sh` | Trước mỗi lần commit/push, và sau mỗi lần sync upstream | Gỡ credentials/PII bên thứ ba. `--check` làm cổng chặn. **Đã hoàn thành.** |
| `setup/volume-migrate.sh` | **Một lần**, trên box hiện tại đang có dữ liệu | Dừng PM2 + Postgres → `rsync` lên volume theo layout §4.1 → verify → đổi tên nguồn thành `.bak` |
| `setup/pod-up.sh` | **Mỗi pod mới** | Preflight → Postgres → symlink → gọi `fullstack-setup.sh` → cloudflared → assertion |
| `setup/pod-smoke.sh` | Sau `pod-up.sh`, hoặc khi debug box đang nghi ngờ | Smoke test end-to-end bằng một job motion 540p thật |
| `setup/sync-upstream.sh` | Khi muốn lấy bản mới của ALD-Project | Pull bản theo dõi → rsync sang repo public → tái áp scrub → chặn nếu còn secret |

Cộng một file cấu hình. Cần phân biệt rõ hai thứ cùng tên:

| Đường dẫn | Trong git? | Vai trò |
|---|---|---|
| `/workspace/tenants/<T>/pod.env` | Không (nằm trên volume) | **File thật.** Env override + tên tenant cho từng khách. `pod-up.sh` nạp file này. |
| `setup/pod.env.example` | Có | Bản mẫu, mọi giá trị bí mật để trống. |
| `setup/pod.env` | Không — đã thêm vào `.gitignore` | Không dùng. Chỉ chặn sẵn để nếu ai đó vô tình tạo ở đây thì không lọt vào git. |

### 5.0 Cách gọi, và vòng lặp chicken-and-egg

```bash
# Trên pod MỚI — repo nằm trên volume nên gọi trực tiếp từ đó, không cần git clone
cd /workspace/tenants/vanhoang/motion-backend
./setup/pod-up.sh vanhoang
```

`pod-up.sh` nhận tên tenant làm tham số vị trí thứ nhất, dùng chính tên đó cho `TEMPLATE`
(quy ước: tên thư mục tenant = tên mẫu trong `templates.json` của bạn).

Vòng lặp cần nói rõ: `pod-up.sh` nằm **trong** repo, mà repo lại nằm **trên** volume. Trên pod mới, volume đã được RunPod mount sẵn lúc boot nên script luôn có ở `/workspace/tenants/<T>/motion-backend/setup/pod-up.sh` — không cần clone lại gì. Chỉ lần đầu tiên (trước khi có volume) mới cần `git clone`, và đó là việc của `volume-migrate.sh`.

### 5.1 Trình tự `pod-up.sh`

```
1. Preflight (§6.2) — sai bất kỳ điều kiện nào thì DỪNG, không chạy tiếp
2. Nạp /workspace/tenants/<T>/pod.env

3. Postgres TRƯỚC — mấu chốt thứ tự
   • apt-get install postgresql
   • sed data_directory → /workspace/tenants/<T>/pgdata (chỉ khi chưa đúng)
   • chown postgres:postgres + chmod 0700
   • pg_ctlcluster start

4. Symlink
   • mkdir -p $HOME/ComfyUI
   • ln -sfn /workspace/shared/comfy-models  $HOME/ComfyUI/models
   • ln -sfn /workspace/shared/hf-cache      $HOME/ComfyUI/hf-cache

5. Đối chiếu manifest model LẦN 1 (§7.2)

6. Export env rồi gọi setup có sẵn
   TEMPLATE=<T>
   TEMPLATES_FILE=/workspace/secrets/templates.json
   SETUP_API_URL=http://127.0.0.1:1              ← chặn API upstream, xem ghi chú dưới
   MOTIONS_REPO=/workspace/shared/mirrors/motions.git
   MOTIONS_DIR=/workspace/tenants/<T>/motions
   SKIP_MODELS=1  SKIP_OLLAMA_MODELS=1
   OLLAMA_MODELS=/workspace/shared/ollama-models
   → ./setup/fullstack-setup.sh

7. cloudflared dưới PM2 (container không có systemd nên systemctl im lặng fail)
   • đọc token từ /etc/cloudflared/config.yml do 'cloudflared service install' ghi ra
   • pm2 start cloudflared --name cf -- tunnel run --token <token>
   • pm2 save

8. Đối chiếu manifest model LẦN 2 + 4 assertion (§7.3)
9. Ghi boot-log.tsv
```

**Cái bẫy `SETUP_API_KEY`:** đặt `SETUP_API_KEY=""` **không** vô hiệu được key gắn cứng, vì `${SETUP_API_KEY:-default}` coi chuỗi rỗng như chưa set nên nó rơi về giá trị mặc định ở `fullstack-setup.sh:141`. Phải trỏ `SETUP_API_URL` sang địa chỉ chết (curl fail → `warn` → dùng `TEMPLATES_FILE` local), hoặc đặt `SETUP_API_KEY` thành một giá trị rác. Sau khi đã scrub dòng 141 theo §3.2c thì key mặc định vô hại, nhưng vẫn giữ `SETUP_API_URL` để chắc chắn không gọi ra ngoài.

`OLLAMA_MODELS` được ollama server đọc từ environment; `setup-pm2.sh:625` start bằng `nohup ollama serve` nên nó kế thừa env đã export ở bước 6.

`.env` mang từ volume sang sẽ còn giá trị cũ của các khóa phụ thuộc IP pod (`S3_PUBLIC_ENDPOINT`, `PUBLIC_BASE_URL`). Không cần xử lý: `setup-pm2.sh:499-500` ghi lại hai khóa này theo IP hiện tại mỗi lần chạy, còn secrets thì `ensure_secret` chỉ sinh khi trống nên được giữ nguyên.

### 5.2 Vì sao gần như không cần sửa code hiện có

Bốn chỗ ghép vào đúng khớp sẵn có:

**Postgres.** Chạy trước ở bước 3 nên khi `setup-pm2.sh` tới bước 4/12, `_pg_up()` (dòng 555) trả true → bỏ qua toàn bộ khối start dòng 556-577, đi thẳng tới tạo role/db, thấy cả hai đã tồn tại từ volume → bỏ qua nốt.

**Symlink `models` và `hf-cache`.** Khối clone-robust `setup-pm2.sh:691-703` có danh sách giữ lại là `models venv custom_nodes hf-cache extra_model_paths.yaml` — trùng đúng hai symlink. Nó `mv` symlink ra chỗ tạm, clone ComfyUI, rồi `mv` trả lại. Kể cả `rm -rf "$COMFY_DIR"` ở dòng 696 cũng chỉ xóa symlink chứ không chạm dữ liệu trên volume. `setup-pm2.sh:777` tạo `models/uploads/*` thì các thư mục đó lành lặn nằm trên volume.

**Venv trong repo.** `setup-pm2.sh:599` và `:606` kiểm bằng `[ ! -x venv/bin/python ]` → còn nguyên thì bỏ qua, gãy thì tự dựng lại.

**Template và repo FE.** `TEMPLATES_FILE` (dòng 135), `MOTIONS_REPO` (dòng 115), `MOTIONS_DIR` (dòng 116), `SETUP_API_URL` (dòng 138) đều đã là env override sẵn.

### 5.3 `sync-upstream.sh`

```
1. cd bản theo dõi (motions-studio-upstream) → git pull
2. rsync -a --exclude .git --exclude setup/templates.json  →  repo public
   (KHÔNG --delete, nên file mới của bạn không bị xóa)
3. ./setup/scrub-secrets.sh          ← rsync vừa mang cả 5 secret trở lại
4. ./setup/scrub-secrets.sh --check  ← còn sót thì DỪNG, không commit
5. ghi SHA vừa sync vào UPSTREAM_SHA rồi commit
```

Bước 3 là lý do `scrub-secrets.sh` phải tồn tại: `rsync` ghi đè `setup-pm2.sh`, `lib-feature.sh`, `fullstack-setup.sh`, `.env.example` và các file seed, mang toàn bộ secret của upstream trở lại mỗi lần sync.

Bản theo dõi upstream đã bị **khóa đường push** (`git remote set-url --push origin DISABLED-…`) để không bao giờ đẩy nhầm lên `ALD-Project`. Fetch vẫn hoạt động bình thường.

## 6. An toàn

### 6.1 Quy tắc cứng của `volume-migrate.sh`

- **Không bao giờ xóa nguồn.** Sau khi verify xong chỉ đổi tên **hai** nguồn chiếm chỗ nhiều nhất: `~/ComfyUI/models` → `models.bak` và data dir Postgres cũ → `main.bak`. Rồi in ra lệnh xóa để người dùng tự chạy. Không có nhánh code nào gọi `rm -rf` lên dữ liệu nguồn.
- **Không đổi tên repo nguồn.** Script đang chạy từ trong repo đó, và pod này dù sao cũng sắp bị hủy.
- **Dừng sạch trước khi copy.** `pm2 stop all` rồi `pg_ctlcluster stop`, chờ `postmaster.pid` biến mất. Copy một PGDATA đang chạy sẽ ra bản hỏng, và bản hỏng trông y hệt thư mục bình thường cho tới lúc cần nó.
- **Kiểm chỗ trống trước.** `du -sb` nguồn so với `df` đích, cần dư ít nhất 10%.
- **`rsync -a --partial --info=progress2`.** Đứt mạng giữa chừng thì chạy lại là tiếp tục.
- **Verify trước khi đổi tên nguồn** (§7.1). Chỉ khi tất cả đạt mới đổi tên.
- **Chạy lại được nhiều lần.** Lần hai chỉ đồng bộ phần lệch.

### 6.2 Preflight của `pod-up.sh`

| Kiểm tra | Vì sao chặn |
|---|---|
| `mountpoint -q /workspace` | **Quan trọng nhất.** Volume không attach được thì `/workspace` chỉ là thư mục rỗng trên container disk. Không chặn thì script chạy tiếp, tải lại 50 GB model vào đó, trả tiền lần nữa và mất hết khi pod chết. |
| `/workspace/.motion-volume` tồn tại | Phân biệt volume của mình với một volume lạ mount nhầm. |
| `tenants/<T>/` tồn tại | Gõ sai tên tenant → dừng, liệt kê các tenant có sẵn. Không tự tạo mới, vì tự tạo nghĩa là im lặng dựng một box rỗng. |
| `/workspace/secrets/templates.json` tồn tại và có mẫu `<T>` | Thiếu thì `fullstack-setup.sh` rơi về hỏi tay hoặc gọi API upstream. Chặn sớm. |
| `tenants/<T>/pgdata/PG_VERSION` khớp `pg_lsclusters` | Lệch major version thì Postgres không start. Dừng sớm kèm hai lối thoát: đổi về đúng base image, hoặc chạy `pg_upgrade`. |
| `LOCK` ghi hostname + timestamp | Còn file và hostname khác → cảnh báo có pod khác đang giữ volume, cần `--force`. `postmaster.pid` là lớp chặn thứ hai. |

Mọi bước ghi đều idempotent: `ln -sfn` cho symlink, `sed` vào `postgresql.conf` chỉ chạy khi `data_directory` chưa đúng.

### 6.3 Secrets không bao giờ vào git

- `/workspace/secrets/templates.json` — token thật, chỉ tồn tại trên volume.
- `pod.env` — file thật ở `/workspace/tenants/<T>/pod.env` trên volume; chỉ `setup/pod.env.example` (giá trị trống) được commit.
- `.env` — đã được `.gitignore` sẵn.
- `sync-upstream.sh` chặn commit nếu `git grep` còn bắt được pattern secret.

### 6.4 Hỏng giữa chừng

`pod-up.sh` **chỉ đọc** trên volume, trừ ba thứ nó tạo ra: `LOCK`, `boot-log.tsv`, và dữ liệu ứng dụng lúc chạy (`.env` do setup ghi, `pgdata`, `.data/minio`). Không bước nào xóa hay dời dữ liệu trên volume. Nên hỏng ở bất kỳ đâu thì cách chữa luôn là: hủy pod, tạo pod mới, chạy lại. Volume không bao giờ ở trạng thái dở dang.

Một ca cần nói rõ: nếu setup chết **sau** khi Postgres đã start với data dir trên volume, cluster có thể còn giữ `postmaster.pid`. Lần chạy sau Postgres phát hiện pid trỏ tới process không tồn tại và dọn — hành vi chuẩn khi máy mất điện, không phải trường hợp đặc biệt.

## 7. Kiểm chứng

Kẻ thù không phải lỗi ồn ào mà là **thành công giả**: box lên xanh, `/health` trả ok, đăng nhập được bằng `SUPER_ADMIN` — trong khi database rỗng vì vừa seed lại, hoặc `models/` là thư mục thật rỗng nên setup đã âm thầm tải lại 50 GB. Cả hai trông y hệt lúc chạy đúng. Nên mọi bước dưới đây **đo bằng con số**.

### 7.1 Sau migrate, trước khi hủy box cũ

Ghi số **trước** khi migrate, so lại **sau** khi migrate, bằng cùng một câu lệnh:

```bash
psql -tAc "SELECT 'users', count(*) FROM users
     UNION ALL SELECT 'jobs', count(*) FROM jobs
     UNION ALL SELECT 'workflow_runs', count(*) FROM workflow_runs
     ORDER BY 1"
du -sb ~/ComfyUI/models    && find ~/ComfyUI/models    -type f | wc -l
du -sb "$ROOT/.data/minio" && find "$ROOT/.data/minio" -type f | wc -l
```

(`$ROOT` = thư mục gốc repo motion-backend trên box cũ.)

Sau migrate, khởi cluster trỏ vào `/workspace/tenants/<T>/pgdata` rồi chạy **đúng câu SQL đó**. Ba con số phải trùng tuyệt đối — đây mới là bằng chứng database sống sót; `rsync` báo xong chỉ chứng minh file đã copy, không chứng minh cluster mở được.

Thêm `rsync -n --checksum` phải in ra rỗng.

### 7.2 Manifest model — chống tải lại

Lúc migrate, ghi `shared/comfy-models/.manifest.json`: đường dẫn tương đối + kích thước từng file + tổng byte + số file.

`pod-up.sh` đối chiếu ở hai thời điểm, trước và sau khi gọi setup:

- File thiếu → in tên cụ thể, không phải "có gì đó sai".
- Tổng byte sau ≠ trước → có thứ gì đã tải thêm. Với `SKIP_MODELS=1` thì setup không tải, nhưng ComfyUI-Manager hoặc custom node vẫn có thể tự kéo model lúc chạy.
- Tổng byte < ngưỡng (ví dụ 40 GB) → gần như chắc chắn `models/` đang trỏ vào thư mục rỗng.

### 7.3 Assertion tự động cuối `pod-up.sh`

Bốn khẳng định, sai một cái là exit khác 0:

```bash
[ "$(readlink -f ~/ComfyUI/models)" = /workspace/shared/comfy-models ]
[ "$(psql -tAc 'SHOW data_directory')" = /workspace/tenants/$T/pgdata ]
[ "$(psql -tAc 'SELECT count(*) FROM users')" -gt 0 ]
curl -sf localhost:8188/object_info/WanVideoModelLoader | grep -q WanVideoModelLoader
```

Khẳng định cuối dùng đúng cơ chế `_comfy_has_node` của worker (`linux.py:981-987`): nó chứng minh custom node đã nạp thật, còn `/system_stats` chỉ chứng minh ComfyUI còn thở.

### 7.4 Boot log — tín hiệu hồi quy giữa các pod

```
2026-07-31T09:14  apt+node 212s  comfy-venv 486s  models-check 3s  fe-build 164s  TONG 1102s
2026-08-02T11:40  apt+node 198s  comfy-venv 471s  models-check 2s  fe-build 158s  TONG 1054s
```

File nằm trên volume nên sống xuyên qua các pod. Ngày nào cột `models-check` nhảy từ 3 giây lên 40 phút thì biết ngay là đang tải lại. Đây cũng là cách đo giai đoạn 2 có thật sự cắt được thời gian hay không.

### 7.5 Smoke test end-to-end (`pod-smoke.sh`)

Bốn assertion chứng minh từng mảnh đúng, nhưng chỉ một job thật mới chứng minh chúng ghép lại đúng. Chạy một job motion 540p ngắn nhất qua API bằng `API_KEY` trong `.env`, chờ `status='done'`, kiểm output tải về được từ MinIO.

Job này đi qua: Postgres (tạo record) → worker claim → ComfyUI nạp Wan Animate **từ symlink volume** → DWPose → sampling → VAE decode → upload MinIO → API trả URL. Nó chạm mọi thứ mà migrate đã đụng vào.

## 8. Kết quả mong đợi

| Hạng mục | Hiện tại | Sau giai đoạn 1 | Sau giai đoạn 2 |
|---|---|---|---|
| apt + Node + PM2 | 3-6 phút | 3-6 phút | trong image |
| ComfyUI venv + torch + 10 custom node | 10-18 phút | 10-18 phút | trong image |
| api npm + worker/bg-remover venv | 3-5 phút | ~20 giây | ~20 giây |
| FE clone + npm install + build | 3-5 phút | 2-4 phút (clone từ mirror local) | 2-4 phút |
| Tải model | 45-55 GB | **0** | 0 |
| Nhập config | vài phút gõ tay | **0** | 0 |
| Database qua Stop/Start | **mất** | **giữ** | giữ |
| pull image (chưa cache) | — | — | 60-90 giây |
| **Tổng** | **45-90+ phút** | **≈ 16-28 phút** | **≈ 3-6 phút** |

Chi phí thêm: Network Volume RunPod khoảng $0,07/GB/tháng — 150 GB ≈ **$10,5/tháng**, tính cả khi không có pod nào chạy.

## 9. Ngoài phạm vi giai đoạn 1

| Không làm | Lý do |
|---|---|
| Bake Docker image | Là giai đoạn 2. Bake trước khi biết quy trình volume có hợp cách làm việc thực tế không thì phải sửa image nhiều lần. |
| Đưa ComfyUI venv lên volume | Xem §4.2: `comfy_recycle` chạy nhiều lần trong một job enhance. |
| Mirror model trên R2/S3, hỗ trợ đa region | Region đã cố định. |
| Tự tạo tenant mới trong `pod-up.sh` | Cố ý. Gõ sai tên mà script tự tạo thư mục rỗng thì sẽ dựng ra box trắng và tưởng là mất dữ liệu. |
| Tự động tạo/hủy pod qua RunPod API | Tiết kiệm vài cú click, đổi lại thêm một API key và một bề mặt lỗi mới. |
| Sửa `mediaJobTimeoutSec` (bug enhance 2K timeout 2700s) | Việc riêng, PR riêng. Trộn vào đây thì lúc hỏng không biết do cái nào. |
| Fork repo FE sang tài khoản riêng | Không cần: chỉ đọc là đủ, và mirror trên volume đã cắt phụ thuộc (§3.3). |
| Rotate token của ALD-Project | Không làm được thay chủ sở hữu. Chỉ thông báo. |

### Lỗ hổng đã biết, cần quyết riêng

Đưa PGDATA lên volume làm database **bắt đầu tồn tại lâu dài** — hiện tại nó chết mỗi lần Stop/Start nên chưa từng có sự phụ thuộc nào. Sau thay đổi này thì có. Mà volume là **một điểm chết duy nhất**: model mất thì tải lại được, `pgdata` và `.data/minio` thì không.

Đề xuất làm ngay sau giai đoạn 1, không gộp vào: cron `pg_dumpall | gzip` đổ vào MinIO cộng một bản offsite.

## 10. Giai đoạn 2 — điều kiện và nội dung

### Điều kiện bắt đầu

1. Đã dựng lại thành công qua **ít nhất 3 pod khác nhau**, trong đó có một lần đổi loại GPU.
2. Manifest model **chưa lệch lần nào**.
3. Đã chốt **một base image cụ thể** và không đổi nữa.

### Nội dung

Cắt hai dòng nặng nhất còn lại: `apt+node` (~200s) và `comfy-venv + custom nodes` (~480s), thay bằng ~60-90s pull image đã cache.

### Chống drift

Rủi ro chính của giai đoạn 2 là image lệch so với box thật — chính là khối cảnh báo `⚠️⚠️ ON-BOX PARITY` đang nằm trong `worker/runpod/Dockerfile`. Cách chống: rút danh sách custom node ra một file dùng chung, ví dụ `comfyui/custom-nodes.txt`, cho cả `setup-pm2.sh:712-722` lẫn Dockerfile đọc từ đó. Lúc ấy drift không còn khả năng xảy ra về mặt cấu trúc, chứ không phải nhờ nhớ cập nhật hai chỗ. Việc này cũng sửa luôn bug parity đang mang sẵn ở nhánh Serverless VVIP.

**Lưu ý cho fork:** `comfyui/custom-nodes.txt` là file mới nhưng việc cho `setup-pm2.sh` đọc nó **là sửa file đang có** → thêm điểm conflict. Ở giai đoạn 2 cần cân lại: hoặc chấp nhận một điểm conflict nữa, hoặc để Dockerfile của bạn tự parse danh sách trực tiếp từ `setup-pm2.sh:712-722` (đọc một chiều, không sửa gì).
