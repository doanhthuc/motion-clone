# MTC_PREBUILT trên RunPod — Design

**Ngày:** 05/08/2026 · **Trạng thái:** thiết kế, **chưa dựng**. Image `worker-image/Dockerfile`
chưa từng được build thành công lần nào — xem [§Vì sao GHCR luôn trống](#ghcr-trong).

## Mục tiêu

Xoá **~20-35 phút cài phần mềm** khỏi mỗi lần dựng pod GPU mới, bằng cách chuyển việc đó sang CI và
boot từ image dựng sẵn (`MTC_PREBUILT=1`).

Đây là khoản cuối còn lại của chi phí dựng pod. Hai khoản kia đã xoá rồi:

| Chi phí mỗi lần dựng pod | Cách xoá | Trạng thái |
|---|---|---|
| ~33GB tải model | `POD_VOLUME=/workspace` | ✅ đã xoá |
| Build frontend | `FE_BUILD=ci` | ✅ đã xoá |
| **~20-35 phút cài phần mềm** | **`MTC_PREBUILT=1`** | ⬅ spec này |

**Vì sao đáng làm:** `gpu-destroy` giữa các phiên hiện tại phải trả lại 20-35 phút ở lần bật sau,
nên cách rẻ hơn là `gpu-down` — mà pod đã dừng **vẫn tính tiền container disk**
([gpu-pod.md#costs](../../gpu-pod.md#costs)). Prebuilt làm `gpu-destroy` thành thao tác rẻ, đưa chi
phí lúc không dùng về đúng $0,0100/giờ chỉ-volume.

**Ngoài phạm vi:** hình dạng chạy FE+BE ở máy Mac với hàng đợi cục bộ. Nó phụ thuộc một phép đo
khác (mọi media đi qua API — `worker_runtime/linux.py:310,608,718`), và độc lập với spec này.

## Đường dây đã có sẵn — không phải viết mới

Toàn bộ phía runtime đã nối xong từ trước, chỉ chưa bao giờ chạy vì không có image:

| Thành phần | Vị trí | Làm gì |
|---|---|---|
| Truyền cờ xuống pod | `scripts/pod-bootstrap.sh:55,120,160` | đọc `MTC_PREBUILT` từ `.env`, truyền vào setup |
| Rẽ nhánh fast-boot | `setup/lib-feature.sh:828-836` | bỏ `phase_apt`, `phase_app_deps`, `phase_comfyui` |
| Nối dependency | `setup/lib-feature.sh:501-530` | symlink `api-node_modules`, `worker-venv`, `ComfyUI` |
| Cổng chặn | `setup/lib-feature.sh:504` | thiếu `/opt/mtc-prebuilt/.ready` là `die` ngay |
| Tự chữa npm hỏng | `setup/lib-feature.sh:513-522` | chạy thử `require('pg')`, thiếu thì `npm install` lại |
| Chọn image | `scripts/pod-provision.sh:101` | `IMAGE="${IMAGE:-$(env_get POD_IMAGE)}"` |

Thiếu đúng hai thứ: **image chưa tồn tại** và **`POD_IMAGE` đang trống**.

<a id="ghcr-trong"></a>
## Vì sao GHCR luôn trống, và Dockerfile hiện tại không dùng được

`worker-image/Dockerfile` vào repo cùng lần import monorepo (`c789bf7`), thừa kế từ orchestrator
khác ("Task Cloud"), và **chưa có workflow nào build nó**. `.github/workflows/` chỉ có
`build-frontend.yml` và `build-serverless-image.yml`.

Nó cũng chứa ít nhất một lỗi chắc chắn làm vỡ build. Dòng 62-64 lặp qua mọi custom node và
`pip install -r requirements.txt`, nhưng `ComfyUI-FlashVSR_Stable` có trong `requirements.txt` dòng:

```
flash-attn --no-build-isolation; platform_system!="Windows"
```

`--no-build-isolation` là option của **lệnh** `pip install`, không hợp lệ **trong** file
requirements → pip parse lỗi cả file. `Dockerfile.selfhosted:83-92` đã gặp, đã ghi lại, và đã né.

Đối chiếu hai Dockerfile cùng dựng ComfyUI:

| | `worker-image/Dockerfile` (chưa build lần nào) | `worker/runpod/Dockerfile.selfhosted` (đang chạy CI + production) |
|---|---|---|
| ComfyUI lõi | của base, không ghim | ghim commit `3221224` |
| Custom node | `--depth 1` master, **không ghim** | ghim SHA từng node |
| FlashVSR requirements hỏng | **vỡ build** | né, cài danh sách tường minh |
| Frame-Interpolation không có `requirements.txt` | có xử lý | có xử lý |
| Assert torch | chỉ `import` | assert cả version **và** `torch.version.cuda` |

Nên hướng đi là **viết lại** `worker-image/Dockerfile`, bê chuỗi đã chứng minh từ
`Dockerfile.selfhosted`, chứ không phải vá cái cũ.

## Quyết định

### Base image: `runpod/pytorch`

Chốt `runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404` — image mặc định đã kiểm của repo, ship
`/start.sh` (sshd + block) nên thoả [bẫy RunPod §1](../../gpu-pod.md#runpod-gotchas), và
`pod-provision.sh:118` hết cảnh báo.

Base cũ `vastai/comfy` **không** va với bẫy §1 như từng lo — entrypoint của nó bung supervisord kèm
ssh (Dockerfile:95-102). Nhưng nó là image bên thứ ba, và tên gọi gây hiểu nhầm là đang dùng
vast.ai. Đổi base **không** đụng gì tới Network Volume hay model; nhà cung cấp do `GPU_PROVIDER`
quyết, không phải image.

**Hệ quả phải xử lý:** `MTC_PREBUILT=1` bỏ qua `motion_install_best_pytorch`, nên torch **trong
image** là torch cuối cùng chạy trên pod. Base ship torch 2.8.0+cu128; image phải cài đè
**torch 2.12.1 + cu130** cho khớp chuỗi của `Dockerfile.selfhosted` và `MIN_CUDA_VERSION=13.0`, kèm
assert lúc build.

### Profile: `full`, `JOB_TYPE` cắt theo model thật có

`SETUP_PROFILE=motion-transfer` → `full`. Bake **9 node** (`PROFILE=full` của `Dockerfile.selfhosted`),
**giữ Ollama** (`setup-full.sh:56` `NEED_OLLAMA=1`), **thêm venv bg-remover**
(`setup-full.sh:57` `NEED_BG_REMOVER=1`).

Venv bg-remover là khoản mới: `phase_prebuilt_deps` gọi `ensure_bg_remover` ở runtime
(`lib-feature.sh:528`) và comment tự thú *"chậm hơn fast-boot vài phút"*. Với `motion-transfer` thì
`NEED_BG_REMOVER=0` nên không ai để ý; với `full` nó chạy **mỗi lần boot**, ăn đúng vào cái
fast-boot đang mua.

## Số đo thật — volume, 05/08/2026

Đọc bằng pod CPU `runpod/base` gắn volume `wfe86wzkpm`, ~5 phút ở $0,06/giờ (~$0,005), đã xoá pod.
**Đây là đọc đĩa thật, không phải suy từ catalog.** `df` hiển thị 1,4P của cả cụm MooseFS — phải
đọc `du`.

**Đã dùng 76,6 GB / quota 100 GB.** `comfy-models` 72 GB · `ollama-models` 4,4 GB · `minio` 180 MB ·
`pg-backup` 34 MB · `hf-cache` + logs ~4 MB.

Đối chiếu `comfyui/catalog.json` theo tên file:

| Có đủ (8 nhóm · 71,4 GB) | GB | | Thiếu hẳn (5 nhóm · 156,9 GB) | GB |
|---|---|---|---|---|
| Qwen-Image-Edit | 29,0 | | Wan I2V | 43,8 |
| Wan Animate (motion-transfer) | 17,1 | | Wan T2V | 41,5 |
| Wan dùng chung · Text/VAE/CLIP | 12,0 | | LTX-2.3 (node SS) | 37,1 |
| FlashVSR Enhance | 8,6 | | Flux (text→image) | 32,1 |
| Wan InfiniteTalk (talk/lip-sync) | 2,5 | | Wan 2.2 · Distill LoRA 4 bước | 2,4 |
| Wan dùng chung · LoRA | 1,7 | | | |
| Qwen-Image-Edit (tryon) | 0,3 | | | |
| Nâng chất lượng / Upscale | 0,2 | | | |

Ollama chỉ có `qwen2.5:7b-instruct` (4,4 GB). Thiếu `qwen2.5vl:7b`, `nomic-embed-text`, `bge-m3`.

**Còn trống 23,4 GB** — nhỏ hơn mọi nhóm còn thiếu trừ Distill LoRA.

### Chỉ thiếu một model để mở hết luồng ảnh

`catalog-create-image.json` là danh sách chuẩn cho `create-image`/`edit-image`/`tryon`, gồm 6 mục,
và **5 mục đã có**. Thiếu duy nhất **`qwen2.5vl:7b` (5,6 GB)** — `VISION_MODEL` cho tryon Auto nhận
loại đồ; thiếu nó thì tryon rơi về chọn tay.

Flux **không** thuộc catalog này (nó là nhóm riêng: `flux1-dev-fp8`, `flux1-schnell-fp8`, thư mục
`checkpoints`), nên không cần cho ba luồng trên.

**Quyết định: giữ volume 100 GB, chỉ tải thêm `qwen2.5vl:7b`.** Sau đó ~82 GB dùng, ~18 GB trống.
Không đổi $7,1/tháng. Tải bằng pod CPU theo [§preload](../../gpu-pod.md#preload), không phải trên
đồng hồ GPU.

### `JOB_TYPE`: 16 type

21 type của `setup-full.sh:49` trừ 5 type không có model:

| Bỏ | Vì thiếu | Bằng chứng |
|---|---|---|
| `text-to-video` | Wan T2V | nhóm Wan T2V 0/3 file |
| `wan-i2v` | Wan I2V | nhóm Wan I2V 0/3 file |
| `bds` | Wan I2V | `_BDS_WAN_MODELS`: wan2.2 cần HIGH+LOW, fallback wan2.1 cũng thuộc nhóm Wan I2V |
| `ss` | LTX-2.3 | nhóm LTX 0/4 file |
| `video` | LTX-2.3 | `linux.py:9736` — "LTX-2.3 ảnh+prompt → video+audio" |

Giữ: `motion · teen-flycam · trend-tiktok · enhance · create-image · edit-image · tryon · talk ·
face-motion · story-film · product-overlay · concat · reveal · voiceover · subtitle · teaser`

`teaser` chạy suy giảm: thiếu LTX thì tự về Ken Burns — fallback có sẵn, không phải lỗi.

So với `motion-transfer` hiện tại (4 type), đây là **thêm 12 type với 5,6 GB tải thêm**.

> **Chưa xác minh từng đường ống:** `product-overlay`, `face-motion`, `story-film` được xếp vào
> nhóm chạy được dựa trên comment đăng ký pipeline (`linux.py:9734,9744,9748`) và việc model chúng
> cần đã có trên đĩa, **không phải** bằng cách chạy thử. Bước verify 6 sẽ kiểm ít nhất `tryon` và
> `create-image`; ba type kia còn là suy luận.

## Hình dạng image

```
FROM runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404   ← /start.sh: sshd + block
  │
  ├─ [bê từ Dockerfile.selfhosted PROFILE=full — đã chứng minh trong CI]
  │    torch 2.12.1 + cu130 cài đè torch 2.8/cu128 của base
  │    ComfyUI lõi ghim 3221224
  │    9 node ghim SHA · né requirements.txt của FlashVSR · no-cupy cho Frame-Interpolation
  │    sageattention + assert torch version VÀ torch.version.cuda
  │    DWPose / yolox weights
  │
  └─ [chỉ pod mới cần]
       postgresql · minio binary · nodejs 20 + pm2
       ollama ghim v0.32.4
       api-node_modules   (+ kiểm require('pg'),require('pg-types'),require('express') lúc build)
       worker-venv        ← worker/requirements.txt
       bg-remover venv    ← MỚI: bỏ vài phút khỏi MỖI lần boot
       touch /opt/mtc-prebuilt/.ready
```

Giữ `ARG PROFILE` như bản serverless, nhưng CI **chỉ build `full`**. Cần bản `motion` sau này thì
thêm một dòng matrix.

**Ghim `OLLAMA_VERSION` là bắt buộc, không phải cẩn thận thừa.** Dockerfile:22-25 ghi rõ: bản cũ tải
`.tgz`, Ollama đã bỏ định dạng đó, `curl -f` gặp 404 thoát khác 0 → vỡ ngay `RUN` đầu tiên → **đó
chính là lý do GHCR luôn trống**.

## CI

`.github/workflows/build-prebuilt-image.yml`, sao khuôn `build-serverless-image.yml`:

- **phải nằm ở gốc repo** — workflow trong `motions-studio/.github/` là file chết
- dọn disk runner trước (`/usr/share/dotnet`, `/usr/local/lib/android`, `/opt/ghc`,
  `/opt/hostedtoolcache`) — runner chuẩn chỉ còn ~14 GB trống
- login GHCR bằng `GITHUB_TOKEN`, image `ghcr.io/${{ github.repository_owner }}/motion-prebuilt`
- `docker/metadata-action`: `sha-<commit>` luôn có; `latest` **chỉ khi** `github.ref == refs/heads/main`
- `context: motions-studio`, `file: motions-studio/worker-image/Dockerfile`
- trigger: `workflow_dispatch` + push `main` chạm `worker-image/Dockerfile`, `api/package.json`,
  `worker/requirements.txt`, `bg-remover/requirements.txt`, và chính file workflow

**`POD_IMAGE` ghim tag `sha-<commit>`, KHÔNG dùng `latest`** — cùng cái bẫy docs đã ghi cho template
serverless.

## Đổi `.env`

| Dòng | Từ | Thành |
|---|---|---|
| 51 | `MTC_PREBUILT=0` | `MTC_PREBUILT=1` — **chỉ sau khi image build xanh** |
| 117 | `SETUP_PROFILE=motion-transfer` | `SETUP_PROFILE=full` |
| — (thêm mới; `.env` chưa có dòng này, `.env.example:39` có) | `POD_IMAGE=` | `ghcr.io/<owner>/motion-prebuilt:sha-<commit>` |
| — (thêm mới) | `JOB_TYPES_OVERRIDE=` | 16 type ở trên |
| 135 | `DISPATCH_JOB_TYPES=motion,teen-flycam,trend-tiktok,enhance` | 16 type ở trên |

`DISPATCH_JOB_TYPES` chỉ có tác dụng với dispatcher serverless, mà `WORKER_SOURCE=local`; sửa để
khỏi lệch nếu sau này đổi ý.

### `JOB_TYPES_OVERRIDE` — cơ chế phải làm mới

Phát hiện lúc viết plan: **hiện không có đường nào thu hẹp `JOB_TYPES`.** `lib-feature.sh:397` ghi
`set_kv JOB_TYPES "$JOB_TYPE"`, mà `JOB_TYPE` là hằng cứng trong profile — `setup-full.sh:49` khai
đủ 21 type. `DISPATCH_JOB_TYPES` **không** thay được: nó chỉ điều khiển dispatcher serverless.

Nên phải thêm một biến đi từ `.env` gốc → `pod-bootstrap.sh` → `phase_dotenv`:

- chỉ **cắt bớt** được, type ngoài profile là cổng chặn `die` — type ngoài profile nghĩa là thiếu
  custom node, worker chết ở `/prompt` chứ không phải chỉ thiếu model
- bỏ trống = dùng nguyên `JOB_TYPE` của profile

**Không sửa thẳng `setup-full.sh:49`.** Profile khai *phần mềm chạy được gì*; model thiếu là sự thật
*của volume này*. Trộn hai thứ thì volume sau tải đủ model vẫn bị khoá ở 16 type mà không ai nhớ vì sao.

## Kiểm chứng — rẻ trước, đắt sau

| # | Việc | Chứng minh | Chi phí |
|---|---|---|---|
| 1 | CI xanh, đọc dung lượng image trên GHCR | build được, và **rủi ro lớn nhất** lộ ra trước khi thuê máy | $0 |
| 2 | Pod CPU: tải `qwen2.5vl:7b`, xoá pod | luồng ảnh đủ model | ~$0,01 |
| 3 | Pod GPU `POD_IMAGE` + `MTC_PREBUILT=1`: đo **pull + `gpu-bootstrap`** | con số thật so baseline 20-35 phút | ~$0,5 |
| 4 | `torch.cuda.get_device_capability()` trên pod | phải ra `(12, 0)` — prebuilt bỏ bước cài torch khớp driver | — |
| 5 | `make gpu-smoke` đủ 6 lớp, có `SMOKE_REF`/`SMOKE_DRIVER` | sáu mảnh ghép lại đúng | — |
| 6 | Một job `tryon` **và** một job `create-image` | đường mới mở: `qwen2.5vl` + venv bg-remover trong image chạy thật | — |

**Con số quyết định là `pull + bootstrap`, không phải riêng bootstrap.** Dời việc từ pod sang CI vẫn
còn khoản kéo image về.

Đo `runpodctl billing` cho tiền thật, **không** `currentSpendPerHr` — nó trễ ~45 giây và đã một lần
lệch 33×.

## Rủi ro

| Rủi ro | Mức | Xử lý |
|---|---|---|
| **Image quá to → pull ăn hết phần tiết kiệm** | cao | bước verify 1 trả lời trước khi tốn tiền. Không đạt thì bỏ bake Ollama — `phase_ollama:550` tự cài lúc runtime bằng `curl ollama.com/install.sh`, nên mất vài phút boot chứ không mất chức năng |
| torch 2.12.1+cu130 không chạy sm_120 trên base runpod | trung bình | assert lúc build (CI bắt) + bước 4 (pod bắt) |
| `DISK=100` không đủ cho image + OS + PGDATA | trung bình | biết dung lượng ở bước 1 thì tính được; model ở volume nên không đụng |
| GHCR package private → RunPod không pull được | thấp | đặt package public, hoặc thêm registry credential vào pod |
| worker-venv lệch code | thấp | CI auto-build theo path đã đóng phần lớn; **không** có kiểm tự chữa như phía api |

> **Chưa xác minh:** RunPod có cho mở rộng network volume tại chỗ không (nếu sau này cần >100 GB);
> thời gian `gpu-up` từ pod đã dừng; tiền container disk của pod stopped ở `DISK=100`.

## Việc không làm

- **Không** giữ hai Dockerfile song song — viết lại một bản, base `runpod/*`.
- **Không** build bản `motion`; chỉ `full`.
- **Không** mở rộng volume, **không** tải Wan I2V / Wan T2V / LTX / Flux.
- **Không** thêm cổng chặn hash để bắt image lệch code — CI auto-build theo path đã đủ cho nhịp
  dùng hiện tại.
