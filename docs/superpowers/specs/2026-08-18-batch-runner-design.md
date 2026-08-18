# Chạy lô material từ máy local → video trên pod GPU — Design

**Ngày:** 18/08/2026 · **Trạng thái:** đã triển khai và **đã nghiệm thu trên pod RTX 5090 thật**
(RunPod EU-RO-1, ~55 phút, $0.99/giờ). Số đo, không phải lời hứa:

| | |
|---|---|
| Lô đầy đủ `character+outfit+background+driver` | tryon 349s (1688 KB) → motion 144s (480 KB) → enhance 79s (3224 KB) |
| Nền có được ghép thật? | Có — job tryon chạy pass "ghép nền (Qwen pass 2)" |
| Enhance đúng đích? | 544×960@16fps → **1088×1920@48fps**, 97 frame, giữ đúng 2,02s |
| `--resume` bắt lại job cũ? | Có — ngắt giữa motion, resume in `✓ BẮT LẠI được job cũ fddb9ec7…`, API xác nhận **chỉ 2 job motion**, không job trùng |
| `params_sent` là param API đã nắn? | Có — dòng motion có `{"hq": false, "cfg": 1, "steps": 4, "fitDriver"…}`, không cái nào manifest gửi |
| ViTPose hay fallback DWPose? | ViTPose — log job ghi `face_crop=vitpose`, không có dòng fallback nào |

**Lô NHIỀU RUN đã chạy thật (18/08/2026, pod thứ ba).** Trước đó mọi lần chạy đều là `[1/1]` —
cả sản phẩm tên là batch runner mà vòng lặp nhiều run chưa từng chạy trên pod. Đã bịt:

| Kiểm | Kết quả |
|---|---|
| Vòng lặp nhiều run | `[1/3] [2/3] [3/3]` · `2 xong · 1 hỏng · ~7 phút GPU` |
| Một run hỏng KHÔNG giết lô | run B hỏng (mp4 rác), run C vẫn chạy và xong |
| `_index.tsv` nhiều run | 5 dòng/3 run, **giữ cả dòng chặng đã làm B dừng** |
| `_final/` | đúng 2 video (A, C), không có B · cả hai 1088×1920@48fps |
| `--fail-fast` | dừng ngay sau B, `[3/3] C` không bao giờ chạy |
| Reattach job HỎNG THẬT | log: "job cũ … đã chạy và HỎNG THẬT — gửi job MỚI" (JobFailed → submit mới, không che lỗi) |
| Nền **xoay vòng** | kiểm MIỄN PHÍ từ manifest: 3 run / 2 nền → `nen-1, nen-2, nen-1` |

Một chi tiết trung thực đáng ghi: `params_sent` của run HỎNG là param của **manifest**, không phải
param API đã nắn — vì job chết trước khi có DTO để đọc. Run xong thì có param thật
(`{"hq": false, "cfg": 1, "steps": 4, …}`). Và thư mục run hỏng chỉ có `run.log`, không có
`run.json` — đúng mục deferred đã ghi ở final review, giờ quan sát được.

**Một lỗi CHẶN TOÀN BỘ chỉ pod thật tìm ra:** Cloudflare chặn User-Agent mặc định của urllib
(§1). 143 test xanh không thấy nó, `pod-smoke.sh` không thấy nó vì dùng `curl`.

**ViTPose đã bake vào image và ĐÃ KIỂM TRÊN POD MỚI (18/08/2026).** Không còn phải clone tay.
Thuê một pod thứ hai với `sha-0cbe433` chỉ để chứng minh, rồi destroy (~8 phút):

```
TRƯỚC bootstrap, chưa thao tác gì tay:
  custom_nodes/                  đủ 10 node, có ComfyUI-WanAnimatePreprocess
  sha của node                   0e0b6a2  (đúng sha ghim)
  deps trong VENV của ComfyUI    onnx 1.22.0 · onnxruntime 1.29.0 · cv2 5.0.0
  import bằng venv python        NODE_CLASS_MAPPINGS đủ 5 class

SAU bootstrap, vẫn không thao tác gì tay:
  /object_info/PoseAndFaceDetection      ✓   (class linux.py:1657 dựng)
  /object_info/OnnxDetectionModelLoader  ✓   } ba tên mà bộ phát hiện fallback
  /object_info/DrawViTPose               ✓   } ở linux.py:4366 tìm
  node vẫn ở 0e0b6a2                     ✓   (bootstrap không xoá nó)
```

Và có cổng chặn tái diễn: `make check-comfy-nodes` giữ bốn danh sách node khớp nhau, kể cả
cổng tự kiểm bên trong `worker-image/Dockerfile` (chỗ đã trôi mà không ai biết). Cả năm phép
kiểm của cổng đều được mutation-test.

Lúc nghiệm thu thì phải: image ghim `sha-14ae224` thiếu `ComfyUI-WanAnimatePreprocess` nên mọi job
motion âm thầm fallback DWPose pad128. Truy nguyên thì fix `3bb2246` (17/08) thêm node vào
`motions-studio/comfyui/Dockerfile` — **file không workflow nào build** — nên nó vô hiệu 100% từ đầu.
Node giờ nằm ở cả bốn danh sách thật (`worker-image/Dockerfile` + cổng tự kiểm của nó,
`worker/runpod/Dockerfile.selfhosted`, `setup-full.sh:62`, `setup-motion-transfer.sh:46`), ghim
`0e0b6a2`, và image đã rebuild: `sha-0cbe433`. `comfyui/Dockerfile` đã dán cảnh báo ở đầu file vì
nó đã lừa được một lần.

**MCP server (§9, giai đoạn 2) đã làm xong** — `scripts/batch_mcp.py` + `batchlib/rpc.py` +
`batchlib/mcp_tools.py`, bốn tool, **0 dependency mới**, 48 test không cần pod. Đã bắt tay thật qua
stdio (`make batch-mcp-check` in đủ bốn tool) và đã đăng ký ở `.mcp.json`. Chưa chạy một lô thật
QUA MCP — phần đó phải đợi lần thuê pod tới, và đây là chỗ ghi ra để không ai tưởng nó đã được
chứng minh.

Năm chỗ bản giao làm KHÁC spec một cách có chủ ý được ghi thẳng vào đúng mục liên quan, dạng trích
dẫn "ĐỔI CÓ CHỦ Ý": `MODE=folders` (§2), `DESTROY_WHEN_DONE` (§5/§8), tên file chặng cuối (§6),
định danh bằng đường dẫn manifest thay vì run id (§9), MCP không tự bật pod (§9). Spec là tài liệu
ràng buộc, nên nó không được phép nói ngược với tool đang chạy.

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
sửa API trên pod; thêm job type mới. MCP server (§9) là giai đoạn 2 và **đã làm xong** sau khi CLI
chạy thật một lô nhiều run trên pod.

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
| `batch/<tên>.yaml` | Dữ liệu **của bạn**. Khai run + param. Runner không bao giờ ghi vào file này. | — |
| `batch/<tên>.state.json` | Dữ liệu **của máy**. Journal: job id, trạng thái, file từng chặng. | manifest |
| `scripts/batch_scan.py` | Đọc 4 thư mục vai trò → đẻ manifest nháp | — |
| `scripts/batch_run.py` | preflight → validate → chạy tuần tự → journal → tổng kết | manifest, `.env` |
| `scripts/batch_params.py` | Rút param từ `linux.py` bằng AST + file khai tay | `linux.py` |
| `scripts/batch-params.json` | Khai tay phần AST không thấy + giá trị hợp lệ | — |
| `scripts/batch_mcp.py` | Entry MCP: nối giao thức với tool (§9) | hai file dưới |
| `scripts/batchlib/rpc.py` | JSON-RPC/stdio. Biết giao thức, **không biết batch** | — |
| `scripts/batchlib/mcp_tools.py` | Bốn tool. Biết batch, **không biết giao thức** | manifest, `batch_run.py` |
| `batch/<tên>.mcp.json` `.log` `.rc` | Dữ liệu **của máy**: pid/argv/log/mã thoát của lượt chạy nền | — |

> **RÀNG BUỘC ĐO ĐƯỢC (18/08/2026, pod thật): mọi request PHẢI đặt User-Agent.**
> Pod nằm sau Cloudflare Tunnel — đó là cả kiến trúc của repo ([gpu-pod.md](../../gpu-pod.md)) —
> và Cloudflare CHẶN User-Agent mặc định của urllib. Cùng một URL `/health`, cùng lúc:
> ```
> curl                     → HTTP 200
> urllib (UA mặc định)     → HTTP 403  "error code: 1010"
> urllib + UA bất kỳ khác  → HTTP 200
> ```
> Bỏ header đó thì runner KHÔNG nói được với pod chút nào: preflight, submit, poll, download đều
> 403. Đây là lỗi **chỉ pod thật phơi ra được** — toàn bộ 143 test vẫn xanh vì chúng bắn vào
> `http.server` giả trên `127.0.0.1`, nơi không có Cloudflare. `pod-smoke.sh` không bao giờ gặp nó
> vì nó dùng `curl`. Ghim bằng `TestUserAgent` trong `test_batch_client.py`.

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

> **ĐỔI CÓ CHỦ Ý (18/08/2026): `MODE=folders` KHÔNG được triển khai** — bản giao chỉ có
> `MODES = ("pair", "cross")` (`scripts/batchlib/scan.py:30`). Lý do: material đã gom sẵn theo run
> thì viết thẳng `inputs:` trong manifest cũng chỉ mất mấy dòng, mà thêm một chế độ nạp là thêm một
> mặt tiếp xúc phải test và phải giải thích. Mở lại khi có ai thật sự cần.

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

> **ĐỔI CÓ CHỦ Ý (18/08/2026): `DESTROY_WHEN_DONE=yes` KHÔNG được triển khai** — runner không bao
> giờ tự `gpu-destroy`, kể cả khi mọi run đều xong ([gpu-pod.md](../../gpu-pod.md) đã ghi đúng hành
> vi này: "Runner tự `make gpu-up` nếu pod đang dừng, nhưng **không bao giờ tự `gpu-destroy`**").
> Lý do: destroy là thao tác KHÔNG đảo được, còn "mọi run done" không có nghĩa là
> video dùng được — người dùng còn phải xem đã. Runner in sẵn `make gpu-destroy` để dán, và đó là
> hướng an toàn hơn; spec là chỗ nói dối, không phải code.

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
        03-enhance.mp4        hardlink với _final/…
        run.json
        run.log
```

> **ĐỔI CÓ CHỦ Ý (18/08/2026): file cuối tên `03-enhance.mp4`, không phải `03-final.mp4`** — quy tắc
> `NN-<tên chặng>` áp cho MỌI chặng, kể cả chặng cuối, nên đọc tên file là biết chặng nào sinh ra nó
> (và pipeline không có enhance thì tên vẫn đúng). Bản "final" đã có ở `_final/<run-id>.mp4`. Đổi
> spec chứ không đổi code.

- **`_final/` tách khỏi `runs/`**: giao việc thì chỉ cần bản cuối, debug thì cần cả chuỗi. Hardlink
  nên không nhân đôi dung lượng.
- **Giữ file trung gian**: khi kết quả cuối xấu, câu hỏi đầu tiên luôn là "tryon ra ảnh gì". Không
  giữ thì phải chạy lại cả chuỗi để trả lời.
- **`run.json` ghi param THẬT đã gửi**, không phải param trong manifest. API có ép giá trị:
  `jobs.js:110-113` ép `detailUpscale=false` cho mọi job motion bất kể client gửi gì;
  `enforceMotionResolution` và `enforceTaskCloudEnhancePolicy` cũng nắn params trước khi ghi DB.
  Ghi lại cái thật là cách duy nhất để sáu tuần sau còn giải thích được vì sao hai lô khác nhau.
- **`manifest.yaml` là bản sao đông cứng** của manifest đã dùng, chép nguyên văn (kể cả comment) tại
  thời điểm chạy — để sáu tuần sau còn biết lô đó chạy bằng manifest nào.

**Journal nằm ở file riêng, không ghi ngược vào manifest.** `batch/<tên>.state.json` giữ job id,
trạng thái và đường dẫn file của từng chặng; `--resume` đọc nó. Lý do tách: PyYAML `safe_dump` xoá
sạch comment, mà comment chính là chỗ bạn ghi "preset này cho khách A, đừng đổi". Runner ghi đè
manifest một lần là mất hết, không lấy lại được. Ranh giới: **`.yaml` là của bạn, `.state.json` là
của máy** — máy không bao giờ ghi vào file của bạn.

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
| Lô xong nhưng có run fail | Không tự destroy dù `DESTROY_WHEN_DONE=yes` (xem §5: cờ này không được triển khai — runner KHÔNG BAO GIỜ tự destroy) |

Journal ghi **sau mỗi chặng**, không phải cuối run. Một run ba chặng đứt ở chặng ba thì resume chỉ
chạy lại chặng ba.

## 9. MCP (giai đoạn 2) — **đã triển khai**

`scripts/batch_mcp.py` bọc mỏng CLI, không tự cài đặt logic:
`batch_validate` · `batch_run` (chạy nền) · `batch_status` · `batch_rerun`.

Làm sau khi CLI đã chạy thật ít nhất một lô. Lý do: lô chạy 30–60 phút, nếu logic nằm trong MCP thì
nó chết theo phiên chat, và debug qua một lớp giao thức khó hơn hẳn đọc một file log.

```
Claude Code ──JSON-RPC/stdio──► scripts/batch_mcp.py ──Popen(setsid)──► batch_run.py
                                        │                                    │ (30-60 phút,
                                        └─ đọc batch/<tên>.state.json ◄──────┘  sống qua phiên chat)
```

Server **không giữ trạng thái nào của riêng nó**. Nguồn sự thật vẫn là journal runner ghi sau mỗi
chặng (§6), nên phiên chat chết / server chết thì lô vẫn chạy và lần sau hỏi lại vẫn đúng. Ba file,
ranh giới cứng: `batchlib/rpc.py` biết giao thức mà không biết batch; `batchlib/mcp_tools.py` biết
batch mà không biết giao thức; `batch_mcp.py` chỉ nối hai cái.

Không thêm dependency nào: tự cầm JSON-RPC (`initialize`/`ping`/`tools/list`/`tools/call`, newline-
delimited). Cùng lý do §1 chọn Python — root repo không có `package.json`, và một cây
`pydantic/anyio/httpx` chỉ để đọc bốn method là cái giá không đáng.

> **ĐỔI CÓ CHỦ Ý (18/08/2026): định danh là ĐƯỜNG DẪN MANIFEST, không phải run id** — spec viết
> `batch_run` "trả run id", bản giao trả `pid` + đường dẫn log, và mọi tool nhận `file`. Lý do:
> batch id do `run_batch` mint tại thời điểm chạy (`runner.py: batch_id_now`), MCP không biết trước
> được nó; bịa một id ở đây buộc phải thêm cờ `--batch-id` vào một CLI **đã nghiệm thu trên pod
> thật**. Journal vốn đã khoá theo đường dẫn manifest (`state_path_for`), nên dùng đúng khoá đó là
> nhất quán với phần còn lại. `batch_status(file)` trả `lo` = batch id thật ngay khi runner ghi nó.

> **ĐỔI CÓ CHỦ Ý (18/08/2026): MCP KHÔNG tự `make gpu-up`, CLI thì có** — §5 cho preflight tự bật
> pod vì "bật là thao tác đảo được". Qua MCP thì mặc định `--no-start`, phải gọi kèm
> `allow_start=true` mới bật. Đây là chỗ duy nhất hai đường cố ý xử sự khác nhau: CLI là bạn gõ,
> MCP là một model gọi, và bật pod bắt đầu tính tiền ngay. Mô tả của `batch_run`/`batch_rerun` nói
> thẳng "TIÊU TIỀN GPU" vì mô tả tool là thứ duy nhất model đọc trước khi quyết định gọi.

**`batch_rerun` là tool duy nhất không có bản CLI**, và đó là lý do nó tồn tại: `RESUME=1` cố ý bỏ
qua run `status == "done"` (§8), nên khi job chạy xong mà **video nhìn xấu** — không lỗi, chỉ là
không dùng được — CLI không có đường nào bắt nó làm lại. Cách làm: xoá đúng entry của run đó khỏi
journal rồi chạy `--resume`; các run khác giữ nguyên `done` nên không ai bị làm lại. Từ chối trước
khi đụng journal nếu đang có lô chạy, và validate trước khi xoá — xoá xong mới phát hiện manifest
sai là mất bản ghi của một run đã chạy xong, đổi lấy không gì cả.

Hai cửa chặn, cùng một lý do §5 ("pod có một GPU"): **manifest sai thì không spawn process nào**, và
**đang có lô chạy cho cùng manifest thì từ chối lô thứ hai** — hai runner cùng ghi một `state.json`
là hỏng journal, và hai job chồng nhau phá đúng giả định "lúc này GPU chỉ có mình tôi" mà
`comfy_recycle` dựa vào.

### Ba cái bẫy chỉ lộ ra khi viết

| Bẫy | Hậu quả nếu không trị | Ghim bằng |
|---|---|---|
| Một dòng không-JSON lọt lên stdout | **rụng cả kết nối MCP**, không phải một dòng log thừa | `test_khong_mot_byte_rac_nao_tren_stdout` chạy entry point thật rồi parse từng dòng |
| Runner là **con** của server → thoát mà không ai `wait()` là thành **zombie**, `os.kill(pid,0)` báo "còn sống" vĩnh viễn | kill lô rồi hỏi lại thì được trả lời sai, mãi mãi | `_con_song` reap bằng `WNOHANG` trước; test kill thật rồi đòi trạng thái đổi trong 3s |
| pid biến mất giống hệt nhau dù lô xong sạch hay ngã ở run đầu | không trả lời được "lô kết thúc thế nào" | bọc `sh -c '…; echo $? > <.rc>'`, `batch_status` trả `ma_thoat` |

Lỗi **tool** trả `isError` + câu nói làm gì tiếp; chỉ lỗi **giao thức** mới là JSON-RPC `error`.
Trộn hai cái nghĩa là một dòng YAML sai cũng làm rụng kết nối, và người dùng phải khởi động lại
phiên chat để sửa nó.

### Kiểm chứng

48 test, không cần pod, không tốn đồng nào (`make batch-test`, đã tự vào cổng vì khớp
`test_batch_*.py`). Spawn nền test được bằng cách **tiêm đường dẫn runner** (`Ctx.runner`) trỏ vào
một script giả — nhờ vậy kiểm được cả ba thứ khó: con có thật sự tách session không
(`os.getpgid`), log có chảy ra file không, mã thoát có đọc lại được sau khi con đã chết không.
Bốn nhánh lỗi được viết trước test đã **mutation-test** để chứng minh test không mù.

`make batch-mcp-check` bắt tay thật với server rồi in bốn tool nó khai — cổng rẻ nhất cho câu
"server còn chạy được không".

Ngoài phạm vi, cố ý: `batch_scan` qua MCP; `gpu-provision`/`gpu-destroy` qua MCP (một cái tiêu tiền,
một cái không đảo được — cả hai phải là người gõ ở terminal).

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
