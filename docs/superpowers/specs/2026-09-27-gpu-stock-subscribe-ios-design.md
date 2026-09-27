# GPU stock subscriptions in the iOS app, with auto-resume — design

Date: 2026-09-27 · Status: approved 2026-09-27

## Why

The Telegram bot has had `/subscribe` since 2026-09-12: pick a GPU and a datacenter, and the bot
posts one message the moment that pair stops being sold out, then forgets it
(`scripts/tgbot/bot.py`, `_tick_gpu_subs`). The iPhone app cannot see or create these subscriptions,
so on the phone a sold-out 5090 is a dead end. The user asked for the same feature in the app, and
for the Pod tab to follow the no-scroll layout New Job already uses (sized tiles, sheets, a drawer).

While scoping it, the user added one more request: when a subscription fires, rent without waiting
for a tap.

## Decisions made in conversation

| Question | Chosen | Rejected, and why |
|---|---|---|
| Where the UI lives | Rebuild the Pod tab as one stage that does not scroll | A bell on each existing `List` row keeps the scroll. A toolbar bell hides the stock the user is deciding on. |
| What auto-rent rents | **Resume a run that is stuck on a stock-out**, through the existing `_do_resume` | "Confirm the current draft when stock appears" would be a third spend path around the confirm gate, and the draft can change between arming and firing. "Rent an idle pod" bills ~$1/h with no job, and no rent-without-a-run path exists. |
| Price ceiling for auto-resume | The GPU's $/h **at the moment the subscription is armed** | A fixed number typed in by the user |
| Phone notification | **Telegram only**, plus an in-app banner when the app opens | Native push needs APNs, which a free Apple account cannot enable. ntfy was designed, then dropped by the user: Telegram's own notification is enough for now. Background App Refresh wakes the app on iOS's schedule (often hours apart), which is too late for stock that is gone again within minutes. |

Kept from the bot: subscriptions are **one-shot** (fire once, then remove). The user asked for this on
2026-09-12: "báo 1 lần rồi gỡ, giống đặt báo thức 1 lần". The app and the bot share **one list**:
the phone API and the bot run in the same process and use the same chat id.

## 1. Server (`scripts/`, deploys to motion-vps)

### Data

`batch/tg-<chat>.gpusubs.json` stays the one store. Each entry gains fields, all optional on read so
existing files load unchanged:

```json
{"id": "a1b2c3", "gpu_id": "NVIDIA GeForce RTX 5090", "datacenter_id": "EU-RO-1",
 "created_at": 1790000000.0,
 "auto_resume": {"run_id": "tg-123", "run_token": "…", "max_usd_per_hr": 0.99}}
```

- `id` is minted on add (short random hex). If an entry was written before `id` existed, it gets
  one the first time it is loaded, and the file is saved back.
- The bot's `/unsubscribe` keeps removing by `(gpu, dc)`. The app removes by `id`.

A new file, `batch/tg-<chat>.gpusubs-fired.json`, keeps the **last 10** firings, newest first:

```json
{"sub_id": "…", "gpu_id": "…", "datacenter_id": "…", "stock": "Low", "usd_per_hr": 0.99,
 "fired_at": 1790000123.0, "action": "notified|resumed|resume_refused", "reason": "…"}
```

`reason` is set only for `resume_refused`: a plain sentence naming the guard that failed.

### Routes (`scripts/httpapi/server.py` → methods on `AppPod`)

| Route | Body / query | Answer |
|---|---|---|
| `GET /v1/gpu/subs` | — | `{subs: [...], fired: [...]}`. There is no `home_dc`: `GET /v1/gpu/stock` already returns `home_datacenter`, and repeating it here would cost a runpodctl call on every read. |
| `POST /v1/gpu/subs` | `{gpu, datacenter, auto_resume: bool, run_id?}` | An upsert keyed on `(gpu, datacenter)`: `201 {sub}` when new, `200 {sub}` when it existed. `auto_resume: true` (with `run_id`) arms it; `false` leaves or makes it notify-only. |
| `DELETE /v1/gpu/subs/{id}` | — | `200 {subs}`. An unknown id also answers 200: the sub may already have fired. |
| `GET /v1/gpu/stock?all=1` | — | The existing body plus `datacenters: [{gpu, datacenter, stock, usd_per_hr}]`, taken from `stock_at_cached(..., include_unavailable=True)` |

No `Idempotency-Key`: the upsert makes a repeated `POST` land on the same entry, and nothing here
spends. `datacenters` is an array, not an object keyed by GPU id, because the app's
`convertFromSnakeCase` decoder also rewrites dictionary keys.

`?all=1` exists because a GPU sold out at **every** datacenter is dropped from runpodctl's default
output (verified live 2026-09-12). That is exactly the moment the user wants to subscribe.
`_offer_gpu_sub_datacenters` already asks for `include_unavailable` for this reason.

`gpu` must be a `_GPU_CATALOG` id (`400 bad_request` otherwise), and `datacenter` must be a
non-empty string. The datacenter is not checked against the live stock list: the bot does not check
it either, and a runpodctl outage should not block subscribing.

### Arming auto-resume

When `POST` carries `auto_resume: true`, the two network reads (`volume_datacenter`,
`stock_at_cached`) run first, **outside** `BOT_LOCK`, for the reason `migrate_ask` gives: a runpodctl
round trip under the lock stalls every Telegram update. The file checks then run under the lock.
Each failure is a 409 with its own code, and nothing is written:

1. `run_id` is this chat's current run → else `stale_run`. The phone does not send a `run_token`:
   the Pod tab never reads `GET /v1/runs/{id}/tryon`, where that token lives. Instead the server
   stores `_run_token(chat_id)` at arming time, and firing refuses if the manifest has been rewritten
   since.
2. The run has an outstanding `provision-failed.json` with `stock_out: true` → else `no_failure`.
3. `datacenter` is the volume's home datacenter (`volume_datacenter(POD_VOLUME_ID)`) → else
   `not_home_dc`. Auto-resume never migrates: a migration copies ~33GB, takes ~15–25 min, and
   deletes the original volume.
4. The GPU's price is known: taken from the stock read the phone showed, re-read through
   `stock_at_cached` → else `no_price`. It is stored as `max_usd_per_hr`.

Only one armed auto-resume exists at a time. Arming a second one moves `auto_resume` onto the new
sub; the old sub stays as notify-only.

### Firing (`_tick_gpu_subs`)

Unchanged: stock comes from `stock_at_cached` (the 60s cache `/gpu` already pays for), a firing
sub is removed in the same tick, and Telegram gets the message it gets today. New in that tick:

- Every firing is appended to the fired file (the file is trimmed to 10).
- A sub with `auto_resume` re-checks, at fire time, every arming guard above, plus:
  - `busy(manifest)` is false, `migration_running()` is false, and `read_lease(LEASE_PATH)` is None.
  - The price at fire time is ≤ `max_usd_per_hr`.

  If all hold, it does what `_CB_RECOVER_SWITCH` does: `env_set(.env, "GPU", gpu_id)`, then
  `_do_resume(tg, chat_id, manifest, dry_run=…, gpu_provider="runpod")`. The Telegram message then
  says the clock is running: "⚡ Auto-resumed — renting RTX 5090 @ EU-RO-1 · $0.99/h". Action
  `resumed`.
- If any guard fails, the sub fires as notify-only. The Telegram message names the reason, and the
  action is `resume_refused`. Nothing is rented.

`_tick_gpu_subs` already runs inside `_run_ticks` under `BOT_LOCK`, which `_do_resume` expects.

**No new spend path.** `_do_resume` is still the only way in, and it only runs for a manifest that
has already been confirmed once. Auto-resume only removes the tap on "Thử lại". It never runs
Phase A, never migrates, and never spends above the price the user saw when arming.

**One-shot also applies to auto-resume.** If the rental fails again, because the stock vanished in
the cache window, `drain.py` writes a new `provision-failed.json` and the usual stock-out card
arrives. The user re-arms by hand. Re-arming automatically could loop rentals, so this is left out
on purpose.

### Telegram side

`/subscribe` and `/unsubscribe` are unchanged, apart from listing the ⚡ tag on a sub the app armed.
The stock-out card does not get an auto-resume button in this change.

## 2. iOS: the Pod tab as one stage

```
┌ Pod ───────────────── ⋯ ┐   ⋯ menu: Refresh stock · Move volume… · Check Vast credit
│ ╭ HERO ───────────────╮ │   No pod + RunPod balance/runway, or the live lease clock + Kill.
│ │ ☾ No pod · $12.40   │ │   While a migration runs, the hero is the migration card.
│ ╰─────────────────────╯ │   Tapping the balance opens a Balance sheet (Vast credit, errors).
│ [5090 ✓ 🔴 🔔][4090 🟢] │   Five GPU tiles in a grid sized to the space left (like
│ [PRO45 🟡][A6000 🟠]…   │   SlotCardGrid): name, home stock dot, $/h, ✓ selected, 🔔 watched.
├─────────────────────────┤
│ ━ 🔔 Watching 2   ⚡1   │   Drawer (collapsed = one bar). Open: subs (swipe to delete,
└─────────────────────────┘   ⚡ = auto-resume armed), then "Recent" firings.
```

- **Stage.** The page does not scroll on an iPhone SE (3rd gen) or an iPhone 18 Pro Max. The old
  sections move as follows:
  - Balance becomes a line in the hero plus a sheet.
  - "Move volume…" and "Check Vast credit" move into `⋯`.
  - The stale and refresh-failed states become the same banner New Job uses.
  - `KillButton` and `KillNotice` keep their server-driven visibility and live in the hero.
- **GPU sheet.** Tapping a tile opens a sheet at the medium detent, laid out top to bottom:
  - A "Use for next rental" button (the old row tap; disabled while a spend is in flight, as today).
  - One row per datacenter from `?all=1`: stock dot, $/h, and 📍 on the home datacenter.
  - On each row, a 🔔 toggle that subscribes or unsubscribes.
  - On rows other than home, a Migrate… action and the caveat that renting there needs the volume
    synced first.
- **Auto-resume.** The home datacenter row offers "Notify + auto-resume <run>" only when
  `PodStatus.failedRental?.stockOut == true`. Choosing it shows the price ceiling ("rents
  automatically at ≤ $0.99/h"). The run flow's stock-out card gains the same action as a shortcut:
  "Resume when in stock".
- **In-app banner.** On launch, and on each return to the foreground, `GpuSubsStore` reads `fired`.
  Entries newer than the last-seen timestamp in `UserDefaults` produce one banner (the New Job
  banner surface), and the drawer marks them as new.
- **MotionKit.** `GpuSubs` models and `GpuSubsStore` (load, add, remove, lastSeen), decoded with
  optional fields as the other stores are. `GpuStock` gains the optional `datacenters`.

## 3. Verification

All of these are free (no GPU spend):

- **Python unittest** (`scripts/tests/`):
  - the routes, including idempotent replay;
  - loading a legacy file;
  - one-shot firing and the trim to 10;
  - each arming refusal and each fire-time refusal;
  - a resume path that goes through `_do_resume`, with `dry_run`;
  - a `max_usd_per_hr` breach.
- **iOS**:
  - `make ios-test` for the store and models;
  - `make ios-build`;
  - `make ios-contract` (GET `/v1/gpu/subs`, GET `/v1/gpu/stock?all=1`);
  - a UI test that screenshots the stage on SE and Pro Max and asserts that nothing sits below the
    drawer or needs a scroll.
- **Live, no spend.** After deploy, subscribe from the phone to a datacenter that already has stock
 . The next tick fires it; check that a Telegram
  message arrives and that the phone's drawer shows it under Recent.

Not provable without a real stock-out: an auto-resume on the real pod. There is no cheap way to
cause a stock-out, so this path is covered by `dry_run` tests only until one happens naturally.

## Out of scope

Native push (needs a paid Apple account), ntfy, auto-resume across datacenters, auto-confirming a
draft, renting an idle pod, a Telegram button for auto-resume.

## Gate record

Append-only: add a row when a gate runs, and do not restate its result in prose elsewhere. "Ran by"
separates the implementer's worktree gates from the controller's live ones — a simulator or
fake-server result is not a live one.

| Gate | Result | Date | Ran by |
|---|---|---|---|
| `make batch-test` | exit 0, `OK (skipped=1)`, 2298 tests | 2026-09-27 | implementer (Task 9) |
| `cd ios/MotionKit && swift test` (`make ios-test`) | 354/354 passed, exit 0 | 2026-09-27 | implementer (Task 9) |
| `make ios-build` | exit 0, clean compile | 2026-09-27 | implementer (Task 9) |
| `xcodebuild … -only-testing:MotionAppUITests/PodStageTests -only-testing:MotionAppUITests/Phase5SmokeTests` on the iPhone 18 Pro Max sim (`CEBAFDD5-2848-423F-A3EA-AC414620C491`) | `PodStageTests` **passed**; `Phase5SmokeTests` **skipped** (a pod is genuinely live right now — its "nothing rented" precondition correctly declines the migrate half). Screenshots `pod-stage`, `gpu-sheet-no-datacenters`, `watch-open` in `out/pod-stage/iphone-18-pro-max/` | 2026-09-27 | implementer (Task 9) |
| Same, on the iPhone SE (3rd gen) sim (`710BCEBB-2A9E-4401-A0DE-D59460070556`) | `PodStageTests` **passed**; `Phase5SmokeTests` **skipped**, same reason. Screenshots in `out/pod-stage/iphone-se-3rd-gen/` | 2026-09-27 | implementer (Task 9) |
| `make ios-ui-test` (full suite, iPhone 18 Pro, `43B83B81-13CD-4383-BB43-4D8AEBEA6582`) | **exit 0**. `xcrun xcresulttool get test-results summary`: `totalTestCount: 11`, `passedTests: 10`, `skippedTests: 1` (`Phase5SmokeTests`, same live-pod reason), `failedTests: 0` | 2026-09-27 | implementer (Task 9) |
| `make ios-contract` | **18 checks total: 15 `ok`, 2 `FAIL`, 1 `skip`** (`GET /v1/runs/{id}`, no runs on the server — unrelated, pre-existing). The 2 `FAIL` are exactly the two Task 9 added and both are **expected pre-deploy**: `GET /v1/gpu/stock?all=1` (`datacentersMissing`: the live server doesn't return `datacenters` yet) and `GET /v1/gpu/subs` (`404 not_found`: route doesn't exist on the VPS yet). All 16 pre-existing checks (15 ok + 1 skip) are unchanged from before this task | 2026-09-27 | implementer (Task 9) |
| `make ios-contract` (fix round 1 re-run, after the `gpu.dc.unsupported` change — that change is UI-only, not a route) | same: 15 `ok`, 2 `FAIL` (identical to the row above), 1 `skip` | 2026-09-27 | implementer (Task 9) |
| `motions-studio/setup/scrub-secrets.sh --check` | exit 0 | 2026-09-27 | implementer (Task 9) |
| `make ios-build` (fix round 1 re-run, after the `GpuSheet`/`PodStageTests` changes) | exit 0, clean compile | 2026-09-27 | implementer (Task 9, fix round 1) |
| `cd ios/MotionKit && swift test` (fix round 1 re-run) | 354/354 passed, exit 0 | 2026-09-27 | implementer (Task 9, fix round 1) |
| `xcodebuild … -only-testing:MotionAppUITests/PodStageTests -only-testing:MotionAppUITests/Phase5SmokeTests` on the iPhone 18 Pro Max sim (fix round 1 re-run) | `PodStageTests` **passed**, and its screenshot manifest confirms it took the `gpu-sheet-no-datacenters` branch (`gpu.dc.unsupported` present) — the pre-deploy path is exercised, not skipped past; `Phase5SmokeTests` **skipped**, same live-pod reason as before | 2026-09-27 | implementer (Task 9, fix round 1) |
| Same, on the iPhone SE (3rd gen) sim (fix round 1 re-run) | `PodStageTests` **passed**, same `gpu-sheet-no-datacenters` branch confirmed; `Phase5SmokeTests` **skipped**, same reason | 2026-09-27 | implementer (Task 9, fix round 1) |
| `motions-studio/setup/scrub-secrets.sh --check` (fix round 1 re-run) | exit 0 | 2026-09-27 | implementer (Task 9, fix round 1) |

**A bug found and fixed while wiring these gates**: `PodView.swift`'s outer
`.accessibilityIdentifier("pod.stage")` — never itself asserted on by any test — was overriding the
identifiers of unrelated descendant buttons (`pod.balance`, `gpu.refresh`, `pod.watch` all reported
back as `pod.stage` in the accessibility tree, iOS 27 / Xcode simulator runtime, build `24A434`),
which failed `Phase5SmokeTests`' `pod.hero` wait and `PodStageTests`' `pod.watch` lookup. Removed;
re-ran both classes on both simulators to confirm the fix, then took the passing runs above.

**Fix round 1 (review finding, Important)**: `PodStageTests`' original "sheet lists datacenters"
check discarded `Phase4Draft.waitUntil`'s result and asserted nothing on `rows`, so the test could
never fail even if `GpuSheet` stopped rendering `gpu.dc.*` rows after deploy. Fixed by making the
pre-deploy state an explicit, asserted-on UI state instead of an unchecked guess:
`GpuSheet.swift` now renders a distinct `gpu.dc.unsupported` row ("This server doesn't list
datacenters yet — update the bot.") when `stock.datacenters == nil` (server hasn't shipped `?all=1`),
separate from the pre-existing "runpodctl lists no datacenter…" text for the non-nil-but-empty case.
`PodStageTests` now waits (≤60s) for either `gpu.dc.unsupported` or a non-empty `gpu.dc.*` row set,
takes the `gpu.dc.unsupported` branch as the (still non-failing) pre-deploy path, and otherwise
`XCTAssertGreaterThan(rows.count, 0, …)` — so post-deploy, an empty datacenter list is now a real
failure. Confirmed live: both simulator re-runs' screenshot manifests show the `gpu-sheet-no-datacenters`
attachment fired, i.e. the pre-deploy `gpu.dc.unsupported` path was actually exercised, not assumed.

**Not yet proven** (needs the deploy + a live device, out of Task 9's scope):
- The deploy itself, and the two new `ios-contract` checks passing against the redeployed server.
- The live, zero-spend Telegram check: watch a datacenter that already has stock, confirm a Telegram
  message within one poll round, the drawer showing it under Recent, and the banner firing once on
  the next foreground.
- Auto-resume firing against a real stock-out (no cheap way to cause one; only the Python `dry_run`
  unit tests cover this path today).
- Install on the phone.
