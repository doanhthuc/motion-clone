# GPU stock subscriptions in the iOS app — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the iPhone app create, list and remove the bot's one-shot GPU stock subscriptions. When a
subscription fires, it can optionally auto-resume a run that is stuck on a stock-out. The Pod tab is
rebuilt as a single stage that does not scroll.

**Architecture:** The bot's existing subscription store
(`batch/tg-<chat>.gpusubs.json`, `_tick_gpu_subs`) stays the single source of truth. This plan adds:
- ids, a fired-history file, and an optional `auto_resume` block that the tick hands to the
  existing `_do_resume`;
- three `AppPod` methods and routes for the phone;
- a `datacenters` array on `GET /v1/gpu/stock?all=1`;
- on iOS, a `GpuSubsStore` in MotionKit and a new Pod stage (hero, GPU tiles, GPU sheet, Watching
  drawer), built on New Job's drawer and banner patterns.

**Tech Stack:** Python 3 stdlib (bot + `http.server` API, `unittest`), SwiftUI / iOS 26, MotionKit
(Swift Testing), XCUITest, xcodegen (`make ios-gen`).

**Spec:** `docs/superpowers/specs/2026-09-27-gpu-stock-subscribe-ios-design.md`. Read it first; this
plan argues from it.

## Global Constraints

- Code, comments, commits and PR text are in English. Do not add `# #region ALD` markers.
- Subscriptions are **one-shot**: a firing sub is removed in the same tick.
- Auto-resume never adds a spend path. It only calls `_do_resume(..., gpu_provider="runpod")`, only
  at the volume's **home** datacenter, only when the run has an outstanding `provision-failed.json`
  with `stock_out: true`, and only at a price ≤ `max_usd_per_hr` (the GPU's $/h **when armed**). It
  never migrates and never runs Phase A.
- Only one sub carries `auto_resume` at a time.
- Notifications go through **Telegram only** (no ntfy, no APNs), plus an in-app banner.
- `POST /v1/gpu/subs` is an upsert keyed on `(gpu, datacenter)` and takes **no** `Idempotency-Key`.
- `datacenters` in `GET /v1/gpu/stock?all=1` is an **array** `[{gpu, datacenter, stock, usd_per_hr}]`.
- Network reads (`volume_datacenter`, `stock_at_cached`) never run while this code holds
  `BOT_LOCK` from an app route.
- Tests must not reach runpodctl, `make gpu-destroy`, `start_drain` or a pod. Patch them by name in
  `tgbot.bot`, as the existing fixtures do.
- The Pod stage must not scroll on an iPhone SE (3rd gen) or an iPhone 18 Pro Max.
- `motions-studio/setup/scrub-secrets.sh --check` exits 0 before every commit (the repo is public).
- Pushing `scripts/**` to `main` auto-deploys `motion-bot`. Merge only when the user says so, after
  checking the VPS has no live drain, Phase A, lease or migration (see CLAUDE.md).

## Review Focus

1. **Sub fires while the stock-out run was replaced by a new one.** The user confirms a new job
   between arming and the fire. Auto-resume must not rent for the new manifest: the `run_token`
   mismatch refuses it (Task 2 test `test_auto_resume_refuses_when_the_manifest_changed`).
2. **Price rose between arming and firing.** A 5090 at $0.99 when armed that comes back at $1.29
   notifies only (Task 2 test `test_auto_resume_refuses_above_the_ceiling`).
3. **Legacy `gpusubs.json` without ids.** A file written by today's bot must load, gain ids, be
   removable from the phone, and still fire (Task 1 test `test_legacy_entries_gain_ids_and_are_saved`).
4. **Arming at a datacenter other than home.** From the phone this is a 409 `not_home_dc`, and
   nothing is written (Task 3 test `test_arming_outside_home_is_409_and_writes_nothing`).
5. **First launch after install with old firings on the server.** The banner must not replay history
   (Task 5 test `firstLoadMarksExistingFiringsSeen`).

---

## File Structure

**Server (`scripts/`)**
- Modify `scripts/tgbot/bot.py`:
  - subscription store helpers (~L4421–4665): ids, fired file, auto-resume at fire;
  - `_gpu_stock_data` (~L4294): `all_dcs`;
  - `AppPod` (~L7549): `gpu_subs`, `add_gpu_sub`, `remove_gpu_sub`, `gpu_stock(force, all_dcs)`;
  - `_run_ticks` (~L8013): pass `dry_run`.
- Modify `scripts/httpapi/server.py` (~L328): three routes, plus `all=1` on stock.
- Tests:
  - `scripts/tests/test_batch_bot.py` (`TestGpuSubscribe`, ~L5341);
  - `scripts/tests/test_batch_control_botpod.py` (new `TestAppPodGpuSubs`, new `TestAutoResumeTick`,
    extended `TestGpuStock`);
  - `scripts/tests/test_batch_control_http.py` (`FakeAppPod`, `_POD_ROUTES`, `TestAppPodRoutes`).

**iOS (`ios/`)**
- Create `ios/MotionKit/Sources/MotionKit/Models/GpuSubs.swift`: the models.
- Modify `ios/MotionKit/Sources/MotionKit/Models/PodCost.swift`: `GpuStock.datacenters`,
  `GpuDatacenter`.
- Modify `ios/MotionKit/Sources/MotionKit/Stores/GpuStore.swift`: always ask `all=1`.
- Create `ios/MotionKit/Sources/MotionKit/Stores/GpuSubsStore.swift`.
- Tests: `ios/MotionKit/Tests/MotionKitTests/GpuSubsStoreTests.swift` (new),
  `GpuAndBalanceStoreTests.swift`, `Fixtures.swift`.
- `ios/MotionApp/Pod/`:
  - rewrite `PodView.swift` (the stage);
  - create `PodHero.swift` (`PodHero` + the moved `LeaseCard` and `MigrationCard`);
  - create `GpuTile.swift`, `GpuSheet.swift`, `WatchDrawer.swift`;
  - create `BalanceSheet.swift` (holds the moved `BalanceSection`).
  - `GpuPickerView.swift` stays for the rent panel.
- Create `ios/MotionApp/Components/SwipeUpToDismiss.swift`: moved out of `NewJobView.swift` so
  the Pod stage can reuse it.
- Modify `ios/MotionApp/MotionApp.swift` (`AppModel.gpuSubs`), `ios/MotionApp/RootView.swift` (pass
  it), and `ios/MotionApp/Runs/RunDetailView.swift` (`RetryRentalCard`: "Resume when in stock").
- UI tests: modify `ios/MotionAppUITests/Phase5SmokeTests.swift`; create
  `ios/MotionAppUITests/PodStageTests.swift`.
- Modify `ios/MotionKit/Sources/motion-contract/main.swift`: two GET checks.
- Docs: `docs/superpowers/swiftui-app-progress.md` gets a section, and the spec gets a gate record.

---

### Task 1: Subscription ids and the fired history (server)

**Files:**
- Modify: `scripts/tgbot/bot.py` (the `_GPU_SUBS` block ~L4421–4665)
- Test: `scripts/tests/test_batch_bot.py` (`TestGpuSubscribe`)

**Interfaces:**
- Produces:
  - `_new_gpu_sub(gpu_id: str, dc: str, existing: list[dict]) -> dict` — a dict with keys `id`,
    `gpu_id`, `datacenter_id`, `created_at`.
  - `_gpu_fired_for(chat_id: int) -> list[dict]`, newest first.
  - `_record_gpu_fired(chat_id: int, entry: dict) -> None`, which keeps 10.
  - `_GPU_FIRED_KEEP = 10`.
  - Fired entry keys: `sub_id, gpu_id, datacenter_id, stock, usd_per_hr, fired_at, action`, and
    `reason` when refused.

- [ ] **Step 1: Write the failing tests** — add to `TestGpuSubscribe`:

```python
    def test_subscribing_mints_an_id_and_timestamp(self):
        bot.handle(self.tg, cb_from(ME, bot._CB_GPUSUB_DC + "5090:EU-RO-1"),
                  allowed_user_id=ME)
        [sub] = bot._gpu_subs_for(ME)
        self.assertRegex(sub["id"], r"^[0-9a-f]{6}$")
        self.assertIsInstance(sub["created_at"], float)

    def test_legacy_entries_gain_ids_and_are_saved(self):
        """A file written before ids existed (every sub made 2026-09-12 → today)."""
        path = bot._gpu_subs_path(ME)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([
            {"gpu_id": "NVIDIA GeForce RTX 5090", "datacenter_id": "EU-RO-1"},
            {"gpu_id": "NVIDIA GeForce RTX 4090", "datacenter_id": "EU-RO-1"}]))
        subs = bot._gpu_subs_for(ME)
        ids = [s["id"] for s in subs]
        self.assertEqual(len(set(ids)), 2)
        on_disk = json.loads(path.read_text())
        self.assertEqual([s["id"] for s in on_disk], ids)

    def test_tick_records_the_firing(self):
        bot._gpu_subs_for(ME).append(bot._new_gpu_sub(
            "NVIDIA GeForce RTX 5090", "EU-RO-1", []))
        bot._save_gpu_subs(ME)
        sub_id = bot._gpu_subs_for(ME)[0]["id"]
        with mock.patch("tgbot.bot.stock_at_cached",
                       return_value=self._stock(status_5090_ro="Low")):
            bot._tick_gpu_subs(self.tg, ME)
        [fired] = bot._gpu_fired_for(ME)
        self.assertEqual((fired["sub_id"], fired["datacenter_id"], fired["stock"],
                          fired["usd_per_hr"], fired["action"]),
                         (sub_id, "EU-RO-1", "Low", 0.99, "notified"))
        self.assertNotIn("reason", fired)

    def test_fired_history_keeps_the_newest_ten(self):
        for i in range(12):
            bot._record_gpu_fired(ME, {"sub_id": f"{i:06x}", "fired_at": float(i)})
        fired = bot._gpu_fired_for(ME)
        self.assertEqual(len(fired), bot._GPU_FIRED_KEEP)
        self.assertEqual(fired[0]["fired_at"], 11.0)

    def test_unreadable_fired_file_reads_as_empty(self):
        path = bot._gpu_fired_path(ME)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json")
        self.assertEqual(bot._gpu_fired_for(ME), [])
```

Also update the two existing tests that compare whole dicts, since entries now carry `id` and
`created_at`:
- In `test_unsubscribe_...` (the one asserting `subs == [{"gpu_id": "NVIDIA GeForce RTX 4090", ...}]`),
  replace the assertion with
  `self.assertEqual([(s["gpu_id"], s["datacenter_id"]) for s in subs], [("NVIDIA GeForce RTX 4090", "EU-RO-1")])`.
- In `test_subscriptions_survive_a_restart`, apply the same projection to
  `[("NVIDIA GeForce RTX 5090", "EU-RO-1")]`.

Make sure `import json` is at the top of the test module (add it if missing).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_bot.py' -k GpuSubscribe`
Expected: FAIL / ERROR, with `AttributeError: module 'tgbot.bot' has no attribute '_new_gpu_sub'`
and similar.

- [ ] **Step 3: Implement** — in `bot.py`, directly after `_save_gpu_subs`:

```python
# The last firings, newest first, for the phone's "Recent" list and its
# in-app banner (2026-09-27 spec §1). The Telegram message is still the
# notification; this is only what the app reads back when it opens.
_GPU_FIRED_KEEP = 10


def _gpu_fired_path(chat_id: int) -> Path:
    return ROOT / "batch" / f"tg-{chat_id}.gpusubs-fired.json"


def _mint_gpu_sub_id(existing: list[dict]) -> str:
    taken = {s.get("id") for s in existing}
    while True:
        sub_id = secrets.token_hex(3)
        if sub_id not in taken:
            return sub_id


def _new_gpu_sub(gpu_id: str, dc: str, existing: list[dict]) -> dict:
    return {"id": _mint_gpu_sub_id(existing), "gpu_id": gpu_id,
            "datacenter_id": dc, "created_at": time.time()}


def _gpu_fired_for(chat_id: int) -> list[dict]:
    try:
        data = json.loads(_gpu_fired_path(chat_id).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (ValueError, TypeError) as exc:
        log(f"gpu fired history for chat {chat_id} unreadable, starting over: {exc!r}")
        return []
    return data if isinstance(data, list) else []


def _record_gpu_fired(chat_id: int, entry: dict) -> None:
    fired = [entry, *_gpu_fired_for(chat_id)][:_GPU_FIRED_KEEP]
    path = _gpu_fired_path(chat_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(fired), encoding="utf-8")
    tmp.replace(path)
```

Change `_gpu_subs_for` so a legacy file gains ids once and is saved back. The phone removes subs by
id, so every entry needs one:

```python
def _gpu_subs_for(chat_id: int) -> list[dict]:
    """Every (gpu_id, datacenter_id) this chat is watching. Loaded once per
    process per chat, same as `_ledger_for`. Entries written before ids
    existed (2026-09-12 → 2026-09-27) get one here, saved straight back."""
    if chat_id not in _GPU_SUBS_LOADED:
        _GPU_SUBS_LOADED.add(chat_id)
        path = _gpu_subs_path(chat_id)
        try:
            _GPU_SUBS[chat_id] = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            pass
        except (ValueError, TypeError) as exc:
            log(f"gpu subs for chat {chat_id} unreadable, starting over: {exc!r}")
        subs = _GPU_SUBS.get(chat_id) or []
        if any("id" not in s for s in subs):
            for s in subs:
                if "id" not in s:
                    s["id"] = _mint_gpu_sub_id(subs)
            _save_gpu_subs(chat_id)
    return _GPU_SUBS.setdefault(chat_id, [])
```

In `_add_gpu_sub`, replace `subs.append({"gpu_id": gpu_id, "datacenter_id": dc})` with
`subs.append(_new_gpu_sub(gpu_id, dc, subs))`.

In `_tick_gpu_subs`, inside the `for sub, hit in fired:` loop and before `tg.send_message`, add:

```python
        _record_gpu_fired(chat_id, {
            "sub_id": sub.get("id") or "", "gpu_id": sub["gpu_id"],
            "datacenter_id": sub["datacenter_id"], "stock": hit.stock_status,
            "usd_per_hr": hit.price_per_hr or None, "fired_at": time.time(),
            "action": "notified"})
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_bot.py' -k GpuSubscribe`
Expected: all PASS.

- [ ] **Step 5: Run the whole bot suite** (other tests read `_GPU_SUBS`)

Run: `make batch-test`
Expected: OK.

- [ ] **Step 6: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add scripts/tgbot/bot.py scripts/tests/test_batch_bot.py
git commit -m "bot: GPU subscriptions get ids and a fired history"
```

---

### Task 2: Auto-resume when a subscription fires (server)

**Files:**
- Modify: `scripts/tgbot/bot.py`:
  - `_tick_gpu_subs`, plus a new `_auto_resume_refusal`;
  - `_gpu_subs_lines_and_buttons` (⚡ tag);
  - `_run_ticks` (~L8013).
- Test: `scripts/tests/test_batch_control_botpod.py` (new class `TestAutoResumeTick`)

**Interfaces:**
- Consumes (Task 1): `_new_gpu_sub`, `_gpu_fired_for`.
- Produces:
  - `_tick_gpu_subs(tg, chat_id, *, dry_run: bool = False) -> None`;
  - `_auto_resume_refusal(chat_id: int, sub: dict, hit: Stock) -> str | None`;
  - the sub key `auto_resume: {"run_id": str, "run_token": str, "max_usd_per_hr": float}`.

- [ ] **Step 1: Write the failing tests** — append to `test_batch_control_botpod.py`:

```python
class TestAutoResumeTick(_PodFixture):
    """`_tick_gpu_subs` handing an armed sub to `_do_resume` (2026-09-27 spec
    §1 Firing). `start_drain` is patched by `_Fixture`; nothing here rents."""

    GPU = "NVIDIA GeForce RTX 5090"

    def setUp(self):
        super().setUp()
        self.job = self._job("app")
        write_manifest([self.job], self._live(), now=time.strftime("%Y-%m-%d %H:%M:%S"))
        state_path_for(self._live()).write_text(
            json.dumps({"batch": "2026-09-27-1200", "runs": {}}), encoding="utf-8")
        write_provision_failure(provision_failure_path(self._live()), ProvisionFailure(
            gpu=self.GPU, datacenter="EU-RO-1", stock_out=True,
            detail="no instances available"))
        (self.root / ".env").write_text(
            "GPU=NVIDIA GeForce RTX 4090\nPOD_VOLUME_ID=vol-1\n", encoding="utf-8")

    def _arm(self, dc="EU-RO-1", cap=0.99, token=None):
        sub = bot._new_gpu_sub(self.GPU, dc, [])
        sub["auto_resume"] = {"run_id": self._live().stem,
                              "run_token": token or bot._run_token(ME),
                              "max_usd_per_hr": cap}
        bot._gpu_subs_for(ME).append(sub)
        bot._save_gpu_subs(ME)
        return sub

    def _fire(self, price=0.99, dc="EU-RO-1"):
        self.patches["stock_at_cached"].return_value = {self.GPU: [Stock(
            gpu_id=self.GPU, display_name="RTX 5090", price_per_hr=price,
            datacenter_id=dc, stock_status="Low")]}
        bot._tick_gpu_subs(self.tg, ME, dry_run=False)
        return bot._gpu_fired_for(ME)[0]

    def test_auto_resume_switches_gpu_and_resumes(self):
        self._arm()
        fired = self._fire()
        self.assertEqual(fired["action"], "resumed")
        self.patches["start_drain"].assert_called_once()
        self.assertEqual(self.patches["start_drain"].call_args.kwargs["gpu_provider"], "runpod")
        self.assertEqual(env_get(self.root / ".env", "GPU"), self.GPU)
        self.assertEqual(bot._gpu_subs_for(ME), [])
        self.assertTrue(any("Auto-resumed" in text for text in self._texts()))

    def test_auto_resume_refuses_above_the_ceiling(self):
        self._arm(cap=0.99)
        fired = self._fire(price=1.29)
        self.assertEqual(fired["action"], "resume_refused")
        self.assertIn("ceiling", fired["reason"])
        self.patches["start_drain"].assert_not_called()
        self.assertEqual(env_get(self.root / ".env", "GPU"), "NVIDIA GeForce RTX 4090")
        self.assertTrue(any("Nothing was rented" in text for text in self._texts()))

    def test_auto_resume_refuses_when_the_manifest_changed(self):
        self._arm(token="1")
        fired = self._fire()
        self.assertEqual(fired["action"], "resume_refused")
        self.patches["start_drain"].assert_not_called()

    def test_auto_resume_refuses_without_an_outstanding_stock_out(self):
        self._arm()
        provision_failure_path(self._live()).unlink()
        fired = self._fire()
        self.assertEqual(fired["action"], "resume_refused")
        self.patches["start_drain"].assert_not_called()

    def test_auto_resume_refuses_outside_home(self):
        self._arm(dc="EU-CZ-1")
        fired = self._fire(dc="EU-CZ-1")
        self.assertEqual(fired["action"], "resume_refused")
        self.patches["start_drain"].assert_not_called()

    def test_auto_resume_refuses_while_a_lease_is_live(self):
        self._arm()
        self.patches["read_lease"].return_value = object()
        fired = self._fire()
        self.assertEqual(fired["action"], "resume_refused")
        self.patches["start_drain"].assert_not_called()

    def test_auto_resume_refuses_during_a_migration(self):
        self._arm()
        self.patches["migration_running"].return_value = True
        fired = self._fire()
        self.assertEqual(fired["action"], "resume_refused")
        self.patches["start_drain"].assert_not_called()

    def test_a_plain_sub_never_resumes(self):
        bot._gpu_subs_for(ME).append(bot._new_gpu_sub(self.GPU, "EU-RO-1", []))
        fired = self._fire()
        self.assertEqual(fired["action"], "notified")
        self.patches["start_drain"].assert_not_called()

    def test_unsubscribe_list_marks_the_armed_sub(self):
        self._arm()
        text, _ = bot._gpu_subs_lines_and_buttons(ME)
        self.assertIn("⚡", text)
```

`_Fixture` patches `volume_datacenter` → `"EU-RO-1"`, `busy`, `migration_running` and
`stock_at_cached`; `_PodFixture` patches `read_lease`. Check both at the top of the file; if either
name is not patched there, add it to `setUp` in the same `mock.patch(f"tgbot.bot.{name}")` style.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_botpod.py' -k AutoResumeTick`
Expected: FAIL. `_tick_gpu_subs() got an unexpected keyword argument 'dry_run'`.

- [ ] **Step 3: Implement** — add above `_tick_gpu_subs`:

```python
def _auto_resume_refusal(chat_id: int, sub: dict, hit) -> str | None:
    """Why an armed sub must only notify, or None when it may resume.

    The arming guards again, read at fire time, plus the live-state ones
    `_do_resume` would refuse on anyway (2026-09-27 spec §1). Each answer is
    one plain sentence: it goes into the Telegram message and the fired
    history as-is. `_home_datacenter` is a runpodctl call; this only runs
    for the rare sub that is both armed and firing.
    """
    armed = sub.get("auto_resume") or {}
    manifest_path = _job_manifest_path(chat_id)
    if (armed.get("run_id") != manifest_path.stem
            or armed.get("run_token") != _run_token(chat_id)):
        return "the run changed since auto-resume was armed"
    failure = read_provision_failure(provision_failure_path(manifest_path))
    if failure is None or not failure.stock_out:
        return "the run no longer has a stock-out to resume from"
    if sub["datacenter_id"] != _home_datacenter():
        return "this is not the volume's home datacenter"
    if busy(manifest_path):
        return "the run is busy"
    if migration_running():
        return "a volume migration is in progress"
    if read_lease(LEASE_PATH) is not None:
        return "a pod is already live"
    cap = armed.get("max_usd_per_hr")
    price = hit.price_per_hr
    if not price or cap is None or price > cap:
        return (f"the price (${price or 0:.2f}/h) is over the ${cap or 0:.2f}/h "
                "ceiling set when it was armed")
    return None
```

Replace the notify loop at the end of `_tick_gpu_subs` (and give the function its new signature,
`def _tick_gpu_subs(tg: Tg, chat_id: int, *, dry_run: bool = False) -> None:`) with:

```python
    for sub, hit in fired:
        short = _GPU_DISPLAY_SHORT.get(sub["gpu_id"], sub["gpu_id"])
        price = f"${hit.price_per_hr:.2f}/h" if hit.price_per_hr else "?"
        entry = {"sub_id": sub.get("id") or "", "gpu_id": sub["gpu_id"],
                 "datacenter_id": sub["datacenter_id"], "stock": hit.stock_status,
                 "usd_per_hr": hit.price_per_hr or None, "fired_at": time.time(),
                 "action": "notified"}
        refusal = _auto_resume_refusal(chat_id, sub, hit) if sub.get("auto_resume") else None
        if sub.get("auto_resume") and refusal is None:
            tail = (f"\n⚡ <b>Auto-resumed</b> — renting {_esc(short)} @ "
                    f"{_esc(sub['datacenter_id'])} · {price}. The clock is running.")
        elif refusal is not None:
            tail = (f"\n{ICON_WARN} Auto-resume skipped: {_esc(refusal)}. "
                    "Nothing was rented.")
        else:
            tail = "\nThis subscription cleared itself — /subscribe again to re-arm."
        tg.send_message(
            chat_id,
            f"🔔 <b>{_esc(short)}</b> is now available at "
            f"<b>{_esc(sub['datacenter_id'])}</b>: "
            f"{_stock_icon(hit.stock_status.lower())} {_esc(hit.stock_status)} · "
            f"{ICON_MONEY_CE} {price}{tail}",
            parse_mode=PARSE_HTML)
        if sub.get("auto_resume") and refusal is None:
            # .env's GPU first, exactly as _CB_RECOVER_SWITCH does: the
            # subscribed card is the one that has stock.
            env_set(ROOT / ".env", "GPU", sub["gpu_id"])
            out = _do_resume(tg, chat_id, _job_manifest_path(chat_id),
                             dry_run=dry_run, gpu_provider="runpod")
            if out:
                entry["action"] = "resumed"
            else:
                entry["action"], entry["reason"] = "resume_refused", _plain(out.message)
        elif refusal is not None:
            entry["action"], entry["reason"] = "resume_refused", refusal
        _record_gpu_fired(chat_id, entry)
```

Remove the `_record_gpu_fired` call Task 1 added, since this loop now records every firing. Also
check that the new tail keeps the existing test `test_tick_fires_and_clears_when_stock_appears`
passing: it asserts "now available", which is still in the message.

In `_run_ticks`, change `_tick_gpu_subs(tg, chat_id)` to `_tick_gpu_subs(tg, chat_id, dry_run=dry_run)`.

In `_gpu_subs_lines_and_buttons`, change the line append to:

```python
        bolt = " ⚡ auto-resume" if s.get("auto_resume") else ""
        lines.append(f"  {_esc(short)} @ {_esc(dc)}{bolt}")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_botpod.py' -k AutoResumeTick`
then `make batch-test`.
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add scripts/tgbot/bot.py scripts/tests/test_batch_control_botpod.py
git commit -m "bot: an armed GPU subscription auto-resumes its stuck run on fire"
```

---

### Task 3: `AppPod` subscription methods and `?all=1` stock (server)

**Files:**
- Modify: `scripts/tgbot/bot.py`: `_gpu_stock_data` (~L4294) and `AppPod` (after `set_gpu`, ~L7845)
- Test: `scripts/tests/test_batch_control_botpod.py` (new `TestAppPodGpuSubs`, extended `TestGpuStock`)

**Interfaces:**
- Consumes (Tasks 1–2): `_new_gpu_sub`, `_gpu_fired_for`, `auto_resume` shape.
- Produces:
  - `AppPod.gpu_subs() -> tuple[int, dict]`: `{"subs": [SubView], "fired": [FiredView]}`.
  - `AppPod.add_gpu_sub(body: dict) -> tuple[int, dict]`: `201`/`200` with `{"sub": SubView}`.
  - `AppPod.remove_gpu_sub(sub_id: str) -> tuple[int, dict]`: `200 {"subs": [SubView]}`.
  - `AppPod.gpu_stock(force: bool, all_dcs: bool = False) -> tuple[int, dict]`.
  - `_gpu_stock_data(*, force: bool, all_dcs: bool = False) -> dict`.
  - SubView: `{"id", "gpu", "name", "datacenter", "created_at", "auto_resume": null | {"run_id", "max_usd_per_hr"}}`.
    `run_token` never leaves the box.
  - FiredView: `{"sub_id", "gpu", "name", "datacenter", "stock", "usd_per_hr", "fired_at", "action", "reason"}`.
  - DatacenterView (in `datacenters`): `{"gpu", "datacenter", "stock", "usd_per_hr"}`.

- [ ] **Step 1: Write the failing tests** — append to `test_batch_control_botpod.py`:

```python
class TestAppPodGpuSubs(_PodFixture):
    GPU = "NVIDIA GeForce RTX 5090"

    def setUp(self):
        super().setUp()
        self.job = self._job("app")
        write_manifest([self.job], self._live(), now=time.strftime("%Y-%m-%d %H:%M:%S"))
        (self.root / ".env").write_text("POD_VOLUME_ID=vol-1\n", encoding="utf-8")
        self.patches["stock_at_cached"].return_value = {self.GPU: [Stock(
            gpu_id=self.GPU, display_name="RTX 5090", price_per_hr=0.99,
            datacenter_id="EU-RO-1", stock_status="none")]}

    def _stock_out(self):
        write_provision_failure(provision_failure_path(self._live()), ProvisionFailure(
            gpu=self.GPU, datacenter="EU-RO-1", stock_out=True, detail="no instances"))

    def test_add_then_list_then_remove(self):
        status, body = self.pod.add_gpu_sub({"gpu": self.GPU, "datacenter": "EU-CZ-1"})
        self.assertEqual(status, 201)
        sub = body["sub"]
        self.assertEqual((sub["gpu"], sub["name"], sub["datacenter"], sub["auto_resume"]),
                         (self.GPU, "RTX 5090", "EU-CZ-1", None))
        status, listing = self.pod.gpu_subs()
        self.assertEqual((status, [s["id"] for s in listing["subs"]], listing["fired"]),
                         (200, [sub["id"]], []))
        status, after = self.pod.remove_gpu_sub(sub["id"])
        self.assertEqual((status, after["subs"]), (200, []))
        self.assertEqual(bot._gpu_subs_for(ME), [])

    def test_add_is_an_upsert(self):
        first = self.pod.add_gpu_sub({"gpu": self.GPU, "datacenter": "EU-RO-1"})
        second = self.pod.add_gpu_sub({"gpu": self.GPU, "datacenter": "EU-RO-1"})
        self.assertEqual((first[0], second[0]), (201, 200))
        self.assertEqual(first[1]["sub"]["id"], second[1]["sub"]["id"])
        self.assertEqual(len(bot._gpu_subs_for(ME)), 1)

    def test_bad_bodies_are_400(self):
        for body in ({"gpu": "nope", "datacenter": "EU-RO-1"},
                     {"gpu": self.GPU, "datacenter": ""},
                     {"gpu": self.GPU, "datacenter": "EU-RO-1", "auto_resume": "yes"},
                     {"gpu": self.GPU, "datacenter": "EU-RO-1", "auto_resume": True}):
            with self.subTest(body=body):
                status, resp = self.pod.add_gpu_sub(body)
                self.assertEqual((status, resp["error"]["code"]), (400, "bad_request"))
        self.assertEqual(bot._gpu_subs_for(ME), [])

    def test_arming_stores_the_price_and_token_and_hides_the_token(self):
        self._stock_out()
        status, body = self.pod.add_gpu_sub({"gpu": self.GPU, "datacenter": "EU-RO-1",
                                             "auto_resume": True, "run_id": self.pod.run_id})
        self.assertEqual(status, 201)
        self.assertEqual(body["sub"]["auto_resume"],
                         {"run_id": self.pod.run_id, "max_usd_per_hr": 0.99})
        [stored] = bot._gpu_subs_for(ME)
        self.assertEqual(stored["auto_resume"]["run_token"], bot._run_token(ME))
        self.assertNotIn("run_token", json.dumps(self.pod.gpu_subs()[1]))

    def test_arming_outside_home_is_409_and_writes_nothing(self):
        self._stock_out()
        status, body = self.pod.add_gpu_sub({"gpu": self.GPU, "datacenter": "EU-CZ-1",
                                             "auto_resume": True, "run_id": self.pod.run_id})
        self.assertEqual((status, body["error"]["code"]), (409, "not_home_dc"))
        self.assertEqual(bot._gpu_subs_for(ME), [])

    def test_arming_without_a_stock_out_is_409(self):
        status, body = self.pod.add_gpu_sub({"gpu": self.GPU, "datacenter": "EU-RO-1",
                                             "auto_resume": True, "run_id": self.pod.run_id})
        self.assertEqual((status, body["error"]["code"]), (409, "no_failure"))

    def test_arming_another_run_is_409_stale_run(self):
        self._stock_out()
        status, body = self.pod.add_gpu_sub({"gpu": self.GPU, "datacenter": "EU-RO-1",
                                             "auto_resume": True, "run_id": "tg-other"})
        self.assertEqual((status, body["error"]["code"]), (409, "stale_run"))

    def test_arming_without_a_price_is_409(self):
        self._stock_out()
        self.patches["stock_at_cached"].return_value = {self.GPU: [Stock(
            gpu_id=self.GPU, display_name="RTX 5090", price_per_hr=None,
            datacenter_id="EU-RO-1", stock_status="none")]}
        status, body = self.pod.add_gpu_sub({"gpu": self.GPU, "datacenter": "EU-RO-1",
                                             "auto_resume": True, "run_id": self.pod.run_id})
        self.assertEqual((status, body["error"]["code"]), (409, "no_price"))

    def test_arming_moves_the_bolt_and_false_disarms(self):
        self._stock_out()
        self.pod.add_gpu_sub({"gpu": self.GPU, "datacenter": "EU-RO-1",
                              "auto_resume": True, "run_id": self.pod.run_id})
        other = "NVIDIA GeForce RTX 4090"
        self.patches["stock_at_cached"].return_value[other] = [Stock(
            gpu_id=other, display_name="RTX 4090", price_per_hr=0.69,
            datacenter_id="EU-RO-1", stock_status="none")]
        self.pod.add_gpu_sub({"gpu": other, "datacenter": "EU-RO-1",
                              "auto_resume": True, "run_id": self.pod.run_id})
        armed = [s["gpu_id"] for s in bot._gpu_subs_for(ME) if s.get("auto_resume")]
        self.assertEqual(armed, [other])
        self.pod.add_gpu_sub({"gpu": other, "datacenter": "EU-RO-1", "auto_resume": False})
        self.assertFalse(any(s.get("auto_resume") for s in bot._gpu_subs_for(ME)))

    def test_runpodctl_down_while_arming_is_502(self):
        self._stock_out()
        self.patches["stock_at_cached"].side_effect = RuntimeError("timeout")
        status, body = self.pod.add_gpu_sub({"gpu": self.GPU, "datacenter": "EU-RO-1",
                                             "auto_resume": True, "run_id": self.pod.run_id})
        self.assertEqual((status, body["error"]["code"]), (502, "upstream_unavailable"))

    def test_removing_an_unknown_id_is_200(self):
        status, body = self.pod.remove_gpu_sub("ffffff")
        self.assertEqual((status, body), (200, {"subs": []}))

    def test_fired_view_names_the_gpu(self):
        bot._record_gpu_fired(ME, {"sub_id": "abc123", "gpu_id": self.GPU,
                                   "datacenter_id": "EU-RO-1", "stock": "Low",
                                   "usd_per_hr": 0.99, "fired_at": 5.0,
                                   "action": "resume_refused", "reason": "a pod is already live"})
        [fired] = self.pod.gpu_subs()[1]["fired"]
        self.assertEqual(fired, {"sub_id": "abc123", "gpu": self.GPU, "name": "RTX 5090",
                                 "datacenter": "EU-RO-1", "stock": "Low", "usd_per_hr": 0.99,
                                 "fired_at": 5.0, "action": "resume_refused",
                                 "reason": "a pod is already live"})
```

Add to `TestGpuStock`:

```python
    def test_all_dcs_lists_every_datacenter_including_sold_out(self):
        self.patches["stock_at_cached"].return_value = _fake_stock()
        data = bot._gpu_stock_data(force=False, all_dcs=True)
        kwargs = self.patches["stock_at_cached"].call_args_list[-1].kwargs
        self.assertTrue(kwargs.get("include_unavailable"))
        self.assertTrue(data["datacenters"])
        for row in data["datacenters"]:
            self.assertEqual(set(row), {"gpu", "datacenter", "stock", "usd_per_hr"})

    def test_plain_stock_has_no_datacenters_key(self):
        self.patches["stock_at_cached"].return_value = _fake_stock()
        self.assertNotIn("datacenters", bot._gpu_stock_data(force=False))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_botpod.py' -k 'AppPodGpuSubs or GpuStock'`
Expected: FAIL / ERROR, with `AttributeError: 'AppPod' object has no attribute 'add_gpu_sub'` and
`unexpected keyword argument 'all_dcs'`.

- [ ] **Step 3: Implement `all_dcs`** in `_gpu_stock_data`. Change the signature to
`def _gpu_stock_data(*, force: bool, all_dcs: bool = False) -> dict:` and add this just before its
`return`:

```python
    if all_dcs:
        # A GPU sold out at every datacenter is missing from the default
        # output (verified live 2026-09-12), which is exactly when the
        # phone wants to subscribe to it. Its own call, so `gpus` above keeps
        # the default output's meaning (sold_out_everywhere, entries[0]).
        full = (stock_at(wanted, include_unavailable=True) if force
                else stock_at_cached(wanted, include_unavailable=True))
        data["datacenters"] = [
            {"gpu": gpu_id, "datacenter": e.datacenter_id,
             "stock": _plain(e.stock_status), "usd_per_hr": e.price_per_hr or None}
            for gpu_id in wanted for e in full.get(gpu_id) or []]
```

If the function builds its return dict inline instead of in a local `data`, assign it to `data` first
and return `data`.

- [ ] **Step 4: Implement the `AppPod` methods.** Change `gpu_stock` to
`def gpu_stock(self, force: bool, all_dcs: bool = False)` and have it call
`_gpu_stock_data(force=bool(force), all_dcs=bool(all_dcs))`. Then add these after `set_gpu`, plus the
two module-level helpers just above `class AppPod`:

```python
def _gpu_sub_view(sub: dict) -> dict:
    """A sub as the phone sees it — `run_token` stays on the box."""
    armed = sub.get("auto_resume")
    return {"id": sub.get("id") or "", "gpu": sub["gpu_id"],
            "name": _GPU_DISPLAY_SHORT.get(sub["gpu_id"], sub["gpu_id"]),
            "datacenter": sub["datacenter_id"], "created_at": sub.get("created_at"),
            "auto_resume": ({"run_id": armed.get("run_id"),
                             "max_usd_per_hr": armed.get("max_usd_per_hr")}
                            if armed else None)}


def _gpu_fired_view(entry: dict) -> dict:
    gpu = entry.get("gpu_id") or ""
    return {"sub_id": entry.get("sub_id") or "", "gpu": gpu,
            "name": _GPU_DISPLAY_SHORT.get(gpu, gpu),
            "datacenter": entry.get("datacenter_id") or "",
            "stock": entry.get("stock") or "", "usd_per_hr": entry.get("usd_per_hr"),
            "fired_at": entry.get("fired_at") or 0.0,
            "action": entry.get("action") or "notified", "reason": entry.get("reason")}
```

```python
    def gpu_subs(self) -> tuple[int, dict]:
        """`GET /v1/gpu/subs`. Under the lock only because the tick mutates
        `_GPU_SUBS` under it; no network here."""
        with _bot_locked() as busy_response:
            if busy_response is not None:
                return busy_response
            subs = [_gpu_sub_view(s) for s in _gpu_subs_for(self.chat_id)]
            fired = [_gpu_fired_view(e) for e in _gpu_fired_for(self.chat_id)]
        return 200, {"subs": subs, "fired": fired}

    def add_gpu_sub(self, body: dict) -> tuple[int, dict]:
        """`POST /v1/gpu/subs` — an upsert on (gpu, datacenter), so a repeat
        lands on the same entry and no Idempotency-Key is needed (2026-09-27
        spec §1). `auto_resume: true` arms it; the network reads for that run
        before the lock, the file checks under it, `migrate_ask`'s split."""
        body = body if isinstance(body, dict) else {}
        gpu, dc = body.get("gpu"), body.get("datacenter")
        auto = body.get("auto_resume", False)
        run_id = body.get("run_id")
        if gpu not in _GPU_CATALOG:
            return 400, _run_error("bad_request", "gpu must be one of the catalog ids "
                                                  "from GET /v1/gpu/stock")
        if not isinstance(dc, str) or not dc.strip() or len(dc) > 64:
            return 400, _run_error("bad_request", "datacenter is required")
        if not isinstance(auto, bool):
            return 400, _run_error("bad_request", "auto_resume must be true or false")
        if auto and (not isinstance(run_id, str) or not run_id):
            return 400, _run_error("bad_request", "run_id is required to arm auto-resume")
        dc = dc.strip()
        price = None
        if auto:
            if dc != _home_datacenter():
                return 409, _run_error("not_home_dc",
                                       "auto-resume only rents in the volume's home "
                                       "datacenter — elsewhere needs a migration first")
            try:
                stock = stock_at_cached(list(_GPU_CATALOG), include_unavailable=True)
            except RuntimeError as exc:
                return 502, _run_error("upstream_unavailable",
                                       _plain(f"couldn't reach runpodctl: {exc}"))
            entry = next((e for e in stock.get(gpu) or [] if e.datacenter_id == dc), None)
            price = entry.price_per_hr if entry is not None else None
            if not price:
                return 409, _run_error("no_price",
                                       "runpodctl lists no price for this GPU here, so "
                                       "there is no ceiling to arm auto-resume with")
        with _bot_locked() as busy_response:
            if busy_response is not None:
                return busy_response
            if auto:
                manifest_path = _job_manifest_path(self.chat_id)
                if run_id != manifest_path.stem:
                    return 409, _run_error("stale_run",
                                           "the run changed since it was read — read it again")
                failure = read_provision_failure(provision_failure_path(manifest_path))
                if failure is None or not failure.stock_out:
                    return 409, _run_error("no_failure",
                                           "this run has no stock-out to resume from")
            subs = _gpu_subs_for(self.chat_id)
            sub = next((s for s in subs
                        if s["gpu_id"] == gpu and s["datacenter_id"] == dc), None)
            created = sub is None
            if created:
                sub = _new_gpu_sub(gpu, dc, subs)
                subs.append(sub)
            if auto:
                for other in subs:
                    other.pop("auto_resume", None)
                sub["auto_resume"] = {"run_id": run_id, "run_token": _run_token(self.chat_id),
                                      "max_usd_per_hr": price}
            else:
                sub.pop("auto_resume", None)
            _save_gpu_subs(self.chat_id)
            view = _gpu_sub_view(sub)
        return (201 if created else 200), {"sub": view}

    def remove_gpu_sub(self, sub_id: str) -> tuple[int, dict]:
        """`DELETE /v1/gpu/subs/{id}`. An unknown id is 200: the sub may have
        fired (and removed itself) since the phone read the list."""
        with _bot_locked() as busy_response:
            if busy_response is not None:
                return busy_response
            subs = _gpu_subs_for(self.chat_id)
            remaining = [s for s in subs if s.get("id") != sub_id]
            if len(remaining) != len(subs):
                _GPU_SUBS[self.chat_id] = remaining
                _save_gpu_subs(self.chat_id)
            views = [_gpu_sub_view(s) for s in remaining]
        return 200, {"subs": views}
```

`read_provision_failure` and `provision_failure_path` are already imported at the top of `bot.py`
(~L89). `_home_datacenter` is defined in the subscription block above.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_botpod.py'` then `make batch-test`.
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add scripts/tgbot/bot.py scripts/tests/test_batch_control_botpod.py
git commit -m "bot: AppPod lists, upserts and removes GPU subscriptions; stock ?all=1"
```

---

### Task 4: HTTP routes (server)

**Files:**
- Modify: `scripts/httpapi/server.py` (the pod block, ~L328)
- Test: `scripts/tests/test_batch_control_http.py` (`FakeAppPod`, `_POD_ROUTES`, `TestAppPodRoutes`)

**Interfaces:**
- Consumes (Task 3): `AppPod.gpu_subs()`, `add_gpu_sub(body)`, `remove_gpu_sub(sub_id)`,
  `gpu_stock(force, all_dcs)`.
- Produces: `GET /v1/gpu/subs`, `POST /v1/gpu/subs`, `DELETE /v1/gpu/subs/{id}`, and
  `GET /v1/gpu/stock?all=1`.

- [ ] **Step 1: Write the failing tests.** In `FakeAppPod.__init__` add:

```python
        self.gpu_subs_response = (200, {"subs": [], "fired": []})
        self.add_gpu_sub_response = (201, {"sub": {"id": "abc123"}})
        self.remove_gpu_sub_response = (200, {"subs": []})
```

Also change `gpu_stock` and add the three methods:

```python
    def gpu_stock(self, force, all_dcs=False):
        self.calls.append(("gpu_stock", force, all_dcs))
        return self.gpu_stock_response

    def gpu_subs(self):
        self.calls.append(("gpu_subs",))
        return self.gpu_subs_response

    def add_gpu_sub(self, body):
        self.calls.append(("add_gpu_sub", body))
        return self.add_gpu_sub_response

    def remove_gpu_sub(self, sub_id):
        self.calls.append(("remove_gpu_sub", sub_id))
        return self.remove_gpu_sub_response
```

Add to `_POD_ROUTES`:

```python
    ("GET", "/v1/gpu/subs", False, False),
    ("POST", "/v1/gpu/subs", True, False),
    ("DELETE", "/v1/gpu/subs/abc123", False, False),
```

In `test_gpu_stock_parses_force_only_for_the_exact_string_1`, change the expected call to
`("gpu_stock", expected, False)`. Then add to `TestAppPodRoutes`:

```python
    def test_gpu_stock_parses_all_only_for_the_exact_string_1(self):
        for query, expected in (("?all=1", True), ("?all=true", False),
                                ("?all=1&force=1", True), ("", False)):
            with self.subTest(query=query):
                self.fake.calls.clear()
                self.send("GET", "/v1/gpu/stock" + query)
                self.assertEqual(self.fake.calls[0][2], expected)

    def test_gpu_subs_routes_reach_app_pod(self):
        resp, body = self.send("GET", "/v1/gpu/subs")
        self.assertEqual((resp.status, json.loads(body)), self.fake.gpu_subs_response)
        payload = {"gpu": "NVIDIA GeForce RTX 5090", "datacenter": "EU-RO-1",
                   "auto_resume": False}
        resp, body = self.send("POST", "/v1/gpu/subs", json_body=payload)
        self.assertEqual((resp.status, json.loads(body)), self.fake.add_gpu_sub_response)
        resp, body = self.send("DELETE", "/v1/gpu/subs/abc123")
        self.assertEqual((resp.status, json.loads(body)), self.fake.remove_gpu_sub_response)
        self.assertEqual(self.fake.calls, [("gpu_subs",), ("add_gpu_sub", payload),
                                           ("remove_gpu_sub", "abc123")])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_http.py'`
Expected: FAIL (404 on the new paths; the call tuples have no `all_dcs`).

- [ ] **Step 3: Implement** — in `server.py`, replace the `GET gpu/stock` block and add the three
routes right after it:

```python
        if method == "GET" and rest == ["gpu", "stock"]:
            # Only the exact string "1" turns these on, like rent-panel's force:
            # "true" or "11" silently meaning yes would make a paid-for slow call
            # (or a forced runpodctl round trip) depend on a spelling.
            query = parse_qs(urlsplit(self.path).query)
            force = query.get("force", ["0"])[0] == "1"
            all_dcs = query.get("all", ["0"])[0] == "1"
            status, body = self._app_pod().gpu_stock(force, all_dcs)
            return self._send_json(status, body)
        if method == "GET" and rest == ["gpu", "subs"]:
            status, body = self._app_pod().gpu_subs()
            return self._send_json(status, body)
        if method == "POST" and rest == ["gpu", "subs"]:
            # No Idempotency-Key: an upsert on (gpu, datacenter), and not a spend.
            app_pod = self._app_pod()
            payload = self._read_json()
            status, body = app_pod.add_gpu_sub(payload)
            return self._send_json(status, body)
        if method == "DELETE" and len(rest) == 3 and rest[:2] == ["gpu", "subs"]:
            status, body = self._app_pod().remove_gpu_sub(rest[2])
            return self._send_json(status, body)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_http.py'` then `make batch-test`.
Expected: all PASS. This includes the table-driven auth and 503 tests over `_POD_ROUTES`.

- [ ] **Step 5: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add scripts/httpapi/server.py scripts/tests/test_batch_control_http.py
git commit -m "phone API: GET/POST/DELETE /v1/gpu/subs and GET /v1/gpu/stock?all=1"
```

---

### Task 5: MotionKit models, `GpuStore` asks `all=1`, and `GpuSubsStore`

**Files:**
- Create: `ios/MotionKit/Sources/MotionKit/Models/GpuSubs.swift`
- Modify: `ios/MotionKit/Sources/MotionKit/Models/PodCost.swift`
- Modify: `ios/MotionKit/Sources/MotionKit/Stores/GpuStore.swift`
- Create: `ios/MotionKit/Sources/MotionKit/Stores/GpuSubsStore.swift`
- Test: `ios/MotionKit/Tests/MotionKitTests/GpuSubsStoreTests.swift` (new), `GpuAndBalanceStoreTests.swift`, `Fixtures.swift`

**Interfaces:**
- Consumes (Task 4): the JSON shapes above.
- Produces (Swift, all `public`):
  - `struct GpuSub: Decodable, Sendable, Equatable, Identifiable { id, gpu, name, datacenter: String; createdAt: Double?; autoResume: GpuSubAutoResume? }`
  - `struct GpuSubAutoResume { runId: String; maxUsdPerHr: Double }`
  - `struct GpuSubFiring: Identifiable { subId, gpu, name, datacenter, stock: String; usdPerHr: Double?; firedAt: Double; action: String; reason: String?; var id: String { "\(subId)@\(firedAt)" }; var resumed: Bool; var refused: Bool }`
  - `struct GpuDatacenter: Identifiable { gpu, datacenter, stock: String; usdPerHr: Double?; var soldOut: Bool; var id: String }`
  - `GpuStock.datacenters: [GpuDatacenter]?` and `GpuStock.datacenters(for gpu: String) -> [GpuDatacenter]`
  - `GpuSubsStore(client:defaults:)` with `subs`, `fired`, `error`, `inFlight: Set<String>`, `message`, `load()`,
    `sub(gpu:datacenter:)`, `watching(gpu:)`, `watch(gpu:datacenter:autoResumeRunID:) -> Bool`, `unwatch(_:)`,
    `unseen`, `markSeen()`, `armed`, `dismissMessage()`.

- [ ] **Step 1: Write the failing tests** — create `GpuSubsStoreTests.swift`:

```swift
import Foundation
import Testing
@testable import MotionKit

extension URLProtocolTests {
@Suite @MainActor struct GpuSubsStoreTests {
    static let listing = #"""
    {"subs": [{"id": "abc123", "gpu": "NVIDIA GeForce RTX 5090", "name": "RTX 5090",
               "datacenter": "EU-RO-1", "created_at": 1.0,
               "auto_resume": {"run_id": "tg-1", "max_usd_per_hr": 0.99}}],
     "fired": [{"sub_id": "old001", "gpu": "NVIDIA GeForce RTX 4090", "name": "RTX 4090",
                "datacenter": "EU-RO-1", "stock": "Low", "usd_per_hr": 0.69,
                "fired_at": 100.0, "action": "notified", "reason": null}]}
    """#

    final class Routes: @unchecked Sendable {
        private let lock = NSLock()
        private var _listing = GpuSubsStoreTests.listing
        private var _post = TestSupport.json(#"{"sub": {"id": "new001", "gpu": "NVIDIA GeForce RTX 5090", "name": "RTX 5090", "datacenter": "EU-CZ-1", "created_at": 2.0, "auto_resume": null}}"#, status: 201)
        var listing: String { get { lock.withLock { _listing } } set { lock.withLock { _listing = newValue } } }
        var post: (Int, [String: String], Data) { get { lock.withLock { _post } } set { lock.withLock { _post = newValue } } }
        func answer(_ request: URLRequest) -> (Int, [String: String], Data) {
            switch (request.httpMethod ?? "GET", request.url?.path ?? "") {
            case ("GET", "/v1/gpu/subs"): return TestSupport.json(listing)
            case ("POST", "/v1/gpu/subs"): return post
            case ("DELETE", "/v1/gpu/subs/abc123"): return TestSupport.json(#"{"subs": []}"#)
            default: return (404, [:], Data())
            }
        }
    }

    private func store(_ routes: Routes, defaults: UserDefaults? = nil) -> (GpuSubsStore, UserDefaults) {
        StubURLProtocol.install { routes.answer($0) }
        let d = defaults ?? UserDefaults(suiteName: "gpusubs-\(UUID().uuidString)")!
        return (GpuSubsStore(client: TestSupport.client(), defaults: d), d)
    }

    @Test func loadsSubsAndFirings() async {
        let (store, _) = store(Routes())
        await store.load()
        #expect(store.subs.map(\.id) == ["abc123"])
        #expect(store.armed?.autoResume?.maxUsdPerHr == 0.99)
        #expect(store.watching(gpu: "NVIDIA GeForce RTX 5090").count == 1)
        #expect(store.fired.first?.subId == "old001")
    }

    @Test func firstLoadMarksExistingFiringsSeen() async {
        let (store, _) = store(Routes())
        await store.load()
        #expect(store.unseen.isEmpty)
    }

    @Test func aNewerFiringIsUnseenUntilMarked() async {
        let routes = Routes()
        let (store, defaults) = store(routes)
        await store.load()
        routes.listing = GpuSubsStoreTests.listing.replacingOccurrences(of: "\"fired_at\": 100.0", with: "\"fired_at\": 200.0")
        await store.load()
        #expect(store.unseen.count == 1)
        store.markSeen()
        #expect(store.unseen.isEmpty)
        let (again, _) = self.store(routes, defaults: defaults)
        await again.load()
        #expect(again.unseen.isEmpty)
    }

    @Test func watchPostsSnakeCaseThenReloads() async throws {
        let (store, _) = store(Routes())
        let ok = await store.watch(gpu: "NVIDIA GeForce RTX 5090", datacenter: "EU-CZ-1", autoResumeRunID: nil)
        #expect(ok)
        let post = try #require(StubURLProtocol.requests.first { $0.httpMethod == "POST" })
        let body = try JSONSerialization.jsonObject(with: StubURLProtocol.body(of: post)) as? [String: Any]
        #expect(body?["auto_resume"] as? Bool == false)
        #expect(body?["run_id"] == nil)
        #expect(StubURLProtocol.requests.last?.url?.path == "/v1/gpu/subs")
        #expect(StubURLProtocol.requests.last?.httpMethod == "GET")
    }

    @Test func armingSendsTheRunID() async throws {
        let (store, _) = store(Routes())
        _ = await store.watch(gpu: "NVIDIA GeForce RTX 5090", datacenter: "EU-RO-1", autoResumeRunID: "tg-1")
        let post = try #require(StubURLProtocol.requests.first { $0.httpMethod == "POST" })
        let body = try JSONSerialization.jsonObject(with: StubURLProtocol.body(of: post)) as? [String: Any]
        #expect(body?["auto_resume"] as? Bool == true)
        #expect(body?["run_id"] as? String == "tg-1")
    }

    @Test func aRefusedArmShowsTheServerMessage() async {
        let routes = Routes()
        routes.post = TestSupport.json(#"{"error": {"code": "not_home_dc", "message": "auto-resume only rents in the volume's home datacenter"}}"#, status: 409)
        let (store, _) = store(routes)
        let ok = await store.watch(gpu: "NVIDIA GeForce RTX 5090", datacenter: "EU-CZ-1", autoResumeRunID: "tg-1")
        #expect(!ok)
        #expect(store.message?.contains("home datacenter") == true)
        #expect(store.inFlight.isEmpty)
    }

    @Test func unwatchRemovesLocally() async {
        let (store, _) = store(Routes())
        await store.load()
        await store.unwatch(store.subs[0])
        #expect(store.subs.isEmpty)
    }
}
}
```

If `StubURLProtocol` has no `body(of:)` helper, add one to `StubURLProtocol.swift`. It reads
`request.httpBodyStream` fully, because URLSession moves the body into the stream:

```swift
    static func body(of request: URLRequest) -> Data {
        if let data = request.httpBody { return data }
        guard let stream = request.httpBodyStream else { return Data() }
        stream.open(); defer { stream.close() }
        var data = Data()
        let buffer = UnsafeMutablePointer<UInt8>.allocate(capacity: 4096)
        defer { buffer.deallocate() }
        while stream.hasBytesAvailable {
            let n = stream.read(buffer, maxLength: 4096)
            if n <= 0 { break }
            data.append(buffer, count: n)
        }
        return data
    }
```

(Check first with `grep -n "httpBodyStream" ios/MotionKit/Tests/MotionKitTests/*.swift`, and reuse
an existing helper if there is one.)

In `GpuAndBalanceStoreTests.stockLoadsCachedAndForceAsksForFresh`, change the two query expectations
to `== "all=1"` and `== "all=1&force=1"`. Add a decode test there too:

```swift
    @Test func datacentersDecodeWhenPresent() async {
        let routes = Routes()
        routes.stock = TestSupport.json(Fixtures.gpuStock.replacingOccurrences(
            of: "\"other_regions\"",
            with: "\"datacenters\": [{\"gpu\": \"NVIDIA GeForce RTX 5090\", \"datacenter\": \"EU-RO-1\", \"stock\": \"none\", \"usd_per_hr\": 0.99}], \"other_regions\""))
        let (store, _) = gpuStore(routes)
        await store.load()
        let rows = store.stock?.datacenters(for: "NVIDIA GeForce RTX 5090") ?? []
        #expect(rows.map(\.datacenter) == ["EU-RO-1"])
        #expect(rows.first?.soldOut == true)
    }
```

(Check that `Fixtures.gpuStock` contains the key `"other_regions"`; if its formatting differs, match
it.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `make ios-test`
Expected: compile errors (`cannot find 'GpuSubsStore' in scope`).

- [ ] **Step 3: Implement the models** — create `Models/GpuSubs.swift`:

```swift
import Foundation

/// `GET /v1/gpu/subs` (bot.py `AppPod.gpu_subs`, 2026-09-27 spec §1): the
/// bot's one-shot stock watches — the same list `/subscribe` writes — and the
/// last ten firings, newest first.
public struct GpuSubs: Decodable, Sendable, Equatable {
    public let subs: [GpuSub]
    public let fired: [GpuSubFiring]
}

public struct GpuSub: Decodable, Sendable, Equatable, Identifiable {
    public let id: String
    public let gpu: String
    public let name: String
    public let datacenter: String
    public let createdAt: Double?
    /// Set on at most one sub: when it fires, the stuck run resumes on its own.
    public let autoResume: GpuSubAutoResume?
}

public struct GpuSubAutoResume: Decodable, Sendable, Equatable {
    public let runId: String
    /// The GPU's $/h when armed; a firing above it only notifies.
    public let maxUsdPerHr: Double
}

public struct GpuSubFiring: Decodable, Sendable, Equatable, Identifiable {
    public let subId: String
    public let gpu: String
    public let name: String
    public let datacenter: String
    public let stock: String
    public let usdPerHr: Double?
    public let firedAt: Double
    /// "notified", "resumed" or "resume_refused".
    public let action: String
    public let reason: String?

    public var id: String { "\(subId)@\(firedAt)" }
    public var resumed: Bool { action == "resumed" }
    public var refused: Bool { action == "resume_refused" }
}

/// `POST /v1/gpu/subs` — an upsert on (gpu, datacenter).
public struct GpuSubRequest: Encodable, Sendable {
    public let gpu: String
    public let datacenter: String
    public let autoResume: Bool
    public let runId: String?
}

public struct GpuSubResponse: Decodable, Sendable { public let sub: GpuSub }
public struct GpuSubsRemaining: Decodable, Sendable { public let subs: [GpuSub] }
```

In `PodCost.swift`, add to `GpuStock` (after `otherRegions`):

```swift
    /// Every datacenter runpodctl lists per GPU, sold-out ones included —
    /// only with `?all=1`, which `GpuStore` always asks for.
    public let datacenters: [GpuDatacenter]?

    public func datacenters(for gpu: String) -> [GpuDatacenter] {
        (datacenters ?? []).filter { $0.gpu == gpu }
    }
```

and, at file level:

```swift
public struct GpuDatacenter: Decodable, Sendable, Equatable, Identifiable {
    public let gpu: String
    public let datacenter: String
    public let stock: String
    public let usdPerHr: Double?
    public var soldOut: Bool { stock.lowercased() == "none" }
    public var id: String { "\(gpu)@\(datacenter)" }
}
```

`GpuStock` has a memberwise init only if something constructs it in code. Grep `GpuStock(` under
`ios/`; if any caller exists, pass `datacenters: nil` there.

- [ ] **Step 4: `GpuStore.load` asks `all=1`.** Replace the two `client.get` branches with:

```swift
            var query = [URLQueryItem(name: "all", value: "1")]
            if force { query.append(URLQueryItem(name: "force", value: "1")) }
            var fresh = try await client.get(GpuStock.self, query: query, timeout: 60, "v1", "gpu", "stock")
```

Also update the doc comment: "Always `?all=1`: the Pod stage's GPU sheet lists every datacenter,
sold-out ones included (2026-09-27)."

- [ ] **Step 5: Implement `GpuSubsStore`** — create `Stores/GpuSubsStore.swift`:

```swift
import Foundation
import Observation

/// `GET/POST/DELETE /v1/gpu/subs` (2026-09-27 spec §2). The Telegram message
/// is the notification; this store only lets the phone see and change the
/// list, and tells the stage which firings are new since the app last looked.
@MainActor @Observable
public final class GpuSubsStore {
    public private(set) var subs: [GpuSub] = []
    public private(set) var fired: [GpuSubFiring] = []
    public private(set) var error: APIError?
    /// "gpu|datacenter" keys with a request in flight.
    public private(set) var inFlight: Set<String> = []
    public private(set) var message: String?
    private var lastSeen: Double

    private let client: APIClient
    private let defaults: UserDefaults
    static let seenKey = "gpuSubs.lastSeenFiredAt"

    public init(client: APIClient, defaults: UserDefaults = .standard) {
        self.client = client
        self.defaults = defaults
        lastSeen = defaults.object(forKey: Self.seenKey) as? Double ?? -1
    }

    public func load() async {
        do {
            let fresh = try await client.get(GpuSubs.self, "v1", "gpu", "subs")
            subs = fresh.subs
            fired = fresh.fired
            error = nil
            // First read on this install: what already fired is history, not news.
            if lastSeen < 0 { markSeen() }
        } catch {
            self.error = error
        }
    }

    public func sub(gpu: String, datacenter: String) -> GpuSub? {
        subs.first { $0.gpu == gpu && $0.datacenter == datacenter }
    }

    public func watching(gpu: String) -> [GpuSub] { subs.filter { $0.gpu == gpu } }

    public var armed: GpuSub? { subs.first { $0.autoResume != nil } }

    public var unseen: [GpuSubFiring] { fired.filter { $0.firedAt > lastSeen } }

    public func markSeen() {
        lastSeen = fired.map(\.firedAt).max() ?? Date().timeIntervalSince1970
        defaults.set(lastSeen, forKey: Self.seenKey)
    }

    public func dismissMessage() { message = nil }

    /// Subscribes (or re-subscribes) the pair. With a run id it also arms
    /// auto-resume, which the server refuses outside home or without a
    /// stock-out; the refusal is shown as `message`.
    @discardableResult
    public func watch(gpu: String, datacenter: String, autoResumeRunID: String?) async -> Bool {
        let key = "\(gpu)|\(datacenter)"
        guard !inFlight.contains(key) else { return false }
        inFlight.insert(key)
        defer { inFlight.remove(key) }
        do {
            _ = try await client.post(
                GpuSubResponse.self,
                body: GpuSubRequest(gpu: gpu, datacenter: datacenter,
                                    autoResume: autoResumeRunID != nil, runId: autoResumeRunID),
                "v1", "gpu", "subs")
            message = nil
            // Arming moves the bolt off any other sub; re-read rather than guess.
            await load()
            return true
        } catch {
            message = error.userMessage
            return false
        }
    }

    public func unwatch(_ sub: GpuSub) async {
        let key = "\(sub.gpu)|\(sub.datacenter)"
        guard !inFlight.contains(key) else { return }
        inFlight.insert(key)
        defer { inFlight.remove(key) }
        do {
            subs = try await client.delete(GpuSubsRemaining.self, "v1", "gpu", "subs", sub.id).subs
            message = nil
        } catch {
            message = error.userMessage
        }
    }
}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `make ios-test`
Expected: all pass. The previous count was 320; expect about 330.

- [ ] **Step 7: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionKit
git commit -m "MotionKit: GPU subscription models and GpuSubsStore; stock asks all=1"
```

---

### Task 6: The Pod stage (hero, GPU tiles, More menu, Balance sheet)

**Files:**
- Rewrite: `ios/MotionApp/Pod/PodView.swift`
- Create: `ios/MotionApp/Pod/PodHero.swift` (`PodHero`, plus `LeaseCard` and `MigrationCard` moved from PodView)
- Create: `ios/MotionApp/Pod/GpuTile.swift`
- Create: `ios/MotionApp/Pod/BalanceSheet.swift` (holds `BalanceSection`, moved unchanged)
- Create: `ios/MotionApp/Components/SwipeUpToDismiss.swift` (moved out of `NewJobView.swift`, made non-private)
- Modify: `ios/MotionApp/MotionApp.swift` (`AppModel.gpuSubs`), `ios/MotionApp/RootView.swift`

**Interfaces:**
- Consumes (Task 5): `GpuSubsStore`, `GpuStock.datacenters(for:)`, `GpuSubsStore.watching(gpu:)`.
- Produces:
  - `PodView(pod:gpu:balance:flow:runs:subs:)`.
  - `GpuTile(row: GpuStockRow, selected: Bool, watching: Int, armed: Bool, selecting: Bool, action: () -> Void)`,
    with accessibility identifier `gpu.tile.<catalog id>`.
  - `AppModel.gpuSubs: GpuSubsStore?`.
  - Identifiers kept for the smoke: `pod.none`, `pod.moveVolume` and `pod.checkVast` (now menu items
    inside `pod.more`), plus `pod.hero` and `pod.stage`.

Layout contract: the stage is a `VStack` inside a `GeometryReader`. Hero on top; below it a GPU
section header ("GPU" plus a refresh icon); then the tile grid, which takes whatever height is left
(two columns, three rows, each tile's height = `(available − 2·spacing) / 3`, clamped to 64…110). A
bottom padding reserves `WatchDrawer.collapsedHeight + 8`. Task 7 adds the drawer overlay. There is
no `List` and no `ScrollView` on the stage.

- [ ] **Step 1: Move `SwipeUpToDismiss`.** Cut the `private struct SwipeUpToDismiss` from the end of
  `NewJobView.swift` into `Components/SwipeUpToDismiss.swift` as `struct SwipeUpToDismiss` (drop
  `private`), with `import SwiftUI` and the same body and doc comment. Then build:
  `make ios-build`. Expected: success.

- [ ] **Step 2: Add `gpuSubs` to `AppModel`.** In `MotionApp.swift`:
  - next to `private(set) var gpu: GpuStore?`, add `private(set) var gpuSubs: GpuSubsStore?`;
  - in `reconnect()`'s nil branch, add `gpuSubs = nil`;
  - after `gpu = GpuStore(...)`, add `gpuSubs = GpuSubsStore(client: client)`.

  In `RootView.swift`, unwrap it with the other stores (follow how `gpu` is unwrapped in the same
  `if let` chain) and pass `subs: gpuSubs` to `PodView`.

- [ ] **Step 3: Create `PodHero.swift`.** Move `LeaseCard` and `MigrationCard` here from
  `PodView.swift` unchanged, and add:

```swift
import SwiftUI
import MotionKit

/// The top of the Pod stage: what is billing right now, and what it can
/// still spend. One surface; a migration replaces it while it runs.
struct PodHero: View {
    let pod: PodStore
    let balance: BalanceStore
    let runs: RunsStore
    let onBalance: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let migration = pod.pod?.migration, migration.running {
                MigrationCard(migration: migration)
            } else if let status = pod.pod, let lease = status.lease {
                LeaseCard(gpu: status.gpu, lease: lease)
            } else if pod.pod != nil {
                Label("No pod running", systemImage: "moon.zzz")
                    .font(.headline)
                    .foregroundStyle(Theme.secondary)
                    .accessibilityIdentifier("pod.none")
            } else if let error = pod.error {
                ErrorBanner(error: error) { await pod.refresh() }
            } else {
                LoadingBlock()
            }
            balanceLine
            if let status = pod.pod {
                if pod.showsKill(runStatus: runs.live?.status), let runID = status.runId {
                    KillButton(pod: pod, runID: runID, hasLease: status.lease != nil)
                } else {
                    KillNotice(pod: pod)
                }
            }
            if pod.isStale {
                HStack {
                    StaleTag(lastSuccess: pod.lastSuccess)
                    Spacer()
                    Button("Retry") { Task { await pod.refresh() } }.buttonStyle(.borderless)
                }
            }
        }
        .opacity(pod.isStale ? 0.6 : 1)
        .heroSurface()
        .accessibilityIdentifier("pod.hero")
    }

    /// Runway is the number that decides whether to rent; the rest of the
    /// balance (Vast credit, errors) is one tap away.
    private var balanceLine: some View {
        Button(action: onBalance) {
            HStack(spacing: 6) {
                Image(systemName: "creditcard").foregroundStyle(Theme.secondary)
                Text(balance.runpodLine ?? (balance.error == nil ? "Reading balance…" : "Balance unavailable"))
                    .font(.subheadline.monospacedDigit())
                    .foregroundStyle(balance.balance?.runpod?.lowRunway == true ? Theme.warning : Theme.label)
                Spacer(minLength: 0)
                Image(systemName: "chevron.right").font(.footnote.weight(.semibold)).foregroundStyle(Theme.secondary)
            }
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("pod.balance")
    }
}
```

When moving `LeaseCard`, **delete its trailing `.heroSurface()`**: `PodHero` is now the surface, and
a second one inside it would draw a card within a card. `PodView` is the only other caller today
(`grep -rn "LeaseCard(" ios/MotionApp`), and it goes away in Step 6.

- [ ] **Step 4: Create `BalanceSheet.swift`.** Move `BalanceSection` here unchanged, and add:

```swift
import SwiftUI
import MotionKit

struct BalanceSheet: View {
    let store: BalanceStore
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List { BalanceSection(store: store) }
                .navigationTitle("Balance")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
                .refreshable { await store.load() }
        }
        .presentationDetents([.medium])
    }
}
```

- [ ] **Step 5: Create `GpuTile.swift`:**

```swift
import SwiftUI
import MotionKit

/// One of the five GPUs on the Pod stage: its home stock at a glance, the
/// price, whether it is the next rental's card, and whether it is watched.
/// Tapping opens the GPU sheet; nothing here spends or rewrites `.env`.
struct GpuTile: View {
    let row: GpuStockRow
    let selected: Bool
    let watching: Int
    let armed: Bool
    let height: CGFloat
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 6) {
                    Circle().fill(dotColor).frame(width: 10, height: 10)
                    Text(row.name).font(.headline).lineLimit(1).minimumScaleFactor(0.8)
                    Spacer(minLength: 0)
                    if selected {
                        Image(systemName: "checkmark.circle.fill").foregroundStyle(Theme.accent)
                    }
                }
                Text(stockLine).font(.subheadline.monospacedDigit())
                    .foregroundStyle(row.soldOutEverywhere ? Theme.warning : Theme.secondary)
                    .lineLimit(1)
                Spacer(minLength: 0)
                if watching > 0 {
                    Label(armed ? "Watching · auto-resume" : "Watching", systemImage: armed ? "bolt.fill" : "bell.fill")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(Theme.accent)
                        .lineLimit(1)
                }
            }
            .padding(12)
            .frame(maxWidth: .infinity, minHeight: height, maxHeight: height, alignment: .topLeading)
            .background(Theme.surface, in: .rect(cornerRadius: Theme.Radius.medium))
            .overlay {
                if selected {
                    RoundedRectangle(cornerRadius: Theme.Radius.medium).stroke(Theme.accent, lineWidth: 1.5)
                }
            }
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(selected ? .isSelected : [])
        .accessibilityIdentifier("gpu.tile.\(row.gpu)")
    }

    private var status: String { row.home?.stock.lowercased() ?? (row.soldOutEverywhere ? "none" : "") }

    private var dotColor: Color {
        switch status {
        case "high": .green
        case "medium": .yellow
        case "low": .orange
        case "none": Theme.danger
        default: Theme.secondary
        }
    }

    private var stockLine: String {
        if row.soldOutEverywhere { return "Sold out everywhere" }
        let price = row.usdPerHr.map { "\(Format.usd($0))/h" } ?? "price ?"
        return "\(price) · \(row.home?.stock ?? "not at home")"
    }
}
```

- [ ] **Step 6: Rewrite `PodView.swift`.** It keeps the same inputs plus `subs`. The drawer comes in
  Task 7; for now, reserve its height:

```swift
import SwiftUI
import MotionKit

/// The Pod tab as one stage that does not scroll (2026-09-27 spec §2): the
/// hero, five GPU tiles sized to the space left, and the Watching drawer
/// below. The old List ran past the screen with the balance, five rows and
/// Move volume stacked; those now live in the hero, the tiles and the ⋯ menu.
struct PodView: View {
    let pod: PodStore
    let gpu: GpuStore
    let balance: BalanceStore
    let flow: RunFlow
    let runs: RunsStore
    let subs: GpuSubsStore
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase
    @State private var openGpu: GpuSheetTarget?
    @State private var showBalance = false

    var body: some View {
        GeometryReader { proxy in
            VStack(alignment: .leading, spacing: 12) {
                PodHero(pod: pod, balance: balance, runs: runs, onBalance: { showBalance = true })
                gpuHeader
                tiles
            }
            .padding(.horizontal, 16)
            .padding(.top, 8)
            .padding(.bottom, WatchDrawer.collapsedHeight + 8)
            .frame(width: proxy.size.width, height: proxy.size.height, alignment: .top)
        }
        .accessibilityIdentifier("pod.stage")
        .navigationTitle("Pod")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar { ToolbarItem(placement: .topBarTrailing) { moreMenu } }
        .sheet(isPresented: $showBalance) { BalanceSheet(store: balance) }
        .task {
            async let a: Void = pod.refresh()
            async let b: Void = balance.load()
            async let c: Void = runs.refresh()
            async let d: Void = subs.load()
            if gpu.stock == nil { await gpu.load() }
            _ = await (a, b, c, d)
        }
        // Scoped to a visible Pod tab in an active scene; cancelled otherwise.
        .task(id: scenePhase) {
            guard scenePhase == .active else { return }
            await subs.load()
            await pod.pollMigration()
        }
    }

    private var gpuHeader: some View {
        HStack {
            Text("GPU").font(.subheadline.weight(.semibold)).foregroundStyle(Theme.secondary)
            if let message = gpu.message {
                Text(message).font(.footnote).foregroundStyle(Theme.warning).lineLimit(1)
            } else if gpu.isStale {
                Text("Couldn't reach runpodctl — last list").font(.footnote).foregroundStyle(Theme.warning).lineLimit(1)
            }
            Spacer()
            Button { Task { await gpu.load(force: true) } } label: {
                ZStack {
                    Image(systemName: "arrow.clockwise").opacity(gpu.isLoading ? 0 : 1)
                    if gpu.isLoading { ProgressView().controlSize(.small) }
                }
                .font(.subheadline.weight(.semibold)).foregroundStyle(Theme.secondary)
                .frame(minWidth: 44, minHeight: 32, alignment: .trailing)
                .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .disabled(gpu.isLoading)
            .accessibilityLabel("Refresh GPU stock")
            .accessibilityIdentifier("gpu.refresh")
        }
    }

    @ViewBuilder private var tiles: some View {
        if let stock = gpu.stock {
            GeometryReader { proxy in
                let spacing: CGFloat = 10
                let rows = CGFloat((stock.gpus.count + 1) / 2)
                let height = min(max((proxy.size.height - spacing * (rows - 1)) / max(rows, 1), 64), 110)
                LazyVGrid(columns: [GridItem(.flexible(), spacing: spacing), GridItem(.flexible(), spacing: spacing)],
                          spacing: spacing) {
                    ForEach(stock.gpus) { row in
                        let watched = subs.watching(gpu: row.gpu)
                        GpuTile(row: row, selected: row.gpu == stock.selected, watching: watched.count,
                                armed: watched.contains { $0.autoResume != nil }, height: height) {
                            openGpu = GpuSheetTarget(gpu: row.gpu)
                        }
                    }
                }
            }
            .opacity(gpu.isStale ? 0.6 : 1)
        } else if let error = gpu.error {
            ErrorBanner(error: error) { await gpu.load() }
        } else {
            LoadingBlock(title: "Reading stock…")
        }
    }

    private var moreMenu: some View {
        Menu {
            Button("Refresh stock", systemImage: "arrow.clockwise") { Task { await gpu.load(force: true) } }
            Button("Move volume…", systemImage: "externaldrive.badge.plus") {
                model.migrateSheet = MigrateRequest(destination: nil)
            }
            .accessibilityIdentifier("pod.moveVolume")
            Button(balance.isLoadingVast ? "Reading Vast credit…" : "Check Vast credit", systemImage: "cloud") {
                showBalance = true
                Task { await balance.loadVast() }
            }
            .disabled(balance.isLoadingVast)
            .accessibilityIdentifier("pod.checkVast")
        } label: {
            Image(systemName: "ellipsis.circle")
        }
        .accessibilityLabel("More")
        .accessibilityIdentifier("pod.more")
    }
}

/// Which GPU the sheet is open for.
struct GpuSheetTarget: Identifiable {
    let gpu: String
    var id: String { gpu }
}
```

`WatchDrawer.collapsedHeight` is referenced before Task 7 creates it. So add a one-line stub file
`ios/MotionApp/Pod/WatchDrawer.swift` now:

```swift
import SwiftUI
struct WatchDrawer { static let collapsedHeight: CGFloat = 48 }
```

Task 7 replaces this stub with the full view.

Pull-to-refresh is gone along with the `List`. The refresh icon in the GPU header and the menu's
"Refresh stock" replace it, and hero refreshes happen on `.task` and `scenePhase`. The old
`.refreshable` also reloaded runs and balance; the Balance sheet keeps its own `.refreshable`.

- [ ] **Step 7: Build and look at it.**

Run: `make ios-build`
Expected: success. Then run the app on the iPhone 18 Pro Max and iPhone SE (3rd gen) simulators,
open Pod, and screenshot:

```bash
xcrun simctl io booted screenshot out/pod-stage/stage-promax.png
```

Check that the five tiles and the reserved drawer band are all visible without scrolling. Following
the [[offer-ui-options-before-building]] memory, attach both screenshots when reporting this task.

- [ ] **Step 8: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionApp
git commit -m "iOS Pod tab: one stage with a hero, GPU tiles and a More menu"
```

---

### Task 7: GPU sheet, Watching drawer and the fired banner

**Files:**
- Create: `ios/MotionApp/Pod/GpuSheet.swift`
- Replace stub: `ios/MotionApp/Pod/WatchDrawer.swift`
- Modify: `ios/MotionApp/Pod/PodView.swift` (sheet, drawer overlay, banner)

**Interfaces:**
- Consumes: `GpuSubsStore` (Task 5); `PodView`, `GpuSheetTarget`, `SwipeUpToDismiss` (Task 6);
  `GpuStore.select(_:whileSpending:)`; `PodStatus.failedRental`, `PodStatus.runId`;
  `AppModel.migrateSheet`.
- Produces: `GpuSheet(gpu:stock:subs:gpuStore:pod:spending:onMigrate:)`,
  `WatchDrawer(subs:level:)`, and identifiers `gpu.sheet`, `gpu.use`, `gpu.dc.<datacenter>`,
  `gpu.bell.<datacenter>`, `gpu.bolt.<datacenter>`, `gpu.migrate.<datacenter>`, `pod.watch`, `pod.watchScrim`, `pod.firedBanner`.

- [ ] **Step 1: Create `GpuSheet.swift`:**

```swift
import SwiftUI
import MotionKit

/// One GPU's datacenters, sold-out ones included (`?all=1`), each with its
/// bell (2026-09-27 spec §2). The home row can also arm auto-resume when
/// the run is stuck on a stock-out; other rows offer Migrate and say why
/// renting there is not instant.
struct GpuSheet: View {
    let gpu: String
    let stock: GpuStock
    let subs: GpuSubsStore
    let gpuStore: GpuStore
    let pod: PodStore
    let spending: Bool
    let onMigrate: (String) -> Void
    @Environment(\.dismiss) private var dismiss

    private var row: GpuStockRow? { stock.gpus.first { $0.gpu == gpu } }
    private var selected: Bool { stock.selected == gpu }
    /// The run auto-resume may attach to, or nil.
    private var stuckRunID: String? {
        guard pod.pod?.failedRental?.stockOut == true, pod.pod?.lease == nil else { return nil }
        return pod.pod?.runId
    }

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Button {
                        Task { if await gpuStore.select(gpu, whileSpending: spending) { dismiss() } }
                    } label: {
                        HStack {
                            Label(selected ? "Next rental uses this GPU" : "Use for next rental",
                                  systemImage: selected ? "checkmark.circle.fill" : "circle")
                            Spacer()
                            if gpuStore.selecting == gpu { ProgressView() }
                        }
                    }
                    .disabled(selected || spending || gpuStore.selecting != nil)
                    .accessibilityIdentifier("gpu.use")
                } footer: {
                    if spending { Text("A spend request is in flight — the GPU can't change until it's answered.") }
                    else if pod.pod?.lease != nil { Text("A change applies to the next rental.") }
                }
                Section {
                    let rows = stock.datacenters(for: gpu)
                    if rows.isEmpty {
                        Text("runpodctl lists no datacenter for this GPU right now.")
                            .foregroundStyle(Theme.secondary)
                    }
                    ForEach(rows) { dc in datacenterRow(dc) }
                } header: {
                    Text("Datacenters")
                } footer: {
                    Text("🔔 sends one Telegram message the moment it has stock, then clears itself. "
                         + "Only 📍 is rentable now; elsewhere needs the volume synced first.")
                }
                if let message = subs.message {
                    Section { Text(message).font(.footnote).foregroundStyle(Theme.warning) }
                }
            }
            .navigationTitle(row?.name ?? gpu)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
            .onDisappear { subs.dismissMessage() }
        }
        .presentationDetents([.medium, .large])
        .accessibilityIdentifier("gpu.sheet")
    }

    private func datacenterRow(_ dc: GpuDatacenter) -> some View {
        let home = dc.datacenter == stock.homeDatacenter
        let sub = subs.sub(gpu: gpu, datacenter: dc.datacenter)
        let busy = subs.inFlight.contains("\(gpu)|\(dc.datacenter)")
        return HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                Text((home ? "📍 " : "") + dc.datacenter).font(.body)
                Text("\(dc.stock) · " + (dc.usdPerHr.map { "\(Format.usd($0))/h" } ?? "price ?"))
                    .font(.subheadline.monospacedDigit())
                    .foregroundStyle(dc.soldOut ? Theme.warning : Theme.secondary)
            }
            Spacer()
            if busy { ProgressView() }
            if home, let runID = stuckRunID {
                boltButton(dc, sub: sub, runID: runID)
            } else if !home {
                // Visible, not a long-press: the old GPU row's globe menu was
                // the only way to find Migrate, and this replaces it.
                Button { dismiss(); onMigrate(dc.datacenter) } label: {
                    Image(systemName: "airplane.departure").foregroundStyle(Theme.secondary)
                        .frame(minWidth: 44, minHeight: 44)
                }
                .buttonStyle(.borderless)
                .accessibilityLabel("Migrate to \(dc.datacenter)")
                .accessibilityIdentifier("gpu.migrate.\(dc.datacenter)")
            }
            bellButton(dc, sub: sub)
        }
        .disabled(busy)
        .accessibilityIdentifier("gpu.dc.\(dc.datacenter)")
    }

    private func bellButton(_ dc: GpuDatacenter, sub: GpuSub?) -> some View {
        Button {
            Task {
                if let sub { await subs.unwatch(sub) }
                else { await subs.watch(gpu: gpu, datacenter: dc.datacenter, autoResumeRunID: nil) }
            }
        } label: {
            Image(systemName: sub == nil ? "bell" : "bell.fill")
                .foregroundStyle(sub == nil ? Theme.secondary : Theme.accent)
                .frame(minWidth: 44, minHeight: 44)
        }
        .buttonStyle(.borderless)
        .accessibilityLabel(sub == nil ? "Watch \(dc.datacenter)" : "Stop watching \(dc.datacenter)")
        .accessibilityIdentifier("gpu.bell.\(dc.datacenter)")
    }

    /// Arms (or disarms) auto-resume. The quoted ceiling is the price the
    /// server will store — the $/h shown on this row right now.
    private func boltButton(_ dc: GpuDatacenter, sub: GpuSub?, runID: String) -> some View {
        let armed = sub?.autoResume != nil
        return Menu {
            if armed {
                Button("Notify only", systemImage: "bell") {
                    Task { await subs.watch(gpu: gpu, datacenter: dc.datacenter, autoResumeRunID: nil) }
                }
            } else {
                Button {
                    Task { await subs.watch(gpu: gpu, datacenter: dc.datacenter, autoResumeRunID: runID) }
                } label: {
                    Text("Notify + auto-resume the stuck run")
                    Text("Rents automatically at ≤ " + (dc.usdPerHr.map { "\(Format.usd($0))/h" } ?? "today's price"))
                }
            }
        } label: {
            Image(systemName: armed ? "bolt.fill" : "bolt")
                .foregroundStyle(armed ? Theme.accent : Theme.secondary)
                .frame(minWidth: 44, minHeight: 44)
        }
        .accessibilityLabel(armed ? "Auto-resume armed" : "Arm auto-resume")
        .accessibilityIdentifier("gpu.bolt.\(dc.datacenter)")
    }
}
```

- [ ] **Step 2: Replace the `WatchDrawer.swift` stub** with a two-level drawer modelled on
  `BasketDrawer`:

```swift
import SwiftUI
import MotionKit

/// The Pod stage's drawer (2026-09-27 spec §2), built like New Job's basket.
/// Collapsed it is one bar: "Watching · N", with ⚡ when auto-resume is
/// armed. Open, it lists the subs (trash to remove) and the recent firings.
/// Not a system sheet: the GPU sheet and Balance sheet must still open over
/// the stage.
@MainActor
struct WatchDrawer: View {
    enum Level { case collapsed, open }

    let subs: GpuSubsStore
    @Binding var level: Level
    @GestureState private var drag: CGFloat = 0

    static let collapsedHeight: CGFloat = 48
    private var expanded: Bool { level == .open }

    var body: some View {
        VStack(spacing: 0) {
            handle
            if expanded { list.transition(.move(edge: .bottom).combined(with: .opacity)) }
        }
        .background(.regularMaterial, in: .rect(cornerRadius: 20))
        .clipShape(.rect(cornerRadius: 20))
        .offset(y: expanded ? max(drag, 0) : min(max(drag, -40), 0) * 0.3)
        .animation(.snappy, value: level)
    }

    private var handle: some View {
        Button { withAnimation(.snappy) { level = expanded ? .collapsed : .open } } label: {
            HStack(spacing: 10) {
                Image(systemName: expanded ? "chevron.down" : "chevron.up")
                    .font(.footnote.weight(.semibold)).foregroundStyle(Theme.secondary)
                Image(systemName: "bell.fill").foregroundStyle(subs.subs.isEmpty ? Theme.secondary : Theme.accent)
                Text("Watching · \(subs.subs.count)").font(.subheadline.weight(.semibold))
                if subs.armed != nil {
                    Image(systemName: "bolt.fill").foregroundStyle(Theme.accent)
                        .accessibilityLabel("auto-resume armed")
                }
                Spacer(minLength: 0)
                if !subs.unseen.isEmpty {
                    Text("\(subs.unseen.count) new").font(.caption.weight(.semibold))
                        .padding(.horizontal, 8).padding(.vertical, 3)
                        .background(Theme.accent.opacity(0.15), in: .capsule)
                        .foregroundStyle(Theme.accent)
                }
            }
            .padding(.horizontal, 16)
            .frame(height: Self.collapsedHeight)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .simultaneousGesture(
            DragGesture(minimumDistance: 12)
                .updating($drag) { value, state, _ in state = value.translation.height }
                .onEnded { value in
                    withAnimation(.snappy) {
                        if value.translation.height < -40 { level = .open }
                        if value.translation.height > 40 { level = .collapsed }
                    }
                })
        .accessibilityHint(expanded ? "Collapse the watch list" : "Show the watch list")
        .accessibilityIdentifier("pod.watch")
    }

    private var list: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 0) {
                if subs.subs.isEmpty {
                    Text("Nothing watched. Open a GPU and tap 🔔 on a datacenter.")
                        .font(.subheadline).foregroundStyle(Theme.secondary)
                        .padding(16)
                }
                ForEach(subs.subs) { sub in
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("\(sub.name) @ \(sub.datacenter)").font(.body)
                            if let armed = sub.autoResume {
                                Label("Auto-resumes \(armed.runId) at ≤ \(Format.usd(armed.maxUsdPerHr))/h",
                                      systemImage: "bolt.fill")
                                    .font(.footnote).foregroundStyle(Theme.accent)
                            }
                        }
                        Spacer()
                        Button(role: .destructive) { Task { await subs.unwatch(sub) } } label: {
                            Image(systemName: "trash").frame(minWidth: 44, minHeight: 44)
                        }
                        .buttonStyle(.borderless)
                        .accessibilityLabel("Stop watching \(sub.name) at \(sub.datacenter)")
                    }
                    .padding(.horizontal, 16).padding(.vertical, 4)
                    Divider().padding(.leading, 16)
                }
                if !subs.fired.isEmpty {
                    Text("Recent").font(.footnote.weight(.semibold)).foregroundStyle(Theme.secondary)
                        .padding(.horizontal, 16).padding(.top, 12).padding(.bottom, 4)
                    ForEach(subs.fired) { firing in
                        FiringRow(firing: firing, new: subs.unseen.contains(firing))
                            .padding(.horizontal, 16).padding(.vertical, 6)
                    }
                }
            }
        }
        .onAppear { subs.markSeen() }
    }
}

/// One past firing: what came into stock, and what auto-resume did about it.
struct FiringRow: View {
    let firing: GpuSubFiring
    let new: Bool

    var body: some View {
        TimelineView(.periodic(from: .now, by: 30)) { ctx in
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    if new { Circle().fill(Theme.accent).frame(width: 6, height: 6) }
                    Text("\(firing.name) @ \(firing.datacenter) · \(firing.stock)").font(.subheadline)
                    Spacer()
                    Text(Format.ago(ctx.date.timeIntervalSince1970 - firing.firedAt))
                        .font(.footnote).foregroundStyle(Theme.secondary)
                }
                if firing.resumed {
                    Label("Auto-resumed — a pod was rented", systemImage: "bolt.fill")
                        .font(.footnote).foregroundStyle(Theme.accent)
                } else if firing.refused, let reason = firing.reason {
                    Label("Auto-resume skipped: \(reason)", systemImage: "exclamationmark.triangle")
                        .font(.footnote).foregroundStyle(Theme.warning)
                }
            }
        }
    }
}
```

(Check `Format.ago`'s parameter type in `ios/MotionKit/Sources/MotionKit/Formatting.swift`;
`StaleTag` passes it a `TimeInterval`.)

- [ ] **Step 3: Wire the sheet, the drawer and the banner into `PodView`.** Add state:

```swift
    @State private var watchLevel = WatchDrawer.Level.collapsed
    @State private var bannerHidden: String?
```

Wrap the stage's `GeometryReader` content in overlays. Replace the `.frame(width:height:alignment:)`
line with the same line followed by:

```swift
            .overlay(alignment: .bottom) {
                ZStack(alignment: .bottom) {
                    if watchLevel == .open {
                        Color.black.opacity(0.35)
                            .contentShape(.rect)
                            .onTapGesture { withAnimation(.snappy) { watchLevel = .collapsed } }
                            .transition(.opacity)
                            .accessibilityLabel("Close the watch list")
                            .accessibilityAddTraits(.isButton)
                            .accessibilityIdentifier("pod.watchScrim")
                    }
                    WatchDrawer(subs: subs, level: $watchLevel)
                        .frame(maxHeight: watchLevel == .open ? proxy.size.height * 0.7 : nil, alignment: .bottom)
                        .padding(.horizontal, 12)
                }
            }
            .overlay(alignment: .top) { firedBanner.padding(.horizontal, 12) }
```

Add the sheet modifier after `.sheet(isPresented: $showBalance)`:

```swift
        .sheet(item: $openGpu) { target in
            if let stock = gpu.stock {
                GpuSheet(gpu: target.gpu, stock: stock, subs: subs, gpuStore: gpu, pod: pod,
                         spending: flow.isSpending,
                         onMigrate: { model.migrateSheet = MigrateRequest(destination: $0) })
            }
        }
```

When the drawer is closed, the banner shows the newest unseen firing. Opening the drawer marks it
seen:

```swift
    /// The newest firing the app has not shown yet. Telegram already buzzed
    /// the phone; this is the same news for whoever opens the app first.
    @ViewBuilder private var firedBanner: some View {
        if watchLevel == .collapsed, let firing = subs.unseen.first, firing.id != bannerHidden {
            let text = firing.resumed
                ? "⚡ \(firing.name) @ \(firing.datacenter) came into stock — auto-resumed, a pod was rented."
                : firing.refused
                    ? "🔔 \(firing.name) @ \(firing.datacenter) came into stock. Auto-resume skipped: \(firing.reason ?? "refused")."
                    : "🔔 \(firing.name) @ \(firing.datacenter) came into stock (\(firing.stock))."
            MessageCard(text: text) { bannerHidden = firing.id; subs.markSeen() }
                .heroSurface()
                .modifier(SwipeUpToDismiss { bannerHidden = firing.id; subs.markSeen() })
                .task(id: firing.id) {
                    try? await Task.sleep(for: .seconds(8))
                    bannerHidden = firing.id
                }
                .transition(.move(edge: .top).combined(with: .opacity))
                .accessibilityIdentifier("pod.firedBanner")
        }
    }
```

A banner that times out hides without calling `markSeen()`, so the drawer still counts that firing
as "new". Only opening the drawer or dismissing the banner marks it seen.

- [ ] **Step 4: Build and look at it.**

Run: `make ios-build`. Then on the simulator:
- open Pod and tap the 5090 tile; the sheet shows datacenters with bells;
- open the drawer, then close it by tapping the scrim.

Screenshot the sheet, the open drawer, and the stage with the drawer collapsed into
`out/pod-stage/`. Do **not** tap a bell on the live server in this step: a real sub would fire a real
Telegram message. That is saved for Task 9's live check.

- [ ] **Step 5: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionApp
git commit -m "iOS Pod: GPU sheet with bells and auto-resume, Watching drawer, fired banner"
```

---

### Task 8: "Resume when in stock" on the rental-failed card

**Files:**
- Modify: `ios/MotionApp/Runs/RunDetailView.swift` (`RetryRentalCard`, ~L450)

**Interfaces:**
- Consumes: `AppModel.gpuSubs` (Task 6), `GpuSubsStore.watch/armed` (Task 5), and
  `FailedRental.{gpu, datacenter, stockOut}`.
- Produces: identifier `run.resumeWhenInStock`.

- [ ] **Step 1: Implement.** In `RetryRentalCard`, add `@Environment(AppModel.self) private var model`,
  then insert this before the `if flow.needsRecheck` block:

```swift
            // Only for a stock-out at a known datacenter: the server arms
            // auto-resume only at home, and a stock-out's datacenter IS home
            // (pod-provision.sh can only try there).
            if failure.stockOut, let dc = failure.datacenter, let subs = model.gpuSubs, let runID = flow.runID {
                let armed = subs.armed?.gpu == failure.gpu && subs.armed?.datacenter == dc
                Button(armed ? "Auto-resume armed · \(failure.gpu) @ \(dc)" : "Resume when in stock",
                       systemImage: armed ? "bolt.fill" : "bolt") {
                    Task { await subs.watch(gpu: failure.gpu, datacenter: dc, autoResumeRunID: runID) }
                }
                .buttonStyle(SecondaryButtonStyle())
                .disabled(armed || subs.inFlight.contains("\(failure.gpu)|\(dc)"))
                .accessibilityIdentifier("run.resumeWhenInStock")
                if let message = subs.message {
                    Text(message).font(.footnote).foregroundStyle(Theme.warning)
                }
            }
```

Also add `.task { await model.gpuSubs?.load() }` on the card's outer `VStack`, so `armed` reflects
the server.

`failure.gpu` is the catalog id (the server writes `ProvisionFailure.gpu` from `.env`'s `GPU`).
Confirm this in `scripts/batchlib_ext/provision_failure.py`. If it holds a display name instead, map
it with `gpu.stock?.gpus.first { $0.name == failure.gpu }?.gpu` before calling `watch`.

- [ ] **Step 2: Build.** Run: `make ios-build`. Expected: success.

- [ ] **Step 3: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionApp/Runs/RunDetailView.swift
git commit -m "iOS run detail: arm auto-resume from the rental-failed card"
```

---

### Task 9: UI tests, the contract, docs, and the live check

**Files:**
- Modify: `ios/MotionAppUITests/Phase5SmokeTests.swift`
- Create: `ios/MotionAppUITests/PodStageTests.swift`
- Modify: `ios/MotionKit/Sources/motion-contract/main.swift`
- Modify: `docs/superpowers/swiftui-app-progress.md`, and the spec (`## Gate record` section at the end)

**Interfaces:**
- Consumes: every identifier from Tasks 6–8.

- [ ] **Step 1: Update `Phase5SmokeTests`.** The Vast button now lives in the ⋯ menu and the rows
  are now tiles. Replace the first assertions with:

```swift
        XCTAssertTrue(app.descendants(matching: .any)["pod.hero"].waitForExistence(timeout: 60), "hero renders")
        let tiles = app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH %@", "gpu.tile."))
        XCTAssertTrue(Phase4Draft.waitUntil(timeout: 90) { tiles.count == 5 }, "five GPU tiles")
```

  Replace `Phase4Draft.revealButton("pod.moveVolume", in: app).tap()` with:

```swift
        app.buttons["pod.more"].tap()
        app.buttons["pod.moveVolume"].tap()
```

- [ ] **Step 2: Create `PodStageTests.swift`:**

```swift
import XCTest

/// Zero-spend, live server: the Pod tab fits on one screen (2026-09-27 spec
/// §2). Every tile sits inside the window and above the Watching drawer with
/// no scrolling; the GPU sheet opens and lists datacenters. Never taps a
/// bell (a real sub would post a real Telegram message), Use, Kill or Migrate.
final class PodStageTests: XCTestCase {
    @MainActor
    func testStageFitsAndSheetOpens() throws {
        let app = XCUIApplication()
        app.launchArguments.append("-UITestRecordingSpendGate")
        app.launch()
        app.tabBars.buttons["Pod"].tap()

        let tiles = app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH %@", "gpu.tile."))
        XCTAssertTrue(Phase4Draft.waitUntil(timeout: 90) { tiles.count == 5 }, "five GPU tiles")
        let drawer = app.buttons["pod.watch"]
        XCTAssertTrue(drawer.waitForExistence(timeout: 10))
        let window = app.windows.firstMatch.frame
        for i in 0..<tiles.count {
            let tile = tiles.element(boundBy: i)
            XCTAssertTrue(tile.isHittable, "\(tile.identifier) is not hittable")
            XCTAssertGreaterThanOrEqual(tile.frame.minY, window.minY)
            XCTAssertLessThanOrEqual(tile.frame.maxY, drawer.frame.minY + 1, "\(tile.identifier) runs under the drawer")
        }
        attach(app, "pod-stage")

        tiles.element(boundBy: 0).tap()
        let rows = app.descendants(matching: .any).matching(NSPredicate(format: "identifier BEGINSWITH %@", "gpu.dc."))
        XCTAssertTrue(Phase4Draft.waitUntil(timeout: 60) { rows.count > 0 }, "the sheet lists datacenters")
        attach(app, "gpu-sheet")
        app.buttons["Done"].tap()

        drawer.tap()
        XCTAssertTrue(app.descendants(matching: .any)["pod.watchScrim"].waitForExistence(timeout: 5))
        attach(app, "watch-open")
        app.descendants(matching: .any)["pod.watchScrim"].tap()

        XCTAssertEqual(app.descendants(matching: .any)["uitest.recordedSpends"].label, "0",
                       "the recording gate saw no spend")
    }

    @MainActor
    private func attach(_ app: XCUIApplication, _ name: String) {
        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = name
        shot.lifetime = .keepAlways
        add(shot)
    }
}
```

(If `NewJobStageTests` already defines `attach` as a shared helper, reuse it rather than redefining
it.)

- [ ] **Step 3: Add the two contract checks** to `motion-contract/main.swift`, next to the existing
  `GET /v1/gpu/stock` check:

```swift
await check("GET /v1/gpu/stock?all=1") {
    let stock = try await client.get(GpuStock.self, query: [URLQueryItem(name: "all", value: "1")],
                                     timeout: 60, "v1", "gpu", "stock")
    guard stock.datacenters != nil else { throw ContractFailure("datacenters missing with all=1") }
}
await check("GET /v1/gpu/subs") {
    _ = try await client.get(GpuSubs.self, "v1", "gpu", "subs")
}
```

(Use whatever error type the file already throws for a failed expectation. Grep `throw` in
`main.swift`, and replace `ContractFailure` with it.)

- [ ] **Step 4: Run all free gates.**

```bash
make batch-test
make ios-test
make ios-build
make ios-ui-test          # needs the live server for Phase5 + PodStageTests; GET-only here until deploy
```

Expected: all green except where the server lacks the new routes. **Before deploy**,
`PodStageTests` passes, because the tile grid needs only `/v1/gpu/stock` (an unknown `all=1` is
ignored by the old server). `subs.load()` gets a 404, which only leaves the drawer empty.
`make ios-contract` will fail on `GET /v1/gpu/subs` until the deploy. Record that in the gate record
as expected, not as a pass.

Run `PodStageTests` on both the iPhone SE (3rd gen) and the iPhone 18 Pro Max simulators (the same
two `NewJobStageTests` uses). Copy the screenshots into `out/pod-stage/`.

- [ ] **Step 5: Docs.**
  - Add a "Pod stage and GPU subscriptions (2026-09-27)" section to
    `docs/superpowers/swiftui-app-progress.md`: what shipped, the gates with their counts, and what is
    not yet proven (auto-resume on a real stock-out; install on the phone).
  - Append a `## Gate record` table to the spec with every gate from Step 4 and its result.

- [ ] **Step 6: Commit, then open the PR (do not merge).**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios docs
git commit -m "iOS Pod stage: UI tests, contract checks, handoff notes"
git push -u origin gpu-stock-subscribe-ios
gh pr create --title "Pod stage + GPU stock subscriptions with auto-resume (iOS + phone API)" --body-file <(printf '%s\n' "Spec: docs/superpowers/specs/2026-09-27-gpu-stock-subscribe-ios-design.md" "Plan: docs/superpowers/plans/2026-09-27-gpu-stock-subscribe-ios.md" "" "Touches scripts/** — merging auto-deploys motion-bot." "" "🤖 Generated with [Claude Code](https://claude.com/claude-code)" "" "https://claude.ai/code/session_01J9MxAWtHKXkMWVKmga8Mpi")
```

`gh` may default to the read-only company account (see the [[two-github-accounts]] memory). If
`gh pr create` returns 403, stop and ask the user to switch accounts.

- [ ] **Step 7: After the user approves the merge**, check the VPS before merging, then verify:

```bash
doctl compute ssh motion-vps --ssh-command "cd ~/motion-clone && ls batch/*.state.json; grep ^GPU_INSTANCE_ID= .env; pgrep -af 'drain.py|batch_run.py' | grep -v pgrep"
```

Merge only when the VPS shows no live drain, Phase A, lease or migration. After the deploy workflow
succeeds:
- `make ios-contract`, expecting all checks to pass, including the two new ones.
- **Live, zero-spend check:**
  1. On the phone or simulator, open a GPU whose home or another datacenter **already has stock**
     and tap 🔔 there.
  2. Within one poll round, a Telegram message "… is now available …" arrives.
  3. The drawer shows it under Recent. On the next foreground, the banner appears once.
  4. Do not arm ⚡ unless a real stock-out is outstanding: arming is refused (`no_failure`) otherwise,
     which is itself worth confirming once.
- Record the results in the spec's gate record, and commit on `main` in a follow-up docs commit (a
  docs-only push does not deploy).
