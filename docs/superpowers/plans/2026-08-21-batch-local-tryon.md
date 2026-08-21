# Try-on chạy local trước, pod chỉ để motion+enhance — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Try-on chạy qua API (`provider: gemini` bây giờ, "qwen-max" sau) chạy thẳng từ máy local
trước khi thuê pod; pod chỉ cần sống cho `motion`/`enhance` và try-on tự host (`provider: qwen`).

**Architecture:** Thêm một "Pha A" (try-on local, bounded thread pool, không cần pod) chạy TRƯỚC
preflight trong `scripts/batch_run.py::main()`. Pha A và Pha B (vòng lặp pod hiện có, không đổi)
chia sẻ CHUNG một `out_dir`/`state`/`state_file` trong cùng một lần gọi `make batch`, qua một hàm
`prepare_batch()` tách ra dùng chung. `run_one()` (Pha B) tự nhận ra chặng Pha A đã làm xong (file +
journal `done`) và bỏ qua, chuyển tiếp output sang chặng sau — không cần biết gì về Pha A.

**Tech Stack:** Python 3 (đã có trong repo), `urllib` (KHÔNG dùng `requests` — máy dev không có thư
viện đó, xem `batchlib/client.py:1-4`), `concurrent.futures.ThreadPoolExecutor`, `ffmpeg`/`ffprobe`
(đã là dependency của batch runner qua `_tryon_postprocess`/`_img_size`), `unittest` + `http.server`
giả (đúng khuôn mẫu `test_batch_client.py` đã dùng cho pod API).

**Spec:** [`docs/superpowers/specs/2026-08-21-batch-local-tryon-design.md`](../specs/2026-08-21-batch-local-tryon-design.md)

## Global Constraints

- KHÔNG dùng thư viện `requests` ở bất kỳ đâu trong `scripts/` — chỉ `urllib` (đo thật 18/08/2026,
  máy dev không có `requests`).
- KHÔNG import `motions-studio/worker/worker_runtime/linux.py` — port logic thuần, cross-reference
  bằng comment `file:line`, đúng quy ước `scripts/batchlib/pipelines.py` đã dùng.
- KHÔNG đổi định dạng manifest — `provider: gemini` đã hợp lệ từ trước (`scripts/batch-params.json`).
- Mọi test mới PHẢI chạy được qua `make batch-test` (glob `test_batch_*.py`, không cần pod, không
  cần API key thật, không cần `ffmpeg`/`ffprobe` thật trừ khi test tự mock `subprocess.run`).
- `qwen-max` (hosted Qwen API): chỉ định nghĩa interface, ném `NotImplementedError` rõ ràng — KHÔNG
  implement thật trong plan này (chưa có endpoint/key).

---

### Task 1: `Settings.gemini_api_key`

**Files:**
- Modify: `scripts/batchlib/config.py`
- Test: `scripts/tests/test_batch_config.py`

**Interfaces:**
- Produces: `Settings.gemini_api_key: str` (default `""`, KHÔNG bắt buộc — `load_settings` không
  raise nếu thiếu, vì không phải batch nào cũng cần try-on local). Đọc từ `.env` gốc, khoá
  `GEMINI_API_KEY` (đúng tên biến pod đã dùng làm fallback, `linux.py:2704-2712`).

- [ ] **Step 1: Viết test cho field mới**

Thêm vào cuối `class TestLoadSettings` trong `scripts/tests/test_batch_config.py`:

```python
    def test_gemini_api_key_co_thi_doc_duoc(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "DOMAIN=api.example.test\nGEMINI_API_KEY=AIzaFakeKeyFakeKeyFakeKeyFake\n",
                   "NUXT_MOTION_API_KEY=mk_x\n")
            s = load_settings(root)
            self.assertEqual(s.gemini_api_key, "AIzaFakeKeyFakeKeyFakeKeyFake")

    def test_thieu_gemini_api_key_van_load_duoc(self):
        # KHÔNG bắt buộc — không phải batch nào cũng cần try-on local (provider gemini).
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "DOMAIN=api.example.test\n", "NUXT_MOTION_API_KEY=mk_x\n")
            s = load_settings(root)
            self.assertEqual(s.gemini_api_key, "")
```

- [ ] **Step 2: Chạy test để thấy nó FAIL**

Run: `python3 -m unittest scripts.tests.test_batch_config -v` (từ repo root)
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'gemini_api_key'`

- [ ] **Step 3: Thêm field vào `Settings` và nạp nó trong `load_settings`**

Trong `scripts/batchlib/config.py`, sửa dataclass `Settings` (dòng 78-86):

```python
@dataclass(frozen=True)
class Settings:
    domain: str
    api_key: str
    instance_id: str
    gemini_api_key: str = ""

    @property
    def base_url(self) -> str:
        return f"https://{self.domain}"
```

Và trong `load_settings()` (dòng 106-110), thêm field vào `return Settings(...)`:

```python
    return Settings(
        domain=domain,
        api_key=api_key,
        instance_id=env_get(root / ".env", "GPU_INSTANCE_ID"),
        gemini_api_key=env_get(root / ".env", "GEMINI_API_KEY"),
    )
```

Không gọi `_reject_whitespace` cho key này — key rỗng là trạng thái hợp lệ (chưa cần try-on local),
khác hẳn `DOMAIN`/`NUXT_MOTION_API_KEY` là bắt buộc.

- [ ] **Step 4: Chạy lại test, xác nhận PASS**

Run: `python3 -m unittest scripts.tests.test_batch_config -v`
Expected: PASS, toàn bộ file (kể cả các test cũ — field mới có default nên không phá constructor
call nào đã có ở nơi khác).

- [ ] **Step 5: Chạy toàn bộ cổng batch-test, xác nhận không phá gì**

Run: `make batch-test`
Expected: PASS toàn bộ (mọi `Settings(domain=..., api_key=..., instance_id=...)` ở các file test
khác vẫn dựng được vì `gemini_api_key` có default).

- [ ] **Step 6: Commit**

```bash
git add scripts/batchlib/config.py scripts/tests/test_batch_config.py
git commit -m "feat(batch): thêm Settings.gemini_api_key cho try-on local"
```

---

### Task 2: `local_tryon.py` — helper thuần (không mạng, không I/O)

**Files:**
- Create: `scripts/batchlib/local_tryon.py`
- Test: `scripts/tests/test_batch_local_tryon.py`

**Interfaces:**
- Consumes: không gì từ task khác.
- Produces (dùng ở Task 3, 4, và `runner.py` sau này):
  - `LOCAL_PROVIDERS: set[str]`
  - `is_local_provider(provider: str) -> bool`
  - `gemini_aspect(dims: tuple[int, int] | None) -> str | None`
  - `valid_gemini_key(key: str) -> bool`
  - `mime_of(path: Path) -> str`
  - `img_size(path: Path) -> tuple[int, int] | None`
  - `GARMENT_LABEL: dict[str, str]`, `GARMENT_MULTI: set[str]`, `GARMENT_SHOES: set[str]`
  - `tryon_extra_clause(extra: str) -> str`
  - `gemini_tryon_prompt_base(gt: str) -> str`
  - `gemini_tryon_prompt(gt: str, extra: str = "") -> str`
  - `TRYON_BG_POS: str`

- [ ] **Step 1: Viết test cho các hàm thuần**

Tạo `scripts/tests/test_batch_local_tryon.py`:

```python
import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib import local_tryon as lt


class TestIsLocalProvider(unittest.TestCase):
    def test_gemini_la_local(self):
        self.assertTrue(lt.is_local_provider("gemini"))
        self.assertTrue(lt.is_local_provider("Gemini"))
        self.assertTrue(lt.is_local_provider("  gemini  "))

    def test_qwen_khong_phai_local(self):
        self.assertFalse(lt.is_local_provider("qwen"))
        self.assertFalse(lt.is_local_provider(""))
        self.assertFalse(lt.is_local_provider(None))

    def test_qwen_max_chua_lam_nen_khong_phai_local(self):
        # Interface đã định nghĩa (§3 spec) nhưng chưa implement — is_local_provider
        # phải trả False cho tới khi thật sự thêm "qwen-max" vào LOCAL_PROVIDERS.
        self.assertFalse(lt.is_local_provider("qwen-max"))


class TestGeminiAspect(unittest.TestCase):
    def test_none_dims_tra_none(self):
        self.assertIsNone(lt.gemini_aspect(None))
        self.assertIsNone(lt.gemini_aspect((0, 100)))

    def test_doc_gan_9_16(self):
        self.assertEqual(lt.gemini_aspect((1080, 1920)), "9:16")

    def test_ngang_gan_16_9(self):
        self.assertEqual(lt.gemini_aspect((1920, 1080)), "16:9")

    def test_vuong(self):
        self.assertEqual(lt.gemini_aspect((1000, 1000)), "1:1")


class TestValidGeminiKey(unittest.TestCase):
    def test_key_dung_dinh_dang(self):
        self.assertTrue(lt.valid_gemini_key("AIza" + "x" * 35))

    def test_key_rong_hoac_sai_tien_to(self):
        self.assertFalse(lt.valid_gemini_key(""))
        self.assertFalse(lt.valid_gemini_key("sk-" + "x" * 35))

    def test_key_co_khoang_trang_bi_tu_choi(self):
        self.assertFalse(lt.valid_gemini_key("AIza xyz" + "x" * 30))


class TestMimeOf(unittest.TestCase):
    def test_cac_duoi_biet(self):
        self.assertEqual(lt.mime_of(Path("a.png")), "image/png")
        self.assertEqual(lt.mime_of(Path("a.JPG")), "image/jpeg")
        self.assertEqual(lt.mime_of(Path("a.webp")), "image/webp")

    def test_duoi_la_thi_fallback_jpeg(self):
        self.assertEqual(lt.mime_of(Path("a.bmp")), "image/jpeg")


class TestTryonExtraClause(unittest.TestCase):
    def test_rong_tra_rong(self):
        self.assertEqual(lt.tryon_extra_clause(""), "")
        self.assertEqual(lt.tryon_extra_clause(None), "")

    def test_co_noi_dung_thi_boc_cau_uu_tien_cao(self):
        out = lt.tryon_extra_clause("keep the hat")
        self.assertIn("ADDITIONAL USER INSTRUCTION", out)
        self.assertIn("keep the hat", out)


class TestGeminiTryonPrompt(unittest.TestCase):
    def test_auto_khong_bia_do(self):
        p = lt.gemini_tryon_prompt("auto")
        self.assertIn("fashion product photo", p)
        self.assertIn("Do not invent items", p)

    def test_shoes_rieng(self):
        p = lt.gemini_tryon_prompt("shoes")
        self.assertIn("footwear", p)
        self.assertIn("BOTH", p)

    def test_mot_mon_le_dung_label(self):
        p = lt.gemini_tryon_prompt("upper")
        self.assertIn("top or shirt", p)

    def test_luon_khoa_mat(self):
        # Mọi nhánh đều phải có câu khoá mặt đứng ĐẦU (linux.py:3538-3543)
        for gt in ("auto", "shoes", "upper", "set"):
            self.assertTrue(lt.gemini_tryon_prompt(gt).startswith("CRITICAL: the person's face"))

    def test_extra_duoc_chen_vao_cuoi(self):
        p = lt.gemini_tryon_prompt("auto", extra="keep the necklace")
        self.assertIn("keep the necklace", p)
        self.assertLess(p.index("ADDITIONAL USER INSTRUCTION"), len(p))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `python3 -m unittest scripts.tests.test_batch_local_tryon -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'batchlib.local_tryon'`

- [ ] **Step 3: Tạo `scripts/batchlib/local_tryon.py` với các hàm thuần**

```python
"""Try-on chạy TRỰC TIẾP từ máy local, không qua pod — cho provider dùng API thuần
(Gemini bây giờ, "qwen-max" sau khi có endpoint/key thật). KHÔNG import linux.py: file
đó chạy TRONG worker process trên pod, phụ thuộc api_log/comfy_upload dù nhánh gemini
không gọi chúng — port phần logic thuần, cross-reference bằng file:line, đúng quy ước
scripts/batchlib/pipelines.py đã dùng cho tên field.

KHÔNG dùng `requests`: máy dev không có thư viện đó (batchlib/client.py:1-4 đã ghi lại
lý do này) — mọi HTTP call ở module này đi qua `urllib`, giống client.py.
"""
from __future__ import annotations

from pathlib import Path

LOCAL_PROVIDERS = {"gemini"}   # + "qwen-max" khi có endpoint/key thật (xem run_local_tryon)


def is_local_provider(provider: str) -> bool:
    return (provider or "").strip().lower() in LOCAL_PROVIDERS


# linux.py:2688-2696 — Gemini image API tự đổi tỉ lệ output nếu KHÔNG truyền
# imageConfig.aspectRatio → cắt cụt ảnh dọc. Tính tỉ lệ gần nhất từ ảnh gốc để ép giữ khung.
_GEMINI_ARS = {"1:1": 1.0, "2:3": 2 / 3, "3:2": 1.5, "3:4": 0.75, "4:3": 4 / 3,
               "4:5": 0.8, "5:4": 1.25, "9:16": 9 / 16, "16:9": 16 / 9, "21:9": 21 / 9}


def gemini_aspect(dims: tuple[int, int] | None) -> str | None:
    if not dims or not dims[0] or not dims[1]:
        return None
    r = dims[0] / dims[1]
    return min(_GEMINI_ARS, key=lambda k: abs(_GEMINI_ARS[k] - r))


def valid_gemini_key(key: str) -> bool:
    # linux.py:2698-2702 — key Google AI Studio dạng 'AIza…' (~39 ký tự, không khoảng trắng).
    k = (key or "").strip()
    return k.startswith("AIza") and 30 <= len(k) <= 60 and not any(c.isspace() for c in k)


def mime_of(path: Path) -> str:
    # linux.py:3451-3453
    return {".png": "image/png", ".webp": "image/webp", ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg"}.get(path.suffix.lower(), "image/jpeg")


def img_size(path: Path) -> tuple[int, int] | None:
    # linux.py:3176-3185 — (W,H) qua ffprobe, None nếu lỗi (caller fallback an toàn:
    # gemini_aspect(None) trả None, gemini_edit khi đó không ép aspect ratio).
    import subprocess
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", str(path)],
            capture_output=True, text=True, timeout=30)
        w, h = (r.stdout or "").strip().split("x")[:2]
        return int(w), int(h)
    except Exception:
        return None


# linux.py:3129-3146 — chỉ hai tập garment nhánh Gemini thật sự rẽ nhánh (SHOES, MULTI);
# GARMENT_DRESS/GARMENT_REVEAL chỉ dùng ở nhánh Qwen (_qwen_tryon_prompts), không cần ở đây.
GARMENT_LABEL = {
    "upper": "top or shirt", "lower": "pants", "skirt": "skirt", "dress": "dress",
    "gown": "gown", "dam": "dress", "đầm": "dress", "jumpsuit": "jumpsuit",
    "playsuit": "playsuit (one-piece outfit)", "romper": "romper (one-piece outfit)",
    "overall": "overall (one-piece outfit)", "bodysuit": "bodysuit",
    "ao-dai": "Vietnamese ao dai (long dress)", "aodai": "Vietnamese ao dai (long dress)",
    "ao_dai": "Vietnamese ao dai (long dress)", "maxi": "maxi dress",
    "onepiece": "one-piece dress", "one-piece": "one-piece dress",
    "bra": "bra (the single upper undergarment)", "accessory": "accessory",
    "shoes": "shoes/footwear", "giay": "shoes/footwear", "dep": "sandals/footwear",
    "set": "complete two-piece everyday outfit (top and bottom)",
    "full": "complete two-piece everyday outfit (top and bottom)",
    "bo": "complete two-piece everyday outfit (top and bottom)",
    "2pieces": "complete two-piece everyday outfit (top and bottom)",
    "two-piece": "complete two-piece everyday outfit (top and bottom)",
    "bikini": "complete bikini/swimwear set", "swimwear": "complete swimwear set",
    "swimsuit": "complete swimsuit set", "lingerie": "complete lingerie set",
    "underwear": "complete lingerie set", "do-lot": "complete lingerie set",
    "dolot": "complete lingerie set",
}
GARMENT_MULTI = {"set", "full", "bikini", "swimwear", "swimsuit", "lingerie", "underwear",
                 "do-lot", "dolot", "do_lot", "noi-y", "noiy", "bo", "2pieces", "two-piece"}
GARMENT_SHOES = {"shoes", "shoe", "footwear", "giay", "giày", "dep", "dép", "sandal",
                 "heels", "boot", "boots"}


def tryon_extra_clause(extra: str) -> str:
    # linux.py:3199-3204 — "Ghi chú thêm" của user, ưu tiên cao, chèn cuối prompt.
    e = (extra or "").strip()
    if not e:
        return ""
    return ("ADDITIONAL USER INSTRUCTION — high priority, follow this exactly and let it "
            "OVERRIDE the general rules where they conflict (e.g. keep or add the items the "
            f"user names): {e}. ")


def gemini_tryon_prompt_base(gt: str) -> str:
    # linux.py:3545-3570
    label = GARMENT_LABEL.get(gt, "garment")
    if gt in ("auto", "generic"):
        return ("Image 1 is a person; image 2 is a fashion product photo. Edit image 1: dress "
                "the person in EXACTLY the complete fashion look shown in image 2 — reproduce "
                "EVERY fashion item present: clothing (dress, two-piece set, top, bottom, "
                "swimwear, straps, belts, trims), hosiery (thigh-high stockings, tights, socks, "
                "garter belts), footwear (shoes/heels/boots on both feet), and accessories "
                "(jewelry, hat or headband worn without changing the hairstyle, gloves, scarf, "
                "glasses, handbag) — each with the exact color, pattern, fabric and cut. "
                "Do not invent items that are not in image 2. Fully remove the person's original "
                "clothing, hosiery, footwear and accessories wherever the new look covers or "
                "replaces them; keep nothing of the original showing through. Keep identity, "
                "face, hair, body, pose and background identical. Photorealistic; output only "
                "the edited photo.")
    if gt in GARMENT_SHOES:
        return ("Image 1 is a photo of a person; image 2 is a product photo of footwear. Edit "
                "image 1: completely replace the shoes on the person's feet with the EXACT "
                "footwear from image 2 (same color, material, sole, heel and design), on BOTH "
                "feet. Keep the person's face, hair, body, clothing, pose and the background "
                "EXACTLY the same. Photorealistic; output only the edited photo.")
    if gt in GARMENT_MULTI:
        return ("Image 1 is a person; image 2 is a clothing set. Edit image 1: dress the person "
                "in the COMPLETE set from image 2 (every piece), fully removing their original "
                "outfit. Keep identity, pose and background identical. Photorealistic; output "
                "only the edited photo.")
    return (f"Image 1 is a person; image 2 is a {label}. Edit image 1: replace the person's "
            f"{label} with the {label} from image 2 (exact color, pattern, fabric, cut). Keep "
            "other garments, identity, pose and background identical. Photorealistic; output "
            "only the edited photo.")


def gemini_tryon_prompt(gt: str, extra: str = "") -> str:
    # linux.py:3538-3543 — khoá mặt đứng ĐẦU prompt (chống "tryon đổi mặt").
    return ("CRITICAL: the person's face and facial identity must remain EXACTLY identical to "
            "image 1 — same facial structure, eyes, nose, lips, jawline, skin tone, makeup and "
            "expression; never beautify, reshape, swap or regenerate the face. "
            ) + gemini_tryon_prompt_base(gt) + tryon_extra_clause(extra)


# linux.py:4712-4721 — dùng NGUYÊN VĂN cho pass 2 ghép nền. Không cần _TRYON_BG_NEG:
# Gemini image-edit không nhận tham số negative riêng (xem cách gọi ở linux.py:4860).
TRYON_BG_POS = (
    "Take the person from image 1 and place them into the environment shown in image 2. "
    "CRITICAL — keep the person EXACTLY as in image 1: same face, same hair, same body "
    "proportions, same pose, and the SAME OUTFIT with its exact colors, patterns, fabrics and "
    "details; do not restyle, recolor or change any garment, shoe or accessory. "
    "The background must be the location from image 2: reproduce its setting, architecture, "
    "furniture, plants and depth faithfully — do NOT keep any part of the original background "
    "from image 1. Blend the person in naturally: match the lighting direction, color "
    "temperature, perspective and camera height of image 2, and add natural contact shadows "
    "under the person. Photorealistic, seamless composite, no cut-out edges.")
```

- [ ] **Step 4: Chạy lại test, xác nhận PASS**

Run: `python3 -m unittest scripts.tests.test_batch_local_tryon -v`
Expected: PASS toàn bộ.

- [ ] **Step 5: Commit**

```bash
git add scripts/batchlib/local_tryon.py scripts/tests/test_batch_local_tryon.py
git commit -m "feat(batch): local_tryon.py — prompt/aspect/key helper thuần cho try-on local"
```

---

### Task 3: `local_tryon.py` — gọi Gemini qua `urllib` (image-edit + dịch VN→EN)

**Files:**
- Modify: `scripts/batchlib/local_tryon.py`
- Modify: `scripts/tests/test_batch_local_tryon.py`

**Interfaces:**
- Consumes: `JobError` từ `scripts/batchlib/client.py` (đã có: `class JobError(Exception)`).
- Produces:
  - `GEMINI_API_BASE: str`, `GEMINI_IMAGE_MODEL: str`, `GEMINI_TEXT_MODEL: str`
  - `gemini_edit(images: list[tuple[bytes, str]], prompt: str, key: str, out_path: Path, aspect_ratio: str | None = None, model: str | None = None, base_url: str = GEMINI_API_BASE) -> Path` — raise `JobError` khi lỗi/không trả ảnh.
  - `translate_vn_to_en(text: str, key: str, base_url: str = GEMINI_API_BASE) -> str` — KHÔNG BAO GIỜ raise (fail-safe: trả nguyên văn khi lỗi).

- [ ] **Step 1: Viết test bằng server giả (`http.server`), đúng khuôn mẫu `test_batch_client.py`**

Thêm vào `scripts/tests/test_batch_local_tryon.py` (sau các import hiện có, thêm import mới):

```python
import base64
import json
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from batchlib.client import JobError

GEMINI_STATE = {"mode": "image", "calls": 0}


class GeminiHandler(BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def do_POST(self):
        GEMINI_STATE["calls"] += 1
        length = int(self.headers["content-length"])
        self.rfile.read(length)
        mode = GEMINI_STATE["mode"]
        if mode == "http_error":
            self.send_response(400)
            body = b'{"error":"bad request"}'
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if mode == "no_candidates":
            payload = {"candidates": []}
        elif mode == "image":
            fake_png = base64.b64encode(b"fake-png-bytes").decode()
            payload = {"candidates": [{"content": {"parts": [
                {"inlineData": {"mimeType": "image/png", "data": fake_png}}]}}]}
        elif mode == "text":
            payload = {"candidates": [{"content": {"parts": [
                {"text": GEMINI_STATE.get("text_reply", "A cat in a garden.")}]}}]}
        else:
            raise AssertionError(f"mode không rõ: {mode}")
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class GeminiServerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), GeminiHandler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        host, port = cls.server.server_address
        cls.base_url = f"http://{host}:{port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        GEMINI_STATE["mode"] = "image"
        GEMINI_STATE["calls"] = 0


class TestGeminiEdit(GeminiServerCase):
    def test_thanh_cong_ghi_ra_file(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out.png"
            result = lt.gemini_edit([(b"model-bytes", "image/jpeg")], "edit prompt", "AIzafake",
                                    out, base_url=self.base_url)
            self.assertEqual(result, out)
            self.assertEqual(out.read_bytes(), b"fake-png-bytes")

    def test_http_loi_raise_joberror(self):
        GEMINI_STATE["mode"] = "http_error"
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(JobError) as cm:
                lt.gemini_edit([(b"x", "image/png")], "p", "AIzafake", Path(d) / "o.png",
                               base_url=self.base_url)
            self.assertIn("400", str(cm.exception))

    def test_khong_tra_anh_raise_joberror(self):
        GEMINI_STATE["mode"] = "no_candidates"
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(JobError):
                lt.gemini_edit([(b"x", "image/png")], "p", "AIzafake", Path(d) / "o.png",
                               base_url=self.base_url)


class TestTranslateVnToEn(GeminiServerCase):
    def test_khong_co_dau_tieng_viet_thi_khong_goi_mang(self):
        out = lt.translate_vn_to_en("keep the hat", "AIzafake", base_url=self.base_url)
        self.assertEqual(out, "keep the hat")
        self.assertEqual(GEMINI_STATE["calls"], 0)

    def test_rong_thi_khong_goi_mang(self):
        out = lt.translate_vn_to_en("", "AIzafake", base_url=self.base_url)
        self.assertEqual(out, "")
        self.assertEqual(GEMINI_STATE["calls"], 0)

    def test_co_dau_tieng_viet_thi_dich(self):
        GEMINI_STATE["mode"] = "text"
        GEMINI_STATE["text_reply"] = "keep the necklace"
        out = lt.translate_vn_to_en("giữ nguyên vòng cổ", "AIzafake", base_url=self.base_url)
        self.assertEqual(out, "keep the necklace")
        self.assertEqual(GEMINI_STATE["calls"], 1)

    def test_loi_mang_thi_tra_nguyen_van_khong_raise(self):
        GEMINI_STATE["mode"] = "http_error"
        out = lt.translate_vn_to_en("giữ nguyên vòng cổ", "AIzafake", base_url=self.base_url)
        self.assertEqual(out, "giữ nguyên vòng cổ")
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `python3 -m unittest scripts.tests.test_batch_local_tryon -v`
Expected: FAIL — `AttributeError: module 'batchlib.local_tryon' has no attribute 'gemini_edit'`

- [ ] **Step 3: Thêm phần gọi mạng vào `local_tryon.py`**

Nối vào cuối `scripts/batchlib/local_tryon.py`:

```python
import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from .client import JobError

GEMINI_API_BASE = "https://generativelanguage.googleapis.com"
# linux.py:2598 — cùng default. Đổi qua env nếu cần model rẻ hơn.
GEMINI_IMAGE_MODEL = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-3-pro-image")
# Không có model text-only nào có sẵn trong linux.py (bản gốc dùng Ollama, xem
# translate_vn_to_en) — chọn một model Gemini text rẻ, ổn định làm mặc định.
GEMINI_TEXT_MODEL = os.environ.get("GEMINI_TEXT_MODEL", "gemini-2.5-flash")


def _post_json(url: str, query: dict, payload: dict, timeout: int) -> dict:
    full_url = f"{url}?{urllib.parse.urlencode(query)}"
    req = urllib.request.Request(
        full_url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read()[:300].decode("utf-8", "replace")
        raise JobError(f"Gemini API {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise JobError(f"Gemini API không phản hồi: {exc}") from exc


def gemini_edit(images: list[tuple[bytes, str]], prompt: str, key: str, out_path: Path,
                aspect_ratio: str | None = None, model: str | None = None,
                base_url: str = GEMINI_API_BASE) -> Path:
    """Cổng urllib của linux.py:_gemini_edit (3455-3478, bản gốc dùng requests.post)."""
    parts = [{"text": prompt}]
    for data, mime in images:
        parts.append({"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode()}})
    gcfg = {"responseModalities": ["IMAGE"]}
    if aspect_ratio:
        gcfg["imageConfig"] = {"aspectRatio": aspect_ratio}
    url = f"{base_url}/v1beta/models/{model or GEMINI_IMAGE_MODEL}:generateContent"
    data = _post_json(url, {"key": key},
                      {"contents": [{"parts": parts}], "generationConfig": gcfg}, 300)
    for cand in (data.get("candidates") or []):
        for part in ((cand.get("content") or {}).get("parts") or []):
            blob = part.get("inlineData") or part.get("inline_data")
            if blob and blob.get("data"):
                out_path.write_bytes(base64.b64decode(blob["data"]))
                return out_path
    raise JobError(f"Gemini không trả ảnh: {json.dumps(data)[:300]}")


_VN_CHARS = "ăâđêôơưàáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩịòóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵ"


def translate_vn_to_en(text: str, key: str, base_url: str = GEMINI_API_BASE) -> str:
    """Thay linux.py:_translate_prompt_en (88-115): bản gốc gọi Ollama TRÊN POD
    (qwen2.5:7b-instruct qua TRANSLATE_URL) — không gọi được từ máy local. Dùng Gemini
    text-generation (đã cần key cho chính bước try-on), cùng system prompt, cùng
    fail-safe: lỗi / không có dấu tiếng Việt → trả nguyên văn, KHÔNG BAO GIỜ raise."""
    t = (text or "").strip()
    if not t or not any(c in t.lower() for c in _VN_CHARS):
        return text
    sys_msg = ("You are a translator for an image-generation prompt. Translate the user's text "
               "into ONE natural English image prompt. Preserve EVERY detail exactly — "
               "scene/location, lighting, outfit, pose, camera angle, accessories, mood — and do "
               "NOT add, remove or invent anything. Output ONLY the English prompt, no quotes, "
               "no explanation.")
    url = f"{base_url}/v1beta/models/{GEMINI_TEXT_MODEL}:generateContent"
    try:
        data = _post_json(url, {"key": key}, {
            "contents": [{"parts": [{"text": t}]}],
            "systemInstruction": {"parts": [{"text": sys_msg}]},
            "generationConfig": {"temperature": 0.1},
        }, 60)
    except JobError:
        return text
    for cand in (data.get("candidates") or []):
        for part in ((cand.get("content") or {}).get("parts") or []):
            out = (part.get("text") or "").strip().strip('"').strip()
            if out:
                return out
    return text
```

- [ ] **Step 4: Chạy lại test, xác nhận PASS**

Run: `python3 -m unittest scripts.tests.test_batch_local_tryon -v`
Expected: PASS toàn bộ.

- [ ] **Step 5: Commit**

```bash
git add scripts/batchlib/local_tryon.py scripts/tests/test_batch_local_tryon.py
git commit -m "feat(batch): local_tryon.py gọi Gemini image-edit + dịch VN-EN qua urllib"
```

---

### Task 4: `local_tryon.py` — postprocess + `run_local_tryon()` (điểm vào)

**Files:**
- Modify: `scripts/batchlib/local_tryon.py`
- Modify: `scripts/tests/test_batch_local_tryon.py`

**Interfaces:**
- Consumes: `Run` từ `scripts/batchlib/manifest.py` (đã có: `id`, `inputs: dict[str, Path]`,
  `stage_params: dict[str, dict]`), `Settings.gemini_api_key` (Task 1), mọi hàm từ Task 2-3.
- Produces: `postprocess(out_path: Path, params: dict) -> Path`,
  `run_local_tryon(run: Run, params: dict, settings: Settings, out_path: Path) -> tuple[int, int]`
  — trả `(elapsed_sec, bytes)`, raise `JobError` (thiếu input/key) hoặc `NotImplementedError`
  (provider chưa hỗ trợ). Đây là hàm `runner.py` (Task 7) gọi cho mỗi run ở Pha A.

- [ ] **Step 1: Viết test — mock `subprocess.run` (ffmpeg) và mock `gemini_edit`/`translate_vn_to_en`**

Thêm vào `scripts/tests/test_batch_local_tryon.py`:

```python
from unittest import mock

from batchlib.config import Settings
from batchlib.manifest import Run


class TestPostprocess(unittest.TestCase):
    def test_khong_co_tham_so_thi_giu_nguyen_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "in.png"
            p.write_bytes(b"x")
            self.assertEqual(lt.postprocess(p, {}), p)

    def test_co_brightness_thi_goi_ffmpeg(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "in.png"
            p.write_bytes(b"x")
            dst = p.with_suffix(".pp.png")

            def fake_run(cmd, **kwargs):
                dst.write_bytes(b"processed" * 200)   # > 1024 byte để qua ngưỡng
                return mock.Mock(returncode=0)

            with mock.patch("subprocess.run", fake_run):
                out = lt.postprocess(p, {"brightness": 0.2})
            self.assertEqual(out, dst)

    def test_ffmpeg_loi_thi_giu_anh_goc(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "in.png"
            p.write_bytes(b"x")
            with mock.patch("subprocess.run", side_effect=OSError("khong co ffmpeg")):
                out = lt.postprocess(p, {"brightness": 0.2})
            self.assertEqual(out, p)


def _run_gemini(tmp: Path, background: bool = False) -> Run:
    (tmp / "char.jpg").write_bytes(b"char-bytes")
    (tmp / "outfit.jpg").write_bytes(b"outfit-bytes")
    inputs = {"character": tmp / "char.jpg", "outfit": tmp / "outfit.jpg"}
    if background:
        (tmp / "bg.jpg").write_bytes(b"bg-bytes")
        inputs["background"] = tmp / "bg.jpg"
    return Run(id="runA", pipeline="tryon-motion-enhance", inputs=inputs,
              stage_params={"tryon": {"provider": "gemini"}})


class TestRunLocalTryon(GeminiServerCase):
    def _settings(self, key="AIza" + "x" * 35):
        return Settings(domain="x.test", api_key="mk_test", instance_id="i-1",
                        gemini_api_key=key)

    def test_thanh_cong_ghi_ra_out_path(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            run = _run_gemini(tmp)
            out = tmp / "01-tryon.png"
            with mock.patch.object(lt, "GEMINI_API_BASE", self.base_url), \
                 mock.patch.object(lt, "img_size", return_value=(1080, 1920)):
                elapsed, size = lt.run_local_tryon(run, run.stage_params["tryon"],
                                                   self._settings(), out)
            self.assertGreaterEqual(elapsed, 0)
            self.assertEqual(size, out.stat().st_size)
            self.assertTrue(out.is_file())

    def test_co_background_thi_goi_pass_2(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            run = _run_gemini(tmp, background=True)
            out = tmp / "01-tryon.png"
            with mock.patch.object(lt, "GEMINI_API_BASE", self.base_url), \
                 mock.patch.object(lt, "img_size", return_value=None):
                lt.run_local_tryon(run, run.stage_params["tryon"], self._settings(), out)
            self.assertEqual(GEMINI_STATE["calls"], 2)   # pass 1 (thay đồ) + pass 2 (ghép nền)

    def test_thieu_key_raise_joberror(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            run = _run_gemini(tmp)
            with self.assertRaises(JobError):
                lt.run_local_tryon(run, run.stage_params["tryon"], self._settings(key=""),
                                   tmp / "out.png")

    def test_key_sai_dinh_dang_raise_joberror(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            run = _run_gemini(tmp)
            with self.assertRaises(JobError):
                lt.run_local_tryon(run, run.stage_params["tryon"], self._settings(key="sk-not-gemini"),
                                   tmp / "out.png")

    def test_provider_chua_ho_tro_raise_not_implemented(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            run = _run_gemini(tmp)
            with self.assertRaises(NotImplementedError):
                lt.run_local_tryon(run, {"provider": "qwen-max"}, self._settings(), tmp / "out.png")

    def test_thieu_input_bat_buoc_raise_joberror(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            run = Run(id="runA", pipeline="tryon-motion-enhance",
                     inputs={"character": tmp / "char.jpg"},   # thiếu outfit
                     stage_params={"tryon": {"provider": "gemini"}})
            (tmp / "char.jpg").write_bytes(b"x")
            with self.assertRaises(JobError):
                lt.run_local_tryon(run, run.stage_params["tryon"], self._settings(), tmp / "out.png")
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `python3 -m unittest scripts.tests.test_batch_local_tryon -v`
Expected: FAIL — `AttributeError: module 'batchlib.local_tryon' has no attribute 'postprocess'`

- [ ] **Step 3: Thêm `postprocess` + `run_local_tryon` vào `local_tryon.py`**

Nối vào cuối `scripts/batchlib/local_tryon.py`:

```python
import subprocess
import tempfile
import time

from .config import Settings
from .manifest import Run

_RES_LONGEDGE = {"1080p": 1080, "2k": 1440}


def postprocess(out_path: Path, params: dict) -> Path:
    """Thay linux.py:_tryon_postprocess (4674-4704): brightness/saturation/resize qua
    ffmpeg. KHÔNG dùng api_log — lỗi bị nuốt về ảnh gốc, giống hệt hành vi worker gốc."""
    try:
        bright = max(-0.5, min(float(params.get("brightness") or 0), 0.5))
    except (TypeError, ValueError):
        bright = 0.0
    try:
        sat = max(0.5, min(float(params.get("saturation") or 1.0), 2.0))
    except (TypeError, ValueError):
        sat = 1.0
    res = str(params.get("outputRes") or params.get("resolution") or "").lower().strip()
    target = _RES_LONGEDGE.get(res)
    vf = []
    if abs(bright) > 0.005 or abs(sat - 1.0) > 0.01:
        vf.append(f"eq=brightness={bright:.3f}:saturation={sat:.3f}")
    if target:
        vf.append(f"scale='if(gte(iw,ih),{target},-2)':'if(gte(iw,ih),-2,{target})':flags=lanczos")
    if not vf:
        return out_path
    dst = out_path.with_suffix(".pp.png")
    try:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(out_path), "-vf", ",".join(vf),
                        str(dst)], check=True, timeout=300)
        if dst.is_file() and dst.stat().st_size > 1024:
            return dst
    except Exception:
        pass
    return out_path


def run_local_tryon(run: Run, params: dict, settings: Settings, out_path: Path) -> tuple[int, int]:
    """Điểm vào Pha A cho MỘT run. Trả (elapsed_sec, bytes). Ném JobError khi hỏng cấu
    hình/mạng, NotImplementedError khi provider chưa có endpoint thật (vd qwen-max)."""
    started = time.time()
    provider = str(params.get("provider") or "").lower().strip()
    if provider != "gemini":
        raise NotImplementedError(
            f"provider {provider!r}: chưa có endpoint/key thật cho try-on local — điền vào "
            "local_tryon.py khi có chi tiết API (vd DashScope cho qwen-max)")

    key = str(params.get("apiKey") or params.get("geminiApiKey")
             or settings.gemini_api_key or "").strip()
    if not key:
        raise JobError(
            f"run {run.id!r}: try-on local (provider gemini) cần API key — thêm GEMINI_API_KEY "
            "vào .env, hoặc apiKey/geminiApiKey trong tryon: của run này")
    if not valid_gemini_key(key):
        raise JobError(
            f"run {run.id!r}: GEMINI_API_KEY không đúng định dạng (phải 'AIza…', ~39 ký tự, "
            "không khoảng trắng)")

    model_path = run.inputs.get("character")
    product_path = run.inputs.get("outfit")
    background_path = run.inputs.get("background")
    if model_path is None or product_path is None:
        raise JobError(f"run {run.id!r}: try-on local cần inputs.character và inputs.outfit")

    garment = (str(params.get("garment_type") or params.get("garmentType") or "").lower().strip()
              or "auto")
    extra_raw = str(params.get("extraPrompt") or params.get("extra_prompt")
                    or params.get("keepNote") or "").strip()
    extra_en = translate_vn_to_en(extra_raw, key) if extra_raw else ""

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        images = [(model_path.read_bytes(), mime_of(model_path)),
                  (product_path.read_bytes(), mime_of(product_path))]
        prompt = gemini_tryon_prompt(garment, extra=extra_en)
        edited = gemini_edit(images, prompt, key, tmp_dir / "pass1.png",
                             aspect_ratio=gemini_aspect(img_size(model_path)))

        if background_path is not None:
            edited = gemini_edit(
                [(edited.read_bytes(), "image/png"),
                 (background_path.read_bytes(), mime_of(background_path))],
                TRYON_BG_POS, key, tmp_dir / "pass2.png",
                aspect_ratio=gemini_aspect(img_size(edited)))

        final = postprocess(edited, params)
        out_path.write_bytes(final.read_bytes())

    return int(time.time() - started), out_path.stat().st_size
```

- [ ] **Step 4: Chạy lại test, xác nhận PASS**

Run: `python3 -m unittest scripts.tests.test_batch_local_tryon -v`
Expected: PASS toàn bộ.

- [ ] **Step 5: Chạy toàn bộ cổng, xác nhận không phá gì khác**

Run: `make batch-test`
Expected: PASS toàn bộ.

- [ ] **Step 6: Commit**

```bash
git add scripts/batchlib/local_tryon.py scripts/tests/test_batch_local_tryon.py
git commit -m "feat(batch): local_tryon.run_local_tryon() — điểm vào Pha A cho một run"
```

---

### Task 5: `runner.py` — `stage_dest()` dùng chung, bỏ gate `resume and` khi bỏ-qua-chặng-đã-xong

**Files:**
- Modify: `scripts/batchlib/runner.py`
- Test: `scripts/tests/test_batch_runner.py`

**Interfaces:**
- Produces: `stage_dest(run: Run, run_dir: Path, stage_name: str) -> Path` — dùng ở cả `run_one`
  (đã có) và `run_local_phase` (Task 7).
- Modifies: điều kiện bỏ-qua-chặng trong `run_one()` không còn phụ thuộc tham số `resume`.

- [ ] **Step 1: Viết test cho hành vi mới (chặng đã `done` trong state được bỏ qua DÙ `resume=False`)**

Thêm vào `scripts/tests/test_batch_runner.py` (cạnh các test khác của `run_one`/`run_batch` — tìm
class test `run_one` hiện có bằng `grep -n "class.*RunOne\|def run_one" scripts/tests/test_batch_runner.py`
để đặt đúng chỗ; nếu không có class riêng, thêm class mới ở cuối file trước dòng
`if __name__ == "__main__":`):

```python
from batchlib.runner import run_one, stage_dest


class TestStageDest(unittest.TestCase):
    def test_duong_dan_dung_cong_thuc_NN_ten_chang(self):
        with tempfile.TemporaryDirectory() as d:
            run = load_manifest(_fixture(Path(d), MANIFEST_MOT_RUN)).runs[0]
            run_dir = Path(d) / "runs" / run.id
            self.assertEqual(stage_dest(run, run_dir, "motion"), run_dir / "01-motion.mp4")
            self.assertEqual(stage_dest(run, run_dir, "enhance"), run_dir / "02-enhance.mp4")


class TestBoQuaChangDaXongKhongCanResume(unittest.TestCase):
    """Pha A (Task 7) ghi 'done' vào journal TRƯỚC khi gọi run_one, trong CÙNG một lần
    chạy `make batch` (resume=False, vì đây là lô mới). run_one phải nhận ra chặng đã
    xong và bỏ qua, KHÔNG được đòi resume=True mới chịu bỏ qua."""

    def test_chang_da_done_va_co_file_thi_bo_qua_du_khong_resume(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            manifest = load_manifest(_fixture(tmp, MANIFEST_MOT_RUN))
            run = manifest.runs[0]
            out_dir = tmp / "out"
            run_dir = out_dir / "runs" / run.id
            run_dir.mkdir(parents=True)
            dest = stage_dest(run, run_dir, "motion")
            dest.write_bytes(b"da-co-san-tu-pha-A")
            state = {"version": 1, "runs": {run.id: {"status": "pending", "stages": {
                "motion": {"status": "done", "file": str(dest), "bytes": dest.stat().st_size}}}}}
            state_file = tmp / "b.state.json"

            pod = FakePod()   # chỉ chặng enhance mới được phép chạm tới pod
            with mock.patch("batchlib.runner.submit_job", pod.submit), \
                 mock.patch("batchlib.runner.poll_job", pod.poll), \
                 mock.patch("batchlib.runner.download_output", pod.download):
                run_one(settings=SETTINGS, run=run, out_dir=out_dir, state=state,
                       state_file=state_file, resume=False, log=lambda *_: None)

            # motion KHÔNG được submit lại — chỉ enhance chạy trên pod giả. `pod.submitted` là
            # list[tuple[job_type, params, files]] (đã có sẵn trong FakePod, xem __init__).
            self.assertEqual([jt for jt, _, _ in pod.submitted], ["enhance"])
            self.assertEqual(dest.read_bytes(), b"da-co-san-tu-pha-A")   # file không bị ghi đè
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `python3 -m unittest scripts.tests.test_batch_runner -v`
Expected: FAIL trên `TestStageDest` (chưa có hàm) và trên `TestBoQuaChangDaXongKhongCanResume`
(hiện tại `resume=False` thì KHÔNG bỏ qua, submit lại `motion` — assertion trên `pod.submitted` sai).

- [ ] **Step 3: Thêm `stage_dest()` và sửa điều kiện bỏ-qua trong `run_one()`**

Trong `scripts/batchlib/runner.py`, thêm hàm mới ngay trước `def run_one(`:

```python
def stage_dest(run: Run, run_dir: Path, stage_name: str) -> Path:
    """Đường dẫn NN-<chặng>.ext — DÙNG CHUNG giữa run_one (Pha B) và run_local_phase
    (Pha A), để không lệch nhau khi Pha A ghi file trước, Pha B kiểm tra sau."""
    index = PIPELINES[run.pipeline].index(stage_name) + 1
    return run_dir / f"{index:02d}-{stage_name}{STAGES[stage_name].output_ext}"
```

Sửa vòng lặp trong `run_one()` (dòng 150-159 hiện tại):

```python
    prev_output: Path | None = None
    for stage_name in PIPELINES[run.pipeline]:
        stage = STAGES[stage_name]
        dest = stage_dest(run, run_dir, stage_name)
        recorded = entry["stages"].get(stage_name) or {}
        params = run.stage_params.get(stage_name, {})

        # Chặng đã "done" VÀ còn file trên đĩa thì bỏ qua — KHÔNG gate theo `resume`.
        # Một lô THẬT SỰ mới (resume=False) luôn khởi tạo state["runs"] rỗng
        # (run_batch/prepare_batch), nên "done" ở đây chỉ có thể đến từ Pha A
        # (run_local_phase) đã ghi trong CHÍNH lần gọi `make batch` này — bỏ qua đúng
        # là hành vi cần, không phải một lỗ hổng bỏ sót --resume.
        if recorded.get("status") == "done" and dest.is_file():
            log(f"    {stage_name}: bỏ qua (đã xong, {dest.name})")
            prev_output = dest
            continue
```

(Xoá dòng `for index, stage_name in enumerate(PIPELINES[run.pipeline], start=1):` cũ và dòng
`dest = run_dir / f"{index:02d}-{stage_name}{stage.output_ext}"` cũ — `index` không còn dùng ở đâu
khác trong hàm.)

- [ ] **Step 4: Chạy lại test, xác nhận PASS**

Run: `python3 -m unittest scripts.tests.test_batch_runner -v`
Expected: PASS toàn bộ.

- [ ] **Step 5: Chạy toàn bộ cổng**

Run: `make batch-test`
Expected: PASS toàn bộ — đặc biệt chú ý các test `--resume` hiện có trong
`test_batch_runner.py`/`test_batch_run.py` vẫn xanh (hành vi resume thật KHÔNG đổi, chỉ điều kiện
bỏ-qua được nới ra đúng một trường hợp mới).

- [ ] **Step 6: Commit**

```bash
git add scripts/batchlib/runner.py scripts/tests/test_batch_runner.py
git commit -m "refactor(batch): stage_dest() dùng chung, bỏ-qua-chặng-đã-xong không cần resume"
```

---

### Task 6: `runner.py` — tách `prepare_batch()`, `run_batch()` nhận `prepared=`

**Files:**
- Modify: `scripts/batchlib/runner.py`
- Test: `scripts/tests/test_batch_runner.py`

**Interfaces:**
- Produces: `prepare_batch(*, manifest: Manifest, out_root: Path, batch_id: str, resume: bool) -> tuple[Path, dict, Path]` (trả `out_dir, state, state_file`).
- Modifies: `run_batch(..., prepared: tuple[Path, dict, Path] | None = None)` — khi `prepared` được
  truyền, dùng nguyên `out_dir`/`state`/`state_file` đó thay vì tự tạo mới. Hành vi hiện tại
  (không truyền `prepared`) không đổi.

- [ ] **Step 1: Viết test cho `prepare_batch()` và cho `run_batch(prepared=...)`**

Thêm vào `scripts/tests/test_batch_runner.py`:

```python
from batchlib.runner import prepare_batch


class TestPrepareBatch(unittest.TestCase):
    def test_lo_moi_state_rong(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            manifest = load_manifest(_fixture(tmp))
            out_dir, state, state_file = prepare_batch(
                manifest=manifest, out_root=tmp / "out", batch_id="2026-08-21-0900", resume=False)
            self.assertTrue((out_dir / "manifest.yaml").is_file())
            self.assertEqual(state, {"version": 1, "runs": {}, "batch": "2026-08-21-0900"})
            self.assertEqual(state_file, state_path_for(manifest.path))

    def test_resume_doc_lai_state_cu(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            manifest = load_manifest(_fixture(tmp))
            state_file = state_path_for(manifest.path)
            save_state(state_file, {"version": 1, "batch": "2026-08-20-0000",
                                    "runs": {"runA": {"status": "done", "stages": {}}}})
            out_dir, state, _ = prepare_batch(
                manifest=manifest, out_root=tmp / "out", batch_id="2026-08-20-0000", resume=True)
            self.assertEqual(state["runs"]["runA"]["status"], "done")


class TestRunBatchNhanPrepared(unittest.TestCase):
    def test_dung_state_da_chuan_bi_san_khong_tu_tao_lai(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            manifest = load_manifest(_fixture(tmp, MANIFEST_MOT_RUN))
            run = manifest.runs[0]
            batch_id = "2026-08-21-0900"
            out_dir, state, state_file = prepare_batch(
                manifest=manifest, out_root=tmp / "out", batch_id=batch_id, resume=False)
            # Giả lập Pha A đã ghi xong chặng "motion" TRƯỚC khi run_batch được gọi.
            run_dir = out_dir / "runs" / run.id
            run_dir.mkdir(parents=True)
            dest = stage_dest(run, run_dir, "motion")
            dest.write_bytes(b"tu-pha-A")
            state["runs"][run.id] = {"status": "pending",
                                     "stages": {"motion": {"status": "done", "file": str(dest),
                                                           "bytes": dest.stat().st_size}}}

            pod = FakePod()
            with mock.patch("batchlib.runner.submit_job", pod.submit), \
                 mock.patch("batchlib.runner.poll_job", pod.poll), \
                 mock.patch("batchlib.runner.download_output", pod.download):
                result = run_batch(settings=SETTINGS, manifest=manifest, out_root=tmp / "out",
                                   batch_id=batch_id, resume=False,
                                   prepared=(out_dir, state, state_file))

            self.assertEqual([jt for jt, _, _ in pod.submitted], ["enhance"])   # motion KHÔNG submit lại
            self.assertEqual(result.done, [run.id])
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `python3 -m unittest scripts.tests.test_batch_runner -v`
Expected: FAIL — `ImportError: cannot import name 'prepare_batch'`.

- [ ] **Step 3: Tách `prepare_batch()` khỏi `run_batch()`**

Trong `scripts/batchlib/runner.py`, thêm hàm mới ngay trước `def run_batch(`:

```python
def prepare_batch(*, manifest: Manifest, out_root: Path, batch_id: str,
                  resume: bool) -> tuple[Path, dict, Path]:
    """Tạo out_dir + nạp/khởi tạo state — tách khỏi run_batch() để Pha A
    (run_local_phase, batch_run.py) và Pha B (run_batch, dưới đây) trong CÙNG một lần
    gọi `make batch` thấy đúng MỘT state: Pha A ghi trước, Pha B đọc lại đúng cái Pha A
    vừa ghi thay vì bị `resume=False` xoá sạch (run_batch cũ luôn ép state rỗng khi
    resume=False — đúng cho lô KHÔNG có Pha A, sai nếu Pha A đã chạy trong cùng lần gọi).
    """
    out_dir = out_root / batch_id
    out_dir.mkdir(parents=True, exist_ok=True)
    # Chép NGUYÊN VĂN, không qua PyYAML — comment của người dùng phải sống sót.
    shutil.copyfile(manifest.path, out_dir / "manifest.yaml")
    state_file = state_path_for(manifest.path)
    state = load_state(state_file) if resume else {"version": 1, "runs": {}}
    state["batch"] = batch_id
    return out_dir, state, state_file
```

Sửa `run_batch()` (dòng 281-296 hiện tại) — thay đoạn tạo `out_dir`/copy manifest/nạp `state` bằng
lời gọi `prepare_batch()`, và thêm tham số `prepared`:

```python
def run_batch(*, settings: Settings, manifest: Manifest, out_root: Path,
              batch_id: str | None = None, resume: bool = False, fail_fast: bool = False,
              log: Callable[[str], None] = print,
              now: Callable[[], float] = time.time,
              prepared: tuple[Path, dict, Path] | None = None) -> BatchResult:
    batch_id = batch_id or batch_id_now()
    if prepared is not None:
        out_dir, state, state_file = prepared
    else:
        out_dir, state, state_file = prepare_batch(manifest=manifest, out_root=out_root,
                                                    batch_id=batch_id, resume=resume)

    result = BatchResult(batch_id=batch_id, out_dir=out_dir)
    for position, run in enumerate(manifest.runs, start=1):
        ...   # KHÔNG đổi phần thân vòng lặp trở xuống — giữ nguyên dòng 297-320 hiện tại
```

Giữ nguyên toàn bộ phần thân vòng lặp `for position, run in enumerate(...)` và các dòng
`save_state`/`write_index`/symlink `latest` ở cuối hàm — chỉ phần đầu hàm (tạo out_dir/state) đổi.

- [ ] **Step 4: Chạy lại test, xác nhận PASS**

Run: `python3 -m unittest scripts.tests.test_batch_runner -v`
Expected: PASS toàn bộ.

- [ ] **Step 5: Chạy toàn bộ cổng**

Run: `make batch-test`
Expected: PASS toàn bộ — `run_batch()` gọi không kèm `prepared` (mọi call site hiện có) phải cho
kết quả HỆT như trước khi refactor.

- [ ] **Step 6: Commit**

```bash
git add scripts/batchlib/runner.py scripts/tests/test_batch_runner.py
git commit -m "refactor(batch): tách prepare_batch(), run_batch() nhận state đã chuẩn bị sẵn"
```

---

### Task 7: `runner.py` — `needs_pod()` + `run_local_phase()` (pool đồng thời có giới hạn)

**Files:**
- Modify: `scripts/batchlib/runner.py`
- Test: `scripts/tests/test_batch_runner.py`

**Interfaces:**
- Consumes: `is_local_provider`, `run_local_tryon` từ `scripts/batchlib/local_tryon.py` (Task 2, 4);
  `prepare_batch`, `stage_dest` (Task 5, 6); `ConfigError` từ `scripts/batchlib/config.py`.
- Produces:
  - `needs_pod(manifest: Manifest) -> bool`
  - `@dataclass LocalPhaseResult`: `ran: bool`, `out_dir: Path | None`, `state: dict | None`, `state_file: Path | None`, `done: list[str]`, `failed: dict[str, str]`
  - `run_local_phase(*, settings: Settings, manifest: Manifest, out_root: Path, batch_id: str, resume: bool, fail_fast: bool = False, log: Callable[[str], None] = print, pool_size: int = 4) -> LocalPhaseResult`

- [ ] **Step 1: Viết test**

Thêm vào `scripts/tests/test_batch_runner.py`:

```python
from batchlib.config import ConfigError
from batchlib.runner import LocalPhaseResult, needs_pod, run_local_phase

MANIFEST_TRYON_GEMINI = """
runs:
  - id: runA
    pipeline: tryon-motion-enhance
    inputs:
      character: char.jpg
      outfit: outfit.jpg
      driver: drv.mp4
    tryon: { provider: gemini }
"""

MANIFEST_HAI_RUN_GEMINI = """
runs:
  - id: runA
    pipeline: tryon-motion-enhance
    inputs:
      character: char.jpg
      outfit: outfit.jpg
      driver: drv.mp4
    tryon: { provider: gemini }
  - id: runB
    pipeline: tryon-motion-enhance
    inputs:
      character: char.jpg
      outfit: outfit.jpg
      driver: drv.mp4
    tryon: { provider: gemini }
"""


def _fixture_tryon(tmp: Path, text: str) -> Path:
    (tmp / "char.jpg").write_bytes(b"x")
    (tmp / "outfit.jpg").write_bytes(b"x")
    (tmp / "drv.mp4").write_bytes(b"x")
    p = tmp / "b.yaml"
    p.write_text(text, encoding="utf-8")
    return p


class TestNeedsPod(unittest.TestCase):
    def test_motion_enhance_luon_can_pod(self):
        with tempfile.TemporaryDirectory() as d:
            manifest = load_manifest(_fixture(Path(d)))
            self.assertTrue(needs_pod(manifest))

    def test_tryon_gemini_van_can_pod_vi_con_motion_enhance(self):
        with tempfile.TemporaryDirectory() as d:
            manifest = load_manifest(_fixture_tryon(Path(d), MANIFEST_TRYON_GEMINI))
            self.assertTrue(needs_pod(manifest))   # motion+enhance luôn cần pod


class TestRunLocalPhase(unittest.TestCase):
    def test_khong_co_run_local_nao_thi_no_op_khong_dung_dia(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            manifest = load_manifest(_fixture(tmp, MANIFEST_MOT_RUN))   # motion-enhance, không tryon
            out_root = tmp / "out"
            result = run_local_phase(settings=SETTINGS, manifest=manifest, out_root=out_root,
                                     batch_id="2026-08-21-0900", resume=False)
            self.assertEqual(result, LocalPhaseResult(ran=False))
            self.assertFalse(out_root.exists())   # KHÔNG được tạo thư mục khi không có việc

    def test_thieu_gemini_key_raise_configerror(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            manifest = load_manifest(_fixture_tryon(tmp, MANIFEST_TRYON_GEMINI))
            settings_khong_key = Settings(domain="x.test", api_key="mk_test", instance_id="i-1")
            with self.assertRaises(ConfigError):
                run_local_phase(settings=settings_khong_key, manifest=manifest,
                                out_root=tmp / "out", batch_id="2026-08-21-0900", resume=False)

    def test_chay_that_ghi_journal_va_file(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            manifest = load_manifest(_fixture_tryon(tmp, MANIFEST_TRYON_GEMINI))
            settings = Settings(domain="x.test", api_key="mk_test", instance_id="i-1",
                                gemini_api_key="AIza" + "x" * 35)

            def fake_run_local_tryon(run, params, settings_, out_path):
                out_path.write_bytes(b"fake-tryon-png")
                return 5, out_path.stat().st_size

            with mock.patch("batchlib.runner.run_local_tryon", fake_run_local_tryon):
                result = run_local_phase(settings=settings, manifest=manifest,
                                         out_root=tmp / "out", batch_id="2026-08-21-0900",
                                         resume=False)

            self.assertTrue(result.ran)
            self.assertEqual(result.done, ["runA"])
            self.assertEqual(result.failed, {})
            stage = result.state["runs"]["runA"]["stages"]["tryon"]
            self.assertEqual(stage["status"], "done")
            self.assertEqual(stage["params_sent"], stage["params_manifest"])
            dest = Path(stage["file"])
            self.assertEqual(dest.read_bytes(), b"fake-tryon-png")
            # Journal đã ghi ra đĩa (không chỉ trong bộ nhớ) — Phase B sau này đọc lại được.
            self.assertEqual(load_state(result.state_file)["runs"]["runA"]["stages"]["tryon"]["status"],
                             "done")

    def test_mot_run_loi_khong_chan_run_khac(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            manifest = load_manifest(_fixture_tryon(tmp, MANIFEST_HAI_RUN_GEMINI))
            settings = Settings(domain="x.test", api_key="mk_test", instance_id="i-1",
                                gemini_api_key="AIza" + "x" * 35)

            def fake_run_local_tryon(run, params, settings_, out_path):
                if run.id == "runA":
                    raise JobError("gemini 429 het quota")
                out_path.write_bytes(b"ok")
                return 3, out_path.stat().st_size

            with mock.patch("batchlib.runner.run_local_tryon", fake_run_local_tryon):
                result = run_local_phase(settings=settings, manifest=manifest,
                                         out_root=tmp / "out", batch_id="2026-08-21-0900",
                                         resume=False, pool_size=2)

            self.assertEqual(result.done, ["runB"])
            self.assertIn("runA", result.failed)
            self.assertEqual(result.state["runs"]["runA"]["stages"]["tryon"]["status"], "error")

    def test_da_lam_xong_tu_lan_truoc_thi_bo_qua(self):
        # Mô phỏng RESUME=1 sau khi provision pod: Pha A của LẦN GỌI TRƯỚC đã ghi xong.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            manifest = load_manifest(_fixture_tryon(tmp, MANIFEST_TRYON_GEMINI))
            settings = Settings(domain="x.test", api_key="mk_test", instance_id="i-1",
                                gemini_api_key="AIza" + "x" * 35)
            state_file = state_path_for(manifest.path)
            out_dir = tmp / "out" / "2026-08-21-0900"
            run_dir = out_dir / "runs" / "runA"
            run_dir.mkdir(parents=True)
            dest = run_dir / "01-tryon.png"
            dest.write_bytes(b"da-xong-tu-truoc")
            save_state(state_file, {"version": 1, "batch": "2026-08-21-0900", "runs": {
                "runA": {"status": "pending", "stages": {
                    "tryon": {"status": "done", "file": str(dest), "bytes": dest.stat().st_size}}}}})

            with mock.patch("batchlib.runner.run_local_tryon") as m_local:
                result = run_local_phase(settings=settings, manifest=manifest,
                                         out_root=tmp / "out", batch_id="2026-08-21-0900",
                                         resume=True)
            m_local.assert_not_called()
            self.assertEqual(result.done, [])   # không có gì MỚI chạy — đã done từ trước
            self.assertEqual(dest.read_bytes(), b"da-xong-tu-truoc")
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `python3 -m unittest scripts.tests.test_batch_runner -v`
Expected: FAIL — `ImportError: cannot import name 'needs_pod'`.

- [ ] **Step 3: Thêm `needs_pod()` và `run_local_phase()` vào `runner.py`**

Thêm import ở đầu `scripts/batchlib/runner.py` (cạnh các import hiện có):

```python
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait as cf_wait
from dataclasses import dataclass, field   # `field` đã import — chỉ thêm nếu thiếu

from .config import ConfigError, Settings   # thêm ConfigError vào import Settings đã có
from .local_tryon import is_local_provider, run_local_tryon
```

Thêm vào cuối `scripts/batchlib/runner.py`:

```python
def needs_pod(manifest: Manifest) -> bool:
    """True nếu còn ít nhất một chặng KHÔNG THỂ chạy local trong toàn bộ manifest.

    Dựa trên ĐỊNH NGHĨA pipeline (tĩnh), không dựa trên state runtime: motion/enhance
    không bao giờ local-eligible, nên với hai pipeline hiện có (PIPELINES) hàm này
    luôn True — nhưng viết tường minh để không âm thầm sai nếu sau này có pipeline
    chỉ gồm try-on.
    """
    for run in manifest.runs:
        for stage_name in PIPELINES.get(run.pipeline, []):
            if stage_name != "tryon":
                return True
            provider = str(run.stage_params.get("tryon", {}).get("provider") or "").lower().strip()
            if not is_local_provider(provider):
                return True
    return False


@dataclass
class LocalPhaseResult:
    ran: bool
    out_dir: Path | None = None
    state: dict | None = None
    state_file: Path | None = None
    done: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)


def run_local_phase(*, settings: Settings, manifest: Manifest, out_root: Path, batch_id: str,
                    resume: bool, fail_fast: bool = False, log: Callable[[str], None] = print,
                    pool_size: int = 4) -> LocalPhaseResult:
    """Pha A: try-on qua API (provider local-eligible) chạy TRƯỚC khi đụng pod, qua một
    pool đồng thời có giới hạn — không tốn GPU nên chạy song song không có cái giá
    "hai job chồng nhau trên một GPU" mà run_one/run_batch phải tránh (xem docstring
    đầu file). KHÔNG tạo out_dir/state nếu manifest không có run nào cần Pha A — giữ
    hành vi HỆT NHƯ TRƯỚC bản sửa này cho mọi manifest không dùng try-on local.
    """
    jobs: list[tuple[Run, dict]] = []
    for run in manifest.runs:
        if "tryon" not in PIPELINES.get(run.pipeline, []):
            continue
        params = run.stage_params.get("tryon", {})
        provider = str(params.get("provider") or "").lower().strip()
        if is_local_provider(provider):
            jobs.append((run, params))

    if not jobs:
        return LocalPhaseResult(ran=False)

    if not settings.gemini_api_key:
        raise ConfigError(
            "Thiếu GEMINI_API_KEY trong .env — cần để chạy try-on local (provider: gemini).\n"
            "  Lấy key ở https://aistudio.google.com/apikey rồi thêm GEMINI_API_KEY=... vào .env."
        )

    out_dir, state, state_file = prepare_batch(manifest=manifest, out_root=out_root,
                                               batch_id=batch_id, resume=resume)
    result = LocalPhaseResult(ran=True, out_dir=out_dir, state=state, state_file=state_file)
    lock = threading.Lock()

    def _one(run: Run, params: dict) -> tuple[str, str | None]:
        with lock:
            entry = state["runs"].setdefault(run.id, {"status": "pending", "stages": {}})
            recorded = entry["stages"].get("tryon") or {}
        run_dir = out_dir / "runs" / run.id
        run_dir.mkdir(parents=True, exist_ok=True)
        dest = stage_dest(run, run_dir, "tryon")
        if recorded.get("status") == "done" and dest.is_file():
            log(f"    {run.id}/tryon: bỏ qua (đã xong local, {dest.name})")
            return run.id, None
        started = time.time()
        try:
            elapsed, size = run_local_tryon(run, params, settings, dest)
        except JobError as exc:
            with lock:
                entry["stages"]["tryon"] = {"status": "error",
                                            "elapsed_sec": int(time.time() - started)}
                save_state(state_file, state)
            log(f"    ✗ {run.id}/tryon (local): {exc}")
            return run.id, str(exc)
        with lock:
            entry["stages"]["tryon"] = {
                "status": "done", "elapsed_sec": elapsed, "file": str(dest), "bytes": size,
                "params_sent": dict(params), "params_manifest": dict(params)}
            save_state(state_file, state)
        log(f"    {run.id}/tryon (local): xong {elapsed}s · {size // 1024} KB → {dest.name}")
        return run.id, None

    pending = list(jobs)
    aborted = False
    with ThreadPoolExecutor(max_workers=pool_size) as pool:
        futures: dict = {}

        def _submit_next() -> None:
            if pending and not (aborted and fail_fast):
                run, params = pending.pop(0)
                futures[pool.submit(_one, run, params)] = run.id

        for _ in range(min(pool_size, len(pending))):
            _submit_next()
        while futures:
            # wait(..., FIRST_COMPLETED) — chờ ÍT NHẤT một future xong rồi xử lý cả lô,
            # không phải vòng lặp bận (busy-wait) kiểu tự dò .done() trên từng future.
            done_set, _ = cf_wait(list(futures), return_when=FIRST_COMPLETED)
            for done_future in done_set:
                run_id, err = done_future.result()
                del futures[done_future]
                if err is None:
                    result.done.append(run_id)
                else:
                    result.failed[run_id] = err
                    if fail_fast:
                        aborted = True
                _submit_next()
    return result
```

- [ ] **Step 4: Chạy lại test, xác nhận PASS**

Run: `python3 -m unittest scripts.tests.test_batch_runner -v`
Expected: PASS toàn bộ.

- [ ] **Step 5: Chạy toàn bộ cổng**

Run: `make batch-test`
Expected: PASS toàn bộ.

- [ ] **Step 6: Commit**

```bash
git add scripts/batchlib/runner.py scripts/tests/test_batch_runner.py
git commit -m "feat(batch): run_local_phase() — try-on local qua pool đồng thời có giới hạn"
```

---

### Task 8: `scripts/batch_run.py::main()` — nối Pha A vào trước preflight

**Files:**
- Modify: `scripts/batch_run.py`
- Test: `scripts/tests/test_batch_run.py`

**Interfaces:**
- Consumes: `run_local_phase`, `needs_pod`, `LocalPhaseResult` (Task 7); `run_batch(..., prepared=...)`
  (Task 6); `Settings.gemini_api_key` (Task 1).
- Modifies: thứ tự lệnh gọi trong `main()`. KHÔNG đổi chữ ký `main(argv)`, KHÔNG đổi hành vi cho
  manifest không có run local-eligible (toàn bộ test hiện có trong `test_batch_run.py` dùng
  `_manifest_chay_duoc` = pipeline `motion-enhance`, không có `tryon` → Pha A luôn no-op cho các
  test đó, PHẢI xanh nguyên vẹn không cần sửa).

- [ ] **Step 1: Viết test MỚI cho luồng có try-on local (không sửa test cũ)**

Thêm vào cuối `scripts/tests/test_batch_run.py`, trước `if __name__ == "__main__":`:

```python
MANIFEST_TRYON_GEMINI = """
runs:
  - id: runA
    pipeline: tryon-motion-enhance
    inputs:
      character: char.jpg
      outfit: outfit.jpg
      driver: drv.mp4
    tryon: { provider: gemini }
"""


def _manifest_tryon_gemini(tmp: Path) -> Path:
    (tmp / "char.jpg").write_bytes(b"x")
    (tmp / "outfit.jpg").write_bytes(b"x")
    (tmp / "drv.mp4").write_bytes(b"x")
    p = tmp / "b.yaml"
    p.write_text(MANIFEST_TRYON_GEMINI, encoding="utf-8")
    return p


class TestMainPhaA(unittest.TestCase):
    """Try-on local chạy TRƯỚC preflight — và pod chưa sẵn sàng thì báo rõ đã xong Pha A."""

    def test_khong_co_pod_sau_khi_xong_pha_a_thi_bao_ro_va_khong_goi_run_batch(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = _manifest_tryon_gemini(tmp)

            def fake_run_local_tryon(run, params, settings_, out_path):
                out_path.write_bytes(b"fake")
                return 2, out_path.stat().st_size

            err = io.StringIO()
            with mock.patch("batch_run.load_settings",
                            return_value=Settings(domain="pod.test", api_key="mk_test",
                                                  instance_id="", gemini_api_key="AIza" + "x" * 35)), \
                 mock.patch("batch_run.health_ok", return_value=False), \
                 mock.patch("batchlib.runner.run_local_tryon", fake_run_local_tryon), \
                 mock.patch("batch_run.run_batch") as m_run_batch, \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                code = batch_run.main(["--file", str(p)])
            self.assertEqual(code, 1)
            self.assertIn("gpu-provision", err.getvalue())
            self.assertIn("RESUME=1", err.getvalue())
            m_run_batch.assert_not_called()

    def test_pod_san_sang_thi_chay_tiep_ca_pha_b_voi_state_da_chuan_bi(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = _manifest_tryon_gemini(tmp)

            def fake_run_local_tryon(run, params, settings_, out_path):
                out_path.write_bytes(b"fake")
                return 2, out_path.stat().st_size

            captured: dict = {}

            def fake_run_batch(**kwargs):
                captured.update(kwargs)
                return BatchResult(batch_id=kwargs["batch_id"],
                                   out_dir=kwargs.get("prepared", (tmp / "out" / kwargs["batch_id"],))[0],
                                   done=["runA"])

            with mock.patch("batch_run.load_settings",
                            return_value=Settings(domain="pod.test", api_key="mk_test",
                                                  instance_id="i-1", gemini_api_key="AIza" + "x" * 35)), \
                 mock.patch("batch_run.health_ok", return_value=True), \
                 mock.patch("batchlib.runner.run_local_tryon", fake_run_local_tryon), \
                 mock.patch("batch_run.run_batch", fake_run_batch), \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = batch_run.main(["--file", str(p)])
            self.assertEqual(code, 0)
            self.assertIn("prepared", captured)
            out_dir, state, _state_file = captured["prepared"]
            self.assertEqual(state["runs"]["runA"]["stages"]["tryon"]["status"], "done")
```

- [ ] **Step 2: Chạy test cũ + mới, xác nhận test MỚI FAIL và test CŨ vẫn PASS**

Run: `python3 -m unittest scripts.tests.test_batch_run -v`
Expected: các class cũ (`TestMainPreflightKhongCoPod`, `TestMainTruyenCoResumeXuongRunBatch`, …)
PASS nguyên vẹn; `TestMainPhaA` FAIL (chưa nối Pha A vào `main()`).

- [ ] **Step 3: Sửa `main()` trong `scripts/batch_run.py`**

Sửa import ở đầu file (dòng 18-23), thêm `run_local_phase`, `needs_pod`:

```python
from batchlib.runner import batch_id_now, needs_pod, run_batch, run_local_phase
```

Sửa phần thân `main()` từ chỗ `settings = load_settings(ROOT)` (dòng 129) tới trước
`minutes = result.gpu_seconds / 60` (dòng 168) thành:

```python
    try:
        settings = load_settings(ROOT)
    except ConfigError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1

    # --resume PHẢI chạy tiếp vào out_dir CŨ (xem resolve_batch_id) — dời lên TRƯỚC Pha A vì
    # Pha A cần biết out_dir/batch_id trước khi chạm tới pod.
    decision = resolve_batch_id(manifest.path, resume=args.resume)
    if args.resume:
        print(f"  {decision.note}")

    try:
        local_result = run_local_phase(settings=settings, manifest=manifest, out_root=ROOT / "out",
                                       batch_id=decision.batch_id, resume=decision.resumed,
                                       fail_fast=args.fail_fast)
    except ConfigError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1

    if local_result.ran and local_result.done:
        print(f"  ✓ try-on local xong {len(local_result.done)} run "
              f"(pod chưa đụng tới — {local_result.out_dir / 'runs'})")
    for run_id, why in local_result.failed.items():
        print(f"    ✗ {run_id} (try-on local): {why}", file=sys.stderr)

    if needs_pod(manifest):
        if not preflight(settings, allow_start=not args.no_start):
            if local_result.ran and local_result.done:
                print(f"\n  Đã xong try-on local — ảnh đã lưu, KHÔNG mất khi thuê pod xong.",
                      file=sys.stderr)
                print(f"  Thuê/bật pod rồi chạy tiếp: make batch FILE={args.file} RESUME=1",
                      file=sys.stderr)
            return 1

    run_batch_kwargs = dict(settings=settings, manifest=manifest, out_root=ROOT / "out",
                            batch_id=decision.batch_id, resume=decision.resumed,
                            fail_fast=args.fail_fast)
    if local_result.ran:
        run_batch_kwargs["prepared"] = (local_result.out_dir, local_result.state,
                                        local_result.state_file)

    # submit_job/download_output (batchlib/client.py) CỐ Ý để URLError/OSError rơi thẳng
    # ra ngoài — chỉ poll_job tự bắt rớt mạng. Ở đây là nơi cuối cùng bắt nó: rớt Wi-Fi
    # giữa lô KHÔNG được biến thành traceback trần trụi, và job vừa gửi có thể vẫn đang
    # chạy trên pod — tuyệt đối không được khuyên gửi lại, vì gửi lại là trả tiền GPU
    # hai lần cho cùng một việc.
    try:
        result = run_batch(**run_batch_kwargs)
    except (urllib.error.URLError, OSError) as exc:
        print(f"\n✗ Mất kết nối tới pod giữa chừng: {exc}", file=sys.stderr)
        print("  Job vừa gửi có thể VẪN đang chạy trên pod — đừng chạy lại từ đầu (tốn tiền GPU hai lần).",
              file=sys.stderr)
        print(f"  Kiểm tra mạng/pod rồi chạy lại: make batch FILE={args.file} RESUME=1", file=sys.stderr)
        return 1
```

Xoá dòng `if not preflight(settings, allow_start=not args.no_start): return 1` cũ (đã thay bằng
khối `if needs_pod(...)` ở trên) và dòng `decision = resolve_batch_id(...)` cũ (đã dời lên sớm hơn,
ngay sau `load_settings`). Phần còn lại của `main()` (in kết quả, `result.failed`, thông báo cuối)
giữ NGUYÊN không đổi.

- [ ] **Step 4: Chạy lại toàn bộ `test_batch_run.py`, xác nhận PASS cả cũ lẫn mới**

Run: `python3 -m unittest scripts.tests.test_batch_run -v`
Expected: PASS toàn bộ — không class nào trong số các class đã có TRƯỚC task này (
`TestResolveBatchId`, `TestMainTruyenCoResumeXuongRunBatch`, `TestThieuNguonBangParam`,
`TestValidateOnly`, `TestManifestYamlHong`, `TestConfigErrorTuLoadSettings`, `TestKetThucCoRunHong`,
`TestPreflightHam`, `TestMainPreflightKhongCoPod`, `TestNoStartTruyenXuongPreflight`,
`TestMatKetNoiGiuaChungLopCloudflare`) được sửa nội dung — chỉ `TestMainPhaA` là mới.

- [ ] **Step 5: Chạy toàn bộ cổng batch**

Run: `make batch-test`
Expected: PASS toàn bộ.

- [ ] **Step 6: Kiểm tay bằng smoke test không cần pod (`--validate-only`)**

Run: `python3 scripts/batch_run.py --file batch/example.yaml --validate-only` (nếu
`batch/example.yaml` tồn tại — nếu không, bỏ qua bước này, Task 8 đã đủ kiểm bằng unit test)
Expected: in `✓ manifest hợp lệ`, không đụng tới `run_local_phase`/pod (nhánh `--validate-only`
return sớm trước khi tới `load_settings`, không đổi so với trước).

- [ ] **Step 7: Commit**

```bash
git add scripts/batch_run.py scripts/tests/test_batch_run.py
git commit -m "feat(batch): main() chạy try-on local trước preflight, resume liền mạch qua RESUME=1"
```

---

### Task 9: Cập nhật `docs/batch-runner.md`

**Files:**
- Modify: `docs/batch-runner.md`

**Interfaces:** không có — chỉ tài liệu.

- [ ] **Step 1: Thêm mục mới sau "### 2.8 Dọn đĩa" (trước "## 3. Đường MCP chat")**

Chèn vào `docs/batch-runner.md`, ngay sau đoạn kết thúc mục 2.8 (dòng có "`_final/` không bao giờ
bị đụng tới.") và trước dòng `---` / `## 3. Đường MCP chat`:

```markdown
### 2.9 Try-on chạy local trước (`provider: gemini`)

Try-on dùng `provider: gemini` là một API call thuần (không cần GPU) — nên nó chạy THẲNG TỪ MÁY
BẠN, TRƯỚC KHI đụng tới pod, không tính vào giờ GPU. Không cần làm gì khác: `make batch` tự nhận ra
run nào dùng `provider: gemini` và chạy try-on cho TẤT CẢ run đó trước, rồi mới kiểm pod.

Cần `GEMINI_API_KEY` trong `.env` gốc (lấy ở https://aistudio.google.com/apikey) — chỉ bắt buộc nếu
manifest thật sự có run `provider: gemini`.

Hai kịch bản:

| Tình huống | Việc runner làm |
|---|---|
| Pod đã sẵn (đang chạy) | Try-on local xong → chảy thẳng sang `motion`/`enhance` như bình thường, một lệnh |
| Pod chưa thuê/chưa bật | Try-on local xong → DỪNG, báo `make gpu-provision` (hoặc `gpu-up`) → chạy tiếp bằng `make batch FILE=... RESUME=1` |

Ảnh try-on đã lưu ở `out/<lô>/runs/<run>/01-tryon.png` ngay cả khi lô dừng ở bước kiểm pod — không
mất, không tốn Gemini lần hai khi `RESUME=1`.

`provider: qwen` (tự host, GPU thật) không đổi gì — vẫn cần pod như trước, chạy chung lô được với
run dùng `provider: gemini`.

Lý do thiết kế: [`superpowers/specs/2026-08-21-batch-local-tryon-design.md`](superpowers/specs/2026-08-21-batch-local-tryon-design.md).
```

- [ ] **Step 2: Đọc lại toàn bộ file, xác nhận không mâu thuẫn với các mục khác**

Kiểm bằng mắt: mục "0. Mô hình tinh thần" (nguyên tắc 3: "Tuần tự, không song song") không bị mâu
thuẫn — nguyên tắc đó nói về chặng TRÊN POD (một GPU), không áp cho Pha A (try-on local, không đụng
pod). Nếu thấy cần, thêm một câu ngoặc đơn làm rõ ngay tại nguyên tắc 3 trong mục 0:

Tìm dòng (mục "### Ba nguyên tắc", nguyên tắc 3):
```
3. **Tuần tự, không song song.** Pod có một GPU, và `run_enhance` gọi `comfy_recycle` để xả RAM/VRAM
   của Wan trước mỗi pha nặng — hai job chồng nhau phá đúng giả định "lúc này GPU chỉ có mình tôi"
   mà các lời gọi đó dựa vào.
```

Sửa thành:

```
3. **Tuần tự, không song song — trên pod.** Pod có một GPU, và `run_enhance` gọi `comfy_recycle`
   để xả RAM/VRAM của Wan trước mỗi pha nặng — hai job chồng nhau phá đúng giả định "lúc này GPU
   chỉ có mình tôi" mà các lời gọi đó dựa vào. Try-on local (`provider: gemini`, §2.9) không đụng
   pod nên chạy đồng thời được, không vi phạm nguyên tắc này.
```

- [ ] **Step 3: Commit**

```bash
git add docs/batch-runner.md
git commit -m "docs(batch): thêm mục 2.9 — try-on local trước khi thuê pod"
```

---

## Sau khi xong cả 9 task

- [ ] Chạy `make batch-test` lần cuối — toàn bộ suite (cũ + mới) phải xanh.
- [ ] Chạy `make check-batch-params` — đảm bảo Task 2-4 không vô tình đổi tên/giá trị param nào mà
      `scripts/batch-params.json` chưa biết (không nên có thay đổi, vì manifest schema không đổi).
- [ ] `motions-studio/setup/scrub-secrets.sh --check` — đảm bảo không có key/domain thật lọt vào
      test fixture hoặc `docs/batch-runner.md` (mọi key trong test đều là chuỗi giả `AIza` + `x`*35).
