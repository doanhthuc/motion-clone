# Control-plane API, slice 2 (chunked upload + materials) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The phone can upload material of any size up to 2 GiB through Cloudflare's 100 MB request
cap (resumable 32 MiB chunks), then list, preview (thumbnail) and delete material on the VPS.

**Architecture:** Staging logic moves out of `bot.py` into the Telegram-free `scripts/control/materials.py`
(the bot keeps thin wrappers, so its behaviour and tests do not change). A new
`scripts/control/uploads.py` stores chunks on disk under `batch/uploads/<id>/` and assembles them
into `batch/tg-staging/app/` via the same staging function the bot uses. `scripts/httpapi/server.py`
gains POST/PUT/DELETE with streamed request bodies. The bot's daily prune tick also ages out
abandoned uploads and orphaned thumbnails.

**Tech Stack:** Python 3 stdlib only; `ffmpeg`/`ffprobe` (already on the VPS, `scripts/vps/README.md`
Install); `unittest`.

**Spec:** `docs/superpowers/specs/2026-09-21-vps-control-plane-api-design.md` — slice 2 = §6 row 2;
contract §5.1; status codes §5.6; concurrency §4.3. Slice 1's plan
(`docs/superpowers/plans/2026-09-21-control-plane-slice-1.md`) shows the existing modules.

## Controller decisions (the spec leaves these open)

- **Material id** is `"<owner>/<name>"`. Material lives at `batch/tg-staging/<owner>/<name>` — the
  directory the bot already uses (`STAGING_DIR_NAME`). Telegram's owner is its chat id; the phone's
  owner is the literal directory `app`. Listing is global (spec §5.1: materials are global); URLs
  carry the id as two path segments: `/v1/materials/<owner>/<name>`.
- **DELETE only for `app`-owned material** (`403 forbidden` otherwise): Telegram's own `/clear`
  and `/wipe` own the chat directories. `409 in_use` if the material's path appears in any
  `batch/*.yaml` whose run is busy (`tgbot.run.busy`). Slice 3 adds the app-draft check.
- **Uploads** live in `batch/uploads/<upload_id>/`: `meta.json` + `NNNNN.part` files. Chunk size
  32 MiB (`33554432`), max file 2 GiB (`2147483648`). Opening an upload requires free disk
  `>= 2 * size + 1 GiB` (chunks + assembled file coexist briefly; 1 GiB keeps the bot's own disk
  headroom). Assembly moves (not copies) the assembled file into staging.
- **Thumbnails** are cached in `batch/thumbs/<owner>/<name>.jpg`, regenerated when the source is
  newer, 320 px wide JPEG via ffmpeg.
- **Abandoned uploads** older than 24 h are removed by the bot's existing daily prune tick
  (`_tick_staging_prune`), together with thumbnails whose source is gone.

## Global Constraints

- No new Python dependency (spec §4.2).
- Every route under `/v1`; errors `{"error": {"code": "...", "message": "..."}}` for every HTTP
  method, including POST/PUT/DELETE (spec §5).
- Status codes: `401` bearer; `404` unknown id / path outside root; `409` world-state refusal
  (material in use, upload incomplete); `422` unprobeable material; `500` logged and opaque
  (spec §5.6). Also `400` bad request, `403` not app-owned, `411` missing Content-Length,
  `413` too large, `507` insufficient disk.
- Every file-naming path parameter goes through `control.paths.safe_child`; symlinks refused
  (spec §5.4).
- The API never exposes absolute paths.
- Request bodies are streamed to disk in ≤1 MiB reads, never held whole in memory (1 GB VPS).
- After any error response to a request that carries a body, the connection is closed
  (`close_connection = True`): unread body bytes would otherwise be parsed as the next request.
- `scripts/control/` imports nothing from Telegram; it may import `tgbot.run` and `tgbot.ingest`
  (both already Telegram-free).
- `scripts/tests/test_batch_bot.py` stays green unchanged after every task.
- New test files are `scripts/tests/test_batch_control_*.py`.
- English for code, comments, docs, commit messages; explain *why* with numbers where a reader
  would wonder. No `#region ALD` markers.
- `motions-studio/setup/scrub-secrets.sh --check` exits 0 before every commit.
- Commit messages end with a blank line then exactly
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- Branch `feat/control-plane-slice-2` (exists). Never push.

## File Structure

| File | Responsibility |
|---|---|
| `scripts/control/materials.py` | Naming (`safe_name`), `stage_file`, `prune_staged`; listing, resolving, deleting, thumbnails, `ingest` (HEIC→PNG + probe) |
| `scripts/control/uploads.py` | Chunked upload store: open, write chunk, status, assemble, prune |
| `scripts/httpapi/server.py` | Adds POST/PUT/DELETE, body reading, upload + material routes |
| `scripts/tgbot/bot.py` | Staging helpers become thin wrappers over `control.materials`; prune tick also prunes uploads + thumbs |
| `scripts/tests/test_batch_control_materials.py` | materials |
| `scripts/tests/test_batch_control_uploads.py` | uploads |
| `scripts/tests/test_batch_control_http.py` | new HTTP routes (append) |

---

### Task 1: Move staging and naming into `control/materials.py`

The upload path must stage files with exactly the bot's rules (safe names, Vietnamese folding,
never overwrite, HEIC name reservation). Move the code; the bot keeps its private names as wrappers
so every call site and test keeps working. `bot.ROOT` is patched by the bot's tests, so the bot
wrappers must keep computing directories from `bot.ROOT`; the moved functions take directories as
parameters.

**Files:**
- Create: `scripts/control/materials.py`
- Modify: `scripts/tgbot/bot.py` (the block from `_SAFE_NAME_RE = ...` through
  `_prune_old_staged_files`, currently ~lines 268–472)
- Test: `scripts/tests/test_batch_control_materials.py`

**Interfaces:**
- Produces:
  - `fold_diacritics(text: str) -> str`
  - `safe_name(name: str) -> str`
  - `stage_file(dest_dir: Path, src: Path, file_name: str | None, *, move: bool = False) -> Path`
    — creates `dest_dir`; never overwrites; reserves the `.png` twin of a `.heic/.heif` name;
    copies (`shutil.copyfile`) or, with `move=True`, `os.replace`s. Raises `OSError` unchanged.
    Name reservation + write happen under a module-level `threading.Lock` (`_STAGE_LOCK`) so two
    threads (bot poll loop, HTTP thread) can never pick the same name.
  - `prune_staged(staging_root: Path, max_age_days: int, now: float) -> list[Path]`

- [ ] **Step 1: Write the failing test**

```python
# scripts/tests/test_batch_control_materials.py
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from control import materials


class TestNaming(unittest.TestCase):
    def test_same_rules_as_the_bot(self):
        self.assertEqual(materials.safe_name("áo dài.jpg"), "ao_dai.jpg")
        self.assertEqual(materials.safe_name("写真.heic"), "file.heic")
        self.assertEqual(materials.safe_name("my driver:v1.mp4"), "my_driver_v1.mp4")
        self.assertEqual(materials.fold_diacritics("／"), "／")

    def test_bot_aliases_are_the_moved_functions(self):
        import tgbot.bot as bot
        self.assertIs(bot._safe_name, materials.safe_name)
        self.assertIs(bot._fold_diacritics, materials.fold_diacritics)


class TestStageFile(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dest = self.tmp / "staging" / "app"

    def src(self, name="x.png", data=b"data"):
        p = self.tmp / name
        p.write_bytes(data)
        return p

    def test_copy_keeps_source(self):
        s = self.src()
        out = materials.stage_file(self.dest, s, "Blue Dress.png")
        self.assertEqual(out, self.dest / "Blue_Dress.png")
        self.assertTrue(s.exists())

    def test_move_removes_source(self):
        s = self.src()
        out = materials.stage_file(self.dest, s, "a.png", move=True)
        self.assertFalse(s.exists())
        self.assertEqual(out.read_bytes(), b"data")

    def test_never_overwrites_and_reserves_heic_png_twin(self):
        materials.stage_file(self.dest, self.src(), "photo.png")
        out = materials.stage_file(self.dest, self.src("y.heic"), "photo.heic")
        self.assertEqual(out.name, "photo-1.heic")

    def test_concurrent_staging_never_collides(self):
        results = []
        def worker(i):
            results.append(materials.stage_file(self.dest, self.src(f"s{i}.png"), "same.png"))
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(len({p.name for p in results}), 8)


class TestPrune(unittest.TestCase):
    def test_removes_only_old_files(self):
        root = Path(tempfile.mkdtemp())
        (root / "app").mkdir()
        old, new = root / "app" / "old.mp4", root / "app" / "new.mp4"
        old.write_bytes(b"o"); new.write_bytes(b"n")
        now = time.time()
        os.utime(old, (now - 8 * 86400, now - 8 * 86400))
        self.assertEqual(materials.prune_staged(root, 7, now), [old])
        self.assertTrue(new.exists())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_materials.py' -v`
Expected: FAIL — `ImportError: cannot import name 'materials' from 'control'`

- [ ] **Step 3: Implement**

Create `scripts/control/materials.py`. **Move verbatim** from `scripts/tgbot/bot.py` — with their
full existing comments and docstrings — `_SAFE_NAME_RE`, `_TRANSLIT`, `_fold_diacritics` (renamed
`fold_diacritics`) and `_safe_name` (renamed `safe_name`, calling `fold_diacritics`). Then add:

```python
"""Material on the VPS: naming, staging, and (Task 2) listing and previews.

Telegram-free: shared by the bot (tgbot/bot.py wraps these) and the HTTP API.
Directories are parameters, never derived here, so the bot can keep deriving
them from its own ROOT (which its tests patch).
"""
from __future__ import annotations

import os
import re
import shutil
import threading
import unicodedata
from pathlib import Path

# <_SAFE_NAME_RE, _TRANSLIT, fold_diacritics, safe_name moved here verbatim>

# Name reservation and the write that claims the name must be one step: the
# bot's poll loop and the HTTP thread both stage files now, and two threads
# that each see "photo.png is free" would both write it.
_STAGE_LOCK = threading.Lock()


def stage_file(dest_dir: Path, src: Path, file_name: str | None, *,
               move: bool = False) -> Path:
    """<the existing _stage_file docstring's paragraph about never overwriting,
    plus: `move=True` renames instead of copying — used for an assembled upload
    that already sits on the same filesystem, so a 2 GiB file is not written
    twice.>"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    stem = safe_name(file_name or src.name)
    # <the existing comment block about HEIC claiming two names, verbatim>
    derived_suffix = ".png" if Path(stem).suffix.lower() in (".heic", ".heif") else None

    def taken(candidate: Path) -> bool:
        if candidate.exists():
            return True
        return derived_suffix is not None and candidate.with_suffix(derived_suffix).exists()

    with _STAGE_LOCK:
        dest = dest_dir / stem
        counter = 1
        while taken(dest):
            # Never overwrite: two files can share a name, and silently replacing
            # the first would lose a file the user believes they sent.
            dest = dest_dir / f"{Path(stem).stem}-{counter}{Path(stem).suffix}"
            counter += 1
        if move:
            os.replace(src, dest)
        else:
            shutil.copyfile(src, dest)
    return dest


def prune_staged(staging_root: Path, max_age_days: int, now: float) -> list[Path]:
    """Delete files under <staging_root>/*/ older than `max_age_days`.

    Age-based and blind to which owner or job a file belongs to — the
    directory carries no other record of that once a job is cleared or
    confirmed. Returns what it removed, so the caller can log it.
    """
    cutoff = now - max_age_days * 86400
    removed: list[Path] = []
    if not staging_root.is_dir():
        return removed
    for owner_dir in staging_root.iterdir():
        if not owner_dir.is_dir():
            continue
        for path in owner_dir.iterdir():
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed.append(path)
    return removed
```

In `scripts/tgbot/bot.py`: delete the moved definitions; add near the other imports

```python
from control import materials
from control.materials import fold_diacritics as _fold_diacritics, safe_name as _safe_name
```

and replace the bodies of `_stage_file` and `_prune_old_staged_files` with wrappers (keep their
names, signatures and docstrings; the Telegram-specific error message stays in the bot):

```python
def _stage_file(chat_id: int, src: Path, file_name: str | None) -> Path:
    """<existing docstring>"""
    try:
        return materials.stage_file(ROOT / "batch" / STAGING_DIR_NAME / str(chat_id),
                                    src, file_name)
    except OSError as exc:
        # <existing comment about not including `src` — it contains the bot token>
        stem = _safe_name(file_name or src.name)
        raise RuntimeError(
            f"could not read the uploaded file for {stem}: {exc.strerror}. "
            f"On the VPS, check that telegram-bot-api.yml mounts "
            f"/var/lib/telegram-bot-api at the identical host path.") from exc


def _prune_old_staged_files(now: float | None = None) -> list[Path]:
    """<existing docstring>"""
    return materials.prune_staged(ROOT / "batch" / STAGING_DIR_NAME, STAGING_MAX_AGE_DAYS,
                                  time.time() if now is None else now)
```

Keep `STAGING_DIR_NAME`, `STAGING_MAX_AGE_DAYS` and their comments in `bot.py` (bot tests read
them from `bot`). If anything else in `bot.py` referenced `_SAFE_NAME_RE` or `_TRANSLIT`, import
them from `control.materials` under the old names.

- [ ] **Step 4: Run the new tests and the bot suite**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_materials.py' -v`
Expected: 7 tests PASS
Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_bot.py'`
Expected: OK, same count as before (691)

- [ ] **Step 5: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add scripts/control/materials.py scripts/tgbot/bot.py scripts/tests/test_batch_control_materials.py
git commit -m "refactor(control): move staging and file naming into control.materials

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Materials — list, resolve, delete, thumbnail, ingest

**Files:**
- Modify: `scripts/control/materials.py` (append)
- Test: `scripts/tests/test_batch_control_materials.py` (append classes)

**Interfaces:**
- Consumes: `control.paths.safe_child`; `tgbot.run.busy` through the module (`run_mod.busy`);
  `tgbot.ingest.probe`, `to_png_if_heic`, `quality_warning` through the module (`ingest.probe`).
- Produces:
  - `APP_OWNER = "app"`
  - `class MaterialError(Exception)` with `.code: str`, `.message: str`; codes
    `not_found`, `forbidden`, `in_use`, `unprobeable`
  - `list_materials(staging_root: Path) -> list[dict]` — newest first; each
    `{"id": "<owner>/<name>", "owner", "name", "bytes": int, "updated_at": float,
    "kind": "image"|"video"|"other"}` (kind from suffix: `IMAGE_SUFFIXES`, `VIDEO_SUFFIXES`).
    Symlinks and non-files skipped.
  - `resolve_material(staging_root: Path, owner: str, name: str) -> Path | None`
  - `delete_material(staging_root: Path, batch_dir: Path, owner: str, name: str) -> None`
  - `thumbnail(staging_root: Path, thumbs_root: Path, owner: str, name: str) -> Path` —
    raises `MaterialError("not_found")` or `MaterialError("unprobeable")` if ffmpeg fails
  - `ingest(path: Path) -> tuple[Path, dict]` — HEIC→PNG (returns the PNG path), probe;
    dict `{"kind","width","height","duration_s","bitrate_kbps","size_bytes","warning"}`;
    raises `MaterialError("unprobeable", <ingest's message>)` on `RuntimeError`

- [ ] **Step 1: Write the failing tests** — append to `test_batch_control_materials.py`
  (add `import json, shutil, subprocess` and `from unittest import mock` to the imports):

```python
import tgbot.run as run_mod


def make_png(path: Path) -> Path:
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=red:s=640x480",
                    "-frames:v", "1", str(path)], check=True)
    return path


class MaterialsBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.staging = self.tmp / "tg-staging"
        self.batch = self.tmp
        (self.staging / "app").mkdir(parents=True)
        (self.staging / "12345").mkdir()
        self.a = self.staging / "app" / "a.mp4"; self.a.write_bytes(b"v" * 10)
        self.b = self.staging / "12345" / "b.png"; self.b.write_bytes(b"i")
        os.utime(self.a, (1000, 1000)); os.utime(self.b, (2000, 2000))
        p = mock.patch.object(run_mod, "busy", return_value=False)
        p.start(); self.addCleanup(p.stop)


class TestList(MaterialsBase):
    def test_global_newest_first_with_kind(self):
        got = materials.list_materials(self.staging)
        self.assertEqual([m["id"] for m in got], ["12345/b.png", "app/a.mp4"])
        self.assertEqual(got[1]["kind"], "video")
        self.assertEqual(got[1]["bytes"], 10)
        self.assertNotIn(str(self.tmp), json.dumps(got))

    def test_symlinks_are_not_material(self):
        os.symlink(self.a, self.staging / "app" / "link.mp4")
        self.assertNotIn("app/link.mp4", [m["id"] for m in materials.list_materials(self.staging)])

    def test_resolve_refuses_traversal(self):
        self.assertEqual(materials.resolve_material(self.staging, "app", "a.mp4"), self.a.resolve())
        for owner, name in (("..", "a.mp4"), ("app", "../12345/b.png"), ("app", "nope.mp4")):
            self.assertIsNone(materials.resolve_material(self.staging, owner, name))


class TestDelete(MaterialsBase):
    def test_deletes_app_material(self):
        materials.delete_material(self.staging, self.batch, "app", "a.mp4")
        self.assertFalse(self.a.exists())

    def test_telegram_material_is_forbidden(self):
        with self.assertRaises(materials.MaterialError) as cm:
            materials.delete_material(self.staging, self.batch, "12345", "b.png")
        self.assertEqual(cm.exception.code, "forbidden")

    def test_unknown_is_not_found(self):
        with self.assertRaises(materials.MaterialError) as cm:
            materials.delete_material(self.staging, self.batch, "app", "nope.mp4")
        self.assertEqual(cm.exception.code, "not_found")

    def test_in_use_by_a_busy_manifest(self):
        (self.batch / "r.yaml").write_text(f"runs:\n  - inputs: {{driver: {self.a.resolve()}}}\n")
        with mock.patch.object(run_mod, "busy", return_value=True):
            with self.assertRaises(materials.MaterialError) as cm:
                materials.delete_material(self.staging, self.batch, "app", "a.mp4")
        self.assertEqual(cm.exception.code, "in_use")
        self.assertTrue(self.a.exists())

    def test_a_finished_manifest_does_not_block(self):
        (self.batch / "r.yaml").write_text(f"runs:\n  - inputs: {{driver: {self.a.resolve()}}}\n")
        materials.delete_material(self.staging, self.batch, "app", "a.mp4")
        self.assertFalse(self.a.exists())


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg required")
class TestThumbAndIngest(MaterialsBase):
    def test_thumbnail_is_cached_and_320_wide(self):
        make_png(self.staging / "app" / "p.png")
        thumbs = self.tmp / "thumbs"
        t1 = materials.thumbnail(self.staging, thumbs, "app", "p.png")
        self.assertEqual(t1, thumbs / "app" / "p.png.jpg")
        mtime = t1.stat().st_mtime
        t2 = materials.thumbnail(self.staging, thumbs, "app", "p.png")
        self.assertEqual(t2.stat().st_mtime, mtime)          # cached, not regenerated
        from tgbot import ingest
        self.assertEqual(ingest.probe(t1).width, 320)

    def test_thumbnail_of_garbage_is_unprobeable(self):
        with self.assertRaises(materials.MaterialError) as cm:
            materials.thumbnail(self.staging, self.tmp / "thumbs", "app", "a.mp4")
        self.assertEqual(cm.exception.code, "unprobeable")

    def test_ingest_probes_an_image(self):
        path, info = materials.ingest(make_png(self.staging / "app" / "q.png"))
        self.assertEqual((info["kind"], info["width"], info["height"]), ("image", 640, 480))
        self.assertEqual(info["warning"], "")

    def test_ingest_of_garbage_is_unprobeable(self):
        with self.assertRaises(materials.MaterialError) as cm:
            materials.ingest(self.a)
        self.assertEqual(cm.exception.code, "unprobeable")
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_materials.py' -v`
Expected: FAIL — `AttributeError: module 'control.materials' has no attribute 'list_materials'`

- [ ] **Step 3: Implement** — append to `scripts/control/materials.py` (add `import subprocess`,
  `from control.paths import safe_child`, `import tgbot.run as run_mod`,
  `from tgbot import ingest as ingest_mod` at the top):

```python
APP_OWNER = "app"
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".heic", ".heif", ".bmp"})
VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".m4v", ".webm", ".mkv"})
THUMB_WIDTH = 320


class MaterialError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def _kind(path: Path) -> str:
    suffix = path.suffix.lower()
    return "image" if suffix in IMAGE_SUFFIXES else "video" if suffix in VIDEO_SUFFIXES else "other"


def list_materials(staging_root: Path) -> list[dict]:
    found = []
    for owner_dir in staging_root.iterdir() if staging_root.is_dir() else []:
        if owner_dir.is_symlink() or not owner_dir.is_dir():
            continue
        for path in owner_dir.iterdir():
            if path.is_symlink() or not path.is_file():
                continue
            try:
                st = path.stat()
            except FileNotFoundError:        # pruned or deleted mid-listing
                continue
            found.append({"id": f"{owner_dir.name}/{path.name}", "owner": owner_dir.name,
                          "name": path.name, "bytes": st.st_size, "updated_at": st.st_mtime,
                          "kind": _kind(path)})
    return sorted(found, key=lambda m: m["updated_at"], reverse=True)


def resolve_material(staging_root: Path, owner: str, name: str) -> Path | None:
    owner_dir = safe_child(staging_root, owner)
    if owner_dir is None or (staging_root / owner.strip()).is_symlink() or not owner_dir.is_dir():
        return None
    path = safe_child(owner_dir, name)
    if path is None or (owner_dir / name.strip()).is_symlink() or not path.is_file():
        return None
    return path


def _in_use(batch_dir: Path, path: Path) -> bool:
    """A busy run's manifest names this file. Only busy runs block: a finished
    manifest keeps naming its inputs forever, and would make nothing deletable."""
    needle = str(path)
    for manifest in batch_dir.glob("*.yaml"):
        try:
            if needle in manifest.read_text(encoding="utf-8", errors="replace") \
                    and run_mod.busy(manifest):
                return True
        except OSError:
            continue
    return False


def delete_material(staging_root: Path, batch_dir: Path, owner: str, name: str) -> None:
    path = resolve_material(staging_root, owner, name)
    if path is None:
        raise MaterialError("not_found", "no such material")
    if owner != APP_OWNER:
        # Telegram's /clear and /wipe own the chat directories; deleting a file
        # a Telegram draft points at would break that draft with no message.
        raise MaterialError("forbidden", "only material uploaded from the app can be deleted here")
    if _in_use(batch_dir, path):
        raise MaterialError("in_use", "a running batch uses this file")
    path.unlink(missing_ok=True)


def thumbnail(staging_root: Path, thumbs_root: Path, owner: str, name: str) -> Path:
    src = resolve_material(staging_root, owner, name)
    if src is None:
        raise MaterialError("not_found", "no such material")
    dest = thumbs_root / owner / f"{name}.jpg"
    if dest.is_file() and dest.stat().st_mtime >= src.stat().st_mtime:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp.jpg")
    # -ss before -i seeks cheaply; 0.5 s skips the black first frame many phone
    # videos start with. For a still image ffmpeg ignores the seek.
    seek = ["-ss", "0.5"] if _kind(src) == "video" else []
    cmd = ["ffmpeg", "-v", "error", "-y", *seek, "-i", str(src), "-frames:v", "1",
           "-vf", f"scale={THUMB_WIDTH}:-2", str(tmp)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise MaterialError("unprobeable", f"no preview for {name}: {exc}") from exc
    if out.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise MaterialError("unprobeable", f"no preview for {name}")
    os.replace(tmp, dest)
    return dest


def ingest(path: Path) -> tuple[Path, dict]:
    """HEIC→PNG, then measure — the same arrival gate the bot applies (ingest.py)."""
    try:
        path = ingest_mod.to_png_if_heic(path)
        p = ingest_mod.probe(path)
    except RuntimeError as exc:
        raise MaterialError("unprobeable", str(exc)) from exc
    return path, {"kind": p.kind, "width": p.width, "height": p.height,
                  "duration_s": p.duration_s, "bitrate_kbps": p.bitrate_kbps,
                  "size_bytes": p.size_bytes, "warning": ingest_mod.quality_warning(p)}
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_*.py' -v`
Expected: all PASS
Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_bot.py'`
Expected: OK

- [ ] **Step 5: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add scripts/control/materials.py scripts/tests/test_batch_control_materials.py
git commit -m "feat(control): list, preview, ingest and delete material

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: `control/uploads.py` — resumable chunked uploads

**Files:**
- Create: `scripts/control/uploads.py`
- Test: `scripts/tests/test_batch_control_uploads.py`

**Interfaces:**
- Consumes: `control.materials.stage_file(dest_dir, src, file_name, move=True)`;
  `control.paths.safe_child`.
- Produces:
  - `CHUNK_SIZE = 32 * 1024 * 1024`, `MAX_UPLOAD_BYTES = 2 * 1024 ** 3`,
    `DISK_HEADROOM = 1024 ** 3`, `READ_BLOCK = 1024 * 1024`
  - `class UploadError(Exception)` with `.code`, `.message`; codes `bad_request`, `too_large`,
    `no_space`, `not_found`, `incomplete`
  - `open_upload(uploads_root: Path, file_name: str, size: int, *, free_bytes=None) -> dict`
    → `{"upload_id", "chunk_size", "chunks_total"}`; `free_bytes` defaults to
    `shutil.disk_usage(uploads_root).free` (tests inject)
  - `expected_chunk_length(meta: dict, n: int) -> int`
  - `write_chunk(uploads_root: Path, upload_id: str, n: int, stream, length: int) -> None`
    — reads exactly `length` bytes from `stream` (`.read(k)`) in `READ_BLOCK` pieces into
    `NNNNN.part.<uuid>.tmp`, then `os.replace` to `NNNNN.part`
  - `upload_status(uploads_root: Path, upload_id: str) -> dict` → open_upload's keys plus
    `"file_name", "size", "received": [n, ...]`
  - `assemble(uploads_root: Path, upload_id: str, dest_dir: Path) -> Path` — the staged path
  - `prune_uploads(uploads_root: Path, max_age_sec: float, now: float) -> list[str]`

- [ ] **Step 1: Write the failing tests**

```python
# scripts/tests/test_batch_control_uploads.py
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from control import uploads

BIG = 10 ** 13          # "plenty of disk" for tests


class UploadsBase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp()) / "uploads"
        self.dest = self.root.parent / "tg-staging" / "app"
        p = mock.patch.object(uploads, "CHUNK_SIZE", 4)       # tiny chunks: 10 bytes = 3 chunks
        p.start(); self.addCleanup(p.stop)

    def open(self, name="clip.mp4", size=10):
        return uploads.open_upload(self.root, name, size, free_bytes=BIG)

    def put(self, uid, n, data):
        uploads.write_chunk(self.root, uid, n, io.BytesIO(data), len(data))


class TestOpen(UploadsBase):
    def test_returns_id_and_chunk_count(self):
        u = self.open()
        self.assertEqual((u["chunk_size"], u["chunks_total"]), (4, 3))
        self.assertTrue((self.root / u["upload_id"] / "meta.json").is_file())

    def test_rejects_bad_input(self):
        for name, size, code in (("", 10, "bad_request"), ("a.mp4", 0, "bad_request"),
                                 ("a.mp4", uploads.MAX_UPLOAD_BYTES + 1, "too_large")):
            with self.assertRaises(uploads.UploadError) as cm:
                uploads.open_upload(self.root, name, size, free_bytes=BIG)
            self.assertEqual(cm.exception.code, code)

    def test_refuses_without_disk_headroom(self):
        with self.assertRaises(uploads.UploadError) as cm:
            uploads.open_upload(self.root, "a.mp4", 10, free_bytes=uploads.DISK_HEADROOM + 19)
        self.assertEqual(cm.exception.code, "no_space")


class TestChunks(UploadsBase):
    def test_wrong_length_or_index_is_bad_request(self):
        uid = self.open()["upload_id"]
        for n, data in ((0, b"abc"), (2, b"ab12"), (3, b"xx"), (-1, b"abcd")):
            with self.assertRaises(uploads.UploadError, msg=(n, data)) as cm:
                self.put(uid, n, data)
            self.assertEqual(cm.exception.code, "bad_request")

    def test_short_stream_leaves_no_part(self):
        uid = self.open()["upload_id"]
        with self.assertRaises(uploads.UploadError):
            uploads.write_chunk(self.root, uid, 0, io.BytesIO(b"ab"), 4)
        self.assertEqual(uploads.upload_status(self.root, uid)["received"], [])
        self.assertEqual(list((self.root / uid).glob("*.tmp")), [])

    def test_resume_and_overwrite(self):
        uid = self.open()["upload_id"]
        self.put(uid, 2, b"90")
        self.put(uid, 0, b"XXXX")
        self.put(uid, 0, b"0123")              # re-sent chunk overwrites
        self.assertEqual(uploads.upload_status(self.root, uid)["received"], [0, 2])

    def test_unknown_or_hostile_id(self):
        for uid in ("nope", "../x", ""):
            with self.assertRaises(uploads.UploadError) as cm:
                uploads.upload_status(self.root, uid)
            self.assertEqual(cm.exception.code, "not_found")


class TestAssemble(UploadsBase):
    def test_incomplete_is_refused(self):
        uid = self.open()["upload_id"]
        self.put(uid, 0, b"0123")
        with self.assertRaises(uploads.UploadError) as cm:
            uploads.assemble(self.root, uid, self.dest)
        self.assertEqual(cm.exception.code, "incomplete")

    def test_byte_identical_and_cleaned_up(self):
        uid = self.open("Áo dài.mp4")["upload_id"]
        for n, data in ((1, b"4567"), (0, b"0123"), (2, b"89")):
            self.put(uid, n, data)
        staged = uploads.assemble(self.root, uid, self.dest)
        self.assertEqual(staged, self.dest / "Ao_dai.mp4")
        self.assertEqual(staged.read_bytes(), b"0123456789")
        self.assertFalse((self.root / uid).exists())


class TestPrune(UploadsBase):
    def test_removes_only_stale_uploads(self):
        old, new = self.open()["upload_id"], self.open()["upload_id"]
        now = time.time()
        meta = self.root / old / "meta.json"
        m = json.loads(meta.read_text()); m["created_at"] = now - 25 * 3600
        meta.write_text(json.dumps(m))
        self.assertEqual(uploads.prune_uploads(self.root, 24 * 3600, now), [old])
        self.assertTrue((self.root / new).exists())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_uploads.py' -v`
Expected: FAIL — `ImportError: cannot import name 'uploads' from 'control'`

- [ ] **Step 3: Implement**

```python
# scripts/control/uploads.py
"""Resumable chunked uploads (spec §5.1).

Cloudflare's free plan caps a request body at 100 MB, and phone uploads get
cut off, so a file arrives as numbered chunks that can be re-sent in any
order. Everything lives on disk under uploads_root/<id>/ — nothing is held in
memory beyond one READ_BLOCK, because the VPS has 1 GB of RAM.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import threading
import time
import uuid
from pathlib import Path

from control.materials import safe_name, stage_file
from control.paths import safe_child

CHUNK_SIZE = 32 * 1024 * 1024        # well under Cloudflare's 100 MB body cap
MAX_UPLOAD_BYTES = 2 * 1024 ** 3     # the local Telegram Bot API's own file limit
DISK_HEADROOM = 1024 ** 3            # left free for the bot, out/ and the journals
READ_BLOCK = 1024 * 1024

# assemble() must not run twice for one upload at once (a phone retrying a
# slow complete) — the second would find half-deleted chunks.
_ASSEMBLE_LOCK = threading.Lock()


class UploadError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def _dir(uploads_root: Path, upload_id: str) -> Path:
    d = safe_child(uploads_root, upload_id) if upload_id else None
    if d is None or not (d / "meta.json").is_file():
        raise UploadError("not_found", "no such upload")
    return d


def _meta(d: Path) -> dict:
    return json.loads((d / "meta.json").read_text(encoding="utf-8"))


def _chunks_total(size: int) -> int:
    return math.ceil(size / CHUNK_SIZE)


def open_upload(uploads_root: Path, file_name: str, size: int, *, free_bytes=None) -> dict:
    if not isinstance(file_name, str) or not file_name.strip():
        raise UploadError("bad_request", "file_name is required")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise UploadError("bad_request", "size must be a positive integer")
    if size > MAX_UPLOAD_BYTES:
        raise UploadError("too_large", f"files over {MAX_UPLOAD_BYTES} bytes are not accepted")
    uploads_root.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(uploads_root).free if free_bytes is None else free_bytes
    # Chunks and the assembled file coexist until assembly finishes: 2x.
    if free < 2 * size + DISK_HEADROOM:
        raise UploadError("no_space", "not enough free disk on the server for this file")
    upload_id = uuid.uuid4().hex
    d = uploads_root / upload_id
    d.mkdir()
    meta = {"file_name": safe_name(file_name), "size": size, "chunk_size": CHUNK_SIZE,
            "created_at": time.time()}
    (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return {"upload_id": upload_id, "chunk_size": CHUNK_SIZE, "chunks_total": _chunks_total(size)}


def expected_chunk_length(meta: dict, n: int) -> int:
    total = math.ceil(meta["size"] / meta["chunk_size"])
    if not 0 <= n < total:
        return -1
    return meta["chunk_size"] if n < total - 1 else meta["size"] - meta["chunk_size"] * (total - 1)


def write_chunk(uploads_root: Path, upload_id: str, n: int, stream, length: int) -> None:
    d = _dir(uploads_root, upload_id)
    if length != expected_chunk_length(_meta(d), n):
        raise UploadError("bad_request", f"chunk {n} must be exactly "
                          f"{expected_chunk_length(_meta(d), n)} bytes")
    tmp = d / f"{n:05d}.part.{uuid.uuid4().hex}.tmp"
    remaining = length
    try:
        with tmp.open("wb") as f:
            while remaining > 0:
                block = stream.read(min(READ_BLOCK, remaining))
                if not block:
                    raise UploadError("bad_request", f"chunk {n} ended early")
                f.write(block)
                remaining -= len(block)
        os.replace(tmp, d / f"{n:05d}.part")
    finally:
        tmp.unlink(missing_ok=True)


def _received(d: Path, meta: dict) -> list[int]:
    got = []
    for n in range(math.ceil(meta["size"] / meta["chunk_size"])):
        part = d / f"{n:05d}.part"
        if part.is_file() and part.stat().st_size == expected_chunk_length(meta, n):
            got.append(n)
    return got


def upload_status(uploads_root: Path, upload_id: str) -> dict:
    d = _dir(uploads_root, upload_id)
    meta = _meta(d)
    return {"upload_id": upload_id, "file_name": meta["file_name"], "size": meta["size"],
            "chunk_size": meta["chunk_size"],
            "chunks_total": math.ceil(meta["size"] / meta["chunk_size"]),
            "received": _received(d, meta)}


def assemble(uploads_root: Path, upload_id: str, dest_dir: Path) -> Path:
    with _ASSEMBLE_LOCK:
        d = _dir(uploads_root, upload_id)
        meta = _meta(d)
        total = math.ceil(meta["size"] / meta["chunk_size"])
        if len(_received(d, meta)) != total:
            raise UploadError("incomplete", "not every chunk has arrived; check GET /v1/uploads/{id}")
        combined = d / "assembled"
        with combined.open("wb") as out:
            for n in range(total):
                with (d / f"{n:05d}.part").open("rb") as part:
                    shutil.copyfileobj(part, out, READ_BLOCK)
        staged = stage_file(dest_dir, combined, meta["file_name"], move=True)
        shutil.rmtree(d, ignore_errors=True)
        return staged


def prune_uploads(uploads_root: Path, max_age_sec: float, now: float) -> list[str]:
    removed = []
    for d in uploads_root.iterdir() if uploads_root.is_dir() else []:
        try:
            created = _meta(d)["created_at"]
        except (OSError, ValueError, KeyError):
            created = d.stat().st_mtime            # unreadable meta: judge by the dir
        if now - created > max_age_sec:
            shutil.rmtree(d, ignore_errors=True)
            removed.append(d.name)
    return removed
```

Note: `open_upload`'s returned `chunk_size` must be the module's current `CHUNK_SIZE` (tests patch
it), which the code above does; `write_chunk` and the others read it back from `meta.json`, so a
deploy that changes `CHUNK_SIZE` does not break uploads already in flight.

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_*.py' -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add scripts/control/uploads.py scripts/tests/test_batch_control_uploads.py
git commit -m "feat(control): resumable chunked uploads stored on disk

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: HTTP — POST/PUT/DELETE, upload and material routes

**Files:**
- Modify: `scripts/httpapi/server.py`
- Test: `scripts/tests/test_batch_control_http.py` (append classes; add imports at the top)

**Interfaces:**
- Consumes: Task 2 `materials.*`, Task 3 `uploads.*`, existing `send_file`.
- Produces routes (all authenticated; JSON errors):

| Method + path | Success |
|---|---|
| `POST /v1/uploads` body `{"file_name": str, "size": int}` | `201` `{upload_id, chunk_size, chunks_total}` |
| `PUT /v1/uploads/{id}/chunks/{n}` raw body | `200` `{"received": n}` |
| `GET /v1/uploads/{id}` | `200` status dict |
| `POST /v1/uploads/{id}/complete` | `201` `{"material": {list item}, "probe": {...}}` |
| `GET /v1/materials` | `200` `{"materials": [...]}` |
| `GET /v1/materials/{owner}/{name}/thumb` | `200` image/jpeg (via `send_file`) |
| `DELETE /v1/materials/{owner}/{name}` | `204`, empty body |

- Server attributes derived in `make_server` (signature unchanged): `staging_root =
  batch_dir / "tg-staging"`, `uploads_root = batch_dir / "uploads"`, `thumbs_root =
  batch_dir / "thumbs"`. `tg-staging` must equal `tgbot.bot.STAGING_DIR_NAME`; do not import the
  bot — a test asserts the two agree.
- Error mapping: `UploadError`/`MaterialError` code → status:
  `bad_request 400, forbidden 403, not_found 404, in_use 409, incomplete 409, too_large 413,
  unprobeable 422, no_space 507`.
- On `complete`, if `materials.ingest` raises `unprobeable`, delete the staged file(s) (the staged
  path and, if different, the PNG) before answering `422`: a file the pipeline cannot read must not
  sit in the material list looking usable.

- [ ] **Step 1: Write the failing tests** — append to `test_batch_control_http.py`
  (add `import shutil, subprocess` and `from control import materials, uploads` at the top):

```python
class HttpWriteBase(HttpTestBase):
    def send(self, method, path, body=b"", *, token=TOKEN, headers=None, json_body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        h = dict(headers or {})
        if token is not None:
            h["Authorization"] = f"Bearer {token}"
        if json_body is not None:
            body = json.dumps(json_body).encode()
            h["Content-Type"] = "application/json"
        conn.request(method, path, body=body, headers=h)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp, data

    def setUp(self):
        super().setUp()
        p = mock.patch.object(uploads, "CHUNK_SIZE", 4)
        p.start(); self.addCleanup(p.stop)
        p = mock.patch("shutil.disk_usage", return_value=mock.Mock(free=10 ** 13))
        p.start(); self.addCleanup(p.stop)


class TestWriteAuthAndErrors(HttpWriteBase):
    def test_every_method_requires_the_token_and_answers_json(self):
        for method in ("POST", "PUT", "DELETE"):
            resp, body = self.send(method, "/v1/uploads", token=None)
            self.assertEqual(resp.status, 401, method)
            self.assertEqual(json.loads(body)["error"]["code"], "unauthorized")

    def test_bad_json_is_400(self):
        resp, body = self.send("POST", "/v1/uploads", b"{not json",
                               headers={"Content-Type": "application/json"})
        self.assertEqual(resp.status, 400)

    def test_staging_dir_name_matches_the_bot(self):
        import tgbot.bot as bot
        self.assertEqual(self.server.staging_root.name, bot.STAGING_DIR_NAME)


class TestUploadFlow(HttpWriteBase):
    def test_chunked_upload_end_to_end(self):
        resp, body = self.send("POST", "/v1/uploads", json_body={"file_name": "clip.bin", "size": 10})
        self.assertEqual(resp.status, 201)
        uid = json.loads(body)["upload_id"]
        for n, data in ((2, b"89"), (0, b"0123"), (1, b"4567")):
            resp, _ = self.send("PUT", f"/v1/uploads/{uid}/chunks/{n}", data)
            self.assertEqual(resp.status, 200)
        resp, body = self.send("GET", f"/v1/uploads/{uid}")
        self.assertEqual(json.loads(body)["received"], [0, 1, 2])
        with mock.patch.object(materials, "ingest",
                               side_effect=lambda p: (p, {"kind": "video"})):
            resp, body = self.send("POST", f"/v1/uploads/{uid}/complete")
        self.assertEqual(resp.status, 201)
        got = json.loads(body)
        self.assertEqual(got["material"]["id"], "app/clip.bin")
        self.assertEqual((self.batch / "tg-staging" / "app" / "clip.bin").read_bytes(), b"0123456789")

    def test_wrong_chunk_length_is_400_and_closes(self):
        uid = json.loads(self.send("POST", "/v1/uploads",
                                   json_body={"file_name": "c.bin", "size": 10})[1])["upload_id"]
        resp, _ = self.send("PUT", f"/v1/uploads/{uid}/chunks/0", b"abc")
        self.assertEqual(resp.status, 400)
        self.assertEqual(resp.getheader("Connection"), "close")

    def test_complete_before_all_chunks_is_409(self):
        uid = json.loads(self.send("POST", "/v1/uploads",
                                   json_body={"file_name": "c.bin", "size": 10})[1])["upload_id"]
        resp, body = self.send("POST", f"/v1/uploads/{uid}/complete")
        self.assertEqual((resp.status, json.loads(body)["error"]["code"]), (409, "incomplete"))

    def test_unprobeable_complete_is_422_and_leaves_nothing(self):
        uid = json.loads(self.send("POST", "/v1/uploads",
                                   json_body={"file_name": "junk.mp4", "size": 4})[1])["upload_id"]
        self.send("PUT", f"/v1/uploads/{uid}/chunks/0", b"junk")
        with mock.patch.object(materials, "ingest",
                               side_effect=materials.MaterialError("unprobeable", "bad")):
            resp, _ = self.send("POST", f"/v1/uploads/{uid}/complete")
        self.assertEqual(resp.status, 422)
        self.assertFalse((self.batch / "tg-staging" / "app" / "junk.mp4").exists())

    def test_too_large_is_413(self):
        resp, _ = self.send("POST", "/v1/uploads",
                            json_body={"file_name": "x", "size": uploads.MAX_UPLOAD_BYTES + 1})
        self.assertEqual(resp.status, 413)


class TestMaterialRoutes(HttpWriteBase):
    def setUp(self):
        super().setUp()
        d = self.batch / "tg-staging" / "app"
        d.mkdir(parents=True)
        (d / "a.mp4").write_bytes(b"v")
        (self.batch / "tg-staging" / "99").mkdir()
        (self.batch / "tg-staging" / "99" / "t.png").write_bytes(b"i")

    def test_list(self):
        resp, body = self.send("GET", "/v1/materials")
        self.assertEqual(sorted(m["id"] for m in json.loads(body)["materials"]),
                         ["99/t.png", "app/a.mp4"])

    def test_delete_app_material_is_204(self):
        resp, body = self.send("DELETE", "/v1/materials/app/a.mp4")
        self.assertEqual((resp.status, body), (204, b""))
        self.assertFalse((self.batch / "tg-staging" / "app" / "a.mp4").exists())

    def test_delete_telegram_material_is_403(self):
        resp, _ = self.send("DELETE", "/v1/materials/99/t.png")
        self.assertEqual(resp.status, 403)

    def test_delete_traversal_is_404(self):
        resp, _ = self.send("DELETE", "/v1/materials/app/..%2F99%2Ft.png")
        self.assertEqual(resp.status, 404)

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg required")
    def test_thumb_is_jpeg(self):
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=blue:s=640x480",
                        "-frames:v", "1", str(self.batch / "tg-staging" / "app" / "p.png")], check=True)
        resp, body = self.send("GET", "/v1/materials/app/p.png/thumb")
        self.assertEqual((resp.status, resp.getheader("Content-Type")), (200, "image/jpeg"))
        self.assertTrue(body.startswith(b"\xff\xd8"))
```

(`HttpTestBase` already provides `self.batch`, `self.out`, `self.server`, and patches
`tgbot.run` liveness. If its batch dir attribute is named differently, use that name.)

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_http.py' -v`
Expected: FAIL — new tests error (`501`/`404`/`AttributeError: staging_root`)

- [ ] **Step 3: Implement** in `scripts/httpapi/server.py`:

1. Imports: `import control.materials as materials`, `import control.uploads as uploads`.
2. Error mapping constant:

```python
_DOMAIN_STATUS = {"bad_request": 400, "forbidden": 403, "not_found": 404, "in_use": 409,
                  "incomplete": 409, "too_large": 413, "unprobeable": 422, "no_space": 507}
MAX_JSON_BODY = 64 * 1024
```

3. Replace `do_GET` with one dispatcher used by all four verbs:

```python
    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def do_DELETE(self):
        self._handle("DELETE")

    def _handle(self, method: str) -> None:
        try:
            self._authenticate()
            self._route(method)
        except ApiError as exc:
            self._error(exc.status, exc.code, exc.message)
        except (uploads.UploadError, materials.MaterialError) as exc:
            self._error(_DOMAIN_STATUS.get(exc.code, 400), exc.code, exc.message)
        except Exception:
            self.server.log("api: request failed\n" + traceback.format_exc())
            self._error(500, "internal", "internal error")

    def _error(self, status: int, code: str, message: str) -> None:
        # A request that carried a body may not have been read to the end; the
        # leftover bytes would be parsed as the next request on this connection.
        if self.command in ("POST", "PUT", "DELETE"):
            self.close_connection = True
        self._send_json(status, {"error": {"code": code, "message": message}})
```

   `_send_json` must add `Connection: close` when `self.close_connection` is True (so the client
   sees it), i.e. `if self.close_connection: self.send_header("Connection", "close")`.

4. Body helpers:

```python
    def _content_length(self) -> int:
        raw = self.headers.get("Content-Length")
        if raw is None:
            raise ApiError(411, "length_required", "Content-Length is required")
        try:
            n = int(raw)
        except ValueError:
            raise ApiError(400, "bad_request", "bad Content-Length")
        if n < 0:
            raise ApiError(400, "bad_request", "bad Content-Length")
        return n

    def _read_json(self) -> dict:
        n = self._content_length()
        if n > MAX_JSON_BODY:
            raise ApiError(413, "too_large", "JSON body too large")
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            raise ApiError(400, "bad_request", "body is not valid JSON")
        if not isinstance(data, dict):
            raise ApiError(400, "bad_request", "body must be a JSON object")
        return data
```

5. `_route(self, method)`: keep the existing GET branches under `if method == "GET":` and add:

```python
        if method == "POST" and rest == ["uploads"]:
            body = self._read_json()
            return self._send_json(201, uploads.open_upload(
                s.uploads_root, body.get("file_name"), body.get("size")))
        if method == "PUT" and len(rest) == 4 and rest[0] == "uploads" and rest[2] == "chunks":
            try:
                n = int(rest[3])
            except ValueError:
                raise NOT_FOUND
            length = self._content_length()
            if length > uploads.CHUNK_SIZE:
                raise ApiError(413, "too_large", "chunk larger than chunk_size")
            uploads.write_chunk(s.uploads_root, rest[1], n, self.rfile, length)
            return self._send_json(200, {"received": n})
        if method == "GET" and len(rest) == 2 and rest[0] == "uploads":
            return self._send_json(200, uploads.upload_status(s.uploads_root, rest[1]))
        if method == "POST" and len(rest) == 3 and rest[0] == "uploads" and rest[2] == "complete":
            staged = uploads.assemble(s.uploads_root, rest[1], s.staging_root / materials.APP_OWNER)
            try:
                final, probe = materials.ingest(staged)
            except materials.MaterialError:
                # Unreadable material must not sit in the list looking usable.
                # A HEIC may already have produced its PNG twin before probe failed.
                staged.unlink(missing_ok=True)
                if staged.suffix.lower() in (".heic", ".heif"):
                    staged.with_suffix(".png").unlink(missing_ok=True)
                raise
            item = next(m for m in materials.list_materials(s.staging_root)
                        if m["id"] == f"{materials.APP_OWNER}/{final.name}")
            return self._send_json(201, {"material": item, "probe": probe})
        if method == "GET" and rest == ["materials"]:
            return self._send_json(200, {"materials": materials.list_materials(s.staging_root)})
        if method == "GET" and len(rest) == 4 and rest[0] == "materials" and rest[3] == "thumb":
            thumb = materials.thumbnail(s.staging_root, s.thumbs_root, rest[1], rest[2])
            try:
                return send_file(self, thumb)
            except FileNotFoundError:
                raise NOT_FOUND
        if method == "DELETE" and len(rest) == 3 and rest[0] == "materials":
            materials.delete_material(s.staging_root, s.batch_dir, rest[1], rest[2])
            return self._send_empty(204)
        raise NOT_FOUND
```

   Keep the existing GET branches; POST/PUT/DELETE to a GET-only path fall through to `404`.
   Add `_send_empty(self, status)` that sends the status, `Content-Length: 0`, and ends headers.
   `_send_json` currently only sets ETag for 200; keep that (201 carries no ETag).

6. `make_server`: after the existing attribute assignment add
   `server.staging_root = batch_dir / "tg-staging"`, `server.uploads_root = batch_dir / "uploads"`,
   `server.thumbs_root = batch_dir / "thumbs"`.

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_*.py' -v`
Expected: all PASS, output free of warnings (ResourceWarning included)

- [ ] **Step 5: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add scripts/httpapi/server.py scripts/tests/test_batch_control_http.py
git commit -m "feat(httpapi): chunked upload and material routes with write verbs

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Bot prune tick covers uploads and thumbnails

**Files:**
- Modify: `scripts/tgbot/bot.py` (`_tick_staging_prune`)
- Modify: `scripts/control/materials.py` (add `prune_thumbs`)
- Test: `scripts/tests/test_batch_control_materials.py` (append), `scripts/tests/test_batch_bot.py`
  only if an existing test of `_tick_staging_prune` needs its mock extended — do not change what it
  asserts.

**Interfaces:**
- Consumes: `uploads.prune_uploads(uploads_root, max_age_sec, now)`.
- Produces: `materials.prune_thumbs(thumbs_root: Path, staging_root: Path) -> list[Path]` — removes
  `<thumbs_root>/<owner>/<name>.jpg` whose `<staging_root>/<owner>/<name>` no longer exists.
  `UPLOAD_MAX_AGE_SEC = 24 * 3600` defined in `bot.py` next to `STAGING_MAX_AGE_DAYS`, with a
  comment: an abandoned upload holds up to 2 GiB on a 25 GB disk, and a phone resuming within a
  day is the realistic case.

- [ ] **Step 1: Write the failing test** — append to `test_batch_control_materials.py`:

```python
class TestPruneThumbs(unittest.TestCase):
    def test_removes_thumbs_whose_source_is_gone(self):
        tmp = Path(tempfile.mkdtemp())
        staging, thumbs = tmp / "tg-staging", tmp / "thumbs"
        (staging / "app").mkdir(parents=True); (thumbs / "app").mkdir(parents=True)
        (staging / "app" / "keep.png").write_bytes(b"k")
        keep, gone = thumbs / "app" / "keep.png.jpg", thumbs / "app" / "gone.png.jpg"
        keep.write_bytes(b"j"); gone.write_bytes(b"j")
        self.assertEqual(materials.prune_thumbs(thumbs, staging), [gone])
        self.assertTrue(keep.exists())
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_materials.py' -v`
Expected: FAIL — `AttributeError: ... 'prune_thumbs'`

- [ ] **Step 3: Implement**

```python
def prune_thumbs(thumbs_root: Path, staging_root: Path) -> list[Path]:
    """Thumbnails whose material is gone (pruned, deleted, /clear'd)."""
    removed = []
    for owner_dir in thumbs_root.iterdir() if thumbs_root.is_dir() else []:
        if not owner_dir.is_dir():
            continue
        for thumb in owner_dir.glob("*.jpg"):
            if not (staging_root / owner_dir.name / thumb.name[: -len(".jpg")]).exists():
                thumb.unlink(missing_ok=True)
                removed.append(thumb)
    return removed
```

In `bot.py` add `from control import uploads` and extend `_tick_staging_prune` after the existing
staged-file prune (same once-a-day gate, same `log` style):

```python
    dropped = uploads.prune_uploads(ROOT / "batch" / "uploads", UPLOAD_MAX_AGE_SEC, now)
    if dropped:
        log(f"pruned {len(dropped)} abandoned upload(s) older than 24h")
    thumbs = materials.prune_thumbs(ROOT / "batch" / "thumbs", ROOT / "batch" / STAGING_DIR_NAME)
    if thumbs:
        log(f"pruned {len(thumbs)} orphaned thumbnail(s)")
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_*.py' -v`
Expected: all PASS
Run: `make batch-test`
Expected: OK

- [ ] **Step 5: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add scripts/control/materials.py scripts/tgbot/bot.py scripts/tests/test_batch_control_materials.py
git commit -m "feat(tgbot): daily prune also drops abandoned uploads and orphaned thumbnails

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## After the tasks (controller, with the user's standing approval to deploy)

Merge via PR, let the deploy run, then from the Mac through the real tunnel: upload a real driver
video of >32 MiB (so it takes ≥2 chunks), resume one chunk deliberately, complete, fetch its
thumbnail, list, delete it; record the timings and `motion-bot` RSS in `scripts/vps/README.md`.
