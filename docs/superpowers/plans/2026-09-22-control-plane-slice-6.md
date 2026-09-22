# Control-plane API slice 6 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix a real drop-before-rent bug in the app's shared-run-slot confirm path, add a persistent
opt-in try-on library, seed a job's try-on stage from a saved library entry, and let the app steer a
regenerate with a closed guidance vocabulary and read version history.

**Architecture:** Everything here extends the existing control-plane API (bot.py + scripts/control/ +
scripts/httpapi/) from slices 1-5. One genuinely new module (`control/tryon_library.py`, shaped like
`control/drafts.py`'s `DraftStore`) plus small, surgical edits to `AppRuns.confirm`'s branch selection,
`tgbot/job.py`'s `Job`/`render_manifest`, `batchlib/runner.py`'s local-phase worker, and
`batchlib/local_tryon.py`'s prompt builders.

**Tech Stack:** Python 3, stdlib `http.server`, existing `batchlib`/`control`/`tgbot` packages. No new
dependencies.

**Spec:** `docs/superpowers/specs/2026-09-21-vps-control-plane-api-design.md` — §5.10 (slice 6), plus
the amended §4.1/§5.2/§5.3/§6/§7/§9. Read §5.10 before Task 1; it is the argument this plan implements.

## Global Constraints

- Every slice deploys on its own; `scripts/tests/test_batch_bot.py` must stay green after this slice
  with zero changes to its own file — the Telegram bot's own behaviour must be byte-identical.
- All new tests are free, no pod. New test modules for control-plane-specific behaviour are named
  `scripts/tests/test_batch_control_*.py` so `make batch-test` picks them up; tests of
  `batchlib/runner.py` and `batchlib/local_tryon.py` go in their existing homes,
  `scripts/tests/test_batch_runner.py` and `scripts/tests/test_batch_local_tryon.py`.
- `motions-studio/setup/scrub-secrets.sh --check` must exit 0 before every commit (repo is public).
- Write all code, docs, commit messages and PR bodies in English (CLAUDE.md convention).
- Never assert cost from `currentSpendPerHr`. This slice adds no new spend path; nothing here should
  touch billing at all.
- Idempotency: only routes that spend money or provider quota need an `Idempotency-Key` (spec §5.5,
  §5.9). `GET/POST/DELETE /v1/tryon-library*` and `PATCH /v1/draft`'s `tryon_seed` field are free file
  operations and take none, exactly like every other `/v1/draft/*` mutation. `regen` already requires
  one; adding `guidance` to its body does not change that.
- Status codes reuse the existing table (spec §5.6): bad request shapes are `400`; a missing/not-found
  id is `404`. This slice introduces no new status code.
- Any new file-serving route goes through `control.paths.safe_child` (or an existing helper built on
  it), matching `materials.thumbnail`, `outputs.resolve_output` and `AppRuns.tryon_image` — no new
  path-traversal surface.
- `scripts/tests/test_batch_control_invariants.py`'s grep/AST checks (call-site counts for
  `start_drain`, `_do_kill`, `_start_migration`; forbidden strings `gpu-destroy`/`volume_migrate` in
  `scripts/control/` and `scripts/httpapi/`) must stay green. Nothing in this slice touches those call
  sites or adds either forbidden string.
- Keep the established split: Telegram-visible behaviour (`_do_phase_a`, `_do_confirm`, `_do_resume`,
  `_regen_tryon`, `AppRuns`, `AppPod`) stays in `scripts/tgbot/bot.py` — 58+ existing tests patch these
  functions by name there. Only genuinely new, Telegram-free logic (`tryon_library.py`) is new in
  `scripts/control/`.
- `make check-batch-params` must stay green: any new manifest-level key this slice invents
  (`seedImage`) must be registered in `scripts/batch-params.json` as a curated `extra` entry with a
  `why`, the same way `naturalNails`/`removeWristAccessories` already are — never a bare, unregistered
  key that `make batch-validate` would then reject as "param không có thật".

## Controller decisions

1. **Drop-before-rent is fixed by changing which branch `AppRuns.confirm` takes, not by adding a new
   code path to `_do_phase_a`/`_do_confirm`.** `_do_confirm` already writes a fresh manifest for
   whatever `jobs` it is given and already offers its own reuse/rerun chooser
   (`_preserved_tryon`/`local_tryon_reusable`) for any job whose try-on is still journalled `done`
   under its stable, content-hashed run id. The bug is only that `AppRuns.confirm` currently routes to
   `_do_resume` (which re-rents the manifest already on disk) whenever `_PHASE_A_OFFERED` matches the
   manifest's token — a check keyed on the *manifest's* identity, not the *draft's* — so a job dropped
   from the draft after Phase A ran is invisible to it. The fix adds one more condition to that branch
   test: the draft's current run ids must still equal the manifest's run ids.
2. **The try-on library is one `TryonLibrary` instance, constructed once in `make_server`, exactly like
   `server.drafts`.** No 503-when-unwired gate (unlike `AppRuns`/`AppPod`): it depends on nothing from
   the bot process, so it can exist before `_start_control_api` ever runs.
3. **`tryon_seed` seeding happens inside `batchlib/runner.py`'s `_one()`, intercepting before
   `run_local_tryon` is ever called.** The bot never knows a job's `out_dir`/`batch_id` in advance
   (`batch_run.py` decides it), so the bot cannot pre-write a journal entry — the manifest itself is
   the only channel that survives from "the app composed a job" to "the runner is about to try it",
   and the runner is the only place that knows the real `dest` path.
4. **Guided regenerate reuses the existing extra-flag-into-prompt-clause pattern**
   (`tryon_hands_prompts`/`_flag`), as a sibling function, not a rewrite of it.
5. **The try-on library is scoped to `owner="app"` only** (`materials.APP_OWNER`), matching
   `DraftStore`'s existing fixed-owner construction in `make_server`. Telegram has no UI for it.

---

### Task 1: Fix the drop-before-rent gap in `AppRuns.confirm`

**Files:**
- Modify: `scripts/tgbot/bot.py` (the `AppRuns` class, ~line 6840-6960; see `confirm` at ~6910)
- Test: `scripts/tests/test_batch_control_botruns.py`

**Interfaces:**
- Consumes: `self.drafts.runnable() -> tuple[list[Job], bool | None, int]` (existing, `control/drafts.py`),
  `_job_manifest_path(chat_id) -> Path` (existing), `load_manifest`/`ManifestError` (already imported
  in `bot.py` from `batchlib.manifest`), `_unique_ids` (already imported in `bot.py` from `tgbot.job`).
- Produces: `AppRuns._phase_a_matches_draft(self) -> bool`, used only inside `AppRuns.confirm`.

- [ ] **Step 1: Write the failing test**

Add to `scripts/tests/test_batch_control_botruns.py` (find the existing `class` that groups confirm
tests — likely near other `AppRuns.confirm` tests — and add a sibling test class if none of the
existing ones fit):

```python
class TestConfirmAfterADroppedBatchJob(unittest.TestCase):
    """§5.10: dropping a job from the draft's basket after Phase A already ran
    for it must shrink what gets rented, not silently rent the stale,
    already-on-disk manifest via the resume branch."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.batch_dir = self.root / "batch"
        self.staging = self.batch_dir / "tg-staging" / "app"
        self.staging.mkdir(parents=True)
        self.chat_id = 999
        bot._PHASE_A_OFFERED.clear()
        self.addCleanup(bot._PHASE_A_OFFERED.clear)

    def _job(self, name: str) -> Job:
        path = self.staging / name
        path.write_bytes(b"x")
        probe = Probe(kind="image", duration_s=None, width=10, height=10)
        return Job(pipeline="tryon-motion-enhance",
                   slots={"character": path, "outfit": path, "driver": path},
                   probes={"character": probe, "outfit": probe, "driver": probe})

    def test_a_dropped_job_is_not_rented(self):
        store = drafts.DraftStore(self.batch_dir, self.batch_dir / "tg-staging", "app",
                                  default_pipeline="tryon-motion-enhance", default_provider="gemini",
                                  probe=lambda p: Probe(kind="image", duration_s=None, width=10, height=10))
        # Compose two jobs distinguishable by their `outfit` file name and add both to the basket.
        # (Full setup mirrors how existing DraftStore tests build a runnable, validated draft —
        # see test_batch_control_botdrafts.py for the exact PATCH/add-to-batch/validate sequence
        # this test must reuse rather than reinvent.)
        ...
        idem = IdempotencyStore(self.batch_dir / "idempotency")
        app_runs = bot.AppRuns(bot.Tg(...), self.chat_id, store, idem)
        # Phase A runs for both jobs (stub start_phase_a so no subprocess launches).
        with mock.patch("tgbot.bot.start_phase_a"):
            status, body = app_runs.phase_a("k1")
        self.assertEqual(status, 202)
        # Manually mark both jobs' try-on stage done in the journal, as a real Phase A run would.
        manifest = load_manifest(bot._job_manifest_path(self.chat_id))
        state_file = state_path_for(manifest.path)
        state = {"version": 1, "batch": "2026-09-22-0000", "runs": {}}
        for run in manifest.runs:
            state["runs"][run.id] = {"status": "done", "stages": {
                "tryon": {"status": "done", "file": str(self.root / f"{run.id}.png"),
                          "params_manifest": {}}}}
            (self.root / f"{run.id}.png").write_bytes(b"img")
        save_state(state_file, state)
        bot._PHASE_A_OFFERED[self.chat_id] = bot._run_token(self.chat_id)
        # Drop the second basket entry.
        view = store.view()
        store.drop_from_batch(view["batch"][1]["digest"])
        with mock.patch("tgbot.bot.start_drain") as fake_start_drain:
            fake_start_drain.return_value = None
            status, body = app_runs.confirm(
                app_runs.run_id, {"provider": "runpod", "panel_token": app_runs.panel_token()},
                "k2")
        # Rewritten manifest has one run, not two — the drop took effect.
        self.assertEqual(len(load_manifest(bot._job_manifest_path(self.chat_id)).runs), 1)
```

The exact draft-composition lines (marked `...`) must be written against
`scripts/tests/test_batch_control_botdrafts.py`'s established fixture pattern (`PATCH` via
`store.patch({...})` then `store.add_to_batch()`) — copy that pattern, do not invent a new one; two
distinct `outfit` slot files are what make the two basket entries distinct jobs.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest scripts.tests.test_batch_control_botruns.TestConfirmAfterADroppedBatchJob -v`
Expected: FAIL — `fake_start_drain` is called with the two-run manifest still on disk (or the resume
path calls `start_drain` with the stale full manifest instead of the shrunk one), because
`AppRuns.confirm` still takes the resume branch unconditionally.

- [ ] **Step 3: Implement the fix**

In `scripts/tgbot/bot.py`, inside `class AppRuns`, add a new method right after `_draft_jobs`:

```python
    def _phase_a_matches_draft(self) -> bool:
        """True when the manifest Phase A already ran for is exactly the app's
        current draft. False when the draft changed since — a job dropped via
        DELETE /v1/draft/batch/{digest}, or added — in which case `confirm`
        must not take the resume branch below: `_do_resume` re-rents the
        manifest already on disk, which still names the dropped job. False
        routes to the `else` branch instead, which already handles this
        correctly: `_do_confirm` re-writes the manifest for the CURRENT
        draft and offers its own reuse/rerun chooser for any job whose
        try-on is still journalled `done` under its stable, content-hashed
        run id (§5.10, slice 6).
        """
        jobs, validated, _ = self.drafts.runnable()
        if validated is not True:
            return False
        try:
            manifest = load_manifest(_job_manifest_path(self.chat_id))
        except (ManifestError, OSError):
            return False
        return set(_unique_ids(jobs)) == {run.id for run in manifest.runs}
```

Then change the branch condition in `confirm` from:

```python
            elif _PHASE_A_OFFERED.get(self.chat_id) == _run_token(self.chat_id):
```

to:

```python
            elif (_PHASE_A_OFFERED.get(self.chat_id) == _run_token(self.chat_id)
                    and self._phase_a_matches_draft()):
```

No other line in `confirm` changes. When the condition is now False (draft changed), execution falls
through to the existing `else` branch unchanged — it already calls `_do_confirm(jobs=jobs, ...)` with
the fresh, correct draft.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest scripts.tests.test_batch_control_botruns.TestConfirmAfterADroppedBatchJob -v`
Expected: PASS.

- [ ] **Step 5: Run the full control-plane and bot suites**

Run: `make batch-test`
Expected: all green, including `test_batch_bot.py` unchanged.

- [ ] **Step 6: Commit**

```bash
git add scripts/tgbot/bot.py scripts/tests/test_batch_control_botruns.py
git commit -m "fix(api): confirm re-derives the manifest when the draft shrank after Phase A

AppRuns.confirm took the resume branch whenever _PHASE_A_OFFERED matched
the manifest's token, keyed on the manifest's identity, not the draft's.
A job dropped from the basket after Phase A ran (DELETE
/v1/draft/batch/{digest}) was invisible to that check, so _do_resume
re-rented the stale, already-on-disk manifest that still named it.

AppRuns._phase_a_matches_draft compares the draft's current run ids
against the manifest's; a mismatch now falls through to the existing
else branch, which _do_confirm already handles correctly — it re-writes
the manifest for the current draft and offers its own reuse/rerun
chooser for any job whose try-on is still journalled done."
```

---

### Task 2: The try-on library — new module and HTTP routes

**Files:**
- Create: `scripts/control/tryon_library.py`
- Modify: `scripts/httpapi/server.py` (`make_server`, `_route`)
- Modify: `scripts/tgbot/bot.py` (`class AppRuns` — one small read-only helper; `_start_control_api`
  is untouched, `tryon_library` is built in `make_server`, not per-chat)
- Test: `scripts/tests/test_batch_control_tryon_library.py` (new, pure module tests),
  `scripts/tests/test_batch_control_http.py` (route wiring)

**Interfaces:**
- Consumes: `control.LOCK` (existing, `scripts/control/__init__.py`), `control.paths.safe_child`
  (existing), `AppRuns.tryon_image(run_id, index) -> Path | None` (existing, raises `ApiError` on a
  lock timeout), `AppRuns._tryon_entries() -> list[tuple[str, Run, str, dict]]` (existing, private —
  read but do not modify).
- Produces: `class TryonLibrary` with `list() -> list[dict]`, `save(*, image: Path, material_ids: dict,
  provider: str) -> dict`, `resolve_image(entry_id: str) -> Path | None`, `delete(entry_id: str) ->
  None` (raises `TryonLibraryError(code, message)` — `not_found` only). `server.tryon_library`
  (attribute on the `_Server` instance). `AppRuns.tryon_save_info(self, index: str) -> tuple[Path,
  dict, str] | None` — `(image_path, material_ids, provider)` for the current try-on preview at
  `index`, or `None` if there is none. Task 3 consumes `TryonLibrary.resolve_image`.

- [ ] **Step 1: Write the failing test for the module**

Create `scripts/tests/test_batch_control_tryon_library.py`:

```python
import tempfile
import unittest
from pathlib import Path

from control.tryon_library import TryonLibrary, TryonLibraryError


class TestTryonLibrary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.lib = TryonLibrary(Path(self.tmp.name) / "tryon-library", owner="app")

    def _seed_image(self) -> Path:
        src = Path(self.tmp.name) / "source.png"
        src.write_bytes(b"fake-png-bytes")
        return src

    def test_empty_library_lists_nothing(self):
        self.assertEqual(self.lib.list(), [])

    def test_save_copies_the_image_and_records_material_ids(self):
        src = self._seed_image()
        record = self.lib.save(image=src, material_ids={"character": "app/c.png",
                                                         "outfit": "app/o.png"},
                               provider="gemini")
        self.assertIn("id", record)
        self.assertEqual(record["material_ids"], {"character": "app/c.png", "outfit": "app/o.png"})
        self.assertEqual(record["provider"], "gemini")
        listed = self.lib.list()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["id"], record["id"])
        # The source is COPIED, not moved or aliased — deleting it must not touch the library.
        src.unlink()
        image = self.lib.resolve_image(record["id"])
        self.assertIsNotNone(image)
        self.assertEqual(image.read_bytes(), b"fake-png-bytes")

    def test_resolve_unknown_id_is_none_not_a_crash(self):
        self.assertIsNone(self.lib.resolve_image("nope"))

    def test_delete_removes_the_entry_and_its_image(self):
        record = self.lib.save(image=self._seed_image(),
                               material_ids={"character": "app/c.png"}, provider="gemini")
        self.lib.delete(record["id"])
        self.assertEqual(self.lib.list(), [])
        self.assertIsNone(self.lib.resolve_image(record["id"]))

    def test_delete_unknown_id_raises_not_found(self):
        with self.assertRaises(TryonLibraryError) as ctx:
            self.lib.delete("nope")
        self.assertEqual(ctx.exception.code, "not_found")

    def test_two_owners_never_see_each_others_entries(self):
        other = TryonLibrary(Path(self.tmp.name) / "tryon-library", owner="tg-1")
        self.lib.save(image=self._seed_image(), material_ids={}, provider="gemini")
        self.assertEqual(other.list(), [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest scripts.tests.test_batch_control_tryon_library -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'control.tryon_library'`.

- [ ] **Step 3: Write the module**

Create `scripts/control/tryon_library.py`:

```python
"""Try-on images that outlive one draft/manifest (spec §5.10, slice 6).

Opt-in: an entry exists only because POST /v1/tryon-library was called —
never written automatically on every Phase A preview. Storage is
deliberately outside out/, so batch-clean and _final pruning can never
remove a saved entry. Shaped like control/drafts.py's DraftStore: one JSON
index per owner, read and written fresh on every call under control.LOCK
rather than cached in memory.
"""
from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path

import control
from control.paths import safe_child


class TryonLibraryError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class TryonLibrary:
    def __init__(self, library_dir: Path, owner: str):
        self.library_dir, self.owner = library_dir, owner
        self._index_path = library_dir / f"{owner}.json"
        self._image_dir = library_dir / owner

    def _load(self) -> list[dict]:
        try:
            data = json.loads(self._index_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError, OSError):
            return []
        return data if isinstance(data, list) else []

    def _save(self, entries: list[dict]) -> None:
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._index_path.with_name(f"{self._index_path.name}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(entries, indent=2), encoding="utf-8")
        tmp.replace(self._index_path)

    def list(self) -> list[dict]:
        with control.LOCK:
            # public copies only — callers never see the internal image_name field
            return [{k: v for k, v in e.items() if k != "image_name"} for e in self._load()]

    def save(self, *, image: Path, material_ids: dict, provider: str) -> dict:
        entry_id = uuid.uuid4().hex[:12]
        with control.LOCK:
            self._image_dir.mkdir(parents=True, exist_ok=True)
            dest = self._image_dir / f"{entry_id}{image.suffix or '.png'}"
            shutil.copy2(image, dest)
            record = {"id": entry_id, "owner": self.owner, "material_ids": dict(material_ids),
                      "provider": provider, "saved_at": time.time(), "image_name": dest.name}
            entries = self._load()
            entries.append(record)
            self._save(entries)
        return {k: v for k, v in record.items() if k != "image_name"}

    def resolve_image(self, entry_id: str) -> Path | None:
        with control.LOCK:
            record = next((e for e in self._load() if e.get("id") == entry_id), None)
        if record is None:
            return None
        path = safe_child(self._image_dir, str(record.get("image_name") or ""))
        if path is None or not path.is_file():
            return None
        return path

    def delete(self, entry_id: str) -> None:
        with control.LOCK:
            entries = self._load()
            record = next((e for e in entries if e.get("id") == entry_id), None)
            if record is None:
                raise TryonLibraryError("not_found", "no such try-on library entry")
            self._save([e for e in entries if e.get("id") != entry_id])
            image_name = str(record.get("image_name") or "")
            if image_name:
                path = safe_child(self._image_dir, image_name)
                if path is not None:
                    path.unlink(missing_ok=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest scripts.tests.test_batch_control_tryon_library -v`
Expected: PASS.

- [ ] **Step 5: Wire it into the server — write the failing HTTP test**

Add to `scripts/tests/test_batch_control_http.py` (follow the file's existing pattern: a live
`ThreadingHTTPServer` on an ephemeral port, `FakeAppRuns` already defined there for `_app_runs()`):

```python
    def test_tryon_library_round_trip(self):
        # Seed one preview the fake AppRuns can hand back.
        image = self.batch_dir / "preview.png"
        image.write_bytes(b"preview-bytes")
        self.fake_app_runs.tryon_save_info_result = (image, {"character": "app/c.png"}, "gemini")
        status, body = self._post("/v1/tryon-library", {"run_id": "tg-1", "index": "0"})
        self.assertEqual(status, 200)
        entry_id = body["id"]
        status, listed = self._get("/v1/tryon-library")
        self.assertEqual(status, 200)
        self.assertEqual([e["id"] for e in listed], [entry_id])
        status, _ = self._get(f"/v1/tryon-library/{entry_id}/image")
        self.assertEqual(status, 200)
        status, _ = self._delete(f"/v1/tryon-library/{entry_id}")
        self.assertEqual(status, 200)
        status, listed = self._get("/v1/tryon-library")
        self.assertEqual(listed, [])

    def test_tryon_library_save_with_no_such_preview_is_404(self):
        self.fake_app_runs.tryon_save_info_result = None
        status, _ = self._post("/v1/tryon-library", {"run_id": "tg-1", "index": "0"})
        self.assertEqual(status, 404)

    def test_tryon_library_image_of_unknown_id_is_404(self):
        status, _ = self._get("/v1/tryon-library/nope/image")
        self.assertEqual(status, 404)
```

Add `self.fake_app_runs.tryon_save_info_result = None` to `FakeAppRuns.__init__` and a
`tryon_save_info(self, index)` method returning it, next to that fake's existing `tryon_image`
stand-in — match whatever shape `FakeAppRuns` already uses for `run_id`/`index` arguments there.

- [ ] **Step 6: Run test to verify it fails**

Run: `python3 -m unittest scripts.tests.test_batch_control_http -v`
Expected: FAIL — `404 Not Found` for `/v1/tryon-library` (no route yet).

- [ ] **Step 7: Wire the routes**

In `scripts/tgbot/bot.py`, inside `class AppRuns`, add (after `tryon_image`):

```python
    def tryon_save_info(self, index: str) -> tuple[Path, dict, str] | None:
        """(image_path, material_ids, provider) for the current try-on
        preview at `index`, for the try-on library to save — or `None` when
        there is no such preview. Reuses `tryon_image`'s own resolution so
        the two routes can never disagree about which file "the current
        preview at this index" means (§5.10)."""
        image = self.tryon_image(self.run_id, index)
        if image is None:
            return None
        entry = next((e for i, _r, _s, e in self._tryon_entries() if i == index), None)
        if entry is None:
            return None
        run = next((r for i, r, _s, _e in self._tryon_entries() if i == index), None)
        material_ids = {role: f"app/{path.name}" for role, path in (run.inputs.items() if run else [])
                        if role != "driver"}
        provider = str(entry.get("params_manifest", {}).get("provider") or "gemini")
        return image, material_ids, provider
```

In `scripts/httpapi/server.py`, add to the imports:

```python
import control.tryon_library as tryon_library
```

Add to `make_server`:

```python
    server.tryon_library = tryon_library.TryonLibrary(
        batch_dir / "tryon-library", materials.APP_OWNER)
```

Add to `_route`, alongside the other `GET`/`POST`/`DELETE` blocks:

```python
        if method == "GET" and rest == ["tryon-library"]:
            return self._send_json(200, {"entries": s.tryon_library.list()})
        if method == "POST" and rest == ["tryon-library"]:
            body = self._read_json()
            run_id, index = str(body.get("run_id") or ""), str(body.get("index") or "")
            info = self._app_runs().tryon_save_info(index) if run_id == self._app_runs().run_id else None
            if info is None:
                raise NOT_FOUND
            image, material_ids, provider = info
            record = s.tryon_library.save(image=image, material_ids=material_ids, provider=provider)
            return self._send_json(200, record)
        if method == "GET" and len(rest) == 3 and rest[0] == "tryon-library" and rest[2] == "image":
            image = s.tryon_library.resolve_image(rest[1])
            if image is None:
                raise NOT_FOUND
            try:
                self._settle_body()
                return send_file(self, image)
            except FileNotFoundError:
                raise NOT_FOUND
        if method == "DELETE" and len(rest) == 2 and rest[0] == "tryon-library":
            try:
                s.tryon_library.delete(rest[1])
            except tryon_library.TryonLibraryError as exc:
                raise ApiError(404 if exc.code == "not_found" else 400, exc.code, exc.message)
            return self._send_json(200, {"ok": True})
```

Place these before the final `raise NOT_FOUND` of `_route`'s materials/drafts section (after the
`GET /v1/pipelines` block is a reasonable spot — group with the other per-owner, non-run routes).

- [ ] **Step 8: Run test to verify it passes**

Run: `python3 -m unittest scripts.tests.test_batch_control_http -v`
Expected: PASS.

- [ ] **Step 9: Run the full suite**

Run: `make batch-test`
Expected: all green.

- [ ] **Step 10: Commit**

```bash
git add scripts/control/tryon_library.py scripts/httpapi/server.py scripts/tgbot/bot.py \
        scripts/tests/test_batch_control_tryon_library.py scripts/tests/test_batch_control_http.py
git commit -m "feat(api): try-on library (slice 6, §5.10) — save, list, read, delete

New control/tryon_library.py, shaped like drafts.py's DraftStore: one
JSON index per owner plus copied image files, under control.LOCK,
outside out/ so batch-clean can't touch it. Opt-in — an entry exists
only because POST /v1/tryon-library asked for it.

Routes: GET/POST /v1/tryon-library, GET /v1/tryon-library/{id}/image,
DELETE /v1/tryon-library/{id}. AppRuns.tryon_save_info reuses
tryon_image's own resolution so the two routes can never disagree about
which file 'the current preview at this index' means."
```

---

### Task 3: `tryon_seed` — seed a job's try-on stage from a library entry

**Files:**
- Modify: `scripts/batch-params.json` (register `seedImage` as a curated extra for `tryon`)
- Modify: `scripts/tgbot/job.py` (`Job` dataclass, `render_manifest`)
- Modify: `scripts/control/drafts.py` (`copy_job`, `signature`, `dump_jobs`, `load_jobs`, `DraftStore`)
- Modify: `scripts/httpapi/server.py` (`make_server`'s `DraftStore(...)` call)
- Modify: `scripts/batchlib/runner.py` (`run_local_phase`'s inner `_one`)
- Test: `scripts/tests/test_batch_runner.py` (the seed itself), `scripts/tests/test_batch_control_botdrafts.py`
  or wherever this repo's `DraftStore.patch` tests live (the `tryon_seed` patch key)

**Interfaces:**
- Consumes: `TryonLibrary.resolve_image(entry_id) -> Path | None` (Task 2).
- Produces: `Job.tryon_seed: Path | None` field; manifest stage param `seedImage` on the `tryon` stage;
  `DraftStore.__init__` gains a required `tryon_library: TryonLibrary` parameter.

- [ ] **Step 1: Register the param — write the failing check**

Run: `make check-batch-params`
Expected (before any change): passes today, with no knowledge of `seedImage` — this step is only to
capture the baseline. The real failing check comes in Step 5 (`make batch-validate` on a manifest
carrying `seedImage`).

Edit `scripts/batch-params.json`'s `"tryon"` block, adding one entry to `"extra"`:

```json
    "seedImage": {
      "why": "Phase A only — control/drafts.py's PATCH /v1/draft tryon_seed field. batchlib/runner.py's run_local_phase intercepts it before run_local_tryon is ever called, so linux.py and the real API never see it (spec §5.10, slice 6)."
    },
```

Run: `make check-batch-params` again — must still pass (a curated `extra` entry with no AST match is
exactly what `naturalNails`/`removeWristAccessories` already are).

- [ ] **Step 2: `Job` gains the field**

In `scripts/tgbot/job.py`, add the field to `Job` (after `provider`):

```python
@dataclass
class Job:
    slots: dict[str, Path]
    probes: dict[str, Probe]
    pipeline: str
    provider: str = DEFAULT_PROVIDER
    tryon_seed: Path | None = None
```

In `render_manifest` (same file), right after the existing provider stage-param block:

```python
        if tryon_stage and job.provider != DEFAULT_PROVIDER:
            stage_params.setdefault(tryon_stage, {})["provider"] = job.provider
        if tryon_stage and job.tryon_seed is not None:
            stage_params.setdefault(tryon_stage, {})["seedImage"] = str(job.tryon_seed)
```

- [ ] **Step 3: `control/drafts.py` carries the field through copy/signature/(de)serialise**

```python
def copy_job(job: Job) -> Job:
    return Job(pipeline=job.pipeline, slots=dict(job.slots),
               probes=dict(job.probes), provider=job.provider, tryon_seed=job.tryon_seed)


def signature(job: Job) -> tuple:
    return (job.pipeline, job.provider, str(job.tryon_seed) if job.tryon_seed else None,
            tuple(sorted((r, str(p)) for r, p in job.slots.items())))


def dump_jobs(jobs: list[Job]) -> list[dict]:
    return [{"pipeline": j.pipeline, "provider": j.provider,
             "slots": {r: str(v) for r, v in j.slots.items()},
             "probes": {r: asdict(pr) for r, pr in j.probes.items()},
             "tryon_seed": str(j.tryon_seed) if j.tryon_seed else None}
            for j in jobs]


def load_jobs(payload: list) -> list[Job]:
    return [Job(pipeline=entry["pipeline"], provider=entry.get("provider", DEFAULT_PROVIDER),
                slots={r: Path(v) for r, v in entry["slots"].items()},
                probes={r: Probe(**d) for r, d in entry["probes"].items()},
                tryon_seed=Path(entry["tryon_seed"]) if entry.get("tryon_seed") else None)
            for entry in payload]
```

`_material_key`/other digest-related helpers that read `signature()`'s tuple do not need changes — the
tuple grew one element, and every caller consumes it opaquely (equality/hash only).

- [ ] **Step 4: `DraftStore` accepts `tryon_seed` on patch**

In `scripts/control/drafts.py`, add the import and wire the constructor:

```python
from control.tryon_library import TryonLibrary
```

```python
_PATCH_KEYS = frozenset({"pipeline", "provider", "slots", "tryon_seed"})
```

```python
    def __init__(self, batch_dir: Path, staging_root: Path, owner: str, *,
                 default_pipeline: str, default_provider: str, tryon_library: TryonLibrary,
                 probe=ingest.probe):
        self.batch_dir, self.owner = batch_dir, owner
        self.staging_root = staging_root.resolve()
        self.default_pipeline, self.default_provider = default_pipeline, default_provider
        self.tryon_library = tryon_library
        self._probe = probe
        self.path = batch_dir / f"{owner}.draft.json"
```

In `patch`, after the existing `slots` type check and before the `with control.LOCK:` block, resolve a
`tryon_seed` id to a path (outside the lock, matching the existing "resolve/probe outside, apply
inside" shape):

```python
        tryon_seed = body.get("tryon_seed")
        if tryon_seed is not None and not isinstance(tryon_seed, str):
            raise DraftError("bad_request", "tryon_seed must be a string id or null")
        seed_path: Path | None = None
        if tryon_seed:
            seed_path = self.tryon_library.resolve_image(tryon_seed)
            if seed_path is None:
                raise DraftError("not_found", f"no such try-on library entry: {tryon_seed}")
```

Then, inside the `with control.LOCK:` block, after the existing slot-application loop and before
`view = self._changed(d)`:

```python
            if "tryon_seed" in body:
                d.job.tryon_seed = seed_path
```

- [ ] **Step 5: Wire `TryonLibrary` into `make_server` — write the failing validate test**

In `scripts/tests/test_batch_control_botdrafts.py` (or wherever `DraftStore(...)` is constructed for
tests — grep `drafts.DraftStore(` across `scripts/tests/`), every existing call site now needs a
`tryon_library=` keyword argument (it has no default — a store built without one is a bug the test
suite should catch, not silently default around). Add it as
`tryon_library=TryonLibrary(self.batch_dir / "tryon-library", "app")` (or the owner the test already
uses) to each.

Add one new test near the others exercising `patch`:

```python
    def test_tryon_seed_resolves_a_library_entry(self):
        lib = TryonLibrary(self.batch_dir / "tryon-library", "app")
        image = self.batch_dir / "seed.png"
        image.write_bytes(b"seed-bytes")
        record = lib.save(image=image, material_ids={}, provider="gemini")
        store = drafts.DraftStore(self.batch_dir, self.staging_root, "app",
                                  default_pipeline="tryon-motion-enhance",
                                  default_provider="gemini", tryon_library=lib,
                                  probe=lambda p: Probe(kind="image", duration_s=None,
                                                        width=10, height=10))
        # (compose a complete job the same way sibling patch tests do)
        view = store.patch({"tryon_seed": record["id"]})
        self.assertEqual(view["missing"], view["missing"])  # sanity — full assertion added by implementer
        d = store._load()
        self.assertIsNotNone(d.job.tryon_seed)

    def test_tryon_seed_of_unknown_id_is_not_found(self):
        lib = TryonLibrary(self.batch_dir / "tryon-library", "app")
        store = drafts.DraftStore(self.batch_dir, self.staging_root, "app",
                                  default_pipeline="tryon-motion-enhance",
                                  default_provider="gemini", tryon_library=lib)
        with self.assertRaises(drafts.DraftError) as ctx:
            store.patch({"tryon_seed": "nope"})
        self.assertEqual(ctx.exception.code, "not_found")
```

In `scripts/httpapi/server.py`'s `make_server`, add `tryon_library=server.tryon_library` to the
existing `drafts.DraftStore(...)` call — this means Task 2's `server.tryon_library =
tryon_library.TryonLibrary(...)` line must run *before* the `server.drafts = drafts.DraftStore(...)`
line; reorder if Task 2 left them the other way.

- [ ] **Step 6: Run tests to verify they fail, then pass**

Run: `make batch-test`
Expected: fails first (missing `tryon_library` argument / `not_found` not raised), then passes once
Steps 2-5 are in place.

- [ ] **Step 7: The runner intercepts `seedImage` — write the failing test**

Add to `scripts/tests/test_batch_runner.py`, inside `class TestRunLocalPhase`:

```python
    def test_seed_image_skips_the_provider_and_copies_the_file(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            seed = tmp / "seed.png"
            seed.write_bytes(b"seed-bytes")
            manifest_text = MANIFEST_TRYON_GEMINI.replace(
                "tryon: { provider: gemini }",
                f"tryon: {{ provider: gemini, seedImage: {seed} }}")
            manifest = load_manifest(_fixture_tryon(tmp, manifest_text))

            def must_not_be_called(*_a, **_k):
                raise AssertionError("run_local_tryon must not be called for a seeded job")

            with mock.patch("batchlib.runner.run_local_tryon", must_not_be_called):
                result = run_local_phase(settings=GEMINI_SETTINGS, manifest=manifest,
                                         out_root=tmp / "out", batch_id="2026-09-22-0000",
                                         resume=False, log=lambda _m: None)
            self.assertEqual(result.done, ["runA"])
            stage = result.state["runs"]["runA"]["stages"]["tryon"]
            self.assertEqual(stage["status"], "done")
            self.assertEqual(Path(stage["file"]).read_bytes(), b"seed-bytes")

    def test_seed_image_missing_file_is_a_run_error_not_a_silent_gemini_call(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            missing = tmp / "does-not-exist.png"
            manifest_text = MANIFEST_TRYON_GEMINI.replace(
                "tryon: { provider: gemini }",
                f"tryon: {{ provider: gemini, seedImage: {missing} }}")
            manifest = load_manifest(_fixture_tryon(tmp, manifest_text))

            def must_not_be_called(*_a, **_k):
                raise AssertionError("run_local_tryon must not be called")

            with mock.patch("batchlib.runner.run_local_tryon", must_not_be_called):
                result = run_local_phase(settings=GEMINI_SETTINGS, manifest=manifest,
                                         out_root=tmp / "out", batch_id="2026-09-22-0000",
                                         resume=False, log=lambda _m: None)
            self.assertEqual(result.done, [])
            self.assertIn("runA", result.failed)
```

- [ ] **Step 8: Run test to verify it fails**

Run: `python3 -m unittest scripts.tests.test_batch_runner.TestRunLocalPhase.test_seed_image_skips_the_provider_and_copies_the_file -v`
Expected: FAIL — `run_local_tryon` is called (the stub raises `AssertionError`), because `_one` does
not yet check `seedImage`.

- [ ] **Step 9: Implement the interception**

In `scripts/batchlib/runner.py`, inside `run_local_phase`'s inner `_one` function, replace:

```python
        try:
            elapsed, size = run_local_tryon(run, params, settings, dest)
        except JobError as exc:
            return _ghi_hong(exc)
        except Exception as exc:   # noqa: BLE001 — cố ý bắt rộng, xem dưới
            return _ghi_hong(exc)
```

with:

```python
        seed_image = params.get("seedImage")
        if seed_image:
            # §5.10, slice 6: control/drafts.py's tryon_seed already resolved this
            # path through TryonLibrary.resolve_image before it reached the
            # manifest, but the manifest is the only channel that survives from
            # "the app composed this job" to "the runner is about to try it" —
            # so this is where a vanished file is caught, not there.
            seed_path = Path(str(seed_image))
            if not seed_path.is_file():
                return _ghi_hong(JobError(
                    f"run {run.id!r}: seedImage không phải file: {seed_path}"))
            shutil.copy2(seed_path, dest)
            elapsed, size = 0, dest.stat().st_size
        else:
            try:
                elapsed, size = run_local_tryon(run, params, settings, dest)
            except JobError as exc:
                return _ghi_hong(exc)
            except Exception as exc:   # noqa: BLE001 — cố ý bắt rộng, xem dưới
                return _ghi_hong(exc)
```

`shutil` is already imported at the top of `runner.py` (used by `run_one`'s `shutil.copy2` for the
`_final` hardlink fallback).

- [ ] **Step 10: Run test to verify it passes**

Run: `python3 -m unittest scripts.tests.test_batch_runner.TestRunLocalPhase -v`
Expected: PASS, including every pre-existing test in the class (no regression).

- [ ] **Step 11: Run the full suite**

Run: `make batch-test && make check-batch-params`
Expected: all green.

- [ ] **Step 12: Commit**

```bash
git add scripts/batch-params.json scripts/tgbot/job.py scripts/control/drafts.py \
        scripts/httpapi/server.py scripts/batchlib/runner.py \
        scripts/tests/test_batch_runner.py scripts/tests/test_batch_control_botdrafts.py
git commit -m "feat(api): seed a job's try-on stage from a saved library entry (§5.10)

PATCH /v1/draft {tryon_seed: id} resolves a try-on library entry to a
path and records it on the draft's Job. render_manifest writes it as
the tryon stage's seedImage param — the only channel that survives from
'the app composed this job' to 'the runner is about to try it', since
the bot never knows a job's batch_id/out_dir in advance.

run_local_phase's _one() intercepts seedImage before run_local_tryon is
ever called: copies the seed image to dest and journals the stage done,
the same shape a real Phase A result leaves, so a later resume's
local_tryon_reusable check treats it identically. A missing seed file
is a run error, not a silent fall-through to a real (and unwanted)
Gemini call.

seedImage is registered in batch-params.json as a curated extra for
tryon, the same way naturalNails/removeWristAccessories already are —
make batch-validate would otherwise reject it as an unknown param."
```

---

### Task 4: Guided regenerate and version history

**Files:**
- Modify: `scripts/batchlib/local_tryon.py` (new guidance clause constants + function)
- Modify: `scripts/tgbot/bot.py` (`_regen_tryon`, `AppRuns.regen`, `AppRuns` gains
  `tryon_version_image`)
- Modify: `scripts/httpapi/server.py` (`_route` — one new `GET` route)
- Test: `scripts/tests/test_batch_local_tryon.py` (the prompt clauses),
  `scripts/tests/test_batch_control_botruns.py` (guidance validation, version image route)

**Interfaces:**
- Consumes: `_flag(params, *keys) -> bool` (existing, `scripts/batchlib/local_tryon.py`),
  `_tryon_versions(image: Path) -> list[Path]` (existing, `scripts/tgbot/bot.py`).
- Produces: `tryon_guidance_prompts(params: dict) -> tuple[str, str]` in `local_tryon.py`.
  `_regen_tryon` gains a `guidance: list[str] | None = None` keyword parameter.
  `AppRuns.tryon_version_image(self, index: str, n: str) -> Path | None`.

- [ ] **Step 1: Write the failing test for the prompt clauses**

Add to `scripts/tests/test_batch_local_tryon.py`:

```python
    def test_keep_face_adds_the_reinforcing_clause(self):
        pos, neg = lt.tryon_guidance_prompts({"keepFace": "1"})
        self.assertIn("facial identity", pos.lower())
        self.assertEqual(neg, "")

    def test_tighter_crop_and_match_lighting_stack(self):
        pos, neg = lt.tryon_guidance_prompts({"tighterCrop": "1", "matchLighting": "1"})
        self.assertIn("crop", pos.lower())
        self.assertIn("lighting", pos.lower())

    def test_no_flags_is_empty(self):
        self.assertEqual(lt.tryon_guidance_prompts({}), ("", ""))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest scripts.tests.test_batch_local_tryon -v`
Expected: FAIL with `AttributeError: module 'batchlib.local_tryon' has no attribute 'tryon_guidance_prompts'`.

- [ ] **Step 3: Implement the clauses**

In `scripts/batchlib/local_tryon.py`, after the existing `_TRYON_WRIST_NEG` constant:

```python
# Guidance chips from the phone's Regenerate screen (§5.10, slice 6) — a
# closed vocabulary of exactly three flags, appended the same way the hands
# clauses above are. "Keep face" reinforces a guard gemini_tryon_prompt
# already applies unconditionally (the CRITICAL face-lock sentence); the
# other two are new instructions.
_TRYON_KEEP_FACE_POS = ("Preserve the person's exact facial identity, expression and skin tone with "
                        "no changes.")
_TRYON_TIGHTER_CROP_POS = ("Frame the result as a tighter, closer crop on the person and garment, "
                           "cropping in from the current framing.")
_TRYON_MATCH_LIGHTING_POS = ("Match the lighting, color temperature and shadow direction of the "
                             "garment to the lighting already present on the person in image 1.")


def tryon_guidance_prompts(params: dict) -> tuple[str, str]:
    """(positive, negative) guidance clauses for a regenerate's guidance chips;
    ("", "") when none are on. Same shape as tryon_hands_prompts, a sibling
    function rather than a rewrite of it, since the two flag families are
    independent (hands come from the draft's job params, guidance from one
    regenerate call)."""
    pos = []
    if _flag(params, "keepFace"):
        pos.append(_TRYON_KEEP_FACE_POS)
    if _flag(params, "tighterCrop"):
        pos.append(_TRYON_TIGHTER_CROP_POS)
    if _flag(params, "matchLighting"):
        pos.append(_TRYON_MATCH_LIGHTING_POS)
    return " ".join(pos), ""
```

At the call site around line 765 (`hands = tryon_hands_prompts(params)`), merge in guidance:

```python
    hands_pos, hands_neg = tryon_hands_prompts(params)
    guide_pos, guide_neg = tryon_guidance_prompts(params)
    hands = (" ".join(x for x in (hands_pos, guide_pos) if x),
             ", ".join(x for x in (hands_neg, guide_neg) if x))
```

(replacing the existing `hands = tryon_hands_prompts(params)` line; the two call sites further down
that pass `hands=hands` to `gemini_tryon_prompt`/`qwen_tryon_prompts` need no change — they already
consume whatever `hands` holds.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest scripts.tests.test_batch_local_tryon -v`
Expected: PASS.

- [ ] **Step 5: Thread `guidance` through `_regen_tryon` and validate it in `AppRuns.regen` — write the failing test**

Add to `scripts/tests/test_batch_control_botruns.py`:

```python
    def test_regen_with_unknown_guidance_is_400_and_calls_nothing(self):
        with mock.patch("tgbot.bot._regen_tryon") as fake_regen:
            status, body = self.app_runs.regen(
                self.app_runs.run_id, "0",
                {"run_token": "whatever", "guidance": ["not_a_real_flag"]}, "k1")
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "bad_request")
        fake_regen.assert_not_called()

    def test_regen_with_valid_guidance_reaches_regen_tryon(self):
        with mock.patch("tgbot.bot._regen_tryon") as fake_regen:
            fake_regen.return_value = Outcome(True, "regenerated")
            self.app_runs.regen(self.app_runs.run_id, "0",
                                {"run_token": "whatever",
                                 "guidance": ["keep_face", "tighter_crop"]}, "k2")
        _args, kwargs = fake_regen.call_args
        self.assertEqual(kwargs.get("guidance"), ["keep_face", "tighter_crop"])
```

(Adapt to whatever `self.app_runs` fixture this test file already provides for `AppRuns` — every
existing `AppRuns.regen`/`AppRuns.confirm` test in this file builds one; reuse that setup rather than
inventing a new one.)

- [ ] **Step 6: Run test to verify it fails**

Run: `python3 -m unittest scripts.tests.test_batch_control_botruns -v`
Expected: FAIL — `regen` has no `guidance` handling yet (either a `TypeError` from the unexpected
key, or the 400 assertion fails because nothing validates it).

- [ ] **Step 7: Implement**

In `scripts/tgbot/bot.py`, `_regen_tryon`'s signature gains one keyword parameter:

```python
def _regen_tryon(tg: Tg, chat_id: int, index: str, token: str, *,
                 dry_run: bool, guidance: list[str] | None = None) -> Outcome:
```

Thread `guidance` into the manifest's stage params the same shape as an existing extra flag. Find
where `_regen_tryon` currently computes the params passed to the re-run (search the function body for
where it drops the stage from the journal / seeds "every OTHER image already previewed" — the code
around `stages.pop(stage_name, None)`), and where the manifest's `Run.stage_params` for this run's
try-on stage get consulted. Add, right before that point:

```python
    _GUIDANCE_FLAGS = {"keep_face": "keepFace", "tighter_crop": "tighterCrop",
                       "match_lighting": "matchLighting"}
    if guidance:
        unknown = [g for g in guidance if g not in _GUIDANCE_FLAGS]
        if unknown:
            return _refuse(tg, chat_id, "bad_request",
                           f"unknown guidance value(s): {', '.join(unknown)} — only "
                           f"{', '.join(sorted(_GUIDANCE_FLAGS))} are accepted")
        run.stage_params.setdefault(stage_name, {}).update(
            {_GUIDANCE_FLAGS[g]: "1" for g in guidance})
```

placed after `run = manifest.runs[int(index)]` and `stage_name = _local_tryon_stage(run)` are both
already bound (so `run.stage_params` is the right run's), and before the manifest write that starts
the actual re-run subprocess — the guard must fire before anything is dropped from the journal or
spent.

In `class AppRuns`, `regen`'s body — validate the shape before `self.idem.begin`, matching how
`token` is already checked there:

```python
    def regen(self, run_id: str, index: str, body: dict, key) -> tuple[int, dict]:
        if run_id != self.run_id:
            return 404, _run_error("not_found", "no such run")
        token = body.get("run_token")
        if not token:
            return 400, _run_error("bad_request", "run_token is required")
        guidance = body.get("guidance")
        if guidance is not None:
            if not isinstance(guidance, list) or not all(isinstance(g, str) for g in guidance):
                return 400, _run_error("bad_request", "guidance must be a list of strings")
            unknown = [g for g in guidance if g not in ("keep_face", "tighter_crop", "match_lighting")]
            if unknown:
                return 400, _run_error("bad_request",
                                       f"unknown guidance value(s): {', '.join(unknown)}")
        replay = self.idem.begin("regen", key)
        if replay is not None:
            return replay
        with self._locked() as busy:
            if busy is not None:
                self.idem.forget("regen", key)
                return busy
            out = _regen_tryon(_AppTg(self.tg), self.chat_id, index, token, dry_run=False,
                               guidance=guidance)
            if out:
                response = (202, {"run_id": self.run_id, "outcome": out.code})
            else:
                response = (status_for(out), _run_error(out.code, out.message))
        self.idem.finish("regen", key, *response)
        return response
```

The `AppRuns.regen` 400-shape checks happen before `idem.begin`, matching how the existing
`run_token` check already does — a malformed request never touches the idempotency store.

- [ ] **Step 8: Run test to verify it passes**

Run: `python3 -m unittest scripts.tests.test_batch_control_botruns -v`
Expected: PASS.

- [ ] **Step 9: Version history route — write the failing test**

Add to `scripts/tests/test_batch_control_botruns.py`:

```python
    def test_version_image_returns_an_older_version(self):
        # (build a run whose try-on image has one .v1 backup on disk, the
        # same way TestTryonPreviews' existing fixtures do)
        ...
        path = self.app_runs.tryon_version_image("0", "1")
        self.assertIsNotNone(path)

    def test_version_image_out_of_range_is_none(self):
        self.assertIsNone(self.app_runs.tryon_version_image("0", "99"))
```

and to `scripts/tests/test_batch_control_http.py`:

```python
    def test_tryon_version_route_404s_for_an_unknown_version(self):
        self.fake_app_runs.tryon_version_image_result = None
        status, _ = self._get("/v1/runs/tg-1/tryon/0/versions/99")
        self.assertEqual(status, 404)
```

- [ ] **Step 10: Run test to verify it fails**

Run: `make batch-test`
Expected: FAIL — `AttributeError: 'AppRuns' object has no attribute 'tryon_version_image'`.

- [ ] **Step 11: Implement**

In `scripts/tgbot/bot.py`, `class AppRuns`, add after `tryon_image`:

```python
    def tryon_version_image(self, index: str, n: str) -> Path | None:
        """An earlier version of the try-on image at `index`, oldest = "1"
        (§5.10, slice 6). Same shape as tryon_image: `None` covers both "no
        such preview" and "n out of range", never an exception."""
        if not n.isdigit() or int(n) < 1:
            return None
        image = self.tryon_image(self.run_id, index)
        if image is None:
            return None
        versions = _tryon_versions(image)
        position = int(n) - 1
        if position >= len(versions):
            return None
        return versions[position]
```

In `scripts/httpapi/server.py`'s `_route`, add right after the existing
`GET /v1/runs/{id}/tryon/{index}` image route:

```python
        if (method == "GET" and len(rest) == 5 and rest[0] == "runs" and rest[2] == "tryon"
                and rest[4] == "versions"):
            image = self._app_runs().tryon_version_image(rest[3], rest[4 + 1])
```

Correct the index math: `rest` for `runs/{id}/tryon/{index}/versions/{n}` is
`["runs", "<id>", "tryon", "<index>", "versions", "<n>"]`, so `len(rest) == 6` and `rest[5]` is `n`:

```python
        if (method == "GET" and len(rest) == 6 and rest[0] == "runs" and rest[2] == "tryon"
                and rest[4] == "versions"):
            image = self._app_runs().tryon_version_image(rest[3], rest[5])
            if image is None:
                raise NOT_FOUND
            try:
                self._settle_body()
                return send_file(self, image)
            except FileNotFoundError:
                raise NOT_FOUND
```

Place it before the existing `POST .../tryon/{index}/regen` block (both match on `rest[2] == "tryon"`
with different lengths, so order between them does not matter, but keeping GETs grouped together
reads better).

- [ ] **Step 12: Run test to verify it passes**

Run: `make batch-test`
Expected: PASS.

- [ ] **Step 13: Commit**

```bash
git add scripts/batchlib/local_tryon.py scripts/tgbot/bot.py scripts/httpapi/server.py \
        scripts/tests/test_batch_local_tryon.py scripts/tests/test_batch_control_botruns.py \
        scripts/tests/test_batch_control_http.py
git commit -m "feat(api): guided regenerate and try-on version history (§5.10)

regen's body gains guidance: string[], a closed vocabulary of exactly
keep_face/tighter_crop/match_lighting, validated before the idempotency
store ever sees the key. Threaded into the manifest's tryon stage
params as keepFace/tighterCrop/matchLighting, read by a new sibling of
tryon_hands_prompts in local_tryon.py — keep_face reinforces the face
lock gemini_tryon_prompt already applies unconditionally; the other two
are new prompt clauses.

GET /v1/runs/{id}/tryon/{index}/versions/{n} exposes the version
history _regen_tryon already kept (_tryon_versions) but never served
over HTTP."
```

---

## Self-Review

**1. Spec coverage.** §5.10's four pieces each map to a task: the drop-before-rent fix → Task 1; the
try-on library (storage, routes) → Task 2; `tryon_seed` → Task 3; guided regenerate + version history →
Task 4. §4.1's new `tryon_library.py` row is Task 2. §5.2's `tryon_seed` patch field is Task 3. §5.3's
`guidance` on regen and the new versions route are Task 4. §7's three required new test cases map:
the drop-before-confirm case → Task 1's test; the `tryon_seed`-skips-the-provider case → Task 3's
runner test; the unknown-`guidance`-is-400 case → Task 4's test. Notify-on-stock is explicitly out of
scope for this plan (§5.10, §9) and no task touches it.

**2. Placeholder scan.** The two `...` markers (Task 1 Step 1's draft-composition lines, Task 3 Step 5's
sanity assertion) are the only ellipses in this document, both explicitly pointed at an existing
fixture pattern in a named sibling test file to copy rather than left as "figure it out" — acceptable
because the exact lines depend on that file's current fixture helpers, which the implementer reads
first. Every other step has literal, runnable code.

**3. Type consistency.** `TryonLibrary.resolve_image`/`save`/`list`/`delete` (Task 2) are used with the
same signatures in Task 3's `DraftStore.patch`. `Job.tryon_seed: Path | None` (Task 3) is the type
`render_manifest` and `DraftStore.patch` both read. `_regen_tryon`'s new `guidance: list[str] | None`
keyword (Task 4) matches what `AppRuns.regen` passes. `AppRuns.tryon_save_info`'s three-tuple return
(Task 2) matches what the `POST /v1/tryon-library` route unpacks.

**4. Ordering.** Task 2 must land before Task 3 (`DraftStore` needs `TryonLibrary` to exist). Tasks 1
and 4 are independent of both and of each other; execute in the written order for a linear branch
history, but a controller may run 1 and 4 in either position relative to 2/3 without conflict — they
touch none of the same functions.
