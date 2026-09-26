# Image Studio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Flow-style "Studio" space in the iOS app — projects with an image grid and a prompt
composer that generates or edits images with Nano Banana Pro / 2 / 2 Lite or Qwen Image 3.0 Pro,
reached through a slide-out sidebar that switches between the Motion space and Studio.

**Architecture:** The VPS bot's phone API (`scripts/httpapi/server.py`) gains `/v1/studio/*`
routes over a new `control/studio.py` store (JSON index + files under `batch/studio/`) and a
`control/studio_runner.py` that calls the existing Gemini/Qwen helpers in
`batchlib/local_tryon.py` on a background thread pool; the app posts a generation (202) and polls
the project. MotionKit gets models + a `StudioStore`; the app gets `SpaceShell` (sidebar) and the
Studio screens.

**Tech Stack:** Python 3 stdlib (`urllib`, `http.server`, `concurrent.futures`, `unittest`),
ffmpeg/ffprobe (already on the VPS), Swift 6 / SwiftUI / Observation, Swift Testing, XCTest UI tests.

**Spec:** `docs/superpowers/specs/2026-09-26-image-studio-design.md`

## Global Constraints

- Everything new is written in English (code, comments, commits). No `# #region ALD` markers.
- `motions-studio/setup/scrub-secrets.sh --check` must exit 0 before every commit (public repo).
- Prompts are sent verbatim to every provider — **no translation**, Qwen included.
- Model keys (API contract, fixed): `nano-banana-pro` → `gemini-3-pro-image` (imageSize `2K`),
  `nano-banana-2` → `gemini-3.1-flash-image` (imageSize `2K`, **default**),
  `nano-banana-2-lite` → `gemini-3.1-flash-lite-image` (no imageSize — pricing page lists 1K only),
  `qwen-image-3` → `QWEN_IMAGE_MODEL` read live (`qwen-image-3.0-pro` in `.env`).
- Reference caps: Gemini 14, Qwen 3. Over the cap → `422 too_many_refs`, never silent truncation.
- Aspects: `16:9`, `4:3`, `1:1`, `3:4`, `9:16`. Count: 1–4.
- Reference kinds: `material` (`owner/name`), `tryon` (library id), `run_tryon` (`run_id/index`),
  `studio` (`project_id/image_id`). The app never sends a path.
- References are snapshotted into the project at submit time; refs downscaled to ≤2048 px per side.
- Studio files live in `batch/studio/` — never under `out/`.
- Generation POST requires an `Idempotency-Key` (it spends money); answers `202`.
- Prices (USD per output image; source https://ai.google.dev/gemini-api/docs/pricing read
  2026-09-26): Pro 2K `0.134`, Nano Banana 2 2K `0.101`, Lite 1K `0.0336`. Qwen: `0.07` — converted
  from the Bailian card's ¥0.5 (2K output) + ¥0.02 per input; replaced by the billed figure in Task 11.
- The Motion space (today's 5-tab `TabView`) is unchanged apart from a ☰ button;
  `KillBanner`/`SpendBanner` stay visible in both spaces.
- Reference thumbnails in the composer show **no ✕ by default**; long-press dims the backdrop,
  shows an enlarged preview above the composer and an ⓧ on the thumbnail.

## Review Focus

1. **Bot restarts mid-generation** (auto-deploy on push to `main`): slots left `queued`/`running`
   must become `error: interrupted` on the next start, not spin forever in the app — pinned in Task 2
   (`test_recover_interrupted_marks_unfinished_slots`) and Task 4 (`make_server` calls it).
2. **Project deleted while its generation is still running**: the worker thread must not crash or
   resurrect the project — pinned in Task 3 (`test_deleted_project_mid_run_is_ignored`).
3. **Phone retries the generation POST after a dropped connection**: same key must not bill twice —
   pinned in Task 4 (`test_generation_post_is_idempotent`) and Task 6 (`generateRetriesWithSameKey`).
4. **A HEIC / huge / non-image reference** (a video material, a 6000 px photo): video refs refused
   with `422 ref_not_image`; big images downscaled before sending — pinned in Task 3
   (`test_fit_image_downscales_large_images`) and Task 4 (`test_video_material_ref_is_422`).
5. **Gemini answers with text instead of an image** (safety block): the slot error must carry the
   reason, not a JSON dump — pinned in Task 1 (`test_no_image_reason_prefers_block_reason_and_text`).

---

## File Structure

| File | Responsibility |
|---|---|
| `scripts/batchlib/local_tryon.py` (modify) | `gemini_image_bytes`, `gemini_no_image_reason`, `qwen_image_generate`; `gemini_edit` / `qwen_max_edit` delegate to them (try-on behaviour unchanged) |
| `scripts/control/studio.py` (create) | `StudioStore`: projects, generations, slots, ref snapshots, recovery |
| `scripts/control/studio_runner.py` (create) | model catalog + prices, request validation, `fit_image`, provider calls, `StudioRunner` thread pool |
| `scripts/httpapi/server.py` (modify) | `/v1/studio/*` routes, ref resolution, wiring in `make_server` |
| `scripts/tests/test_batch_local_tryon_studio.py` (create) | provider helper tests |
| `scripts/tests/test_batch_control_studio.py` (create) | store + runner tests |
| `scripts/tests/test_batch_control_http_studio.py` (create) | route tests |
| `ios/MotionKit/Sources/MotionKit/Models/Studio.swift` (create) | Codable models |
| `ios/MotionKit/Sources/MotionKit/Stores/StudioStore.swift` (create) | project list/detail, composer state, pricing/gating, generate, polling, promote |
| `ios/MotionKit/Tests/MotionKitTests/StudioStoreTests.swift` (create) | store tests |
| `ios/MotionApp/Shell/SpaceShell.swift`, `SidebarView.swift` (create) | slide-out sidebar + space switch |
| `ios/MotionApp/Studio/*.swift` (create) | project screen, grid, viewer, composer, settings sheet, source picker, "Edit in Studio" sheet |
| `ios/MotionApp/MotionApp.swift`, `RootView.swift` (modify) | `studio` store, `selectedSpace`, ☰ buttons |
| `ios/MotionApp/RunFlow/TryonPreviewCard.swift`, `Materials/SavedTryonsView.swift` (modify) | "Edit in Studio" |
| `ios/MotionAppUITests/StudioSmokeTests.swift` (create) | zero-spend live UI smoke |
| `ios/MotionKit/Sources/motion-contract/main.swift` (modify) | decode Studio GETs |

---

### Task 1: Provider helpers in `local_tryon.py`

**Files:**
- Modify: `scripts/batchlib/local_tryon.py:456-575` (`gemini_edit`, the Qwen comment block, `qwen_max_edit`)
- Test: `scripts/tests/test_batch_local_tryon_studio.py`

**Interfaces:**
- Produces:
  - `gemini_no_image_reason(data: dict) -> str`
  - `gemini_image_bytes(images: list[tuple[bytes, str]], prompt: str, key: str, *, model: str | None = None, aspect_ratio: str | None = None, image_size: str | None = None, base_url: str = GEMINI_API_BASE) -> bytes` — raises `JobError`
  - `qwen_image_generate(images: list[tuple[bytes, str]], prompt: str, key: str, *, n: int = 1, size: str | None = None, model: str | None = None, negative_prompt: str | None = None) -> list[bytes]` — raises `JobError`; 0–3 images; more than 3 → `JobError`
  - `gemini_edit` / `qwen_max_edit` keep their signatures and behaviour.

- [ ] **Step 1: Write the failing tests**

```python
# scripts/tests/test_batch_local_tryon_studio.py
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import batchlib.local_tryon as lt
from batchlib.client import JobError


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(routes):
    """routes: list of (predicate(req_or_url) -> bool, bytes). Records requests."""
    seen = []

    def urlopen(req, timeout=None):
        seen.append(req)
        for match, body in routes:
            if match(req):
                return _Resp(body)
        raise AssertionError(f"unexpected request {getattr(req, 'full_url', req)}")
    return urlopen, seen


class TestGeminiHelpers(unittest.TestCase):
    def test_image_bytes_returns_the_first_inline_image(self):
        body = json.dumps({"candidates": [{"content": {"parts": [
            {"text": "here"}, {"inlineData": {"mimeType": "image/png", "data": "aGk="}}]}}]}).encode()
        urlopen, seen = _fake_urlopen([(lambda r: True, body)])
        with mock.patch.object(lt.urllib.request, "urlopen", urlopen):
            out = lt.gemini_image_bytes([], "a cat", "AIza-test", model="gemini-3.1-flash-image",
                                        aspect_ratio="9:16", image_size="2K")
        self.assertEqual(out, b"hi")
        sent = json.loads(seen[0].data)
        self.assertEqual(sent["generationConfig"]["imageConfig"], {"aspectRatio": "9:16", "imageSize": "2K"})
        self.assertIn("gemini-3.1-flash-image:generateContent", seen[0].full_url)

    def test_no_image_reason_prefers_block_reason_and_text(self):
        data = {"promptFeedback": {"blockReason": "SAFETY"},
                "candidates": [{"finishReason": "IMAGE_SAFETY",
                                "content": {"parts": [{"text": "I can't make that image."}]}}]}
        reason = lt.gemini_no_image_reason(data)
        self.assertIn("SAFETY", reason)
        self.assertIn("IMAGE_SAFETY", reason)
        self.assertIn("I can't make that image.", reason)
        self.assertNotIn("{", reason)

    def test_image_bytes_raises_with_the_reason(self):
        body = json.dumps({"candidates": [{"finishReason": "IMAGE_SAFETY",
                                           "content": {"parts": [{"text": "blocked"}]}}]}).encode()
        urlopen, _ = _fake_urlopen([(lambda r: True, body)])
        with mock.patch.object(lt.urllib.request, "urlopen", urlopen):
            with self.assertRaises(JobError) as ctx:
                lt.gemini_image_bytes([], "x", "AIza-test")
        self.assertIn("IMAGE_SAFETY", str(ctx.exception))
        self.assertIn("blocked", str(ctx.exception))


class TestQwenGenerate(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(lt, "_qwen_image_url", return_value="https://ws.example/api")
        p.start(); self.addCleanup(p.stop)

    def _answer(self, urls):
        return json.dumps({"output": {"choices": [
            {"message": {"content": [{"image": u} for u in urls]}}]}}).encode()

    def test_text_only_sends_one_text_part_and_n(self):
        urlopen, seen = _fake_urlopen([
            (lambda r: isinstance(r, str) and r.endswith("/1.png"), b"one"),
            (lambda r: isinstance(r, str) and r.endswith("/2.png"), b"two"),
            (lambda r: not isinstance(r, str), self._answer(["https://o/1.png", "https://o/2.png"])),
        ])
        with mock.patch.object(lt.urllib.request, "urlopen", urlopen):
            out = lt.qwen_image_generate([], "một con mèo", "sk-test", n=2, size="1536*2048",
                                         model="qwen-image-3.0-pro")
        self.assertEqual(out, [b"one", b"two"])
        body = json.loads(seen[0].data)
        self.assertEqual(body["input"]["messages"][0]["content"], [{"text": "một con mèo"}])
        self.assertEqual(body["parameters"]["n"], 2)
        self.assertEqual(body["parameters"]["size"], "1536*2048")
        self.assertEqual(body["model"], "qwen-image-3.0-pro")

    def test_more_than_three_images_is_refused_not_truncated(self):
        with self.assertRaises(JobError):
            lt.qwen_image_generate([(b"x", "image/png")] * 4, "edit", "sk-test")

    def test_qwen_max_edit_still_writes_the_first_image(self):
        urlopen, _ = _fake_urlopen([
            (lambda r: isinstance(r, str), b"img"),
            (lambda r: not isinstance(r, str), self._answer(["https://o/1.png"])),
        ])
        out = Path(self.id().replace(".", "_") + ".png")
        self.addCleanup(lambda: out.unlink(missing_ok=True))
        with mock.patch.object(lt.urllib.request, "urlopen", urlopen):
            lt.qwen_max_edit([(b"a", "image/png")] * 5, "p", "sk-test", out)
        self.assertEqual(out.read_bytes(), b"img")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_local_tryon_studio.py' -v`
Expected: FAIL — `AttributeError: module 'batchlib.local_tryon' has no attribute 'gemini_image_bytes'`.

- [ ] **Step 3: Implement.** Replace `gemini_edit` (lines 456–489) with:

```python
def gemini_no_image_reason(data: dict) -> str:
    """Why a generateContent answer carried no image, in words a phone can show.

    Gemini answers a refused image with 200 and no inlineData: the reason sits in
    promptFeedback.blockReason, a candidate's finishReason, and/or a text part.
    Image Studio shows this string to the user, so it must not be a JSON dump.
    """
    bits = []
    block = (data.get("promptFeedback") or {}).get("blockReason")
    if block:
        bits.append(f"blocked: {block}")
    for cand in data.get("candidates") or []:
        if cand.get("finishReason") and cand["finishReason"] != "STOP":
            bits.append(f"finish: {cand['finishReason']}")
        for part in (cand.get("content") or {}).get("parts") or []:
            if part.get("text"):
                bits.append(part["text"].strip())
    return " · ".join(bits) or "no image in the response"


def gemini_image_bytes(images: list[tuple[bytes, str]], prompt: str, key: str, *,
                       model: str | None = None, aspect_ratio: str | None = None,
                       image_size: str | None = None, base_url: str = GEMINI_API_BASE) -> bytes:
    """One generateContent call → the first returned image's bytes.

    image_size: generationConfig.imageConfig.imageSize ("1K"/"2K"/"4K", capital K). Without it
    gemini-3-pro-image falls back to 1K (~1 MP) however large the input — measured 2026-09-16
    (run IMG67441-IMG6957-IMG68943-tiktok178952): the camera-reframe candidate came out 768x1376,
    the 1K preset for 9:16.
    """
    parts = [{"text": prompt}]
    for data, mime in images:
        parts.append({"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode()}})
    gcfg = {"responseModalities": ["IMAGE"]}
    if aspect_ratio or image_size:
        gcfg["imageConfig"] = {}
        if aspect_ratio:
            gcfg["imageConfig"]["aspectRatio"] = aspect_ratio
        if image_size:
            gcfg["imageConfig"]["imageSize"] = image_size
    url = f"{base_url}/v1beta/models/{model or GEMINI_IMAGE_MODEL}:generateContent"
    data = _post_json(url, {"key": key},
                      {"contents": [{"parts": parts}], "generationConfig": gcfg}, 300)
    for cand in (data.get("candidates") or []):
        for part in ((cand.get("content") or {}).get("parts") or []):
            blob = part.get("inlineData") or part.get("inline_data")
            if blob and blob.get("data"):
                return base64.b64decode(blob["data"])
    raise JobError(f"Gemini returned no image: {gemini_no_image_reason(data)}")


def gemini_edit(images: list[tuple[bytes, str]], prompt: str, key: str, out_path: Path,
                aspect_ratio: str | None = None, model: str | None = None,
                base_url: str = GEMINI_API_BASE, image_size: str | None = None) -> Path:
    """urllib port of linux.py:_gemini_edit (3455-3478). See gemini_image_bytes."""
    out_path.write_bytes(gemini_image_bytes(images, prompt, key, model=model,
                                            aspect_ratio=aspect_ratio, image_size=image_size,
                                            base_url=base_url))
    return out_path
```

Replace the Qwen comment above `_REPO_ROOT` (lines 491–494) with:

```python
# linux.py: the "Qwen-Image (DashScope Model Studio) — provider='qwen-max' & Gemini fallback" block
# (added 2026-08-25). The code default below is the GA qwen-image-edit-plus; the VPS and local .env
# set QWEN_IMAGE_MODEL=qwen-image-3.0-pro (access granted since; checked 2026-09-26). Qwen Image 3.0
# does text-to-image (text only) and editing (1-3 images) on the same endpoint — see
# qwen_image_generate.
```

Replace `qwen_max_edit` (lines 541–575) with:

```python
QWEN_MAX_IMAGES = 3


def qwen_image_generate(images: list[tuple[bytes, str]], prompt: str, key: str, *, n: int = 1,
                        size: str | None = None, model: str | None = None,
                        negative_prompt: str | None = None) -> list[bytes]:
    """One synchronous DashScope multimodal-generation call → every returned image's bytes.

    0 images = text-to-image; 1-3 = edit (Bailian API reference for Qwen Image 3.0, read
    2026-09-26). More than 3 is refused rather than sliced: Image Studio promises never to drop a
    reference silently. Result URLs are OSS links that live 24 h, so they are downloaded now.
    """
    if len(images) > QWEN_MAX_IMAGES:
        raise JobError(f"Qwen accepts at most {QWEN_MAX_IMAGES} images, got {len(images)}")
    content = [{"image": f"data:{mime};base64,{base64.b64encode(data).decode()}"} for data, mime in images]
    content.append({"text": prompt})
    body = {"model": model or QWEN_IMAGE_MODEL,
            "input": {"messages": [{"role": "user", "content": content}]},
            "parameters": {"watermark": False, "n": n}}
    if negative_prompt:
        body["parameters"]["negative_prompt"] = negative_prompt
    if size:
        body["parameters"]["size"] = size
    req = urllib.request.Request(
        _qwen_image_url(), data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise JobError(f"Qwen API {exc.code}: {exc.read()[:300].decode('utf-8', 'replace')}") from exc
    except OSError as exc:
        raise JobError(f"Qwen API did not respond: {exc}") from exc
    urls = [item["image"]
            for choice in ((data.get("output") or {}).get("choices") or [])
            for item in ((choice.get("message") or {}).get("content") or [])
            if isinstance(item, dict) and item.get("image")]
    if not urls:
        raise JobError(f"Qwen returned no image: {data.get('code') or ''} {data.get('message') or ''}".strip())
    out = []
    for url in urls:
        try:
            with urllib.request.urlopen(url, timeout=180) as resp:
                out.append(resp.read())
        except OSError as exc:
            raise JobError(f"Qwen image download failed: {exc}") from exc
    return out


def qwen_max_edit(images: list[tuple[bytes, str]], prompt: str, key: str, out_path: Path,
                  negative_prompt: str | None = None, model: str | None = None,
                  size: str | None = None) -> Path:
    """Port of linux.py:_qwen_max_edit. Try-on keeps its historical behaviour: extra images are
    sliced to the first 3, and only the first result is kept."""
    results = qwen_image_generate(images[:QWEN_MAX_IMAGES], prompt, key, size=size, model=model,
                                  negative_prompt=negative_prompt)
    out_path.write_bytes(results[0])
    return out_path
```

- [ ] **Step 4: Run the new tests and the existing try-on tests**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_local_tryon*.py' -v`
Expected: all PASS. If an existing test asserted the old Vietnamese error strings ("Gemini không
trả ảnh", "Qwen-Max không trả ảnh"), update that assertion to the new English message — the
behaviour (a `JobError`) is unchanged.

- [ ] **Step 5: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add scripts/batchlib/local_tryon.py scripts/tests/test_batch_local_tryon_studio.py
git commit -m "local_tryon: byte-returning Gemini/Qwen helpers with text-to-image and readable refusals"
```

---

### Task 2: `StudioStore` (persistence)

**Files:**
- Create: `scripts/control/studio.py`
- Test: `scripts/tests/test_batch_control_studio.py`

**Interfaces:**
- Produces:
  - `class StudioError(Exception)` with `.code`, `.message`
  - `StudioStore(studio_dir: Path, owner: str)` with:
    - `list_projects() -> list[dict]` — `{id, title, created_at, updated_at, cover, spent_usd, image_count}`, newest `updated_at` first; `cover` = newest done image id or `None`
    - `create_project(title: str = "") -> dict`
    - `get_project(pid: str) -> dict` — full project + `spent_usd`; raises `StudioError("not_found")`
    - `rename(pid: str, title: str) -> dict`
    - `delete_project(pid: str) -> None`
    - `add_generation(pid, *, prompt: str, model: str, aspect: str, count: int, refs: list[tuple[dict, Path]], unit_price_usd: float, copy=shutil.copyfile) -> dict`
    - `set_slot(pid, gid, slot: int, *, status: str, image: bytes | None = None, error: str | None = None) -> None` — raises `StudioError("not_found")` if the project/generation is gone
    - `resolve_image(pid, image_id) -> Path | None`, `resolve_ref(pid, file) -> Path | None`
    - `generation(pid, gid) -> dict`
    - `recover_interrupted() -> int`
  - Generation dict: `{id, created_at, prompt, model, aspect, count, refs: [{kind, id, file}], slots: [{status, image?, error?}], status, unit_price_usd, est_cost_usd}`; `status` is `running` if any slot is `queued`/`running`, else `error` if no slot is `done`, else `done`.

- [ ] **Step 1: Write the failing tests**

```python
# scripts/tests/test_batch_control_studio.py
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from control.studio import StudioError, StudioStore

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 16


class StoreBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        # studio_runner reads GEMINI_API_KEY from <root>/.env to decide availability.
        (self.root / ".env").write_text("GEMINI_API_KEY=AIza-test\n")
        self.store = StudioStore(self.root / "studio", "app")

    def ref_file(self, name="ref.png", data=PNG):
        path = self.root / name
        path.write_bytes(data)
        return path


class TestProjects(StoreBase):
    def test_create_list_rename_delete(self):
        p = self.store.create_project()
        self.assertEqual(p["title"], "")
        self.assertEqual(p["generations"], [])
        self.assertEqual([x["id"] for x in self.store.list_projects()], [p["id"]])
        self.assertEqual(self.store.rename(p["id"], "  Model A ")["title"], "Model A")
        self.store.delete_project(p["id"])
        self.assertEqual(self.store.list_projects(), [])
        self.assertFalse((self.root / "studio" / "app" / p["id"]).exists())

    def test_unknown_project_is_not_found(self):
        with self.assertRaises(StudioError) as ctx:
            self.store.get_project("nope")
        self.assertEqual(ctx.exception.code, "not_found")

    def test_title_is_capped(self):
        p = self.store.create_project()
        self.assertEqual(len(self.store.rename(p["id"], "x" * 500)["title"]), 80)


class TestGenerations(StoreBase):
    def setUp(self):
        super().setUp()
        self.pid = self.store.create_project()["id"]

    def test_refs_are_snapshotted_and_survive_source_deletion(self):
        src = self.ref_file()
        gen = self.store.add_generation(self.pid, prompt="fix hand", model="nano-banana-2",
                                        aspect="9:16", count=2,
                                        refs=[({"kind": "material", "id": "app/ref.png"}, src)],
                                        unit_price_usd=0.101)
        src.unlink()
        ref = gen["refs"][0]
        self.assertEqual((ref["kind"], ref["id"]), ("material", "app/ref.png"))
        self.assertEqual(self.store.resolve_ref(self.pid, ref["file"]).read_bytes(), PNG)
        self.assertEqual([s["status"] for s in gen["slots"]], ["queued", "queued"])
        self.assertEqual(gen["status"], "running")
        self.assertAlmostEqual(gen["est_cost_usd"], 0.202)

    def test_slots_aggregate_and_spent_counts_only_done(self):
        gen = self.store.add_generation(self.pid, prompt="p", model="nano-banana-pro", aspect="1:1",
                                        count=2, refs=[], unit_price_usd=0.134)
        self.store.set_slot(self.pid, gen["id"], 0, status="done", image=PNG)
        self.store.set_slot(self.pid, gen["id"], 1, status="error", error="blocked: SAFETY")
        got = self.store.generation(self.pid, gen["id"])
        self.assertEqual(got["status"], "done")
        self.assertEqual(got["slots"][1]["error"], "blocked: SAFETY")
        image_id = got["slots"][0]["image"]
        self.assertEqual(self.store.resolve_image(self.pid, image_id).read_bytes(), PNG)
        self.assertAlmostEqual(self.store.get_project(self.pid)["spent_usd"], 0.134)
        summary = self.store.list_projects()[0]
        self.assertEqual((summary["cover"], summary["image_count"]), (image_id, 1))

    def test_all_failed_is_error(self):
        gen = self.store.add_generation(self.pid, prompt="p", model="nano-banana-2", aspect="1:1",
                                        count=1, refs=[], unit_price_usd=0.1)
        self.store.set_slot(self.pid, gen["id"], 0, status="error", error="quota")
        self.assertEqual(self.store.generation(self.pid, gen["id"])["status"], "error")

    def test_image_suffix_follows_magic_bytes(self):
        gen = self.store.add_generation(self.pid, prompt="p", model="nano-banana-2", aspect="1:1",
                                        count=1, refs=[], unit_price_usd=0.1)
        self.store.set_slot(self.pid, gen["id"], 0, status="done", image=b"\xff\xd8\xff" + b"0" * 8)
        image_id = self.store.generation(self.pid, gen["id"])["slots"][0]["image"]
        self.assertEqual(self.store.resolve_image(self.pid, image_id).suffix, ".jpg")

    def test_set_slot_on_deleted_project_raises_not_found(self):
        gen = self.store.add_generation(self.pid, prompt="p", model="nano-banana-2", aspect="1:1",
                                        count=1, refs=[], unit_price_usd=0.1)
        self.store.delete_project(self.pid)
        with self.assertRaises(StudioError):
            self.store.set_slot(self.pid, gen["id"], 0, status="done", image=PNG)

    def test_path_escapes_resolve_to_none(self):
        self.assertIsNone(self.store.resolve_image(self.pid, "../../app"))
        self.assertIsNone(self.store.resolve_ref(self.pid, "../x"))

    def test_recover_interrupted_marks_unfinished_slots(self):
        gen = self.store.add_generation(self.pid, prompt="p", model="nano-banana-2", aspect="1:1",
                                        count=2, refs=[], unit_price_usd=0.1)
        self.store.set_slot(self.pid, gen["id"], 0, status="done", image=PNG)
        self.store.set_slot(self.pid, gen["id"], 1, status="running")
        fresh = StudioStore(self.root / "studio", "app")        # a bot restart
        self.assertEqual(fresh.recover_interrupted(), 1)
        got = fresh.generation(self.pid, gen["id"])
        self.assertEqual([s["status"] for s in got["slots"]], ["done", "error"])
        self.assertEqual(got["slots"][1]["error"], "interrupted")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_studio.py' -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'control.studio'`.

- [ ] **Step 3: Implement `scripts/control/studio.py`**

```python
"""Image Studio projects (spec 2026-09-26-image-studio-design.md).

Shaped like control/tryon_library.py: one JSON index per owner, read and
written fresh under control.LOCK on every call, rewritten atomically. Files
live under studio_dir/<owner>/<project_id>/{refs,img}/ — outside out/, so
batch-clean never touches them. References are COPIED in at submit time:
materials are pruned after 7 days and library entries can be deleted, and
neither may break a project's history or its Retry.
"""
from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path

import control
from control.paths import safe_child

TITLE_MAX = 80
_UNFINISHED = ("queued", "running")


class StudioError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def _suffix_for(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".png"


def _aggregate(slots: list[dict]) -> str:
    if any(s["status"] in _UNFINISHED for s in slots):
        return "running"
    return "done" if any(s["status"] == "done" for s in slots) else "error"


def _spent(project: dict) -> float:
    return round(sum(g.get("unit_price_usd", 0.0) * sum(s["status"] == "done" for s in g["slots"])
                     for g in project["generations"]), 4)


class StudioStore:
    def __init__(self, studio_dir: Path, owner: str):
        self.studio_dir, self.owner = studio_dir, owner
        self._index_path = studio_dir / f"{owner}.json"
        self._files_dir = studio_dir / owner

    # -- persistence -------------------------------------------------------

    def _load(self) -> list[dict]:
        try:
            data = json.loads(self._index_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError, OSError):
            return []
        return data if isinstance(data, list) else []

    def _save(self, projects: list[dict]) -> None:
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._index_path.with_name(f"{self._index_path.name}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(projects, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self._index_path)

    @staticmethod
    def _find(projects: list[dict], pid: str) -> dict:
        project = next((p for p in projects if p.get("id") == pid), None)
        if project is None:
            raise StudioError("not_found", "no such studio project")
        return project

    def _dir(self, pid: str) -> Path | None:
        return safe_child(self._files_dir, pid)

    @staticmethod
    def _view(project: dict) -> dict:
        view = json.loads(json.dumps(project))
        for gen in view["generations"]:
            gen["status"] = _aggregate(gen["slots"])
            gen["est_cost_usd"] = round(gen["unit_price_usd"] * gen["count"], 4)
        view["spent_usd"] = _spent(project)
        return view

    # -- projects ------------------------------------------------------------

    def list_projects(self) -> list[dict]:
        with control.LOCK:
            projects = self._load()
        out = []
        for p in projects:
            images = [s["image"] for g in p["generations"] for s in g["slots"] if s.get("image")]
            out.append({"id": p["id"], "title": p["title"], "created_at": p["created_at"],
                        "updated_at": p["updated_at"], "cover": images[-1] if images else None,
                        "spent_usd": _spent(p), "image_count": len(images)})
        return sorted(out, key=lambda p: p["updated_at"], reverse=True)

    def create_project(self, title: str = "") -> dict:
        now = time.time()
        project = {"id": uuid.uuid4().hex[:12], "title": str(title or "").strip()[:TITLE_MAX],
                   "created_at": now, "updated_at": now, "generations": []}
        with control.LOCK:
            projects = self._load()
            projects.append(project)
            self._save(projects)
        return self._view(project)

    def get_project(self, pid: str) -> dict:
        with control.LOCK:
            return self._view(self._find(self._load(), pid))

    def rename(self, pid: str, title: str) -> dict:
        with control.LOCK:
            projects = self._load()
            project = self._find(projects, pid)
            project["title"] = str(title or "").strip()[:TITLE_MAX]
            project["updated_at"] = time.time()
            self._save(projects)
            return self._view(project)

    def delete_project(self, pid: str) -> None:
        with control.LOCK:
            projects = self._load()
            self._find(projects, pid)
            self._save([p for p in projects if p["id"] != pid])
        directory = self._dir(pid)
        if directory is not None:
            shutil.rmtree(directory, ignore_errors=True)

    # -- generations ---------------------------------------------------------

    def add_generation(self, pid: str, *, prompt: str, model: str, aspect: str, count: int,
                       refs: list[tuple[dict, Path]], unit_price_usd: float,
                       copy=shutil.copyfile) -> dict:
        gid = uuid.uuid4().hex[:10]
        directory = self._dir(pid)
        if directory is None:
            raise StudioError("not_found", "no such studio project")
        ref_dir = directory / "refs"
        ref_dir.mkdir(parents=True, exist_ok=True)
        snapshots = []
        for i, (ref, src) in enumerate(refs):        # slow copies stay outside the lock
            # Only PNG/JPEG snapshots: mime_of() derives the MIME type from the suffix, and both
            # providers take these two. `copy` (fit_image) converts anything else.
            suffix = ".jpg" if src.suffix.lower() in (".jpg", ".jpeg") else ".png"
            dest = ref_dir / f"{gid}-{i}{suffix}"
            copy(src, dest)
            snapshots.append({"kind": ref["kind"], "id": ref["id"], "file": dest.name})
        now = time.time()
        gen = {"id": gid, "created_at": now, "prompt": prompt, "model": model, "aspect": aspect,
               "count": count, "refs": snapshots, "unit_price_usd": unit_price_usd,
               "slots": [{"status": "queued"} for _ in range(count)]}
        with control.LOCK:
            projects = self._load()
            project = self._find(projects, pid)
            project["generations"].append(gen)
            project["updated_at"] = now
            self._save(projects)
            return self._view(project)["generations"][-1]

    def generation(self, pid: str, gid: str) -> dict:
        project = self.get_project(pid)
        gen = next((g for g in project["generations"] if g["id"] == gid), None)
        if gen is None:
            raise StudioError("not_found", "no such generation")
        return gen

    def set_slot(self, pid: str, gid: str, slot: int, *, status: str,
                 image: bytes | None = None, error: str | None = None) -> None:
        image_name = None
        if image is not None:
            directory = self._dir(pid)
            if directory is None or not directory.is_dir():
                raise StudioError("not_found", "no such studio project")
            (directory / "img").mkdir(exist_ok=True)
            image_name = f"{gid}-{slot}{_suffix_for(image)}"
            (directory / "img" / image_name).write_bytes(image)
        with control.LOCK:
            projects = self._load()
            project = self._find(projects, pid)
            gen = next((g for g in project["generations"] if g["id"] == gid), None)
            if gen is None or not 0 <= slot < len(gen["slots"]):
                raise StudioError("not_found", "no such generation slot")
            entry = {"status": status}
            if image_name:
                entry["image"] = Path(image_name).stem
            if error:
                entry["error"] = error[:500]
            gen["slots"][slot] = entry
            project["updated_at"] = time.time()
            self._save(projects)

    def resolve_image(self, pid: str, image_id: str) -> Path | None:
        directory = self._dir(pid)
        img_dir = directory / "img" if directory is not None else None
        if img_dir is None or not img_dir.is_dir() or safe_child(img_dir, image_id) is None:
            return None
        return next((p for p in img_dir.iterdir() if p.stem == image_id and p.is_file()), None)

    def resolve_ref(self, pid: str, file: str) -> Path | None:
        directory = self._dir(pid)
        path = safe_child(directory / "refs", file) if directory is not None else None
        return path if path is not None and path.is_file() else None

    def recover_interrupted(self) -> int:
        """Called once at bot start: the threads that owned unfinished slots are gone."""
        changed = 0
        with control.LOCK:
            projects = self._load()
            for p in projects:
                for g in p["generations"]:
                    for i, s in enumerate(g["slots"]):
                        if s["status"] in _UNFINISHED:
                            g["slots"][i] = {"status": "error", "error": "interrupted"}
                            changed += 1
            if changed:
                self._save(projects)
        return changed
```

Check `control/paths.safe_child` returns `None` for `..` and absolute names (it does for the
tryon library); `test_path_escapes_resolve_to_none` pins it here too.

- [ ] **Step 4: Run tests**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_studio.py' -v`
Expected: PASS (all 10).

- [ ] **Step 5: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add scripts/control/studio.py scripts/tests/test_batch_control_studio.py
git commit -m "control/studio: project store with ref snapshots and restart recovery"
```

---

### Task 3: `StudioRunner` (catalog, validation, provider calls)

**Files:**
- Create: `scripts/control/studio_runner.py`
- Test: `scripts/tests/test_batch_control_studio.py` (append classes)

**Interfaces:**
- Consumes: `StudioStore` (Task 2); `gemini_image_bytes`, `qwen_image_generate`, `qwen_max_configured`, `qwen_setting`, `mime_of`, `img_size` (Task 1 / existing).
- Produces:
  - `MODELS: dict[str, ModelSpec]`, `DEFAULT_MODEL = "nano-banana-2"`, `ASPECTS`, `MAX_COUNT = 4`
  - `ModelSpec(key, label, provider, api_model, image_size, max_refs, price_usd)` (frozen dataclass)
  - `catalog(root: Path) -> dict` → `{"models": [{key, label, provider, max_refs, price_usd, available, default}], "aspects": [...], "max_count": 4}`
  - `parse_request(body: dict, root: Path) -> GenerationRequest` — raises `StudioError` (`bad_request`, `unknown_model`, `model_unavailable`, `too_many_refs`)
  - `GenerationRequest(prompt, model: ModelSpec, aspect, count, refs: list[dict])`
  - `fit_image(src: Path, dest: Path) -> None` — copy, or ffmpeg-downscale to ≤2048 px per side
  - `StudioRunner(store, root, *, gemini=None, qwen=None, workers=4, log=print)` with `submit(pid, req, ref_paths: list[Path]) -> dict` and `wait_idle(timeout) -> bool` (tests)
  - `gemini` callable: `(spec, prompt, images: list[tuple[bytes,str]], aspect) -> bytes`; `qwen`: `(spec, prompt, images, aspect, count) -> list[bytes]`

- [ ] **Step 1: Append the failing tests** (above the file's `if __name__ == "__main__":` block)

```python
# appended to scripts/tests/test_batch_control_studio.py
import shutil
import subprocess
import threading
from unittest import mock

import control.studio_runner as sr
from control.studio_runner import StudioRunner, catalog, fit_image, parse_request


class TestCatalogAndParse(StoreBase):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(sr, "qwen_max_configured", return_value=True)
        p.start(); self.addCleanup(p.stop)

    def body(self, **kw):
        base = {"prompt": "make it red", "model": "nano-banana-2", "aspect": "9:16", "count": 1,
                "refs": []}
        base.update(kw)
        return base

    def test_catalog_lists_four_models_with_prices(self):
        cat = catalog(self.root)
        self.assertEqual([m["key"] for m in cat["models"]],
                         ["nano-banana-pro", "nano-banana-2", "nano-banana-2-lite", "qwen-image-3"])
        self.assertEqual([m["key"] for m in cat["models"] if m["default"]], ["nano-banana-2"])
        self.assertTrue(all(m["price_usd"] > 0 for m in cat["models"]))
        self.assertEqual(cat["aspects"], ["16:9", "4:3", "1:1", "3:4", "9:16"])

    def test_qwen_unavailable_without_config(self):
        with mock.patch.object(sr, "qwen_max_configured", return_value=False):
            self.assertFalse(next(m for m in catalog(self.root)["models"]
                                  if m["key"] == "qwen-image-3")["available"])
            with self.assertRaises(sr.StudioError) as ctx:
                parse_request(self.body(model="qwen-image-3"), self.root)
            self.assertEqual(ctx.exception.code, "model_unavailable")

    def test_parse_rejects_bad_input(self):
        cases = [(self.body(prompt="  "), "bad_request"),
                 (self.body(model="dall-e"), "unknown_model"),
                 (self.body(aspect="2:1"), "bad_request"),
                 (self.body(count=5), "bad_request"),
                 (self.body(count="2"), "bad_request"),
                 (self.body(refs=[{"kind": "file", "id": "/etc/passwd"}]), "bad_request"),
                 (self.body(model="qwen-image-3",
                            refs=[{"kind": "material", "id": f"app/{i}.png"} for i in range(4)]),
                  "too_many_refs")]
        for body, code in cases:
            with self.subTest(body=body):
                with self.assertRaises(sr.StudioError) as ctx:
                    parse_request(body, self.root)
                self.assertEqual(ctx.exception.code, code)

    def test_qwen_accepts_text_only(self):
        req = parse_request(self.body(model="qwen-image-3"), self.root)
        self.assertEqual((req.model.key, req.refs), ("qwen-image-3", []))


class TestFitImage(StoreBase):
    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
    def test_fit_image_downscales_large_images(self):
        src = self.root / "big.png"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=red:s=3000x1000",
                        "-frames:v", "1", str(src)], check=True)
        dest = self.root / "out.png"
        fit_image(src, dest)
        self.assertEqual(sr.img_size(dest), (2048, 683))

    def test_small_images_are_copied_byte_for_byte(self):
        src = self.ref_file()
        dest = self.root / "copy.png"
        with mock.patch.object(sr, "img_size", return_value=(800, 600)):
            fit_image(src, dest)
        self.assertEqual(dest.read_bytes(), PNG)


class TestRunner(StoreBase):
    def setUp(self):
        super().setUp()
        self.pid = self.store.create_project()["id"]
        self.calls = []
        p = mock.patch.object(sr, "qwen_max_configured", return_value=True)
        p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(sr, "_key", return_value="k")
        p.start(); self.addCleanup(p.stop)

    def runner(self, gemini=None, qwen=None):
        r = StudioRunner(self.store, self.root, gemini=gemini, qwen=qwen, log=lambda *_: None)
        self.addCleanup(r.shutdown)
        return r

    def req(self, **kw):
        body = {"prompt": "p", "model": "nano-banana-2", "aspect": "1:1", "count": 2, "refs": []}
        body.update(kw)
        return parse_request(body, self.root)

    def test_gemini_slots_are_independent(self):
        def gemini(spec, prompt, images, aspect):
            self.calls.append((spec.api_model, prompt, len(images), aspect))
            if len(self.calls) == 2:
                raise sr.JobError("Gemini returned no image: finish: IMAGE_SAFETY")
            return PNG
        r = self.runner(gemini=gemini)
        gen = r.submit(self.pid, self.req(), [])
        self.assertTrue(r.wait_idle(5))
        got = self.store.generation(self.pid, gen["id"])
        self.assertEqual(sorted(s["status"] for s in got["slots"]), ["done", "error"])
        self.assertIn("IMAGE_SAFETY", next(s["error"] for s in got["slots"] if s["status"] == "error"))
        self.assertEqual(self.calls[0][0], "gemini-3.1-flash-image")

    def test_qwen_is_one_call_with_n(self):
        def qwen(spec, prompt, images, aspect, count):
            self.calls.append((count, len(images)))
            return [PNG] * count
        r = self.runner(qwen=qwen)
        src = self.ref_file()
        gen = r.submit(self.pid, self.req(model="qwen-image-3", count=3,
                                          refs=[{"kind": "material", "id": "app/ref.png"}]), [src])
        self.assertTrue(r.wait_idle(5))
        self.assertEqual(self.calls, [(3, 1)])
        self.assertEqual(self.store.generation(self.pid, gen["id"])["status"], "done")

    def test_qwen_failure_fails_every_slot(self):
        def qwen(spec, prompt, images, aspect, count):
            raise sr.JobError("Qwen API 400: InvalidParameter")
        r = self.runner(qwen=qwen)
        gen = r.submit(self.pid, self.req(model="qwen-image-3"), [])
        self.assertTrue(r.wait_idle(5))
        got = self.store.generation(self.pid, gen["id"])
        self.assertEqual([s["status"] for s in got["slots"]], ["error", "error"])

    def test_unexpected_exception_becomes_a_slot_error(self):
        def gemini(spec, prompt, images, aspect):
            raise RuntimeError("boom")
        r = self.runner(gemini=gemini)
        gen = r.submit(self.pid, self.req(count=1), [])
        self.assertTrue(r.wait_idle(5))
        self.assertEqual(self.store.generation(self.pid, gen["id"])["slots"][0]["error"], "internal error")

    def test_deleted_project_mid_run_is_ignored(self):
        started, release = threading.Event(), threading.Event()

        def gemini(spec, prompt, images, aspect):
            started.set()
            release.wait(5)
            return PNG
        r = self.runner(gemini=gemini)
        r.submit(self.pid, self.req(count=1), [])
        self.assertTrue(started.wait(5))
        self.store.delete_project(self.pid)
        release.set()
        self.assertTrue(r.wait_idle(5))
        self.assertEqual(self.store.list_projects(), [])
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_studio.py' -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'control.studio_runner'`.

- [ ] **Step 3: Implement `scripts/control/studio_runner.py`**

```python
"""Image Studio generation: model catalog, request validation, provider calls.

Runs inside motion-bot on a small thread pool (spec approach A): the phone
gets 202 at once and polls, because Nano Banana Pro takes tens of seconds
per image and Cloudflare cuts a request near 100 s.
"""
from __future__ import annotations

import shutil
import subprocess
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from batchlib.client import JobError
from batchlib.config import env_get
from batchlib.local_tryon import (gemini_image_bytes, img_size, mime_of, qwen_image_generate,
                                  qwen_max_configured, qwen_setting)
from control.studio import StudioError, StudioStore

MAX_REF_SIDE = 2048
MAX_COUNT = 4
MAX_PROMPT = 4000
REF_KINDS = ("material", "tryon", "run_tryon", "studio")
# Qwen `size` is "W*H" with total pixels <= 2048*2048 (Bailian Qwen Image 3.0 reference,
# read 2026-09-26). Gemini takes the ratio string itself.
ASPECTS = {"16:9": "2048*1152", "4:3": "2048*1536", "1:1": "2048*2048",
           "3:4": "1536*2048", "9:16": "1152*2048"}


@dataclass(frozen=True)
class ModelSpec:
    key: str
    label: str
    provider: str            # "gemini" | "qwen"
    api_model: str | None    # None = read QWEN_IMAGE_MODEL live
    image_size: str | None
    max_refs: int
    price_usd: float


# USD per output image. Gemini: https://ai.google.dev/gemini-api/docs/pricing, read 2026-09-26
# (Pro 1K/2K $0.134; Nano Banana 2 2K $0.101; Lite lists 1K only, $0.0336 — so no imageSize).
# Qwen: ~$0.07 converted from the Bailian card's ¥0.5 per 2K output + ¥0.02 per input image;
# replace with the Singapore-workspace billed figure after the first live run.
MODELS = {m.key: m for m in (
    ModelSpec("nano-banana-pro", "Nano Banana Pro", "gemini", "gemini-3-pro-image", "2K", 14, 0.134),
    ModelSpec("nano-banana-2", "Nano Banana 2", "gemini", "gemini-3.1-flash-image", "2K", 14, 0.101),
    ModelSpec("nano-banana-2-lite", "Nano Banana 2 Lite", "gemini", "gemini-3.1-flash-lite-image",
              None, 14, 0.0336),
    ModelSpec("qwen-image-3", "Qwen Image 3.0 Pro", "qwen", None, None, 3, 0.07),
)}
DEFAULT_MODEL = "nano-banana-2"


@dataclass(frozen=True)
class GenerationRequest:
    prompt: str
    model: ModelSpec
    aspect: str
    count: int
    refs: list


def _available(spec: ModelSpec, root: Path) -> bool:
    if spec.provider == "qwen":
        return qwen_max_configured(root)
    return bool(env_get(root / ".env", "GEMINI_API_KEY"))


def _key(provider: str, root: Path) -> str:
    return env_get(root / ".env", "GEMINI_API_KEY" if provider == "gemini" else "DASHSCOPE_API_KEY")


def catalog(root: Path) -> dict:
    return {"models": [{"key": m.key, "label": m.label, "provider": m.provider,
                        "max_refs": m.max_refs, "price_usd": m.price_usd,
                        "available": _available(m, root), "default": m.key == DEFAULT_MODEL}
                       for m in MODELS.values()],
            "aspects": list(ASPECTS), "max_count": MAX_COUNT}


def parse_request(body: dict, root: Path) -> GenerationRequest:
    prompt = body.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise StudioError("bad_request", "prompt is required")
    if len(prompt) > MAX_PROMPT:
        raise StudioError("bad_request", f"prompt is longer than {MAX_PROMPT} characters")
    spec = MODELS.get(body.get("model"))
    if spec is None:
        raise StudioError("unknown_model", f"unknown model: {body.get('model')!r}")
    if not _available(spec, root):
        raise StudioError("model_unavailable", f"{spec.label} is not configured on the server")
    aspect = body.get("aspect")
    if aspect not in ASPECTS:
        raise StudioError("bad_request", f"aspect must be one of {', '.join(ASPECTS)}")
    count = body.get("count")
    if type(count) is not int or not 1 <= count <= MAX_COUNT:
        raise StudioError("bad_request", f"count must be an integer 1-{MAX_COUNT}")
    refs = body.get("refs") or []
    if not isinstance(refs, list) or not all(
            isinstance(r, dict) and r.get("kind") in REF_KINDS and isinstance(r.get("id"), str)
            and r["id"] for r in refs):
        raise StudioError("bad_request", f"each ref needs kind in {REF_KINDS} and an id")
    if len(refs) > spec.max_refs:
        raise StudioError("too_many_refs",
                          f"{spec.label} takes at most {spec.max_refs} reference images, got {len(refs)}")
    return GenerationRequest(prompt, spec, aspect, count, [{"kind": r["kind"], "id": r["id"]} for r in refs])


def fit_image(src: Path, dest: Path) -> None:
    """Write `src` to `dest` (a .png or .jpg name), downscaled to <= MAX_REF_SIDE px per side.

    Qwen refuses inputs over 10 MB and Gemini's inline payload grows with size; a 2048 px side keeps
    the detail both models use. A same-format image that already fits is copied byte for byte;
    anything else (a .webp, an oversize photo) goes through ffmpeg. Unreadable → ref_not_image.
    """
    size = img_size(src)
    a, b = src.suffix.lower(), dest.suffix.lower()
    same_format = a == b or {a, b} <= {".jpg", ".jpeg"}
    # Unprobeable (size None) but already PNG/JPEG: pass it through and let the provider judge,
    # rather than refusing a file ffprobe merely failed to read.
    if same_format and (size is None or max(size) <= MAX_REF_SIDE):
        shutil.copyfile(src, dest)
        return
    vf = []
    if size is not None and max(size) > MAX_REF_SIDE:
        w, h = size
        vf = ["-vf", f"scale={MAX_REF_SIDE}:-2" if w >= h else f"scale=-2:{MAX_REF_SIDE}"]
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), *vf, "-frames:v", "1", str(dest)],
                       capture_output=True, timeout=60)
    if r.returncode != 0:
        raise StudioError("ref_not_image", f"could not read reference image {src.name}")


def gemini_call(spec: ModelSpec, prompt: str, images: list, aspect: str, *, key: str) -> bytes:
    return gemini_image_bytes(images, prompt, key, model=spec.api_model, aspect_ratio=aspect,
                              image_size=spec.image_size)


def qwen_call(spec: ModelSpec, prompt: str, images: list, aspect: str, count: int, *, key: str,
              root: Path) -> list[bytes]:
    model = qwen_setting("QWEN_IMAGE_MODEL", root, default="qwen-image-3.0-pro")
    return qwen_image_generate(images, prompt, key, n=count, size=ASPECTS[aspect], model=model)


class StudioRunner:
    def __init__(self, store: StudioStore, root: Path, *, gemini=None, qwen=None,
                 workers: int = 4, log=print):
        self.store, self.root, self.log = store, root, log
        self._gemini = gemini or (lambda spec, p, imgs, a: gemini_call(
            spec, p, imgs, a, key=_key("gemini", root)))
        self._qwen = qwen or (lambda spec, p, imgs, a, n: qwen_call(
            spec, p, imgs, a, n, key=_key("qwen", root), root=root))
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="studio")
        self._pending = 0
        self._idle = threading.Condition()

    def submit(self, pid: str, req: GenerationRequest, ref_paths: list[Path]) -> dict:
        gen = self.store.add_generation(
            pid, prompt=req.prompt, model=req.model.key, aspect=req.aspect, count=req.count,
            refs=list(zip(req.refs, ref_paths)), unit_price_usd=req.model.price_usd, copy=fit_image)
        images = []
        for ref in gen["refs"]:
            path = self.store.resolve_ref(pid, ref["file"])
            images.append((path.read_bytes(), mime_of(path)))
        if req.model.provider == "qwen":
            self._spawn(self._run_qwen, pid, gen["id"], req, images)
        else:
            for slot in range(req.count):
                self._spawn(self._run_gemini_slot, pid, gen["id"], slot, req, images)
        return gen

    def _spawn(self, fn, *args) -> None:
        with self._idle:
            self._pending += 1
        self._pool.submit(self._guard, fn, *args)

    def _guard(self, fn, *args) -> None:
        try:
            fn(*args)
        finally:
            with self._idle:
                self._pending -= 1
                self._idle.notify_all()

    def _mark(self, pid, gid, slot, **kw) -> bool:
        try:
            self.store.set_slot(pid, gid, slot, **kw)
            return True
        except StudioError:            # project deleted while the call ran — drop the result
            return False

    def _error_text(self, exc: Exception) -> str:
        if isinstance(exc, JobError):
            return str(exc)
        self.log("studio: generation failed\n" + traceback.format_exc())
        return "internal error"

    def _run_gemini_slot(self, pid, gid, slot, req, images) -> None:
        if not self._mark(pid, gid, slot, status="running"):
            return
        try:
            data = self._gemini(req.model, req.prompt, images, req.aspect)
        except Exception as exc:
            self._mark(pid, gid, slot, status="error", error=self._error_text(exc))
            return
        self._mark(pid, gid, slot, status="done", image=data)

    def _run_qwen(self, pid, gid, req, images) -> None:
        for slot in range(req.count):
            if not self._mark(pid, gid, slot, status="running"):
                return
        try:
            results = self._qwen(req.model, req.prompt, images, req.aspect, req.count)
        except Exception as exc:
            text = self._error_text(exc)
            for slot in range(req.count):
                self._mark(pid, gid, slot, status="error", error=text)
            return
        for slot in range(req.count):
            if slot < len(results):
                self._mark(pid, gid, slot, status="done", image=results[slot])
            else:
                self._mark(pid, gid, slot, status="error", error="Qwen returned fewer images than asked")

    def wait_idle(self, timeout: float) -> bool:
        with self._idle:
            return self._idle.wait_for(lambda: self._pending == 0, timeout)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
```

- [ ] **Step 4: Run tests**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_studio.py' -v`
Expected: PASS (ffmpeg test skipped only if ffmpeg is missing locally; it is installed on the Mac).

- [ ] **Step 5: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add scripts/control/studio_runner.py scripts/tests/test_batch_control_studio.py
git commit -m "control/studio_runner: model catalog, validation and background generation"
```

---

### Task 4: `/v1/studio/*` routes

**Files:**
- Modify: `scripts/httpapi/server.py` (imports; `_DOMAIN_STATUS`; `_handle` except tuple; `_route` dispatch before `if rest[:1] == ["draft"]`; new `_route_studio`, `_studio_ref_path`; `make_server`)
- Test: `scripts/tests/test_batch_control_http_studio.py`

**Interfaces:**
- Consumes: `StudioStore`, `StudioRunner`, `parse_request`, `catalog` (Tasks 2–3); `materials.resolve_material`, `materials.stage_file`, `materials.material_item`, `TryonLibrary.resolve_image/save`, `app_runs.tryon_image`, `IdempotencyStore`.
- Produces (HTTP contract used by Task 6):
  - `GET /v1/studio/models` → `catalog()`
  - `GET /v1/studio/projects` → `{"projects": [summary]}`
  - `POST /v1/studio/projects` `{title?}` → `201 {"project": project}`
  - `GET|PATCH|DELETE /v1/studio/projects/{pid}` → `{"project": project}` / `{"project": project}` / `204`
  - `POST /v1/studio/projects/{pid}/generations` (header `Idempotency-Key`) → `202 {"generation": gen}`
  - `GET /v1/studio/projects/{pid}/images/{image_id}`, `GET /v1/studio/projects/{pid}/refs/{file}` → file
  - `POST /v1/studio/projects/{pid}/images/{image_id}/promote` `{to: "material"|"tryon"}` → `{"material": item}` or `{"entry": record}`
  - Errors: `404 not_found`, `400 bad_request`, `422 unknown_model|model_unavailable|too_many_refs|ref_not_found|ref_not_image`, `409 outcome_unknown` (idempotency).

- [ ] **Step 1: Write the failing tests**

```python
# scripts/tests/test_batch_control_http_studio.py
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import control.studio_runner as sr
from control.studio_runner import StudioRunner
from test_batch_control_http import FakeAppRuns, HttpWriteBase

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 16


class StudioHttpBase(HttpWriteBase):
    def setUp(self):
        super().setUp()
        for target in ("qwen_max_configured", "_available"):
            p = mock.patch.object(sr, target, return_value=True)
            p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(sr, "_key", return_value="k")
        p.start(); self.addCleanup(p.stop)
        self.gemini_calls = []

        def gemini(spec, prompt, images, aspect):
            self.gemini_calls.append((spec.key, prompt, len(images)))
            return PNG
        self.server.studio_runner.shutdown()
        self.server.studio_runner = StudioRunner(self.server.studio, self.batch.parent,
                                                 gemini=gemini, log=lambda *_: None)
        self.addCleanup(self.server.studio_runner.shutdown)
        app = self.batch / "tg-staging" / "app"
        app.mkdir(parents=True)
        (app / "me.png").write_bytes(PNG)
        (app / "dance.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")

    def json_call(self, method, path, body=None, headers=None):
        resp, data = self.send(method, path, json_body=body if body is not None else None,
                               headers=headers)
        return resp.status, (json.loads(data) if data else None)

    def new_project(self):
        status, body = self.json_call("POST", "/v1/studio/projects", {})
        self.assertEqual(status, 201)
        return body["project"]["id"]

    def generate(self, pid, body, key="k-1"):
        return self.json_call("POST", f"/v1/studio/projects/{pid}/generations", body,
                              headers={"Idempotency-Key": key})


class TestStudioRoutes(StudioHttpBase):
    def test_models_catalog(self):
        status, body = self.json_call("GET", "/v1/studio/models")
        self.assertEqual(status, 200)
        self.assertIn("nano-banana-2", [m["key"] for m in body["models"]])

    def test_project_crud(self):
        pid = self.new_project()
        status, body = self.json_call("PATCH", f"/v1/studio/projects/{pid}", {"title": "Model A"})
        self.assertEqual((status, body["project"]["title"]), (200, "Model A"))
        status, body = self.json_call("GET", "/v1/studio/projects")
        self.assertEqual([p["id"] for p in body["projects"]], [pid])
        status, _ = self.json_call("DELETE", f"/v1/studio/projects/{pid}")
        self.assertEqual(status, 204)
        status, body = self.json_call("GET", f"/v1/studio/projects/{pid}")
        self.assertEqual((status, body["error"]["code"]), (404, "not_found"))

    def test_generation_with_material_ref_runs_and_serves_the_image(self):
        pid = self.new_project()
        status, body = self.generate(pid, {"prompt": "red dress", "model": "nano-banana-2",
                                           "aspect": "9:16", "count": 2,
                                           "refs": [{"kind": "material", "id": "app/me.png"}]})
        self.assertEqual(status, 202, body)
        self.assertTrue(self.server.studio_runner.wait_idle(5))
        _, body = self.json_call("GET", f"/v1/studio/projects/{pid}")
        gen = body["project"]["generations"][0]
        self.assertEqual(gen["status"], "done")
        self.assertEqual(self.gemini_calls[0], ("nano-banana-2", "red dress", 1))
        resp, data = self.send("GET", f"/v1/studio/projects/{pid}/images/{gen['slots'][0]['image']}")
        self.assertEqual((resp.status, data), (200, PNG))
        resp, data = self.send("GET", f"/v1/studio/projects/{pid}/refs/{gen['refs'][0]['file']}")
        self.assertEqual((resp.status, data), (200, PNG))

    def test_generation_needs_an_idempotency_key(self):
        pid = self.new_project()
        status, body = self.json_call("POST", f"/v1/studio/projects/{pid}/generations",
                                      {"prompt": "x", "model": "nano-banana-2", "aspect": "1:1",
                                       "count": 1})
        self.assertEqual(status, 400)

    def test_generation_post_is_idempotent(self):
        pid = self.new_project()
        body = {"prompt": "x", "model": "nano-banana-2", "aspect": "1:1", "count": 1, "refs": []}
        first = self.generate(pid, body, key="same")
        second = self.generate(pid, body, key="same")
        self.assertEqual(first, second)
        self.assertTrue(self.server.studio_runner.wait_idle(5))
        self.assertEqual(len(self.gemini_calls), 1)

    def test_video_material_ref_is_422(self):
        pid = self.new_project()
        status, body = self.generate(pid, {"prompt": "x", "model": "nano-banana-2", "aspect": "1:1",
                                           "count": 1,
                                           "refs": [{"kind": "material", "id": "app/dance.mp4"}]})
        self.assertEqual((status, body["error"]["code"]), (422, "ref_not_image"))

    def test_missing_ref_is_422_and_nothing_is_recorded(self):
        pid = self.new_project()
        status, body = self.generate(pid, {"prompt": "x", "model": "nano-banana-2", "aspect": "1:1",
                                           "count": 1,
                                           "refs": [{"kind": "tryon", "id": "gone"}]})
        self.assertEqual((status, body["error"]["code"]), (422, "ref_not_found"))
        _, body = self.json_call("GET", f"/v1/studio/projects/{pid}")
        self.assertEqual(body["project"]["generations"], [])

    def test_too_many_refs_for_qwen_is_422(self):
        pid = self.new_project()
        status, body = self.generate(pid, {"prompt": "x", "model": "qwen-image-3", "aspect": "1:1",
                                           "count": 1,
                                           "refs": [{"kind": "material", "id": "app/me.png"}] * 4})
        self.assertEqual((status, body["error"]["code"]), (422, "too_many_refs"))

    def test_studio_ref_and_run_tryon_ref_resolve(self):
        pid = self.new_project()
        self.generate(pid, {"prompt": "x", "model": "nano-banana-2", "aspect": "1:1", "count": 1})
        self.assertTrue(self.server.studio_runner.wait_idle(5))
        _, body = self.json_call("GET", f"/v1/studio/projects/{pid}")
        image_id = body["project"]["generations"][0]["slots"][0]["image"]
        preview = self.out / "b1" / "tryon.png"
        preview.write_bytes(PNG)
        fake = FakeAppRuns()
        fake.tryon_image_path = preview
        self.server.app_runs = fake
        status, body = self.generate(pid, {"prompt": "y", "model": "nano-banana-2", "aspect": "1:1",
                                           "count": 1,
                                           "refs": [{"kind": "studio", "id": f"{pid}/{image_id}"},
                                                    {"kind": "run_tryon", "id": "run-1/0"}]},
                                     key="k-2")
        self.assertEqual(status, 202, body)
        self.assertEqual(len(body["generation"]["refs"]), 2)

    def test_promote_to_material_and_tryon(self):
        pid = self.new_project()
        self.generate(pid, {"prompt": "x", "model": "nano-banana-2", "aspect": "1:1", "count": 1})
        self.assertTrue(self.server.studio_runner.wait_idle(5))
        _, body = self.json_call("GET", f"/v1/studio/projects/{pid}")
        image_id = body["project"]["generations"][0]["slots"][0]["image"]
        base = f"/v1/studio/projects/{pid}/images/{image_id}/promote"
        status, body = self.json_call("POST", base, {"to": "material"})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["material"]["id"].startswith("app/studio-"))
        status, body = self.json_call("POST", base, {"to": "tryon"})
        self.assertEqual((status, body["entry"]["provider"]), (200, "studio:nano-banana-2"))
        status, body = self.json_call("POST", base, {"to": "elsewhere"})
        self.assertEqual(status, 400)


class TestStudioStartup(StudioHttpBase):
    def test_make_server_recovers_interrupted_slots(self):
        from httpapi.server import make_server
        pid = self.new_project()
        gen = self.server.studio.add_generation(pid, prompt="p", model="nano-banana-2", aspect="1:1",
                                                count=1, refs=[], unit_price_usd=0.1)
        again = make_server(token="t", batch_dir=self.batch, out_dir=self.out, port=0,
                            log=lambda *_: None)
        self.addCleanup(again.server_close)
        self.addCleanup(again.studio_runner.shutdown)
        self.assertEqual(again.studio.generation(pid, gen["id"])["slots"][0]["error"], "interrupted")


if __name__ == "__main__":
    unittest.main()
```

`FakeAppRuns.tryon_image` (test_batch_control_http.py:976) returns `self.tryon_image_path` for any
run/index, which is what the `run_tryon` test relies on.

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_http_studio.py' -v`
Expected: FAIL — `AttributeError: '_Server' object has no attribute 'studio_runner'`.

- [ ] **Step 3: Implement in `scripts/httpapi/server.py`**

Imports (with the other `control` imports):

```python
import control.studio as studio
import control.studio_runner as studio_runner
from control.idempotency import IdempotencyError, IdempotencyStore
```

Add to `_DOMAIN_STATUS`:

```python
                  # Image Studio (control/studio_runner.py): a request the models cannot take.
                  "unknown_model": 422, "model_unavailable": 422, "too_many_refs": 422,
                  "ref_not_found": 422, "ref_not_image": 422,
```

Add `studio.StudioError` to the domain-exception tuple in `_handle`.

In `_route`, just before `if rest[:1] == ["draft"]:`:

```python
        if rest[:1] == ["studio"]:
            return self._route_studio(method, rest[1:])
```

New methods on `_Handler` (after `_route_draft`):

```python
    def _studio_ref_path(self, ref: dict) -> Path:
        """The file behind one Studio reference. The phone names things, never paths."""
        s, kind, ident = self.server, ref["kind"], ref["id"]
        path = None
        if kind == "material" and ident.count("/") == 1:
            path = materials.resolve_material(s.staging_root, *ident.split("/"))
        elif kind == "tryon":
            path = s.tryon_library.resolve_image(ident)
        elif kind == "run_tryon" and ident.count("/") == 1 and s.app_runs is not None:
            path = s.app_runs.tryon_image(*ident.split("/"))
        elif kind == "studio" and ident.count("/") == 1:
            path = s.studio.resolve_image(*ident.split("/"))
        if path is None:
            raise ApiError(422, "ref_not_found", f"reference not found: {kind} {ident}")
        if path.suffix.lower() not in materials.IMAGE_SUFFIXES:
            raise ApiError(422, "ref_not_image", f"reference is not an image: {kind} {ident}")
        return path

    def _route_studio(self, method: str, rest: list[str]) -> None:
        s = self.server
        store = s.studio
        if method == "GET" and rest == ["models"]:
            return self._send_json(200, studio_runner.catalog(s.repo_root))
        if rest == ["projects"]:
            if method == "GET":
                return self._send_json(200, {"projects": store.list_projects()})
            if method == "POST":
                title = self._read_json().get("title") or ""
                return self._send_json(201, {"project": store.create_project(str(title))})
        if len(rest) == 2 and rest[0] == "projects":
            pid = rest[1]
            if method == "GET":
                return self._send_json(200, {"project": store.get_project(pid)})
            if method == "PATCH":
                return self._send_json(200, {"project": store.rename(pid, str(self._read_json().get("title") or ""))})
            if method == "DELETE":
                store.delete_project(pid)
                return self._send_empty(204)
        if method == "POST" and len(rest) == 3 and rest[0] == "projects" and rest[2] == "generations":
            pid = rest[1]
            key = self._idempotency_key()
            store.get_project(pid)                                  # 404 before anything else
            req = studio_runner.parse_request(self._read_json(), s.repo_root)
            paths = [self._studio_ref_path(r) for r in req.refs]   # 422 before spending
            replay = s.studio_idem.begin("studio-generate", key)
            if replay is not None:
                return self._send_json(*replay)
            try:
                gen = s.studio_runner.submit(pid, req, paths)
            except Exception:
                s.studio_idem.forget("studio-generate", key)       # nothing was sent to a provider
                raise
            body = {"generation": gen}
            s.studio_idem.finish("studio-generate", key, 202, body)
            return self._send_json(202, body)
        if method == "GET" and len(rest) == 4 and rest[0] == "projects" and rest[2] in ("images", "refs"):
            path = (store.resolve_image(rest[1], rest[3]) if rest[2] == "images"
                    else store.resolve_ref(rest[1], rest[3]))
            if path is None:
                raise NOT_FOUND
            try:
                self._settle_body()
                return send_file(self, path)
            except FileNotFoundError:
                raise NOT_FOUND
        if (method == "POST" and len(rest) == 5 and rest[0] == "projects" and rest[2] == "images"
                and rest[4] == "promote"):
            pid, image_id = rest[1], rest[3]
            target = self._read_json().get("to")
            if target not in ("material", "tryon"):
                raise ApiError(400, "bad_request", "to must be 'material' or 'tryon'")
            path = store.resolve_image(pid, image_id)
            if path is None:
                raise NOT_FOUND
            model = next((g["model"] for g in store.get_project(pid)["generations"]
                          if image_id.startswith(g["id"] + "-")), "studio")
            if target == "material":
                staged = materials.stage_file(s.staging_root / materials.APP_OWNER, path,
                                              f"studio-{image_id}{path.suffix}")
                item = s.material_roles.annotate([materials.material_item(materials.APP_OWNER, staged)])[0]
                return self._send_json(200, {"material": item})
            record = s.tryon_library.save(image=path, material_ids={}, provider=f"studio:{model}")
            return self._send_json(200, {"entry": record})
        raise NOT_FOUND
```

`_idempotency_key()` already raises 400 when the header is missing — confirm by reading it
(server.py:207) and keep that behaviour.

In `make_server`, after `server.tryon_library = ...`:

```python
    server.studio = studio.StudioStore(batch_dir / "studio", materials.APP_OWNER)
    # Slots a previous process left queued/running have no thread any more (a deploy restarts
    # motion-bot); without this the phone would poll them forever.
    server.studio.recover_interrupted()
    server.studio_runner = studio_runner.StudioRunner(server.studio, batch_dir.parent, log=log)
    # Its own store object over the same directory the bot's AppRuns uses; scopes keep keys apart.
    server.studio_idem = IdempotencyStore(batch_dir / "idempotency")
```

- [ ] **Step 4: Run the new tests and the whole HTTP suite**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_http*.py' -v`
Expected: PASS. Then `make batch-test` — expected: PASS (no other suite touched).

- [ ] **Step 5: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add scripts/httpapi/server.py scripts/tests/test_batch_control_http_studio.py
git commit -m "httpapi: /v1/studio routes — projects, idempotent generations, refs, promote"
```

---

### Task 5: Document the API in the control-plane spec

**Files:**
- Modify: `docs/superpowers/specs/2026-09-21-vps-control-plane-api-design.md` (append a section)

- [ ] **Step 1: Append** a section `## Slice 7 — Image Studio (2026-09-26)` listing the endpoints
  exactly as in Task 4's "Produces", the four ref kinds, the `studio-generate` idempotency scope,
  the `batch/studio/` location, and a pointer to `2026-09-26-image-studio-design.md`.
- [ ] **Step 2: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add docs/superpowers/specs/2026-09-21-vps-control-plane-api-design.md
git commit -m "Control-plane API spec: Image Studio slice"
```

---

### Task 6: MotionKit models and `StudioStore`

**Files:**
- Create: `ios/MotionKit/Sources/MotionKit/Models/Studio.swift`
- Create: `ios/MotionKit/Sources/MotionKit/Stores/StudioStore.swift`
- Test: `ios/MotionKit/Tests/MotionKitTests/StudioStoreTests.swift`

**Interfaces:**
- Consumes: HTTP contract from Task 4; `APIClient.get/post/patch/delete/data/spendPost`, `MotionJSON.decoder`, `APIError.userMessage`.
- Produces (used by Tasks 7–10):
  - `StudioRef(kind: StudioRefKind, id: String)`; `StudioRefKind: material, tryon, runTryon ("run_tryon"), studio`
  - `StudioModelInfo`, `StudioCatalog`, `StudioProjectSummary`, `StudioProject`, `StudioGeneration`, `StudioSlot`
  - `@MainActor @Observable final class StudioStore` with:
    - state: `catalog`, `projects`, `project: StudioProject?`, `attachments: [StudioRef]`, `prompt`, `modelKey`, `aspect`, `count`, `message`, `isSending`
    - `loadCatalog()`, `loadProjects()`, `open(_ id: String)`, `close()`, `createProject() -> String?`, `rename(_ id:, to:)`, `delete(_ id:)`
    - `attach(_:)`, `detach(_:)`, `send() -> Bool`, `retry(_ generation:) -> Bool`
    - `promote(imageID:, to: StudioPromoteTarget) -> String?` (returns a confirmation line)
    - `thumbnail(for ref: StudioRef) -> Data?`, `image(projectID:, imageID:) -> Data?`
    - derived: `selectedModel`, `estimateUSD`, `canSend`, `disabledReason(for model:) -> String?`, `hasRunning`
    - `static let pollInterval: Duration = .seconds(3)`; init takes `sleep:` and `makeKey:` for tests.

- [ ] **Step 1: Write the failing tests**

```swift
// ios/MotionKit/Tests/MotionKitTests/StudioStoreTests.swift
import Foundation
import Testing
@testable import MotionKit

extension URLProtocolTests {
@Suite @MainActor struct StudioStoreTests {
    nonisolated static let catalog = #"""
    {"models":[
      {"key":"nano-banana-pro","label":"Nano Banana Pro","provider":"gemini","max_refs":14,"price_usd":0.134,"available":true,"default":false},
      {"key":"nano-banana-2","label":"Nano Banana 2","provider":"gemini","max_refs":14,"price_usd":0.101,"available":true,"default":true},
      {"key":"qwen-image-3","label":"Qwen Image 3.0 Pro","provider":"qwen","max_refs":3,"price_usd":0.07,"available":false,"default":false}
    ],"aspects":["16:9","4:3","1:1","3:4","9:16"],"max_count":4}
    """#

    nonisolated static func project(status: String) -> String {
        #"{"project":{"id":"p1","title":"","created_at":1,"updated_at":2,"spent_usd":0.1,"generations":[{"id":"g1","created_at":2,"prompt":"red","model":"nano-banana-2","aspect":"9:16","count":1,"refs":[{"kind":"material","id":"app/me.png","file":"g1-0.png"}],"slots":[{"status":"\#(status)"\#(status == "done" ? #","image":"g1-0""# : "")}],"status":"\#(status == "done" ? "done" : "running")","unit_price_usd":0.101,"est_cost_usd":0.101}]}}"#
    }

    final class Box: @unchecked Sendable {
        var keys: [String] = []
        var polls = 0
        var failFirstPost = false
        let lock = NSLock()
        func record(key: String?) { lock.lock(); keys.append(key ?? ""); lock.unlock() }
    }

    private func make(box: Box = Box()) -> StudioStore {
        StubURLProtocol.install { request in
            let path = request.url?.path ?? ""
            switch (request.httpMethod ?? "GET", path) {
            case ("GET", "/v1/studio/models"): return TestSupport.json(Self.catalog)
            case ("GET", "/v1/studio/projects"):
                return TestSupport.json(#"{"projects":[{"id":"p1","title":"","created_at":1,"updated_at":2,"cover":null,"spent_usd":0,"image_count":0}]}"#)
            case ("POST", "/v1/studio/projects"):
                return TestSupport.json(#"{"project":{"id":"p2","title":"","created_at":3,"updated_at":3,"spent_usd":0,"generations":[]}}"#, status: 201)
            case ("GET", "/v1/studio/projects/p1"):
                box.lock.lock(); box.polls += 1; let n = box.polls; box.lock.unlock()
                return TestSupport.json(Self.project(status: n >= 3 ? "done" : "running"))
            case ("POST", "/v1/studio/projects/p1/generations"):
                box.record(key: request.value(forHTTPHeaderField: "Idempotency-Key"))
                if box.failFirstPost && box.keys.count == 1 { return (-1, [:], Data()) }   // dropped connection
                return TestSupport.json(#"{"generation":{"id":"g2","created_at":5,"prompt":"blue","model":"nano-banana-2","aspect":"9:16","count":2,"refs":[],"slots":[{"status":"queued"},{"status":"queued"}],"status":"running","unit_price_usd":0.101,"est_cost_usd":0.202}}"#, status: 202)
            default: return (404, [:], Data())
            }
        }
        return StudioStore(client: TestSupport.client(), sleep: { _ in }, makeKey: { "key-1" }, autoPoll: false)
    }

    @Test func catalogPicksTheServerDefaultAndPrices() async {
        let store = make()
        await store.loadCatalog()
        #expect(store.modelKey == "nano-banana-2")
        store.count = 4
        #expect(abs(store.estimateUSD - 0.404) < 0.0001)
    }

    @Test func unavailableAndOverCapModelsAreDisabled() async {
        let store = make()
        await store.loadCatalog()
        let qwen = store.catalog!.models.first { $0.key == "qwen-image-3" }!
        #expect(store.disabledReason(for: qwen) != nil)
        let pro = store.catalog!.models.first { $0.key == "nano-banana-pro" }!
        #expect(store.disabledReason(for: pro) == nil)
        for i in 0..<15 { store.attach(StudioRef(kind: .material, id: "app/\(i).png")) }
        #expect(store.disabledReason(for: pro) != nil)
        #expect(!store.canSend)
    }

    @Test func attachIgnoresDuplicates() {
        let store = make()
        let ref = StudioRef(kind: .tryon, id: "abc")
        store.attach(ref); store.attach(ref)
        #expect(store.attachments == [ref])
        store.detach(ref)
        #expect(store.attachments.isEmpty)
    }

    @Test func sendRequiresAPrompt() async {
        let store = make()
        await store.loadCatalog()
        store.prompt = "   "
        #expect(!store.canSend)
    }

    @Test func generateRetriesWithSameKey() async {
        let box = Box()
        box.failFirstPost = true
        let store = make(box: box)
        await store.loadCatalog()
        await store.open("p1")
        store.prompt = "blue"
        store.count = 2
        let ok = await store.send()
        #expect(ok)
        #expect(box.keys == ["key-1", "key-1"])
        #expect(store.project?.generations.contains { $0.id == "g2" } == true)
        #expect(store.prompt.isEmpty)
    }

    @Test func pollingStopsWhenNothingRuns() async {
        let box = Box()
        let store = make(box: box)
        await store.open("p1")
        await store.pollUntilIdle()
        #expect(store.project?.generations.first?.status == .done)
        #expect(!store.hasRunning)
        #expect(box.polls == 3)
    }

    @Test func createProjectOpensIt() async {
        let store = make()
        let id = await store.createProject()
        #expect(id == "p2")
        #expect(store.project?.id == "p2")
    }
}
}
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ios/MotionKit && swift test --filter StudioStoreTests`
Expected: FAIL to compile — `cannot find 'StudioStore' in scope`.

- [ ] **Step 3: Implement models** — `ios/MotionKit/Sources/MotionKit/Models/Studio.swift`:

```swift
import Foundation

/// Image Studio (spec 2026-09-26). The server names every file; the phone
/// only ever sends `{kind, id}` references.
public enum StudioRefKind: String, Codable, Sendable, Hashable {
    case material, tryon, studio
    case runTryon = "run_tryon"
}

public struct StudioRef: Codable, Sendable, Hashable, Identifiable {
    public let kind: StudioRefKind
    public let id: String
    /// Set on references read back from a generation: the server's snapshot file.
    public let file: String?

    public init(kind: StudioRefKind, id: String, file: String? = nil) {
        self.kind = kind; self.id = id; self.file = file
    }

    /// Two refs to the same thing are the same attachment whatever their snapshot.
    public static func == (a: Self, b: Self) -> Bool { a.kind == b.kind && a.id == b.id }
    public func hash(into h: inout Hasher) { h.combine(kind); h.combine(id) }
}

public struct StudioModelInfo: Decodable, Sendable, Equatable, Identifiable {
    public let key: String
    public let label: String
    public let provider: String
    public let maxRefs: Int
    public let priceUsd: Double
    public let available: Bool
    public let `default`: Bool
    public var id: String { key }
}

public struct StudioCatalog: Decodable, Sendable, Equatable {
    public let models: [StudioModelInfo]
    public let aspects: [String]
    public let maxCount: Int
}

public struct StudioProjectSummary: Decodable, Sendable, Equatable, Identifiable {
    public let id: String
    public let title: String
    public let createdAt: Double
    public let updatedAt: Double
    public let cover: String?
    public let spentUsd: Double
    public let imageCount: Int
}

public enum StudioStatus: String, Decodable, Sendable {
    case queued, running, done, error
}

public struct StudioSlot: Decodable, Sendable, Equatable {
    public let status: StudioStatus
    public let image: String?
    public let error: String?
}

public struct StudioGeneration: Decodable, Sendable, Equatable, Identifiable {
    public let id: String
    public let createdAt: Double
    public let prompt: String
    public let model: String
    public let aspect: String
    public let count: Int
    public let refs: [StudioRef]
    public let slots: [StudioSlot]
    public let status: StudioStatus
    public let unitPriceUsd: Double
    public let estCostUsd: Double
}

public struct StudioProject: Decodable, Sendable, Equatable, Identifiable {
    public let id: String
    public let title: String
    public let createdAt: Double
    public let updatedAt: Double
    public let spentUsd: Double
    public var generations: [StudioGeneration]
}

struct StudioProjectsResponse: Decodable, Sendable { let projects: [StudioProjectSummary] }
struct StudioProjectResponse: Decodable, Sendable { let project: StudioProject }
struct StudioGenerationResponse: Decodable, Sendable { let generation: StudioGeneration }
struct StudioPromoteMaterial: Decodable, Sendable { let material: Material }
struct StudioPromoteEntry: Decodable, Sendable { let entry: TryonLibraryEntry }

public enum StudioPromoteTarget: String, Sendable { case material, tryon }
```

- [ ] **Step 4: Implement the store** — `ios/MotionKit/Sources/MotionKit/Stores/StudioStore.swift`:

```swift
import Foundation
import Observation

/// Image Studio state: the project list for the sidebar, the open project,
/// and the composer (spec 2026-09-26). Generations are sent with an
/// Idempotency-Key and resent with the SAME key after a transport failure,
/// so a dropped connection never bills twice. Not routed through `SpendGate`:
/// that gate serialises pod spends and keeps a ledger across launches, while
/// a Studio send costs cents and its outcome is visible in the project itself.
@MainActor @Observable
public final class StudioStore {
    public static let pollInterval: Duration = .seconds(3)
    static let sendAttempts = 3

    public private(set) var catalog: StudioCatalog?
    public private(set) var projects: [StudioProjectSummary] = []
    public private(set) var project: StudioProject?
    public private(set) var attachments: [StudioRef] = []
    public private(set) var isSending = false
    public var prompt = ""
    public var modelKey = ""
    public var aspect = "9:16"
    public var count = 1
    public var message: String?

    private let client: APIClient
    private let sleep: @Sendable (Duration) async throws -> Void
    private let makeKey: @Sendable () -> String
    private var images: [String: Data] = [:]
    private var pollTask: Task<Void, Never>?
    /// Off in tests, which call `pollUntilIdle()` themselves so the request count is exact.
    private let autoPoll: Bool

    public init(client: APIClient,
                sleep: @escaping @Sendable (Duration) async throws -> Void = { try await Task.sleep(for: $0) },
                makeKey: @escaping @Sendable () -> String = { UUID().uuidString },
                autoPoll: Bool = true) {
        self.client = client
        self.sleep = sleep
        self.makeKey = makeKey
        self.autoPoll = autoPoll
    }

    // MARK: derived

    public var selectedModel: StudioModelInfo? { catalog?.models.first { $0.key == modelKey } }
    public var estimateUSD: Double { (selectedModel?.priceUsd ?? 0) * Double(count) }
    public var hasRunning: Bool {
        project?.generations.contains { $0.status == .running || $0.status == .queued } ?? false
    }

    public func disabledReason(for model: StudioModelInfo) -> String? {
        if !model.available { return "\(model.label) isn't configured on the server." }
        if attachments.count > model.maxRefs {
            return "\(model.label) takes at most \(model.maxRefs) reference images."
        }
        return nil
    }

    public var canSend: Bool {
        guard let model = selectedModel, project != nil, !isSending else { return false }
        return !prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && disabledReason(for: model) == nil
    }

    // MARK: catalog & projects

    public func loadCatalog() async {
        do {
            let cat = try await client.get(StudioCatalog.self, "v1", "studio", "models")
            catalog = cat
            if selectedModel == nil { modelKey = cat.models.first { $0.default }?.key ?? cat.models.first?.key ?? "" }
        } catch { message = error.userMessage }
    }

    public func loadProjects() async {
        do {
            projects = try await client.get(StudioProjectsResponse.self, "v1", "studio", "projects").projects
        } catch { message = error.userMessage }
    }

    public func open(_ id: String) async {
        if project?.id != id { attachments = []; prompt = "" }
        do {
            project = try await client.get(StudioProjectResponse.self, "v1", "studio", "projects", id).project
            startPolling()
        } catch { message = error.userMessage }
    }

    public func close() {
        pollTask?.cancel(); pollTask = nil
        project = nil
    }

    public func createProject() async -> String? {
        struct Body: Encodable, Sendable { let title: String }
        do {
            let created = try await client.post(StudioProjectResponse.self, body: Body(title: ""),
                                                "v1", "studio", "projects").project
            project = created
            attachments = []; prompt = ""
            await loadProjects()
            return created.id
        } catch { message = error.userMessage; return nil }
    }

    public func rename(_ id: String, to title: String) async {
        struct Body: Encodable, Sendable { let title: String }
        do {
            let updated = try await client.patch(StudioProjectResponse.self, body: Body(title: title),
                                                 "v1", "studio", "projects", id).project
            if project?.id == id { project = updated }
            await loadProjects()
        } catch { message = error.userMessage }
    }

    public func delete(_ id: String) async {
        do {
            try await client.delete("v1", "studio", "projects", id)
            if project?.id == id { close() }
            await loadProjects()
        } catch { message = error.userMessage }
    }

    // MARK: composer

    public func attach(_ ref: StudioRef) {
        if !attachments.contains(ref) { attachments.append(ref) }
    }

    public func detach(_ ref: StudioRef) { attachments.removeAll { $0 == ref } }

    public func send() async -> Bool {
        guard canSend else { return false }
        let ok = await post(prompt: prompt, model: modelKey, aspect: aspect, count: count, refs: attachments)
        if ok { prompt = "" }
        return ok
    }

    /// A new generation with the failed one's parameters and the same references.
    public func retry(_ generation: StudioGeneration) async -> Bool {
        await post(prompt: generation.prompt, model: generation.model, aspect: generation.aspect,
                   count: generation.count, refs: generation.refs)
    }

    private func post(prompt: String, model: String, aspect: String, count: Int, refs: [StudioRef]) async -> Bool {
        guard let pid = project?.id, !isSending else { return false }
        struct Ref: Encodable { let kind: String; let id: String }
        struct Body: Encodable { let prompt: String; let model: String; let aspect: String; let count: Int; let refs: [Ref] }
        let body: Data
        do {
            body = try JSONEncoder().encode(Body(prompt: prompt, model: model, aspect: aspect, count: count,
                                                 refs: refs.map { Ref(kind: $0.kind.rawValue, id: $0.id) }))
        } catch { message = "Couldn't encode the request."; return false }
        isSending = true
        defer { isSending = false }
        message = nil
        let key = makeKey()
        for attempt in 1...Self.sendAttempts {
            switch await client.spendPost(["v1", "studio", "projects", pid, "generations"], body: body,
                                          idempotencyKey: key, timeout: 60) {
            case .http(status: 202, body: let data):
                guard let gen = try? MotionJSON.decoder.decode(StudioGenerationResponse.self, from: data).generation
                else { message = "The server's answer couldn't be read."; return false }
                project?.generations.append(gen)
                startPolling()
                return true
            case .http(status: let status, body: let data):
                message = APIClient.error(status: status, body: data).userMessage
                return false
            case .transport(let reason):
                if attempt == Self.sendAttempts { message = "Couldn't reach the server: \(reason)"; return false }
                try? await sleep(.seconds(2))
            }
        }
        return false
    }

    // MARK: polling

    private func startPolling() {
        guard autoPoll, hasRunning, pollTask == nil else { return }
        pollTask = Task { [weak self] in
            await self?.pollUntilIdle()
            self?.pollTask = nil
        }
    }

    /// Re-reads the open project every `pollInterval` while any slot is
    /// unfinished. Internal for tests, which call it directly.
    func pollUntilIdle() async {
        while hasRunning, let id = project?.id, !Task.isCancelled {
            try? await sleep(Self.pollInterval)
            guard let fresh = try? await client.get(StudioProjectResponse.self, "v1", "studio", "projects", id).project,
                  project?.id == id else { continue }
            project = fresh
        }
        await loadProjects()
    }

    // MARK: images & promote

    public func image(projectID: String, imageID: String) async -> Data? {
        let key = "studio/\(projectID)/\(imageID)"
        if let cached = images[key] { return cached }
        guard let data = try? await client.data("v1", "studio", "projects", projectID, "images", imageID) else { return nil }
        images[key] = data
        return data
    }

    /// Thumbnail for an attached (or historical) reference. `nil` draws a placeholder.
    public func thumbnail(for ref: StudioRef) async -> Data? {
        let key = "\(ref.kind.rawValue)/\(ref.id)"
        if let cached = images[key] { return cached }
        let parts = ref.id.split(separator: "/", maxSplits: 1).map(String.init)
        let data: Data?
        switch ref.kind {
        case .material where parts.count == 2:
            data = try? await client.data("v1", "materials", parts[0], parts[1], "thumb")
        case .tryon:
            data = try? await client.data("v1", "tryon-library", ref.id, "image")
        case .runTryon where parts.count == 2:
            data = try? await client.data("v1", "runs", parts[0], "tryon", parts[1])
        case .studio where parts.count == 2:
            data = try? await client.data("v1", "studio", "projects", parts[0], "images", parts[1])
        default:
            data = nil
        }
        if let data { images[key] = data }
        return data
    }

    public func promote(imageID: String, to target: StudioPromoteTarget) async -> String? {
        guard let pid = project?.id else { return nil }
        struct Body: Encodable, Sendable { let to: String }
        do {
            switch target {
            case .material:
                let m = try await client.post(StudioPromoteMaterial.self, body: Body(to: "material"),
                                              "v1", "studio", "projects", pid, "images", imageID, "promote")
                return "Added to Materials as \(m.material.name)."
            case .tryon:
                _ = try await client.post(StudioPromoteEntry.self, body: Body(to: "tryon"),
                                          "v1", "studio", "projects", pid, "images", imageID, "promote")
                return "Saved to the try-on library."
            }
        } catch { message = error.userMessage; return nil }
    }

    /// A temp file named `<imageID>.jpg|png` — Photos decides the type from the extension,
    /// and the image URL itself has none.
    public func download(imageID: String) async throws -> URL {
        guard let pid = project?.id else { throw APIError.transport("no project open") }
        let data = try await client.data("v1", "studio", "projects", pid, "images", imageID)
        let ext = data.starts(with: [0xFF, 0xD8, 0xFF]) ? "jpg" : "png"
        let dir = FileManager.default.temporaryDirectory.appending(component: UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let file = dir.appending(component: "\(imageID).\(ext)")
        try data.write(to: file)
        return file
    }
}
```

`APIClient.error(status:body:)` is `static func` with internal access (APIClient.swift:246) —
usable inside the module. `APIError.userMessage` exists (used by `TryonLibraryStore`).

- [ ] **Step 5: Run tests**

Run: `cd ios/MotionKit && swift test --filter StudioStoreTests` → PASS; then `make ios-test` → PASS.

- [ ] **Step 6: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionKit
git commit -m "MotionKit: Studio models and StudioStore (idempotent send, polling, pricing)"
```

---

### Task 7: Sidebar shell (`SpaceShell`) and app wiring

**Files:**
- Create: `ios/MotionApp/Shell/SpaceShell.swift`, `ios/MotionApp/Shell/SidebarView.swift`
- Modify: `ios/MotionApp/MotionApp.swift` (`AppModel`: `studio`, `selectedSpace`, `openStudio(project:)`), `ios/MotionApp/RootView.swift` (wrap the `TabView`; ☰ on every tab root)

**Interfaces:**
- Consumes: `StudioStore` (Task 6).
- Produces: `enum AppSpace: String { case motion, studio }`; `AppModel.studio: StudioStore?`, `AppModel.selectedSpace: AppSpace` (persisted), `AppModel.isSidebarOpen: Bool`, `AppModel.openStudio(projectID: String?) async`; `View.sidebarButton()` modifier; `StudioSpaceView` placeholder replaced in Task 8.

- [ ] **Step 1: `AppModel` additions** (MotionApp.swift): add `enum AppSpace: String { case motion, studio }` next to `AppTab`; in `AppModel` add

```swift
    private(set) var studio: StudioStore?
    var selectedSpace: AppSpace = AppSpace(rawValue: UserDefaults.standard.string(forKey: "selectedSpace") ?? "") ?? .motion {
        didSet { UserDefaults.standard.set(selectedSpace.rawValue, forKey: "selectedSpace") }
    }
    var isSidebarOpen = false

    /// Switches to Studio and opens (or creates, when `projectID` is nil) a project.
    func openStudio(projectID: String?) async {
        guard let studio else { return }
        if let projectID { await studio.open(projectID) } else { _ = await studio.createProject() }
        selectedSpace = .studio
        isSidebarOpen = false
    }
```

  In `reconnect()`, set `studio = nil` in the no-credentials branch and `studio = StudioStore(client: client)`
  after `balance = ...`.

- [ ] **Step 2: `SidebarView.swift`**

```swift
import MotionKit
import SwiftUI

/// The slide-out panel: the two spaces, Studio's projects, Settings. Modelled
/// on the Claude app's space switcher (spec 2026-09-26 "Shell and sidebar").
struct SidebarView: View {
    @Environment(AppModel.self) private var model
    let studio: StudioStore
    @State private var renaming: StudioProjectSummary?
    @State private var newTitle = ""
    @State private var showSettings = false

    var body: some View {
        List {
            Section {
                spaceRow(.motion, title: "Motion", icon: "waveform.path.ecg")
                spaceRow(.studio, title: "Image Studio", icon: "sparkles")
            }
            Section("Projects") {
                Button { Task { await model.openStudio(projectID: nil) } } label: {
                    Label("New project", systemImage: "plus")
                }
                ForEach(studio.projects) { p in
                    Button { Task { await model.openStudio(projectID: p.id) } } label: {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(StudioFormat.title(p.title, createdAt: p.createdAt)).lineLimit(1)
                            Text("\(p.imageCount) images · \(StudioFormat.usd(p.spentUsd))")
                                .font(.caption).foregroundStyle(Theme.secondary)
                        }
                    }
                    .listRowBackground(studio.project?.id == p.id && model.selectedSpace == .studio
                                       ? Theme.surfaceRaised : Theme.surface)
                    .contextMenu {
                        Button("Rename", systemImage: "pencil") { newTitle = p.title; renaming = p }
                        Button("Delete", systemImage: "trash", role: .destructive) {
                            Task { await studio.delete(p.id) }
                        }
                    }
                }
            }
            Section {
                Button { showSettings = true } label: { Label("Settings", systemImage: "gearshape") }
            }
        }
        .listStyle(.insetGrouped)
        .scrollContentBackground(.hidden)
        .background(Theme.bg)
        .task { await studio.loadProjects() }
        .alert("Rename project", isPresented: Binding(get: { renaming != nil }, set: { if !$0 { renaming = nil } })) {
            TextField("Title", text: $newTitle)
            Button("Save") { if let p = renaming { Task { await studio.rename(p.id, to: newTitle) } } }
            Button("Cancel", role: .cancel) {}
        }
        .sheet(isPresented: $showSettings) { NavigationStack { SettingsView(firstRun: false) } }
    }

    private func spaceRow(_ space: AppSpace, title: String, icon: String) -> some View {
        Button {
            model.selectedSpace = space
            model.isSidebarOpen = false
        } label: {
            Label(title, systemImage: icon).fontWeight(model.selectedSpace == space ? .semibold : .regular)
        }
        .accessibilityIdentifier("sidebar.\(space.rawValue)")
    }
}

enum StudioFormat {
    static func title(_ title: String, createdAt: Double) -> String {
        title.isEmpty ? Date(timeIntervalSince1970: createdAt).formatted(date: .abbreviated, time: .shortened) : title
    }

    static func usd(_ value: Double) -> String {
        value.formatted(.currency(code: "USD").precision(.fractionLength(value < 1 ? 3 : 2)))
    }
}
```

  Check `SettingsView(firstRun:)`'s signature in `ios/MotionApp/Settings/SettingsView.swift` and
  match it.

- [ ] **Step 3: `SpaceShell.swift`**

```swift
import MotionKit
import SwiftUI

/// Hosts the current space and slides the sidebar in from the left — edge
/// swipe or the ☰ button. Not NavigationSplitView: on iPhone that collapses
/// into a push stack, not a panel.
struct SpaceShell<Motion: View>: View {
    @Environment(AppModel.self) private var model
    let studio: StudioStore
    @ViewBuilder let motion: () -> Motion
    @State private var drag: CGFloat = 0
    private let width: CGFloat = 300

    var body: some View {
        @Bindable var model = model
        let offset = max(0, min(width, (model.isSidebarOpen ? width : 0) + drag))
        ZStack(alignment: .leading) {
            SidebarView(studio: studio)
                .frame(width: width)
                .offset(x: offset - width)
            Group {
                switch model.selectedSpace {
                case .motion: motion()
                case .studio: StudioSpaceView(studio: studio)
                }
            }
            .overlay {
                Color.black.opacity(0.35 * offset / width)
                    .ignoresSafeArea()
                    .allowsHitTesting(model.isSidebarOpen)
                    .onTapGesture { withAnimation(.snappy) { model.isSidebarOpen = false } }
            }
            .offset(x: offset)
        }
        .gesture(
            DragGesture(minimumDistance: 12)
                .onChanged { value in
                    let fromEdge = value.startLocation.x < 24
                    if model.isSidebarOpen || fromEdge { drag = value.translation.width }
                }
                .onEnded { value in
                    let projected = (model.isSidebarOpen ? width : 0) + value.predictedEndTranslation.width
                    withAnimation(.snappy) {
                        model.isSidebarOpen = projected > width / 2
                        drag = 0
                    }
                }
        )
        .onChange(of: model.isSidebarOpen) { _, open in if open { Task { await studio.loadProjects() } } }
    }
}

extension View {
    /// The ☰ that opens the sidebar, in the leading slot of a navigation bar.
    func sidebarButton() -> some View { modifier(SidebarButton()) }
}

private struct SidebarButton: ViewModifier {
    @Environment(AppModel.self) private var model
    func body(content: Content) -> some View {
        content.toolbar {
            ToolbarItem(placement: .topBarLeading) {
                Button { withAnimation(.snappy) { model.isSidebarOpen = true } } label: {
                    Image(systemName: "line.3.horizontal")
                }
                .accessibilityLabel("Open sidebar")
                .accessibilityIdentifier("sidebar.open")
            }
        }
    }
}

/// Replaced by the real Studio screen in Task 8.
struct StudioSpaceView: View {
    let studio: StudioStore
    var body: some View { NavigationStack { Text("Image Studio").sidebarButton() } }
}
```

- [ ] **Step 4: Wire `RootView`.** Add `let studio = model.studio` to the `if let` chain
  (`let studio = model.studio`), then replace `TabView(selection: $model.selectedTab) { … }` with
  `SpaceShell(studio: studio) { TabView(selection: $model.selectedTab) { … } }` keeping every existing
  modifier (`.tint`, `.textCase`, `.safeAreaInset` banners, `.onChange`, `.sheet`, `.overlay`) on the
  **`SpaceShell`**, so banners and the migrate sheet cover both spaces. Inside each `Tab`, append
  `.sidebarButton()` to the root view (e.g. `RunsView(...).sidebarButton()`). If a tab root already
  places a `.topBarLeading` item, keep both — SwiftUI stacks them; check visually in Step 5.

- [ ] **Step 5: Build and look**

Run: `make ios-build` → BUILD SUCCEEDED. Launch in the simulator (`make ios-ui-test` boots one, or
open Xcode) and check: ☰ on all 5 tabs, edge swipe opens the panel, tapping the dimmed area closes
it, "Image Studio" switches spaces, the space survives an app relaunch, Kill/Spend banners still
show in both spaces.

- [ ] **Step 6: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionApp
git commit -m "iOS: slide-out sidebar switching Motion and Image Studio spaces"
```

---

### Task 8: Project screen — grid, empty state, image viewer

**Files:**
- Create: `ios/MotionApp/Studio/StudioSpaceView.swift` (replaces the placeholder in `SpaceShell.swift` — delete it there), `ios/MotionApp/Studio/StudioGrid.swift`, `ios/MotionApp/Studio/StudioImageViewer.swift`
- Modify: `ios/MotionApp/Shell/SpaceShell.swift` (remove placeholder)

**Interfaces:**
- Consumes: `StudioStore` (Task 6), `StackLoader`, `MediaExporter` (Components/MediaActions.swift), `StudioFormat`.
- Produces: `StudioSpaceView(studio:)`; `StudioComposer` is referenced here and built in Task 9 — until then `StudioSpaceView` uses `Color.clear.frame(height: 0)` in its place (Task 9 swaps it).

- [ ] **Step 1: `StudioGrid.swift`**

```swift
import MotionKit
import SwiftUI

/// Two-column masonry, newest first: each tile is one slot of one generation.
struct StudioGrid: View {
    let studio: StudioStore
    let onOpen: (StudioGeneration, String) -> Void

    struct Tile: Identifiable {
        let generation: StudioGeneration
        let slot: Int
        var id: String { "\(generation.id)-\(slot)" }
        var state: StudioSlot { generation.slots[slot] }
    }

    private var tiles: [Tile] {
        (studio.project?.generations ?? []).reversed().flatMap { g in
            g.slots.indices.map { Tile(generation: g, slot: $0) }
        }
    }

    var body: some View {
        let all = tiles
        HStack(alignment: .top, spacing: 4) {
            column(all.enumerated().filter { $0.offset % 2 == 0 }.map(\.element))
            column(all.enumerated().filter { $0.offset % 2 == 1 }.map(\.element))
        }
    }

    private func column(_ items: [Tile]) -> some View {
        LazyVStack(spacing: 4) {
            ForEach(items) { tile in StudioTile(studio: studio, tile: tile, onOpen: onOpen) }
        }
    }
}

private struct StudioTile: View {
    let studio: StudioStore
    let tile: StudioGrid.Tile
    let onOpen: (StudioGeneration, String) -> Void
    @State private var image: UIImage?

    private var ratio: CGFloat {
        let parts = tile.generation.aspect.split(separator: ":").compactMap { Double($0) }
        return parts.count == 2 ? CGFloat(parts[0] / parts[1]) : 1
    }

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 12).fill(Theme.surface)
            switch tile.state.status {
            case .queued, .running:
                StackLoader(size: 40)
            case .error:
                VStack(spacing: 8) {
                    Image(systemName: "exclamationmark.triangle").foregroundStyle(Theme.danger)
                    Text(tile.state.error ?? "Failed").font(.caption2).multilineTextAlignment(.center)
                        .foregroundStyle(Theme.secondary).lineLimit(4)
                    Button("Retry") { Task { _ = await studio.retry(tile.generation) } }
                        .buttonStyle(.bordered).controlSize(.small)
                }.padding(8)
            case .done:
                if let image {
                    Image(uiImage: image).resizable().scaledToFill()
                } else {
                    ProgressView()
                }
            }
        }
        .aspectRatio(ratio, contentMode: .fit)
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .contentShape(Rectangle())
        .onTapGesture { if let id = tile.state.image { onOpen(tile.generation, id) } }
        .task(id: tile.state.image) {
            guard let pid = studio.project?.id, let id = tile.state.image,
                  let data = await studio.image(projectID: pid, imageID: id) else { return }
            image = UIImage(data: data)
        }
        .accessibilityIdentifier("studio.tile")
    }
}
```

- [ ] **Step 2: `StudioImageViewer.swift`**

```swift
import MotionKit
import SwiftUI

/// Full-screen image with Edit this / Use as material / Save to try-on library
/// / Save & Share, plus the prompt and model that made it.
struct StudioImageViewer: View {
    let studio: StudioStore
    let generation: StudioGeneration
    let imageID: String
    @Environment(\.dismiss) private var dismiss
    @State private var image: UIImage?
    @State private var exporter = MediaExporter()
    @State private var note: String?

    var body: some View {
        NavigationStack {
            VStack(spacing: 16) {
                Group {
                    if let image { Image(uiImage: image).resizable().scaledToFit() } else { ProgressView() }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                VStack(alignment: .leading, spacing: 4) {
                    Text(generation.prompt).font(.callout).lineLimit(4)
                    Text(studio.catalog?.models.first { $0.key == generation.model }?.label ?? generation.model)
                        .font(.caption).foregroundStyle(Theme.secondary)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                if let note { Text(note).font(.footnote).foregroundStyle(Theme.accent) }
                HStack(spacing: 12) {
                    Button("Edit this", systemImage: "wand.and.stars") {
                        if let pid = studio.project?.id { studio.attach(StudioRef(kind: .studio, id: "\(pid)/\(imageID)")) }
                        dismiss()
                    }
                    .buttonStyle(.borderedProminent)
                    Menu {
                        Button("Use as material", systemImage: "photo.badge.plus") {
                            Task { note = await studio.promote(imageID: imageID, to: .material) }
                        }
                        Button("Save to try-on library", systemImage: "tshirt") {
                            Task { note = await studio.promote(imageID: imageID, to: .tryon) }
                        }
                        Button("Save to Photos", systemImage: "square.and.arrow.down") {
                            Task { await exporter.save { try await studio.download(imageID: imageID) } }
                        }
                        Button("Share", systemImage: "square.and.arrow.up") {
                            Task { await exporter.share { try await studio.download(imageID: imageID) } }
                        }
                    } label: { Label("More", systemImage: "ellipsis.circle") }
                    .buttonStyle(.bordered)
                }
            }
            .padding()
            .background(Theme.bg)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } } }
            .task {
                guard let pid = studio.project?.id,
                      let data = await studio.image(projectID: pid, imageID: imageID) else { return }
                image = UIImage(data: data)
            }
        }
    }
}
```

  Before writing, read `MediaExporter` (Components/MediaActions.swift) for the exact names of its
  save/share entry points, toast and share-sheet presentation, and mirror how `MaterialPreview.swift`
  attaches them (`.sheet(item: $exporter.sharing)` and the toast overlay). Adjust the two calls above
  to those names.

- [ ] **Step 3: `StudioSpaceView.swift`** (and delete the placeholder struct from `SpaceShell.swift`)

```swift
import MotionKit
import SwiftUI

struct StudioSpaceView: View {
    @Environment(AppModel.self) private var model
    let studio: StudioStore
    @State private var viewing: Viewing?

    struct Viewing: Identifiable {
        let generation: StudioGeneration
        let imageID: String
        var id: String { imageID }
    }

    var body: some View {
        NavigationStack {
            Group {
                if let project = studio.project {
                    ScrollView {
                        if project.generations.isEmpty {
                            ContentUnavailableView("Nothing yet", systemImage: "sparkles",
                                                   description: Text("Describe an image, or tap + to add references."))
                                .padding(.top, 80)
                        }
                        StudioGrid(studio: studio) { gen, id in viewing = Viewing(generation: gen, imageID: id) }
                            .padding(.horizontal, 4)
                    }
                    .defaultScrollAnchor(.top)
                    .safeAreaInset(edge: .bottom) { StudioComposerSlot(studio: studio) }
                    .navigationTitle(StudioFormat.title(project.title, createdAt: project.createdAt))
                } else {
                    ContentUnavailableView {
                        Label("Image Studio", systemImage: "sparkles")
                    } description: {
                        Text("Generate and edit images with Nano Banana and Qwen.")
                    } actions: {
                        Button("New project") { Task { await model.openStudio(projectID: nil) } }
                            .buttonStyle(.borderedProminent)
                    }
                }
            }
            .navigationBarTitleDisplayMode(.inline)
            .sidebarButton()
            .background(Theme.bg)
            .alert("Studio", isPresented: Binding(get: { studio.message != nil }, set: { if !$0 { studio.message = nil } })) {
                Button("OK", role: .cancel) {}
            } message: { Text(studio.message ?? "") }
            .sheet(item: $viewing) { v in StudioImageViewer(studio: studio, generation: v.generation, imageID: v.imageID) }
            .task { await studio.loadCatalog() }
        }
    }
}

/// Task 9 replaces this with the real composer.
struct StudioComposerSlot: View {
    let studio: StudioStore
    var body: some View { Color.clear.frame(height: 0) }
}
```

- [ ] **Step 4: Build** — `make ios-build` → BUILD SUCCEEDED.

- [ ] **Step 5: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionApp
git commit -m "iOS Studio: project grid, retry tiles and image viewer"
```

---

### Task 9: Composer — references, settings sheet, sources, send

**Files:**
- Create: `ios/MotionApp/Studio/StudioComposer.swift`, `ios/MotionApp/Studio/StudioSettingsSheet.swift`, `ios/MotionApp/Studio/StudioSourcePicker.swift`
- Modify: `ios/MotionApp/Studio/StudioSpaceView.swift` (delete `StudioComposerSlot`, use `StudioComposer`)

**Interfaces:**
- Consumes: `StudioStore` (Task 6); `MaterialsStore.startUpload(fileURL:fileName:)` via `MediaImport.upload(_:to:)`; `MaterialsStore.materials`; `TryonLibraryStore.entries`; `AppModel.materials/tryonLibrary/runFlow`.
- Produces: `StudioComposer(studio:)`.

- [ ] **Step 1: `StudioComposer.swift`** — refs row with long-press preview + ⓧ (spec: no ✕ by default)

```swift
import MotionKit
import SwiftUI

/// The pinned prompt bar: reference thumbnails, the prompt, ＋, the
/// "model · aspect · xN" pill and Send (spec "Project screen").
struct StudioComposer: View {
    let studio: StudioStore
    @State private var showSettings = false
    @State private var showSources = false
    @State private var peeking: StudioRef?
    @FocusState private var focused: Bool

    var body: some View {
        @Bindable var studio = studio
        VStack(alignment: .leading, spacing: 12) {
            if !studio.attachments.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(studio.attachments) { ref in
                            RefThumb(studio: studio, ref: ref, peeking: $peeking)
                        }
                    }
                }
            }
            TextField("What do you want to create?", text: $studio.prompt, axis: .vertical)
                .lineLimit(1...5)
                .focused($focused)
                .font(.title3)
                .accessibilityIdentifier("studio.prompt")
            HStack(spacing: 12) {
                Button { showSources = true } label: { Image(systemName: "plus").font(.title2) }
                    .accessibilityLabel("Add reference")
                    .accessibilityIdentifier("studio.add")
                Spacer()
                Button { showSettings = true } label: {
                    HStack(spacing: 6) {
                        Text(studio.selectedModel?.label ?? "Model").lineLimit(1)
                        Text(studio.aspect)
                        Text("x\(studio.count)")
                    }
                    .font(.footnote)
                    .padding(.horizontal, 14).padding(.vertical, 10)
                    .background(Capsule().fill(Theme.surfaceRaised))
                }
                .accessibilityIdentifier("studio.settings")
                Button {
                    focused = false
                    Task { _ = await studio.send() }
                } label: {
                    if studio.isSending { ProgressView() } else {
                        Label(StudioFormat.usd(studio.estimateUSD), systemImage: "arrow.right")
                            .labelStyle(.titleAndIcon).font(.footnote.weight(.semibold))
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(!studio.canSend)
                .accessibilityIdentifier("studio.send")
            }
        }
        .padding(16)
        .background(RoundedRectangle(cornerRadius: 28).fill(Theme.surface).ignoresSafeArea(edges: .bottom))
        .overlay(alignment: .topLeading) {
            if let ref = peeking {
                RefPeek(studio: studio, ref: ref).offset(y: -236).transition(.scale(scale: 0.8).combined(with: .opacity))
            }
        }
        .background {
            if peeking != nil {
                Color.black.opacity(0.5).ignoresSafeArea().frame(height: 4000).offset(y: -2000)
                    .onTapGesture { withAnimation(.snappy) { peeking = nil } }
            }
        }
        .sheet(isPresented: $showSettings) { StudioSettingsSheet(studio: studio).presentationDetents([.medium]) }
        .sheet(isPresented: $showSources) { StudioSourcePicker(studio: studio) }
    }
}

/// A reference thumbnail. The ⓧ appears only while this ref is being peeked
/// (long-press), as in Flow; a plain tap does nothing.
private struct RefThumb: View {
    let studio: StudioStore
    let ref: StudioRef
    @Binding var peeking: StudioRef?
    @State private var image: UIImage?

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 12).fill(Theme.surfaceRaised)
            if let image { Image(uiImage: image).resizable().scaledToFill() }
            if peeking == ref {
                Button { withAnimation(.snappy) { studio.detach(ref); peeking = nil } } label: {
                    Image(systemName: "xmark.circle").font(.title2).foregroundStyle(.white)
                        .shadow(radius: 2)
                }
                .accessibilityLabel("Remove reference")
                .accessibilityIdentifier("studio.ref.remove")
            }
        }
        .frame(width: 56, height: 56)
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .onLongPressGesture(minimumDuration: 0.35) { withAnimation(.snappy) { peeking = ref } }
        .task { if let data = await studio.thumbnail(for: ref) { image = UIImage(data: data) } }
        .accessibilityIdentifier("studio.ref")
    }
}

private struct RefPeek: View {
    let studio: StudioStore
    let ref: StudioRef
    @State private var image: UIImage?

    var body: some View {
        Group {
            if let image { Image(uiImage: image).resizable().scaledToFit() } else { ProgressView() }
        }
        .frame(width: 160, height: 220)
        .background(RoundedRectangle(cornerRadius: 16).fill(Theme.surfaceRaised))
        .clipShape(RoundedRectangle(cornerRadius: 16))
        .task { if let data = await studio.thumbnail(for: ref) { image = UIImage(data: data) } }
    }
}
```

- [ ] **Step 2: `StudioSettingsSheet.swift`**

```swift
import MotionKit
import SwiftUI

/// Aspect, count, and model with its per-image price (like Flow's sheet).
struct StudioSettingsSheet: View {
    let studio: StudioStore

    var body: some View {
        @Bindable var studio = studio
        NavigationStack {
            Form {
                Section {
                    Text("This will use about \(StudioFormat.usd(studio.estimateUSD))")
                        .font(.footnote).foregroundStyle(Theme.secondary)
                }
                Section("Aspect") {
                    Picker("Aspect", selection: $studio.aspect) {
                        ForEach(studio.catalog?.aspects ?? [], id: \.self) { Text($0).tag($0) }
                    }.pickerStyle(.segmented)
                }
                Section("Images") {
                    Picker("Count", selection: $studio.count) {
                        ForEach(1...(studio.catalog?.maxCount ?? 4), id: \.self) { Text("x\($0)").tag($0) }
                    }.pickerStyle(.segmented)
                }
                Section("Model") {
                    ForEach(studio.catalog?.models ?? []) { m in
                        let reason = studio.disabledReason(for: m)
                        Button { studio.modelKey = m.key } label: {
                            HStack {
                                VStack(alignment: .leading) {
                                    Text(m.label)
                                    if let reason { Text(reason).font(.caption).foregroundStyle(Theme.secondary) }
                                }
                                Spacer()
                                Text("\(StudioFormat.usd(m.priceUsd))/image").font(.caption).foregroundStyle(Theme.secondary)
                                if m.key == studio.modelKey { Image(systemName: "checkmark").foregroundStyle(Theme.accent) }
                            }
                        }
                        .disabled(reason != nil)
                        .accessibilityIdentifier("studio.model.\(m.key)")
                    }
                }
            }
            .navigationTitle("Settings").navigationBarTitleDisplayMode(.inline)
        }
    }
}
```

- [ ] **Step 3: `StudioSourcePicker.swift`** — Photos (upload → material), Materials, Try-on library, current run's previews, Studio images

```swift
import MotionKit
import PhotosUI
import SwiftUI

/// Where a reference comes from. Photos go through the normal upload and
/// become a material, so the server only ever resolves names it owns.
struct StudioSourcePicker: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    let studio: StudioStore
    @State private var photo: PhotosPickerItem?
    @State private var uploading = false
    @State private var failure: String?

    var body: some View {
        NavigationStack {
            List {
                Section {
                    PhotosPicker(selection: $photo, matching: .images) {
                        Label(uploading ? "Uploading…" : "Photo library", systemImage: "photo.on.rectangle")
                    }
                    .disabled(uploading)
                    if let failure { Text(failure).font(.footnote).foregroundStyle(Theme.danger) }
                }
                if let materials = model.materials {
                    Section("Materials") {
                        grid(materials.materials.filter { $0.kind == .image }.map { StudioRef(kind: .material, id: $0.id) })
                    }
                }
                if let library = model.tryonLibrary, !library.entries.isEmpty {
                    Section("Try-on library") { grid(library.entries.map { StudioRef(kind: .tryon, id: $0.id) }) }
                }
                if let previews = runPreviews, !previews.isEmpty {
                    Section("Current try-on previews") { grid(previews) }
                }
                if let project = studio.project {
                    let refs = project.generations.flatMap { g in g.slots.compactMap(\.image) }
                        .reversed().map { StudioRef(kind: .studio, id: "\(project.id)/\($0)") }
                    if !refs.isEmpty { Section("This project") { grid(Array(refs)) } }
                }
            }
            .navigationTitle("Add reference").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } } }
            .task {
                await model.materials?.refresh()
                await model.tryonLibrary?.load()
            }
            .onChange(of: photo) { _, item in
                guard let item, let materials = model.materials else { return }
                uploading = true
                Task {
                    defer { uploading = false; photo = nil }
                    do {
                        if let material = try await MediaImport.upload(item, to: materials) {
                            studio.attach(StudioRef(kind: .material, id: material.id))
                            dismiss()
                        } else {
                            failure = materials.errorMessage ?? "Upload failed."
                        }
                    } catch { failure = error.localizedDescription }
                }
            }
        }
    }

    /// Try-on previews of the live Phase A run, if any.
    private var runPreviews: [StudioRef]? {
        guard let flow = model.runFlow, let runID = flow.runID else { return nil }
        return flow.cards.filter(\.hasImage).map { StudioRef(kind: .runTryon, id: "\(runID)/\($0.index)") }
    }

    private func grid(_ refs: [StudioRef]) -> some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 88), spacing: 6)], spacing: 6) {
            ForEach(refs) { ref in
                SourceThumb(studio: studio, ref: ref, picked: studio.attachments.contains(ref)) {
                    if studio.attachments.contains(ref) { studio.detach(ref) } else { studio.attach(ref) }
                }
            }
        }
        .listRowInsets(EdgeInsets(top: 6, leading: 6, bottom: 6, trailing: 6))
    }
}

private struct SourceThumb: View {
    let studio: StudioStore
    let ref: StudioRef
    let picked: Bool
    let toggle: () -> Void
    @State private var image: UIImage?

    var body: some View {
        Button(action: toggle) {
            ZStack(alignment: .topTrailing) {
                Rectangle().fill(Theme.surfaceRaised)
                    .overlay { if let image { Image(uiImage: image).resizable().scaledToFill() } }
                    .clipped()
                if picked {
                    Image(systemName: "checkmark.circle.fill").foregroundStyle(Theme.accent).padding(4)
                }
            }
            .aspectRatio(3 / 4, contentMode: .fit)
            .clipShape(RoundedRectangle(cornerRadius: 8))
        }
        .buttonStyle(.plain)
        .task { if let data = await studio.thumbnail(for: ref) { image = UIImage(data: data) } }
    }
}
```

  `RunFlow.runID` (`pod?.runId`) and `RunFlow.cards: [TryonPreview]` (each with `index: String`,
  `hasImage`) are the same values `TryonPreviewCard` reads. Check `MediaImport.upload`'s return/throw contract and `MaterialsStore.errorMessage` in
  `Materials/MediaImport.swift` and match them.

- [ ] **Step 4: Swap the composer in** — in `StudioSpaceView.swift`, replace
  `StudioComposerSlot(studio: studio)` with `StudioComposer(studio: studio)` and delete
  `StudioComposerSlot`.

- [ ] **Step 5: Build and check by hand** — `make ios-build`; in the simulator: long-press a ref →
  backdrop dims, enlarged preview above the composer, ⓧ on the thumbnail; tap ⓧ removes; tap outside
  dismisses; plain tap does nothing. The settings sheet greys out Qwen when 4 refs are attached; the
  send button shows the price; empty prompt keeps Send disabled.

- [ ] **Step 6: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionApp ios/MotionKit
git commit -m "iOS Studio: composer with long-press reference peek, model sheet and sources"
```

---

### Task 10: "Edit in Studio" entry points

**Files:**
- Create: `ios/MotionApp/Studio/EditInStudioSheet.swift`
- Modify: `ios/MotionApp/RunFlow/TryonPreviewCard.swift`, `ios/MotionApp/Materials/SavedTryonsView.swift`

**Interfaces:**
- Consumes: `AppModel.openStudio(projectID:)`, `StudioStore.attach`, `StudioStore.projects`.
- Produces: `EditInStudioSheet(ref: StudioRef)`.

- [ ] **Step 1: `EditInStudioSheet.swift`**

```swift
import MotionKit
import SwiftUI

/// Pick a project (or make one), attach `ref` to its composer, switch to Studio.
struct EditInStudioSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    let ref: StudioRef

    var body: some View {
        NavigationStack {
            List {
                Button { go(nil) } label: { Label("New project", systemImage: "plus") }
                if let studio = model.studio {
                    ForEach(studio.projects) { p in
                        Button(StudioFormat.title(p.title, createdAt: p.createdAt)) { go(p.id) }
                    }
                }
            }
            .navigationTitle("Edit in Studio").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarLeading) { Button("Cancel") { dismiss() } } }
            .task { await model.studio?.loadProjects() }
        }
    }

    private func go(_ projectID: String?) {
        Task {
            await model.openStudio(projectID: projectID)
            model.studio?.attach(ref)
            dismiss()
        }
    }
}
```

- [ ] **Step 2: Add the buttons.** In `TryonPreviewCard.swift`, next to the existing save/regenerate
  actions, add `Button("Edit in Studio", systemImage: "wand.and.stars") { editing = StudioRef(kind: .runTryon, id: "\(runID)/\(index)") }`
  using `flow.runID` (skip the button when nil) and `preview.index`, with `@State private var editing: StudioRef?` and
  `.sheet(item: $editing) { EditInStudioSheet(ref: $0) }`. In `SavedTryonsView.swift`, add the same
  to each entry's context menu with `StudioRef(kind: .tryon, id: entry.id)`.
- [ ] **Step 3: Build and check** — `make ios-build`; from Saved try-ons → Edit in Studio → New
  project lands in Studio with the image attached.
- [ ] **Step 4: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionApp
git commit -m "iOS: Edit in Studio from try-on previews and saved try-ons"
```

---

### Task 11: Contract, UI smoke, live run, deploy

**Files:**
- Modify: `ios/MotionKit/Sources/motion-contract/main.swift`
- Create: `ios/MotionAppUITests/StudioSmokeTests.swift`
- Modify: `docs/superpowers/specs/2026-09-26-image-studio-design.md` (measurements), `scripts/control/studio_runner.py` (Qwen price)

- [ ] **Step 1: Contract.** In `motion-contract/main.swift`, following how it decodes the other GET
  routes, add `GET /v1/studio/models` → `StudioCatalog` and `GET /v1/studio/projects` →
  `StudioProjectsResponse` (make that struct `public` with a public `projects` if the tool lives
  outside the module's internal scope). Run later against the deployed server in Step 5.

- [ ] **Step 2: UI smoke (zero spend)** — `StudioSmokeTests.swift`:

```swift
import XCTest

/// Zero-spend, live server: opens the sidebar, switches to Image Studio,
/// creates a project, opens the settings sheet, and never taps Send.
/// Deletes the project it made at the end.
final class StudioSmokeTests: XCTestCase {
    @MainActor
    func testSidebarAndComposerWithoutSpending() throws {
        let app = XCUIApplication()
        app.launchArguments += ["-UITestRecordingSpendGate"]
        app.launch()
        let open = app.buttons["sidebar.open"].firstMatch
        XCTAssertTrue(open.waitForExistence(timeout: 20))
        open.tap()
        app.buttons["sidebar.studio"].tap()
        open.tap()
        app.buttons["New project"].firstMatch.tap()
        XCTAssertTrue(app.textFields["studio.prompt"].waitForExistence(timeout: 10)
                      || app.textViews["studio.prompt"].waitForExistence(timeout: 1))
        XCTAssertFalse(app.buttons["studio.send"].isEnabled, "Send stays disabled with an empty prompt")
        app.buttons["studio.settings"].tap()
        XCTAssertTrue(app.buttons["studio.model.nano-banana-2"].waitForExistence(timeout: 10))
        app.swipeDown()
        // Clean up: delete the project from the sidebar.
        app.buttons["sidebar.open"].firstMatch.tap()
        let row = app.buttons.matching(NSPredicate(format: "label CONTAINS '0 images'")).firstMatch
        XCTAssertTrue(row.waitForExistence(timeout: 10))
        row.press(forDuration: 1.0)
        app.buttons["Delete"].tap()
    }
}
```

  Run with the repo's UI-test runner (`scripts/ios-ui-test.sh` runs Phase 3; run this class with
  `-only-testing:MotionAppUITests/StudioSmokeTests` the same way). Expected: PASS. Needs the Studio
  routes deployed — run it after Step 5.

- [ ] **Step 3: Full free gates** — `make batch-test`, `make ios-test`, `make ios-build`,
  `motions-studio/setup/scrub-secrets.sh --check`. All must pass.

- [ ] **Step 4: Pre-merge VPS check (read-only)** —
  `doctl compute ssh motion-vps --ssh-command "cd ~/motion-clone && ls batch/*.state.json; grep -c '^GPU_INSTANCE_ID=.' .env; pgrep -af 'drain.py|batch_run.py' | grep -v pgrep"`.
  If a drain/Phase A/lease/migration is live, wait. Then open a PR from `image-studio` (push with
  the doanhthuc SSH remote; `gh` needs the personal account — see memory "Two GitHub accounts") and
  merge after the user says so. The merge auto-deploys `scripts/**`.

- [ ] **Step 5: Contract against the deployed API** — `make ios-contract` → the Studio GETs decode.

- [ ] **Step 6: Live run (costs money — ask the user first, stating "~$0.50 total")** — from the
  app, one project: x1 each of Nano Banana Pro, Nano Banana 2, Nano Banana 2 Lite (text-to-image,
  Vietnamese prompt), Qwen Image 3.0 Pro text-to-image, and Qwen edit with one reference. Record for
  each: wall time (from `created_at` to done), output pixel size (`ffprobe` on the VPS file), and
  whether Lite rejected anything. The next day, read the Google AI Studio and Bailian billing pages
  and record billed cost per image.

- [ ] **Step 7: Record measurements** — add a `## Measured (2026-09-2x)` section to the spec with
  Step 6's numbers; update `MODELS["qwen-image-3"].price_usd` and its comment to the billed USD
  figure; confirm or correct Gemini `max_refs` (14) from Google's image-generation docs per model and
  note the source. Commit:

```bash
motions-studio/setup/scrub-secrets.sh --check
git add docs/superpowers/specs/2026-09-26-image-studio-design.md scripts/control/studio_runner.py ios
git commit -m "Image Studio: measured latency, sizes and billed prices; contract and UI smoke"
```
