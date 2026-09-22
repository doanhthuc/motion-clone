# Control-plane API slice 5 (pod: kill, resume, GPU stock and choice, balance, migrate) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The phone can stop a run, retry a failed rental, see GPU stock / balance / the pod, pick the GPU, and start a volume migration — through the same bot functions Telegram uses, behind the slice-4 lock and idempotency machinery.

**Architecture:** A new `AppPod` class in `scripts/tgbot/bot.py`, beside `AppRuns`, sharing its `BOT_LOCK` discipline and `IdempotencyStore`. `_do_kill` and `_start_migration` are converted to return `Outcome` (every `tg.send_message(...); return` becomes `return _refuse(...)`, texts unchanged), exactly as slice 4 did to `_do_confirm`. The read halves of `_report_gpu_stock` / `_report_balance` are mirrored as JSON-returning `_gpu_stock_data` / `_balance_data` (the `_rent_panel_data` precedent: Telegram's functions are not touched). HTTP routes are added to `scripts/httpapi/server.py`.

**Tech Stack:** Python 3 stdlib only, `unittest`. No new dependency.

**Spec:** `docs/superpowers/specs/2026-09-21-vps-control-plane-api-design.md` — §5.9 (this slice's decisions, already written), §5.5, §5.6, §5.8. Read §5.9 first.

## Global Constraints

- **Money and data safety invariants (grep-tested).** `start_drain` keeps exactly two call sites and `start_phase_a` exactly two (slice 4's invariant, unchanged). NEW: neither `make gpu-destroy` / `gpu-destroy` nor `volume_migrate` appears as a string in any `.py` under `scripts/control/` or `scripts/httpapi/`. `_do_kill` is called from exactly two places in `scripts/tgbot/bot.py`: the Telegram callback (`_CB_KILL_GO`) and `AppPod`'s kill worker.
- **No test, no verification step, ever calls the real `volume_migrate.py`, `make gpu-destroy`, `runpodctl`, `vastai` or `start_drain`.** Tests patch `subprocess.run` / `subprocess.Popen` / `tgbot.bot.start_drain` / `stock_at` / `stock_at_cached` / `account_balance` / `vast_credit` by name in `tgbot.bot`. A test that could reach one unpatched is a defect.
- **Telegram behaviour does not change.** Every message text, keyword argument and gate order in `_do_kill`, `_start_migration`, `_ask_kill`, `_ask_migrate`, `_report_gpu_stock`, `_report_balance` stays byte-identical. `scripts/tests/test_batch_bot.py` stays green with no edits to it.
- **Lock discipline.** Every `AppPod` call that reads or changes bot state holds `BOT_LOCK` (via the shared `_bot_locked()` helper); order is always `BOT_LOCK` → `control.LOCK`. Network calls (`stock_at*`, `volume_datacenter`, `account_balance`, `vast_credit`) run **outside** `BOT_LOCK`. `BOT_LOCK_TIMEOUT_SEC` (60) applies; a timeout is `503 bot_busy`, never recorded in the idempotency store (`forget`).
- **Idempotency.** `kill`, `resume` and `migrate` take an `Idempotency-Key` (400 if missing, checked in the HTTP layer before any call). The record is `pending` before acting (`idem.begin`), finished after (`idem.finish`). `migrate/ask`, `PUT /pod/gpu` and every GET take none.
- **No absolute paths and no HTML in any JSON body.** Every message passes through `_plain` (which already strips tags and `str(ROOT)`).
- **Status codes:** `Outcome.ok` → `202`; refusals → `status_for(outcome)` = `OUTCOME_STATUS.get(code, 409)`. New table entries: `"upstream_unavailable": 502`, `"bad_request": 400`. Unknown id → `404 not_found` (checked before the idempotency store sees the key).
- **Comments:** English, plain (`# ` — no `#region ALD`), explain *why*; keep the existing density. Any number in a comment is one you measured, with the date, or is left out.
- **Every commit:** `motions-studio/setup/scrub-secrets.sh --check` exits 0 first; trailer `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`.

## Controller decisions (rulings on things the spec leaves open)

1. `AppPod` is one class with all pod-side calls; it takes `(tg, chat_id, idem)` and reads `AppRuns`-independent state. It gets the slot's run id from `_job_manifest_path(chat_id).stem`, same as `AppRuns.run_id`. `AppRuns._locked` is refactored into a module-level `_bot_locked()` context manager that both classes use; `AppRuns._locked()` keeps its name and behaviour and delegates.
2. `_do_kill` gets `Outcome` returns: `phase_a_stopped` (ok), `phase_a_finished` (ok=False, benign), `killed` (ok), `destroy_unverified` (ok=False). Its Telegram texts do not change: for the app path a *refusal* is the only thing suppressed by `_AppTg`, and none of `_do_kill`'s messages is a refusal — they are all outcomes the chat should see (a pod that may still bill must reach Telegram). So `_do_kill` uses plain `tg.send_message` as today and only adds `return Outcome(...)`.
3. `_start_migration` gets `Outcome` returns: `migration` (refusal: already running), `launch_failed` (refusal), `started` (ok). The two failure sends become `_refuse(...)`; the success send stays.
4. `GET /v1/pod`'s `migration` field is read from `_migrate_progress_path()` (the file `tick_migration_progress` renders) plus `migration_running()`; it never calls the network.
5. The confirm token for migration is held in `AppPod._migrate_ask: dict | None` (in memory). A new `ask` replaces the old one.
6. `PUT /v1/pod/gpu` has no run/drain guard (parity with Telegram's `_CB_RUN_SWITCH`), only catalog membership. It takes `BOT_LOCK` because `env_set` rewrites `.env`.
7. The optional `gpu` on `confirm` is added to `AppRuns.confirm` in Task 2 as the one edit to slice 4 code; a mismatch is `Outcome(False, "stale_panel", ...)` produced before any spend.

---

### Task 1: `_do_kill` / `_start_migration` return outcomes; `AppPod.kill` and `AppPod.resume`

**Files:**
- Modify: `scripts/tgbot/bot.py` (`_do_kill` ~5141, `_start_migration` ~5248, `AppRuns._locked` ~6667, new `_bot_locked`, new `class AppPod`)
- Modify: `scripts/control/runs.py` (`OUTCOME_STATUS`)
- Create: `scripts/tests/test_batch_control_botpod.py`

**Interfaces:**
- Consumes: `Outcome`, `status_for`, `_AppTg`, `_refuse`, `_plain`, `IdempotencyStore.begin/finish/forget`, `_run_token`, `_do_resume`, `busy`, `drain_running`, `phase_a_running`, `read_provision_failure`, `provision_failure_path`, `_job_manifest_path`, `BOT_LOCK`, `BOT_LOCK_TIMEOUT_SEC`, `_run_error(code, message) -> dict`.
- Produces:
  - `_bot_locked()` — `@contextlib.contextmanager`; yields `None` once `BOT_LOCK` is held, or the ready-made `(503, _run_error("bot_busy", ...))` tuple on timeout. (This is `AppRuns._locked`'s body moved; `AppRuns._locked` becomes `return _bot_locked()`-equivalent delegation with identical behaviour.)
  - `_do_kill(tg, chat_id) -> Outcome`; `_start_migration(tg, chat_id, to_dc) -> Outcome`.
  - `class AppPod: __init__(self, tg, chat_id, idem)`; `run_id` property; `kill(run_id, key) -> tuple[int, dict]`; `resume(run_id, body, key) -> tuple[int, dict]`; attribute `last_kill: dict | None` (`{"at": float, "ok": bool, "code": str, "message": str}`); `_kill_thread: threading.Thread | None`.

- [ ] **Step 1: Write the failing tests** in `scripts/tests/test_batch_control_botpod.py`. Reuse the fixture pattern of `test_batch_control_botruns.py` (temp `ROOT`, fake `tg`, patched globals). Required cases:
  - `test_do_kill_returns_outcomes`: with a Phase A running and no drain → `Outcome(True, "phase_a_stopped")`; `stop_phase_a` returning False → `Outcome(False, "phase_a_finished")`; drain path with `make gpu-destroy` (patched `subprocess.run`, returncode 0) → `Outcome(True, "killed")`; returncode 1 → `Outcome(False, "destroy_unverified")` and the Telegram "may not have worked" message was sent; `TimeoutExpired` → `destroy_unverified`.
  - `test_do_kill_telegram_messages_unchanged`: the exact texts sent on each path equal the pre-change literals (copy them from the current `_do_kill`).
  - `test_start_migration_returns_outcomes`: already running → `Outcome(False, "migration")` and the same text sent to Telegram; `Popen` raising `OSError` → `Outcome(False, "launch_failed")`, marker removed, text unchanged; success → `Outcome(True, "started")`, marker written before `Popen` (assert with a `Popen` stub that checks the marker exists).
  - `test_kill_refuses_when_nothing_is_running_and_never_destroys`: idle (no drain, no Phase A) → `409 nothing_running`; `subprocess.run` mock **not called**; no thread started.
  - `test_kill_wrong_run_id_is_404_and_does_not_touch_idempotency`.
  - `test_kill_starts_a_worker_and_answers_202`: drain "running" (patch `drain_running` True) → `202 {"outcome": "kill_started"}` immediately even when the patched `_do_kill` blocks on an Event; after the Event is set and the thread joined, `last_kill` holds the outcome.
  - `test_kill_worker_rechecks_under_the_lock`: `drain_running` True at request time, False by the time the worker holds the lock → `_do_kill` is **not** called and `last_kill` is `{"ok": False, "code": "nothing_running", ...}`.
  - `test_second_kill_while_one_runs_is_409_kill_in_progress`.
  - `test_kill_replayed_with_same_key_starts_one_worker` (call twice, same key; `_do_kill` patched, called once).
  - `test_kill_holds_bot_lock_while_it_runs`: while the patched `_do_kill` blocks, `BOT_LOCK.acquire(blocking=False)` from the test thread fails.
  - `test_resume_requires_an_outstanding_provision_failure`: no `provision-failed.json` → `409 no_failure`, `_do_resume` not called, `start_drain` not called.
  - `test_resume_stale_run_token_is_409_stale_run` and `test_resume_bad_provider_is_400` and `test_resume_wrong_id_is_404`.
  - `test_resume_starts_once_and_replays`: with a failure file, valid token, `provider="runpod"` → `_do_resume` called once with `gpu_provider="runpod"`, `202`; replay with the same key → same response, `start_drain` (patched) called **once** in total; the failure file is gone afterwards (that is `_do_resume`'s own `clear_provision_failure`).
  - `test_resume_refusal_is_returned_not_sent_to_telegram` (e.g. migration running → `409 migration`, fake `tg` got no message).
  - `test_resume_bot_busy_is_503_and_forgets_the_key`.
- [ ] **Step 2: Run** `python3 -m unittest scripts.tests.test_batch_control_botpod -v` from `scripts/`'s parent per the repo's convention (`make batch-test` discovers it). Expected: FAIL (names missing).
- [ ] **Step 3: Implement.**
  - `_do_kill`: add the `Outcome` returns exactly as in Controller decision 2. No message edit.
  - `_start_migration`: Controller decision 3.
  - `OUTCOME_STATUS`: add `"upstream_unavailable": 502, "bad_request": 400`.
  - `_bot_locked()` and the `AppRuns._locked` delegation (behaviour-identical; slice 4's 42 tests are the proof).
  - `AppPod.kill(run_id, key)`:
    ```python
    if run_id != self.run_id: return 404, _run_error("not_found", "no such run")
    replay = self.idem.begin("kill", key)
    if replay is not None: return replay
    with _bot_locked() as busy:
        if busy is not None:
            self.idem.forget("kill", key); return busy
        if self._kill_thread is not None and self._kill_thread.is_alive():
            response = (409, _run_error("kill_in_progress", "a kill is already running"))
        elif not self._something_live():
            response = (409, _run_error("nothing_running", "nothing is running — there is no pod to kill"))
        else:
            self._kill_thread = threading.Thread(target=self._kill_worker, daemon=True)
            self._kill_thread.start()
            response = (202, {"run_id": self.run_id, "outcome": "kill_started"})
    self.idem.finish("kill", key, *response)
    return response
    ```
    `_something_live()` is `drain_running(manifest) or phase_a_running(manifest)` — **through `run_mod`**, the way `_busy_reason` does, so it resolves through the same globals as `busy()`. `_kill_worker` does `with BOT_LOCK:` (no timeout — the bot's own loop has none either), re-checks `_something_live()`, records `last_kill = {"at": time.time(), "ok": False, "code": "nothing_running", "message": ...}` and returns if false, else `out = _do_kill(_AppTg(self.tg), self.chat_id)` and records `out`. Wrap the body in `try/except Exception` that records `code: "error"` and calls `log(...)` — a worker thread's exception must never vanish silently or leave `last_kill` empty.
  - `AppPod.resume(run_id, body, key)`: 404 check; `provider` must be `"runpod"|"vast"` (400 `bad_request`); `run_token` required (400); idempotency begin; under `_bot_locked()`: token mismatch → `Outcome(False, "stale_run", ...)`; `read_provision_failure(provision_failure_path(manifest)) is None` → `Outcome(False, "no_failure", "no failed rental to retry for this run")`; else `_do_resume(_AppTg(self.tg), self.chat_id, manifest, dry_run=False, gpu_provider=provider)`. Response shape identical to `AppRuns.confirm`'s (`202 {"run_id", "outcome"}` or `status_for` + `_run_error`). Do **not** clear the app draft (resume has nothing to do with it).
- [ ] **Step 4: Run** the new test file and `python3 -m unittest discover -s scripts/tests -p 'test_batch_bot.py'` and `test_batch_control_botruns.py`. Expected: all pass, `test_batch_bot.py` unchanged (691).
- [ ] **Step 5: Commit** `feat(api): kill and resume from the phone, _do_kill and _start_migration return outcomes`.

### Task 2: Read side and GPU choice — `pod()`, `gpu_stock()`, `balance()`, `set_gpu()`, cost quote, `gpu` guard

**Files:**
- Modify: `scripts/tgbot/bot.py` (`_gpu_stock_data`, `_balance_data` beside their `_report_*` twins; `AppPod` methods; `AppRuns.confirm` and `AppPod.resume` gain the `gpu` guard)
- Modify: `scripts/control/runs.py` (`run_detail`'s `lease` gains `quoted_usd_per_hr`; constant `RUNPOD_FLAT_USD_PER_HR = 0.99`)
- Modify: `scripts/tests/test_batch_control_botpod.py`, `scripts/tests/test_batch_control_runs.py`, `scripts/tests/test_batch_control_botruns.py` (one added test)

**Interfaces:**
- Produces:
  - `_gpu_stock_data(*, force: bool) -> dict`: `{"selected": str|None, "home_datacenter": str|None, "gpus": [{"gpu": id, "name": display_name_or_short, "usd_per_hr": float|None, "home": {"stock": str}|None, "sold_out_everywhere": bool}], "other_regions": [{"gpu", "name", "datacenter", "stock", "usd_per_hr"}]}`. Same selection logic as `_report_gpu_stock` (`entries[0]` for name/price; `home` = entry at `home_dc`; `elsewhere` = non-home entries with stock != "none", sorted by `_STOCK_RANK`, first 2 per GPU). Raises `RuntimeError` when `stock_at*` does (caller maps to 502).
  - `_balance_data(*, vast: bool) -> dict`: `{"runpod": {"usd": float, "usd_per_hr": float, "runway_hours": float, "low_runway": bool} | None, "vast": {"usd": float|None} | None}`; `runpod` is `None` when `account_balance()` raises `RuntimeError` (the reason goes in a top-level `"errors": [str]`, `_plain`ed); `vast` is present only when `vast=True`.
  - `AppPod.pod() -> tuple[int, dict]`: `{"run_id", "gpu": <.env GPU or primary>, "lease": {"provider", "provisioned_at", "abs_max_min", "quoted_usd_per_hr", "run_id"|None} | None, "migration": {"running": bool, "phase": str|None, "to_dc": str|None, "started_at": float|None, "bytes_copied", "total_bytes"} , "last_kill": dict|None, "failed_rental": {"gpu", "datacenter", "stock_out", "detail"} | None}`. No network. `lease` uses `lease_for(manifest) or read_lease(LEASE_PATH)`. **No field derived from the current time.**
  - `AppPod.gpu_stock(force) -> tuple[int, dict]`, `AppPod.balance(vast) -> tuple[int, dict]`, `AppPod.set_gpu(body) -> tuple[int, dict]`.

- [ ] **Step 1: Write the failing tests.**
  - `test_gpu_stock_data_matches_report_gpu_stock`: build one fake `stock` dict (5090 at home Medium and elsewhere; 4090 absent) and assert the JSON's per-GPU numbers equal what `_report_gpu_stock` rendered for the same input (parse the numbers/strings out of the sent Telegram text, or compare the entries chosen) — the two must never drift.
  - `test_gpu_stock_runpodctl_failure_is_502_upstream_unavailable`; `test_gpu_stock_force_uses_live_check` (`stock_at` vs `stock_at_cached` patched, which one was called).
  - `test_gpu_stock_runs_outside_the_bot_lock` (patched `stock_at_cached` asserts `BOT_LOCK.acquire(blocking=False)` **succeeds** from the same thread… use a second thread to try the lock while the network stub blocks, as slice 4's `test_rent_panel_network_runs_outside_the_bot_lock` does).
  - `test_balance_runway_and_low_flag` (balance 0.50 → `low_runway` true; 40 → false), `test_balance_vast_only_when_asked` (`vast_credit` patched; not called without `vast=1`), `test_balance_runpodctl_failure_is_200_with_error_not_500`.
  - `test_pod_reports_idle`, `test_pod_reports_lease_migration_failed_rental_last_kill` (write a real lease file / migrate progress / `provision-failed.json` in the temp root), `test_pod_makes_no_network_call` (patch `stock_at`, `stock_at_cached`, `volume_datacenter`, `account_balance`, `vast_credit` and `subprocess.run` to fail the test if called), `test_pod_body_is_stable_between_calls` (two calls → equal dicts: the ETag-safety property), `test_pod_names_no_absolute_path`.
  - `test_set_gpu_writes_env_and_rejects_unknown` (unknown id → `400 bad_request`, `.env` unchanged; valid → `env_get(ROOT/".env","GPU")` equals it), `test_set_gpu_takes_the_bot_lock` (`BOT_LOCK` held by another thread with `BOT_LOCK_TIMEOUT_SEC` patched to 0.05 → `503 bot_busy`).
  - `test_confirm_with_a_different_gpu_is_stale_panel_and_spends_nothing` (in `test_batch_control_botruns.py`: `.env` GPU = A, body `gpu` = B, valid panel token → `409 stale_panel`; `start_drain` not called; a matching `gpu` and an omitted `gpu` both proceed as before). Same for `AppPod.resume`.
  - `test_run_detail_lease_carries_a_quote` (in `test_batch_control_runs.py`): runpod lease → `quoted_usd_per_hr == 0.99`; vast lease → `None`; two consecutive `run_detail` calls equal (ETag stability).
- [ ] **Step 2: Run** the three files. Expected: FAIL.
- [ ] **Step 3: Implement.** Data functions mirror, never call, `_report_*`. Network calls stay out of `BOT_LOCK`: `gpu_stock` and `balance` do not take it at all (they read `.env` and call the network; nothing they read is bot state). `pod()` and `set_gpu()` take `_bot_locked()` (state and `.env` writes). `run_detail`'s new field is a constant, not derived from time; comment why (§5.3's ETag rule, and "a quote, not the invoice" per CLAUDE.md). The `gpu` guard is one small helper `_gpu_mismatch(body) -> Outcome | None` used by `confirm` and `resume`; it compares against `env_get(ROOT / ".env", "GPU") or _PRIMARY_GPU_ID`.
- [ ] **Step 4: Run** the three files plus `test_batch_bot.py`. Expected: all pass.
- [ ] **Step 5: Commit** `feat(api): pod, GPU stock, balance and GPU choice for the phone`.

### Task 3: Migrate — `AppPod.migrate_ask` and `AppPod.migrate`

**Files:**
- Modify: `scripts/tgbot/bot.py` (`AppPod`)
- Modify: `scripts/tests/test_batch_control_botpod.py`

**Interfaces:**
- Consumes: `_start_migration` (Task 1 → `Outcome`), `migration_running`, `_migrate_options`-equivalent stock listing, `volume_datacenter`, `stock_at_cached`, `read_lease`, `LEASE_PATH`, `busy`, `MIGRATE_DURATION_TEXT`, `_plain`.
- Produces:
  - `AppPod.migrate_ask(body) -> tuple[int, dict]`: body `{to_dc}`; success `200 {"to_dc", "home_datacenter", "confirm_token", "expires_in_sec": 600, "warning": <plain text>}`.
  - `AppPod.migrate(body, key) -> tuple[int, dict]`: body `{to_dc, confirm_token}`; success `202 {"outcome": "started", "to_dc"}`.
  - Token record: `AppPod._migrate_ask = {"token": str, "to_dc": str, "volume_id": str, "expires": float}` (`time.monotonic()` based; `secrets.token_urlsafe(16)`).

- [ ] **Step 1: Write the failing tests.** `_start_migration`, `subprocess.Popen`, `stock_at_cached`, `volume_datacenter` are always patched.
  - Ask refusals (each: nothing stored, `_start_migration` not called): `to_dc` missing/not a string → `400`; migration already running → `409 migration`; a live lease → `409 run_active`; the chat's run busy → `409 run_active`; `to_dc == home_dc` → `409 same_datacenter`; `to_dc` not among the datacenters the stock check lists for the wanted GPUs with stock != "none" → `409 unknown_datacenter`; `stock_at_cached` raising `RuntimeError` → `502 upstream_unavailable` (**fail closed**); home datacenter unknown (`volume_datacenter` → None) → `409 home_unknown`.
  - `test_ask_returns_a_token_and_the_warning_without_html_or_paths` (warning mentions deleting the current volume, `Cannot be undone`; no `<`, no `str(ROOT)`).
  - `test_ask_network_runs_outside_the_bot_lock`.
  - Go refusals (each: `_start_migration` **not called**, `Popen` not called): no prior ask → `409 bad_confirm_token`; wrong token; right token but different `to_dc`; expired token (patch `time.monotonic`); token already used (second go with the same token but a **new** idempotency key → `409 bad_confirm_token`); volume id changed since the ask (`volume_datacenter`/`.env` `POD_VOLUME_ID` differs) → `409 bad_confirm_token`; a migration started since the ask → `409 migration`; a drain live since the ask → `409 run_active`; missing `Idempotency-Key` is the HTTP layer's job (Task 4).
  - `test_go_consumes_the_token_and_starts_exactly_once`: valid → `202`, `_start_migration` called once with `(_AppTg-wrapped tg, chat_id, to_dc)`; token cleared; replay with the same key → same response, still one call; a fresh key with the same token → `409 bad_confirm_token`.
  - `test_go_refusal_from_start_migration_is_returned_not_sent` (patched to return `Outcome(False, "launch_failed", ...)` → `409`-mapped body, fake tg got no message).
  - `test_a_new_ask_replaces_the_old_token`.
  - `test_go_bot_busy_is_503_and_forgets_the_key`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** `migrate_ask`: outside the lock, `volume_id = env_get(ROOT/".env","POD_VOLUME_ID")`, `home = volume_datacenter(volume_id)`, `stock = stock_at_cached([_PRIMARY_GPU_ID, *_FALLBACK_GPU_IDS])` (catch `RuntimeError` → 502); the listed datacenters are `{e.datacenter_id for entries in stock.values() for e in entries if e.stock_status.lower() != "none"}` minus `home`. Then `_bot_locked()`: check `migration_running()`, `read_lease(LEASE_PATH)`, `busy(_job_manifest_path(chat))`; store the token record. `migrate`: 400 checks first (`to_dc`, `confirm_token` strings), idempotency `begin("migrate", key)`, then under `_bot_locked()` re-check every guard **and** compare the token (`hmac.compare_digest`), expiry and `to_dc` and `volume_id` (re-read from `.env`, no network), **pop the token before** calling `_start_migration` (a crash then leaves no reusable token), call `_start_migration(_AppTg(self.tg), self.chat_id, to_dc)`, respond. The Telegram-side "started" message is sent by `_start_migration` itself, as today.
- [ ] **Step 4: Run** the botpod file, `test_batch_control_botruns.py`, `test_batch_bot.py`. Expected: pass.
- [ ] **Step 5: Commit** `feat(api): two-step volume migration from the phone`.

### Task 4: HTTP routes, wiring, invariants, docs

**Files:**
- Modify: `scripts/httpapi/server.py` (routes, `make_server(..., app_pod=None)`, `_app_pod()` helper)
- Modify: `scripts/tgbot/bot.py` (`_start_control_api` builds `AppPod`, assigns `server.app_pod`; `_tick_staging_prune`'s idempotency sweep already covers the shared store)
- Modify: `scripts/tests/test_batch_control_http.py`, `scripts/tests/test_batch_control_invariants.py`
- Modify: `scripts/vps/README.md`

**Interfaces:**
- Consumes: `AppPod` (Tasks 1-3).
- Produces (routes; all authenticated by the existing `_authenticate`):
  - `POST /v1/runs/{id}/kill` (key) → `app_pod.kill(id, key)`
  - `POST /v1/runs/{id}/resume` (key, JSON) → `app_pod.resume(id, body, key)`
  - `GET /v1/pod` → `app_pod.pod()`
  - `GET /v1/gpu/stock[?force=1]` → `app_pod.gpu_stock(force)`
  - `GET /v1/balance[?vast=1]` → `app_pod.balance(vast)`
  - `PUT /v1/pod/gpu` (JSON) → `app_pod.set_gpu(body)`
  - `POST /v1/pod/migrate/ask` (JSON) → `app_pod.migrate_ask(body)`
  - `POST /v1/pod/migrate` (key, JSON) → `app_pod.migrate(body, key)`
  - Without `app_pod` wired: `503 pod_unavailable` (never `AttributeError`/500), like `_app_runs()`.

- [ ] **Step 1: Write the failing tests** in `test_batch_control_http.py` with a fake `AppPod` recording calls (the slice-4 `FakeAppRuns` pattern): each route is reached with the right arguments; `kill`, `resume` and `migrate` without `Idempotency-Key` → `400` and the fake was **not** called; `migrate/ask`, `PUT /pod/gpu`, and the GETs need no key; no bearer → `401` on every new route; `?force=1` and `?vast=1` parsed (only the exact string `1`); `app_pod=None` → `503 pod_unavailable`; a non-object JSON body → `400`.
  In `test_batch_control_invariants.py`: (a) no `.py` under `scripts/control/` or `scripts/httpapi/` contains `gpu-destroy` or `volume_migrate`; (b) `_do_kill(` is called from exactly two places in `scripts/tgbot/bot.py` (the `_CB_KILL_GO` branch in `_handle_callback`, and `AppPod._kill_worker`) — AST-based like the existing `start_drain` check; (c) `_start_migration(` from exactly two (`_CB_MIGRATE_GO` branch, `AppPod.migrate`).
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** the routes exactly like slice 4's (`self._idempotency_key()` **before** `self._read_json()`, method + path-shape matching before dispatch, `_send_json(status, body)`); wire `AppPod` in `_start_control_api` **before** `start_in_thread` (same reason as `AppRuns`); `make_server` gains `app_pod=None` and `server.app_pod = app_pod`.
- [ ] **Step 4: README.** In `scripts/vps/README.md` add a "Slice 5 (pod, kill, resume, GPU, balance, migrate)" section: the route table above, the `kill` → `202` + poll `GET /v1/pod`'s `last_kill` contract, why `resume` needs an outstanding failure and the run token, the migration two-step (token lifetime 10 min, in memory, single use; that step two deletes the source volume once verified), `502 upstream_unavailable`, `GET /v1/balance?vast=1` is slow (~30 s, measured with `vast_credit`'s own timeout — say "up to" only if you measured; otherwise omit the number). Do **not** write a "Measured" table: the controller adds it after live verification.
- [ ] **Step 5: Run** `make batch-test` (whole suite), `make check-job-types`, `motions-studio/setup/scrub-secrets.sh --check`. Expected: all green, count = previous 1966 + new tests.
- [ ] **Step 6: Commit** `feat(api): slice 5 routes for the pod, kill, resume, GPU and migrate`.
