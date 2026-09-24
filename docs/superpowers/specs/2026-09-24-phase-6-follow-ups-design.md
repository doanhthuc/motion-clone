# Phase 6 follow-ups — a server-side resume latch and four app fixes

Date: 2026-09-24 · Status: design approved in chat, not yet implemented · Branch: `feat/phase-6-follow-ups` (one branch, one PR)

These are the five items Phase 6 recorded in
[`../swiftui-app-progress.md`](../swiftui-app-progress.md) §"Known incomplete work" and could not
fix on its own branch. Four are app-side; one is a `scripts/**` change to the money path and is the
reason this exists as its own effort rather than a cleanup commit.

### Gate record

Append-only: add a row when a gate runs, and do not restate its result in prose elsewhere in this
file. "Ran by" separates worktree gates from live ones — a simulator or fake-server result is not a
live one.

| Gate | Result | Date | Ran by |
|---|---|---|---|
| `make batch-test` | _not yet run_ | | |
| `cd ios/MotionKit && swift test` | _not yet run_ | | |
| `make ios-build` | _not yet run_ | | |
| `xcodebuild … build-for-testing` | _not yet run_ | | |
| `motions-studio/setup/scrub-secrets.sh --check` | _not yet run_ | | |
| `make ios-contract` (live) | _not yet run_ | | |
| `make ios-ui-test` (live, outside the sandbox) | _not yet run_ | | |
| VPS pre-merge check (drain / Phase A / lease / migration) | _not yet run_ | | |
| `deploy-bot` workflow run | _not yet run_ | | |
| `make ios-contract` (post-deploy, live) | _not yet run_ | | |

## 1. Scope

In, in the order the handoff lists them:

1. **The money one.** Make the rental-retry generation latch survive an app relaunch by moving it
   server-side: `resume` refuses `409 stale_run` when the draft has moved since the confirm that
   wrote the manifest it is about to re-rent (§3).
2. `MigrateFlow.migrate()` bypasses `RunFlow.spend`, so it is the one spend entry point Phase 6's
   `isDropping` guard does not cover (§4).
3. A failed validation reaches the phone as developer-facing copy — and, in one path, as an **empty**
   string (§5).
4. `Fixtures.pipelines` names a role `mask` that does not exist anywhere in `scripts/**` (§6).
5. Batch mode renders no `Clear`, so emptying the draft means switching to Single first (§7).

Out of scope, still recorded in the handoff: the `not_found` collision between a stale material and a
deleted library entry (needs a distinct server code and its own review); the `app/` owner coupling
`TryonLibraryStore.matches(slots:)` depends on; the deferred parent-spec items (pair mode,
stock-watch); and any live spend. **No pod is rented and no try-on provider is called by anything in
this spec.**

## 2. What the server does today (read 2026-09-24)

Four facts constrain §3, and each was read rather than assumed. Two of them contradict the obvious
design.

- **An accepted confirm clears the app's draft.** `AppRuns.confirm` calls `self.drafts.clear()` on
  *both* accepted branches — the `_phase_a_matches_draft` resume branch (`bot.py:7029`) and the
  fresh-spend branch (`bot.py:7039`) — under `if out:`. `_do_confirm` returns a truthy
  `Outcome(True, "started")` as soon as `start_drain` is spawned, so the draft is cleared even for a
  rental that fails seconds later. **Consequence:** after any accepted confirm the draft is empty, so
  `_phase_a_matches_draft()` returns `False` unconditionally — its first test is `if validated is not
  True: return False` (`bot.py:6945-6946`), and `clear()` leaves a `_fresh()` draft whose `validated`
  is `None`. Reusing that predicate inside `resume` would permanently break Retry rental.
- **`clear()` increments the generation by exactly one. It does not reset it, and it does not stand
  still.** `drafts.py:485-490` seeds `self._fresh(generation=self._load().generation)` and then
  returns through `_changed(d)`, which does `d.generation += 1` (`drafts.py:275-279`). The comment's
  "The generation keeps counting" means *is not reset to 0*, not *does not move*. Measured
  2026-09-24: two clears on a fresh store give 0 → 1 → 2.

  **The first draft of this spec stated this fact backwards**, claiming `clear()` preserved the
  generation. The error came from reading lines 485, 487 and 489 out of a grep and never reading line
  490, which is the `return self._changed(d)`. It was load-bearing rather than cosmetic: it is what
  made "read the generation anywhere inside the locked block" look safe, and following that literally
  stamps `G` while the confirm leaves the draft at `G+1`, so `_resume_generation_refusal` would have
  refused every legitimate app Retry rental that ever existed. Task 2's implementer caught it by
  measurement and deviated from the plan; §3 carries the correction.
- **`validate()` does not move it.** `drafts.py:555` saves the verdict with `self._save(d)` and the
  comment "a verdict is not a change: generation stays". Only `_changed()` bumps it
  (`drafts.py:275-279`). Re-verified after the error above, not assumed.
- **`generation` is monotonic**, and the one way it goes *down* is a corrupt draft file: `_load`'s
  `except` branch moves the file aside as `….<uuid>.bad` and returns `self._fresh()` at
  `drafts.py:265`, i.e. generation 0.

Together these make "the generation at the accepted confirm, compared to the generation now" a sound
server-side rule — **provided the stamp is taken after the confirm's own `clear()`**. That ordering,
not any property of `clear()`, is what makes the clear survivable. The rule is not tripped by a
re-validate, and it cannot be satisfied by accident later, because the counter only ever rises.

Also read: `AppPod.__init__` is `(tg, chat_id, idem)` — it has no `DraftStore`, unlike `AppRuns`
(`bot.py:6889`). It is built at `bot.py:7789` from the same scope that already holds `server.drafts`
(used one line earlier, at `:7785`, for `AppRuns`). `_kill_result_path` / `_save_kill_result` /
`_load_kill_result` (`bot.py:7247-7274`) are the existing convention for "small per-chat JSON that
must survive a bot restart": `ROOT / "batch" / f"tg-{chat_id}.<name>.json"`, atomic `tmp.replace`,
and a load that fails quiet to `None`.

## 3. Item 1 — the server-side generation latch on `resume`

### Approaches rejected

- **Reuse `_phase_a_matches_draft()` inside `resume`.** Rejected on the first fact in §2: the draft is
  empty after an accepted confirm, so the comparison always fails and Retry rental never works.
- **The app sends the generation it confirmed in the resume body.** Rejected. The client's number is
  exactly what an app relaunch loses, so this rebuilds the bug instead of fixing it. It also adds a
  body field and leaves every already-installed build unprotected — the guarantee would depend on the
  client volunteering the right number, which is not a guarantee.

### Chosen: a confirm stamp on disk

The server persists the draft generation at the moment `AppRuns.confirm` is accepted, and
`AppPod.resume` compares it to the draft's generation now. Both halves are server state, so an app
relaunch is irrelevant — which is the whole point.

**Persistence.** Three module-level helpers next to `_kill_result_path`, following its convention
exactly:

- `_confirm_stamp_path(chat_id) -> Path` → `ROOT / "batch" / f"tg-{chat_id}.confirmed-generation.json"`.
- `_save_confirm_stamp(chat_id, generation) -> None` → `mkdir(parents=True, exist_ok=True)`, write
  `{"generation": N}` to `<name>.tmp`, `tmp.replace(path)`. Atomic, so a reader never sees a
  half-written stamp.
- `_load_confirm_stamp(chat_id) -> int | None` → `None` on `OSError`/`ValueError`, on a non-dict
  body, and on a missing or non-`int` `generation`. Fail-quiet, for `_load_kill_result`'s reason:
  this is read while handling a request and a corrupt stamp must not take the route down.

`batch/` is already gitignored machine state, and the file is per chat like every other
`tg-<chat_id>.*` record.

**Write.** Inside `AppRuns.confirm`'s existing `with self._locked() as busy:` block, at the single
`if out:` that builds the 202 response — the same condition that already guards both
`self.drafts.clear()` calls — read the generation (`self.drafts.runnable()[2]`) and save it. One
read, one write, one place, so the stamp is written for the resume branch and the fresh-spend branch
alike and for nothing else.

**The read must come after the clears, not before them.** `clear()` increments (§2), so a pre-clear
read stamps `G` while the confirm leaves the draft at `G+1`, and `_resume_generation_refusal` would
then refuse *every* legitimate app Retry rental. That is fail-closed — never a wrong spend — but the
feature would be silently dead for every user, and nothing else in the gate would notice. `BOT_LOCK`
is held for the whole block, so nothing outside it can move the counter between the clear and the
read. A refused confirm — `stale_panel`, `not_validated`, `nothing_to_run`,
`bot_busy`, `gpu_mismatch`, `choice_required` — writes nothing, because nothing was agreed to.

The stamp is never deleted and never rewritten on a successful resume. It means "the generation the
user last agreed to spend on", and a successful resume re-rents the same manifest without the
generation moving, so the old value is still the right one. A stale file sitting on disk is
inert: `resume` only reads it behind the existing `provision-failed.json` requirement.

**Read.** `AppPod.__init__` gains `drafts`, in `AppRuns`'s argument order — `(tg, chat_id, drafts,
idem)` — and `bot.py:7789` passes `server.drafts`. One `elif` in `AppPod.resume`, immediately after
the `run_token` check at `bot.py:7413` so the two staleness rules sit together and read as one idea,
and before the `provision-failed.json` / `_gpu_mismatch` / `_do_resume` chain:

```python
elif (stale := _resume_generation_refusal(self.chat_id, self.drafts)) is not None:
    out = Outcome(False, "stale_run", stale)
```

with

```python
def _resume_generation_refusal(chat_id: int, drafts: DraftStore) -> str | None:
    """Why `resume` must not re-rent, or None when it may."""
```

returning `None` when there is no stamp or the generation matches, and otherwise
`"the draft changed since this rental was confirmed — Confirm again to rent what is in the draft
now"`.

**`stale_run`, not a new code.** `status_for` already maps an unknown code to 409, so a new code would
work, but `stale_run` is the right one: it is the same class of refusal as the `run_token` check
above it ("what you are looking at is not what is on disk"), and the app already handles it —
`RunFlowStore.applyRefusal`'s `("stale_run", .resume)` case does `await refreshTryon()`, which is
exactly the recovery. `APIError.userMessage` passes a 409's text through verbatim, so the new
sentence reaches the screen unchanged.

**Fail open when there is no stamp.** A Telegram-initiated confirm writes no stamp, and `_do_confirm`
must not write one: it is shared with the Telegram flow, where the app's draft is not what the user
reviewed, so certifying its generation would be a lie. The latch therefore covers app-initiated
confirms only. That is precisely the population the client-side latch covers today, so this is the
same coverage moved to where a relaunch cannot lose it — not a regression, and not a claim to more.

A corrupt draft file resets the generation to 0 (`drafts.py:265`) while the stamp keeps its larger
number, so the resume is refused. Fail-closed on a draft this box can no longer read is the right
direction for a money call, and the recovery is the one the message already names.

**App side: no change is required for correctness**, so both deploy orders work — the property Phase
6's `tryon_seed` field had. `RunFlow.confirmedGeneration`, `canRetryRental` and
`retryRentalBlockReason` all stay: they turn a refused tap into a message shown *before* the tap,
which is better UX and free. Its wording aligns with the server's sentence so one rule has one
sentence, and the comment at `RunFlowStore.swift:96-104` gains a line naming the server as the
authority and this file as the pre-tap copy.

**Blast radius.** The Telegram recovery buttons (`_CB_RECOVER_RETRY`, `_CB_RECOVER_SWITCH`,
`_CB_PHASE_A_SPEND`) call `_do_resume` directly and never enter `AppPod.resume`, so they are
untouched. `test_batch_control_invariants.py` is unaffected: no new `start_drain` call site appears.
`httpapi/server.py:292-296` passes the body through unchanged.

## 4. Item 2 — `MigrateFlow.migrate()` and the drop guard

Phase 6 put `guard !isDropping` in `RunFlow.spend(_:label:)`, the single funnel `confirm`,
`regenerate`, `retryRental` and `choose(_:)` go through. `MigrateFlow.migrate()` calls
`gate.perform` directly and so is the one spend that escapes it.

`MigrateFlow.init` gains a required `runFlow: RunFlow`. `MotionApp.swift:93-94` already builds
`runFlow` immediately before `migrate`, so the wiring is one argument; `MigrateFlowTests.swift:33` is
the single factory to update. Required, not defaulted — an optional dependency that silently no-ops
when absent is how a guard rots. **But `AppModel.runFlow` is `RunFlow?`** (`MotionApp.swift:37`, nil'd
on credential teardown), so the construction cannot pass it directly: it needs a local `let`, mirroring
the file's own `let pod = …; self.pod = pod` convention, so that `AppModel` and `MigrateFlow` provably
hold the *same instance*. Two instances would make the guard inert while every test still passed,
because the tests build their own pairing.

The guard goes in both places, mirroring the Phase 6 pattern — **and the order inside `migrate()` is
load-bearing**:

- `canMigrate(at:)` gains `!runFlow.isDropping`, so the button is off the glass. With the drop guard
  first in `migrate()`, this term exists purely for the UI's button state.
- `migrate()` gains `guard !runFlow.isDropping else { message = …; return }` **ahead of** the
  `canMigrate` guard, not behind it. Both are `@MainActor` with no `await` between them, so if
  `canMigrate` carries the same term and is checked first, it returns false during a drop, the first
  guard returns silently, and the choke point is unreachable — a dead guard that reads as coverage it
  does not provide, and a refusal the user never sees. Measured during implementation: the
  behind-`canMigrate` order fails the task's own test with `migrate.message → nil`.

`RunFlow.spend`'s equivalent is not dead in the same shape because `regenerate`, `retryRental` and
`choose` all skip `canConfirm` and reach `spend` directly; `migrate()` is `MigrateFlow`'s only entry
point, so the Phase 6 pattern does not transfer by analogy. Corrected 2026-09-24 during
implementation; the first draft of this spec specified the unreachable order.

Message: `"A batch drop is still in flight — wait for it before moving the volume."` — the same
sentence shape `RunFlow.spend` uses, with the migration's own noun.

The window this closes is the client's own timeouts, not the server's budgets: a drop spends up to
**95 s** on the slot `PATCH` and **95 s** on the validate (`RunFlowStore.swift:308`, `:314`,
`timeout: 95`). The 90 s figure the first draft of this spec quoted is the *server's* validate budget
(`RunFlowStore.swift:311-312`: "the server can probe for 60 s and validate for 90 s, so stay under
Cloudflare's ~100 s"). The two are different numbers and conflating them understates the window.

`recheck()` and `replayPendingOnce()` stay unguarded, on purpose: they resolve a request that was
already sent, exactly as `RunFlow`'s equivalents do, and blocking them would strand a pending
migration behind an unrelated drop.

**Recorded asymmetry, not fixed here.** The reverse direction is still open: `canDropFromBatch`
consults `RunFlow`'s own state only, so a drop can start while a migration is in flight. That is a
different exposure — the server holds `BOT_LOCK` for `_start_migration` and `_do_resume` already
refuses while `migration_running()` (`bot.py:5561-5566`) — and widening `canDropFromBatch` reaches
the pending-notice machinery Phase 6 deliberately kept out of `spend`. The handoff asked for this
direction only.

## 5. Item 3 — validation copy on the phone

### What actually reaches the screen today

`APIError.userMessage`'s `default` branch returns a 422's server text verbatim (`APIError.swift:35`).
For `code == "invalid"` that text is one of three things, and all three are wrong on a consumer
screen:

- the validator's raw stdout+stderr, path-stripped and truncated to `VALIDATE_OUTPUT_MAX_CHARS`
  (`drafts.py:535,544-548`);
- the literal `make batch-validate failed` when that output is empty (`drafts.py:566`);
- **an empty string.** `bot.py:6051` returns `Outcome(False, "invalid", "")` from inside
  `_do_confirm`, which `AppRuns.confirm` calls, and `bot.py:6048-6050` explains why it is empty:
  `_render_and_validate` "already sent the specific reason" — to Telegram. So a phone confirm on that
  path renders a red banner with no text at all. This was not in the handoff's description of the
  item and is the strongest argument for fixing it.

`bot.py:6064` is a fourth case: `_refuse(…, "invalid", "this manifest did not pass
\`make batch-validate\`, and its output was sent above — nothing will run. Fix what it named and
send the file(s) again.")`, which tells a phone user to "send the file(s) again" into a chat they are
not in.

### Fix: client-side, in `APIError`

The server's text stays right for Telegram, where the reader is a developer with the transcript
above it. The phone decides what the phone shows. No deploy, no bot risk, and `make ios-test` is the
gate.

- `APIError.userMessage` gains a case ahead of `default`: `case 422 where code == "invalid"` returns a
  fixed human headline. Proposed: `"This draft didn't pass validation, so it can't run yet."`
- A new `public var detailMessage: String?` returns the server's raw text for `.server(422,
  "invalid", …)` and `nil` for everything else — **and `nil` when that text is blank**, so
  `bot.py:6051`'s empty message produces no empty disclosure.
- `ErrorBanner` (`StatusViews.swift:34-51`) wraps its existing `HStack` in a `VStack` and renders the
  detail in a `DisclosureGroup` labelled "Details" when `detailMessage != nil`. `nil` for every other
  error, so **every other `ErrorBanner` call site renders exactly as it did** and the Retry button keeps
  its place. There are eleven of them (`GpuPickerView`, `PodView` ×2, `MigrateSheet`, `RunFlowView`,
  `NewJobView` ×2, `OutputPlayerView`, `OutputsView`, `RunsView`, `RunDetailView`), measured
  2026-09-24 — this spec's first draft said six, which is the kind of count that should not be written
  down at all, since the property is what matters and the number only drifts.

`ErrorBanner` is the primary surface: `DraftStore` keeps a validation failure in `store.error`
(`DraftStoreTests.swift:246-247`), and `NewJobView.banners` renders it at `:157`.

`RunFlow.drop()`'s catch shows the **headline with no disclosure**, and that is correct rather than an
oversight: there the validation failure is a side effect of the drop, and the drop's own copy already
tells the user to open New Job. But the mechanism is *not* that the path bypasses `APIError` — this
spec's first draft said the catch "sets `message` from a `String`, not an `APIError`", which is wrong.
`RunFlowStore.swift:329` is `message = apiError(error).userMessage`, so the drop path routes through
`userMessage` and does receive the headline; it gets no disclosure because `message` is a `String`
field, so there is no `APIError` left to hand to `ErrorBanner`. The distinction mattered: because the
path does route through `userMessage`, `RunFlowTests.swift:403` pinned the old verbatim behaviour and
broke, making `RunFlowTests.swift` a fifth file in this task. Corrected 2026-09-24 during
implementation.

### The existing test this breaks

`DraftStoreTests.swift:249` asserts `store.message == "Driver video is unreadable."` for a
`.server(status: 422, code: "invalid", …)` — the fixture's message is idealized human copy, unlike
the real server's. That assertion must be updated to the headline, and a new one added for
`store.error?.detailMessage == "Driver video is unreadable."`. `store.error` itself still equals the
full `.server(...)` value, so `:246-247` is unchanged: the raw text is preserved, only its rendering
moves.

`DraftStoreTests.swift:196` asserts a 422 with code **`missing_slots`**, which the new case does not
match, so it is untouched. `missing_slots` is the one 422 whose server copy is already written for a
human, and it must stay verbatim.

## 6. Item 4 — `Fixtures` `mask` → `background`

`Fixtures.pipelines` names `tryon-motion-enhance`'s optional role `mask`. The live catalog says
`background`, and `mask` occurs nowhere in `scripts/**` as a role — the server's pipelines use
`background` (`scripts/batchlib/pipelines.py:54,81`). Phase 6 §6 already corrected the plan's ruling
text on this; the fixture is what is left.

Eight lines across seven sites: `Fixtures.swift:159,160,170` — 159 and 160 are inside one
`tryon-motion-enhance` object, so they are a single substitution block; `ModelsTests.swift:176,192,254`;
`RunFlowTests.swift:33`; `BatchComposerTests.swift:102`. This spec's first draft said "Seven sites" and
then enumerated eight. The line numbers are also pre-Task-3 and pre-Task-5, so find each site by its
quoted string rather than by number.

`roles["background"]` keeps the kind `"future_kind"`, so `ModelsTests.swift:176`
(`catalog.pipelines[1].roles["background"] == .unknown`, inside `pipelineAndDraftModelsDecode`) still tests what it was written to test — that an
unknown role *kind* decodes as `.unknown`. The role's name was never the subject.

**The recorded blocker does not fire.** Phase 6 could not edit `Fixtures.swift` because three suites
do exact `.replacingOccurrences` surgery on a fixture's text. There are **nine** call sites and none
of their anchors contains `mask`. This spec's first draft listed six, then eight; the ninth is the one
that matters most, because it operates on a string this change edits — `RunFlowTests.swift:59-60`
builds `draftAfterDelete` from `Routes.draftJSON`'s output, and `draftJSON` is the function whose
`"optional"` array was renamed. Had that edit dropped `"validated":true`, `draftAfterDelete` would have
silently equalled `draftAfterDrop` and the downstream `validated`-null assertions would have passed
vacuously. It did not, but a completeness check scoped to three files would never have noticed:

| Suite | Anchors |
|---|---|
| `TryonLibraryStoreTests.swift:29,32` | `"driver":null}}]` · `"jobs":1,"estimate_min":null}` |
| `DraftStoreTests.swift:399,403` | `"generation":4` · `"estimate_min":null}` |
| `DraftStoreTests.swift:131,152` | `"stale":false` (twice, on `Fixtures.validatedDraft`) |
| `RunFlowTests.swift:59-60` | `"validated":true` (on `Routes.draftJSON`'s output — **the string this change edits**) |
| `ModelsTests.swift:231,233` | `"estimate_min":null}` · `"provider":"gemini","slots":{"character"` |

No behaviour depends on the name either: `BatchComposer.supports` filters on pipelines whose
`required ∪ optional` contains both `character` and `outfit`, and `tryon-motion-enhance` carries both
in `required`.

## 7. Item 5 — a `Clear` in Batch mode

`NewJobView.editor`'s if/else puts `editorActions(draft)` — the only `Clear` — and `readiness(draft)`
in the Single arm alone (`NewJobView.swift:108-115`, `:202`, `:219`). Clearing the draft from Batch
mode means switching to Single first, which is not theoretical: `Phase6SmokeTests` has to do exactly
that twice.

The `Clear` button is extracted from `editorActions` into its own `clearAction` computed property and
rendered in **both** arms, with the identical style, the identical `"Clear"` label and `role:
.destructive`, and the identical `.disabled(store.isBusy || composer.isRunning)`. `editorActions`
keeps calling it, so there is one implementation and the two arms cannot drift. A computed property,
not a method taking `draft`: the button needs nothing from the draft, and an unused parameter is one
the next reader has to reason about. This spec's first draft wrote `clearAction(_ draft: Draft)`; the
plan corrected it, the implementation followed the plan, and the disagreement was reported rather than
resolved silently.

**`readiness(draft)` stays Single-only**, with a comment saying why: it reports `draft.required` and
`draft.missing` for the *edited job*, and the Batch arm does not render the edited job — it renders
shared slots plus an outfit multi-select. A "2 of 3 required slots assigned" line under a cross-build
form describes a job the user is not looking at.

### The smoke change, and the trap in it

Both `Phase6SmokeTests` call sites drop their mode switch: the fewer-than-two-outfits skip path
(`:63-68`) and the final clear (`:105-113`). Each keeps its `app.buttons["Done"].tap()` where it has
one, and keeps a `Phase4Draft.waitUntil` on `isEnabled` before tapping — `revealButton` only waits
for `isHittable`, and a tap on a disabled SwiftUI control is a silent no-op. The wait moves from the
mode picker's "Single" segment to the `Clear` button itself.

**`Phase4Draft.clear(in:)` cannot be called from Batch mode.** Its post-condition is
`app.staticTexts["0 of 3 required slots assigned"]` / `"0 of 2 …"` (`Phase4SmokeTests.swift:56-60`) —
the readiness line, which is Single-only and stays Single-only. Clearing from Batch must assert
something Batch renders: the header's `Text("\(draft.jobs) jobs")` (`NewJobView.swift:149`), i.e.
`app.staticTexts["0 jobs"]`, which the smoke already uses as its precondition. So `Phase6SmokeTests`
gets a small local `clearFromBatch(in:)` helper rather than reusing `Phase4Draft.clear(in:)`.
`Phase3SmokeTests.swift:67,119-120` stay as they are; they run in Single mode and their post-condition
is still correct.

## 8. Testing and acceptance

**Server (`scripts/tests/`, `make batch-test`).** In `TestAppPodResume`:

- stamp equals the current generation → `202 started`, `start_drain` called;
- stamp differs → `409 stale_run`, the new sentence, `start_drain` **not** called;
- no stamp → allowed, `start_drain` called (the fail-open, asserted rather than assumed);
- a corrupt stamp file → allowed, no exception (the fail-quiet load);
- the stamp check runs *before* `_gpu_mismatch` and `_do_resume`, so a refused resume leaves the
  provision-failure file in place for a later, valid retry.

In `test_batch_control_botruns.py`:

- both accepted confirm branches write the stamp;
- a refused confirm writes nothing. Tested for `stale_panel` and `not_validated`. **`bot_busy` is not
  tested, by ruling:** its early return (`bot.py:7009-7012`, after `self.idem.forget(…)`) is three
  lines above the write and never reaches `if out:`, so it cannot stamp; arranging a `_bot_locked()`
  timeout in a test costs more than that coverage is worth. Verified structurally during Task 2's
  review rather than left assumed;
- **the confirm's own `clear()` leaves the stamp matching the draft, so an immediate retry is
  allowed.** Asserted through the real `_resume_generation_refusal` rather than as a bare number
  comparison, and seeded from a non-zero generation so that a reset and a no-op are distinguishable.
  This is the test that catches the §2 trap from either side: a future change to what `clear()` does
  to the counter, or a future move of the read back above the clears, both silently break Retry
  rental while every other gate stays green. The trap is not hypothetical — this spec fell into it
  once already, and only an implementer's measurement caught it.

Plus a `_save`/`_load` round-trip pair mirroring the existing kill-result tests, including
`_load` returning `None` for a corrupt file.

**App (`ios/MotionKit`, `swift test`).** `MigrateFlowTests`: migrate refused while
`runFlow.isDropping`; `canMigrate` false while dropping and true once the drop ends; `recheck` and
`replayPendingOnce` unaffected by a drop. `APIClientTests.userMessages()` (`:295`): the 422
`invalid` headline; `detailMessage` non-nil for it and nil for every other error; `detailMessage` nil
when the server's text is blank; `missing_slots` still verbatim. `DraftStoreTests`: update `:249`,
add the `detailMessage` assertion. `RunFlowTests`: the relaunch case — a fresh `RunFlow` with
`confirmedGeneration == nil` sends the resume, receives `409 stale_run`, surfaces the server's
sentence and calls `refreshTryon()`. That is the regression test for the bug §3 exists to fix, and it
is the one that fails today.

`Fixtures`/`ModelsTests`/`RunFlowTests`/`BatchComposerTests`: the §6 rename, with the suite count
unchanged at 249 before this branch's own additions.

**Gates, in order.** `make batch-test` → `cd ios/MotionKit && swift test` → `make ios-build` →
`xcodebuild -scheme MotionApp -destination 'generic/platform=iOS Simulator' build-for-testing` →
`motions-studio/setup/scrub-secrets.sh --check` → the VPS pre-merge check → merge → the deploy
workflow → live `make ios-contract` and `make ios-ui-test`.

`make ios-ui-test` must run outside the command sandbox (simulator control is killed inside it), and
its exit code must be checked explicitly rather than through a pipe — `cmd 2>&1 | tail` reports
`tail`'s status. Read the bundle with `xcrun xcresulttool get test-results summary` and `… tests`, and
confirm `skipped: 0`: `-quiet` prints no per-test output, so a skipped case is invisible otherwise.
It stays zero-spend (`-UITestRecordingSpendGate`, closing `uitest.recordedSpends == "0"` assertion).
No command in this effort sends Phase A, confirm or kill.

`make ios-contract` stays 14/14: §3 adds no route and no response field, so the contract is unchanged
in both deploy orders.

## 9. Docs to amend

- `docs/superpowers/specs/2026-09-21-vps-control-plane-api-design.md` §5.9, in place, as each slice
  has: the "`resume` is for a failed rental only" bullet gains the third gate — an app-confirm stamp,
  `409 stale_run` on mismatch, fail-open when absent because a Telegram confirm writes none. The
  route table at `:185` is unchanged: no new field, no new route.
- `docs/superpowers/swiftui-app-progress.md`: the four follow-up entries this closes are replaced by
  what shipped, the `MigrateFlow` residual entry loses its "Residual" qualifier, the Batch-mode
  `Clear` papercut entry goes, and the "Next work" list is rewritten. The gate record gains this
  branch's rows. The `Phase6SmokeTests` "switch to Single before clearing" evidence in §"Notes on the
  `make ios-ui-test` run" is now historical and must be re-worded, not deleted — it was the proof the
  papercut was real.
- This file's gate record.

## 10. Recorded limits

- The latch covers **app-initiated** confirms only. A rental confirmed from Telegram and retried from
  the phone is unchecked, because `_do_confirm` cannot certify a generation the Telegram user never
  reviewed. Same coverage as the client latch it replaces.
- The stamp is per chat, so one slot's confirm overwrites another's. That matches the existing
  one-run-slot model (`AppPod.run_id` is `_job_manifest_path(chat_id).stem`, one per chat) and every
  other `tg-<chat_id>.*` record.
- Item 3 changes what the phone shows, not what the server sends. Telegram's copy is untouched, and a
  future non-app client would still receive the raw text.
- §4's reverse direction (a drop starting during a migration) is left open, with the reason stated
  there.
- **The stamp write is not atomic with the confirm's `clear()`, and the residual race fails open.**
  `confirm` holds `_bot_locked()`, but `DraftStore` writers take `control.LOCK` instead, and
  `scripts/httpapi/server.py` contains **zero** BOT_LOCK references — so a phone `PATCH /v1/draft`
  running concurrently with a confirm takes no lock this code holds. `_changed` is the only place
  `generation += 1` happens (`drafts.py:277`) and it has four callers — `patch` (`:458`),
  `add_to_batch` (`:474`), `drop_from_batch` (`:483`) and `clear` (`:490`) — all reachable without
  BOT_LOCK. `clear()` and the stamp's `runnable()` read are two *separate* `control.LOCK` acquisitions,
  so a concurrent mutation can land between them and the stamp then records the post-mutation
  generation, letting a later `resume` through against a draft edited after the confirm.

  Left open deliberately. The window is two adjacent statements with no I/O between them beyond the
  writes already there; firing it needs a *second* concurrent client, because the phone serialises its
  own spends (`RunFlow.spend` sets `isBusy`, which drives `canSpend`); and the pre-existing
  `panel_token()` compare has the identical shape, so closing only this one would buy a guarantee the
  confirm path as a whole does not have. It is closable if that judgment is ever revisited: `clear()`
  already returns the generation it persisted, inside its own single `control.LOCK` acquisition
  (`_view`, `drafts.py:322`), so capturing that value in both accepted branches instead of re-reading
  would remove the window entirely — at the cost of the one-read-one-write-one-place shape that makes
  the writer auditable. Recorded 2026-09-24 during Task 2's fix round, after the review understated
  the exposure as one non-bumping writer and the implementer measured four bumping ones.
