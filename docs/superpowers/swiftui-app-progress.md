# Motion SwiftUI App — Development Progress and Handoff

Last updated: 2026-09-23

This is the current implementation handoff for the native iPhone app. Read it before planning or
changing another SwiftUI phase. The approved product contract remains
[`specs/2026-09-22-swiftui-app-design.md`](specs/2026-09-22-swiftui-app-design.md); this file records
what actually shipped, what was verified, and the next safe boundary.

## Current baseline

- Repository: `/Users/thucpham/Desktop/motion-clone`
- Branch: `main`
- Latest implementation commit: `2945481` (merge of PR #65, `feat/swiftui-phase-4`), merged 2026-09-23.
- PR #65 changed only `ios/**`, `docs/**` and `Makefile` — nothing under `scripts/**` — so it did not
  trigger the deploy-bot workflow. The VPS bot still runs the `affcf46` deploy (GitHub Actions run
  `35813558396`, `motion-bot` active, control API on `127.0.0.1:8787`).
- Control-plane API slices 1–6 are live. Phase 4 added no route or field.
- Phases 1–4 are implemented. Phase 5 has not started; there is no Phase 5 detail spec or plan yet.

## Phase status

| Phase | Status | Delivered | Remaining evidence |
|---|---|---|---|
| 1 — shell and read-only app | Complete | Keychain credentials, Settings health test, Runs, run detail with ETag polling, Outputs, authenticated Range playback, Save to Photos, Silent Mode audio behavior | No current blocker |
| 2 — materials and uploads | Complete in code | Material list and thumbnails, Photos/Files import, chunked foreground upload, persisted checkpoints, resume of missing chunks, delete ownership/error handling | Physical-phone interruption test for a video larger than 32 MiB is still pending |
| 3 — single New Job | Complete | Catalog-driven pipeline/provider picker, compatible material pickers, server-authoritative draft mutations, validation, add/drop basket entries, stale/error reconciliation | Automated live simulator smoke passes; physical phone is optional coverage |
| 4 — run flow | Complete in code | `SpendGate`/idempotency ledger, Phase A + try-on previews, regenerate with the closed guidance vocabulary, Keep, the rent panel (RunPod + Vast, out-of-stock), confirm (with the reuse/rerun chooser) and resume, all through `RunFlow`; `make ios-test` (158 tests), `make ios-build`, `make ios-contract` (adds `/tryon` and `/rent-panel`), `make ios-ui-test` (adds `Phase4SmokeTests`, zero-spend via `-UITestRecordingSpendGate`) and `scrub-secrets.sh --check` all pass | `make ios-refusal-smoke` passed live on 2026-09-23 (three bogus-token spends refused with 409, no lease before or after). No live Phase A / regen / confirm / resume has run — no pod has been rented for Phase 4 |
| 5 — pod and cost | Not started | Nothing yet | Kill, GPU choice, balances and migration |
| 6 — batch/library | Not started | Nothing yet | Cross build UI, batch progress, saved try-ons and guided regenerate |

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

## Current file map

```text
ios/
  MotionKit/Sources/MotionKit/
    API/                    APIClient, APIError, credentials
    Models/                 runs, pod, outputs, materials, drafts, run flow (previews, rent panel)
    Money/                  SpendIntent, Guidance, IdempotencyLedger, SpendResult, SpendGate
    Secrets/                Keychain storage and first-launch seeding
    Stores/                 Runs, detail, outputs, pod, materials, draft, RunFlow
    Upload/                 chunk planning, checkpoint journal, uploader
  MotionKit/Tests/MotionKitTests/
                            158 logic/contract tests in 18 suites
  MotionApp/
    Runs/ Outputs/ Materials/ NewJob/ Settings/ RunFlow/
                            thin SwiftUI consumers of stores
  MotionAppUITests/         live Phase 3 + zero-spend Phase 4 simulator smoke
  project.yml               XcodeGen source; generated project is ignored
  Secrets.xcconfig          generated and ignored; never commit
```

## Verified gates at the handoff

All of these passed on 2026-09-23 after the final implementation change:

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

## Known incomplete work

- Phase 2's physical-phone smoke with a real interrupted upload larger than 32 MiB has not run.
- Phase 5–6 app models, stores and screens (pod management, GPU choice, balances, migrate, batch/library)
  do not exist yet.
- No GPU or pod was rented for Phases 1–4. Do not reinterpret simulator or fake-server coverage as a
  real spend-path test — no live Phase A, regenerate, confirm or resume has run. The refusal smoke
  proves only that bogus-token spends are refused before anything is called.
- `ios/Secrets.xcconfig`, personal media, screenshots with personal names, upload checkpoints and live
  API payloads must remain uncommitted.

## Next work

Two things are open, in order:

1. **The one real spend test for Phase 4.** Development stayed zero-spend on purpose
   (§"Known incomplete work"); the zero-spend `make ios-refusal-smoke` has passed. Before Phase 5 work
   starts, offer the user — only with a quoted cost and a separate explicit go-ahead — one real
   Phase A + confirm + `make gpu-destroy`, with cost read from `runpodctl billing`, never
   `currentSpendPerHr`. It has not run.
2. **Phase 5 — pod and cost.** Create and approve a focused Phase 5 design before implementation, then
   write its implementation plan. Suggested paths:

   ```text
   docs/superpowers/specs/2026-09-2X-swiftui-app-phase-5-design.md
   docs/superpowers/plans/2026-09-2X-swiftui-app-phase-5.md
   ```

   Its scope is fixed by the parent design (`2026-09-22-swiftui-app-design.md` §5 row 5, §3 "Pod &
   cost"): `GET /v1/pod`, `GET /v1/gpu/stock`, `GET /v1/balance`, kill (`POST /v1/runs/{id}/kill` → `202`, then poll `GET /v1/pod` until
   `kill_running` is false; `destroy_unverified` raises a persistent banner), GPU choice
   (`PUT /v1/pod/gpu` then a panel re-read), and migrate (`ask` → typed-confirmation sheet → `migrate`
   with its token, expiring in 10 min or on a bot restart). It reuses `SpendGate` for any call that
   takes an `Idempotency-Key`; kill and migrate's own idempotency rules are in the control-plane API
   design §5.9–§5.10. Out of scope: Phase 6 (cross builds, batch progress, saved try-ons, guided
   regenerate).

Required safety behavior carries over unchanged from Phase 4: one UUID key per tap and never mint a new
one to retry an earlier spend intent; disable the active button and show its quote while in flight;
persist the ledger entry before sending and replay it once, under 20 h, on launch; zero-spend gates
(`ios-test`, `ios-build`, `ios-contract`, `ios-ui-test`, `scrub-secrets.sh --check`) before every commit,
with any live call needing its own separate approval and quoted cost. Before any later push touching
`scripts/**`, check the VPS for a drain, Phase A, pod lease and migration — pure `ios/**` changes do not
auto-deploy the bot.

## Recommended Claude kickoff

1. Verify `git status`, branch and current SHA; preserve unrelated changes.
2. Read `AGENTS.md`, this handoff, the parent SwiftUI design and the control-plane API design §5.9–§5.10.
3. Inspect the shipped Phase 4 `RunFlow`/`SpendGate` store and tests for state and error-handling
   conventions — Phase 5 reuses the same money layer.
4. Draft the Phase 5 design with explicit spend/no-spend acceptance criteria and wait for approval.
5. Execute task-by-task TDD. Keep views free of direct `APIClient` calls.
6. Report live validation separately from mocked/simulator validation; never claim a spend path was
   tested unless it actually ran with prior user authorization.

