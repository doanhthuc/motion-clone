# Motion SwiftUI App — Development Progress and Handoff

Last updated: 2026-09-25

This is the current implementation handoff for the native iPhone app. Read it before planning or
changing another SwiftUI phase. The approved product contract remains
[`specs/2026-09-22-swiftui-app-design.md`](specs/2026-09-22-swiftui-app-design.md); this file records
what actually shipped, what was verified, and the next safe boundary.

## Current baseline

- Repository: `/Users/thucpham/Desktop/motion-clone`
- Branch: merged. Phase 6 is **PR #67** (Phase 5 was PR #66 → `7a8c0e2`, Phase 4 PR #65 → `2945481`).
  It first landed as merge commit `8518e13`, and `main` was then rewritten on request into a single
  squash commit **`f65fc63`**. `git diff f65fc63 8518e13` is empty — the two trees are byte-identical, so
  only the history shape changed. `8518e13` is no longer reachable from `main`; the 34 granular commits
  and their review provenance remain viewable on PR #67.
- `main` and `origin/main` are in sync at `f65fc63`. The Phase 6 spec (`4052b97`) and plan (`5eaa652`)
  were pushed ahead of the branch, docs-only, which did not trigger the deploy workflow — its path filter
  is `scripts/**` plus the workflow file itself.
- PR #66 changed only `ios/**`, `docs/**`, `Makefile`, `CLAUDE.md` and `AGENTS.md` — nothing under
  `scripts/**` — so it did not trigger the deploy-bot workflow; PR #65 did not touch `scripts/**` either.
  **PR #67 did, and deployed twice**: GitHub Actions run `35950453694` for `8518e13` and run
  `35951269293` for the squash rewrite, both `completed success`. The VPS is on **`f65fc63`**,
  `motion-bot` active since 2026-09-24 03:23:59 UTC, control API on `127.0.0.1:8787`.
  `scripts/vps/deploy-bot.sh` runs `git reset --hard origin/main`, not a fast-forward, which is why it
  followed the rewritten history with no manual intervention on the box.
- Phase 6 is the first phase to touch `scripts/**`: Task 1 adds a read-only `tryon_seed` field to
  `GET /v1/draft` (`scripts/control/drafts.py`, in `_view`). **Deployed and verified live**: the key is
  present in the response (`null` on an empty draft), no absolute path leaks, and `make ios-contract` is
  14/14 against the redeployed server. The app decodes the field as optional, which is why
  `make ios-contract` also passed 14/14 *before* the deploy — either order of deploy works.
- Control-plane API slices 1–6 are live. Phases 4 and 5 added no route or field; Phase 6 adds one
  read-only field and no route.
- Phases 1–6 are implemented in code, and every gate in Phase 6's record has run: `make ios-contract`
  (14/14, live against the VPS, 2026-09-24) and `make ios-ui-test` (`Phase6SmokeTests`, 4/4 passed,
  zero spends recorded, 2026-09-24). See "Gate record" below — that table is the single place a gate
  result is written down.

## Multi-driver batch (2026-09-25, PR #70 → `737c15c`, deployed)

Spec: [`specs/2026-09-25-multi-driver-batch-shared-tryon-design.md`](specs/2026-09-25-multi-driver-batch-shared-tryon-design.md);
plan: [`plans/2026-09-25-multi-driver-batch-shared-tryon.md`](plans/2026-09-25-multi-driver-batch-shared-tryon.md).
The spec's gate record is the single place its gate results are written down.

- **What it replaces.** The parent spec's deferred "pair (1:1) mode". The user asked for a different
  shape: one character, many outfits *and* many drivers. Batch mode now crosses N outfits × M drivers
  (`batch.pickDrivers`), capped at **12 jobs in total** (`BatchComposer.maxJobs`), not 12 outfits.
- **The money half is server-side.** Phase A used to make one try-on per job, so 4 outfits × 3 drivers
  cost 12 provider calls for 4 distinct images, and the three videos of one outfit showed three
  different renders. `runner.tryon_share_groups` now groups runs whose try-on inputs are identical
  (the driver counts only for a camera-aware stage). The leader calls the provider, and followers
  copy its image. A follower never calls the provider, in Phase A or on the pod: the Phase B guard was
  added after the final review found that a leader failing in Phase A let every follower resubmit its
  try-on on the GPU.
- **The app shows one preview card per look.** "Drop from batch" on a card drops every job that uses
  that image. The summary line counts videos and try-ons before Phase A. For a pod provider (`qwen`)
  the try-on count equals the job count, because no sharing happens there.
- **Not yet proven by a real run:** no Phase A has run over a real 2 × 2 batch. The proposed spend
  test is 2 outfits × 2 drivers, expecting 2 provider calls, and it needs its own approval and a
  quoted price.

## New Job single stage (2026-09-26, branch `newjob-single-stage`)

Spec: [`specs/2026-09-26-newjob-single-stage-design.md`](specs/2026-09-26-newjob-single-stage-design.md);
plan: [`plans/2026-09-26-newjob-single-stage.md`](plans/2026-09-26-newjob-single-stage.md).

- **New Job is one stage that does not scroll.** It has slot cards sized to the space left
  (`SlotCardGrid`), a Pipeline · Provider chip in the toolbar, a basket drawer above the action bar,
  and Add / Continue. The Single | Batch switch is gone: on a try-on pipeline, Outfit and Driver take
  many picks (`NewJobState`), and one of each is one job.
- **Continue adds a pending composition, validates, and opens the run flow in one tap.** Validate is no
  longer a step of its own.
- **The crossed roles live only in `BatchComposer`.** `adoptDraftSelection()` moves a draft's outfit,
  seed and driver in, which is how Saved try-ons' "Use in job" still works. `reset()` empties the
  selection on Clear and on switching to a non-try-on pipeline. After a build, **drivers stay picked**
  and only the outfits clear.
- **Picking chains only on a fresh draft.** One sheet walks the empty required cards. Long press on a
  card gives Clear, or the outfit's saved try-on and Remove. Drop is a swipe left in the drawer.
- **Refresh moved into the `⋯` menu.** The icon turns to a warning while the draft is stale. A bar
  button that came and went with `isStale` crowded the chip, and iOS 26 then dropped the whole
  trailing group, Clear with it (seen in the first Phase 3 run).
- **Gates:**
  - `make ios-test`: 316/316 passed.
  - `make ios-build`: clean.
  - `make ios-ui-test`: 8/9 on the rerun. `Phase5SmokeTests` timed out once waiting for the RunPod
    migrate ask, then passed alone.
  - `NewJobStageTests` passed on an iPhone SE (3rd gen) and an iPhone 18 Pro Max simulator. It checks
    that every card sits above the action bar and that `⋯` stays in the bar, with no swipe.
    Screenshots are in `out/newjob-stage/` (gitignored).
- **Not yet done:**
  - Installed on the phone: no. The phone read `unavailable` to `devicectl` on 2026-09-26.
  - No screenshot covers the drawer open, the error banner, or the chain mid-way.
  - The gestures (paging through outfits on a card, dragging the drawer, the long-press menus) have
    only been exercised by XCUITest taps, not by hand.

## Materials add flow (2026-09-26)

- **The category is chosen before the pick.** The "What is this image?" dialog that followed each
  image upload is gone: it floated over the grid, detached from the image, one dialog per photo.
  The Add sheet has a "Save photos as" choice (remembered in `materials.add.role`); a See all
  screen has its own + and files what it adds under that category, with the picker limited to
  what fits it (videos + TikTok link for drivers, images otherwise).
- **Multi-select.** Photos and Files take up to 20 at once; `MaterialUploadQueue` uploads them one
  after another (the store runs one upload at a time) and `UploadQueueCard` shows the batch with a
  per-item state strip. The queue lives in `MaterialTabView`, so it survives See all and the
  Saved try-ons switch. A failure that leaves a resumable checkpoint stops the rest of the batch.
- **Pickers present from the screen, not from the sheet.** The Add sheet closes first. Stacked
  on the small-detent sheet, the Photos picker lagged and flickered on open and on first scroll
  (reported on a phone 2026-09-26). Whether this change alone removed the flicker is not yet
  confirmed on device.
- Gates: `make ios-build`; `make ios-ui-test` exit 0 (before the last progress fix); installed on
  the phone.

## Native design pass (2026-09-25)

An audit of the rendered app found it read as generated. It counted lime in 54 places with eight
meanings, amber on every untouched slot, eight corner radii, 31 font size/weight variants down to
9pt, and a bordered card around every row. The app now uses inset-grouped `List`s, SF Pro text
styles (Dynamic Type), the system grouped palette, two radii (8/12), and lime only for what is
interactive or selected. Both bundled fonts were removed, and the tabs are Runs · Materials ·
New Job · Outputs · Pod. `ios/MotionApp/Theme.swift` holds the tokens and their rules. No route,
field or store changed.

- **Lazy lists change how the UI smokes find things.** A `List` row scrolled off screen is not in
  the accessibility tree, so the smokes scroll to an element before they assert on it
  (`Phase4Draft.revealText`, `revealButton`). A new smoke that checks `exists` on a row must do
  the same.
- **The Materials | Saved try-ons switch stays in `MaterialTabView`.** Placed as the first row
  inside a child `ScrollView`, taps landed but the selection never changed.
- **Gates run 2026-09-25:** `make ios-build`; `make ios-test` (275/275); `make ios-ui-test`
  (6/6: Phase 3, 4, 5, 6 ×2 and the Outputs feed, zero recorded spends);
  `scrub-secrets.sh --check`.

## Batch screen redesign, Photos and TikTok import in pickers (2026-09-25)

- **Every material picker can bring material in.** `MaterialImportBar` sits at the top of the
  single picker and the outfit/driver multi-pickers: Photo Library (filtered to the role's kind)
  for any role, plus **TikTok link** for video roles — a field with the system `PasteButton`, so
  a copied share caption imports in one tap. The new material is selected (or toggled into the
  batch) as soon as it lands; the sheet cannot be swiped away mid-transfer.
- **New route, `POST /v1/materials/link {url}`** (`scripts/control/links.py`). It reuses the bot's
  `tgbot/tiktok.py` unchanged and answers like an upload `complete`. The request is held open for
  the download; a Cloudflare `524` is shown as "still running" and the list is re-read, because
  the download carries on server-side. Spec: control-plane API §5.1. **Deploys with the next
  push under `scripts/**`**; until then the TikTok field answers 404.
- **Batch mode layout.** Outfits and drivers are horizontal thumbnail strips with the add tile
  first (it never scrolls out of reach); a tile's menu holds the saved-try-on choice and Remove,
  replacing the sub-44 pt ✕. The outfit/driver pickers are 3-across photo grids instead of lists
  of file names. The summary and "Add N jobs" moved into `BatchRunBar`, a glass panel pinned above
  the tab bar. Every smoke identifier (`batch.*`, `outfit.pick.*`, `driver.pick.*`) is unchanged.
- **Gates run 2026-09-25:** `make ios-build`; `make ios-test` (280/280); `make ios-ui-test`
  (6/6, 0 skipped); `make batch-test` (2191 OK). No live TikTok download through the phone yet —
  it needs the deploy.
- **Material preview (follow-up PR).** Tapping a material in the Materials tab opens
  `MaterialPreview`: a video plays in the Outputs feed's player (tap to pause, loops, the same
  `ScrubBar` in a black strip — not AVKit's controls, so the two players match), and an image loads
  at full resolution. `PlayerSurface`, `ScrubBar` and `ClipSurface` live in their own files under
  `Outputs/`, and `FeedClip` takes an API path.
  In the pickers, where a tap selects, it is behind a long-press → Play/Preview. It streams
  from the new `GET /v1/materials/{owner}/{name}` through the same
  `AuthenticatedAssetResourceLoader` the Outputs feed uses, now keyed by a path instead of
  batch/file.

## Streaming playback, Materials tab rework (2026-09-25, from a phone session)

- **Playback starts before the download ends.** `AuthenticatedAssetResourceLoader` answered a
  to-the-end request with one GET for the whole remainder, so AVPlayer saw no byte until all of it
  had arrived. It now answers in 1 MiB pieces, each handed over as it lands. Measured through the
  tunnel from the Mac: a 20.7 MB output took 2.0–2.3 s whole, 0.28–0.32 s for its first MiB. Both
  Outputs and material previews use this loader.
- **Photos import from the Materials tab works.** Its `PhotosPicker` sat inside a `Menu`, which never
  presented it. The Add button now opens `AddMaterialSheet`: Photos, TikTok link and Files, the
  same `MaterialImportBar` tiles as the job pickers.
- **Original quality.** Both Photos pickers pass `preferredItemEncoding: .current`. The default,
  `.automatic`, may transcode (HEVC to H.264, HEIC to JPEG) before upload. The server stores video
  byte for byte and turns HEIC into lossless PNG.
- **Layout.** Materials are grouped into Videos / Images / Other with pinned headers. The Add button
  is part of the layout, a full-width bar under the Materials | Saved try-ons switch: tall at rest
  ("Photos · TikTok link · Files" spelled out), shrinking to one line as the grid scrolls and never
  scrolling away. A floating bottom button was tried first and rejected on the phone: collapsed and
  centred it sat on top of the tab bar's New Job +. Tapping a material pushes the preview
  (edge swipe back, like Outputs); from a picker it opens as a sheet (swipe down).

- **Grouped by role, not by kind** (same day, on request): Motion drivers · Outfits · Characters
  (then Backgrounds and Unsorted when non-empty). Each category is **one horizontal row with a
  See all** (`MaterialCategoryView`, a 3-across grid), not a full grid: stacked grids pushed the
  next category a whole grid further down as the library grew. The role comes from the server
  (`control/material_roles.py`, see the API spec §5.1). An image uploaded from the Materials tab
  gets the bot's question ("What is this image?"); one imported from inside a job slot is tagged
  with that slot's role; long-press → Move to re-files it. Pickers list their own role first, then
  unsorted, then the rest — a role is a hint, never a lock.

## Phase status

| Phase | Status | Delivered | Remaining evidence |
|---|---|---|---|
| 1 — shell and read-only app | Complete | Keychain credentials, Settings health test, Runs, run detail with ETag polling, Outputs, authenticated Range playback, Save to Photos, Silent Mode audio behavior | No current blocker |
| 2 — materials and uploads | Complete in code | Material list and thumbnails, Photos/Files import, chunked foreground upload, persisted checkpoints, resume of missing chunks, delete ownership/error handling | Physical-phone interruption test for a video larger than 32 MiB is still pending |
| 3 — single New Job | Complete | Catalog-driven pipeline/provider picker, compatible material pickers, server-authoritative draft mutations, validation, add/drop basket entries, stale/error reconciliation | Automated live simulator smoke passes; physical phone is optional coverage |
| 4 — run flow | Complete in code | `SpendGate`/idempotency ledger, Phase A + try-on previews, regenerate with the closed guidance vocabulary, Keep, the rent panel (RunPod + Vast, out-of-stock), confirm (with the reuse/rerun chooser) and resume, all through `RunFlow`; `make ios-test` (158 tests), `make ios-build`, `make ios-contract` (adds `/tryon` and `/rent-panel`), `make ios-ui-test` (adds `Phase4SmokeTests`, zero-spend via `-UITestRecordingSpendGate`) and `scrub-secrets.sh --check` all pass | `make ios-refusal-smoke` passed live on 2026-09-23 (three bogus-token spends refused with 409, no lease before or after). No live Phase A / regen / confirm / resume has run — no pod has been rented for Phase 4 |
| 5 — pod and cost | Complete in code | Pod tab: lease card with a quoted cost, kill (fresh key per tap, outside `SpendGate`, followed to `last_kill`), the "pod may still be billing" banner (`destroy_unverified`/`error`, cleared only by acknowledgement or a later successful kill), GPU choice on the Pod tab and the rent panel (re-reads the panel), RunPod balance and on-tap Vast credit, the migration (ask → typed, expiring confirmation → `SpendIntent.migrate` through `SpendGate`), an unanswered migrate on every tab; `make ios-test` (211 tests), `ios-build`, `ios-contract` (adds `gpu/stock`, `balance`), `ios-ui-test` (adds `Phase5SmokeTests`) pass | With no pod leased (2026-09-23): `make ios-ui-test` ran `Phase5SmokeTests` in full and `make ios-refusal-smoke` passed all seven steps. A real Phase A → GPU change → confirm → kill ran once through the app's MotionKit code ($0.012, see §"Real spend test"). No real migration has run |
| 6 — batch/library | Complete in code | Cross build (Batch mode) with resumable re-planning and per-outfit seeds, the seed badge and its "no longer exists" variant, drop after Phase A with the re-validate and stale-panel clearing, the `!isDropping` guard on every `RunFlow` spend, the rental-retry generation latch, the batch progress list, saved try-ons (browse/use/delete) on the Material tab, and the server's read-only `tryon_seed` field. Gates: see "Gate record" below | `make ios-contract` ran live 2026-09-24 (14/14, including the new `GET /v1/tryon-library`), and `make ios-ui-test` passed live the same day (4/4 including `Phase6SmokeTests`, `skipped: 0`, zero recorded spends). **No real batch has run**: the smoke builds its two-outfit batch out of free draft mutations, never rents a pod, never calls Phase A and never reaches a confirm. A 2-outfit batch with one seeded job is still the proposed spend test |

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
  a build is in flight, and `lastAdded` counts the steps that run executed — not the size of the
  selection, which over-reports after a Continue. The run button hides once a build has landed,
  because success clears the selection and a disabled "Add 0 jobs to batch" is noise.
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
- Drop after Phase A (`RunFlow`): a basket of ≥ 2 entries offers "Drop from batch"
  (`tryon.drop.<index>`) per preview card, gated by `RunFlow.canDropFromBatch` — the authority for the
  condition; the one counter-intuitive fact is that it counts **basket entries** (`draft.batch.count`),
  not `draft.jobs`. Dropping re-validates the draft and re-reads the previews and rent panel. Every
  `RunFlow` spend is refused while a drop is in flight, because the guard sits in `RunFlow.spend` —
  the single funnel all four entry points go through — and not only in `canConfirm`.
  Run detail shows a per-job batch progress list. The server's draft view reports a read-only
  `tryon_seed` on the edited job and each basket entry. Since the Phase 6 follow-ups, `MigrateFlow`
  carries the same guard itself — it calls `gate.perform` directly and so never went through
  `RunFlow.spend` — with the drop check *ahead* of `canMigrate`, because the reverse order makes the
  choke point unreachable and the refusal silent. `recheck()` and `replayPendingOnce()` stay unguarded
  on purpose: they resolve an already-sent request.
- **A failed rental can only be retried against the draft that was confirmed — and the latch is the
  server's.** `resume` re-rents the manifest on disk and its gate **compares** only the draft's
  `generation`; nothing else in the draft feeds the decision. An accepted `confirm` stamps that
  generation to `batch/tg-<chat_id>.confirmed-generation.json` and `resume` refuses `409 stale_run`
  when the draft has moved since, so the guard survives an app relaunch. The stamp is read *after* the
  confirm's own `clear()`, because `clear()` increments the generation — reading before it would refuse
  every legitimate retry. Fails open when no stamp exists: a Telegram-initiated confirm writes none, and
  `_do_confirm` must not write one because the app draft is not what a Telegram user reviewed.
  **The in-memory latch Phase 6 added was removed** (2026-09-24): it held the *pre-clear* generation
  against the server's *post-clear* stamp, so a draft re-read between an accepted confirm and a Retry
  tap withheld a retry the server would have granted, with a message naming a recovery the cleared draft
  made impossible. `retryRentalBlockReason` and `confirmedGeneration` no longer exist; the refusal
  reaches the run detail card verbatim and the card stays up, because `applyRefusal` never mutates `pod`.
- **Invariant worth checking, not just stating: every control that writes the draft is gated on
  `composer.isRunning`.** A cross build takes minutes and each step is a `PATCH` whose server-side
  probe can take ~60 s, so a draft write from another tab landing between two steps is merged per role
  by the server and silently produces a job built from materials the build never chose. Verify with
  `grep -rn "composer.isRunning" ios/MotionApp/` — the Saved try-ons tile buttons ("Use in job" and
  Delete) are the ones that were missing it.

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
                            249 logic/contract tests in 23 suites
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

### Phase 6 (2026-09-24, PR #67, merged to `main` as `f65fc63`)

#### Gate record

Append-only: add a row when a gate runs, and do not restate its result in prose elsewhere in this file.
"Ran by" separates the implementer's worktree gates from the controller's live ones — a simulator or
fake-server result is not a live one.

| Gate | Result | Date | Ran by |
|---|---|---|---|
| `make ios-build` | exit 0. Compiles `MotionApp` only — the scheme builds `MotionAppUITests` for the `test` action, not `build` | 2026-09-24 | implementer (Task 10), re-run by the final fix wave |
| `xcodebuild -scheme MotionApp -destination 'generic/platform=iOS Simulator' build-for-testing` | exit 0; `Phase6SmokeTests.o` and `MotionAppUITests.xctest` produced. This is what proves the UI-test file compiles. No simulator booted | 2026-09-24 | implementer (Task 10), re-run by the final fix wave |
| `cd ios/MotionKit && swift test` | **249 tests in 23 suites**, exit 0, no new warnings | 2026-09-24 | final fix wave (244 before it) |
| `motions-studio/setup/scrub-secrets.sh --check` | exit 0 | 2026-09-24 | implementer (Task 10), re-run by the final fix wave |
| `make ios-contract` | **14/14 ok, exit 0**, live against the VPS, including the new `GET /v1/tryon-library` decoding `TryonLibraryResponse` | 2026-09-24 | controller |
| `make batch-test` | exit 0, `OK (skipped=1)`. Covers the branch's only `scripts/**` change — the one that auto-deploys a live bot on merge | 2026-09-24 | controller |
| `make ios-ui-test` | **exit 0** (checked explicitly, not inferred from a pipeline). Bundle `Test-MotionApp-2026.09.24_09-22-03-+0700.xcresult`, read with `xcrun xcresulttool get test-results summary`: `result: Passed`, `totalTestCount: 4`, `passed: 4`, `failed: 0`, **`skipped: 0`**. All four cases Passed (`… get test-results tests`): `Phase3SmokeTests.testDraftCompositionBatchDropAndValidation`, `Phase4SmokeTests.testRunFlowReachesRentPanelWithoutSpending`, `Phase5SmokeTests.testPodTabRendersAndMigrateStaysLocked`, `Phase6SmokeTests.testCrossBuildDropAndLibrary`. **Zero spends recorded** — the smoke launches with `-UITestRecordingSpendGate` and its closing `uitest.recordedSpends == "0"` assertion passed | 2026-09-24 | controller, live against the VPS on an already-booted iPhone 18 Pro simulator (`43B83B81-13CD-4383-BB43-4D8AEBEA6582`); `scripts/ios-ui-test.sh` `clear_draft` ran before and after, as it always does |

The `ios-contract` run is also the live evidence for the deploy-order property this handoff and the
spec both claim: the server had *not* been deployed with Phase 6's `tryon_seed` field, and
`GET /v1/draft` still decoded, because the app reads the field as optional. Either deploy order works.

#### Notes on the `make ios-ui-test` run (2026-09-24)

It must run outside the command sandbox (simulator control is killed inside it). The 2026-09-24 pass
settles two things this handoff previously reasoned about but had never observed.

**The Materials toolbar survived Task 7's `MaterialTabView` wrapper.** The smoke's `"Add material"`
assertion — `MaterialsView.addMenu`'s accessibility label, reached through the `.toolbar`
`ToolbarItem` that installs it — passed against a live run. That was the one assertion with no passing
precedent: it queries `"Add material"` type-agnostically, because a SwiftUI `Menu`'s XCUITest element
type is not guaranteed to be `.button`, so the risk was a false alarm while the toolbar still worked.
It did not fire, and the assertion is now precedent-backed like every other asserted string.
**Widen the query, do not delete the assertion** — that instruction stands for any future failure of
this check, but it is no longer needed *for* this assertion, which has passed live and so no longer
needs defending on suspicion.

**The two mode-picker `isEnabled` waits neither hung nor false-failed.** `Phase6SmokeTests` waits for
`newjob.mode`'s "Single" segment to report `isEnabled` before switching back to Single, twice — once
on the fewer-than-two-outfits `XCTSkip` path and once before the final `Phase4Draft.clear(in:)` —
because the mode `Picker` in `NewJobView` carries `.disabled(store.isBusy || composer.isRunning)` and
a tap on a disabled SwiftUI control is a silent no-op. A passing run proves those waits did not block
and did not fail, so they are **safe**. It does **not** prove they are load-bearing, and this file must
not say they are: if XCUITest does not propagate a SwiftUI `Picker`'s `.disabled` to its synthesized
segment buttons, each wait returns true immediately and is inert rather than harmful. Telling the two
apart needs a deliberately-disabled picker, which no gate builds. Whether they are effective is
**unsettled**.

Material preconditions for a re-run: the ≥2-image outfit count is guarded and skips, but the smoke also
needs ≥1 compatible image (Character) and ≥1 compatible **video** (Driver — `role_kind` in
`scripts/control/drafts.py` maps `driver` to `video`, per its `VIDEO_ROLES` set); a missing Character
or Driver material hard-fails inside `Phase4Draft.chooseMaterial` ("A compatible material must exist
for Driver") rather than skipping. That is an environment gap (restore test material), not a code
regression — Phase 3/4 pass live against the same three roles, so the library normally has them. The
empty-draft precondition skips for three distinct causes and names the literals it expected, so a
network blip does not read as a non-empty draft.

**No real batch has run, and this gate is not one.** `Phase6SmokeTests` builds its two-outfit batch out
of *free* draft mutations (`PATCH` + add-to-batch — no `SpendGate`, no Idempotency-Key), then drops an
entry and clears; it never rents a pod, never calls Phase A and never reaches a confirm, which is why
it can assert zero recorded spends. A passing `make ios-ui-test` is therefore not batch evidence. A
2-outfit batch with one seeded job — proving Phase A calls the provider once for the unseeded outfit
and reuses the saved image for the seeded one — remains the proposed spend test; it needs its own
approval and a quoted price, and its cost comes from the balance delta and then `runpodctl billing`.

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
- Phase 6 is complete in code on `feat/swiftui-phase-6`, and every gate in its record has now run:
  `make ios-contract` (14/14), `make batch-test` and `make ios-ui-test` (`Phase6SmokeTests`, 4/4,
  zero recorded spends), all live on 2026-09-24. See the gate record above. A re-run of
  `make ios-ui-test` boots a simulator, so it must run outside the command sandbox. **No real batch
  has run, and the smoke is not one** — it builds its two-outfit batch from free draft mutations and
  never rents a pod. A 2-outfit batch with one seeded job is still the proposed spend test and needs
  its own approval and quoted price.
- Unsettled by that pass (not a bug, and not proven either way): whether XCUITest propagates a SwiftUI
  `Picker`'s `.disabled` to its synthesized segment buttons. `Phase6SmokeTests`'s two `newjob.mode`
  `isEnabled` waits passed, which proves they are safe but not load-bearing — if the property does not
  propagate, each wait returns true immediately and is inert. Observing the difference needs a
  deliberately-disabled picker. See §"Notes on the `make ios-ui-test` run".
- A draft whose basket has fewer than 2 entries has no drop affordance by design — the condition is
  `RunFlow.canDropFromBatch`, and the counter-intuitive part is that it counts **basket entries**
  (`draft.batch.count`), not `draft.jobs`. So a run of 2 jobs built from 1 basket entry plus a
  complete edited job (`draft.jobs == 2`, `batch.count == 1`) shows no Drop at all. **Clear**, on the
  New Job tab in either mode, is the only way to remove the last basket entry.
- Closed 2026-09-24 (PR #68): Batch mode has a `Clear`. It was extracted from `editorActions` into one
  shared `clearAction` rendered in both arms, so the two cannot drift, and `Phase6SmokeTests` no longer
  switches `newjob.mode` → Single before clearing. `readiness` stays Single-only on purpose: it reports
  the *edited job*'s required and missing roles, and the Batch arm does not render the edited job — it
  renders shared slots plus an outfit multi-select, so a "2 of 3 assigned" line there would describe a
  job the user is not looking at. Batch mode's own readiness is `BatchComposer.canRun`, which the run
  button's disabled state already shows. The papercut was real, and the evidence that it was real is the
  two mode switches the smoke used to need; the smoke now clears from Batch directly and asserts the
  header's `0 jobs` plus the `Character` slot returning to `Missing required`, because `Clear` on the
  skip path is the only thing that proves the draft was left empty.
- Closed 2026-09-24 (PR #68): `Fixtures.pipelines` names the optional role `background`, as the catalog
  does. The recorded blocker did not fire — there are **nine** `.replacingOccurrences` call sites across
  the test directory (not the six this entry implied, and not the three files it names), and none of
  their anchors contains `mask`. The ninth is the one that mattered: `RunFlowTests.swift:59-60` builds
  `draftAfterDelete` from `Routes.draftJSON`'s output, which is the function the rename edited, so a
  dropped `"validated":true` would have made `draftAfterDelete` silently equal `draftAfterDrop`. The
  role's *kind* stays the unrecognised `"future_kind"` on purpose — `ModelsTests`' unknown-kind assertion
  is about the kind, not the name — even though the live catalog reports `"image"` for it.
- Closed 2026-09-24 (PR #68), and **client-side, so it needed no deploy gate** — the entry above assumed
  a `scripts/**` change and that assumption was wrong. `APIError.userMessage` now headlines `422 invalid`
  and a new `detailMessage` keeps the server's raw text for a collapsed `DisclosureGroup` in
  `ErrorBanner`, `nil` when the text is blank. The server's copy is untouched, which is the point: it is
  right for Telegram, where the reader has the validator's output above it. This also fixed a fourth
  variant the entry never listed — `bot.py:6051` returns `Outcome(False, "invalid", "")`, an **empty**
  message, because `_render_and_validate` already sent the real reason to Telegram. That rendered a red
  banner with no text in it. `RunFlow.drop()`'s catch shows the headline with no disclosure, correctly:
  it assigns `apiError(error).userMessage` to a `String`, so the `APIError` is discarded at the
  assignment.
- Closed on this branch (money safety): `isDropping` used to gate only Confirm. The guard now sits in
  `RunFlow.spend(_:label:)` — the single funnel `confirm`, `regenerate`, `retryRental` and `choose(_:)`
  all go through — so all four are refused while a drop is in flight, and any spend entry point added
  later inherits it. `canConfirm`'s own `!isDropping` term stays as the button-level guard.
  The earlier entry here argued this was out of reach because widening `canSpend` reaches
  `canDropFromBatch` and the pending-notice machinery. That reasoning is correct about `canSpend` and
  irrelevant to `spend`: `canDropFromBatch` is evaluated *before* `isDropping` is set, with no `await`
  between, so the guard cannot deadlock the drop; and `recheck()` / `replayPendingOnce()` call
  `gate.recheck` / `gate.replayPending` directly and never pass through `spend`, so the pending-notice
  machinery is untouched. Both were re-verified in the code, not taken on trust.
  **The residual named here is closed** (2026-09-24, PR #68): `MigrateFlow` now takes a `RunFlow` and
  guards `!runFlow.isDropping` at both levels. The exposure turned out to be the same shape after all —
  a drop's two 95 s client timeouts are a window the most destructive call in the API could be launched
  inside.
- Closed 2026-09-24 (PR #68) — **but not the way this entry proposed, and the difference matters.** The
  proposal above was to give `resume` an optional client-sent `generation`. That approach was designed,
  written up and **rejected**: the client's number is exactly what an app relaunch loses, so sending it
  would rebuild the bug instead of fixing it, and it would leave every already-installed build
  unprotected. What shipped is the opposite — the *server* persists the generation when a confirm is
  accepted and reads the draft's current value itself, so the client sends nothing and no app change is
  needed for correctness. Do not implement the version described above. Full reasoning, the two rejected
  approaches and the recorded limits are in
  `docs/superpowers/specs/2026-09-24-phase-6-follow-ups-design.md` §3 and §10.
- Follow-up (a latent server-side coupling Phase 6 is the first to depend on): `tryon_save_info`
  hardcodes `f"app/{path.name}"` (`scripts/tgbot/bot.py:7204`), while `scripts/control/materials.py:224`
  builds `f"{owner}/{path.name}"` and `_material_id` returns `"/".join(rel.parts)`
  (`scripts/control/drafts.py:283-288`). All three agree today because `APP_OWNER = "app"`
  (`scripts/control/materials.py:199`), and
  `TryonLibraryStore.matches(slots:)` is dictionary equality over exactly those strings — so if
  `owner` ever became configurable, every library match would silently return `[]` and no outfit would
  ever get a default seed. Nothing would error. Recorded, not changed: fixing it is a `scripts/**`
  edit and this branch is allowed exactly one.
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
  proves only that bogus-token spends are refused before anything is called. The 2026-09-24
  `make ios-ui-test` pass (4/4) does not change this: `Phase6SmokeTests` runs under
  `-UITestRecordingSpendGate` and asserts zero recorded spends, so it is the fourth zero-spend
  simulator smoke, not a batch run.
- `ios/Secrets.xcconfig`, personal media, screenshots with personal names, upload checkpoints and live
  API payloads must remain uncommitted.

## Next work

No phase remains — Phases 1–6 are implemented in code. Open items, none of them a phase:

1. ~~**Merge**~~ — **done, twice over.** Phase 6 merged as PR #67 (`f65fc63`, deploy run
   `35951269293`); the Phase 6 follow-ups merged as PR #68 (`f0b7fa7`, deploy run `36029721301`,
   2026-09-24). Both live gates ran for each, and every gate row in both specs' records is filled.
   `main` and `origin/main` are in sync. Nothing here is outstanding; the entry is kept only so the
   history of what was waiting does not look like it was skipped.
2. **Optional spend test.** A 2-outfit batch with one seeded job, proving Phase A calls the provider
   once for the unseeded outfit and reuses the saved image for the seeded one. Needs its own approval
   and a quoted price; cost from the balance delta then `runpodctl billing`; `make gpu-destroy` after.
3. **Parent spec's deferred items** (`2026-09-22-swiftui-app-design.md` §1): pair (1:1) mode was
   replaced by the multi-driver batch (see §"Multi-driver batch", 2026-09-25). Stock-watch
   notifications are still open. The Telegram bot already has a one-shot `/subscribe`
   (`bot.py` `_tick_gpu_subs`). Free provisioning rules out push notifications, so the app side would
   be routes to manage subscriptions, with delivery still going through Telegram. It needs its own
   spec.
4. **Phase 6 follow-ups**: four of the five closed 2026-09-24 by PR #68 — the server-side resume
   latch (the money one), the `MigrateFlow.migrate()` `isDropping` residual, the developer-facing
   validation copy on the phone, and the `Fixtures.pipelines` `mask` vs `background` mismatch. The
   fifth pair — the `app/` owner coupling and the `not_found` collision between a stale material and a
   deleted library entry — closed 2026-09-25 by PR #69 (`seed_not_found`, 404; spec
   `2026-09-25-seed-not-found-and-owner-coupling-design.md`). Three of its small leftovers closed
   2026-09-25 on branch `ios-small-followups`: `TryonLibraryStore.use` now `forget`s an entry that
   answered `seed_not_found`, as `delete`'s 404 already did; `MigrateSheet` says why the confirm is
   off while a batch drop is in flight (`MigrateFlow.dropBlocked`); and `RunDetailView`'s redundant
   inner `canRetryRental` is gone. Gates for that branch: `swift test` 275/275, `make ios-build`,
   `make ios-contract` 14/14, `scrub-secrets.sh --check` exit 0, and `make ios-ui-test` 5/5 with 0
   skipped on the second run. The first run failed `Phase5SmokeTests` at "ask returned a
   confirmation": the VPS log shows `POST /v1/pod/migrate/ask` answered 409 at 01:17:03 UTC with no
   lease, drain or Phase A live, then 200 at 01:26:05 on the rerun. The likeliest cause is the stock
   cache no longer listing the tapped datacenter (`unknown_datacenter`), but that is unconfirmed —
   the sheet's message sat below the fold, so the smoke could not show it. Still open: the Swift
   post-confirm fixture having no cross-language pin on `clear()`'s shape, and the stamp write's
   fail-open race. The five uncovered `batch/` state paths were closed the same day (`ac9ab3a`), after a
   post-deploy `git status` on the VPS showed two of them sitting untracked with a Telegram chat id in
   the filename.

Required safety behavior carries over unchanged from Phase 4: one UUID key per tap and never mint a new
one to retry an earlier spend intent; disable the active button and show its quote while in flight;
persist the ledger entry before sending and replay it once, under 20 h, on launch; zero-spend gates
(`ios-test`, `ios-build`, `ios-contract`, `ios-ui-test`, `scrub-secrets.sh --check`) before every commit,
with any live call needing its own separate approval and quoted cost. Before any later push touching
`scripts/**`, check the VPS for a drain, Phase A, pod lease and migration — pure `ios/**` changes do not
auto-deploy the bot.

## Recommended kickoff for the next effort

No phase remains (see "Next work") and no Phase 6 gate is outstanding, so this is the start of
whatever comes next — the optional spend test, a deferred parent-spec item, or a Phase 6 follow-up.

1. Verify `git status`, the branch and the current SHA; preserve unrelated changes. Phase 6 is merged:
   `main` is `f65fc63` (PR #67) and in sync with `origin/main`.
2. Read `AGENTS.md`, this handoff, the parent SwiftUI design and the Phase 6 design. For anything
   touching the money layer, inspect the shipped `RunFlow`/`SpendGate`/`MigrateFlow`/`PodStore` stores
   and tests for state and error-handling conventions first.
3. Before any push that touches `scripts/**` — Phase 6's read-only `tryon_seed` field is one — check the
   VPS for a drain, Phase A, pod lease and migration; the merge auto-deploys `motion-bot`. Pure `ios/**`
   changes do not.
4. Keep views free of direct `APIClient` calls; execute task-by-task TDD.
5. Report live validation separately from mocked/simulator validation; never claim a spend path was
   tested unless it actually ran with prior user authorization and a quoted cost.

