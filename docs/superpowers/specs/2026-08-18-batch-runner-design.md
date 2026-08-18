# Chạy lô material từ máy local → video trên pod GPU — Design

**Ngày:** 18/08/2026 · **Trạng thái:** thiết kế, chưa triển khai.

## Mục tiêu

Chuẩn bị material trên máy, gõ một lệnh, nhận video. Không đụng UI.

Hai quy trình:

```
tryon-motion-enhance   character + outfit + background + driver  →  tryon → motion → enhance → mp4
motion-enhance         character + driver                        →  motion → enhance → mp4
```

Nhịp dùng thật của dự án là thuê pod theo phiên làm việc rồi `gpu-destroy`
([gpu-pod.md](../../gpu-pod.md)). Một phiên như vậy hiện phải bấm UI từng job, mỗi job phải ngồi
canh để bấm job kế. Spec này biến phiên đó thành: thả material vào thư mục, gõ `make batch`, đi làm
việc khác.

**Ngoài phạm vi:** thuê pod (`gpu-provision` vẫn là thao tác riêng, có xác nhận, vì nó tiêu tiền);
sửa API trên pod; thêm job type mới; và MCP server (§9 — giai đoạn 2, sau khi CLI đã chạy thật).

## Vì sao không dùng workflow engine có sẵn trên pod

Pod đã có engine chạy graph (`motions-studio/api/src/wf-worker/engine.js`), đã có template
`linux-motion-control` làm đúng quy trình thứ hai (`db/seeds/workflow_templates.sql`), và
`POST /workflows/:slug/invoke` nhận `x-api-key` (`api/src/auth/session.js:7-21`) nên CLI gọi được.

Đường đó vẫn hợp lệ. Không chọn vì **chỉnh param theo từng bộ material**: config của node nằm trong
`definition` lưu ở DB, nên đổi một toggle cho riêng một lần chạy phải `PUT` lại definition hoặc đẻ
thêm một slug biến thể. Với một lô 12 run mỗi run một preset khác, đó là 12 lần ghi DB cho một việc
đáng lẽ là 12 dòng trong một file. Cơ chế override qua `workflowInput` có tồn tại
(`handlers.js:751` merge `ctx.workflowInput` vào params) nhưng phải kiểm chứng từng node xem param
nào đi lọt — một mặt tiếp xúc không được kiểm soát bởi test nào.

Đổi lại, hướng client-side nhận ba nhược điểm, ghi ra đây để sau này không ai tưởng chúng bị bỏ sót:

1. **Máy phải thức suốt lô.** Bù bằng journal + `--resume` (§6).
2. **Run không hiện trong tab Workflow của app.** Job vẫn hiện trong tab Jobs, vì vẫn là job thật.
3. **Byte đi vòng qua máy local** giữa các chặng (§3).

## 1. Kiến trúc

```
~/materials/            batch/<tên>.yaml            pod GPU
 characters/            runs:                        POST /jobs        (multipart)
 outfits/       scan→     - id: …          run→      GET  /jobs/:id    (poll)
 backgrounds/            inputs: …                   GET  /jobs/:id/download
 drivers/                motion: {…}                       ↓
                                                    out/<lô>/…
```

| Đơn vị | Việc | Phụ thuộc |
|---|---|---|
| `batch/<tên>.yaml` | Dữ liệu. Khai run + param. Runner ghi ngược kết quả vào chính nó. | — |
| `scripts/batch_scan.py` | Đọc 4 thư mục vai trò → đẻ manifest nháp | — |
| `scripts/batch_run.py` | preflight → validate → chạy tuần tự → journal → tổng kết | manifest, `.env` |
| `scripts/batch_params.py` | Rút param từ `linux.py` bằng AST + file khai tay | `linux.py` |
| `scripts/batch-params.json` | Khai tay phần AST không thấy + giá trị hợp lệ | — |

Viết bằng **Python 3**: PyYAML 6.0.3 đã có sẵn trên máy dev (đo 18/08/2026), `scripts/` đã có tiền
lệ Python (`omni-flash-motion-test.py`, `pysite/`), và AST extractor ở §7 buộc phải là Python. Root
repo không có `package.json` nên hướng Node sẽ phải đẻ thêm một cây `node_modules` chỉ để đọc YAML.
Preflight kiểm PyYAML và in đúng `pip3 install pyyaml` nếu thiếu.

## 2. Nạp material: thư mục vai trò

Không đặt tên file. Thả đúng ngăn:

```
~/materials/
  characters/   IMG_2841.jpg   co-gai-toc-ngan.png   mau-C.jpeg
  outfits/      vay-do.jpg     ao-khoac-den.jpg
  backgrounds/  studio-trang.jpg
  drivers/      dandong.mp4    xoay-nguoi.mp4
```

```bash
make batch-scan DIR=~/materials MODE=pair     # ghép theo thứ tự, dừng ở ngăn ngắn nhất
make batch-scan DIR=~/materials MODE=cross    # tích Descartes
```

`cross` với thư mục trên là 3 × 2 × 1 × 2 = **12 run**. `batch-scan` in số run và tổng thời gian GPU
ước tính **trước khi** ghi file, vì kiểu này rất dễ vô tình đẻ 60 run.

Manifest sinh ra là **bản nháp để soát**, không phải lệnh chạy. Đoán sai chỉ tốn một lần liếc mắt,
không tốn tiền GPU — đó là lý do bước sinh manifest tách khỏi bước chạy thay vì runner tự quét
thẳng thư mục.

Pipeline suy ra từ material có mặt: có `outfits/` → `tryon-motion-enhance`, không có →
`motion-enhance`. `backgrounds/` rỗng thì `tryon` vẫn chạy, chỉ là không có nền ghép.

Đường lùi: `MODE=folders` đọc kiểu mỗi run một thư mục con với tiền tố cố định
(`character.*` / `outfit.*` / `background.*` / `driver.*`), cho trường hợp material đã gom sẵn theo
run. Không phải cách chính.

## 3. Manifest

```yaml
# batch/2026-08-18.yaml
defaults:                            # áp cho mọi run, run tự ghi đè được
  enhance: { targetRes: 1080p, fpsInterp: "60" }

runs:
  - id: mauA__vay-do__dandong
    pipeline: tryon-motion-enhance
    inputs:
      character:  ~/materials/characters/IMG_2841.jpg
      outfit:     ~/materials/outfits/vay-do.jpg
      background: ~/materials/backgrounds/studio-trang.jpg
      driver:     ~/materials/drivers/dandong.mp4
    motion: { preset: drv-30s }

  - id: mauB__dandong
    pipeline: motion-enhance
    inputs:
      character: ~/materials/characters/mau-C.jpeg
      driver:    ~/materials/drivers/dandong.mp4
    enhance: { fpsInterp: "48" }     # ghi đè defaults
```

Chỉ `inputs` là bắt buộc. Không ghi param nào thì chạy đúng mặc định như bấm UI.

Tên run tự sinh từ tên material (`<character>__<outfit>__<driver>`, đã bỏ đuôi và làm sạch), nên
nhìn file kết quả là biết ai mặc gì nhảy theo cái gì mà không phải đặt tên gì. Trùng thì thêm `-2`.

## 4. Pipeline là dữ liệu

```yaml
motion-enhance:       [motion, enhance]
tryon-motion-enhance: [tryon, motion, enhance]
```

Mỗi chặng khai job type + ánh xạ field. Ánh xạ khớp đúng tên field worker đọc — đã đối chiếu từng
cái ngày 18/08/2026:

| Chặng | Field gửi | Worker đọc ở |
|---|---|---|
| `tryon` | `model`, `product`, `product2`, `background` | `worker/worker_runtime/linux.py:4734,4735,4744,4765` |
| `motion` | `ref`, `motion` | `scripts/pod-smoke.sh:294-295` |
| `enhance` | `input` | `worker/worker_runtime/linux.py:9544` |

Thêm pipeline mới = thêm một dòng, không sửa runner.

### Byte đi vòng qua máy local

`POST /jobs` dựng `inputs` **chỉ từ file upload** (`api/src/routes/jobs.js:118-129`) — không có
đường trỏ tới key MinIO có sẵn. Nên nối chặng bắt buộc: tải output chặng trước về máy, upload lại
cho chặng sau.

Đo trên `ab-results/run1/` (output motion thật, 81–161 frame): 552 KB – 918 KB. Tryon ~1,4 MB (số
ghi trong `pod-smoke.sh:48`). Vài MB mỗi chặng, vài giây mỗi lần — không đáng đổi kiến trúc, và
**không** đề xuất sửa API để nhận key MinIO. Ghi lại ở đây để lần sau ai thấy vòng lặp
download→upload thì biết nó là quyết định chứ không phải sơ suất.

Ngưỡng khiến quyết định này sai: output vượt ~200 MB/chặng, hoặc uplink dưới ~5 Mbps. Lúc đó mở lại
bằng cách cho `POST /jobs` nhận `inputs` dạng key — nhưng phải giải bài phân quyền trước, vì key là
chuỗi đoán được và hiện không có kiểm chủ sở hữu.

## 5. Vòng đời pod

```
preflight  pod đang chạy?  → tiếp
           pod đang stop?  → make gpu-up, đợi /health
           chưa có pod?    → DỪNG, in "chạy make gpu-provision" (không tự thuê máy)
validate   toàn bộ manifest, TRƯỚC khi submit job đầu tiên
chạy lô    tuần tự
kết thúc   in bảng + thời gian GPU đã dùng + lệnh destroy sẵn để copy
           tự destroy CHỈ KHI DESTROY_WHEN_DONE=yes VÀ không run nào fail
```

Bật là thao tác đảo được, xoá thì không — nên bật thì tự làm, xoá thì phải xin phép. Điều khoản
"không destroy khi có run fail" là cố ý: lô hỏng giữa chừng chính là lúc cần pod nhất để đọc
`pm2 logs worker`. Job và MinIO vẫn sống trên Network Volume sau destroy, nhưng phải dựng lại pod
~5 phút mới xem được.

Chạy tuần tự, không song song: pod có một GPU, và `run_enhance` gọi `comfy_recycle` để xả RAM/VRAM
của Wan trước mỗi pha nặng (`linux.py:9586` lanczos, `9650` SeedVR2, `9691` FlashVSR, `9740` RIFE) —
hai job chồng nhau sẽ phá đúng giả định "lúc này GPU chỉ có mình tôi" mà các lời gọi đó dựa vào.

## 6. Output

```
out/
  latest -> 2026-08-18-1430
  2026-08-18-1430/
    _final/
      mauA__vay-do__dandong.mp4
      mauB__dandong.mp4
    _index.tsv
    manifest.yaml
    runs/
      mauA__vay-do__dandong/
        01-tryon.png
        02-motion.mp4
        03-final.mp4          hardlink với _final/…
        run.json
        run.log
```

- **`_final/` tách khỏi `runs/`**: giao việc thì chỉ cần bản cuối, debug thì cần cả chuỗi. Hardlink
  nên không nhân đôi dung lượng.
- **Giữ file trung gian**: khi kết quả cuối xấu, câu hỏi đầu tiên luôn là "tryon ra ảnh gì". Không
  giữ thì phải chạy lại cả chuỗi để trả lời.
- **`run.json` ghi param THẬT đã gửi**, không phải param trong manifest. API có ép giá trị:
  `jobs.js:110-113` ép `detailUpscale=false` cho mọi job motion bất kể client gửi gì;
  `enforceMotionResolution` và `enforceTaskCloudEnhancePolicy` cũng nắn params trước khi ghi DB.
  Ghi lại cái thật là cách duy nhất để sáu tuần sau còn giải thích được vì sao hai lô khác nhau.
- **`manifest.yaml` là bản sao** đã dùng, có kết quả ghi ngược. File gốc trong `batch/` cũng được
  cập nhật để `--resume` chạy được.

Đĩa: `make batch-clean KEEP=3` xoá `runs/` của các lô cũ hơn 3 lô gần nhất. `_final/` không bao giờ
động tới. `out/` vào `.gitignore`.

## 7. Không biết param thì tra ở đâu

```bash
make batch-params TYPE=motion
```

Sinh từ AST của `worker/worker_runtime/linux.py`: tên key, giá trị mặc định, và `file:line` chỗ đọc
nó để nhảy thẳng vào comment gốc — repo này comment rất dày, đó là tài liệu thật. Nguyên mẫu đã
chạy thử ngày 18/08/2026:

```
run_motion  : 39 key   (preset, frames, render_fps, steps, width, height,
                        faceCropMode, poseRetarget, motionDeflicker, sharpen, …)
run_tryon   : 14 key   (provider, garmentType, cleanOnly, feetCrop, extraPrompt, …)
run_enhance :  7 key   (engine, targetRes, mode, allowFallback, …)
```

**Extractor có lỗ, và lỗ đó phải được khai chứ không được giấu.** Param đọc động thì AST không
thấy — ví dụ `fpsInterp`, đọc qua
`next((k for k in ("fpsInterp","fps_interp","fpsTarget") if k in params))` (`linux.py:9569`), tức
là **đúng cái param quan trọng nhất của quy trình này** (48/60 fps) lại vô hình với extractor. Nên:

- `scripts/batch-params.json` khai tay phần đó + giá trị hợp lệ (`fpsInterp: "" | 30 | 48 | 60`,
  `targetRes: 1080p | 2k`, `engine: flashvsr | seedvr2`).
- `make check-batch-params` đỏ khi `linux.py` thêm param mà file khai tay chưa có — cùng cơ chế
  với `scripts/check-job-types.mjs` đã có, và cùng lý do: danh sách chép ở hai chỗ thì lệch nhau
  im lặng.

### Validate là lớp chống lỗi đắt nhất

Code nhận **cả hai** kiểu viết: `targetRes` và `target_res`, `faceCropMode` và `face_crop_mode`,
`cleanOnly` và `clean_only`. Gõ sai một biến thể thứ ba thì `params.get()` trả `None`, job **vẫn
chạy**, **vẫn tính tiền**, và ra kết quả của giá trị mặc định. Không có lỗi, không có log, không ai
biết cho tới khi so hai video thấy giống nhau.

Nên validate chặn key lạ kèm gợi ý key gần đúng, và chạy **trước khi submit job đầu tiên** của cả
lô — không phải trước từng job. Manifest sai ở run thứ 9 phải nổ trước khi run thứ nhất tiêu GPU.

## 8. Sai sót

| Tình huống | Xử lý |
|---|---|
| Manifest sai (file thiếu, key lạ, pipeline không có) | Chặn ở validate, chưa tiêu GPU |
| Một run fail | Ghi `status: error` + lý do, sang run kế. `--fail-fast` để đảo |
| Chặng chạy quá lâu | Timeout riêng từng chặng (enhance 60fps lâu hơn motion nhiều) |
| Rớt mạng lúc poll | Retry có backoff; job vẫn chạy trên pod |
| Máy ngủ / Ctrl-C giữa lô | `--resume` bắt lại bằng `job_id` đã ghi trong journal |
| Lô xong nhưng có run fail | Không tự destroy dù `DESTROY_WHEN_DONE=yes` |

Journal ghi **sau mỗi chặng**, không phải cuối run. Một run ba chặng đứt ở chặng ba thì resume chỉ
chạy lại chặng ba.

## 9. MCP (giai đoạn 2)

`scripts/batch_mcp.py` bọc mỏng CLI, không tự cài đặt logic:
`batch_validate` · `batch_run` (chạy nền, trả run id) · `batch_status` · `batch_rerun`.

Làm sau khi CLI đã chạy thật ít nhất một lô. Lý do: lô chạy 30–60 phút, nếu logic nằm trong MCP thì
nó chết theo phiên chat, và debug qua một lớp giao thức khó hơn hẳn đọc một file log.

## 10. Kiểm chứng

**Không cần pod** — parse/validate manifest, resolve pipeline, sinh tên run, ghép `pair`/`cross`,
logic resume đọc journal dở.

**Cần pod thật** — `.smoke/` đã sẵn `nhanvat.jpeg` + `sanpham.jpeg` + `dandong.mp4`, đủ dựng một
manifest smoke 2 run (một `tryon-motion-enhance`, một `motion-enhance`) chạy được ngay mà không
phải đi kiếm material.

**Cổng** — `make check-batch-params`; và `motions-studio/setup/scrub-secrets.sh --check` phải xanh
trước mọi commit, nghĩa là manifest mẫu trong repo không được chứa domain thật hay đường dẫn cá
nhân.

## Tiền lệ trong repo

`ab-results/runner.sh` + `run1/manifest.tsv` (17/08/2026) đã làm đúng vòng submit → poll →
download → ghi manifest, cho 6 run A/B, và nó chạy được. Nhưng nó hardcode domain, cổng SSH, IP,
đường dẫn material, và từng run; comment đầu file tự gọi mình là "one-off resume-runner".

Spec này là bản tổng quát của đúng file đó. Phần mới so với nó: validate trước khi tiêu GPU, nối
nhiều chặng, resume theo chặng, và layout output phân biệt được.

`scripts/pod-smoke.sh:184-250` (`run_and_check_job`) là bản mẫu gần nhất cho vòng submit → poll →
download, kể cả các bẫy đã trị: `set -u` với biến chỉ gán trong vòng lặp, và floor kích thước file
để bắt "job báo done nhưng MinIO trả về rỗng".
