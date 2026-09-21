# Control-plane API, slice 3 (drafts: compose a job from the phone) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The phone can list pipelines, compose a job (pick a pipeline and provider, assign material to
each slot), collect several jobs into a batch, drop one, clear, and run the free `make batch-validate`
check — all against its own draft, separate from the Telegram chat's draft.

**Architecture:** The draft logic that has no Telegram in it (`_copy_job`, `_signature`, `_job_digest`,
`_jobs_for`, the slot-dropping half of `_switch_pipeline`, `_dump_jobs`/`_load_jobs`, `PROVIDER_LABELS`)
moves from `bot.py` into a new `scripts/control/drafts.py`; the bot keeps aliases/thin wrappers, so its
behaviour and tests do not change. `drafts.py` also gets a file-backed `DraftStore` for one owner
(`app`), guarded by the spec's `control.LOCK`, with slow work (ffprobe, `make batch-validate`) run
outside the lock. `scripts/httpapi/server.py` gains `PATCH` and the seven routes of spec §5.2.

**Tech Stack:** Python 3 stdlib only; `ffprobe` (already on the VPS); `make batch-validate`
(`scripts/batch_run.py --validate-only`, no pod); `unittest`.

**Spec:** `docs/superpowers/specs/2026-09-21-vps-control-plane-api-design.md` — slice 3 = §6 row 3;
contract §5.2; status codes §5.6; concurrency §4.3. Slice 2's plan
(`docs/superpowers/plans/2026-09-21-control-plane-slice-2.md`) shows `control/materials.py`,
`control/uploads.py` and the HTTP server this slice extends.

## Controller decisions (the spec leaves these open, or this slice narrows it)

- **The bot's in-memory draft state stays in `bot.py` this slice.** The user chose "shared runs,
  separate drafts": the app never reads or writes the Telegram chat's draft, so moving `_STATE`,
  `_BASKET`, `_PENDING`, `_LAST_VALIDATE` (which also carry Telegram-only panel state) into the core buys
  nothing the app can use and puts `test_batch_bot.py`'s 691 tests at risk. Only the pure helpers move;
  the bot calls them. Moving the bot's state under an owner key waits until something needs it
  (slice 4's confirm, if it does).
- **The app's draft is file-backed, not in memory:** `batch/app.draft.json`, read and written on every
  operation under `control.LOCK`. It is a few KB; reading it per request removes the bot's
  `_LOADED`/`_save_draft` envelope and its check-then-act races entirely, and it survives the
  auto-deploy restarts. Writes are tmp-file + `replace`, with a unique tmp name.
- **Exceptions, not return values, carry refusals** (`DraftError(code, message)`), the same pattern as
  slice 2's `MaterialError`/`UploadError` and the server's `_DOMAIN_STATUS` table. Spec §4.1's
  "refusals are return values" is about the Telegram adapter sending the same text; no Telegram
  adapter calls the app's store.
- **`control.LOCK` guards the app draft only.** The spec wants every bot tick wrapped too, but the bot
  touches no state the API touches in this slice, and wrapping `handle()` would block phone requests
  behind a 2 GB Telegram staging copy or a 120 s validate. Slice 4 (confirm, shared drain state) is
  where the bot starts taking the lock.
- **Slots take a role and a material id**, e.g. `{"slots": {"character": "app/me.png"}}`; `null`
  empties a slot. The app picks the role, so the bot's "which slot is this image?" queue (`_PENDING`)
  does not exist for the app. Role must belong to the (new) pipeline; `driver` must be a video and
  every other role an image (`slot_for`'s rule, `job.py:64`). Every file is probed with
  `ingest.probe` outside the lock before anything changes; a PATCH either applies entirely or not at
  all. Any owner's material may be used (materials are global, spec §5.1).
- **A slot whose file has vanished** (Telegram's `/clear` removes its own staging dir) stays in the
  draft with `"exists": false` and counts as missing. The draft never silently rewrites itself.
- **`clear` resets the app's draft only; it deletes no material.** Material has its own delete route;
  the bot's `/clear` deletes its chat's staging dir because Telegram has no other way to.
- **A generation counter** increments on every mutation. `validate` renders the manifest under the
  lock, runs `make batch-validate` outside it, and records the verdict only if the generation is
  unchanged — otherwise the answer says `"stale": true` and the draft's verdict stays unknown.
- **Validation manifests are throwaway** files in `batch/.validate/<owner>-<uuid>.yaml`, deleted after
  the run. A subdirectory, so `materials._in_use`'s `batch/*.yaml` scan and `runs.list_runs` never see
  them. Slice 4 renders the real run manifest when Phase A starts, as the bot does.
- **Validate timeout is 90 s, not the bot's 120 s:** Cloudflare answers `524` after 100 s without a
  response. The observed runtime is ~1 s (bot.py's comment: 120 s is "~100x the observed runtime").
- **Validation output is returned with the repo root stripped**, because error lines name staged
  files by absolute path and the API never exposes absolute paths.
- **Deleting app material referenced by the app draft is `409 in_use`** (slice 2's plan deferred this
  check here): `materials._in_use` also scans `batch/app.draft.json` with the same whole-path regex.
- **Default pipeline and provider** for a new app draft are the bot's `_DEFAULT_PIPELINE` /
  `_DEFAULT_PROVIDER` (so `TG_PIPELINE`/`TG_PROVIDER` in `.env` apply to both), passed into
  `make_server`.

## Global Constraints

- No new Python dependency (spec §4.2).
- Every route under `/v1`; errors `{"error": {"code": "...", "message": "..."}}` (spec §5).
- Status codes: `400` malformed body; `401` bearer; `404` unknown material id or batch digest;
  `409` material in use; `422` draft refusal — unknown pipeline/role/provider, wrong kind, provider not
  applicable, missing slots, duplicate job, nothing to validate, validation failed, unprobeable
  (spec §5.6); `500` logged and opaque.
- The API never exposes absolute paths — not in draft views, not in validation output.
- No field in `GET /v1/draft` or `GET /v1/pipelines` is derived from the current time (they carry
  `ETag`s the phone polls with, spec §5.3's rule).
- Slow work (ffprobe, `make batch-validate`) runs outside `control.LOCK` (spec §4.3).
- After any error response to a request that carries a body (POST/PUT/PATCH/DELETE), the connection
  is closed.
- `scripts/control/` imports nothing from Telegram; it may import `tgbot.job`, `tgbot.ingest`,
  `tgbot.run` (all Telegram-free) and `batchlib.*`.
- `scripts/tests/test_batch_bot.py` stays green **unchanged** after every task.
- New test files are `scripts/tests/test_batch_control_*.py`; run with
  `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_*.py'` and the full
  `make batch-test` before each commit.
- `motions-studio/setup/scrub-secrets.sh --check` exits 0 before every commit.
- English for code, comments, docs, commit messages; explain *why* with numbers where a reader would
  wonder. No `#region ALD` markers.
- Commit messages end with the trailer `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

---

### Task 1: Move the pure draft helpers into `control/drafts.py`

**Files:**
- Create: `scripts/control/drafts.py`
- Modify: `scripts/control/__init__.py` (add `LOCK`)
- Modify: `scripts/tgbot/bot.py` — `PROVIDER_LABELS` (~line 137), `_switch_pipeline` (~726),
  `_copy_job`/`_signature`/`_job_digest` (~2483-2516), `_jobs_for` (~2604), `_dump_jobs`/`_load_jobs`
  (~3810-3827)
- Test: `scripts/tests/test_batch_control_drafts.py` (new)

**Interfaces:**
- Produces (used by Tasks 2-4):
  - `control.LOCK: threading.RLock`
  - `drafts.PROVIDER_LABELS: dict[str, str]` (moved verbatim)
  - `drafts.copy_job(job: Job) -> Job`
  - `drafts.signature(job: Job) -> tuple`
  - `drafts.job_digest(job: Job) -> str` (10 hex chars)
  - `drafts.jobs_for(current: Job | None, basket: list[Job]) -> list[Job]`
  - `drafts.drop_unusable(job: Job, pipeline: str) -> list[str]` — sets `job.pipeline`, removes slots
    and probes the new pipeline cannot use, returns the dropped roles sorted
  - `drafts.dump_jobs(jobs: list[Job]) -> list[dict]`, `drafts.load_jobs(payload: list) -> list[Job]`
  - `drafts.role_kind(role: str) -> str` — `"video"` for `driver`, else `"image"`
  - `drafts.pipeline_catalog() -> list[dict]`

- [ ] **Step 1: Write the failing tests**

Create `scripts/tests/test_batch_control_drafts.py`:

```python
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import control
from control import drafts
from tgbot.ingest import Probe
from tgbot.job import Job

IMG = Probe(kind="image", width=1080, height=1920, duration_s=0.0, bitrate_kbps=0, size_bytes=10)
VID = Probe(kind="video", width=1080, height=1920, duration_s=12.0, bitrate_kbps=9000, size_bytes=10)


def job(pipeline="tryon-motion-enhance", provider="gemini", **slots):
    return Job(slots={r: Path(p) for r, p in slots.items()},
               probes={r: (VID if r == "driver" else IMG) for r in slots},
               pipeline=pipeline, provider=provider)


class TestPureHelpers(unittest.TestCase):
    def test_lock_is_reentrant(self):
        with control.LOCK:
            with control.LOCK:
                pass

    def test_copy_is_detached(self):
        a = job(character="/s/a.png")
        b = drafts.copy_job(a)
        b.slots["outfit"] = Path("/s/o.png")
        self.assertNotIn("outfit", a.slots)
        self.assertEqual(drafts.signature(a), drafts.signature(job(character="/s/a.png")))

    def test_digest_is_stable_and_material_keyed(self):
        a = job(character="/s/a.png")
        self.assertEqual(drafts.job_digest(a), drafts.job_digest(drafts.copy_job(a)))
        self.assertEqual(len(drafts.job_digest(a)), 10)
        self.assertNotEqual(drafts.job_digest(a), drafts.job_digest(job(character="/s/b.png")))

    def test_jobs_for_appends_a_complete_current_job_once(self):
        full = job(character="/s/c.png", outfit="/s/o.png", driver="/s/d.mp4")
        self.assertEqual(drafts.jobs_for(None, []), [])
        self.assertEqual(drafts.jobs_for(job(character="/s/c.png"), []), [])
        self.assertEqual(len(drafts.jobs_for(full, [])), 1)
        self.assertEqual(len(drafts.jobs_for(full, [drafts.copy_job(full)])), 1)

    def test_drop_unusable(self):
        j = job(character="/s/c.png", outfit="/s/o.png", driver="/s/d.mp4")
        dropped = drafts.drop_unusable(j, "motion-enhance")
        self.assertEqual(dropped, ["outfit"])
        self.assertEqual(j.pipeline, "motion-enhance")
        self.assertEqual(set(j.slots), {"character", "driver"})
        self.assertEqual(set(j.probes), {"character", "driver"})

    def test_dump_load_round_trip(self):
        jobs = [job(character="/s/c.png", driver="/s/d.mp4", pipeline="motion-enhance")]
        back = drafts.load_jobs(drafts.dump_jobs(jobs))
        self.assertEqual([drafts.signature(j) for j in back], [drafts.signature(j) for j in jobs])
        self.assertEqual(back[0].probes["driver"], VID)

    def test_role_kind(self):
        self.assertEqual(drafts.role_kind("driver"), "video")
        self.assertEqual(drafts.role_kind("character"), "image")

    def test_catalog(self):
        cat = {p["id"]: p for p in drafts.pipeline_catalog()}
        tme = cat["tryon-motion-enhance"]
        self.assertEqual(tme["stages"], ["tryon", "motion", "enhance"])
        self.assertEqual(tme["required"], ["character", "driver", "outfit"])
        self.assertEqual(tme["optional"], ["background"])
        self.assertEqual(tme["roles"]["driver"], "video")
        self.assertEqual(tme["roles"]["outfit"], "image")
        self.assertEqual({p["id"] for p in tme["providers"]}, set(drafts.PROVIDER_LABELS))
        self.assertEqual(cat["motion-enhance"]["providers"], [])
        self.assertEqual([p["id"] for p in drafts.pipeline_catalog()],
                         sorted(p["id"] for p in drafts.pipeline_catalog()))


class TestBotUsesTheMovedHelpers(unittest.TestCase):
    def test_aliases(self):
        import tgbot.bot as bot
        self.assertIs(bot.PROVIDER_LABELS, drafts.PROVIDER_LABELS)
        self.assertIs(bot._copy_job, drafts.copy_job)
        self.assertIs(bot._signature, drafts.signature)
        self.assertIs(bot._job_digest, drafts.job_digest)
        self.assertIs(bot._dump_jobs, drafts.dump_jobs)
        self.assertIs(bot._load_jobs, drafts.load_jobs)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_drafts.py'`
Expected: FAIL — `ImportError: cannot import name 'drafts'` (and `control.LOCK` missing).

- [ ] **Step 3: Implement**

Append to `scripts/control/__init__.py`:

```python
import threading

# Spec §4.3: the bot was single-threaded, the HTTP API is not. One re-entrant
# lock wraps every read-modify-write of shared control-plane state. Slow work
# (ffprobe, make batch-validate, file copies) must run outside it.
LOCK = threading.RLock()
```

Create `scripts/control/drafts.py`. Move `PROVIDER_LABELS` **verbatim, with its comment block**
(bot.py ~lines 125-142) into it; move the bodies of `_copy_job`, `_signature`, `_job_digest`,
`_dump_jobs`, `_load_jobs` **verbatim, with their docstrings**, renamed without the underscore:

```python
"""Composing jobs: the draft logic shared by the Telegram bot and the HTTP API.

Pure helpers moved out of bot.py (spec 2026-09-21 §6, slice 3). The bot keeps
its own in-memory drafts and calls these; the phone's draft lives in
DraftStore below.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict
from pathlib import Path

from batchlib.pipelines import PIPELINES, optional_roles, required_roles
from tgbot.ingest import Probe
from tgbot.job import DEFAULT_PROVIDER, Job, _tryon_stage, missing_slots

# <PROVIDER_LABELS and its comment, moved verbatim from bot.py>

# Structural, not a question (job.py's slot_for): a video can only be the driver.
VIDEO_ROLES = frozenset({"driver"})


def copy_job(job: Job) -> Job:
    # <docstring and body of bot._copy_job, verbatim>


def signature(job: Job) -> tuple:
    # <docstring and body of bot._signature, verbatim>


def job_digest(job: Job) -> str:
    # <docstring and body of bot._job_digest, verbatim; it calls signature(job)>


def jobs_for(current: Job | None, basket: list[Job]) -> list[Job]:
    """What Run would submit: the batch, plus the job being edited when it is
    complete and not already an exact copy of a batch entry."""
    jobs = list(basket)
    if current is None or missing_slots(current):
        return jobs
    if any(signature(current) == signature(other) for other in jobs):
        return jobs
    return jobs + [current]


def drop_unusable(job: Job, pipeline: str) -> list[str]:
    """Switch `job` to `pipeline`, dropping the slots it cannot use.

    Slots and probes are dropped together: every renderer assumes
    set(job.probes) == set(job.slots). The caller validates the name.
    """
    usable = required_roles(pipeline) | optional_roles(pipeline)
    dropped = sorted(set(job.slots) - usable)
    for role in dropped:
        job.slots.pop(role, None)
        job.probes.pop(role, None)
    job.pipeline = pipeline
    return dropped


def dump_jobs(jobs: list[Job]) -> list[dict]:
    # <body of bot._dump_jobs, verbatim>


def load_jobs(payload: list) -> list[Job]:
    # <body of bot._load_jobs, verbatim — it uses job.py's DEFAULT_PROVIDER on purpose>


def role_kind(role: str) -> str:
    return "video" if role in VIDEO_ROLES else "image"


def pipeline_catalog() -> list[dict]:
    """Everything the phone needs to draw a pipeline picker, so it never
    hardcodes one (spec §5.2). Providers only where a try-on stage exists:
    elsewhere the provider is never read (render_manifest, job.py)."""
    catalog = []
    for name in sorted(PIPELINES):
        required, optional = required_roles(name), optional_roles(name)
        catalog.append({
            "id": name,
            "stages": list(PIPELINES[name]),
            "required": sorted(required),
            "optional": sorted(optional),
            "roles": {role: role_kind(role) for role in sorted(required | optional)},
            "providers": ([{"id": pid, "label": label} for pid, label in PROVIDER_LABELS.items()]
                          if _tryon_stage(name) is not None else []),
        })
    return catalog
```

In `scripts/tgbot/bot.py`:
- Delete `PROVIDER_LABELS` and its comment; import it: `from control.drafts import PROVIDER_LABELS`
  (next to the existing `control` imports; keep a one-line comment where it used to be:
  `# PROVIDER_LABELS lives in control/drafts.py (shared with the phone API).`).
- Replace the four `def`s with aliases at the same spot, e.g.
  `_copy_job = drafts.copy_job`, `_signature = drafts.signature`, `_job_digest = drafts.job_digest`,
  and `_dump_jobs = drafts.dump_jobs`, `_load_jobs = drafts.load_jobs` (with `import control.drafts as
  drafts` at the top).
- `_jobs_for(chat_id)` keeps its docstring and becomes
  `return drafts.jobs_for(_STATE.get(chat_id), _BASKET.get(chat_id) or [])`.
- `_switch_pipeline` keeps its signature and docstring; its body becomes:

```python
    job = _job_for(chat_id)
    dropped = drafts.drop_unusable(job, name)
    _LAST_VALIDATE.pop(chat_id, None)
    _CONFIRM_WARNED.discard(chat_id)
    return job, dropped
```

(Keep whatever comments sat inside the old body next to the equivalent lines.)

- [ ] **Step 4: Run tests**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_drafts.py'` → PASS.
Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_bot.py'` → 691 tests OK, file unchanged
(`git diff --stat scripts/tests/test_batch_bot.py` prints nothing).
Run: `make batch-test` → OK.

- [ ] **Step 5: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add scripts/control/__init__.py scripts/control/drafts.py scripts/tgbot/bot.py scripts/tests/test_batch_control_drafts.py
git commit -m "refactor(control): move the pure draft helpers out of the bot

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: `DraftStore` — the app's draft (view, patch, batch, clear) and the in-use check

**Files:**
- Modify: `scripts/control/drafts.py` (append)
- Modify: `scripts/control/materials.py` (`_in_use`)
- Test: `scripts/tests/test_batch_control_drafts.py` (append), `scripts/tests/test_batch_control_materials.py` (append)

**Interfaces:**
- Consumes: Task 1's helpers; `materials.resolve_material(staging_root, owner, name) -> Path | None`,
  `materials.APP_OWNER == "app"`; `tgbot.ingest.probe(path) -> Probe`;
  `tgbot.run.estimate_minutes(job) -> int`.
- Produces (used by Tasks 3-4):
  - `drafts.DraftError(code: str, message: str)` with `.code`, `.message`
  - `drafts.DraftStore(batch_dir: Path, staging_root: Path, owner: str, *, default_pipeline: str,
    default_provider: str, probe=ingest.probe)`; attribute `.path` = `batch_dir / f"{owner}.draft.json"`
  - `store.view() -> dict`, `store.patch(body: dict) -> dict` (view plus `"dropped": [...]`),
    `store.add_to_batch() -> dict`, `store.drop_from_batch(digest: str) -> dict`, `store.clear() -> dict`
  - Private, used by Task 3: `store._load() -> _Draft`, `store._save(d: _Draft) -> None`

The view shape (every mutating call returns it too):

```json
{"owner": "app", "pipeline": "tryon-motion-enhance", "provider": "gemini", "generation": 4,
 "slots": {"character": {"material_id": "app/me.png", "name": "me.png", "exists": true,
                         "probe": {"kind": "image", "width": 1080, "height": 1920,
                                   "duration_s": 0.0, "bitrate_kbps": 0, "size_bytes": 812345},
                         "warning": ""}},
 "required": ["character", "driver", "outfit"], "optional": ["background"],
 "missing": ["driver", "outfit"],
 "validated": null,
 "batch": [{"digest": "3f9a0c1b2d", "run_id": "me-dress-dance", "pipeline": "...",
            "provider": "gemini", "slots": {"character": "app/me.png", "...": "..."}}],
 "jobs": 1, "estimate_min": null}
```

`material_id` is `"<owner>/<name>"` when the file sits directly in `staging_root/<owner>/`, else
`null`. `warning` is `ingest.quality_warning(probe)` (plain text). `jobs` is `len(jobs_for(...))`.
`estimate_min` is `sum(estimate_minutes(j) for j in jobs)` only when `validated is True`, else `null`
(the bot shows the estimate only after validation too). `missing` counts a slot whose file is gone.

- [ ] **Step 1: Write the failing tests**

Append to `scripts/tests/test_batch_control_drafts.py` (add `import json, shutil, tempfile, threading`
and `from unittest import mock` at the top):

```python
class StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.batch = self.tmp / "batch"
        self.staging = self.batch / "tg-staging"
        (self.staging / "app").mkdir(parents=True)
        (self.staging / "123").mkdir()
        for name in ("me.png", "dress.png", "bg.png"):
            (self.staging / "app" / name).write_bytes(b"img")
        (self.staging / "app" / "dance.mp4").write_bytes(b"vid")
        (self.staging / "123" / "tg.png").write_bytes(b"img")
        self.probed = []

        def fake_probe(path):
            self.probed.append(path.name)
            if path.name.startswith("broken"):
                raise RuntimeError("ffprobe could not read it")
            return VID if path.suffix == ".mp4" else IMG

        self.store = drafts.DraftStore(self.batch, self.staging, "app",
                                       default_pipeline="tryon-motion-enhance",
                                       default_provider="gemini", probe=fake_probe)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def fill(self):
        return self.store.patch({"slots": {"character": "app/me.png", "outfit": "app/dress.png",
                                           "driver": "app/dance.mp4"}})

    def assertRefused(self, code, fn, *args):
        with self.assertRaises(drafts.DraftError) as cm:
            fn(*args)
        self.assertEqual(cm.exception.code, code)


class TestView(StoreCase):
    def test_fresh_draft_uses_defaults_and_writes_nothing(self):
        v = self.store.view()
        self.assertEqual((v["owner"], v["pipeline"], v["provider"]), ("app", "tryon-motion-enhance", "gemini"))
        self.assertEqual(v["slots"], {})
        self.assertEqual(v["missing"], ["character", "driver", "outfit"])
        self.assertEqual((v["validated"], v["batch"], v["jobs"], v["estimate_min"]), (None, [], 0, None))
        self.assertFalse(self.store.path.exists())

    def test_view_has_no_absolute_paths(self):
        self.fill()
        self.assertNotIn(str(self.tmp), json.dumps(self.store.view()))

    def test_unreadable_file_is_set_aside(self):
        self.store.path.parent.mkdir(parents=True, exist_ok=True)
        self.store.path.write_text("{not json")
        v = self.store.view()
        self.assertEqual(v["slots"], {})
        self.assertTrue(self.store.path.with_name("app.draft.json.bad").exists())


class TestPatch(StoreCase):
    def test_fill_all_slots(self):
        v = self.fill()
        self.assertEqual(v["missing"], [])
        self.assertEqual(v["slots"]["driver"]["material_id"], "app/dance.mp4")
        self.assertEqual(v["slots"]["driver"]["probe"]["kind"], "video")
        self.assertTrue(v["slots"]["driver"]["exists"])
        self.assertEqual(v["jobs"], 1)
        self.assertEqual(v["dropped"], [])
        self.assertEqual(self.store.view()["generation"], v["generation"])   # persisted

    def test_other_owners_material_is_usable(self):
        v = self.store.patch({"slots": {"character": "123/tg.png"}})
        self.assertEqual(v["slots"]["character"]["material_id"], "123/tg.png")

    def test_null_empties_a_slot(self):
        self.fill()
        v = self.store.patch({"slots": {"outfit": None}})
        self.assertNotIn("outfit", v["slots"])
        self.assertEqual(v["missing"], ["outfit"])

    def test_switch_pipeline_reports_dropped_roles(self):
        self.fill()
        v = self.store.patch({"pipeline": "motion-enhance"})
        self.assertEqual(v["dropped"], ["outfit"])
        self.assertEqual(set(v["slots"]), {"character", "driver"})

    def test_pipeline_and_slots_in_one_patch_check_roles_against_the_new_pipeline(self):
        self.assertRefused("unknown_role", self.store.patch,
                           {"pipeline": "motion-enhance", "slots": {"outfit": "app/dress.png"}})

    def test_refusals(self):
        self.assertRefused("unknown_pipeline", self.store.patch, {"pipeline": "nope"})
        self.assertRefused("unknown_provider", self.store.patch, {"provider": "nope"})
        self.assertRefused("unknown_role", self.store.patch, {"slots": {"hat": "app/me.png"}})
        self.assertRefused("wrong_kind", self.store.patch, {"slots": {"driver": "app/me.png"}})
        self.assertRefused("wrong_kind", self.store.patch, {"slots": {"character": "app/dance.mp4"}})
        self.assertRefused("not_found", self.store.patch, {"slots": {"character": "app/missing.png"}})
        self.assertRefused("not_found", self.store.patch, {"slots": {"character": "app/../123/tg.png"}})
        self.assertRefused("not_found", self.store.patch, {"slots": {"character": "me.png"}})
        self.assertRefused("bad_request", self.store.patch, {"colour": "red"})
        self.assertRefused("bad_request", self.store.patch, {"slots": ["app/me.png"]})
        self.assertRefused("bad_request", self.store.patch, {"pipeline": 3})
        self.assertRefused("not_applicable", self.store.patch,
                           {"pipeline": "motion-enhance", "provider": "qwen"})

    def test_unprobeable(self):
        (self.staging / "app" / "broken.png").write_bytes(b"x")
        self.assertRefused("unprobeable", self.store.patch, {"slots": {"character": "app/broken.png"}})

    def test_a_refused_patch_changes_nothing(self):
        before = self.fill()
        self.assertRefused("wrong_kind", self.store.patch,
                           {"pipeline": "motion-enhance", "slots": {"driver": "app/me.png"}})
        after = self.store.view()
        self.assertEqual(after["pipeline"], "tryon-motion-enhance")
        self.assertEqual(after["generation"], before["generation"])

    def test_every_change_bumps_generation_and_resets_the_verdict(self):
        g = self.fill()["generation"]
        d = self.store._load()
        d.validated = True
        self.store._save(d)
        v = self.store.patch({"provider": "qwen-max"})
        self.assertEqual(v["generation"], g + 1)
        self.assertIsNone(v["validated"])

    def test_probe_runs_outside_the_lock(self):
        seen = []
        orig = self.store._probe

        def probe_and_check(path):
            t = threading.Thread(target=lambda: seen.append(control.LOCK.acquire(timeout=1)))
            t.start(); t.join()
            if seen[-1]:
                control.LOCK.release()
            return orig(path)

        self.store._probe = probe_and_check
        self.store.patch({"slots": {"character": "app/me.png"}})
        self.assertEqual(seen, [True])

    def test_vanished_file_counts_as_missing(self):
        self.fill()
        (self.staging / "app" / "dress.png").unlink()
        v = self.store.view()
        self.assertFalse(v["slots"]["outfit"]["exists"])
        self.assertEqual(v["missing"], ["outfit"])


class TestBatch(StoreCase):
    def test_add_keeps_editing_a_copy(self):
        self.fill()
        v = self.store.add_to_batch()
        self.assertEqual(len(v["batch"]), 1)
        self.assertEqual(v["jobs"], 1)                  # the current job equals the batch entry
        v = self.store.patch({"slots": {"outfit": "app/bg.png"}})
        self.assertEqual(v["jobs"], 2)
        self.assertEqual(v["batch"][0]["slots"]["outfit"], "app/dress.png")

    def test_add_refusals(self):
        self.assertRefused("missing_slots", self.store.add_to_batch)
        self.fill()
        self.store.add_to_batch()
        self.assertRefused("duplicate", self.store.add_to_batch)

    def test_add_refuses_when_a_file_vanished(self):
        self.fill()
        (self.staging / "app" / "dance.mp4").unlink()
        self.assertRefused("missing_slots", self.store.add_to_batch)

    def test_drop_by_digest(self):
        self.fill()
        digest = self.store.add_to_batch()["batch"][0]["digest"]
        self.assertRefused("not_found", self.store.drop_from_batch, "0000000000")
        v = self.store.drop_from_batch(digest)
        self.assertEqual(v["batch"], [])

    def test_concurrent_adds_make_one_entry(self):
        self.fill()
        errors = []

        def add():
            try:
                self.store.add_to_batch()
            except drafts.DraftError as exc:
                errors.append(exc.code)

        threads = [threading.Thread(target=add) for _ in range(8)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(len(self.store.view()["batch"]), 1)
        self.assertEqual(errors, ["duplicate"] * 7)

    def test_clear_resets_but_deletes_no_material(self):
        g = self.fill()["generation"]
        self.store.add_to_batch()
        v = self.store.clear()
        self.assertEqual((v["slots"], v["batch"]), ({}, []))
        self.assertGreater(v["generation"], g)
        self.assertTrue((self.staging / "app" / "me.png").exists())
```

Append to `scripts/tests/test_batch_control_materials.py` (inside whichever class tests
`delete_material`, or a new `TestDeleteVsAppDraft(unittest.TestCase)` with its own temp dirs):

```python
    def test_delete_refuses_material_the_app_draft_uses(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        batch, staging = tmp / "batch", tmp / "batch" / "tg-staging"
        (staging / "app").mkdir(parents=True)
        used, free = staging / "app" / "a.png", staging / "app" / "a.png.bak"
        used.write_bytes(b"x"); free.write_bytes(b"x")
        (batch / "app.draft.json").write_text(json.dumps({"slots": {"character": str(used)}}, indent=2))
        with self.assertRaises(materials.MaterialError) as cm:
            materials.delete_material(staging, batch, "app", "a.png")
        self.assertEqual(cm.exception.code, "in_use")
        materials.delete_material(staging, batch, "app", "a.png.bak")   # whole-path match only
        self.assertFalse(free.exists())
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_drafts.py'` → FAIL
(`AttributeError: module 'control.drafts' has no attribute 'DraftStore'`).
Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_materials.py'` → the new
test FAILs (delete succeeds).

- [ ] **Step 3: Implement the store**

Append to `scripts/control/drafts.py` (and add the imports `json`, `uuid`, `from dataclasses import
dataclass, field`, `import control`, `from control import materials`,
`from tgbot import ingest`, `from tgbot.run import estimate_minutes`, `from tgbot.job import run_id_for`):

```python
class DraftError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass
class _Draft:
    job: Job
    basket: list[Job] = field(default_factory=list)
    # Tri-state, as the bot's _LAST_VALIDATE: None = never checked since the
    # last change, True/False = the verdict of make batch-validate.
    validated: bool | None = None
    generation: int = 0


_PATCH_KEYS = frozenset({"pipeline", "provider", "slots"})


class DraftStore:
    """One owner's draft, on disk at batch/<owner>.draft.json.

    Read and written on every call under control.LOCK instead of cached in
    memory: the file is a few KB, and a per-request read removes the bot's
    load-once/save-in-finally envelope along with its check-then-act races.
    Slow work (ffprobe here, make batch-validate in validate()) runs outside
    the lock.
    """

    def __init__(self, batch_dir: Path, staging_root: Path, owner: str, *,
                 default_pipeline: str, default_provider: str, probe=ingest.probe):
        self.batch_dir, self.staging_root, self.owner = batch_dir, staging_root, owner
        self.default_pipeline, self.default_provider = default_pipeline, default_provider
        self._probe = probe
        self.path = batch_dir / f"{owner}.draft.json"

    # -- persistence -------------------------------------------------------

    def _fresh(self, generation: int = 0) -> _Draft:
        return _Draft(job=Job(slots={}, probes={}, pipeline=self.default_pipeline,
                              provider=self.default_provider), generation=generation)

    def _load(self) -> _Draft:
        if not self.path.exists():
            return self._fresh()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            [current] = load_jobs([payload["job"]])
            if current.pipeline not in PIPELINES:
                raise ValueError(f"unknown pipeline {current.pipeline!r}")
            return _Draft(job=current, basket=load_jobs(payload["basket"]),
                          validated=payload["validated"], generation=int(payload["generation"]))
        except (ValueError, KeyError, TypeError):
            # Moved aside, never deleted: it is the only copy of what the
            # user had composed (same rule as the bot's _load_draft).
            self.path.replace(self.path.with_name(self.path.name + ".bad"))
            return self._fresh()

    def _save(self, d: _Draft) -> None:
        payload = {"job": dump_jobs([d.job])[0], "basket": dump_jobs(d.basket),
                   "validated": d.validated, "generation": d.generation}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f"{self.path.name}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def _changed(self, d: _Draft) -> dict:
        d.validated = None
        d.generation += 1
        self._save(d)
        return self._view(d)

    # -- rendering ---------------------------------------------------------

    def _material_id(self, path: Path) -> str | None:
        try:
            rel = path.relative_to(self.staging_root)
        except ValueError:
            return None
        return "/".join(rel.parts) if len(rel.parts) == 2 else None

    def _missing(self, job: Job) -> list[str]:
        present = {r for r, p in job.slots.items() if p.is_file()}
        return sorted(required_roles(job.pipeline) - present)

    def _view(self, d: _Draft) -> dict:
        job = d.job
        jobs = jobs_for(job, d.basket)
        return {
            "owner": self.owner, "pipeline": job.pipeline, "provider": job.provider,
            "generation": d.generation,
            "slots": {role: {"material_id": self._material_id(path), "name": path.name,
                             "exists": path.is_file(), "probe": asdict(job.probes[role]),
                             "warning": ingest.quality_warning(job.probes[role])}
                      for role, path in sorted(job.slots.items())},
            "required": sorted(required_roles(job.pipeline)),
            "optional": sorted(optional_roles(job.pipeline)),
            "missing": self._missing(job),
            "validated": d.validated,
            "batch": [{"digest": job_digest(b), "run_id": run_id_for(b), "pipeline": b.pipeline,
                       "provider": b.provider,
                       "slots": {r: self._material_id(p) for r, p in sorted(b.slots.items())}}
                      for b in d.basket],
            "jobs": len(jobs),
            "estimate_min": sum(estimate_minutes(j) for j in jobs) if d.validated is True else None,
        }

    def view(self) -> dict:
        with control.LOCK:
            return self._view(self._load())

    # -- mutations ---------------------------------------------------------

    def _resolve(self, material_id) -> Path:
        if not isinstance(material_id, str) or material_id.count("/") != 1:
            raise DraftError("not_found", "no such material")
        owner, name = material_id.split("/")
        path = materials.resolve_material(self.staging_root, owner, name)
        if path is None:
            raise DraftError("not_found", f"no such material: {material_id}")
        return path

    def patch(self, body: dict) -> dict:
        if not isinstance(body, dict) or not body or set(body) - _PATCH_KEYS:
            raise DraftError("bad_request", "expected an object with pipeline, provider and/or slots")
        pipeline, provider, slots = body.get("pipeline"), body.get("provider"), body.get("slots", {})
        if pipeline is not None and not isinstance(pipeline, str):
            raise DraftError("bad_request", "pipeline must be a string")
        if provider is not None and not isinstance(provider, str):
            raise DraftError("bad_request", "provider must be a string")
        if not isinstance(slots, dict):
            raise DraftError("bad_request", "slots must be an object of role -> material id or null")
        if pipeline is not None and pipeline not in PIPELINES:
            raise DraftError("unknown_pipeline", f"unknown pipeline {pipeline!r}")
        if provider is not None and provider not in PROVIDER_LABELS:
            raise DraftError("unknown_provider", f"unknown provider {provider!r}")

        # Outside the lock: ffprobe takes up to 60 s per file (ingest.probe).
        filled: dict[str, tuple[Path, Probe]] = {}
        for role, material_id in slots.items():
            if material_id is None:
                continue
            path = self._resolve(material_id)
            try:
                filled[role] = (path, self._probe(path))
            except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
                raise DraftError("unprobeable", f"{path.name} could not be read: {exc}")

        with control.LOCK:
            d = self._load()
            target = pipeline or d.job.pipeline
            usable = required_roles(target) | optional_roles(target)
            for role in slots:
                if role not in usable:
                    raise DraftError("unknown_role", f"{target} has no {role!r} slot")
            for role, (path, probed) in filled.items():
                if probed.kind != role_kind(role):
                    raise DraftError("wrong_kind",
                                     f"{role} must be {role_kind(role)}, {path.name} is {probed.kind}")
            if provider is not None and _tryon_stage(target) is None:
                raise DraftError("not_applicable", f"{target} has no try-on stage to pick a provider for")
            # Every check passed: apply. Nothing above wrote anything.
            dropped = drop_unusable(d.job, target) if pipeline is not None else []
            if provider is not None:
                d.job.provider = provider
            for role, material_id in slots.items():
                if material_id is None:
                    d.job.slots.pop(role, None)
                    d.job.probes.pop(role, None)
                else:
                    d.job.slots[role], d.job.probes[role] = filled[role]
            view = self._changed(d)
        view["dropped"] = dropped
        return view

    def add_to_batch(self) -> dict:
        with control.LOCK:
            d = self._load()
            if self._missing(d.job):
                raise DraftError("missing_slots",
                                 "fill every required slot first: " + ", ".join(self._missing(d.job)))
            if any(signature(d.job) == signature(other) for other in d.basket):
                raise DraftError("duplicate", "that exact job is already in the batch")
            # Two copies: the batch entry and the job still being edited must
            # not share slot dicts (bot's _add_to_batch).
            d.basket.append(copy_job(d.job))
            d.job = copy_job(d.job)
            return self._changed(d)

    def drop_from_batch(self, digest: str) -> dict:
        with control.LOCK:
            d = self._load()
            index = next((i for i, b in enumerate(d.basket) if job_digest(b) == digest), None)
            if index is None:
                raise DraftError("not_found", "that entry is no longer in the batch")
            d.basket.pop(index)
            return self._changed(d)

    def clear(self) -> dict:
        with control.LOCK:
            # The generation keeps counting, so a validate that started before
            # the clear cannot record its verdict on the empty draft.
            d = self._fresh(generation=self._load().generation)
            return self._changed(d)
```

Add `import subprocess` to the module imports.

- [ ] **Step 4: Implement the in-use check**

In `scripts/control/materials.py`, `_in_use`: after the manifest loop and before `return False`, add:

```python
    # The phone's draft (control/drafts.py) names files it has not run yet;
    # deleting one would leave the draft pointing at nothing. Not a manifest,
    # so not gated on busy(): a draft is always "in use".
    draft = batch_dir / f"{APP_OWNER}.draft.json"
    try:
        if pattern.search(draft.read_text(encoding="utf-8", errors="replace")):
            return True
    except OSError:
        pass
```

Update the docstring's first line to say "A busy run's manifest, or the app's draft, names this file."
`APP_OWNER` is defined below `_in_use`'s current position? — it is at module level above it
(line ~198); if not, move the constant up. Do not import `drafts` from `materials` (drafts imports
materials).

- [ ] **Step 5: Run tests**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_*.py'` → OK.
Run: `make batch-test` → OK; `git diff --stat scripts/tests/test_batch_bot.py` empty.

- [ ] **Step 6: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add scripts/control/drafts.py scripts/control/materials.py scripts/tests/test_batch_control_drafts.py scripts/tests/test_batch_control_materials.py
git commit -m "feat(control): the app's own draft, composed through DraftStore

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: `DraftStore.validate` — free `make batch-validate`, outside the lock

**Files:**
- Modify: `scripts/control/drafts.py` (add method + constant)
- Test: `scripts/tests/test_batch_control_drafts.py` (append)

**Interfaces:**
- Consumes: Task 2's `DraftStore`, `_load`, `_save`, `_view`; `tgbot.job.write_manifest(jobs, path, *, now)`.
- Produces: `DraftStore.validate(*, repo_root: Path, run=None) -> dict` (`None` = `subprocess.run`,
  looked up at call time so a test can patch `control.drafts.subprocess.run`) returning
  `{"valid": True, "stale": bool, "draft": <view>}`; raises `DraftError("nothing_to_validate", …)` or
  `DraftError("invalid", <output>)`. Constant `VALIDATE_TIMEOUT_SEC = 90`.

- [ ] **Step 1: Write the failing tests**

```python
class TestValidate(StoreCase):
    def fake_run(self, returncode=0, out="  ✓ manifest hợp lệ · 1 run\n", err="", before=None):
        calls = []

        def run(cmd, **kw):
            calls.append((cmd, kw))
            manifest = Path(cmd[2][len("FILE="):])
            calls.append(manifest.read_text())
            if before:
                before()
            return mock.Mock(returncode=returncode, stdout=out, stderr=err)
        return run, calls

    def test_nothing_to_validate(self):
        self.assertRefused("nothing_to_validate", self.store.validate, repo_root=self.tmp)

    def test_success_records_the_verdict_and_removes_the_manifest(self):
        self.fill()
        run, calls = self.fake_run()
        result = self.store.validate(repo_root=self.tmp, run=run)
        self.assertEqual((result["valid"], result["stale"]), (True, False))
        self.assertTrue(result["draft"]["validated"])
        self.assertIsNotNone(result["draft"]["estimate_min"])
        cmd, kw = calls[0]
        self.assertEqual(cmd[:2], ["make", "batch-validate"])
        self.assertEqual(kw["cwd"], self.tmp)
        self.assertEqual(kw["timeout"], drafts.VALIDATE_TIMEOUT_SEC)
        self.assertIn("pipeline: tryon-motion-enhance", calls[1])
        manifest = Path(cmd[2][len("FILE="):])
        self.assertEqual(manifest.parent, self.batch / ".validate")
        self.assertFalse(manifest.exists())
        self.assertEqual(list(self.batch.glob("*.yaml")), [])

    def test_failure_is_422_with_output_and_no_absolute_paths(self):
        self.fill()
        run, _ = self.fake_run(returncode=1, err=f"✗ missing {self.tmp}/batch/tg-staging/app/me.png\n")
        with self.assertRaises(drafts.DraftError) as cm:
            self.store.validate(repo_root=self.tmp, run=run)
        self.assertEqual(cm.exception.code, "invalid")
        self.assertIn("batch/tg-staging/app/me.png", cm.exception.message)
        self.assertNotIn(str(self.tmp), cm.exception.message)
        self.assertIs(self.store.view()["validated"], False)

    def test_timeout_is_recorded_as_failed(self):
        self.fill()

        def run(cmd, **kw):
            raise drafts.subprocess.TimeoutExpired(cmd, kw["timeout"])

        with self.assertRaises(drafts.DraftError) as cm:
            self.store.validate(repo_root=self.tmp, run=run)
        self.assertEqual(cm.exception.code, "invalid")
        self.assertIn("90", cm.exception.message)

    def test_a_change_during_validation_makes_the_verdict_stale(self):
        self.fill()
        run, _ = self.fake_run(before=lambda: self.store.patch({"provider": "qwen-max"}))
        result = self.store.validate(repo_root=self.tmp, run=run)
        self.assertTrue(result["stale"])
        self.assertIsNone(self.store.view()["validated"])

    def test_the_subprocess_runs_outside_the_lock(self):
        self.fill()
        got = []

        def probe_lock():
            t = threading.Thread(target=lambda: got.append(control.LOCK.acquire(timeout=1)))
            t.start(); t.join()
            if got[-1]:
                control.LOCK.release()

        run, _ = self.fake_run(before=probe_lock)
        self.store.validate(repo_root=self.tmp, run=run)
        self.assertEqual(got, [True])
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_drafts.py'` → FAIL
(`AttributeError: 'DraftStore' object has no attribute 'validate'`).

- [ ] **Step 3: Implement**

Add near the top of `scripts/control/drafts.py` (import `time` and `from tgbot.job import write_manifest`):

```python
# Cloudflare answers 524 when the origin sends nothing for 100 s, so the
# phone's validate must finish (or give up) before that. The bot allows 120 s,
# "~100x the observed runtime"; 90 s is still ~75x.
VALIDATE_TIMEOUT_SEC = 90
# The tail of the validator's output returned to the phone: enough for every
# error line batch_run.py prints, bounded so a runaway log stays small.
VALIDATE_OUTPUT_MAX_CHARS = 4000
```

Add the method to `DraftStore`:

```python
    def validate(self, *, repo_root: Path, run=None) -> dict:
        """make batch-validate on what Run would submit. Free: no pod.

        The manifest is rendered under the lock and checked outside it; the
        verdict is recorded only if the draft did not change meanwhile.
        """
        with control.LOCK:
            d = self._load()
            jobs = jobs_for(d.job, d.basket)
            if not jobs:
                raise DraftError("nothing_to_validate", "no complete job to validate yet")
            generation = d.generation
            # A subdirectory: materials._in_use and runs.list_runs scan
            # batch/*.yaml, and this file is never a run.
            manifest = self.batch_dir / ".validate" / f"{self.owner}-{uuid.uuid4().hex}.yaml"
            write_manifest(jobs, manifest, now=time.strftime("%Y-%m-%d %H:%M:%S"))
        run = run or subprocess.run
        try:
            result = run(["make", "batch-validate", f"FILE={manifest}"], cwd=repo_root,
                         capture_output=True, text=True, timeout=VALIDATE_TIMEOUT_SEC)
            ok, output = result.returncode == 0, (result.stdout + result.stderr).strip()
        except subprocess.TimeoutExpired:
            ok, output = False, f"make batch-validate did not finish within {VALIDATE_TIMEOUT_SEC}s"
        finally:
            manifest.unlink(missing_ok=True)
        # Staged files are named by absolute path in the validator's errors.
        output = output.replace(str(repo_root) + "/", "")[-VALIDATE_OUTPUT_MAX_CHARS:]

        with control.LOCK:
            d = self._load()
            stale = d.generation != generation
            if not stale:
                d.validated = ok
                self._save(d)          # a verdict is not a change: generation stays
            view = self._view(d)
        if not ok:
            raise DraftError("invalid", output or "make batch-validate failed")
        return {"valid": True, "stale": stale, "draft": view}
```

- [ ] **Step 4: Run tests**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_*.py'` → OK; `make batch-test` → OK.

- [ ] **Step 5: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add scripts/control/drafts.py scripts/tests/test_batch_control_drafts.py
git commit -m "feat(control): validate the app's draft with make batch-validate

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: HTTP routes for pipelines and the draft; wire the bot's defaults; document

**Files:**
- Modify: `scripts/httpapi/server.py`
- Modify: `scripts/tgbot/bot.py` (`_start_control_api`'s `make_server` call only)
- Modify: `scripts/vps/README.md` (Phone API section: list the new routes)
- Test: `scripts/tests/test_batch_control_http.py` (append)

**Interfaces:**
- Consumes: `drafts.DraftStore`, `drafts.DraftError`, `drafts.pipeline_catalog()`,
  `materials.APP_OWNER`.
- Produces: `make_server(*, token, batch_dir, out_dir, host="127.0.0.1", port=0, log=print,
  default_pipeline="tryon-motion-enhance", default_provider="gemini", probe=None)`; the server gets
  `server.drafts: DraftStore` (owner `app`) and `server.repo_root = batch_dir.parent`. `probe=None`
  means `ingest.probe`; tests pass a fake.

Routes:

| Method + path | Calls | Success |
|---|---|---|
| `GET /v1/pipelines` | `{"pipelines": drafts.pipeline_catalog()}` | 200 |
| `GET /v1/draft` | `s.drafts.view()` | 200 |
| `PATCH /v1/draft` | `s.drafts.patch(self._read_json())` | 200 |
| `POST /v1/draft/add-to-batch` | `s.drafts.add_to_batch()` | 200 |
| `DELETE /v1/draft/batch/<digest>` | `s.drafts.drop_from_batch(digest)` | 200 (returns the view) |
| `POST /v1/draft/clear` | `s.drafts.clear()` | 200 |
| `POST /v1/draft/validate` | `s.drafts.validate(repo_root=s.repo_root)` | 200 |

- [ ] **Step 1: Write the failing tests**

Look at the top of `scripts/tests/test_batch_control_http.py` first: reuse its existing server
fixture and request helper (it starts `make_server` on port 0 in a thread and has a helper that sends
a method, path, optional JSON body and headers and returns status + parsed JSON). If the fixture does
not accept `probe`, add a keyword pass-through. Then append a test class that, with material laid out
as in Task 2's `StoreCase` under the fixture's `batch_dir`, and a fake probe (image for `.png`, video
for `.mp4`) passed to `make_server`, asserts:

```python
class TestDraftRoutes(<existing server base class>):
    # setUp: create batch_dir/tg-staging/app/{me.png,dress.png,dance.mp4}; start the server with
    # probe=fake_probe, default_pipeline="tryon-motion-enhance", default_provider="gemini".

    def test_pipelines(self):
        status, body = self.request("GET", "/v1/pipelines")
        self.assertEqual(status, 200)
        self.assertIn("tryon-motion-enhance", [p["id"] for p in body["pipelines"]])

    def test_compose_batch_and_clear(self):
        status, body = self.request("GET", "/v1/draft")
        self.assertEqual((status, body["pipeline"], body["provider"]), (200, "tryon-motion-enhance", "gemini"))
        status, body = self.request("PATCH", "/v1/draft", {"slots": {
            "character": "app/me.png", "outfit": "app/dress.png", "driver": "app/dance.mp4"}})
        self.assertEqual((status, body["missing"]), (200, []))
        status, body = self.request("POST", "/v1/draft/add-to-batch")
        self.assertEqual((status, len(body["batch"])), (200, 1))
        digest = body["batch"][0]["digest"]
        status, body = self.request("DELETE", f"/v1/draft/batch/{digest}")
        self.assertEqual((status, body["batch"]), (200, []))
        status, body = self.request("POST", "/v1/draft/clear")
        self.assertEqual((status, body["slots"]), (200, {}))

    def test_refusal_statuses(self):
        cases = [
            ("PATCH", "/v1/draft", {"pipeline": "nope"}, 422, "unknown_pipeline"),
            ("PATCH", "/v1/draft", {"slots": {"driver": "app/me.png"}}, 422, "wrong_kind"),
            ("PATCH", "/v1/draft", {"slots": {"character": "app/none.png"}}, 404, "not_found"),
            ("PATCH", "/v1/draft", {"hat": 1}, 400, "bad_request"),
            ("POST", "/v1/draft/add-to-batch", None, 422, "missing_slots"),
            ("DELETE", "/v1/draft/batch/0000000000", None, 404, "not_found"),
            ("POST", "/v1/draft/validate", None, 422, "nothing_to_validate"),
        ]
        for method, path, body, want_status, want_code in cases:
            with self.subTest(method=method, path=path, body=body):
                status, got = self.request(method, path, body)
                self.assertEqual((status, got["error"]["code"]), (want_status, want_code))

    def test_draft_etag_is_stable_between_polls(self):
        # Use the fixture's raw-request helper to read the ETag header of GET /v1/draft,
        # send it back in If-None-Match, and assert 304.

    def test_deleting_material_the_draft_uses_is_409(self):
        self.request("PATCH", "/v1/draft", {"slots": {"character": "app/me.png"}})
        status, body = self.request("DELETE", "/v1/materials/app/me.png")
        self.assertEqual((status, body["error"]["code"]), (409, "in_use"))

    def test_patch_error_closes_the_connection(self):
        # Same technique as the existing keep-alive tests for DELETE/POST errors: send
        # PATCH /v1/draft with {"pipeline": "nope"} on a raw connection and assert the
        # response carries "Connection: close".

    def test_validate_route_passes_repo_root(self):
        self.request("PATCH", "/v1/draft", {"slots": {
            "character": "app/me.png", "outfit": "app/dress.png", "driver": "app/dance.mp4"}})
        with mock.patch("control.drafts.subprocess.run",
                        return_value=mock.Mock(returncode=0, stdout="ok", stderr="")) as run:
            status, body = self.request("POST", "/v1/draft/validate")
        self.assertEqual((status, body["valid"]), (200, True))
        self.assertEqual(run.call_args.kwargs["cwd"], self.batch_dir.parent)
```

Fill in the two commented tests with real code using the helpers that already exist in the file
(the file already has 304/ETag tests for `/v1/runs/<id>` and `Connection: close` tests for DELETE — copy
their technique).

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_http.py'` → the new tests
FAIL with 404s (routes do not exist) or 501 for PATCH.

- [ ] **Step 3: Implement**

In `scripts/httpapi/server.py`:
- `import control.drafts as drafts`.
- Extend `_DOMAIN_STATUS` with
  `"unknown_pipeline": 422, "unknown_provider": 422, "unknown_role": 422, "wrong_kind": 422,
  "not_applicable": 422, "missing_slots": 422, "duplicate": 422, "nothing_to_validate": 422,
  "invalid": 422` (keep existing entries; `not_found`, `bad_request`, `unprobeable` already map).
- `_handle`: catch `drafts.DraftError` in the same `except` tuple as `UploadError`/`MaterialError`.
- Add `def do_PATCH(self): self._handle("PATCH")`.
- `_error`: `("POST", "PUT", "PATCH", "DELETE")`.
- In `_route`, before the final `raise NOT_FOUND`:

```python
        if method == "GET" and rest == ["pipelines"]:
            return self._send_json(200, {"pipelines": drafts.pipeline_catalog()})
        if rest[:1] == ["draft"]:
            return self._route_draft(method, rest[1:])
```

and a new method:

```python
    def _route_draft(self, method: str, rest: list[str]) -> None:
        store = self.server.drafts
        if method == "GET" and rest == []:
            return self._send_json(200, store.view())
        if method == "PATCH" and rest == []:
            return self._send_json(200, store.patch(self._read_json()))
        if method == "POST" and rest == ["add-to-batch"]:
            return self._send_json(200, store.add_to_batch())
        if method == "POST" and rest == ["clear"]:
            return self._send_json(200, store.clear())
        if method == "POST" and rest == ["validate"]:
            return self._send_json(200, store.validate(repo_root=self.server.repo_root))
        if method == "DELETE" and len(rest) == 2 and rest[0] == "batch":
            return self._send_json(200, store.drop_from_batch(rest[1]))
        raise NOT_FOUND
```

- `make_server`: add the keyword parameters listed under Interfaces, and after `thumbs_root`:

```python
    server.repo_root = batch_dir.parent
    server.drafts = drafts.DraftStore(
        batch_dir, server.staging_root, materials.APP_OWNER,
        default_pipeline=default_pipeline, default_provider=default_provider,
        **({"probe": probe} if probe is not None else {}))
```

- If `_read_json` refuses a non-object body, fine; `patch` also checks.

In `scripts/tgbot/bot.py`, `_start_control_api`: pass
`default_pipeline=_DEFAULT_PIPELINE, default_provider=_DEFAULT_PROVIDER` to `make_server`, with a
comment that `main()` has already applied `TG_PIPELINE`/`TG_PROVIDER` by then, so the phone's new
drafts start where Telegram's do.

In `scripts/vps/README.md`, in the "Phone API" section, add the slice-3 routes table (the one above)
and one sentence: the app's draft is `batch/app.draft.json`, separate from Telegram's
`batch/tg-<chat>.draft.json`; deleting it resets the phone's draft only.

- [ ] **Step 4: Run tests**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_*.py'` → OK.
Run: `make batch-test` → OK; `git diff --stat scripts/tests/test_batch_bot.py` empty.

- [ ] **Step 5: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add scripts/httpapi/server.py scripts/tgbot/bot.py scripts/vps/README.md scripts/tests/test_batch_control_http.py
git commit -m "feat(httpapi): pipelines and draft routes for composing jobs from the phone

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## After the tasks (controller, not a subagent)

- Whole-branch review, PR, and — on the user's go — merge, which auto-deploys and restarts
  `motion-bot`.
- On the VPS through the tunnel (with a `User-Agent` header; Cloudflare 1010 otherwise): `make
  api-smoke`; `GET /v1/pipelines`; compose a draft from already-uploaded material, add to batch,
  `POST /v1/draft/validate` and record its wall time; clear. Confirm Telegram's own draft
  (`batch/tg-<chat>.draft.json`) is unchanged by all of it, and `motion-bot` RSS before/after in
  `scripts/vps/README.md`.
