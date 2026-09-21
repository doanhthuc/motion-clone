# Control-plane API, slice 4 (run a job from the phone: Phase A, try-on, rent panel, confirm) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The phone can take its validated draft through the whole paid path: start Phase A (local
try-on), look at the try-on images and regenerate one, read the rent panel (RunPod stock/price, Vast
quote), and **confirm** — renting a pod — without ever causing a second pod or a duplicate run.

**Architecture:** One run slot, shared with Telegram (spec §5.8). The app's jobs are written into the
Telegram chat's manifest `batch/tg-<chat>.yaml` and go through the bot's own `_do_phase_a`,
`_do_confirm`, `_do_resume` and `_regen_tryon`, which now return an `Outcome` instead of `None`. A
new `AppRuns` class in `bot.py` is the phone's entry point: it takes `control.BOT_LOCK` (also held by
the bot loop around `handle()` and the tick round), checks the panel token and the idempotency
record, calls the bot function with a Telegram proxy that suppresses refusals, and turns the
`Outcome` into an HTTP response. `scripts/httpapi/server.py` exposes it.

**Tech Stack:** Python 3 stdlib only; `unittest`.

**Spec:** `docs/superpowers/specs/2026-09-21-vps-control-plane-api-design.md` — §5.8 (one run slot,
binding), §5.3 run routes, §5.5 money protection (amended), §5.6 status codes, §4.3 concurrency, §7
required tests.

## Controller decisions

- **Nothing moves out of `bot.py`.** The four functions stay where 58 + 120 test patches
  (`tgbot.bot.start_drain`, `tgbot.bot.drain_running`, …) can reach them. They gain return values and
  two optional parameters; Telegram's behaviour and messages do not change.
- **`Outcome(ok, code, message)`**, truthy iff `ok`, lives in `control/runs.py`. `_do_resume`'s
  existing callers test its truthiness (`if _do_resume(...)`, `assertTrue(started)`), so they keep
  working unchanged.
- **Refusals from an app call are not sent to Telegram;** successes are (`🚀 Started`, progress,
  try-on previews) because the run is shared. The marker is a proxy, `_AppTg(tg)`, with
  `app_origin = True`; `_refuse(tg, chat_id, code, text, **kw)` sends only when the marker is absent.
- **Injected jobs.** `_do_phase_a(..., jobs=None)` and `_do_confirm(..., jobs=None)`: when `jobs` is
  given (the app's), it replaces `_jobs_for(chat_id)`, the unassigned-files (`_PENDING`) gate and the
  `_LAST_VALIDATE` gate are skipped (the caller has already required the app draft to be validated),
  and the Telegram teardown (`_freeze_panel`, popping `_STATE`, `_BASKET`, `_PENDING`,
  `_LAST_VALIDATE`, `_CONFIRM_WARNED`, `_ALBUM_KEY`, `_FIDELITY`, `_STRIP`) is skipped — the Telegram
  draft is not what was submitted. `.last.json` is still written (it records the slot's last run).
  `_draft_manifest` gains the same `jobs=None` parameter so the Vast check in `_do_confirm` measures the
  app's jobs, not Telegram's draft.
- **Which body confirm runs:** if Phase A finished and offered a rental on the current manifest
  (`_PHASE_A_OFFERED.get(chat) == _run_token(chat)`), `_do_resume(..., gpu_provider=provider)` — what
  Telegram's `pa:spend` button does; otherwise `_do_confirm(..., jobs=app_jobs, gpu_provider=provider,
  phase_a_choice=tryon)`.
- **The try-on chooser.** When `_do_confirm` would offer "reuse / re-run try-on" and the call is from
  the app, it returns `Outcome(False, "choice_required", …)` instead of sending buttons; the phone
  confirms again with `"tryon": "reuse"|"rerun"`. That first call rewrote the manifest, so its
  response carries a fresh `panel_token`.
- **`panel_token` = `f"{_run_token(chat)}.{draft_generation}"`.** Checked under `BOT_LOCK` before
  anything is called. Two confirms tapped at once: the second waits for the lock, then finds the
  manifest rewritten (and the app draft cleared) and gets `409 stale_panel`.
- **Regenerate uses `run_token` = `_run_token(chat)` alone** (draft edits don't change a finished
  Phase A), exposed by `GET /v1/runs/{id}/tryon`.
- **Idempotency** (`Idempotency-Key` header, required on phase-a, confirm, regen): a record is written
  as `pending` under `BOT_LOCK` **before** the call and replaced by the final response after it. A
  replay returns the stored response; a replay of a still-`pending` key (the call crashed midway)
  returns `409 outcome_unknown` telling the phone to read `GET /v1/runs/{id}` — never a second
  attempt. `503 bot_busy` (lock timeout) is not recorded, so it can be retried with the same key.
  Records live in `batch/idempotency/`, pruned after 24 h by `_tick_staging_prune`.
- **`BOT_LOCK` is separate from slice 3's `control.LOCK`.** The bot holds `BOT_LOCK` through a whole
  `handle()` (which can include a 2 GB staging copy or a 120 s validate); sharing the lock would stall
  the phone's draft edits behind that. Order is always `BOT_LOCK` → `control.LOCK`, never the
  reverse. The HTTP side waits at most `BOT_LOCK_TIMEOUT_SEC = 60` (under Cloudflare's 100 s).
- **Rent panel network calls run outside `BOT_LOCK`.** `runpodctl` (30 s timeout, 60 s cache) and the
  Vast quote (measured 4 s, 120 s timeout, 60 s cache) touch only process-global caches. The lock is
  held only to read the token and `_PHASE_A_OFFERED`. The shared Vast `_last` quote can be replaced by
  a Telegram panel read in between; `_vast_refusal` then refuses (safe direction — never overspends).
- **Out of this slice:** choosing the RunPod GPU type (`.env` `GPU`, global → slice 5), retrying a
  try-on with a different provider, `/kill` and resume-after-failure from the phone (slice 5).

## Global Constraints

- `start_drain` is called in exactly two places, both in `scripts/tgbot/bot.py` (`_do_confirm`,
  `_do_resume`); `start_phase_a` in exactly two, both in `bot.py` (`_regen_tryon`,
  `_start_phase_a_and_report`). None in `scripts/control/` or `scripts/httpapi/`. A test enforces it.
- No refusal text visible in Telegram changes; `scripts/tests/test_batch_bot.py` stays green
  **unchanged**.
- Every call into the four bot functions from the HTTP thread happens under `control.BOT_LOCK`.
- Idempotency-Key required on POST phase-a / confirm / regen; a key is never executed twice.
- No absolute paths in any response. No time-derived fields in bodies that carry ETags.
- Status codes: `400` bad body / missing Idempotency-Key; `401`; `404` unknown run id, index, or
  image; `409` world-state refusal (`stale_panel`, `migration`, `drain_running`, `phase_a_running`,
  `already_running`, `choice_required`, `outcome_unknown`, `vast_refused`, `too_late`, `not_ready`,
  `nothing_to_resume`, `not_local`, `dry_run`); `422` draft refusal (`nothing_to_run`,
  `not_validated`, `invalid`, `manifest_error`); `503 bot_busy`; `500` logged and opaque.
- `scripts/control/` imports nothing from Telegram.
- New test files `scripts/tests/test_batch_control_*.py`; `make batch-test` and
  `motions-studio/setup/scrub-secrets.sh --check` pass before each commit.
- English; comments explain *why* and never mention tasks, reviews or fix rounds; no `#region ALD`.
- Commit trailer exactly `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

---

### Task 1: `Outcome`, `BOT_LOCK` and the idempotency store

**Files:**
- Modify: `scripts/control/__init__.py`, `scripts/control/runs.py`
- Create: `scripts/control/idempotency.py`
- Test: `scripts/tests/test_batch_control_idempotency.py` (new), `scripts/tests/test_batch_control_runs.py` (append)

**Interfaces — Produces:**
- `control.BOT_LOCK: threading.RLock`, `control.BOT_LOCK_TIMEOUT_SEC = 60`
- `control.runs.Outcome(ok: bool, code: str, message: str = "")`, frozen dataclass, `__bool__ → ok`
- `control.runs.OUTCOME_STATUS: dict[str, int]` and `control.runs.status_for(outcome) -> int`
  (ok → 202; unknown refusal code → 409)
- `control.idempotency.IdempotencyStore(root: Path, ttl_sec: int = 24*3600)` with
  `begin(scope, key) -> tuple[int, dict] | None`, `finish(scope, key, status, body)`,
  `forget(scope, key)`, `prune(now) -> int`; `IdempotencyError(code, message)`

- [ ] **Step 1: Write the failing tests**

`scripts/tests/test_batch_control_idempotency.py`:

```python
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from control.idempotency import IdempotencyError, IdempotencyStore


class TestIdempotencyStore(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.store = IdempotencyStore(self.tmp / "idem")

    def test_first_begin_returns_none_and_replay_returns_the_stored_response(self):
        self.assertIsNone(self.store.begin("confirm", "k1"))
        self.store.finish("confirm", "k1", 202, {"outcome": "started"})
        self.assertEqual(self.store.begin("confirm", "k1"), (202, {"outcome": "started"}))

    def test_a_pending_key_is_outcome_unknown_not_a_second_attempt(self):
        self.assertIsNone(self.store.begin("confirm", "k1"))
        status, body = self.store.begin("confirm", "k1")
        self.assertEqual((status, body["error"]["code"]), (409, "outcome_unknown"))

    def test_scopes_do_not_collide(self):
        self.assertIsNone(self.store.begin("confirm", "k"))
        self.assertIsNone(self.store.begin("phase-a", "k"))

    def test_forget_allows_a_retry(self):
        self.store.begin("confirm", "k")
        self.store.forget("confirm", "k")
        self.assertIsNone(self.store.begin("confirm", "k"))

    def test_bad_keys(self):
        for key in ("", None, "x" * 201, 5):
            with self.subTest(key=key), self.assertRaises(IdempotencyError):
                self.store.begin("confirm", key)

    def test_key_is_hashed_so_no_client_text_reaches_the_filesystem(self):
        self.store.begin("confirm", "../../etc/passwd")
        names = [p.name for p in (self.tmp / "idem").iterdir()]
        self.assertEqual(len(names), 1)
        self.assertNotIn("..", names[0])

    def test_prune_removes_only_expired_records(self):
        self.store.begin("confirm", "old"); self.store.finish("confirm", "old", 202, {})
        self.store.begin("confirm", "new"); self.store.finish("confirm", "new", 202, {})
        old = next(p for p in (self.tmp / "idem").iterdir()
                   if json.loads(p.read_text())["key_hint"] == "old")
        past = time.time() - 25 * 3600
        os.utime(old, (past, past))
        self.assertEqual(self.store.prune(time.time()), 1)
        self.assertIsNone(self.store.begin("confirm", "old"))
        self.assertIsNotNone(self.store.begin("confirm", "new"))
```

Append to `scripts/tests/test_batch_control_runs.py`:

```python
class TestOutcome(unittest.TestCase):
    def test_truthiness_and_status(self):
        import control
        from control.runs import Outcome, status_for
        self.assertTrue(Outcome(True, "started"))
        self.assertFalse(Outcome(False, "migration", "wait"))
        self.assertEqual(status_for(Outcome(True, "queued")), 202)
        self.assertEqual(status_for(Outcome(False, "not_validated")), 422)
        self.assertEqual(status_for(Outcome(False, "no_such_code")), 409)
        self.assertEqual(status_for(Outcome(False, "not_found")), 404)
        with control.BOT_LOCK:
            with control.BOT_LOCK:
                pass
        self.assertEqual(control.BOT_LOCK_TIMEOUT_SEC, 60)
```

- [ ] **Step 2: Run to verify they fail** —
`python3 -m unittest discover -s scripts/tests -p 'test_batch_control_idempotency.py'` → ImportError.

- [ ] **Step 3: Implement**

Append to `scripts/control/__init__.py`:

```python
# Spec §5.8: the bot loop holds this around each handle() and each tick round,
# and the HTTP thread around every call into the bot's run functions, so the
# check-then-start of a paid drain can never interleave. Separate from LOCK:
# the bot holds this one through slow Telegram work (a 2 GB staging copy, a
# 120 s validate), which must not stall the phone's draft edits.
BOT_LOCK = threading.RLock()
# The HTTP side gives up after this and answers 503: Cloudflare cuts a request
# that sends nothing for 100 s.
BOT_LOCK_TIMEOUT_SEC = 60
```

Append to `scripts/control/runs.py`:

```python
@dataclass(frozen=True)
class Outcome:
    """What a run action did. Truthy when it started or queued something, so
    callers that used to test a bool (`if _do_resume(...)`) keep working."""
    ok: bool
    code: str
    message: str = ""

    def __bool__(self) -> bool:
        return self.ok


OUTCOME_STATUS = {
    "not_found": 404,
    "nothing_to_run": 422, "not_validated": 422, "invalid": 422, "manifest_error": 422,
    "bot_busy": 503,
}


def status_for(outcome: Outcome) -> int:
    if outcome.ok:
        return 202
    return OUTCOME_STATUS.get(outcome.code, 409)
```

(add `from dataclasses import dataclass` if missing).

Create `scripts/control/idempotency.py`:

```python
"""Idempotency-Key records for the money-spending routes (spec §5.5).

A phone retries. A record is written as "pending" before the action runs and
replaced by the response after it, so a retry replays the answer instead of
acting twice — and a retry of a call that died midway gets "outcome unknown",
never a second attempt at renting a pod.
"""
from __future__ import annotations

import hashlib
import json
import threading
import uuid
from pathlib import Path

MAX_KEY_LEN = 200
_LOCK = threading.Lock()


class IdempotencyError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class IdempotencyStore:
    def __init__(self, root: Path, ttl_sec: int = 24 * 3600):
        self.root, self.ttl_sec = root, ttl_sec

    def _path(self, scope: str, key) -> Path:
        if not isinstance(key, str) or not key or len(key) > MAX_KEY_LEN:
            raise IdempotencyError("bad_request",
                                   f"Idempotency-Key must be 1-{MAX_KEY_LEN} characters")
        digest = hashlib.sha256(f"{scope}\0{key}".encode()).hexdigest()[:40]
        return self.root / f"{scope}-{digest}.json"

    def _write(self, path: Path, record: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(record), encoding="utf-8")
        tmp.replace(path)

    def begin(self, scope: str, key) -> tuple[int, dict] | None:
        """None: first time — a pending record now exists, go ahead.
        Otherwise the (status, body) to answer with instead of acting."""
        path = self._path(scope, key)
        with _LOCK:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                self._write(path, {"state": "pending", "key_hint": key[:40]})
                return None
            except (OSError, ValueError):
                record = {"state": "pending"}
        if record.get("state") == "done":
            return record["status"], record["body"]
        return 409, {"error": {"code": "outcome_unknown",
                               "message": "an earlier request with this Idempotency-Key did not "
                                          "finish; read the run's status before trying again"}}

    def finish(self, scope: str, key: str, status: int, body: dict) -> None:
        with _LOCK:
            self._write(self._path(scope, key), {"state": "done", "status": status, "body": body,
                                                  "key_hint": key[:40]})

    def forget(self, scope: str, key: str) -> None:
        with _LOCK:
            self._path(scope, key).unlink(missing_ok=True)

    def prune(self, now: float) -> int:
        removed = 0
        for path in self.root.glob("*.json") if self.root.is_dir() else []:
            try:
                if now - path.stat().st_mtime > self.ttl_sec:
                    path.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                continue
        return removed
```

- [ ] **Step 4: Run tests** — the two files, then `make batch-test`.

- [ ] **Step 5: Commit** — `feat(control): outcomes, the bot lock and idempotency records`

---

### Task 2: The four bot run functions return outcomes and accept the app's jobs

**Files:**
- Modify: `scripts/tgbot/bot.py` — `_do_phase_a` (~5606), `_do_confirm` (~5686), `_do_resume` (~5298),
  `_regen_tryon` (~3024), `_draft_manifest` (~5516); new `_refuse`, `_plain`, `_AppTg`
- Test: `scripts/tests/test_batch_control_botruns.py` (new)

**Interfaces:**
- Consumes: `control.runs.Outcome`.
- Produces (used by Task 3):
  - `_AppTg(tg)` — forwards every attribute to `tg`; class attribute `app_origin = True`
  - `_refuse(tg, chat_id, code: str, text: str, **send_kwargs) -> Outcome` — sends `text` with
    `send_kwargs` unless `getattr(tg, "app_origin", False)`; returns `Outcome(False, code, _plain(text))`
  - `_plain(text: str) -> str` — removes HTML tags (including `<tg-emoji …>…</tg-emoji>` wrappers,
    keeping their inner text), `html.unescape`, strips
  - `_do_phase_a(tg, chat_id, *, dry_run, jobs=None) -> Outcome` — ok code `"started"`
  - `_do_confirm(tg, chat_id, *, dry_run, phase_a_choice=None, gpu_provider=None, jobs=None) -> Outcome`
    — ok codes `"started"`, `"queued"`, `"already_running"`; Telegram's chooser branch returns
    `Outcome(False, "choice_offered")` (buttons sent as today); for an `_AppTg` it returns
    `Outcome(False, "choice_required", "try-on already ran for {reusable} of {total} job(s) — confirm again with tryon=reuse to keep it, or tryon=rerun to redo it")`
    without sending anything
  - `_do_resume(...) -> Outcome` — ok code `"started"`
  - `_regen_tryon(...) -> Outcome` — ok code `"started"`
  - `_draft_manifest(chat_id, jobs=None)` — `jobs` replaces `_jobs_for(chat_id)`

**Refusal codes** (every existing `tg.send_message(chat_id, <text>, …); return` in these functions
becomes `return _refuse(tg, chat_id, <code>, <text>, …)` with the **exact same text and kwargs**):

| Function | Gate | Code |
|---|---|---|
| `_do_phase_a` | dry run | `dry_run` |
| | `migration_running()` | `migration` |
| | no complete job | `nothing_to_run` |
| | unassigned files (Telegram only — skipped when `jobs` is given) | `unassigned_files` |
| | busy: drain | `drain_running` |
| | busy: Phase A | `phase_a_running` |
| `_do_confirm` | migration | `migration` |
| | Phase A running | `phase_a_running` |
| | no complete job | `nothing_to_run` |
| | unassigned files (skipped when `jobs` given) | `unassigned_files` |
| | `_render_and_validate` failed (Telegram only) | `invalid` |
| | validated is False (Telegram only) | `invalid` |
| | `_vast_queue_refusal` | `vast_refused` |
| | `_vast_refusal` | `vast_refused` |
| `_do_resume` | migration | `migration` |
| | `busy(...)` | `already_running` |
| | no batch in journal | `nothing_to_resume` |
| | `ManifestError` | `manifest_error` |
| | `_vast_refusal` | `vast_refused` |
| `_regen_tryon` | stale token | `stale_panel` |
| | dry run | `dry_run` |
| | drain running | `too_late` |
| | Phase A running | `phase_a_running` |
| | `ManifestError` | `manifest_error` |
| | bad index | `not_found` |
| | try-on no longer local | `not_local` |
| | later stage already recorded | `too_late` |
| | not run yet | `not_ready` |

Where `_do_confirm`'s `_render_and_validate` already sent its own message and the function returned,
return `Outcome(False, "invalid", "")` without sending again. Where a function returned silently
(no message), return a refusal Outcome with a sensible code and no send.

Every normal end of these functions returns `Outcome(True, <code>)`. `_do_resume` keeps returning
`False`-y/truthy exactly where it returned `False`/`True`.

**`jobs` injection** (only when `jobs is not None`):
- `_do_phase_a`: `queued = jobs`; skip the `_PENDING` gate.
- `_do_confirm`: `queued = jobs`; skip the `_PENDING` gate; skip the whole `_LAST_VALIDATE` block
  (both the "None → `_render_and_validate`" and "False → refuse" branches); pass `jobs` to
  `_draft_manifest` in the Vast check; skip `_freeze_panel` and every Telegram-draft pop in the
  teardown (`_PENDING`, `_BASKET`, `_STATE`, `_LAST_VALIDATE`, `_CONFIRM_WARNED`, `_ALBUM_KEY`,
  `_FIDELITY`, `_STRIP`) and the "running without N unassigned file(s)" line; still write
  `.last.json` with `queued`.
- Comment at the top of each function: the phone's draft is not the Telegram draft (spec §5.8), which
  is why these are skipped.

- [ ] **Step 1: Write the failing tests**

Create `scripts/tests/test_batch_control_botruns.py`. Build it on the same fixture pattern
`test_batch_bot.py` uses for confirm tests (read `TestRunStartsPhaseAFirst` (~6209),
`TestConfirmAsksBeforeReusingTryon` (~5856) and `test_confirm_calls_start_drain_once_with_dry_run_false`
(~3495) first, and copy their setUp: a fake `tg`, `bot.ROOT` pointed at a temp dir, and
`mock.patch("tgbot.bot.start_drain")`, `…drain_running`, `…phase_a_running`, `…migration_running`,
`…stock_at_cached`, `…volume_datacenter`, `…_start_progress` as those tests do). Tests, each asserting
the real behaviour:

- `test_refusal_is_sent_to_telegram_and_returned` — Telegram `tg`, migration running →
  `_do_confirm` returns `Outcome(False, "migration", …)`, and `tg` got exactly the migration text.
- `test_app_refusal_is_returned_but_not_sent` — same with `bot._AppTg(tg)` → same Outcome, `tg` got
  nothing.
- `test_plain_strips_html` — `bot._plain('⚠️ <b>Not queued</b> — a &amp; b <tg-emoji emoji-id="1">🚀</tg-emoji>')`
  → `"⚠️ Not queued — a & b 🚀"`.
- `test_confirm_with_app_jobs_starts_once_and_leaves_the_telegram_draft_alone` — put a Telegram draft
  in `bot._STATE[ME]` and a different job list in `jobs`; `_do_confirm(_AppTg(tg), ME, dry_run=False,
  jobs=app_jobs)` → `Outcome(True, "started")`, `start_drain` called once with
  `batch/tg-<ME>.yaml`, the manifest on disk names the app job's files, `bot._STATE[ME]` is the same
  object as before, `_freeze_panel` not called, `.last.json` holds the app jobs.
- `test_confirm_with_app_jobs_skips_the_validate_gate` — `_LAST_VALIDATE[ME] = False` and app `jobs`
  → still starts (the gate is skipped; the caller checks the app draft).
- `test_confirm_while_a_drain_runs_queues_and_does_not_rent` — `drain_running` True → Outcome
  `(True, "queued")`, `start_drain` not called, the mailbox `tg-<ME>.next.yaml` exists.
- `test_app_chooser_returns_choice_required_without_buttons` — patch `tgbot.bot._preserved_tryon` to
  return `(1, 2)` → `Outcome(False, "choice_required", …)`, message contains `"1 of 2"`, `tg` sent
  nothing, `start_drain` not called.
- `test_phase_a_with_app_jobs` — `_do_phase_a(_AppTg(tg), ME, dry_run=False, jobs=app_jobs)` →
  `(True, "started")`, `start_phase_a` called once with `tg-<ME>.yaml`, Telegram draft untouched.
- `test_phase_a_busy_refusal_codes` — `busy` via `phase_a_running` True → code `phase_a_running`.
- `test_resume_outcome_truthiness` — `_do_resume` with migration → falsy Outcome code `migration`;
  normal → truthy `(True, "started")`.
- `test_regen_stale_token` — `_regen_tryon(_AppTg(tg), ME, "0", "not-the-token", dry_run=False)` →
  `(False, "stale_panel")`, nothing sent.

- [ ] **Step 2: Run to verify they fail.**

- [ ] **Step 3: Implement** as specified above. Import `Outcome` from `control.runs`. Change only
  what the table and the injection list name; keep every message text byte-identical.

- [ ] **Step 4: Run tests.** `test_batch_control_botruns.py` passes; `test_batch_bot.py` 691 OK and
  `git diff --stat main -- scripts/tests/test_batch_bot.py` is empty; `make batch-test` OK.

- [ ] **Step 5: Commit** — `refactor(tgbot): run functions return outcomes and accept the app's jobs`

---

### Task 3: `AppRuns` — Phase A and confirm for the phone, under `BOT_LOCK`; the bot loop takes the lock

**Files:**
- Modify: `scripts/tgbot/bot.py` — new `class AppRuns`, new `_handle_locked`, `_run_ticks`; `main()`
  loop; `_tick_staging_prune` (idempotency sweep)
- Modify: `scripts/control/drafts.py` — new `DraftStore.runnable()`
- Test: `scripts/tests/test_batch_control_botruns.py` (append), `scripts/tests/test_batch_control_drafts.py` (append)

**Interfaces:**
- Consumes: Task 1 (`BOT_LOCK`, `BOT_LOCK_TIMEOUT_SEC`, `Outcome`, `status_for`, `IdempotencyStore`),
  Task 2 (`_AppTg`, outcome-returning functions, `jobs=`), slice 3's `DraftStore`.
- Produces:
  - `DraftStore.runnable() -> tuple[list[Job], bool | None, int]` — `(self._jobs(d), d.validated,
    d.generation)` under `control.LOCK`
  - `AppRuns(tg, chat_id: int, drafts: DraftStore, idem: IdempotencyStore)` with
    - `.run_id -> str` (`_job_manifest_path(chat_id).stem`)
    - `.panel_token() -> str` = `f"{_run_token(chat_id)}.{generation}"`
    - `.phase_a(key) -> tuple[int, dict]`
    - `.confirm(run_id: str, body: dict, key) -> tuple[int, dict]`
  - `_handle_locked(tg, update, **kwargs)` — `with BOT_LOCK: handle(tg, update, **kwargs)`
  - `_run_ticks(tg, chat_id, *, dry_run)` — `with BOT_LOCK:` the six tick calls `main()` makes today,
    in the same order

`AppRuns` method contract (all bodies JSON-safe, no absolute paths):

```text
_locked(): acquire BOT_LOCK with timeout BOT_LOCK_TIMEOUT_SEC; on timeout return
           (503, {"error": {"code": "bot_busy", "message": "the bot is busy — try again in a moment"}})
           (not recorded in idempotency: forget(scope, key) so the same key can retry)

phase_a(key):
  replay = idem.begin("phase-a", key)  -> if not None: return replay
  under BOT_LOCK:
    jobs, validated, _ = drafts.runnable()
    no jobs            -> (422, error nothing_to_run "no complete job in the app's draft yet")
    validated is not True -> (422, error not_validated "validate the draft first (POST /v1/draft/validate)")
    out = _do_phase_a(_AppTg(tg), chat_id, dry_run=False, jobs=jobs)
  response = (202, {"run_id", "outcome": out.code}) if out else (status_for(out), error(out.code, out.message))
  idem.finish("phase-a", key, *response); return response
  (the app draft is NOT cleared — Phase A is not a submission, same as the bot)

confirm(run_id, body, key):
  run_id != self.run_id            -> (404, not_found)
  body.provider not in {"runpod","vast"} -> (400, bad_request)
  body.tryon not in {None,"reuse","rerun"} -> (400, bad_request)
  replay = idem.begin("confirm", key) -> if not None: return replay
  under BOT_LOCK:
    body.panel_token != self.panel_token() -> response (409, stale_panel "the job changed since the panel was read — read it again")
    elif _PHASE_A_OFFERED.get(chat_id) == _run_token(chat_id):
        out = _do_resume(_AppTg(tg), chat_id, _job_manifest_path(chat_id), dry_run=False, gpu_provider=provider)
        if out: _PHASE_A_OFFERED.pop(chat_id, None); drafts.clear()
    else:
        jobs, validated, _ = drafts.runnable()
        (same nothing_to_run / not_validated refusals as phase_a)
        out = _do_confirm(_AppTg(tg), chat_id, dry_run=False, jobs=jobs, gpu_provider=provider, phase_a_choice=tryon)
        if out: drafts.clear()
    response = (202, {"run_id", "outcome": out.code}) if out
               else (status_for(out), error(out.code, out.message) + {"panel_token": self.panel_token()} when out.code == "choice_required")
  idem.finish("confirm", key, *response); return response
  Any exception after begin(): leave the record pending (the replay then says outcome_unknown), re-raise.
```

`main()`: replace `handle(tg, update, …)` in the updates loop with `_handle_locked(…)` and the six
tick calls with `_run_ticks(tg, allowed_user_id, dry_run=args.dry_run)`. `get_updates` stays outside
the lock. `_tick_staging_prune` gains a fourth independent sweep:
`IdempotencyStore(ROOT / "batch" / "idempotency").prune(time.time())`, guarded like the other three.

- [ ] **Step 1: Write the failing tests** (append to `test_batch_control_botruns.py`; reuse Task 2's
  fixture; a real `DraftStore` over the temp `batch/` with a fake probe, filled and marked validated
  via `store._load()` / `store._save()`; a real `IdempotencyStore` in the temp dir):

- `test_phase_a_requires_a_validated_draft` → 422 `not_validated`; `start_phase_a` not called.
- `test_phase_a_starts_and_replay_does_not_start_twice` — same key twice → both `(202, …)`,
  `start_phase_a` called once.
- `test_confirm_replayed_with_the_same_key_calls_start_drain_once` (spec §7).
- `test_confirm_with_a_stale_panel_token_is_409` (spec §7) — and `start_drain` not called.
- `test_two_concurrent_confirms_make_one_drain` (spec §7) — two threads, two different keys, the same
  token read before either starts; `start_drain` called once; the other gets 409 `stale_panel`.
  (Make the patched `start_drain` also touch the manifest so the token changes, as a real write does —
  or rely on `drafts.clear()` changing the generation.)
- `test_confirm_after_phase_a_resumes` — set `bot._PHASE_A_OFFERED[ME] = bot._run_token(ME)` →
  `start_drain` called with `resume=True`; `_PHASE_A_OFFERED` no longer has `ME`; app draft cleared.
- `test_confirm_clears_the_app_draft_only_on_success` — a refusal leaves the app draft as it was.
- `test_crash_midway_leaves_the_key_pending` — patched `start_drain` raises → the call raises; the
  same key again → 409 `outcome_unknown`; `start_drain` called once in total.
- `test_bot_busy_is_503_and_retryable` — hold `BOT_LOCK` in another thread with
  `BOT_LOCK_TIMEOUT_SEC` patched to 0.2 → 503 `bot_busy`; release; the same key succeeds.
- `test_confirm_wrong_run_id_is_404`, `test_confirm_bad_provider_is_400`.
- `test_handle_and_ticks_hold_the_bot_lock` — patch `tgbot.bot.handle` / one tick function to record
  whether another thread can `BOT_LOCK.acquire(blocking=False)` while they run (acquire and release in
  that other thread) → it cannot.
- In `test_batch_control_drafts.py`: `test_runnable` — returns the jobs, the verdict and the generation.

- [ ] **Step 2: Run to verify they fail.** **Step 3: Implement.** **Step 4:** focused tests, then
  `make batch-test`; `test_batch_bot.py` unchanged. **Step 5: Commit** —
  `feat(tgbot): AppRuns runs the phone's draft through the bot's own gates`

---

### Task 4: Rent panel data and try-on previews/regenerate for the phone

**Files:**
- Modify: `scripts/tgbot/bot.py` — `AppRuns.rent_panel`, `AppRuns.tryon`, `AppRuns.tryon_image`,
  `AppRuns.regen`; new helper `_rent_panel_data`
- Test: `scripts/tests/test_batch_control_botruns.py` (append)

**Interfaces — Produces:**
- `AppRuns.rent_panel(run_id, *, force: bool) -> tuple[int, dict]` — body:

```json
{"run_id": "tg-123", "panel_token": "1726912345000000000.7",
 "after_phase_a": true, "jobs": 2, "estimate_min": 68,
 "runpod": {"gpu": "NVIDIA GeForce RTX 5090", "datacenter": "EU-RO-1",
            "stock": "High", "usd_per_hr": 0.99, "sold_out": false},
 "vast": {"enabled": true, "usd_per_hr": 0.42, "session_usd": 1.1,
          "blockers": [], "can_spend": true}}
```

  Built by `_rent_panel_data(chat_id, *, force, manifest)` from the same primitives
  `_offer_run_confirm` and `_offer_vast_panel` use (`env_get(".env","GPU")`/`_PRIMARY_GPU_ID`,
  `volume_datacenter`, `stock_at`/`stock_at_cached`, `_gpu_price`, `vast_download_gb`,
  `vast_build_view`, `vast_fetch_quote`, `vast_credit`, `_vast_enabled`). **Do not change
  `_offer_run_confirm` or `_offer_vast_panel`.** `stock` is the home-datacenter stock status or `null`;
  failures of `runpodctl` fail open exactly as the Telegram panel does (`{}` → `sold_out: true` if no
  home entry). Vast `blockers` are plain text (use `_plain`). `manifest` is the on-disk slot manifest
  when `after_phase_a` (`_PHASE_A_OFFERED.get(chat) == _run_token(chat)`), otherwise
  `_draft_manifest(chat_id, jobs=app_jobs)`. The network calls run **outside** `BOT_LOCK`; the lock is
  held only to read `after_phase_a`, the token and the app jobs. `jobs`/`estimate_min` from the app
  jobs (or the manifest's runs after Phase A). Unknown `run_id` → 404.
- `AppRuns.tryon(run_id) -> tuple[int, dict]` — `{"run_id", "run_token", "phase_a_running": bool,
  "previews": [{"index": "0", "run": "<run id>", "status": "done"|"error"|"running"|"pending",
  "has_image": bool}]}` for the slot manifest, derived the way `_deliver_tryon_previews` finds each
  run's local try-on stage and file (read it; reuse its helpers such as `_local_tryon_stage`).
  `index` is the string `_regen_tryon` takes. Under `BOT_LOCK` (short: journal reads only).
- `AppRuns.tryon_image(run_id, index) -> Path | None` — the current try-on image; `None` unless the
  resolved path is inside `ROOT / "out"` and is a file.
- `AppRuns.regen(run_id, index, body, key) -> tuple[int, dict]` — `body.run_token` required;
  `idem.begin("regen", key)`; under `BOT_LOCK`: `out = _regen_tryon(_AppTg(tg), chat_id, index,
  body["run_token"], dry_run=False)`; response `(202, {"run_id", "outcome"})` or the refusal;
  `idem.finish`.

- [ ] **Step 1: Write the failing tests** (patch `tgbot.bot.volume_datacenter`,
  `tgbot.bot.stock_at_cached`, `tgbot.bot.stock_at`, `tgbot.bot.vast_fetch_quote`,
  `tgbot.bot.vast_credit` as the existing panel tests do — read `TestRunConfirmPanelIsParameterised`
  (~6524) and `TestVastConfirm` (~7687) for the shapes of their return values):
- `test_rent_panel_in_stock` — home DC stock "High" → `sold_out` false, price from `_gpu_price`.
- `test_rent_panel_sold_out_and_runpodctl_failure_fail_open` — `stock_at_cached` raises
  `RuntimeError` → 200 with `sold_out: true`, no 500.
- `test_rent_panel_token_matches_what_confirm_accepts` — `panel_token` from the panel works in
  `confirm`.
- `test_rent_panel_network_runs_outside_the_bot_lock` — the patched `stock_at_cached` checks from a
  second thread that `BOT_LOCK` is free.
- `test_rent_panel_vast_blockers_are_plain_text`.
- `test_tryon_lists_previews_and_image_path_stays_in_out` — journal with one done try-on whose file
  exists under `out/` → `has_image` true and `tryon_image` returns it; a journal entry pointing
  outside `out/` → `None`.
- `test_regen_starts_once_per_key_and_refuses_a_stale_run_token`.
- `test_no_absolute_paths_in_any_body` — `json.dumps` of every body above does not contain the temp
  root.

- [ ] **Steps 2-5** as usual. Commit — `feat(tgbot): rent panel and try-on previews for the phone`

---

### Task 5: HTTP routes, wiring, the call-site invariant, docs

**Files:**
- Modify: `scripts/httpapi/server.py`, `scripts/tgbot/bot.py` (`_start_control_api`),
  `scripts/vps/README.md`
- Create: `scripts/tests/test_batch_control_invariants.py`
- Test: `scripts/tests/test_batch_control_http.py` (append)

Routes (`s.app_runs` is the `AppRuns`; when it is `None` every route below answers
`503 runs_unavailable`):

| Method + path | Calls | Notes |
|---|---|---|
| `POST /v1/runs/phase-a` | `app_runs.phase_a(key)` | `Idempotency-Key` required |
| `GET /v1/runs/{id}/rent-panel[?force=1]` | `app_runs.rent_panel(id, force=…)` | |
| `POST /v1/runs/{id}/confirm` `{provider, panel_token, tryon?}` | `app_runs.confirm(id, body, key)` | `Idempotency-Key` required |
| `GET /v1/runs/{id}/tryon` | `app_runs.tryon(id)` | |
| `GET /v1/runs/{id}/tryon/{index}` | `send_file(app_runs.tryon_image(id, index))` | 404 when `None` |
| `POST /v1/runs/{id}/tryon/{index}/regen` `{run_token}` | `app_runs.regen(id, index, body, key)` | `Idempotency-Key` required |

- `Idempotency-Key` missing → `400 bad_request`; `IdempotencyError` → 400. Map through the existing
  error path (add `IdempotencyError` to `_handle`'s domain-exception tuple).
- Route matching must not disturb `GET /v1/runs` and `GET /v1/runs/{id}`; `POST /v1/runs/phase-a` is
  distinguished by method. The query string (`?force=1`) is parsed with `urllib.parse.parse_qs`.
- `make_server(..., app_runs=None)`; `bot._start_control_api` builds
  `AppRuns(tg, chat_id, server.drafts, IdempotencyStore(ROOT / "batch" / "idempotency"))` after
  `make_server` and before `start_in_thread`, and assigns `server.app_runs`.

`test_batch_control_invariants.py` — parse every `.py` under `scripts/` except `scripts/tests/` with
`ast`, collect `Call` nodes whose callee name is `start_drain` / `start_phase_a` (bare name or
attribute), with the enclosing function name, and assert:
- `start_drain`: exactly `{("tgbot/bot.py", "_do_confirm"), ("tgbot/bot.py", "_do_resume")}`, one call each;
- `start_phase_a`: exactly `{("tgbot/bot.py", "_regen_tryon"), ("tgbot/bot.py", "_start_phase_a_and_report")}`;
- nothing under `control/` or `httpapi/` calls either.

HTTP tests (fake `app_runs` recording calls and returning canned `(status, body)`): each route reaches
the right method with the right arguments; missing key → 400; `None` app_runs → 503; a
`(202, …)` passes through; `GET …/tryon/{index}` streams an image file with 200 and 404s on `None`;
a POST error closes the connection.

README "Phone API" section: the slice-4 routes table, the one-run-slot rule (the phone's run *is* the
Telegram chat's run; Telegram shows its progress), `Idempotency-Key`, `panel_token`, the
`reuse`/`rerun` choice, `503 bot_busy`.

Commit — `feat(httpapi): run the phone's draft — phase A, try-on, rent panel, confirm`

---

## After the tasks (controller)

Whole-branch review (most capable model; money path). PR. Merge only on the user's go. Live check on
the VPS **without renting**: `make api-smoke`; `GET /v1/runs/tg-<chat>/rent-panel` (reads stock and a
Vast quote — free); `POST /v1/runs/phase-a` only if the user agrees to spend Gemini quota; a confirm
with a deliberately stale `panel_token` → 409 and no pod (verify with `runpodctl pod list`); a
replayed Idempotency-Key. No real confirm unless the user asks for one.
