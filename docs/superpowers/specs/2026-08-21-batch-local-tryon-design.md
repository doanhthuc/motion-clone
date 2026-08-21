# Try-on chạy local trước, pod chỉ để motion + enhance — Design

**Ngày:** 21/08/2026 · **Trạng thái:** thiết kế, chưa triển khai.

Tài liệu liên quan: [`2026-08-18-batch-runner-design.md`](2026-08-18-batch-runner-design.md) (kiến trúc
batch runner hiện tại — spec này là một thay đổi TRÊN NỀN đó, không viết lại từ đầu).

## Mục tiêu

`tryon` với `provider: gemini` đã là một API call thuần — worker tự comment rõ điều này
(`linux.py:4782`: "gemini/huggingface là API call thuần — KHÔNG upload ComfyUI"). Nhưng batch runner
hiện tại vẫn gửi chặng đó như MỘT JOB TRÊN POD, nên pod phải sống suốt lúc chờ Gemini trả ảnh dù
không có tí GPU nào được dùng cho chặng này. Cùng lúc đó `motion`/`enhance` luôn cần GPU thật.

Đổi luồng: chạy **toàn bộ chặng try-on dùng API** (Gemini, và sau này một API Qwen lưu trữ trên
cloud — tạm gọi "qwen-max", KHÔNG phải `provider: qwen` tự host hiện có) **từ máy local, trước khi
đụng tới pod**. Pod chỉ cần sống cho `motion` + `enhance`, và cho try-on tự host (`provider: qwen`,
compute GPU thật, vẫn phải qua pod).

**Ngoài phạm vi:** đổi định dạng manifest (không cần — `provider: gemini` đã hợp lệ từ trước); tự
động `gpu-provision` (vẫn là quyết định tiêu tiền của người dùng, không đổi); implement đầy đủ
"qwen-max" (chưa có endpoint/key thật — chỉ định nghĩa interface, xem §3).

## 1. Mô hình thực thi

Batch runner hiện tại: preflight (đòi pod sống) → chạy tuần tự MỌI chặng của MỌI run trên pod.

Đổi thành hai pha trong **cùng một lần gọi** `make batch` / `batch_run`:

- **Pha A — try-on local (không cần pod).** Trước preflight, quét cả manifest tìm run có chặng
  `tryon` dùng provider API (`gemini` bây giờ, `qwen-max` sau). Chạy các call đó thẳng từ máy local
  vào API của provider, qua một pool đồng thời có giới hạn (mặc định 4), ghi ảnh ra thẳng
  `runs/<id>/01-tryon.png` và ghi journal `done` — đúng layout file như thể nó chạy trên pod.
- **Cổng kiểm pod.** Sau Pha A, kiểm xem phần CÒN LẠI của manifest có thật sự cần pod không
  (`motion`/`enhance` luôn cần; try-on tự host `provider: qwen` cũng cần). Nếu có, chạy preflight
  như cũ (pod sống / tự `gpu-up` / bảo `gpu-provision`). Pod chưa sẵn sàng thì DỪNG ở đây — kết quả
  Pha A đã nằm an toàn trên đĩa và trong journal, thuê pod xong thì chạy tiếp bằng `RESUME=1`.
- **Pha B — chặng trên pod.** Đúng vòng lặp tuần tự từng run/từng chặng hiện tại, không đổi, trừ
  việc chặng đã xong ở Pha A được nhận ra là `done` và bỏ qua (chuyển tiếp output sang chặng sau)
  thay vì gửi job lại.

Kết quả: vẫn MỘT lệnh `make batch`. Gọi trước khi thuê pod → làm hết phần sinh ảnh API miễn phí,
báo đi thuê pod, rồi tiếp đúng chỗ dừng khi gọi lại. Pod đã sẵn sống thì chảy thẳng từ Pha A sang
Pha B, không cần bước phụ nào.

Try-on tự host (`provider: qwen`) không đổi gì — vẫn là GPU thật, vẫn nằm ở Pha B, gửi job cho pod
như hiện tại. Một manifest được phép trộn cả hai loại run trong cùng một lô.

## 2. Vì sao không làm khác

**Tại sao không sửa API/worker để nhận try-on trực tiếp (bỏ qua bước gửi job)?** Ngoài phạm vi đã
định của batch runner (xem spec §0: "sửa API trên pod" không nằm trong phạm vi) — và job try-on vẫn
hiện đúng trong tab Jobs của app dù chạy kiểu nào, nên không có lý do sửa API chỉ để phục vụ batch
tool.

**Tại sao không dùng lại trực tiếp code Gemini trong `linux.py` (import thay vì port)?** File đó
được viết để chạy BÊN TRONG worker process trên pod — phụ thuộc `api_log`/`api_progress` (cần job
context thật), `comfy_upload` và các global chỉ có nghĩa khi có ComfyUI cạnh đó, dù nhánh gemini
không gọi chúng. Import thẳng kéo theo cả một file worker khổng lồ không chạy được ngoài pod. Port
phần logic thuần (prompt/aspect/API call/postprocess) sang một module local nhỏ, cross-reference
bằng `file:line` — đúng quy ước `pipelines.py` đã dùng cho tên field.

**Tại sao dịch VN→EN bằng Gemini thay vì gọi lại Ollama trên pod?** `_translate_prompt_en`
(`linux.py:88-115`) gọi Ollama CHẠY TRÊN POD (`qwen2.5:7b-instruct` qua `TRANSLATE_URL/api/chat`) —
không phải một API ngoài. Gọi nó từ local nghĩa là vẫn cần pod sống, phá đúng mục tiêu của spec này.
Dùng Gemini (đã cần key cho chính bước try-on) làm một call text-generation riêng, cùng system
prompt (`linux.py:99-102`), cùng cơ chế fail-safe (lỗi → trả nguyên văn, không raise) — tương đương
về chức năng, khác về câu chữ chính xác so với Ollama.

## 3. Thành phần

### `scripts/batchlib/local_tryon.py` (mới)

```python
LOCAL_PROVIDERS = {"gemini"}   # + "qwen-max" khi có endpoint/key thật

def is_local_provider(provider: str) -> bool: ...

def run_local_tryon(run: Run, params: dict, settings: Settings, out_path: Path) -> tuple[int, int]:
    """Trả (elapsed_sec, bytes). Ném JobError/JobFailed khi hỏng — cùng loại lỗi
    runner.py đã xử lý, không thêm loại lỗi mới."""
```

Port từ `linux.py`, mỗi hàm kèm comment trỏ đúng dòng gốc:

| Việc | Nguồn | Ghi chú |
|---|---|---|
| Prompt theo `garment_type`/`garmentType` | `_gemini_tryon_prompt`/`_gemini_tryon_prompt_base` (`linux.py:3538-3545`) | copy nguyên văn |
| Dịch `extraPrompt`/`extra_prompt`/`keepNote` VN→EN | thay Ollama bằng **Gemini text call**, cùng system prompt `linux.py:99-102` | fail-safe: lỗi → giữ nguyên văn |
| Tỉ lệ khung ảnh | `_gemini_aspect` (`linux.py:2692`) | thuần hàm, copy được |
| Gọi ảnh Gemini image-edit | tương đương `_gemini_edit` (`linux.py:3455`) | `requests` trực tiếp tới `generativelanguage.googleapis.com` |
| Ghép nền pass 2 | cùng prompt `_TRYON_BG_POS` (`linux.py:4712`) | chỉ chạy nếu manifest có `background` |
| Hậu kỳ brightness/saturation/resize | `_tryon_postprocess` (`linux.py:4674`) | gọi `ffmpeg` — đã có sẵn trên máy dev |

`qwen-max`: hàm định nghĩa sẵn trong `LOCAL_PROVIDERS`-adjacent switch, ném
`NotImplementedError("qwen-max: chưa có endpoint/key — điền vào local_tryon.py khi có chi tiết DashScope")`.

### `batchlib/config.py`

Thêm `Settings.gemini_api_key`, đọc từ `.env` gốc (`GEMINI_API_KEY`) — cùng cơ chế các field khác
của `Settings` đã dùng. Thiếu key thì báo lỗi MỘT LẦN, TRƯỚC khi spawn bất kỳ worker Pha A nào —
không để N run cùng ném N lỗi giống hệt nhau từ một thread pool.

### `batchlib/runner.py`

- `stage_dest(run, out_dir, stage_name) -> tuple[int, Path]` — tách công thức đường dẫn
  `NN-<chặng>.ext` ra một hàm dùng chung, để Pha A và vòng lặp `run_one` hiện tại tính ra đúng cùng
  một đường dẫn (không lệch nhau).
- `run_local_phase(*, settings, manifest, out_dir, state, state_file, log, pool_size=4, fail_fast=False)`
  — mới. Duyệt mọi run; run nào có chặng `tryon` dùng provider local thì đẩy vào
  `concurrent.futures.ThreadPoolExecutor(max_workers=pool_size)`. Mỗi worker: tính `stage_dest`, gọi
  `local_tryon.run_local_tryon(...)`, cập nhật `entry["stages"]["tryon"]` khi xong
  (`status="done"`, `elapsed_sec`, `file`, `bytes`, **`params_sent = params_manifest`** — local
  execution gửi đúng những gì manifest xin, không có lớp API nào nắn lại giữa đường). Mọi lời gọi
  `save_state` đi qua MỘT `threading.Lock` — nhiều worker ghi journal cùng lúc không được phép
  interleave.
- Bỏ điều kiện `resume and` trong chỗ bỏ-qua-chặng-đã-xong của `run_one`
  (`if resume and recorded.get("status")=="done" and dest.is_file(): ...` →
  `if recorded.get("status")=="done" and dest.is_file(): ...`). An toàn: một lô THẬT SỰ mới thì
  `state["runs"]` khởi tạo rỗng, nên không có gì "done" trước khi Pha A chạy — điều kiện này chỉ có
  tác dụng thêm đúng cho trường hợp Pha A vừa ghi xong trong CÙNG một lần gọi.
- `needs_pod(manifest, state) -> bool` — true nếu còn ít nhất một (run, chặng) chưa xong mà không
  phải try-on local.

### `scripts/batch_run.py::main()`

Thứ tự mới: validate → `load_settings` → `resolve_batch_id` (dời lên sớm hơn — Pha A cần `out_dir`
của batch) → **Pha A** (`run_local_phase`) → `needs_pod()` → preflight (chỉ khi cần) → **Pha B**
(đúng vòng lặp `run_batch` hiện tại, giờ bắt đầu từ những gì Pha A đã để lại trong journal).

### `batchlib/mcp_tools.py`

Không đổi — `batch_run` (MCP tool) chỉ spawn `scripts/batch_run.py` như một subprocess, nên tự động
thừa hưởng hành vi mới.

## 4. Xử lý lỗi & đồng thời

- Một run hỏng ở Pha A (key sai, hết quota, mất mạng) → ghi `status: error` + lý do vào journal và
  `run.log`, giống hệt một chặng hỏng trên pod hôm nay — KHÔNG làm chết cả pool. Run khác vẫn chạy
  tiếp. `--fail-fast` áp dụng cả Pha A: không nhận job MỚI vào pool sau khi có một run lỗi, nhưng
  job đang chạy dở vẫn để xong.
- Thiếu `GEMINI_API_KEY`: chặn TRƯỚC khi tạo thread pool — cùng triết lý "validate trước, tiêu tiền
  sau" của toàn bộ batch runner, áp cho một điều kiện tiên quyết phía client thay vì tiền GPU.
- Đồng thời: `ThreadPoolExecutor` (I/O-bound, threads đủ dùng, không cần multiprocessing). Số luồng
  đọc từ env `LOCAL_TRYON_WORKERS` (mặc định 4) — một tham số vận hành phía client, không phải dữ
  liệu của run nên không nằm trong manifest. `save_state` bọc trong `threading.Lock`.
- Rate limit (Gemini 429...): nổi lên như một lỗi run bình thường (ghi `error`, xử lý y hệt lỗi
  khác) — CHƯA có retry/backoff riêng ở bản này. Cần thêm thì làm sau, không chặn spec này.
- `_index.tsv`: không đổi cột. Dòng try-on local có `job_id` rỗng (không có job pod nào) và
  `params_sent == params_manifest` — dấu hiệu nhìn nhanh biết "chặng này chạy local" khi đọc file
  sáu tuần sau.

## 5. Kiểm chứng

**Không cần pod, không cần key Gemini thật** — test `local_tryon.py` bằng transport `requests` giả
(cùng cách `test_batch_client.py` đã giả tầng HTTP của pod). Test `run_local_phase`: chạy đồng thời
không làm hỏng journal (nhiều worker giả chạy song song, kiểm `state.json` cuối cùng nhất quán), một
run lỗi không chặn run khác, `needs_pod()` đúng cho manifest trộn cả hai loại provider.

**Cần key Gemini thật (không cần pod)** — smoke test gọi thật API Gemini một lần, xác nhận
`local_tryon.run_local_tryon` cho ra ảnh hợp lệ, đối chiếu tay với ảnh cùng input chạy qua pod
(provider gemini hiện tại) để xác nhận prompt/aspect port đúng.

**Cổng:** thêm vào `make batch-test` hiện có — không cần cổng mới, không cần pod, không tốn GPU nào
cho phần test này.

## 6. Tài liệu cần cập nhật

`docs/batch-runner.md`: thêm một mục ngắn giải thích luồng hai pha và cách "gọi một lần → được bảo
đi thuê pod → gọi lại với `RESUME=1`" — theo đúng khuôn dạng tài liệu hiện tại (cách dùng, không
phải lý do thiết kế, lý do nằm ở spec này).
