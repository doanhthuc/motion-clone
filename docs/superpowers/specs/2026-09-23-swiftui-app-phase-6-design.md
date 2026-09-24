# Motion iPhone app — Phase 6 batch and library design

Date: 2026-09-23 · Status: implemented and merged — PR #67, on `main` as `f65fc63` (squashed on request; the branch first landed as merge commit `8518e13`, and `git diff f65fc63 8518e13` is empty, so only the history shape changed). §3's field is deployed to the VPS; see the gate record for what ran and when; no real batch has run

### Gate record

Append-only: add a row, do not restate a result in prose elsewhere. "Ran by" distinguishes the
implementer's worktree gates from the controller's live ones.

| Gate | Result | Date | Ran by |
|---|---|---|---|
| `make ios-build` | exit 0 (compiles `MotionApp` only) | 2026-09-24 | implementer, then the final fix wave |
| `xcodebuild … build-for-testing` | exit 0, `Phase6SmokeTests.o` produced, no simulator booted | 2026-09-24 | implementer, then the final fix wave |
| `cd ios/MotionKit && swift test` | 249 tests in 23 suites, exit 0 | 2026-09-24 | final fix wave (244 before it) |
| `motions-studio/setup/scrub-secrets.sh --check` | exit 0 | 2026-09-24 | implementer, then the final fix wave |
| `make ios-contract` | **14/14 ok, exit 0**, live against the VPS | 2026-09-24 | controller |
| `make batch-test` | exit 0, `OK (skipped=1)` | 2026-09-24 | controller |
| `make ios-ui-test` | **exit 0**, `result: Passed`, `totalTestCount: 4`, `passed: 4`, `failed: 0`, **`skipped: 0`** — §8's `Phase6SmokeTests.testCrossBuildDropAndLibrary` Passed, so neither of its two `XCTSkip` guards (non-empty draft, fewer than two outfit images) fired; the Phase 3, 4 and 5 smokes Passed alongside it. Zero spends recorded (`-UITestRecordingSpendGate`; the closing `uitest.recordedSpends == "0"` assertion passed). Bundle `Test-MotionApp-2026.09.24_09-22-03-+0700.xcresult`, read with `xcrun xcresulttool get test-results summary` / `… tests` | 2026-09-24 | controller, live against the VPS on an already-booted iPhone 18 Pro simulator (`43B83B81-13CD-4383-BB43-4D8AEBEA6582`) |
| `make ios-contract` (post-deploy) | **14/14 ok, exit 0**, live against the VPS after PR #67 deployed. `GET /v1/draft` now carries `tryon_seed` (`null` on an empty draft) and leaks no absolute path; the VPS is on `f65fc63` with `motion-bot` active since 03:23:59 UTC | 2026-09-24 | controller |

The **pre-deploy** `ios-contract` run is also the live proof of the deploy-order property §3 claims but
could not evidence until then: the server had *not* been deployed with §3's `tryon_seed` field, and
`GET /v1/draft` still decoded, because the app reads the field as optional. The same run covers the
14th route, `GET /v1/tryon-library` decoding `TryonLibraryResponse`.

The `ios-ui-test` run settles two properties this spec reasoned about but could not observe:

- **§6's Material tab wrapper did not break toolbar propagation.** The `"Add material"` assertion
  (`MaterialsView.addMenu`'s accessibility label, installed by its `.toolbar` `ToolbarItem`) passed
  against a live run. It was the one assertion with no passing precedent — it queries the label
  type-agnostically because a SwiftUI `Menu`'s XCUITest element type is not guaranteed to be `.button`,
  so the feared failure was a false alarm rather than a real break. It did not fire. The standing
  instruction, **widen the query rather than delete the assertion**, stays for any future failure, but
  is no longer needed for this assertion, which is now precedent-backed.
- **The two mode-picker `isEnabled` waits are safe, and their effectiveness is still unsettled.** The
  smoke waits for `newjob.mode`'s "Single" segment to be `isEnabled` before switching back to Single,
  on both the outfit-count `XCTSkip` path and the final clear, because §4's mode `Picker` is
  `.disabled(store.isBusy || composer.isRunning)` and a tap on a disabled SwiftUI control is a silent
  no-op. A passing run proves those waits neither hung nor false-failed. It does **not** prove XCUITest
  propagates a SwiftUI `Picker`'s `.disabled` to its synthesized segment buttons — if it does not, each
  wait returns true immediately and is inert rather than harmful. Do not record them as proven
  effective; observing the difference needs a deliberately-disabled picker.

No real batch has run, and `ios-ui-test` is not one: the smoke builds its two-outfit batch out of
*free* draft mutations (`PATCH` + add-to-batch, no `SpendGate`, no Idempotency-Key), drops an entry and
clears — it never rents a pod, never calls Phase A and never reaches a confirm. A 2-outfit batch with
one seeded job is the proposed spend test (§8) and needs its own approval and quoted price.

This spec refines Phase 6 of `docs/superpowers/specs/2026-09-22-swiftui-app-design.md` (§3 "New
batch, Bulk try-on", "Saved try-ons", §5 row 6). Phases 1–5 are shipped
(`docs/superpowers/swiftui-app-progress.md`). It consumes the live control-plane API
(`2026-09-21-vps-control-plane-api-design.md` §5.2, §5.3, §5.10) and adds **one read-only field** to
the draft view; no new route.

## 1. Scope and success criteria

Part of the parent design's row 6 already shipped in earlier phases and is **not** rebuilt here:
guided regenerate, try-on versions and Keep (Phase 4, `TryonPreviewCard`), several try-on previews
in the run flow (Phase 4), per-job timelines in run detail (Phase 1), and
`DraftStore.dropFromBatch(_:)` (Phase 3).

Phase 6 is complete when the installed app can:

- **cross build** from the New job tab's **Batch** mode: one character, one driver (and background
  when the pipeline has one) × up to 12 outfits → one basket job per outfit, resumable after a
  partial failure and never adding a duplicate;
- **seed** a job from a saved try-on — automatically offered per outfit in a cross build, and from
  the library screen — so Phase A skips the provider call for that job;
- **drop** one try-on from a batch after Phase A, before renting, so confirm rents only what is
  left;
- show **batch progress** as a compact per-job list with a summary, each row expanding to the
  existing timeline;
- browse, use and delete **saved try-ons** from the Material tab; and
- pass every free gate, without renting a pod or calling a try-on provider.

Out of Phase 6: pair (1:1) mode, editing a saved entry's material ids, a size cap or expiry on the
library, stock-watch notifications, and any live spend (a real batch run is a separate, separately
approved step — §8).

## 2. What the live server does (read 2026-09-23)

- `POST /v1/draft/add-to-batch` appends a **copy** of the job being edited and leaves the edited job
  in place — the same slots and the same `tryon_seed`. Consequences:
  - each cross-build step must send `tryon_seed` explicitly (id or `null`) with the outfit, or the
    next outfit inherits the previous one's seed;
  - `jobs_for` counts the edited job as one more job when it is complete and not an exact
    `signature()` copy of a basket entry. Right after a cross build it *is* a copy of the last
    outfit, so N outfits give N jobs. But dropping that last outfit from the basket makes the edited
    copy unique again and it silently **comes back** into Run. The app therefore leaves the edited
    job incomplete (§4, §5).
- `add-to-batch` refuses an exact copy with `422 duplicate`.
- `DELETE /v1/draft/batch/{digest}` is free; `404 not_found` when the digest is gone.
- `PATCH /v1/draft {tryon_seed}` only attaches the image; it does not fill slots, and it does not
  check the entry's material ids against the job's slots. `422 not_local` when the job's try-on
  does not run locally; `404 not_found` for an unknown id.
- `GET /v1/draft` does **not** report `tryon_seed` today, for the edited job or for basket entries.
- `GET /v1/runs/{id}/tryon` previews carry `run` (the manifest run id), not a digest. `GET
  /v1/draft`'s `batch[].run_id` is the id the manifest gives that entry (`_unique_ids`), so
  `preview.run == entry.runID` maps a preview to its digest.
- Confirm after Phase A compares the draft against the Phase A manifest
  (`_phase_a_matches_draft`, `scripts/tgbot/bot.py`). When a job was dropped, it falls through to
  `_do_confirm` with the current draft, which may answer `choice_required` (the Phase 4 reuse/rerun
  chooser). Kept try-ons are journalled `done` under content-hashed run ids, so *reuse* calls no
  provider again.
- Library entries: `{id, material_ids: {character, outfit, background?}, provider, saved_at}`; the
  image is stored as `batch/tryon-library/app/{id}{ext}`.

## 3. Server change (the only one)

`scripts/control/drafts.py` `_view` adds `"tryon_seed": <library id | null>` to the top level (the
edited job) and to each `batch[]` entry. The id is the seed path's stem (`{id}{ext}` → `{id}`);
a seed whose entry was deleted since still reports its id — the app shows "saved try-on no longer
exists". Read-only: no behaviour changes. One unit test in `scripts/tests/` covers both places;
`make batch-test` passes. Pushing it auto-deploys `motion-bot`, so the VPS is checked first for a
drain, Phase A, pod lease and migration. The app decodes the field as optional, so it works against
the server before and after the deploy.

**Known collision this does not fix.** `drafts.py` raises `DraftError("not_found", …)` for two
different causes — `no such material: {id}` from `_resolve`, and `no such try-on library entry: {id}`
from `patch` — so both reach the app as `404 not_found` with no way to discriminate. The app treats a
404 during a slot-carrying `PATCH /v1/draft` as "a material went stale, reload materials"
(`DraftStore.needsMaterialsRefresh`), and every cross build sends `tryon_seed` *together with*
`slots.outfit`, so a seed whose library entry was deleted since surfaces as a spurious
materials-refresh nudge. Nothing is corrupted: the server writes nothing before raising, the
authoritative draft is re-fetched, and the correct server message still reaches `store.message`. The
real fix is a distinct server code (`seed_not_found`), deliberately not taken here — this branch is
allowed exactly one `scripts/**` change and it had to be the seed reporting above. Recorded so
`DraftStore.apply`'s comment reads as a known limit, not an oversight.

## 4. New batch (cross build)

**UI.** The `+` tab gains a segmented control **Single | Batch** at the top; Single is today's
screen, unchanged. Batch:

- pipeline picker (the existing `PipelinePicker`) filtered to pipelines whose `required ∪ optional`
  roles contain both `character` and `outfit` — read from the catalog, never hardcoded; an empty
  state with the reason when none do;
- shared slots: character, driver, background when the pipeline has it (existing `MaterialPicker`);
- an outfit multi-select, capped at **12** per build. The cap only guards against a mistap: Phase A
  runs one try-on per outfit and the rent estimate grows linearly with N; the server has no limit;
- one row per chosen outfit: thumbnail and a **"Use saved try-on"** toggle, on by default when the
  library has a match (§6), tappable to pick another match;
- **"Add N jobs to batch"**, disabled while running;
- the existing basket list below, each row with drop and a seed badge.

**`BatchComposer`** (`@Observable @MainActor`, in MotionKit, uses `DraftStore`):

- `plan(...) -> [CrossStep]`, pure: one step per outfit, skipping outfits already in the basket
  (same slots, provider and `tryonSeed` as a `DraftBatchEntry`).
- `run()` executes steps **sequentially**: one `PATCH {slots: shared + outfit, tryon_seed: id|null}`,
  then `add-to-batch`. After the last step, one `PATCH {slots: {outfit: null}}` leaves the edited
  job incomplete (§2).
- The first error stops the run with `progress = k/N` and that outfit's error. **Continue** re-reads
  the draft and re-plans, so outfits already added are skipped. `422 duplicate` counts as done.
- Free: no `SpendGate`, no Idempotency-Key. The button is disabled while `run()` is in flight.

## 5. Bulk try-on — drop after Phase A

On each `TryonPreviewCard` in `RunFlowView`:

- **"Drop from batch"** appears when the **basket has ≥ 2 entries** (`draft.batch.count`, *not*
  `draft.jobs`) and Phase A is not running — gated by `RunFlow.canDropFromBatch`, which is the
  authority for the full condition. Hidden for the last remaining entry (Clear covers that), so a run
  of 2 jobs built from 1 basket entry plus a complete edited job shows no Drop at all.
- The digest comes from `preview.run == DraftBatchEntry.runID` in a fresh `GET /v1/draft`. No match
  → the button is disabled with "Draft changed — reload".
- Disabled while an outstanding spend is unanswered (`flow.canSpend == false`), so the draft never
  changes under an in-flight confirm.
- Tap → confirmation → if the edited job equals the dropped entry (same slots, provider, seed),
  first `PATCH` that job's `outfit` slot to `null` (or, for a pipeline without `outfit`, its first
  required role in sorted order), so it cannot come back (§2) → `DELETE /v1/draft/batch/{digest}` →
  `POST /v1/draft/validate` (every draft change resets `validated`, and confirm refuses
  `not_validated`; validate is free, up to ~90 s) → re-read the draft, previews and rent panel.
  An invalid or stale validation leaves the server's message on screen and confirm disabled.
- Confirm then follows the Phase 4 path; on `choice_required` the existing chooser is shown with
  "Choose *reuse*: the remaining try-ons do not call Gemini/Qwen again."
- A seeded job's card shows a "Saved try-on" badge. Regenerate still works on it and calls the
  provider as usual.

**Batch progress** (`RunDetailView`, ≥ 2 jobs): a summary (done/total, running, failed) and one
collapsed row per job — status dot, run id, elapsed — that expands to the existing timeline;
failed and running jobs sort first. Kill is unchanged and labelled "Kill stops every remaining job".

## 6. Saved try-ons

**UI.** The Material tab gains a segmented control **Materials | Saved try-ons**. The library is a
grid from `GET /v1/tryon-library` with images from `…/{id}/image` (cached by id; an entry's image
never changes). Each tile: provider, saved date, the character and outfit names resolved from the
materials list ("(deleted)" when gone).

- **Use in job** → one `PATCH {slots: material_ids, tryon_seed: id}` (pipeline and driver unchanged)
  → switch to the `+` tab in Single mode. `422 not_local` / `wrong_kind` / `unknown_role` show the
  server's message.
- **Delete** → confirmation (warning "Job X uses this image" when the draft or basket references
  it) → `DELETE /v1/tryon-library/{id}`.

**`TryonLibraryStore`** (`@Observable @MainActor`): `load()`, `image(id)`, `delete(id)`,
`use(entry)` (through `DraftStore`), `matches(character:outfit:background:) -> [TryonLibraryEntry]`
(newest first; background must be equal, including both absent), and the usual loading / error /
stale state.

**Models.** `Draft.tryonSeed: String?`, `DraftBatchEntry.tryonSeed: String?` (optional);
`TryonLibraryEntry {id, materialIDs: [String: String], provider, savedAt: Date}` (the existing
`TryonLibraryRecord` is reused or folded in); the draft patch encodes `tryon_seed` as absent,
explicit `null`, or an id.

**Known limitation (Task 7, recorded in code).** The server merges a `PATCH /v1/draft` per role
(`scripts/control/drafts.py:450-455`): it sets the roles the patch names and pops only a role sent as
explicit `null`, leaving every other slot untouched. `TryonLibraryStore.use` sends the entry's
`materialIDs` plus the seed, so when an entry lacks a non-driver role the draft already has — in
practice `background` — that role stays on the draft while the seed points at an image made without
it. The job is still valid; the reuse simply does not carry the leftover role into the saved image,
and `matches(slots:)` (exact equality over every non-driver role) will not offer that entry for the
mismatched pair. The optional role is `background`, not `mask`: `mask` is not a server role anywhere
in `scripts/**` (the server's pipelines use `background`, `scripts/batchlib/pipelines.py:54,81`),
which corrects the plan's ruling text.

## 7. App wiring

`AppModel` owns `BatchComposer` and `TryonLibraryStore` next to the existing stores and rebuilds
them on `reconnect()`. Views never call `APIClient` directly. `motion-contract` adds
`GET /v1/tryon-library` (14 routes).

## 8. Testing and acceptance

**Free gates (all must pass):**

- `make ios-test`: cross build of 3 outfits → exactly 3 × (`PATCH` with an explicit `tryon_seed` +
  `add-to-batch`) then the outfit-clearing `PATCH`; failure at step 2 stops at 1/3 and Continue
  skips the added outfit; `422 duplicate` counts as done; preview → digest mapping including the
  unmatched case; drop clears the edited job first when it equals the entry; library list, delete,
  use, and `matches` with and without background; `Draft.tryonSeed` decodes when absent.
- `make batch-test`: the `_view` test (§3).
- `make ios-build`, `make ios-contract` (14/14).
- `make ios-ui-test` adds `Phase6SmokeTests` against the live server, using only reads and free
  draft mutations: Batch mode builds 2 outfits → the basket shows 2 jobs → drop one → open Saved
  try-ons → Clear. It skips when the draft is not empty or there is not 1 character and 2 outfits
  among the materials.
- `motions-studio/setup/scrub-secrets.sh --check` before every commit.

**Spend:** Phase 6 does not require one. A real run — a 2-outfit batch with one seeded job,
proving Phase A calls the provider once — needs its own approval with a quoted price, and its cost
comes from the balance delta and then `runpodctl billing`.

**Order:** server `_view` (push after the VPS check) → models → `TryonLibraryStore` →
`BatchComposer` → views → UI test → docs.
