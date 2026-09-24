# Multi-driver batch and shared try-on: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One character × N outfits × M drivers in the app's Batch mode, with Phase A making one try-on per look instead of one per job.

**Architecture:** The runner groups Phase A runs by a computed share key (character, outfit, background, try-on params, plus the driver only when the stage is camera-aware). Leaders call the provider. Followers copy the leader's image, and a SHA-256 check keeps them in sync after a regenerate. The bot and API expose the grouping as two additive preview fields. The app composes outfits × drivers from existing draft routes and renders one preview card per group.

**Tech Stack:** Python 3 stdlib (`unittest`, `hashlib`, `shutil`) in `scripts/`; Swift 6 / SwiftUI, `swift-testing` in `ios/MotionKit`, XCUITest in `ios/MotionAppUITests`.

**Spec:** `docs/superpowers/specs/2026-09-25-multi-driver-batch-shared-tryon-design.md`. Read it before any task. Section numbers below (§N) refer to it.

## Global Constraints

- Write in English: code comments, docs and commit messages. Do not add `# #region ALD` markers.
- Comments explain **why**, in the style of the surrounding code (see the long docstrings in `runner.py`).
- A follower never calls the provider (§2).
- No new HTTP route. Preview fields are additive only: `shared_from`, `shares` (§3).
- Batch cap: **12 jobs total**, `outfits.count × max(drivers.count, 1)` (§4).
- Accessibility identifiers: `batch.pickDrivers`, `driver.pick.<id>`; keep all existing identifiers (§4).
- `motions-studio/setup/scrub-secrets.sh --check` must exit 0 before every commit.
- Python tests: `python3 -m unittest discover -s scripts/tests -p '<file>'`. Swift: `cd ios/MotionKit && swift test --filter <Suite>`.
- Every commit ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Mixed batch.** Some outfits are seeded and some are not, over 2 drivers. Seeded runs must never be grouped (key `None`), and unseeded ones share. Pinned in Task 1 (`test_seeded_runs_are_never_grouped`) and Task 2 (`test_mixed_seeded_and_unseeded`).
2. **Same outfit, different background.** These must not share an image, because background is part of the key. Pinned in Task 1 (`test_background_splits_a_group`).
3. **Resume after the leader's file was deleted** (batch-clean). The leader re-runs once, and the followers recopy by SHA. Pinned in Task 2 (`test_leader_file_deleted_reruns_once_and_recopies`).
4. **Dropping the only group** is impossible, because the last basket entries must remain. Dropping a group whose size equals the basket size must be refused. Pinned in Task 5 (`dropOfAGroupThatIsTheWholeBasketIsNotOffered`).
5. **Toggling a driver that would push the job count over 12** is refused with a visible reason, and the selection stays unchanged. Pinned in Task 6 (`aDriverThatWouldExceedTwelveJobsIsRefusedWithAReason`).

---

## File map

| File | Responsibility | Tasks |
|---|---|---|
| `scripts/batchlib/runner.py` | share key and groups, two-pass Phase A, follower reuse, `preserved_local_tryon` | 1, 2 |
| `scripts/tests/test_batch_runner.py` | runner tests (new class `ShareTryonTests`) | 1, 2 |
| `scripts/tgbot/bot.py` | `AppRuns.tryon` fields, `_deliver_tryon_previews`, `_regen_tryon`, `_retry_tryon`, `_report_failed_tryons` | 3, 4 |
| `scripts/tests/test_batch_bot.py`, `scripts/tests/test_batch_control_botruns.py` | bot and API tests | 3, 4 |
| `ios/MotionKit/Sources/MotionKit/Models/RunFlow.swift` | `TryonPreview.sharedFrom/shares` | 5 |
| `ios/MotionKit/Sources/MotionKit/Stores/RunFlowStore.swift` | leader cards, group drop | 5 |
| `ios/MotionApp/RunFlow/TryonPreviewCard.swift` (+ the view that lists cards) | "used by K videos", leader-only cards | 5 |
| `ios/MotionKit/Sources/MotionKit/Stores/BatchComposer.swift` | drivers dimension, `maxJobs`, try-on count | 6 |
| `ios/MotionApp/NewJob/BatchComposerSection.swift` | driver multi-select, summary line | 7 |
| `ios/MotionAppUITests/…Phase6SmokeTests.swift` (or new `MultiDriverSmokeTests`) | zero-spend 2×2 smoke | 7 |
| `docs/superpowers/swiftui-app-progress.md`, spec gate record | handoff | 8 |

---

### Task 1: Share key and groups (pure functions)

**Files:**
- Modify: `scripts/batchlib/runner.py` (add after `_local_tryon_stage`, around line 405)
- Test: `scripts/tests/test_batch_runner.py` (new class `ShareTryonKeyTests`)

**Interfaces:**
- Produces: `tryon_share_key(run: Run, stage_name: str) -> tuple | None`, and `tryon_share_groups(manifest: Manifest) -> dict[str, str]` (run id → leader run id, leader maps to itself; only runs whose `_local_tryon_stage` is not None).

- [ ] **Step 1: Write the failing tests.** Build manifests with the existing `_fixture_tryon` helper pattern in `test_batch_runner.py`, or write the YAML inline. Read how `MANIFEST_TRYON_GEMINI` is shaped and write a manifest with several runs over one character and two outfits × two drivers, provider `gemini`. Tests:
  - `test_same_look_different_driver_shares_a_key`: two runs with the same character, outfit and params and different drivers give an equal key. `tryon_share_groups` maps both to the first run's id.
  - `test_camera_aware_stage_keys_on_the_driver`: with `mock.patch.dict(PIPELINES, {...: ["camera-tryon"]})` (as at line 139), two drivers give different keys.
  - `test_background_splits_a_group`: the same outfit with different backgrounds gives different keys.
  - `test_params_split_a_group`: a different `provider` or `garment_type` gives different keys.
  - `test_seeded_runs_are_never_grouped`: a run whose params carry `seedImage` returns `None` and maps to itself.
  - `test_non_local_runs_are_absent`: a run with a non-local provider is not in the dict.

- [ ] **Step 2: Run them and confirm they fail** with `ImportError`/`AttributeError`.
  `python3 -m unittest discover -s scripts/tests -p 'test_batch_runner.py' -k Share`

- [ ] **Step 3: Implement.**

```python
_TRUTHY = ("1", "true", "yes", "on")


def tryon_share_key(run: Run, stage_name: str) -> tuple | None:
    """What makes two runs' Phase A try-ons the same image (spec §1).

    The driver is an input only for a camera-aware stage (local_tryon.py reads
    it for the guide frame and nowhere else), so an ordinary try-on of one
    look is identical across drivers — calling the provider once per driver
    paid N×M for N images, and the M copies of one outfit came out visibly
    different. A seeded run is never grouped: it copies its own saved image
    and calls nothing.
    """
    params = effective_stage_params(stage_name, run.stage_params.get(stage_name))
    if params.get("seedImage"):
        return None
    camera_aware = str(params.get("cameraAware") or "").lower().strip() in _TRUTHY
    inputs = run.inputs

    def _p(role: str) -> str | None:
        path = inputs.get(role)
        return str(path) if path is not None else None

    return (_p("character"), _p("outfit"), _p("background"),
            _p("driver") if camera_aware else None,
            json.dumps(params, sort_keys=True, default=str))


def tryon_share_groups(manifest: Manifest) -> dict[str, str]:
    """run id -> the id of the run whose try-on it reuses (itself for a leader).

    The leader is the first run of its group in manifest order, so the answer
    is stable across calls. Only runs _local_tryon_stage names appear — that
    function is the one answer to "is this stage local at all".
    """
    leaders: dict[tuple, str] = {}
    groups: dict[str, str] = {}
    for run in manifest.runs:
        stage_name = _local_tryon_stage(run)
        if stage_name is None:
            continue
        key = tryon_share_key(run, stage_name)
        if key is None:
            groups[run.id] = run.id
            continue
        groups[run.id] = leaders.setdefault(key, run.id)
    return groups
```

  Check that `json` is imported in `runner.py` (add it if not). `run.inputs` values are `Path`s, as used in `local_tryon.py:774-776`.

- [ ] **Step 4: Run the tests. Expected: PASS.** Then run the whole runner file, which must still pass.
- [ ] **Step 5: Commit.** `git commit -m "feat(batch): compute which Phase A try-ons are the same image"`

---

### Task 2: Two-pass Phase A with follower copies

**Files:**
- Modify: `scripts/batchlib/runner.py`: `run_local_phase` (≈ lines 525-720) and `preserved_local_tryon` (≈ line 458)
- Test: `scripts/tests/test_batch_runner.py` (new class `SharedTryonPhaseATests`)

**Interfaces:**
- Consumes: `tryon_share_groups`, `tryon_share_key` (Task 1).
- Produces: `follower_reusable(run, stage_name, recorded, dest, leader_id, leader_file: Path) -> bool`, and journal fields `shared_from: str` and `source_sha256: str` on follower entries. Task 3 reads these fields and must not recompute groups from them. Task 3 calls `tryon_share_groups`.

- [ ] **Step 1: Write the failing tests.** Use a counting fake, patched with `mock.patch("batchlib.runner.run_local_tryon", fake)`:

```python
calls = []
def fake(run, params, settings_, out_path):
    calls.append(run.id)
    out_path.write_bytes(f"img-{run.inputs['outfit'].name}-{len(calls)}".encode())
    return 1, out_path.stat().st_size
```

  Tests, each asserting `len(calls)` and the journal:
  - `test_three_outfits_two_drivers_call_three_times`: 3 calls. Every follower's file bytes equal its leader's. Follower entries have `status == "done"`, `shared_from == leader`, a `source_sha256` equal to the sha256 of the leader's file, `elapsed_sec == 0` and `phase == "local"`. `result.done` holds only the 3 leaders.
  - `test_camera_aware_one_outfit_two_drivers_calls_twice` (patch PIPELINES as at line 139).
  - `test_failed_leader_fails_its_followers_without_calling`: the fake raises `JobError` for one leader. That leader's followers are `error`, with the error text containing `"shared try-on from <leader id> failed"`. The call count equals the number of leaders only.
  - `test_resume_calls_nothing`: run twice, the second with `resume=True`. Second-run calls: 0.
  - `test_regenerated_leader_recopies_followers`: after the first run, pop the leader's stage from the journal (as `_regen_tryon` does) and rerun with `resume=True`. 1 call, and the follower bytes equal the new leader bytes.
  - `test_leader_file_deleted_reruns_once_and_recopies`: delete the leader's file and rerun. 1 call, and the followers are recopied.
  - `test_dropping_the_leader_promotes_without_calling`: after the first run, write a manifest without the leader run (same batch id, `resume=True`). 0 calls, and the remaining follower of the next leader has `shared_from` equal to the new leader.
  - `test_mixed_seeded_and_unseeded`: one outfit is seeded (`seedImage` pointing at a temp file) and one is not, × 2 drivers. Calls: 1 (the unseeded leader only). Both seeded runs copy their seed.
  - `test_preserved_local_tryon_counts_followers`: after the first run, `preserved_local_tryon` returns `(6, 6)` for the 3×2 manifest.

- [ ] **Step 2: Run them. Expected: FAIL** (6 calls instead of 3, and so on).

- [ ] **Step 3: Implement.**
  1. Add a helper `_sha256(path: Path) -> str` (`hashlib.sha256`, reading in 1 MiB chunks) and:

```python
def follower_reusable(run: Run, stage_name: str, recorded: dict, dest: Path,
                      leader_id: str, leader_file: Path) -> bool:
    """A follower's copy stands only while it is still a copy of THIS leader's
    CURRENT image (spec §2): regenerate replaces the leader's file and leaves
    the follower's journal alone, so the digest is what notices."""
    return (local_tryon_reusable(run, stage_name, recorded, dest)
            and recorded.get("shared_from") == leader_id
            and leader_file.is_file()
            and recorded.get("source_sha256") == _sha256(leader_file))
```

  2. In `run_local_phase`, after `jobs` is built, compute `groups = tryon_share_groups(manifest)`. Split `jobs` into `leader_jobs` (where `groups.get(run.id, run.id) == run.id`) and `follower_jobs`. Feed only `leader_jobs` to the existing pool (`pending = list(leader_jobs)`). The leaders' reuse check stays `local_tryon_reusable`, and a promoted leader's stale `shared_from` is ignored because that function does not read it.
  3. After the `with ThreadPoolExecutor` block, process `follower_jobs` sequentially on the main thread. `fail_fast` does not skip them: they cost nothing, and each is either a copy or an error. For each one:
     - Look up the leader's `run_dir` and `dest` (`stage_dest(leader_run, out_dir / "runs" / leader_id, stage_name)`) and its journal entry, under `lock`.
     - If `not force` and `follower_reusable(...)`: log a skip and continue. Apply the same "clear a stale error" block the leader skip path has.
     - If the leader's entry is not `done` or its file is missing: record `error` exactly like `_ghi_hong` does (stage entry and run-level status/error, `save_state`, `run.log` line), with the message `f"shared try-on from {leader_id} failed"`, and add it to `result.failed`.
     - Otherwise `shutil.copy2(leader_dest, dest)` and write the stage entry with the same keys as the success path (`status`, `elapsed_sec: 0`, `file`, `bytes`, `params_sent`, `params_manifest`, `phase: "local"`), plus `shared_from` and `source_sha256`. Clear the run-level error the same way the success path does, then `save_state`. Log `f"{stage_name} (local): shared from {leader_id} → {dest.name}"`. Do **not** append to `result.done`.
     - Extract the shared "write a done entry" and "write an error entry" logic out of `_one` into two local closures that both passes call, so the journal shape cannot drift between them. `_ghi_hong` becomes one of them.
  4. In `preserved_local_tryon`, compute `groups = tryon_share_groups(manifest)`. For a follower, use `follower_reusable` with the leader's dest. For a leader, keep `local_tryon_reusable`.
  5. Add a paragraph to `run_local_phase`'s docstring explaining the two passes and why followers never call the provider.

- [ ] **Step 4: Run the new tests, then the full suite.** `make batch-test`. Expected: all pass, with no existing test changed. If an existing test breaks because its fixture has two runs that now share a key, **stop and report**: do not edit that test's expectations. The fix may be giving that fixture distinct outfits, but that is a controller decision.
- [ ] **Step 5: Commit.** `git commit -m "feat(batch): Phase A calls the provider once per look and copies it to every driver"`

---

### Task 3: Previews show the grouping (API and Telegram)

**Files:**
- Modify: `scripts/tgbot/bot.py`: `AppRuns.tryon` (≈ line 7172) and `_deliver_tryon_previews` (≈ line 2938)
- Test: `scripts/tests/test_batch_control_botruns.py`, `scripts/tests/test_batch_bot.py`

**Interfaces:**
- Consumes: `tryon_share_groups` from `batchlib.runner` (import it where `_local_tryon_stage` is imported).
- Produces: the preview JSON fields `shared_from: str | None` (leader **index**) and `shares: list[str]` (the leader's followers' indices, `[]` otherwise).

- [ ] **Step 1: Write the failing tests.**
  - In `test_batch_control_botruns.py`, next to the existing `tryon` tests, write a manifest of 2 outfits × 2 drivers (look at how the existing tests there build the chat manifest and journal, and reuse that setup). `GET …/tryon` must return, for indices `0..3` ordered (o1d1, o1d2, o2d1, o2d2): index 0 `shared_from None, shares ["1"]`; index 1 `shared_from "0", shares []`; index 2 `shared_from None, shares ["3"]`; index 3 `shared_from "2", shares []`.
  - In `test_batch_bot.py`, next to `test_phase_a_previews_carry_a_regenerate_button_per_image`: with a grouped manifest where all four are done, `_deliver_tryon_previews` sends **2** documents, each caption contains `"used by 2 runs"`, the buttons carry indices 0 and 2, and `payload["sent_tryon"]` contains all 4 run ids.
  - Also check the ungrouped case: the existing tests there must still pass unchanged.

- [ ] **Step 2: Run them. Expected: FAIL.**
- [ ] **Step 3: Implement.**
  - `AppRuns.tryon`: load the manifest once (as `_tryon_entries` does). Compute `groups` and a map `id → index`. For each preview, `shared_from = None if groups[run.id] == run.id else index_of[groups[run.id]]`, and `shares = [index_of[r] for r, l in groups.items() if l == run.id and r != run.id]`, in manifest order. Put the manifest load inside the existing lock.
  - `_deliver_tryon_previews`: compute `groups` once. Skip a run whose leader is not itself. For a leader, add `" · used by {k} runs: {ids}"` to the caption when it has followers (k counts the leader). Mark the leader **and** its followers in `sent`. A follower that is not yet `done` must not keep the group from being sent, because the leader's image is the content.
- [ ] **Step 4: Run both test files. Expected: PASS.**
- [ ] **Step 5: Commit.** `git commit -m "feat(bot): previews name the runs that share one try-on"`

---

### Task 4: Regenerate, retry and failure reports act on the group

**Files:**
- Modify: `scripts/tgbot/bot.py`: `_regen_tryon` (≈ 3079), `_retry_tryon` (≈ 3278), `_report_failed_tryons` (≈ 3240)
- Test: `scripts/tests/test_batch_bot.py`, `scripts/tests/test_batch_control_botruns.py`

**Interfaces:**
- Consumes: `tryon_share_groups`.
- Produces: no new names.

- [ ] **Step 1: Write the failing tests.**
  - `test_regenerate_on_a_follower_regenerates_its_leader`: tap `rg:1:<token>`, where index 1 is a follower of 0. The leader's file is backed up to `.v1`, the leader's stage is popped, and the follower's stage and file are untouched. `start_phase_a` is called once, and `payload["regen"]["run"]` is the leader id.
  - API: `POST /v1/runs/{id}/tryon/1/regen` on a follower reaches `_regen_tryon` and acts on the leader. Assert on the journal as above.
  - `test_retry_switches_the_provider_of_the_whole_group`: the leader has failed and its followers have errors. Tap retry with `qwen-max` (patch `qwen_max_configured` to True). Every member's drafted job has `provider == "qwen-max"` in the rewritten manifest, and the other group's runs keep `gemini`.
  - `test_one_failure_report_per_failed_leader`: 1 failed leader with 2 followers gives exactly 1 message. It names both follower ids, and its buttons target the leader's index.
- [ ] **Step 2: Run them. Expected: FAIL.**
- [ ] **Step 3: Implement.**
  - `_regen_tryon`: right after `run = manifest.runs[int(index)]` and `stage_name` are resolved, and before the `seeded` check, compute `groups`. If `groups.get(run.id, run.id) != run.id`, replace `run` and `index` with the leader's. Comment it: "followers pick up the new image by source_sha256 (runner.follower_reusable); popping them too would make _settle_regen's single-entry restore incomplete". The seed list must include the followers, which are `done` and so already get added. Check that the progress payload's `sent_tryon` includes them.
  - `_retry_tryon`: when switching the provider, set it on every job whose run id is in the named run's group (`[i for i, r in enumerate(manifest.runs) if groups.get(r.id) == groups.get(run.id)]`). Change the message to "{n} runs switched to …". Update the docstring's "that run alone" sentence.
  - `_report_failed_tryons`: compute `groups`. Skip followers. For a leader with followers, append `"\nAlso waiting on this image: {ids}"`.
- [ ] **Step 4: Run both test files, then `make batch-test`. Expected: PASS.**
- [ ] **Step 5: Commit.** `git commit -m "feat(bot): regenerate, retry and failure reports act on a shared try-on's group"`

---

### Task 5: App previews: one card per look, group drop

**Files:**
- Modify: `ios/MotionKit/Sources/MotionKit/Models/RunFlow.swift`, `ios/MotionKit/Sources/MotionKit/Stores/RunFlowStore.swift`, `ios/MotionApp/RunFlow/TryonPreviewCard.swift` and the view that iterates `previews` (find it with `grep -rn "previews" ios/MotionApp/RunFlow`)
- Test: `ios/MotionKit/Tests/MotionKitTests/RunFlowTests.swift`, `ModelsTests.swift`, `Fixtures.swift`

**Interfaces:**
- Produces: `TryonPreview.sharedFrom: String?` and `shares: [String]?` (the decoder uses snake-case conversion, so check how `hasImage` maps and follow it); `RunFlow.cards: [TryonPreview]` (leaders only, in order); `RunFlow.members(of: TryonPreview) -> [TryonPreview]` (the leader plus its followers); `RunFlow.canDropFromBatch(_ preview: TryonPreview) -> Bool`; `drop(_:)` now drops the group.

- [ ] **Step 1: Write the failing tests.**
  - `ModelsTests`: a preview JSON without the new keys decodes with both nil. A preview with `"shared_from":"0","shares":[]` decodes.
  - `RunFlowTests`, with a 2×2 previews fixture where each `run` matches a basket entry's `runID`:
    - `cardsAreLeadersOnly`: 2 cards, indices "0" and "2".
    - `dropRemovesEveryMemberThenValidatesOnce`: the recorded requests are two `DELETE …/batch/<digest>` (one per member) and exactly one `POST …/validate`.
    - `dropOfAGroupThatIsTheWholeBasketIsNotOffered`: with a basket of 2 entries that are both in one group, `canDropFromBatch(card) == false`.
    - `aSingleUngroupedPreviewStillDropsAlone`: a regression for today's behaviour when the fields are nil.
  - Follow the existing drop tests' `StubURLProtocol` routing style in `RunFlowTests.swift`.
- [ ] **Step 2: Run** `cd ios/MotionKit && swift test --filter RunFlowTests`. **Expected: FAIL** (compile errors are acceptable as the failure).
- [ ] **Step 3: Implement.**
  - Model: add the two optional properties.
  - `cards`: `tryon?.previews.filter { $0.sharedFrom == nil } ?? []`.
  - `members(of:)`: the leader plus the previews whose `sharedFrom == leader.index`.
  - `canDropFromBatch(_:)`: the existing condition (keep the `canDropFromBatch` property as the shared prefix) plus `draft.batch.count - members(of:).count >= 1`. Keep the property for the call sites that do not have a preview. Otherwise rename the call sites and grep for every use.
  - `drop(_ preview:)`: after the fresh draft read, map every member to `batchEntry(for:)`. If any is nil, take the existing "draft changed" path. For each entry, apply the existing `editedJobEquals`/`clearRole` PATCH rule, then `DELETE`. Validate once afterwards. The rest of the function (the message, panel clearing, `refreshTryon`) is unchanged. Do not duplicate the function: loop inside it.
  - Views: iterate `flow.cards` instead of `previews`. On a card with `shares` non-empty, show `Text("Used by \(shares.count + 1) videos")`. The Drop button uses `canDropFromBatch(preview)`.
- [ ] **Step 4: Run** `swift test` (the whole package) **and** `make ios-build`. **Expected: PASS.**
- [ ] **Step 5: Commit.** `git commit -m "feat(ios): one preview card per shared try-on, drop removes the whole look"`

---

### Task 6: `BatchComposer` drivers dimension

**Files:**
- Modify: `ios/MotionKit/Sources/MotionKit/Stores/BatchComposer.swift`
- Test: `ios/MotionKit/Tests/MotionKitTests/BatchComposerTests.swift` (extend `FakeDraftServer` so PATCH accepts a `driver` slot; read its `answer` implementation first)

**Interfaces:**
- Produces: `static let driverRole = "driver"`, `static let maxJobs = 12` (remove `maxOutfits` and update every reference: `grep -rn maxOutfits ios/`), `private(set) var drivers: [String]`, `func toggle(driverID: String)`, `var jobCount: Int`, `var tryonCount: Int`, `var capReason: String?` (non-nil after a refused toggle, cleared by any successful toggle), `static func supportsDrivers(_ pipeline: Pipeline) -> Bool`, `var cameraAwareTryon: Bool` (true when the selected pipeline's try-on is camera-aware: the pipeline id has the prefix `"tryon-camera"`. Check the catalog's pipeline ids in `Fixtures.pipelines` and the live `ios-contract` output. If the catalog exposes a better signal, use that and note it).

- [ ] **Step 1: Write the failing tests.**
  - `twoOutfitsTwoDriversAreFourPatchAddPairsThenBothSlotsAreCleared`: the write sequence is `PATCH {outfit:o1, driver:d1}` → `add-to-batch` → `(o1,d2)` → `(o2,d1)` → `(o2,d2)`, then a final PATCH that clears both `outfit` and `driver`. Mirror `threeOutfitsAreThreePatchAddPairsThenTheOutfitIsCleared`.
  - `noDriversSelectedKeepsTodaysBehaviour`: the existing 3-outfit sequence, byte-for-byte (the final PATCH clears only `outfit`).
  - `continueSkipsPairsAlreadyInTheBasket`: preload `(o1,d1)`, then run. 3 pairs are added.
  - `aDriverThatWouldExceedTwelveJobsIsRefusedWithAReason`: with 6 outfits and 2 drivers selected (12 jobs), toggling a 3rd driver leaves `drivers.count == 2` and `capReason` non-nil. Toggle two outfits off (4 outfits); now the 3rd driver is accepted (4×3 = 12) and `capReason == nil`.
  - `anOutfitThatWouldExceedTwelveJobsIsRefused`: the same check from the outfit side.
  - `seedsAreSharedAcrossDriversOfOneOutfit`: every PATCH for o1 carries the same `tryon_seed`.
  - `tryonCountIsOnePerUnseededOutfit`: 3 outfits, 1 seeded, 2 drivers gives `tryonCount == 2` and `jobCount == 6`. With the camera-aware pipeline selected: `tryonCount == 4` (2 unseeded × 2 drivers).
  - `missingSharedExcludesTheDriverOnlyWhenDriversAreSelected`.
- [ ] **Step 2: Run** `swift test --filter BatchComposerTests`. **Expected: FAIL.**
- [ ] **Step 3: Implement.**
  - `sharedSlots` / `missingShared`: exclude `driverRole` when `!drivers.isEmpty`.
  - `jobCount = outfits.count * max(drivers.count, 1)`. In both `toggle` functions, refuse an addition that would make it exceed `maxJobs` and set `capReason = "At most \(Self.maxJobs) videos per batch — \(outfits.count) outfits × \(drivers.count) drivers is already \(jobCount)."`
  - `canRun` gains `jobCount <= Self.maxJobs`.
  - Introduce `struct CrossStep: Equatable { let outfit: CrossOutfit; let driverID: String? }`. `pending(in:)` returns `[CrossStep]` (outfit-major), comparing the slots including the driver when non-nil. Update `progress` to count steps (`total = jobCount`).
  - `run()`: iterate steps. PATCH `slots: [outfitRole: o, driverRole: d]` when `d != nil`. The final clear PATCH clears the driver too when `!drivers.isEmpty`. On success, `drivers = []` as well. `stop(at:)` names the pair.
  - `tryonCount`: `cameraAwareTryon ? unseeded * max(drivers.count,1) : unseeded`, where `unseeded = outfits.filter { $0.seedID == nil }.count`.
  - Update the doc comment on the class (Phase 6 spec §4 plus this spec §4) and on `maxJobs` (why jobs, not outfits).
- [ ] **Step 4: Run** `swift test` (the whole package). **Expected: PASS.**
- [ ] **Step 5: Commit.** `git commit -m "feat(ios): batch composer crosses outfits with drivers, capped at 12 jobs"`

---

### Task 7: Batch UI and zero-spend smoke

**Files:**
- Modify: `ios/MotionApp/NewJob/BatchComposerSection.swift`, `ios/MotionApp/NewJob/NewJobView.swift` (only if the seed observers need `drivers`)
- Test: the UI test target (`ls ios/MotionAppUITests`). Extend `Phase6SmokeTests` or add `MultiDriverSmokeTests` following its structure and its `-UITestRecordingSpendGate` launch argument.

**Interfaces:**
- Consumes: Task 6's API.

- [ ] **Step 1: Write the failing UI smoke.** Open New Job, switch to Batch, pick the character and a pipeline with a driver role (mirror what Phase6SmokeTests does), open `batch.pickDrivers` and tap 2 `driver.pick.*`, pick 2 outfits, and assert the summary text contains `"2 outfits × 2 drivers = 4 videos"`. Tap `batch.run`, wait for the header's `4 jobs`, clear, and assert zero recorded spends (as Phase6SmokeTests does).
- [ ] **Step 2: Implement the view.**
  - A driver multi-select mirroring the outfit picker (`batch.pickDrivers` opens a sheet listing the video materials of role kind `driver`, each row `driver.pick.<id>`), shown only when `BatchComposer.supportsDrivers(pipeline)`.
  - The summary line under the pickers: `"\(outfits) outfits × \(max(drivers,1)) drivers = \(jobCount) videos · \(tryonCount) try-ons"`, plus, when `cameraAwareTryon && drivers.count > 1`, a caption: "Camera pipelines make one try-on per driver." The line has `accessibilityIdentifier("batch.summary")`.
  - `capReason` renders as a caption (`batch.capReason`).
  - Every new control is `.disabled(composer.isRunning)`, which is the Phase 6 invariant. After the change, verify with `grep -rn "composer.isRunning" ios/MotionApp/`.
- [ ] **Step 3: Run** `make ios-build`, then `make ios-ui-test`. That target boots a simulator, so run it outside the command sandbox. **Expected: PASS, zero spends.** If the simulator cannot boot in the environment, report that and do not claim the gate.
- [ ] **Step 4: Commit.** `git commit -m "feat(ios): pick several drivers in Batch mode"`

---

### Task 8: Gates, docs, PR (controller)

- [ ] `make batch-test`, `make check-job-types`, `make check-batch-params`, `cd ios/MotionKit && swift test`, `make ios-build`, `make ios-ui-test`, `scrub-secrets.sh --check`. Add a row to the spec's gate record for each.
- [ ] Update `docs/superpowers/swiftui-app-progress.md`:
  - "Current baseline" to the current SHA;
  - a "Multi-driver batch" subsection;
  - the Next-work item 3 (pair mode) closed, and replaced with a pointer to this spec;
  - the known gap: no real 2×2 Phase A has run.
- [ ] Push, open the PR (`gh` needs the doanhthuc account; see memory), and wait for review and CI.
- [ ] VPS pre-merge check (spec §6), then merge. Then watch the `deploy-bot` run, run `make ios-contract` live, and add the post-deploy gate rows in a docs commit.
