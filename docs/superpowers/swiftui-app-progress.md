# Motion SwiftUI App — Development Progress and Handoff

Last updated: 2026-09-24

This is the current implementation handoff for the native iPhone app. Read it before planning or
changing another SwiftUI phase. The approved product contract remains
[`specs/2026-09-22-swiftui-app-design.md`](specs/2026-09-22-swiftui-app-design.md); this file records
what actually shipped, what was verified, and the next safe boundary.

## Current baseline

- Repository: `/Users/thucpham/Desktop/motion-clone`
- Branch: `feat/swiftui-phase-6` (unmerged). The last **merged** implementation is `7a8c0e2` (merge of
  PR #66, `feat/swiftui-phase-5`), merged 2026-09-23; Phase 4 was PR #65 (`2945481`). Phase 6 has no
  merge commit or PR number yet — fill those in after the merge.
- `main` is 2 commits ahead of `origin/main` (`4052b97` Phase 6 spec, `5eaa652` Phase 6 plan), both
  unpushed. Push `main` before opening the Phase 6 PR, or the PR will carry those two doc commits.
- PR #66 changed only `ios/**`, `docs/**`, `Makefile`, `CLAUDE.md` and `AGENTS.md` — nothing under
  `scripts/**` — so it did not trigger the deploy-bot workflow. PR #65 did not touch `scripts/**`
  either. The VPS bot still runs the `affcf46` deploy (GitHub Actions run `35813558396`, `motion-bot`
  active, control API on `127.0.0.1:8787`).
- Phase 6 is the first phase to touch `scripts/**`: Task 1 adds a read-only `tryon_seed` field to
  `GET /v1/draft` (`scripts/control/drafts.py` `_view`, lines 331 and 334). It is on the branch, not
  deployed — merging to `main` auto-deploys `motion-bot`, so check the VPS for a drain, Phase A, pod
  lease and migration first. The app decodes the field as optional, so it works against the server
  before and after that deploy.
- Control-plane API slices 1–6 are live. Phases 4 and 5 added no route or field; Phase 6 adds one
  read-only field and no route.
- Phases 1–6 are implemented in code. Phase 6's two live gates (`ios-contract`, `ios-ui-test`) are
  pending the controller run.

## Phase status

| Phase | Status | Delivered | Remaining evidence |
|---|---|---|---|
| 1 — shell and read-only app | Complete | Keychain credentials, Settings health test, Runs, run detail with ETag polling, Outputs, authenticated Range playback, Save to Photos, Silent Mode audio behavior | No current blocker |
| 2 — materials and uploads | Complete in code | Material list and thumbnails, Photos/Files import, chunked foreground upload, persisted checkpoints, resume of missing chunks, delete ownership/error handling | Physical-phone interruption test for a video larger than 32 MiB is still pending |
| 3 — single New Job | Complete | Catalog-driven pipeline/provider picker, compatible material pickers, server-authoritative draft mutations, validation, add/drop basket entries, stale/error reconciliation | Automated live simulator smoke passes; physical phone is optional coverage |
| 4 — run flow | Complete in code | `SpendGate`/idempotency ledger, Phase A + try-on previews, regenerate with the closed guidance vocabulary, Keep, the rent panel (RunPod + Vast, out-of-stock), confirm (with the reuse/rerun chooser) and resume, all through `RunFlow`; `make ios-test` (158 tests), `make ios-build`, `make ios-contract` (adds `/tryon` and `/rent-panel`), `make ios-ui-test` (adds `Phase4SmokeTests`, zero-spend via `-UITestRecordingSpendGate`) and `scrub-secrets.sh --check` all pass | `make ios-refusal-smoke` passed live on 2026-09-23 (three bogus-token spends refused with 409, no lease before or after). No live Phase A / regen / confirm / resume has run — no pod has been rented for Phase 4 |
| 5 — pod and cost | Complete in code | Pod tab: lease card with a quoted cost, kill (fresh key per tap, outside `SpendGate`, followed to `last_kill`), the "pod may still be billing" banner (`destroy_unverified`/`error`, cleared only by acknowledgement or a later successful kill), GPU choice on the Pod tab and the rent panel (re-reads the panel), RunPod balance and on-tap Vast credit, the migration (ask → typed, expiring confirmation → `SpendIntent.migrate` through `SpendGate`), an unanswered migrate on every tab; `make ios-test` (211 tests), `ios-build`, `ios-contract` (adds `gpu/stock`, `balance`), `ios-ui-test` (adds `Phase5SmokeTests`) pass | With no pod leased (2026-09-23): `make ios-ui-test` ran `Phase5SmokeTests` in full and `make ios-refusal-smoke` passed all seven steps. A real Phase A → GPU change → confirm → kill ran once through the app's MotionKit code ($0.012, see §"Real spend test"). No real migration has run |
| 6 — batch/library | Complete in code | Cross build (Batch mode) with resumable re-planning and per-outfit seeds, the seed badge and its "no longer exists" variant, drop after Phase A with the re-validate and stale-panel clearing, `canConfirm`'s `!isDropping` guard, the batch progress list, saved try-ons (browse/use/delete) on the Material tab, and the server's read-only `tryon_seed` field; `make ios-build` exit 0, the UI-test target compiled (`build-for-testing`), `swift test` 244 tests in 23 suites, `scrub-secrets.sh --check` exit 0 | `make ios-contract` (the 14th route, `GET /v1/tryon-library`) and `make ios-ui-test` (`Phase6SmokeTests`) are pending the controller run. No real batch has run |

## What is implemented now

### Foundation and read paths

- `ios/MotionKit` is a Swift 6 package with no SwiftUI dependency.
- `APIClient` owns Cloudflare Access headers, bearer authentication, explicit User-Agent, JSON error
  envelopes, Cloudflare HTML 403 mapping, write requests and authenticated byte ranges.
- `CredentialVault` seeds four values from the generated, gitignored `ios/Secrets.xcconfig` only when
  Keychain values are empty. Rebuilds do not overwrite Settings edits.
- Runs and run detail are read from the VPS journals. Run detail uses weak ETags and polls only while
  its view is active.
- Outputs use an authenticated resource loader for Range playback. The audio session uses playback
  category so videos remain audible in Silent Mode.

### Materials and uploads

- `MaterialsStore` retains the last good list on refresh errors, caches authenticated thumbnails and
  allows deletion only for app-owned materials.
- `Uploader` checkpoints before the first chunk, resumes from the server's received indices and sends
  only missing chunks.
- A `409 incomplete` completion response performs one bounded status refresh and missing-chunk retry.
- Upload recovery is explicit: resume or discard. Only one foreground upload can run at a time.
- `MaterialProbe.warning` deliberately defaults to an empty string when absent. Upload responses can
  include it, while a draft slot's nested base probe legitimately omits it. Do not make it required
  again; `draftProbeDecodesWhenNestedWarningIsOmitted` protects this contract.

### Phase 3 New Job

- `DraftStore` loads the pipeline catalog and draft, serializes writes and installs every authoritative
  draft returned by the server.
- Failed mutations reconcile with a refresh before releasing the mutation gate. Validation preserves
  the server error while installing the returned draft, including stale results.
- `NewJobView` contains no Phase A, Run, rent, confirm, resume or pod action.
- Pipeline roles and material kinds come from the live catalog. Unknown role kinds show an unsupported
  state instead of accepting arbitrary media.
- Basket rows use stable server digests and require destructive confirmation before Drop.

### Phase 6 batch and library

- `BatchComposer` (MotionKit) drives a cross build: the edited job's slots are shared, and each chosen
  outfit becomes one basket job through `PATCH {slots.outfit, tryon_seed}` + `add-to-batch`. `run()`
  re-reads the draft and re-plans, so a resumed build skips outfits already basketed and never adds a
  duplicate (`422 duplicate` counts as done); every step names its seed explicitly, so no outfit
  inherits the previous one's. Free — no `SpendGate`, no Idempotency-Key. The button is disabled while
  a build is in flight, and `lastAdded` counts the selection.
- `NewJobView` gained a **Single | Batch** segmented control (`newjob.mode`). Batch renders
  `BatchComposerSection`: shared slots, an outfit multi-select capped at 12 (`batch.pickOutfits` →
  `outfit.pick.<id>`), a per-outfit "Use saved try-on" toggle (`batch.seed.<outfitID>`, on by default
  when the library has a match), and the run button (`batch.run`). The seed observers sit above the
  Single|Batch split, so a mode switch cannot strand a seed picked against the old slots.
- `TryonLibraryStore` backs the Material tab's **Materials | Saved try-ons** split (`MaterialTabView`):
  browse the grid, **Use in job** (one `PATCH` through `DraftStore.apply`, then switch to the New Job
  tab in Single mode), and delete after a confirmation naming the jobs that would lose their image.
  `matches(slots:)` is exact equality over every non-driver role.
- The edited job's seed shows a badge in Single mode, with a "The saved try-on no longer exists"
  variant when its library entry was deleted; a basket entry shows a "Saved try-on" tag.
- Drop after Phase A (`RunFlow`): a batch of ≥ 2 jobs offers "Drop from batch" (`tryon.drop.<index>`)
  per preview card, which re-validates the draft and re-reads the previews and rent panel; `canConfirm`
  is guarded by `!isDropping`. Run detail shows a per-job batch progress list. The server's draft view
  reports a read-only `tryon_seed` on the edited job and each basket entry.

## Current file map

```text
ios/
  MotionKit/Sources/MotionKit/
    API/                    APIClient, APIError, credentials
    Models/                 runs, pod, outputs, materials, drafts (slots, batch, seed, DraftPatch),
                            run flow (previews, rent panel), try-on library
    Money/                  SpendIntent, Guidance, IdempotencyLedger, SpendResult, SpendGate
    Secrets/                Keychain storage and first-launch seeding
    Stores/                 Runs, detail, outputs, pod (kill, banner), GPU, balance, materials, draft,
                            RunFlow, MigrateFlow, TryonLibraryStore, BatchComposer
    Upload/                 chunk planning, checkpoint journal, uploader
  MotionKit/Tests/MotionKitTests/
                            244 logic/contract tests in 23 suites
  MotionApp/
    Runs/ Outputs/ Settings/ RunFlow/ Pod/
                            thin SwiftUI consumers of stores
    Materials/              MaterialsView, MaterialTabView (Materials | Saved try-ons), SavedTryonsView
    NewJob/                 NewJobView (Single | Batch), BatchComposerSection, SlotRow, pickers
  MotionAppUITests/         live Phase 3 + zero-spend Phase 4, 5 and 6 simulator smoke
  project.yml               XcodeGen source; generated project is ignored
  Secrets.xcconfig          generated and ignored; never commit
```

## Verified gates at the handoff

### Phase 6 (2026-09-24, branch `feat/swiftui-phase-6`, unmerged)

What actually ran, on the worktree:

- `make ios-build` — exit 0. This compiles `MotionApp` only: the scheme builds `MotionAppUITests` for
  the `test` action, not the `build` action, so `ios-build` does not cover the UI-test target.
- `xcodebuild -scheme MotionApp -destination 'generic/platform=iOS Simulator' build-for-testing` —
  exit 0, and `Phase6SmokeTests.o` plus `MotionAppUITests.xctest` were produced. This is what proves
  the new UI-test file compiles. No simulator was booted.
- `cd ios/MotionKit && swift test` — 244 tests in 23 suites, exit 0. Unchanged from the Task 1–9
  baseline; this task added no MotionKit test.
- `motions-studio/setup/scrub-secrets.sh --check` — exit 0.

Pending the controller run, **not** passed here:

- `make ios-contract` — the 14th route (`GET /v1/tryon-library`) decoding against the live server;
  expected 14/14. It contacts the VPS, so it is the controller's to run.
- `make ios-ui-test` — `Phase6SmokeTests`, live and zero-spend on a booted simulator. It must run
  outside the command sandbox (simulator control is killed inside it). One assertion has no passing
  precedent: the Materials toolbar check queries `"Add material"` (`MaterialsView.swift:96`, in
  `.toolbar` at `:51`) type-agnostically, because a SwiftUI `Menu`'s XCUITest element type is not
  guaranteed to be `.button`. If XCUITest does not surface a toolbar `Menu`'s accessibility label, that
  single assertion false-alarms while the toolbar still works — widen the query, do not delete the
  assertion (it is the only check that Task 7's `MaterialTabView` wrapper did not break toolbar
  propagation). Every other asserted string is precedent-backed by a shipped screen.

No real batch has run. A 2-outfit batch with one seeded job — proving Phase A calls the provider once
for the unseeded outfit and reuses the saved image for the seeded one — is the proposed spend test; it
needs its own approval and a quoted price, and its cost comes from the balance delta and then
`runpodctl billing`.

### Phase 5 (2026-09-23, merged as `7a8c0e2`)

- `make ios-test` — 211 tests in 21 suites.
- `make ios-build` — succeeded.
- `make ios-contract` — 13 live GET contracts, adding `GET /v1/gpu/stock` (cached) and `GET /v1/balance`
  (no Vast), with `PodStatus.migration` decoded.
- `make ios-ui-test` (sandbox disabled) — first run: `Phase3SmokeTests` and `Phase4SmokeTests` passed;
  `Phase5SmokeTests` asserted the balance card and five GPU rows, then skipped the migrate half at its
  no-lease guard because a real RunPod lease (`tg-1959705051`, provisioned ~16:59 +07, started by the
  user, not the agent) was live. After the user stopped that pod, a re-run passed all three suites with
  `Phase5SmokeTests` in full: a live `migrate/ask`, the warning shown, Migrate locked with the field
  empty, and zero recorded spends. The Phase 3 smoke's "must not expose Pod" check ignores the tab-bar
  item.
- `scrub-secrets.sh --check` — passed.
- `make ios-refusal-smoke` — run live with the user's approval, no lease: no lease before; confirm →
  `409 stale_panel`; regen → `409 stale_panel`; resume → `409 stale_run`; idle kill → `409
  nothing_running`; bogus migrate → `409 bad_confirm_token`; no lease after. Zero spend.

### Phase 4

All of these passed on 2026-09-23 after the final Phase 4 change:

- `make ios-test` — 158 tests in 18 suites.
- `make ios-build` — simulator build succeeded.
- `make ios-contract` — eleven live GET contracts decoded: health, pipelines, draft, runs, newest run,
  pod, `runs/{slot}/tryon`, `runs/{slot}/rent-panel`, materials, outputs and authenticated output Range.
- `make ios-ui-test` — passed on a booted iPhone 18 Pro simulator, run with the command sandbox
  disabled (simulator control is killed inside it): both `Phase3SmokeTests` and the new
  `Phase4SmokeTests` (`-UITestRecordingSpendGate`, reaches a priced Confirm or the sold-out state
  without tapping a spend button).
- `IOS_SIMULATOR_ID=7BB12409-C05E-4E9A-B3BD-8F77D3036BB7 make ios-ui-test` — proved a shutdown
  iPhone 17e is booted automatically, tested, then returned to Shutdown.
- `make api-smoke` — expected `403` without Access, `401` without bearer and `200` with both.
- `motions-studio/setup/scrub-secrets.sh --check` — passed.
- `make ios-refusal-smoke` (`motion-contract --refusal-smoke`) — run live on 2026-09-23 after the user
  approved it: `confirm` with a bogus `panel_token` → `409 stale_panel`, `regen` with a bogus
  `run_token` → `409 stale_panel`, `resume` with a bogus `run_token` → `409 stale_run`, and no pod
  lease before or after. Zero spend. It stays opt-in: it sends live POSTs, so ask before each run.
- The live draft was empty after smoke: zero slots, jobs and basket entries.

`make ios-ui-test` uses only free draft/read routes plus the Phase 4 flow up to (never through) a spend
button. It selects an available iPhone Simulator, boots one when needed, clears the live draft before
and after testing, and shuts down only a simulator that it booted. It passes
`-collect-test-diagnostics never` because Xcode 27 can otherwise spend minutes collecting
`simctl diagnose` after a UI-test failure. Because it drives simulator control, it must run outside this
agent's command sandbox (simulator control is killed inside it) — run it with the sandbox disabled, or
from a plain terminal.

The live UI smoke assumes the VPS material library contains at least one compatible image and video.
If that precondition is missing, restore test material; do not weaken the kind-filter assertions.

## Real spend test (2026-09-23)

Run once with the user's approval and a quoted cost, through the app's own MotionKit code
(`DraftStore`, `RunFlow` + `SpendGate`, `GpuStore`, `PodStore`) from a throwaway CLI — not by tapping
the UI:

1. Draft: 1 × `tryon-motion-enhance`, provider `gemini`, validated (free).
2. **Phase A** (real Gemini call) → preview done in ~30 s.
3. RTX 5090 went out of stock at EU-RO-1 between the quote and the confirm; the rent panel correctly
   offered no spend button. **GPU change** (`PUT /v1/pod/gpu`) to RTX PRO 4500 ($0.72/h) through
   `GpuStore`, panel re-read → quote $0.14; stock flickered to none and back within a minute.
4. **Confirm** accepted at 19:20:00 +07; lease at 19:20:05.
5. **Kill** through `PodStore.kill` → `last_kill = killed` ("pod destroyed and verified gone") in 7 s;
   `runpodctl pod list` empty.
6. GPU switched back to RTX 5090.

Cost: the RunPod balance (`GET /v1/balance`, a real account read) went from $10.5964 to $10.5842 —
**$0.012**. `runpodctl billing pods` had not listed the pod 8 minutes later; the invoice lags, so
re-check it before quoting this number anywhere else. Gemini cost for the one try-on was not
measured. Not exercised: a real migration (never, unless the user asks), and the same path by
tapping the UI on a phone.

## Known incomplete work

- Phase 2's physical-phone smoke with a real interrupted upload larger than 32 MiB has not run.
- Phase 6 is complete in code on `feat/swiftui-phase-6`, but its two live gates — `make ios-contract`
  (the 14th route) and `make ios-ui-test` (`Phase6SmokeTests`) — are pending the controller run; they
  contact the VPS and boot a simulator, so they did not run here. No real batch has run: a 2-outfit
  batch with one seeded job is the proposed spend test and needs its own approval and quoted price.
- A one-job draft has no drop affordance by design — "Drop from batch" needs ≥ 2 jobs (Phase 6 spec
  §5). **Clear**, on the New Job tab in Single mode, is the only way to remove the last job.
- Batch mode renders no `Clear` button and no readiness line: Task 8's plan put `editorActions(draft)`
  (the only `Clear`, `NewJobView.swift:231`) and `readiness(draft)` (`:208`) in the Single arm of
  `NewJobView.editor` alone, so clearing the draft from Batch mode means switching to Single first.
  Individual basket entries can still be dropped in Batch mode, and `Validate` renders in both.
  Plan-mandated and discoverable via the mode switch, but a real papercut — `Phase6SmokeTests` has to
  switch `newjob.mode` → Single before every `clear(in:)`, which is the evidence it is not theoretical.
- Follow-up (cannot be fixed on this branch): `ios/MotionKit/Tests/MotionKitTests/Fixtures.pipelines`
  names `tryon-motion-enhance`'s optional role `mask`, while the live catalog says `background`;
  `mask` occurs nowhere in `scripts/**` as a role. `Fixtures.swift` cannot be edited here because two
  suites do exact `.replacingOccurrences` surgery on `Fixtures.draft`'s text, so a change can silently
  break another suite's assertions.
- Follow-up (needs its own VPS deploy gate): the server's invalid-validation message reaches the phone
  verbatim as `make batch-validate failed: …` — developer-facing copy on a consumer screen. Fixing it
  is a `scripts/**` change.
- Open question (money safety, deliberately not fixed on this branch): `isDropping` gates only Confirm.
  `RunFlow.spend(_:label:)` guards on `inFlightLabel == nil` (`RunFlowStore.swift:484-485`), and a drop
  is a free draft mutation that never sets `inFlightLabel`, so `canSpend` (`:69`, which has no
  `!isDropping`) stays true while a drop is in flight. `canConfirm` gained `!isDropping` (`:365`) in
  Task 5's fix round precisely because a Confirm tapped mid-drop would present a `panel_token` the
  server still accepts — the token is `f"{_run_token(chat_id)}.{generation}"`
  (`scripts/tgbot/bot.py:6895-6902`) and `generation` does not move until the drop's first write lands.
  Confirm is therefore closed, but **`regenerate` (`:472`), resume (`retryRental()`, `:411`),
  `choose(_:)` (`:398`) and `MigrateFlow.migrate()` (`MigrateFlow.swift:109`) are not `isDropping`-gated.**
  Widening `canSpend` to carry `!isDropping` reaches `canDropFromBatch` itself (`:229`) and the
  pending-notice/migrate machinery, so the blast radius is past this branch. Recorded as an open
  question with that reasoning, not a bug to fix here.
- Constraint on a future change (not a bug): `BatchComposer.run()` re-picks per-outfit seeds only when
  the draft it re-reads changed `sharedSlots`, because an unconditional re-pick would override the
  shipped "Saved image" picker on every build. That is safe today only because `outfits` is populated
  solely from `BatchComposerSection`, and `NewJobView` is retained by `TabView`/`NavigationStack` once
  shown, so its hoisted `.onChange(of: composer.sharedSlots)` / `.onChange(of: library.loaded)`
  observers outlive tab switches and pushes. If the composer is ever moved behind a genuinely
  unmounting screen, the re-pick trigger must be widened — or compare the draft's `generation` instead
  of the slots.
- The real Phase A → GPU change → confirm → kill path ran once through the app's MotionKit code
  (§"Real spend test"); it has not been driven by tapping the UI. No real migration has run.
- No GPU or pod was rented for Phases 1–6. Do not reinterpret simulator or fake-server coverage as a
  real spend-path test — no live Phase A, regenerate, confirm or resume has run. The refusal smoke
  proves only that bogus-token spends are refused before anything is called.
- `ios/Secrets.xcconfig`, personal media, screenshots with personal names, upload checkpoints and live
  API payloads must remain uncommitted.

## Next work

No phase remains — Phases 1–6 are implemented in code. Open items, none of them a phase:

1. **Run the pending live gates, then merge** (controller). `make ios-contract` (expect 14/14) and
   `make ios-ui-test` (`Phase6SmokeTests`) both contact the VPS / boot a simulator, so they did not run
   here. `main` is 2 commits ahead of `origin/main` (`4052b97` spec, `5eaa652` plan), both unpushed —
   push `main` first or the Phase 6 PR will carry them. Task 1 touches `scripts/**`, so before merging
   check the VPS for a drain, Phase A, pod lease and migration (the merge auto-deploys `motion-bot`).
2. **Optional spend test.** A 2-outfit batch with one seeded job, proving Phase A calls the provider
   once for the unseeded outfit and reuses the saved image for the seeded one. Needs its own approval
   and a quoted price; cost from the balance delta then `runpodctl billing`; `make gpu-destroy` after.
3. **Parent spec's deferred items** (`2026-09-22-swiftui-app-design.md` §1): pair (1:1) mode and
   stock-watch notifications. Both need a later API slice, not app work.
4. **Two Phase 6 follow-ups** (see "Known incomplete work"): the `Fixtures.pipelines` `mask` vs
   `background` mismatch, and the server's developer-facing validation message on the phone.

Required safety behavior carries over unchanged from Phase 4: one UUID key per tap and never mint a new
one to retry an earlier spend intent; disable the active button and show its quote while in flight;
persist the ledger entry before sending and replay it once, under 20 h, on launch; zero-spend gates
(`ios-test`, `ios-build`, `ios-contract`, `ios-ui-test`, `scrub-secrets.sh --check`) before every commit,
with any live call needing its own separate approval and quoted cost. Before any later push touching
`scripts/**`, check the VPS for a drain, Phase A, pod lease and migration — pure `ios/**` changes do not
auto-deploy the bot.

## Recommended kickoff for the next effort

No phase remains (see "Next work"), so this is the start of whatever comes next — running the pending
live gates and merging, the optional spend test, a deferred parent-spec item, or a Phase 6 follow-up.

1. Verify `git status`, the branch and the current SHA; preserve unrelated changes. Phase 6 lives on the
   unmerged `feat/swiftui-phase-6`, and `main` is 2 unpushed commits ahead of `origin/main`
   (`4052b97` spec, `5eaa652` plan) — push `main` before opening the Phase 6 PR.
2. Read `AGENTS.md`, this handoff, the parent SwiftUI design and the Phase 6 design. For anything
   touching the money layer, inspect the shipped `RunFlow`/`SpendGate`/`MigrateFlow`/`PodStore` stores
   and tests for state and error-handling conventions first.
3. Before any push that touches `scripts/**` — Phase 6's read-only `tryon_seed` field is one — check the
   VPS for a drain, Phase A, pod lease and migration; the merge auto-deploys `motion-bot`. Pure `ios/**`
   changes do not.
4. Keep views free of direct `APIClient` calls; execute task-by-task TDD.
5. Report live validation separately from mocked/simulator validation; never claim a spend path was
   tested unless it actually ran with prior user authorization and a quoted cost.

