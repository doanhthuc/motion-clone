# Chạy lô material: hướng dẫn dùng (CLI và MCP)

Chuẩn bị material trên máy, gõ một lệnh, nhận video. Không đụng UI.

Tài liệu này là **cách dùng**. Còn *vì sao* thiết kế như vậy và các đánh đổi đã cân:
[`superpowers/specs/2026-08-18-batch-runner-design.md`](superpowers/specs/2026-08-18-batch-runner-design.md).

Mọi giá trị trong đây tra từ code, kèm `file:line` để bạn tự kiểm.

---

## Đường ngắn nhất

```bash
make gpu-provision CONFIRM=yes && make gpu-wait && make gpu-bootstrap   # pod
make batch-scan DIR=~/materials MODE=pair                              # đẻ manifest nháp
$EDITOR batch/$(date +%F).yaml                                         # SOÁT
make batch-validate FILE=batch/$(date +%F).yaml                        # miễn phí
make batch          FILE=batch/$(date +%F).yaml                        # chạy
make gpu-destroy                                                       # xong việc
```

Kết quả ở `out/latest/_final/`.

---

## 0. Mô hình tinh thần

### Ranh giới cứng: `.yaml` là của bạn, mọi thứ khác là của máy

| File | Của ai | Ai ghi |
|---|---|---|
| `batch/<tên>.yaml` | **bạn** — khai run + param, kèm comment | **không ai ghi vào đây ngoài bạn** |
| `batch/<tên>.state.json` | máy | runner ghi sau **mỗi chặng** (journal: `job_id`, trạng thái, file) |
| `batch/<tên>.mcp.json` `.log` `.rc` | máy | MCP ghi pid / log / mã thoát của lượt chạy nền |
| `out/<lô>/` | máy | kết quả |

Lý do tách journal ra file riêng: `PyYAML.safe_dump` xoá sạch comment, mà comment chính là
chỗ bạn ghi *"preset này cho khách A, đừng đổi"*. Ghi đè một lần là mất, không lấy lại được.

Cả bốn dạng file của máy đều đã `.gitignore` — material và kết quả là dữ liệu cá nhân, repo này public.

### Ba nguyên tắc

1. **Validate trước, tiêu tiền sau.** Manifest sai ở run thứ 9 phải nổ *trước khi* run thứ nhất chạm GPU.
2. **Sinh manifest tách khỏi chạy manifest.** `batch-scan` đẻ ra *bản nháp để soát*, không phải lệnh
   chạy. Đoán sai chỉ tốn một lần liếc mắt, không tốn tiền GPU.
3. **Tuần tự, không song song.** Pod có một GPU, và `run_enhance` gọi `comfy_recycle` để xả RAM/VRAM
   của Wan trước mỗi pha nặng — hai job chồng nhau phá đúng giả định "lúc này GPU chỉ có mình tôi"
   mà các lời gọi đó dựa vào.

---

## 1. Trước mọi thứ: pod phải sống

```bash
make gpu-preflight                 # kiểm .env — MIỄN PHÍ, làm trước khi tiêu tiền
make gpu-provision                 # dry-run: in lệnh create + giá, KHÔNG thuê
make gpu-provision CONFIRM=yes     # thuê thật  ← đồng hồ bắt đầu chạy từ đây
make gpu-wait                      # ~2 phút, ghi GPU_SSH_HOST/PORT vào .env
make gpu-bootstrap                 # dựng backend trên pod
make gpu-status                    # xác nhận backend trả lời
```

| Trạng thái pod | Lệnh |
|---|---|
| Đang chạy | không cần gì |
| Đang **dừng** | `make gpu-up` |
| **Chưa có / đã destroy** | `make gpu-provision CONFIRM=yes` → `gpu-wait` → `gpu-bootstrap` |

Xong việc: **`make gpu-destroy`**.

> **Network Volume vẫn tính tiền tháng sau `gpu-destroy`.** Đó là chủ ý — nó giữ ~78 GB model, DB và
> MinIO, nên lô sau khỏi tải lại. Xoá volume là lần bootstrap sau phải tải lại toàn bộ.

Dấu hiệu pod chưa sẵn sàng: `curl https://$DOMAIN/health` trả **530** = Cloudflare không thấy origin
(pod đang boot, hoặc không có pod). Trả **200** là xong.

---

## 2. Đường CLI

### 2.1 Nạp material — thả đúng ngăn, không đặt tên file

```
~/materials/
  characters/    IMG_2841.jpg  co-gai-toc-ngan.png  mau-C.jpeg
  outfits/       vay-do.jpg    ao-khoac-den.jpg
  backgrounds/   studio-trang.jpg
  drivers/       dandong.mp4   xoay-nguoi.mp4
```

```bash
make batch-scan DIR=~/materials MODE=pair    # ghép theo thứ tự, dừng ở ngăn NGẮN NHẤT
make batch-scan DIR=~/materials MODE=cross   # tích Descartes
```

Thư mục trên: `pair` → 2 run · `cross` → 3×2×1×2 = **12 run**. `batch-scan` in số run và thời gian
GPU ước tính **trước khi ghi file**, vì `cross` rất dễ vô tình đẻ 60 run.

| Biến | Việc |
|---|---|
| `OUT=batch/ten-khac.yaml` | đổi chỗ ghi (mặc định `batch/<hôm nay>.yaml`) |
| `FORCE=1` | cho ghi đè file đã có |

Pipeline **tự suy ra** từ material có mặt: có `outfits/` → `tryon-motion-enhance`, không có →
`motion-enhance`. `backgrounds/` rỗng thì tryon vẫn chạy, chỉ là không ghép nền.

Tên run tự sinh từ tên material (`<character>__<outfit>__<driver>`), nên nhìn tên file kết quả là
biết ai mặc gì nhảy theo cái gì. Trùng thì thêm `-2`.

### 2.2 Manifest

```yaml
defaults:                              # áp cho MỌI run, run tự ghi đè được
  enhance: { targetRes: 1080p, fpsInterp: "60" }

runs:
  - id: mauA__vay-do__dandong
    pipeline: tryon-motion-enhance
    inputs:
      character:  ~/materials/characters/IMG_2841.jpg
      outfit:     ~/materials/outfits/vay-do.jpg
      background: ~/materials/backgrounds/studio-trang.jpg   # TUỲ CHỌN
      driver:     ~/materials/drivers/dandong.mp4
    motion: { preset: drv-30s, quality: 720p }

  - id: mauB__dandong
    pipeline: motion-enhance
    inputs:
      character: ~/materials/characters/mau-C.jpeg
      driver:    ~/materials/drivers/dandong.mp4
    enhance: { fpsInterp: "48" }       # ghi đè defaults
```

**Chỉ `inputs` là bắt buộc.** Không ghi param nào thì chạy đúng mặc định như bấm UI.

| Pipeline | Chặng | `inputs` bắt buộc | Tuỳ chọn |
|---|---|---|---|
| `motion-enhance` | motion → enhance | `character`, `driver` | — |
| `tryon-motion-enhance` | tryon → motion → enhance | `character`, `outfit`, `driver` | `background` |

Khối param tên **đúng bằng tên chặng**: `tryon:` · `motion:` · `enhance:`.

Timeout riêng từng chặng (`batchlib/pipelines.py`): tryon 20 phút · motion 60 · enhance 90.
enhance 1080p60 nội suy RIFE rồi encode lại nên **luôn lâu hơn** motion sinh ra nó.

### 2.3 Tra param — đừng đoán

```bash
make batch-params TYPE=motion      # 39 key
make batch-params TYPE=tryon       # 14 key
make batch-params TYPE=enhance     #  7 key
```

Nó in tên key, giá trị mặc định, và **`file:line` chỗ worker đọc nó** — repo này comment rất dày,
đó là tài liệu thật.

Giá trị hợp lệ (`scripts/batch-params.json`, và `make check-batch-params` giữ nó khớp `linux.py`):

| Chặng | Param | Giá trị hợp lệ |
|---|---|---|
| `motion` | `preset` | `drv-5s` `drv-10s` `drv-15s` `drv-20s` `drv-30s` |
| | `quality` | `540p` `720p` |
| | `render_profile` | `fast` `max` — nhưng xem bẫy #3 |
| `tryon` | `provider` | `qwen` `gemini` |
| `enhance` | `targetRes` | `1080p` `2k` |
| | `fpsInterp` | `""` `30` `48` `60` |
| | `engine` | `flashvsr` `seedvr2` |
| | `mode` | `auto` `image` `video` |

### 2.4 Bốn cái bẫy param — cả bốn đều IM LẶNG

**#1 · `quality` chỉ có tác dụng khi có `preset: drv-*`.** Không có preset thì
`enforceMotionResolution` return sớm (`api/src/motion-resolution.js:20`), `quality` bị bỏ qua
**không một dòng log**, và `720p` sẽ ra `540p`. `make batch-validate` chặn đúng dạng này.

**#2 · Đừng gõ `frames` cùng `preset: drv-*`.** Worker tự đo độ dài driver rồi đặt `frames`
(`linux.py:4142`); số bạn gõ mất luôn.

**#3 · `render_profile` bị API ép `fast` VÔ ĐIỀU KIỆN** (`api/src/routes/jobs.js:36-37`,
`normalizeMotionDriverSegment`). Gửi `max` cũng thành `fast`. Muốn 20-step thật thì phải bật
`MOTION_FORCE_QUALITY=1` **trên worker**, không đặt trong manifest — và nhớ tắt sau khi A/B xong.
Cùng cơ chế: `jobs.js` ép `detailUpscale=false` cho **mọi** job motion.

**#4 · Code nhận CẢ HAI kiểu viết** — `targetRes` và `target_res`, `faceCropMode` và
`face_crop_mode`, `cleanOnly` và `clean_only`. Gõ sai một biến thể **thứ ba** thì `params.get()` trả
`None`, job **vẫn chạy**, **vẫn tính tiền**, và ra kết quả của giá trị mặc định. Không lỗi, không
log, không ai biết cho tới khi so hai video thấy giống nhau. Đây là lý do validate chặn key lạ kèm
gợi ý key gần đúng, và chạy **trước job đầu tiên của cả lô** chứ không phải trước từng job.

### 2.5 Chạy

```bash
make batch-validate FILE=batch/2026-08-18.yaml    # MIỄN PHÍ — chạy trước, luôn luôn
make batch          FILE=batch/2026-08-18.yaml
make batch          FILE=batch/2026-08-18.yaml RESUME=1
make batch          FILE=batch/2026-08-18.yaml FAIL_FAST=1
```

| Cờ | Việc |
|---|---|
| `RESUME=1` | chạy tiếp lô dở — xem §2.7 |
| `FAIL_FAST=1` | dừng cả lô ngay khi một run hỏng. Mặc định: **run hỏng KHÔNG giết lô** |

CLI **tự `make gpu-up`** nếu pod đang dừng (bật là thao tác đảo được nên nó tự làm). Nhưng
**không bao giờ tự `gpu-destroy`** — nó in sẵn lệnh cho bạn dán, vì "mọi run done" không có nghĩa
là video dùng được; bạn còn phải xem đã.

### 2.6 Kết quả

```
out/
  latest -> 2026-08-18-2105                  symlink tới lô mới nhất
  2026-08-18-2105/
    _final/                                  giao việc thì chỉ cần cái này
      mauA__vay-do__dandong.mp4
      mauB__dandong.mp4
    _index.tsv                               MỘT DÒNG MỖI CHẶNG
    manifest.yaml                            bản đông cứng, chép NGUYÊN VĂN cả comment
    runs/mauA__vay-do__dandong/
      01-tryon.png                           tryon ra ẢNH
      02-motion.mp4
      03-enhance.mp4                         hardlink với _final/, không nhân đôi đĩa
      run.json                               param THẬT đã gửi
      run.log                                đóng terminal không mất
```

Quy tắc tên: `NN-<tên chặng>` áp cho **mọi** chặng kể cả chặng cuối — đọc tên file là biết chặng nào
sinh ra nó. Bản "final" đã có ở `_final/<run-id>.mp4`.

**Giữ file trung gian là cố ý:** khi kết quả cuối xấu, câu hỏi đầu tiên luôn là *"tryon ra ảnh gì"*.
Không giữ thì phải chạy lại cả chuỗi để trả lời.

`_index.tsv` có **hai** cột param, và đây là chỗ đáng giá nhất khi debug sáu tuần sau:

| Cột | Nghĩa |
|---|---|
| `params_manifest` | bạn **xin** gì (từ `.yaml`) |
| `params_sent` | API **ghi vào DB** gì (đọc từ DTO thật của `GET /jobs/<id>`) |

Chỗ lệch giữa hai cột chính là câu trả lời cho *"vì sao hai lô khác nhau"*. Đo thật trên pod:
manifest gửi `{preset: drv-5s, quality: 540p}` → API ghi
`{cfg: 1, detailUpscale: false, fitDriver: false, deliveryPreset: source, …}`.

Chặng chưa bao giờ `done` thì `params_sent` là `{}` — đúng, vì lúc đó ta **thật sự không biết**.

### 2.7 `RESUME=1` — điều quan trọng nhất phải hiểu

Nó **không** chạy lại từ đầu. Thứ tự việc:

1. Run đã `done` → **bỏ qua**
2. Chặng đã `done` và file còn trên đĩa → **bỏ qua**
3. Chặng có `job_id` mà chưa `done` → **BẮT LẠI đúng job cũ trên pod**, không gửi job mới

Điểm 3 là lý do nó tồn tại: chặng enhance 40 phút bị Ctrl-C ở phút 39, gửi job mới là **trả tiền GPU
hai lần cho cùng một việc**.

Chỉ **hai** kết cục được phép gửi job mới:

| Kết cục | Xử lý |
|---|---|
| Job đã chạy và **hỏng thật** (`JobFailed`) | gửi job mới |
| Job **không còn trên pod** (404 — pod đã dựng lại, DB mới) | gửi job mới |
| **Quá hạn / mất liên lạc** | **KHÔNG gửi mới** — job vẫn đang chạy trên pod |

Journal ghi sau **mỗi chặng**, không phải cuối run. Một run ba chặng đứt ở chặng ba thì resume chỉ
chạy lại chặng ba.

### 2.8 Dọn đĩa

```bash
make batch-clean KEEP=3 DRY=1     # xem trước, chưa xoá
make batch-clean KEEP=3           # xoá runs/ của lô cũ hơn 3 lô gần nhất
```

Chỉ xoá `runs/` (file trung gian). **`_final/` không bao giờ bị đụng tới.**

---

## 3. Đường MCP chat

### 3.1 Bật một lần

`.mcp.json` đã có trong repo. Mở `claude` trong thư mục này → duyệt server `batch` → xong. Không cài
gói nào (server tự cầm JSON-RPC, 0 dependency).

Kiểm rẻ, không tốn gì: **`make batch-mcp-check`** — bắt tay thật rồi in bốn tool nó khai.

> **Sửa code MCP xong PHẢI khởi động lại `claude`.** Server là process sống suốt phiên, nạp module
> một lần — sửa `scripts/batchlib/mcp_tools.py` mà không khởi động lại thì tool chạy code **cũ**,
> **im lặng, không báo gì**. Cách nhận ra: `ps -o pid,lstart -p $(pgrep -f batch_mcp.py)` cũ hơn lần
> sửa của bạn. `make batch-mcp-check` KHÔNG bắt được vì nó spawn process mới nên luôn thấy code mới.

### 3.2 Bốn tool

| Tool | Tiêu tiền? | Tham số | Việc |
|---|---|---|---|
| `batch_validate` | không | `file` | kiểm manifest, trả danh sách lỗi có cấu trúc |
| `batch_run` | **CÓ** | `file` · `resume` · `fail_fast` · `allow_start` | chạy cả lô, **nền**, trả pid |
| `batch_status` | không | `file` · `so_dong_log` | tiến độ từng run/từng chặng + tail log |
| `batch_rerun` | **CÓ** | `file` · `run_id` | chạy lại **một** run, kể cả đã `done` |

Mọi tool định danh bằng **đường dẫn manifest**, không phải batch id — nhớ một thứ đó là đủ.

Lô chạy **nền, tách hẳn session**, nên đóng phiên chat không giết nó. Server MCP không giữ trạng
thái gì của riêng nó: nguồn sự thật vẫn là journal runner ghi.

### 3.3 Nói thế nào trong chat

Nói tiếng người, không cần nhớ tên tool:

| Bạn nói | Tool được gọi |
|---|---|
| *"kiểm `batch/2026-08-19.yaml` giúp tôi"* | `batch_validate` |
| *"chạy lô đó đi"* | `batch_validate` rồi `batch_run` |
| *"chạy tới đâu rồi"* | `batch_status` |
| *"chạy tiếp lô dở hôm qua"* | `batch_run(resume=true)` |
| *"video của `mauB` nhìn xấu, chạy lại run đó"* | `batch_rerun(run_id="mauB…")` |

### 3.4 `batch_rerun` — thứ duy nhất CLI không làm được

`RESUME=1` **cố ý bỏ qua** run `status: done`. Nên khi job chạy xong mà **video nhìn xấu** — không
lỗi, chỉ là không dùng được — CLI không có đường nào bắt nó làm lại.

`batch_rerun` xoá đúng entry của run đó khỏi journal rồi chạy `--resume`. Các run khác giữ nguyên
`done` nên **không ai bị làm lại**.

Muốn **đổi param** trước khi chạy lại thì sửa `.yaml` của bạn **trước**, rồi mới rerun — tool không
bao giờ sửa file của bạn.

### 3.5 Hai cửa chặn tự động

**Pod đang dừng → MCP KHÔNG tự bật.** Mặc định `--no-start`: nó báo lỗi và bảo bạn gõ `make gpu-up`.
Muốn tự bật phải gọi kèm `allow_start=true`. **CLI thì tự bật** — đây là chỗ duy nhất hai đường cố ý
xử sự khác nhau, vì CLI là bạn gõ còn MCP là một model gọi, và bật pod bắt đầu tính tiền ngay.

**Đang có lô chạy cho cùng manifest → từ chối lô thứ hai.** Không phải để lịch sự: hai runner cùng
ghi một `state.json` là hỏng journal, và hai job chồng nhau phá giả định "GPU chỉ có mình tôi".

Thêm: **manifest sai thì không spawn process nào** — validate chạy trước khi có gì được gửi đi.

### 3.6 `batch_status` trả gì

```json
{ "lo": "2026-08-18-2105",
  "thu_muc_ket_qua": "/…/out/2026-08-18-2105",
  "dang_chay": false, "pid": 58963, "ma_thoat": 0,
  "tong": { "xong": 2, "hong": 0, "con_lai": 0 },
  "runs": [{ "id": "…", "pipeline": "motion-enhance", "status": "done", "loi": null,
             "chang": [{ "ten": "motion", "status": "done",
                         "job_id": "60703d2c…", "giay": 247, "bytes": 1437581 }] }],
  "log_file": "batch/….mcp.log",
  "log": ["…N dòng cuối…"] }
```

Nó đọc **journal**, không hỏi pod — nên **vẫn đúng sau khi pod đã destroy**.

`ma_thoat` là mã thoát của lô: `null` = chưa kết thúc, `0` = xong sạch, khác 0 = có run hỏng.

---

## 4. Khi hỏng thì làm gì

| Triệu chứng | Chỗ xem |
|---|---|
| Manifest sai (file thiếu, key lạ, pipeline không có) | `batch_validate` / `make batch-validate` — chặn trước khi tiêu GPU |
| Một run hỏng | `out/<lô>/runs/<run>/run.log`. `_index.tsv` giữ **cả dòng chặng đã làm nó dừng** |
| Param thật đã gửi là gì | `run.json`, hoặc cột `params_sent` của `_index.tsv` |
| Lỗi phía worker | `make gpu-logs LOG=worker` — **phải làm TRƯỚC khi destroy** |
| `face_crop=vitpose` hay fallback DWPose? | **`GET /jobs/:id/logs`** — nó ghi bằng `api_log` (`linux.py:3962`) nên **không** nằm trong `~/.pm2/logs/worker-out.log` |
| Lô đứt giữa chừng, máy ngủ, Ctrl-C | `RESUME=1` — bắt lại job cũ, không tiêu tiền lần hai |
| Job báo done nhưng file rỗng | runner đã chặn: có sàn `min_bytes` (mp4 100 KB, ảnh 5 KB) |
| `/health` trả 530 | Cloudflare không thấy origin — pod đang boot, hoặc không có pod |

**Lô hỏng thì đừng destroy pod ngay** — đó chính là lúc cần nó nhất để đọc log worker. Runner cũng
không bao giờ tự destroy, kể cả khi mọi run đều xong.

> **Bẫy đã mất thời gian một lần:** `~/.pm2/logs/comfyui-*.log` đầy dòng `DWPose` (kể cả
> `DWPose: Bbox 7.42ms` đúng khung giờ job motion) và **không** dòng nào có `vitpose` — nhìn y như
> fallback DWPose pad128 đã quay lại. **Không phải.** DWPose lo **pose thân người** (node 20),
> ViTPose chỉ lo **face crop**; hai cái vốn chạy cùng nhau. Nguồn đúng là `GET /jobs/:id/logs`.

---

## 5. Nên dùng đường nào

| Việc | Dùng |
|---|---|
| Lô lớn, chạy qua đêm, muốn thấy tiến độ trực tiếp trên terminal | **CLI** |
| Đang làm việc khác trong chat, muốn "chạy rồi báo tôi" | **MCP** |
| Chạy lại một run vì video xấu | **MCP** (`batch_rerun`) — CLI không có |
| Debug lỗi lạ | **CLI** — đọc một file log dễ hơn qua một lớp giao thức |
| Thuê pod / tắt pod | **CLI luôn** — cả hai là quyết định tiêu tiền của bạn |

---

## 6. Cổng kiểm (không cần pod, không tốn gì)

```bash
make batch-test           # 239 unit test
make check-batch-params   # batch-params.json phải khớp linux.py
make batch-mcp-check      # bắt tay thật với MCP server, in 4 tool
make batch-coverage       # dòng nào của batch runner chưa test nào chạm tới
```

Và trước mọi commit (repo này public): `motions-studio/setup/scrub-secrets.sh --check`.

---

## 7. Số đo thật, để so sánh

Pod RTX 5090 (RunPod EU-RO-1, $0,99/giờ), lô `2026-08-18-2105` gọi bằng tool `batch_run`:

| Run | Chặng | Giây | KB |
|---|---|---:|---:|
| `vidu-day-du` (tryon→motion→enhance) | tryon | 351 | 1 846 |
| | motion | 247 | 1 404 |
| | enhance | 114 | 7 476 |
| `vidu-chi-motion` (motion→enhance) | motion | 160 | 866 |
| | enhance | 101 | 5 874 |

**2 xong · 0 hỏng · 973s ≈ 16,2 phút GPU.** Cả hai video 1088×1920; `vidu-day-du` ra **60fps**
(`defaults`), `vidu-chi-motion` ra **48fps** (`enhance.fpsInterp` ghi đè) — dùng để kiểm nhanh rằng
override theo từng run vẫn còn tác dụng.

Con số của bạn sẽ khác nếu đổi `preset`, `quality` hay `fpsInterp`: 60fps thì enhance vừa lâu hơn vừa
ra file to hơn 48fps.
