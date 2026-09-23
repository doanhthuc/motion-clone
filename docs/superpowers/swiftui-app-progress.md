# Motion SwiftUI App — Development Progress and Handoff

Last updated: 2026-09-23

This is the current implementation handoff for the native iPhone app. Read it before planning or
changing another SwiftUI phase. The approved product contract remains
[`specs/2026-09-22-swiftui-app-design.md`](specs/2026-09-22-swiftui-app-design.md); this file records
what actually shipped, what was verified, and the next safe boundary.

## Current baseline

- Repository: `/Users/thucpham/Desktop/motion-clone`
- Branch: `main`
- Latest implementation: `7a8c0e2` (merge of PR #66, `feat/swiftui-phase-5`), merged 2026-09-23.
  Phase 4 was PR #65 (`2945481`).
- PR #66 changed only `ios/**`, `docs/**`, `Makefile`, `CLAUDE.md` and `AGENTS.md` — nothing under
  `scripts/**` — so it did not trigger the deploy-bot workflow.
- PR #65 did not touch `scripts/**` either. The VPS bot still runs the `affcf46` deploy (GitHub Actions run
  `35813558396`, `motion-bot` active, control API on `127.0.0.1:8787`).
- Control-plane API slices 1–6 are live. Phases 4 and 5 added no route or field.
- Phases 1–5 are implemented. Phase 6 has not started.

## Phase status

| Phase | Status | Delivered | Remaining evidence |
|---|---|---|---|
| 1 — shell and read-only app | Complete | Keychain credentials, Settings health test, Runs, run detail with ETag polling, Outputs, authenticated Range playback, Save to Photos, Silent Mode audio behavior | No current blocker |
| 2 — materials and uploads | Complete in code | Material list and thumbnails, Photos/Files import, chunked foreground upload, persisted checkpoints, resume of missing chunks, delete ownership/error handling | Physical-phone interruption test for a video larger than 32 MiB is still pending |
| 3 — single New Job | Complete | Catalog-driven pipeline/provider picker, compatible material pickers, server-authoritative draft mutations, validation, add/drop basket entries, stale/error reconciliation | Automated live simulator smoke passes; physical phone is optional coverage |
| 4 — run flow | Complete in code | `SpendGate`/idempotency ledger, Phase A + try-on previews, regenerate with the closed guidance vocabulary, Keep, the rent panel (RunPod + Vast, out-of-stock), confirm (with the reuse/rerun chooser) and resume, all through `RunFlow`; `make ios-test` (158 tests), `make ios-build`, `make ios-contract` (adds `/tryon` and `/rent-panel`), `make ios-ui-test` (adds `Phase4SmokeTests`, zero-spend via `-UITestRecordingSpendGate`) and `scrub-secrets.sh --check` all pass | `make ios-refusal-smoke` passed live on 2026-09-23 (three bogus-token spends refused with 409, no lease before or after). No live Phase A / regen / confirm / resume has run — no pod has been rented for Phase 4 |
| 5 — pod and cost | Complete in code | Pod tab: lease card with a quoted cost, kill (fresh key per tap, outside `SpendGate`, followed to `last_kill`), the "pod may still be billing" banner (`destroy_unverified`/`error`, cleared only by acknowledgement or a later successful kill), GPU choice on the Pod tab and the rent panel (re-reads the panel), RunPod balance and on-tap Vast credit, the migration (ask → typed, expiring confirmation → `SpendIntent.migrate` through `SpendGate`), an unanswered migrate on every tab; `make ios-test` (211 tests), `ios-build`, `ios-contract` (adds `gpu/stock`, `balance`), `ios-ui-test` (adds `Phase5SmokeTests`) pass | With no pod leased (2026-09-23): `make ios-ui-test` ran `Phase5SmokeTests` in full and `make ios-refusal-smoke` passed all seven steps. A real Phase A → GPU change → confirm → kill ran once through the app's MotionKit code ($0.012, see §"Real spend test"). No real migration has run |
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
    Stores/                 Runs, detail, outputs, pod (kill, banner), GPU, balance, materials, draft,
                            RunFlow, MigrateFlow
    Upload/                 chunk planning, checkpoint journal, uploader
  MotionKit/Tests/MotionKitTests/
                            211 logic/contract tests in 21 suites
  MotionApp/
    Runs/ Outputs/ Materials/ NewJob/ Settings/ RunFlow/ Pod/
                            thin SwiftUI consumers of stores
  MotionAppUITests/         live Phase 3 + zero-spend Phase 4 and Phase 5 simulator smoke
  project.yml               XcodeGen source; generated project is ignored
  Secrets.xcconfig          generated and ignored; never commit
```

## Verified gates at the handoff

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
- Phase 6 app models, stores and screens (cross build, batch progress, saved try-ons) do not exist yet.
- The real Phase A → GPU change → confirm → kill path ran once through the app's MotionKit code
  (§"Real spend test"); it has not been driven by tapping the UI. No real migration has run.
- No GPU or pod was rented for Phases 1–4. Do not reinterpret simulator or fake-server coverage as a
  real spend-path test — no live Phase A, regenerate, confirm or resume has run. The refusal smoke
  proves only that bogus-token spends are refused before anything is called.
- `ios/Secrets.xcconfig`, personal media, screenshots with personal names, upload checkpoints and live
  API payloads must remain uncommitted.

## Next work

One thing is open:

1. **Phase 6 — batch and library.** Create and approve a focused Phase 6 design first (parent design
   §5 row 6: cross build, bulk try-on, batch progress, saved try-ons, guided regenerate).

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
3. Inspect the shipped `RunFlow`/`SpendGate`/`MigrateFlow`/`PodStore` stores and tests for state and
   error-handling conventions — Phase 6 reuses the same money layer.
4. Draft the Phase 6 design with explicit spend/no-spend acceptance criteria and wait for approval.
5. Execute task-by-task TDD. Keep views free of direct `APIClient` calls.
6. Report live validation separately from mocked/simulator validation; never claim a spend path was
   tested unless it actually ran with prior user authorization.

