# Motion — iPhone app

SwiftUI client for the control-plane API on `motion-vps`
(`docs/superpowers/specs/2026-09-22-swiftui-app-design.md`). Talks only to the VPS tunnel, never to a pod.

Current shipped phases, verification evidence, known gaps and the next implementation boundary are in
[`docs/superpowers/swiftui-app-progress.md`](../docs/superpowers/swiftui-app-progress.md).

## Layout

- `MotionKit/` — Swift package: models, `APIClient`, Keychain vault, stores. No SwiftUI.
  Tested on the Mac with `make ios-test` (no simulator).
- `MotionApp/` — SwiftUI views only.
- `project.yml` — XcodeGen spec. `MotionApp.xcodeproj` and `MotionApp/Info.plist` are generated and gitignored.

## Build and install

    brew install xcodegen          # once
    make ios-secrets               # .env → ios/Secrets.xcconfig (gitignored)
    make ios-test ios-build        # free gates
    make ios-ui-test               # auto-boot a simulator; free Phase 3 + zero-spend Phase 4 smoke
    make ios-audio-test            # Silent Mode playback check; needs a booted simulator
    make ios-contract              # decode the live API with the app's models (GET only)
    make ios-refusal-smoke         # live, zero-spend: bogus-token confirm/regen/resume must 409 (asks first)
    open ios/MotionApp.xcodeproj   # pick your iPhone, Run

Set `IOS_DEVELOPMENT_TEAM=<your personal team id>` in `.env` so a regenerated project keeps
signing; otherwise pick the team once under Signing & Capabilities.

`make ios-ui-test` drives simulator control (`simctl`/`xcodebuild test`), which is killed when run
inside this agent's command sandbox — run it with the sandbox disabled, or from a plain terminal.

## Free provisioning

No paid Apple account, so the app expires 7 days after install. Re-run from Xcode to reinstall.
The Keychain survives this, so the secrets do not need re-entering. The secrets are seeded only into empty
Keychain entries: an edit made in Settings is never overwritten by a rebuild.

No push notifications: Telegram reports progress and results.

## Materials and uploads

The Material tab lists the global VPS library, including files uploaded through Telegram. Thumbnails
use the same Cloudflare Access and bearer headers as the rest of the app. Only materials whose owner
is `app` can be deleted; the server keeps an in-use material and shows its `409` explanation.

Add accepts one image or video from Photos or Files. Uploads are serial and foreground-only. The app
copies the provider file into Application Support, sends server-sized chunks without reading the whole
file into memory, and checkpoints the active upload. If the network drops or the app is terminated,
open the app again to resume only the missing chunks. A probe warning remains visible until the process
ends; the current material-list API does not return old warnings after a cold launch.

This flow uses only the VPS material/upload routes and never rents a GPU pod.

### Phase 2 phone smoke (pending)

This physical-phone smoke remains deferred and unrun. Run it only with personal test media; do not
commit selected media, screenshots containing personal names, or upload checkpoint contents.

1. Open Material and confirm existing global items and thumbnails load.
2. Add a small image from Photos, then delete its app-owned card.
3. Add a video larger than 32 MiB. After at least one chunk, interrupt the network or terminate the
   app, reopen it, and confirm the upload resumes instead of restarting.
4. Confirm the completed video thumbnail and any amber warning, then verify Runs and Output still load.

## New Job

The central New Job tab is a catalog-driven, server-authoritative single-job composer. It loads
pipelines and roles from the VPS, filters each role's material picker by the required image or video
kind, and replaces its draft after every server mutation. Switching pipelines can remove incompatible
slots; the app shows the server's notice. A complete editor can be added to the basket, and a basket
row is removed by its stable digest rather than its position. Validation is free: Ready and any estimate
come from the server, and the next draft edit clears Ready.

The Phase 3 UI intentionally contains no Phase A, Run, rent, pod, confirm, resume, or other
spend-capable action. Its mutations use only the free draft endpoints; its reads use the free pipeline,
draft, material, and authenticated-thumbnail routes. Later phases introduce spend actions.

### Phase 3 simulator smoke

`make ios-ui-test` automatically selects an available iPhone Simulator, boots it when needed, runs
the free live smoke below, clears the VPS draft even when the test fails, and shuts down a simulator
that it booted. Set `IOS_SIMULATOR_ID=<UDID>` to target a specific simulator. A physical iPhone is
optional coverage, not a prerequisite for this flow.

1. Open New Job and switch between two pipelines; confirm incompatible slots disappear with a notice.
2. Assign image and video materials and verify incompatible kinds are absent from each picker.
3. Clear a required slot; confirm Add is disabled and the role is named missing.
4. Complete a job, add it to the basket, remove it by its digest-backed row, and add it again.
5. Validate; confirm Ready and an estimate appear, then edit one slot and confirm Ready disappears.
6. Confirm no Phase A, Run, rent, or pod action exists in the Phase 3 UI.

## Phase 4 run flow

The run flow (`RunFlow` store, `MotionApp/RunFlow/`) takes a validated try-on draft through Phase A,
per-job try-on preview and regeneration, the rent panel, and confirm/resume — the app's first money
boundary. Every spend call (`phase-a`, `regen`, `confirm`, `resume`) goes through `SpendGate`, the only
sender of an `Idempotency-Key`.

- **Idempotency ledger.** `SpendGate` persists at most one pending entry at
  `Application Support/Motion/Spend/pending-spend.json` before a spend request leaves the phone, and
  clears it only on a definitive answer. A dropped connection or an app kill mid-spend resends the same
  key on the next launch instead of minting a new one.
- **The 20 h rule.** On launch, a ledger entry younger than 20 hours is resent once, with its original
  key, under "Checking the earlier Confirm…". An entry 20 hours or older is never resent — the server
  prunes idempotency records after 24 h, so a late replay would count as a fresh spend — and is
  discarded with a message pointing at Runs and Pod instead.
- **`-UITestRecordingSpendGate`.** This launch argument swaps `SpendGate` for a recording fake in the
  UI-test build, so a stray tap in a UI test cannot reach the VPS. `Phase4SmokeTests` uses it to drive
  the flow up to a priced Confirm (or the sold-out state) without ever tapping a spend button.
- **Settings save is refused while a spend is in flight** — one run slot means one in-flight spend, and
  `SpendGate.perform` refuses a second concurrent call outright.
- **The rent-panel read uses a 120 s timeout** (`GET /v1/runs/{id}/rent-panel`), longer than other reads,
  because the Vast quote it carries can be slow to price.
- **The try-on chooser** (`choice_required`) uses the provider of the confirm that was actually sent (never the
  current picker selection) and shows its price; each choice is a new tap with a new key.
- **Live spend is never part of any gate.** `make ios-test`, `make ios-build`, `make ios-contract`, and
  `make ios-ui-test` are all zero-spend. `make ios-refusal-smoke` is also zero-spend — it sends a bogus
  `panel_token`/`run_token` through the real `SpendGate` against the live API and asserts each call is
  refused (`409 stale_panel` / `409 stale_run`) with no pod lease before or after — but it is a live call
  to the VPS and is opt-in: run it only after explicitly deciding to, never automatically.

## Phase 5 pod and cost

The Pod tab (`MotionApp/Pod/`) reads `GET /v1/pod`, `GET /v1/gpu/stock` and `GET /v1/balance`, and
offers kill, GPU choice and the Network Volume migration. Design:
`docs/superpowers/specs/2026-09-23-swiftui-app-phase-5-design.md`.

- **Kill bypasses `SpendGate`.** `PodStore.kill` mints a fresh `Idempotency-Key` per tap, never
  resends, and is never disabled by a pending spend — a kill must work exactly when a confirm is
  unanswered. An ambiguous answer is followed by three `GET /v1/pod` probes; a `202` is followed every
  2 s until `kill_running` is false, capped at 5 minutes (then **Check again**). The result is the
  server's `last_kill`, compared by its own `at`.
- **"Pod may still be billing" banner.** The server clears the lease even when `make gpu-destroy`
  could not verify the pod is gone, so "no lease" proves nothing. After a `last_kill` with
  `destroy_unverified` or `error`, a red banner stays on every tab until the user taps
  **I checked — the pod is gone** (stored in `UserDefaults` by that kill's `at`) or a later kill
  succeeds. Check the RunPod console or `runpodctl get pod` before acknowledging.
- **GPU choice** (`PUT /v1/pod/gpu`) is free and immediate, refused while a spend is in flight. From
  the rent panel's **Change GPU** it clears the old quote and re-reads the panel, so Confirm needs a
  new tap at the new price.
- **Balances.** The Vast credit is read only on **Check Vast credit** (a ~30 s subprocess on the VPS).
  An unreadable balance never shows as `$0`; a failed stock read keeps the last list, dimmed.
- **Migration** is ask → a sheet with the server's warning verbatim → type the destination
  datacenter id exactly → **Migrate and delete the old volume**, before the 10-minute token (minus a
  15 s margin) expires. The migrate call goes through `SpendGate` as `SpendIntent.migrate`, so an
  interrupted tap is resent with its original key; an unanswered migrate shows on every tab with
  **Check again**.
- **UI smoke.** `Phase5SmokeTests` (recording gate) reads the Pod tab and opens the migrate sheet to
  the locked confirm button; it asserts the gate recorded zero spends. It never taps a GPU row (that
  rewrites the live `.env`), Kill, or Migrate. The migrate half is skipped while any pod is leased.
- **Refusal smoke** now also sends a kill with nothing running (`409 nothing_running`) — only after a
  fresh read shows no lease, no running kill, no Phase A and no live run — and a migrate with a bogus
  `confirm_token` (`409 bad_confirm_token`).
