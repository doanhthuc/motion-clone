# Multi-driver batch, and one try-on per look

Date: 2026-09-25 · Status: approved in conversation, not yet implemented

The parent SwiftUI spec (`2026-09-22-swiftui-app-design.md` §1) deferred "pair (1:1) mode". Asked
what pair was for, the user described a different shape: **one character, many outfits and many
driving videos.** Batch mode today is one character × N outfits with **one** shared driver, so M
drivers means building the batch M times by hand. That is the UI half.

The money half matters more. Phase A makes **one try-on per job**, with no reuse
(`batchlib/runner.py` `run_local_phase` → `local_tryon.run_local_tryon`, once per run). A try-on's
inputs are the character, outfit, background, provider and try-on params. The driver is an input
**only** when the stage runs with `cameraAware` (`local_tryon.py:790-792`, the `camera-tryon` stage
in `pipelines.py:85`). So 1 character × 4 outfits × 3 drivers today costs 12 provider calls for 4
distinct images. The 12 images also differ from one another, so the three videos of one outfit do
not show the same garment render.

Decided with the user (2026-09-25), in this order:

- **Share one try-on across every driver of an outfit** (option A of three). The rejected
  alternatives: a manual two-pass flow through Saved try-ons (B), and accepting N×M calls (C).
- **Detect sharing in the runner** (approach 1 of three), not through a manifest field written by
  each composer (2), and not through a server-orchestrated two-pass Phase A (3). Approach 2 fails
  silently whenever a composer forgets the field. Approach 3 adds an intermediate state to an
  already complicated flow.
- **Cap a batch at 12 jobs in total**, replacing the 12-outfit cap, because GPU cost grows with the
  number of videos, not the number of outfits.

Out of scope: stock-watch notifications, which get their own spec. Also out of scope: a pair mode
that zips N characters with N outfits. Nothing asked for it, and Single mode's **Add to batch**
already builds arbitrary pairs by hand.

### Gate record

Append-only: add a row when a gate runs, and do not restate its result in prose elsewhere in this
file.

| Gate | Result | Date | Ran by |
|---|---|---|---|

## 1. The share key (runner)

A new pure function in `scripts/batchlib/runner.py`:

```python
def tryon_share_key(run: Run, stage_name: str) -> tuple | None
```

- It returns `None` when the stage's effective params carry `seedImage`. A seeded run already copies
  its image and calls no provider, so it is never grouped.
- Otherwise it returns a tuple of: the resolved `character`, `outfit` and `background` input paths
  (`background` may be `None`); the `driver` path **only if** the effective params' `cameraAware` is
  truthy (the same `("1", "true", "yes", "on")` test `local_tryon.py` applies); and the effective
  stage params (`effective_stage_params(stage_name, run.stage_params.get(stage_name))`) as a sorted
  JSON string. The provider lives inside those params, so it needs no separate element.
- It is computed from the manifest at run time. **No manifest field is added.** The app, Telegram,
  and hand-written YAML batches all benefit without knowing about it.

`tryon_share_groups(manifest) -> dict[str, str]` maps each local-try-on run id to its group's
**leader** run id. A leader maps to itself. The leader is the first run of the group in manifest
order, so it is deterministic. Only runs for which `_local_tryon_stage(run)` is not `None` appear,
because that function is the one answer to "is this stage local at all" (its docstring's rule
against second opinions applies here too).

## 2. Phase A in two passes (`run_local_phase`)

1. **Leaders** go through the existing pool, unchanged. Each one either calls the provider or copies
   its `seedImage`.
2. **Followers** run afterwards on the main thread. Each copies its leader's `dest` to its own `dest`.
   A copy is cheap, so no pool is needed. The journal entry has the same shape a real Phase A result
   has (status `done`, `file`, `bytes`, `params_sent`, `params_manifest`, `phase: "local"`,
   `elapsed_sec: 0`), plus two fields:
   - `shared_from`: the leader's run id;
   - `source_sha256`: the SHA-256 of the leader's image at copy time.

Rules:

- **A follower never calls the provider.** If the leader's stage is not `done`, or its file is
  missing, the follower is journalled `error` with `"shared try-on from <leader id> failed"`. The
  run-level `status`/`error` follow the same rule `_ghi_hong` applies. Calling the provider instead
  would pay for exactly what just failed.
- **The follower's reuse check** replaces `local_tryon_reusable` for followers only. The follower
  counts as done when:
  1. `local_tryon_reusable` holds (status, file, params);
  2. `shared_from` equals its current leader;
  3. `source_sha256` equals the SHA-256 of the leader's current file.

  Condition 3 makes regenerate work without touching followers. A new leader image fails the
  comparison, so the follower copies again. A restored old image (`_settle_regen`) passes it again.
- **Drop of a leader.** The next member becomes the leader. It already holds an identical, reusable
  copy (same file, same params), so `local_tryon_reusable` reuses it and no provider call happens.
  Its own `shared_from` is ignored once it leads. The remaining followers fail condition 2 and copy
  again, which costs only a file copy.
- **`force`** still bypasses reuse for every run. Leaders call again and followers copy again, so the
  group still costs one call.
- **`fail_fast`** stops new leaders, as today. Followers of leaders that never ran become `error` by
  the first rule.
- `preserved_local_tryon` counts a follower as reusable by the same follower rule, so the stock-out
  card's "N/M preserved" still agrees with what a resume does.
- `result.done` lists only leaders that actually ran, because it is the list readers use to count
  quota spent. Follower copies are logged, but they are not in `done`.

## 3. Bot and API (`scripts/tgbot/bot.py`, `scripts/httpapi/`)

- **Previews** (`AppRuns.tryon` → `GET /v1/runs/{id}/tryon`). Each preview gains two read-only
  fields: `shared_from` (the leader's **index** as a string, or `null` for a leader or an ungrouped
  run) and `shares` (the indices of the leader's followers, `[]` when there are none). Both are
  additive, so an installed app build still decodes the response and keeps showing N×M cards.
- **Regenerate** (`_regen_tryon`). An `index` that names a follower is resolved to its leader first.
  Only the leader's stage is backed up and popped, and the followers pick up the new image by the
  `source_sha256` rule. `_settle_regen` is unchanged. The `seeded` refusal keeps its current position
  and meaning.
- **Retry with another provider** (`_retry_tryon`). This switches the provider of **every member** of
  the named run's group, not just one. Switching one member would split the group: the old
  followers would elect a new leader and call the old provider again.
- **Failed try-on reports** (`_report_failed_tryons`). One message per failed **leader**. It names
  the followers that depend on it, and the retry buttons target the leader's index. Followers get no
  message of their own, because M identical messages would be noise.
- **Telegram previews** (`_deliver_tryon_previews`). One photo per group, captioned
  "used by N runs: …". The 🔄/Keep buttons carry the leader's index. Followers are marked
  `sent_tryon` together with their leader so they are never sent on their own.
- **Keep** (`POST /v1/tryon-library`) is unchanged. Any index in a group holds the same image.

## 4. App (`ios/`)

### `BatchComposer` (MotionKit)

- A new `drivers: [String]` holds the selected driver material ids. When it is empty, today's
  behaviour applies exactly: the driver is a shared slot. When it is non-empty, `driver` joins
  `outfit` in being excluded from `sharedSlots` and `missingShared`.
- The steps are outfits × drivers (outfit-major). Each step is
  `PATCH {slots: {outfit, driver}, tryon_seed}` followed by `add-to-batch`. There is still no new
  route and no `SpendGate`.
- `pending(in:)` compares the full slot set including the driver, so a Continue skips every pair that
  is already in the basket.
- Seeds stay per outfit. `library.matches(slots:)` already excludes the driver role, so all M jobs of
  one outfit carry the same seed.
- The cap becomes `maxJobs = 12`, computed as `outfits.count × max(drivers.count, 1)`. A toggle that
  would exceed it is refused, and the reason is rendered next to the picker. It is never silently
  ignored.
- After a successful build, the PATCH clears `outfit` and also clears `driver` when drivers were
  multi-selected.
- `supportsDrivers(_ pipeline:)` is true when the pipeline's roles include `driver`.

### `BatchComposerSection`

- A driver multi-select (`batch.pickDrivers` → `driver.pick.<id>`), shown only when
  `supportsDrivers`.
- A summary line: **"4 outfits × 3 drivers = 12 videos · 4 try-ons"**. The try-on count is computed
  on the device: one per unseeded outfit, or outfits × drivers when the pipeline's try-on stage is
  camera-aware. In the camera-aware case a short note says why it is not shared. The count is shown
  so the number of provider calls is visible before Phase A.

### `RunFlow` previews

- `TryonPreview` gains optional `sharedFrom: String?` and `shares: [String]?`, so it keeps decoding
  against an older server.
- The previews screen renders one card per leader (a preview whose `sharedFrom` is nil), with a
  "used by K videos" label when `shares` is non-empty. Regenerate and Keep use the leader's index.
- **Drop from batch** on a card drops the whole group: one `DELETE /v1/draft/batch/{digest}` per
  member, followed by a single re-validate. The rejected thing is a look, and every video of that
  look shows it. `canDropFromBatch(for:)` requires that at least one basket entry remains after the
  group is removed. The `isDropping` guard in `RunFlow.spend` covers the whole loop. A single pair can
  still be removed before Phase A from the basket list.

## 5. Testing

All gates are free.

- `make batch-test`. Runner tests use a fake provider that counts calls:
  - 3 outfits × 2 drivers make 3 calls, and the followers' images are byte-identical to their
    leader's;
  - camera-aware, 1 outfit × 2 drivers makes 2 calls;
  - a failed leader leaves its followers in `error` with 0 extra calls;
  - a resume makes 0 calls;
  - regenerating a leader (its stage popped) makes 1 call and recopies the followers;
  - dropping a leader makes 0 calls and the new leader is reused;
  - a seeded run is never grouped.

  Bot tests cover: the preview `shared_from`/`shares` fields, regen on a follower index resolving to
  the leader, retry switching the provider for the whole group, one failure report per leader, and
  one Telegram photo per group.
- `make ios-test`. N×M planning, the 12-job cap and its refusal, Continue re-planning with drivers,
  preview grouping, group drop, and the try-on count.
- `make ios-contract` decodes the new preview fields against the live server, after the deploy.
- `make ios-ui-test` gains a smoke that builds 2 outfits × 2 drivers from free draft mutations and
  asserts zero recorded spends.
- `make check-job-types`, `make check-batch-params` and `scrub-secrets.sh --check` must pass as
  usual.

**Not covered by any of the above:** a real Phase A over a 2 × 2 batch that shows 2 provider calls.
That is a spend test, and it needs its own approval and a quoted price.

## 6. Deploy

This touches `scripts/**`, so merging auto-deploys `motion-bot`. Before merging, check the VPS for a
drain, Phase A, lease or migration (`batch/*.state.json`, `.env`'s `GPU_INSTANCE_ID`,
`pgrep -af 'drain.py|batch_run.py'`). The server change is additive on the wire, so the order
between the app install and the deploy does not matter for correctness.
