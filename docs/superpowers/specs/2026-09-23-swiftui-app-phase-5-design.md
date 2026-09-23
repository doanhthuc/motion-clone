# Motion iPhone app — Phase 5 pod and cost design

Date: 2026-09-23 · Status: implemented; `make ios-ui-test` (Phase 5 in full) and `make ios-refusal-smoke` (idle kill and bogus migrate refused) passed live 2026-09-23, zero spend; a real kill, GPU change and migration not yet exercised

This spec refines Phase 5 of `docs/superpowers/specs/2026-09-22-swiftui-app-design.md` (§3 "Pod &
cost", §4 kill and migrate). Phases 1–4 are shipped (`docs/superpowers/swiftui-app-progress.md`).
Phase 5 gives the phone the stop button Phase 4 lacked — the app can rent a pod since Phase 4 but can
only stop it from Telegram — plus GPU choice, balances and the volume migration. It consumes the live
control-plane API (`2026-09-21-vps-control-plane-api-design.md` §5.4, §5.9) and adds **no** backend
route or field: nothing under `scripts/**` changes, so nothing auto-deploys to the VPS.

## 1. Scope and success criteria

Phase 5 is complete when the installed app can:

- show a **Pod** tab: the lease (provider, GPU, elapsed, a quoted cost), the migration in progress,
  the RunPod balance and runway, the Vast credit on request, the five-GPU stock list, and a way into
  the migration;
- **kill** what is running — from the Pod tab and from run detail — and follow the kill to its
  result, never blocked by an outstanding spend;
- keep a red **"pod may still be billing"** banner on every tab after a kill that could not verify
  the destroy, until the user acknowledges it or a later kill succeeds;
- **change the RunPod GPU** from the Pod tab and from the Phase 4 rent panel, which then re-reads the
  panel and requires a new Confirm tap;
- **migrate** the Network Volume through ask → typed-confirmation sheet → migrate, with the migrate
  call under the Phase 4 `SpendGate`; and
- pass every free gate plus a live zero-spend refusal smoke, without renting a pod, changing the live
  GPU or starting a migration.

Out of Phase 5: cross builds, bulk try-on, batch progress, saved try-ons, library seeding (Phase 6);
cancelling a migration, switching provider on a resume, stock-watch notifications (the API has none —
API design §5.9 "Not in slice 5"); any server-side check of whether a pod really exists (see §2).

## 2. What the live server actually does

Read from `scripts/tgbot/bot.py` (`AppPod`, `_do_kill`, `_gpu_stock_data`, `_balance_data`,
`_migrate_warning`) and `scripts/httpapi/server.py` on 2026-09-23. Facts the parent design does not
spell out, and that this design depends on:

- **`GET /v1/pod`** → `{run_id, gpu, lease, migration, kill_running, last_kill, failed_rental}`.
  Files and memory only, takes no `BOT_LOCK` — deliberately, so it answers while a kill holds the lock.
  `migration` = `{running, phase, to_dc, started_at, bytes_copied, total_bytes}`, every field but
  `running` nullable. `last_kill` = `{at, ok, code, message}` and is **persisted to disk**
  (`_save_kill_result`), so it survives a bot restart; `kill_running` means "a kill thread is alive in
  this process" and reads `false` right after a restart.
- **`POST /v1/runs/{id}/kill`** requires an `Idempotency-Key` (server kind `kill`). Answers:
  `202 {run_id, outcome: "kill_started"}`; `409 kill_in_progress`; `409 nothing_running` (neither a
  drain nor Phase A is live); `503 bot_busy` (key forgotten); `404` for a run id that is not the slot.
  The worker re-checks "something is live" under `BOT_LOCK` before `_do_kill`, and records
  `nothing_running` into `last_kill` if the run ended in between. Possible `last_kill.code`: `killed`
  (ok), `phase_a_stopped` (ok), `phase_a_finished`, `nothing_running`, `destroy_unverified`, `error`
  (the worker raised — possibly mid-destroy).
- **`_do_kill` clears the lease whether or not the destroy verified** (`bot.py` `clear_lease` after
  `make gpu-destroy`). The parent design's rule "the red banner clears on a later `GET /v1/pod` with no
  lease" would therefore clear it on the very first poll. §4 replaces that rule. No route can tell the
  phone whether a pod still exists on RunPod.
- **`GET /v1/gpu/stock[?force=1]`** → `{selected, home_datacenter, gpus: [{gpu, name, usd_per_hr,
  home: {stock} | null, sold_out_everywhere}], other_regions: [{gpu, name, datacenter, stock,
  usd_per_hr}]}`. Five GPUs in catalog order. A dead `runpodctl` is `502 upstream_unavailable` — never
  an empty list. Uncached it is a `runpodctl` round trip, ~30 s worst case.
- **`GET /v1/balance[?vast=1]`** → `{runpod: {usd, usd_per_hr, runway_hours, low_runway} | null,
  vast?: {usd | null}, errors: [String]}`. Fails soft (always `200`). `vast` is present only when
  asked; `vast_credit()` is its own ~30 s subprocess. `low_runway` is under 1 h.
- **`PUT /v1/pod/gpu {gpu}`** → `200 {gpu, name}`. No `Idempotency-Key`, no run or drain guard (a
  rental in flight read its GPU when it started). `400 bad_request` for an id outside the catalog;
  `503 bot_busy`. Confirm and resume already refuse a stale `gpu` with `409 stale_panel` (Phase 4).
- **`POST /v1/pod/migrate/ask {to_dc}`** → `200 {to_dc, home_datacenter, confirm_token,
  expires_in_sec: 600, warning}`. No key: it acts on nothing and a repeat ask replaces the token. The
  token is single use, bound to `to_dc` and the volume id, held in memory (a bot restart voids it).
  Refusals: `409 home_unknown | same_datacenter | unknown_datacenter | migration | run_active`,
  `502 upstream_unavailable` (the stock check fails closed), `503 bot_busy`.
- **`POST /v1/pod/migrate {to_dc, confirm_token}`** requires an `Idempotency-Key` (server kind
  `migrate`). Re-checks every guard under `BOT_LOCK`, pops the token, then `_start_migration` →
  `202 {outcome, to_dc}`. A wrong, expired, reused or volume-mismatched token is one code,
  `409 bad_confirm_token`. **Deletes the source Network Volume once the copy verifies.** Progress is
  `GET /v1/pod`'s `migration`; Telegram posts it too.

## 3. Architecture

Approach chosen: small stores split by responsibility, matching Phase 4's `RunFlow`.

| Unit | Owns | Used by |
|---|---|---|
| `PodStore` (extended) | `GET /v1/pod`, kill + its poll, the unverified-kill banner, the migration poll | Pod tab, run detail, `RootView` banner |
| `GpuStore` (new) | `GET /v1/gpu/stock`, `PUT /v1/pod/gpu` | Pod tab, rent panel's GPU sheet, `MigrateFlow` destinations |
| `BalanceStore` (new) | `GET /v1/balance[?vast=1]` | Pod tab |
| `MigrateFlow` (new) | ask, the token and its expiry, the typed confirmation, migrate via `SpendGate` | Pod tab's migrate sheet |

All four live in `MotionKit` (`Stores/`), are `@Observable @MainActor`, take `APIClient` (and, for
`MigrateFlow`, the shared `any SpendSending`), and are rebuilt by `AppModel.reconnect()`. Views never
call `APIClient`. Clocks and sleeps are injected so tests run instantly.

Model additions (`Models/Pod.swift`): `PodMigration`, `PodStatus.migration`, `GpuStock`
(`GpuStockRow`, `GpuHomeStock`, `GpuRegion`), `Balance` (`RunpodBalance`, `VastBalance`). All decode
with the client's snake_case strategy.

## 4. Kill and the unverified-kill banner

**Kill does not use `SpendGate` or the ledger.** The server makes a duplicate kill harmless
(`kill_in_progress`, `nothing_running`, and the live re-check under the lock), so a fresh key per tap
is safe, and a kill must never be blocked by an outstanding confirm — that is exactly when a pod may
be billing. `PodStore.kill(runID:)`:

1. Snapshot `lastKill?.at` (the server's own clock, so no phone/server skew).
2. `APIClient.spendPost(["v1","runs",runID,"kill"], body: Data("{}".utf8), idempotencyKey: UUID().uuidString, timeout: 90)` —
   90 s because the server can wait 60 s for `BOT_LOCK`.
3. Answer handling:

| Answer | Phone does |
|---|---|
| `202` | `killState = .killing`; poll `GET /v1/pod` every 2 s |
| `409 kill_in_progress` | Same as `202` |
| `409 nothing_running` | Show the server's message; refresh |
| `503 bot_busy` | "The bot is busy — tap Kill again"; button re-enabled (a new key is safe) |
| Transport failure, timeout, 5xx | Never resend. Poll 3× at 2 s: `kill_running` → `.killing`; `lastKill.at` changed → result; neither → "Couldn't tell whether the kill started — killing again is safe" |
| Other 4xx | The server's message |

4. The poll ends when `killRunning == false`. If `lastKill.at` differs from the snapshot, show that
   result (`killed`, `phase_a_stopped`, `phase_a_finished`, `nothing_running`, `destroy_unverified`,
   `error`). If it does not — the bot restarted mid-kill — show "The kill ended without a result —
   check Telegram".
5. The poll runs only while the scene is active, capped at 5 minutes (the server's kill is ≤ ~210 s).
   At the cap: "Still running" with a **Check again** button that refreshes once.

**Where Kill appears:** on the Pod tab's lease card and on run detail next to Retry rental, when
`pod.lease != nil` or the run's status is `running`/`phase_a` (the server decides; this only draws the
button). Red, with a confirmation dialog: with a lease, "Destroy the pod now. Jobs in progress are
lost; finished outputs stay."; Phase A only, "Stop the try-on phase. Nothing was rented; Gemini calls
already made are not refunded." Disabled only while a kill is sending or polling — never by
`SpendGate`.

**Banner.** `PodStore.unverifiedKill` is `pod.lastKill` when `ok == false`, `code` is
`destroy_unverified` **or `error`** (a worker that raised mid-destroy may equally have left a pod
billing), and `at != ackedKillAt`. `RootView` shows it on every tab next to `SpendBanner`: "Pod may
still be billing — check RunPod". It clears when:

- the user taps **"I checked — the pod is gone"** and confirms; `ackedKillAt = at` is stored in
  `UserDefaults` (a later failed kill has a new `at`, so the banner returns); or
- a later kill succeeds, overwriting `last_kill` on the server.

`PodStore.refresh()` also runs whenever the scene becomes active, so the banner survives an app kill.

**Cost on the lease card:** provider, GPU, elapsed from `provisioned_at`, and "≈ $X (quote, not the
invoice)" = elapsed × `quoted_usd_per_hr`; hidden when that is `null` (Vast).

## 5. GPU choice and balances

**`GpuStore`**

- `load(force: Bool)` — `GET /v1/gpu/stock`, 60 s timeout, `?force=1` only on pull-to-refresh. A `502`
  keeps the last good stock dimmed with "Couldn't reach runpodctl" and Retry; it never becomes an empty
  list, which would read as "sold out".
- `select(gpu:)` — `PUT /v1/pod/gpu`. On `200`: `selected = gpu`, then `PodStore.refresh()`. `400` or
  `503 bot_busy`: the server's message, tap again. Refused while `RunFlow.isSpending` (a confirm or
  resume request is in flight). With a live lease the row notes "Applies to the next rental".

**`GpuPickerView`** (shared): five rows — name, `$X/h`, home stock (`high`/`medium`/`low`/`none`, or
"unknown" when `home` is null), "Sold out everywhere", ✓ on the selected GPU. Tapping another row
selects it immediately — free, so no confirmation. Each row expands to its other regions (read-only),
each with "Migrate to <dc> →", which opens the migrate sheet with that destination.

**Phase 4 rent panel changes (`RentPanelView`, `RunFlow`)**

- The RunPod row gains **Change GPU** → a sheet with `GpuPickerView`. After a successful select the
  sheet closes and `RunFlow.loadPanel(force: false)` re-reads the panel (the same path `stale_panel` uses): the
  old quote and `panel_token` are cleared, Confirm shows the new price and needs a new tap.
- The sold-out state gains **Change GPU** and **Migrate →** (switches to the Pod tab and opens the
  migrate sheet).

**`BalanceStore`**

- `load()` — `GET /v1/balance` on Pod tab appear and pull-to-refresh, 45 s timeout. The RunPod card:
  "$12.34 · ≈ 12h 28m of RTX 5090 at $0.99/h", amber when `low_runway`. `errors[]` shown verbatim.
  `runpod == null` reads "Couldn't read the balance" — never `$0`.
- `loadVast()` — only on a **Check Vast credit** tap: `GET /v1/balance?vast=1`, 90 s timeout, spinner;
  updates both cards. `vast.usd == null` shows the error, never `$0`.

**Pod tab order:** banners → lease card + Kill (or "No pod running") → migration progress (when
running) → balance → GPU list → **Move volume…**.

## 6. Migrate

Entry points: **Move volume…** on the Pod tab, "Migrate to <dc> →" in the GPU list (preselected), and
**Migrate →** from the rent panel's sold-out state.

**`MigrateFlow`**

1. **Destination.** Datacenters from `GpuStore.otherRegions`, de-duplicated, home excluded, each with
   the GPUs stocked there. The start button is disabled — with the reason — while `migration.running`,
   while `pod.lease != nil`, or while the run is `running`/`phase_a`. The server re-checks all of it.
2. **`ask(toDc:)`** — `POST /v1/pod/migrate/ask`, no key, 90 s timeout (two `runpodctl` reads plus a
   possible lock wait). `409` → the server's message; `502` → Retry; `503` → tap again.
3. **Confirmation sheet.** The server's `warning` verbatim in red, `home → to_dc`, and a countdown to
   `receivedAt + expires_in_sec − 15 s` (margin for latency; injected clock). A text field must equal
   `to_dc` exactly (case-sensitive) before the red **Migrate and delete the old volume** button
   enables. At zero the button disables and **Expired — ask again** re-runs step 2.
4. **`migrate()`** — through `SpendGate` as a new `SpendIntent.migrate(toDc:, confirmToken:)`
   (new `SpendKind.migrate`, path `v1/pod/migrate`, body `{to_dc, confirm_token}`, `runID == nil` — the
   existing `runID: String?` already allows it). One UUID per tap, ledger fsync'd before sending,
   Phase 4's busy/ambiguous/`outcome_unknown` rules unchanged, disabled while the gate cannot spend.

| Result | Phone does |
|---|---|
| `accepted` (`202`) | Close the sheet, `PodStore.refresh()`, show the progress card |
| `refused 409 bad_confirm_token` | Drop the token, back to step 2: "That confirmation expired — ask again" |
| `refused 409 migration / run_active` | The server's message |
| `outcomeUnknown` | No retry: "Couldn't tell whether the migration started — check Pod or Telegram"; refresh the pod |
| `busy`, `unreachable`, `expired`, `notSent` | As Phase 4 |

The `confirm_token` is persisted only inside the ledger entry, because a replay must resend the same
body; it is single use and expires in 10 minutes.

**Replay ownership.** Phase 4's `RunFlow.replayPendingOnce()` owns the one launch replay. Phase 5 moves
the dispatch to `AppModel.replayPendingSpend()`: it reads `gate.pending()` and hands a `.migrate` entry
to `MigrateFlow.replayPendingOnce()`, anything else to `RunFlow` as today. `RunFlow.recheck()` ignores
a pending `.migrate` intent. While a migrate entry is unresolved, a Phase 4 spend gets the gate's
existing `.notSent` ("an earlier request is still pending"). `SpendBanner` labels it "Checking the
earlier Migrate…".

**Progress card** (from `pod.migration`): phase, destination, elapsed from `started_at`, and a
`bytes_copied / total_bytes` bar when both are present. While `migration.running` and the Pod tab is
visible, `PodStore` polls `GET /v1/pod` every 10 s (files only on the server; a migration takes
~25–30 min). No cancel — the API has none.

## 7. App wiring

- `AppTab.pod` — the fifth tab, "Pod", SF Symbol `cpu`, as the parent design's tab list already says.
- `AppModel` holds `GpuStore`, `BalanceStore` and `MigrateFlow` beside the existing `PodStore`, all
  rebuilt by `reconnect()` (which already refuses while a spend is in flight).
- `RootView`: `KillBanner` beside `SpendBanner`; `PodStore.refresh()` on `scenePhase == .active`.
- `RunDetailView`: Kill beside Retry rental.
- `-UITestRecordingSpendGate` covers `MigrateFlow` too (same gate instance).

## 8. Testing and acceptance

**Logic (`make ios-test`)** — swift-testing, `URLProtocol` stubs, injected clock and sleeper:

- Kill: every row of §4's table; poll end on changed vs unchanged `lastKill.at`; the 5-minute cap;
  kill still sends while the gate holds a pending entry.
- Banner: shown for `destroy_unverified` and `error`, not for `killed`; cleared by acknowledgement;
  shown again for a new `at`; cleared by a later successful kill.
- `GpuStore`: `502` keeps the last stock; select refreshes the pod; refused while spending.
- `BalanceStore`: `runpod: null` never renders `$0`; `?vast=1` only from `loadVast()`.
- `MigrateFlow`: each ask refusal; expiry via the clock; exact, case-sensitive match; `202`,
  `bad_confirm_token` and `outcome_unknown` through `FakeSpendGate`; replay routing.
- `SpendIntent.migrate`: path, body, `runID == nil`, ledger round trip.
- `RunFlow`: a GPU change clears the quote, re-reads the panel and requires a new tap; `recheck`
  ignores a pending migrate.
- Decoding: `PodStatus` with and without `migration` fields, `GpuStock`, `Balance` fixtures copied
  from the server shapes in §2.

**Live, zero spend**

- `make ios-contract` adds `GET /v1/gpu/stock` (cached, no `force`) and `GET /v1/balance` (no `vast`),
  and decodes `PodStatus.migration`.
- `make ios-refusal-smoke` (opt-in; ask before every run) adds two refused writes:
  - kill → `409 nothing_running`, sent only after confirming no lease **and**
    `phase_a_running == false` (a live Phase A would really be stopped); skipped otherwise;
  - migrate with a bogus token → `409 bad_confirm_token` (no lease means `_migrate_blocked` passes,
    then the token check refuses).
- `make ios-ui-test` adds a Pod smoke: the no-pod state, the GPU list and the balance render; the
  migrate flow opens to the confirmation sheet (ask acts on nothing; its token lapses in 10 min) and
  the Migrate button stays disabled with the field empty; the recording gate records **zero** spends.
  The test never taps a GPU row (that would rewrite the live `.env`), Kill or Migrate.

**Acceptance:** `ios-test`, `ios-build`, `ios-contract`, `ios-ui-test` and
`scrub-secrets.sh --check` pass; the extended refusal smoke passes live after approval; no file under
`scripts/**` changes.

**Not verified by Phase 5, and recorded as such in the handoff:** killing a real pod (best folded into
the one real Phase A → confirm → Kill-from-the-app test, quoted and approved separately); a real GPU
change; a real migration (~25–30 min, two temporary CPU pods, deletes the source volume — not run
unless the user explicitly asks).
