# Phase 6 Follow-ups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the rental-retry generation latch server-side so it survives an app relaunch, and close the four app-side gaps Phase 6 recorded but could not fix on its own branch.

**Architecture:** One branch, one PR. The money change is entirely server-side: an accepted `confirm` persists the draft's `generation` to a per-chat JSON stamp, and `AppPod.resume` refuses `409 stale_run` when the draft has moved since — so no app change is needed for correctness and both deploy orders work. The four app changes are independent of it and of each other: a drop guard on `MigrateFlow.migrate()`, human copy for a `422 invalid`, a test-fixture role rename, and a `Clear` button in Batch mode.

**Tech Stack:** Python 3 stdlib (`unittest`, `unittest.mock`) for `scripts/tgbot/bot.py`; Swift 6 with swift-testing (`@Suite`, `#expect`, `StubURLProtocol`) for `ios/MotionKit`; SwiftUI on iOS 26 for `ios/MotionApp`; XCUITest for `ios/MotionAppUITests`.

**Spec:** `docs/superpowers/specs/2026-09-24-phase-6-follow-ups-design.md` — read it first. §2 pins the four server facts the whole design rests on, and §3 records the two approaches that were rejected and why. The plan argues from the spec; do not re-derive it.

## Global Constraints

- **Every command runs from the root of the checkout you are working in.** Set `REPO=$(git rev-parse --show-toplevel)` once per shell before the first command that uses `"$REPO"`. This branch is implemented in a git worktree, so a hardcoded absolute path would run the gate against the *main* checkout and report another tree's result as this branch's. Added after Task 1: the plan originally hardcoded the main checkout's path in all 37 command sites, and the implementer had to override it by hand.
- Write in English: code, comments, commit messages. Much of the existing repo is Vietnamese; that is legacy, not a pattern to copy.
- No `# #region ALD <DD/MM/YYYY> - …` markers. Explain **why**, with the number that was measured and the date.
- Four-space indent in Python **and in Swift** (verified 2026-09-23: 63 four-space lines vs 0 two-space in `DraftStore.swift`).
- `snake_case` Python, `camelCase` Swift members, `PascalCase` Swift types. Python test methods are `test_<scenario>`.
- Run `motions-studio/setup/scrub-secrets.sh --check` before every commit; it must exit 0. Stage only the exact files the task names — never `git add -A`.
- Never commit `ios/Secrets.xcconfig`, `.env`, or `ios/MotionApp.xcodeproj`.
- Commit messages end with the trailer `Co-Authored-By: Qwen Code <noreply@qwen.com>`.
- **No command in this plan sends Phase A, confirm, kill, migrate or resume to the live server. No pod is rented. No try-on provider is called.** Every spend path is exercised through `FakeSpendGate` or a patched `start_drain`.
- Money-path test rule: a refusal is only proven by asserting the money call was **not** made — `self.patches["start_drain"].assert_not_called()` in Python, `#expect(await gate.intents.isEmpty)` (or `.allSatisfy { $0.kind != … }`) in Swift. A status code alone is not evidence.
- `make ios-ui-test` must run outside the command sandbox. Check its exit code explicitly (`make ios-ui-test > /tmp/x.log 2>&1; echo $?`) — piping to `tail` reports `tail`'s status.
- Views never call `APIClient` directly. MotionKit stays SwiftUI-free.
- Do not edit `docs/superpowers/swiftui-app-progress.md` on this branch. It is amended after the merge, once the live gate results exist.

## Review Focus

The spec is a vision document; its silence on an input is not permission for that input to break the program. These five are the ones no task's obvious tests cover, most likely to bite first. Each names the test that pins it.

1. **A leftover app stamp against a Telegram-initiated confirm.** The stamp is per chat and never deleted, and `_do_confirm` writes none — so a stamp from an app confirm three days ago is still on disk when the phone retries a rental the user confirmed from Telegram. Expected: allowed when the draft's generation has not moved (Telegram never touches the app draft), refused when it has. → Task 1, `test_a_leftover_stamp_allows_a_resume_the_draft_has_not_moved`.
2. **A corrupt draft file.** `_load`'s `except` branch moves the file aside and returns generation 0 (`drafts.py:265`), so a stamp of 5 no longer matches. Expected: resume refused — fail closed on a draft this box cannot read, for a money call. → Task 1, `test_a_corrupt_draft_refuses_the_resume`.
3. **An empty `invalid` message.** `bot.py:6051` returns `Outcome(False, "invalid", "")`, so today the phone renders a red banner with no text. Expected: the headline shows and **no** empty disclosure appears. → Task 5, the `message: ""` and `message: "  \n "` assertions inside `APIClientTests.userMessages`.
4. **A pending migration stranded by the new drop guard.** `canMigrate` gains a term; if `recheck()` or `replayPendingOnce()` consulted it, an unanswered migrate could become unresolvable while a drop runs. Expected: both still work during a drop. → Task 4, `recheckAndReplayAreNotBlockedByADrop`.
5. **A silently no-op'd fixture rename.** Three suites do exact `.replacingOccurrences` surgery on `Fixtures.draft`; if an anchor stopped matching, the replacement would no-op and a test could pass vacuously. Expected: the suite count is unchanged and `usersListsBasketRunsBeforeTheEditedJob` still fails loudly if its anchor breaks. → Task 6, step 5's count check.

---

### Task 1: Server — `resume` refuses when the draft moved since the confirm

**Files:**
- Modify: `scripts/tgbot/bot.py` — new helpers after `_load_kill_result` (`:7274`), `AppPod.__init__` (`:7288`), `AppPod.resume` (`:7413`), the `AppPod` construction at `:7789`
- Test: `scripts/tests/test_batch_control_botpod.py` — imports (`:22`), `_PodFixture.setUp` (`:47-72`), `TestKillSurvivesARestart` (`:387`, `:395`, `:400`), `TestAppPodResume` (`:412+`)

**Interfaces:**
- Consumes: `DraftStore.runnable() -> tuple[list[Job], bool | None, int]` (`drafts.py:345-353`) — the third element is the generation. `Outcome(ok, code, message)` (`control/runs.py`), truthy when it started something. `status_for` maps an unknown code to 409.
- Produces, for Task 2 and Task 3:
  - `_confirm_stamp_path(chat_id: int) -> Path`
  - `_save_confirm_stamp(chat_id: int, generation: int) -> None`
  - `_load_confirm_stamp(chat_id: int) -> int | None`
  - `_resume_generation_refusal(chat_id: int, drafts: DraftStore) -> str | None`
  - `AppPod.__init__(self, tg: Tg, chat_id: int, drafts: DraftStore, idem: IdempotencyStore)`
  - `RESUME_STALE_GENERATION: str` — the refusal sentence, so Task 3's app-side test and the server assert one literal.

- [ ] **Step 1: Write the failing tests**

In `scripts/tests/test_batch_control_botpod.py`, add the two imports next to the existing `control` ones (after `:22`):

```python
import control.drafts as drafts
from control.tryon_library import TryonLibrary
```

In `_PodFixture.setUp`, replace the last line (`self.pod = bot.AppPod(self.tg, ME, self.idem)`) with a real `DraftStore` and the new argument order:

```python
        self.idem = IdempotencyStore(self.root / "batch" / "idempotency")
        # `AppPod.resume` reads the draft's generation to compare against the
        # confirm stamp, so the fixture needs a real store over the same temp
        # batch/ — the same construction `_AppRunsFixture` uses. Not faked: a
        # stub generation would let the fail-closed case pass for the wrong
        # reason.
        staging = self.root / "batch" / "tg-staging"
        staging.mkdir(parents=True, exist_ok=True)
        self.store = drafts.DraftStore(
            self.root / "batch", staging, "app",
            default_pipeline="motion-enhance", default_provider="gemini",
            tryon_library=TryonLibrary(self.root / "batch" / "tryon-library", "app"))
        self.pod = bot.AppPod(self.tg, ME, self.store, self.idem)
```

Update the three other `AppPod` constructions in the same file to pass `self.store` (all three are inside `_PodFixture` subclasses, so it exists):

- `:387` → `restarted = bot.AppPod(self.tg, ME, self.store, self.idem)`
- `:395` → `self.assertIsNone(bot.AppPod(self.tg, ME, self.store, self.idem).last_kill)`
- `:400` → `self.assertIsNone(bot.AppPod(self.tg, ME, self.store, self.idem).last_kill)`

Add a new test class immediately after `TestAppPodResume`'s existing methods (keep the class; append to it):

```python
    # -- the confirm stamp (2026-09-24 follow-ups spec §3) ------------------

    def test_stamp_round_trips_and_a_corrupt_one_is_none(self):
        self.assertIsNone(bot._load_confirm_stamp(ME))       # never written
        bot._save_confirm_stamp(ME, 7)
        self.assertEqual(bot._load_confirm_stamp(ME), 7)
        bot._confirm_stamp_path(ME).write_text("not json", encoding="utf-8")
        self.assertIsNone(bot._load_confirm_stamp(ME))       # fail quiet
        bot._confirm_stamp_path(ME).write_text('{"generation":"7"}', encoding="utf-8")
        self.assertIsNone(bot._load_confirm_stamp(ME))       # not an int

    def test_resume_refuses_when_the_draft_moved_since_the_confirm(self):
        self._seed_failure()
        bot._save_confirm_stamp(ME, 4)
        d = self.store._load()
        d.generation = 5          # a drop, a PATCH, an add-to-batch — anything
        self.store._save(d)
        status, body = self.pod.resume(self.pod.run_id, self._body(), "k1")
        self.assertEqual(status, 409)
        self.assertEqual(body["error"]["code"], "stale_run")
        self.assertEqual(body["error"]["message"], bot.RESUME_STALE_GENERATION)
        self.patches["start_drain"].assert_not_called()
        # The failure record survives a refusal, so a later valid retry still
        # has something to retry.
        self.assertTrue(provision_failure_path(self._live()).exists())

    def test_resume_allowed_when_the_stamp_matches(self):
        self._seed_failure()
        generation = self.store.runnable()[2]
        bot._save_confirm_stamp(ME, generation)
        status, body = self.pod.resume(self.pod.run_id, self._body(), "k1")
        self.assertEqual((status, body), (202, {"run_id": self.pod.run_id,
                                                "outcome": "started"}))
        self.patches["start_drain"].assert_called_once()

    def test_resume_allowed_when_there_is_no_stamp(self):
        """Fail open, deliberately: a Telegram-initiated confirm writes no
        stamp, and `_do_confirm` must not write one because it is shared with
        the Telegram flow where the app's draft is not what the user reviewed.
        Refusing here would break retrying a Telegram rental from the phone."""
        self._seed_failure()
        self.assertIsNone(bot._load_confirm_stamp(ME))
        d = self.store._load()
        d.generation = 99
        self.store._save(d)
        self.assertEqual(self.pod.resume(self.pod.run_id, self._body(), "k1")[0], 202)
        self.patches["start_drain"].assert_called_once()

    def test_a_leftover_stamp_allows_a_resume_the_draft_has_not_moved(self):
        """Review Focus 1. The stamp is per chat and never deleted, so an app
        confirm from days ago is still on disk when the phone retries a rental
        the user has since confirmed from Telegram. Telegram never touches the
        app draft, so the generation has not moved and the retry is legitimate."""
        self._seed_failure()
        generation = self.store.runnable()[2]
        bot._save_confirm_stamp(ME, generation)
        bot._STATE[ME] = self._job("telegram")     # a Telegram-side draft
        self.assertEqual(self.pod.resume(self.pod.run_id, self._body(), "k1")[0], 202)
        self.patches["start_drain"].assert_called_once()

    def test_a_corrupt_draft_refuses_the_resume(self):
        """Review Focus 2. `_load`'s except branch moves a corrupt draft aside
        and returns generation 0 (drafts.py:265), so the stamp no longer
        matches. Fail closed: this is a money call about a draft this box can
        no longer read."""
        self._seed_failure()
        bot._save_confirm_stamp(ME, 5)
        self.store.path.write_text("{not json", encoding="utf-8")
        status, body = self.pod.resume(self.pod.run_id, self._body(), "k1")
        self.assertEqual(status, 409)
        self.assertEqual(body["error"]["code"], "stale_run")
        self.patches["start_drain"].assert_not_called()

    def test_the_stamp_check_runs_before_the_gpu_check(self):
        """Ordering: both are 409 staleness, and the draft one is the more
        fundamental refusal. A gpu mismatch must not mask it, or the user is
        told to re-read the pod when re-confirming is what is needed.

        The .env write and the mismatched gpu are the same pair
        `test_resume_with_a_different_gpu_is_stale_panel_and_spends_nothing`
        uses, so the gpu check genuinely would have fired had it been reached —
        without that, this test would pass for the wrong reason."""
        (self.root / ".env").write_text("GPU=NVIDIA GeForce RTX 4090\n", encoding="utf-8")
        self._seed_failure()
        bot._save_confirm_stamp(ME, 4)
        d = self.store._load()
        d.generation = 5
        self.store._save(d)
        status, body = self.pod.resume(
            self.pod.run_id, self._body(gpu="NVIDIA GeForce RTX 5090"), "k1")
        self.assertEqual(status, 409)
        self.assertEqual(body["error"]["code"], "stale_run")     # not stale_panel
        self.patches["start_drain"].assert_not_called()
```

`self.store.path` is the draft file (`drafts.py:238`, `batch_dir / f"{owner}.draft.json"`), which is what `_save` writes and `_load` reads — so corrupting it exercises the real recovery branch.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd "$REPO" && python3 -m unittest scripts.tests.test_batch_control_botpod -v 2>&1 | tail -30`

Expected: FAIL — `AttributeError: module 'tgbot.bot' has no attribute '_load_confirm_stamp'`, and every `bot.AppPod(self.tg, ME, self.store, self.idem)` call failing with `TypeError: AppPod.__init__() takes 4 positional arguments but 5 were given`.

- [ ] **Step 3: Write the stamp helpers and the refusal predicate**

In `scripts/tgbot/bot.py`, after `_load_kill_result` (which ends at `:7274`) and before `class AppPod` (`:7277`):

```python
# The refusal sentence, one literal: the app's pre-tap copy in
# RunFlow.retryRentalBlockReason says the same thing, and a test on each side
# asserts this string rather than a paraphrase of it.
RESUME_STALE_GENERATION = ("the draft changed since this rental was confirmed — "
                           "Confirm again to rent what is in the draft now")


def _confirm_stamp_path(chat_id: int) -> Path:
    """Where the generation an app confirm was accepted at survives a bot
    restart, for `resume` to compare the draft against.

    `_run_token` alone cannot do this job: it is the manifest's mtime_ns
    (`_manifest_token`), which moves only when a manifest is *rewritten*, and a
    draft edit rewrites nothing. That is why `panel_token` joins the generation
    for `confirm` — and why `resume`, which re-rents the manifest on disk and
    deliberately never reads the draft, had no equivalent. Without this stamp
    the only latch was `RunFlow.confirmedGeneration` in the app's memory, which
    an app relaunch loses (2026-09-24 follow-ups spec §3).
    """
    return ROOT / "batch" / f"tg-{chat_id}.confirmed-generation.json"


def _save_confirm_stamp(chat_id: int, generation: int) -> None:
    path = _confirm_stamp_path(chat_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"generation": generation}), encoding="utf-8")
    tmp.replace(path)   # atomic: a reader never sees a half-written stamp


def _load_confirm_stamp(chat_id: int) -> int | None:
    """None means "no stamp to honour" — including a corrupt file, for
    `_load_kill_result`'s reason: a bad answer here must not take the route
    down, and the caller fails open (see _resume_generation_refusal)."""
    try:
        raw = json.loads(_confirm_stamp_path(chat_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    generation = raw.get("generation") if isinstance(raw, dict) else None
    return generation if isinstance(generation, int) else None


def _resume_generation_refusal(chat_id: int, drafts: DraftStore) -> str | None:
    """Why `resume` must not re-rent, or None when it may.

    Fails OPEN when there is no stamp: a Telegram-initiated confirm writes
    none, and `_do_confirm` must not write one because it is shared with the
    Telegram flow, where the app's draft is not what the user reviewed. So this
    covers app-initiated confirms only — the same population the app's own
    in-memory latch covered, moved where a relaunch cannot lose it.

    Comparing generations rather than draft contents is what makes this work at
    all: `confirm` clears the draft on acceptance (both branches), so a
    content comparison like `_phase_a_matches_draft` would answer False always
    and Retry rental would never fire. `clear()` counts the generation UP by
    one (`_changed`, drafts.py:275-279) rather than resetting it, and the
    writer stamps after the clear (AppRuns.confirm's `if out:`), so the
    confirm's own clear is already accounted for; `validate()` does not bump it
    (drafts.py:555), so a re-validate does not trip this either — and the
    counter only rises, so a stale stamp can never match again by accident.
    """
    confirmed = _load_confirm_stamp(chat_id)
    if confirmed is None:
        return None
    if drafts.runnable()[2] == confirmed:
        return None
    return RESUME_STALE_GENERATION
```

`DraftStore` is already imported at `bot.py:57` (`from control.drafts import DraftStore, PROVIDER_LABELS`) and `AppRuns.__init__` already annotates with it (`:6888`), so the new helper signatures and `AppPod.__init__` need no new import.

- [ ] **Step 4: Give `AppPod` the draft store and the new gate**

`AppPod.__init__` (`:7288`) becomes:

```python
    def __init__(self, tg: Tg, chat_id: int, drafts: DraftStore, idem: IdempotencyStore):
        self.tg, self.chat_id, self.drafts, self.idem = tg, chat_id, drafts, idem
```

Leave the rest of `__init__` (`last_kill`, `_kill_thread`, `_migrate_ask`) untouched.

In `AppPod.resume`, insert one `elif` between the `run_token` check (`:7413-7415`) and the `read_provision_failure` check:

```python
            if body.get("run_token") != _run_token(self.chat_id):
                out = Outcome(False, "stale_run",
                              "the run changed since it was read — read it again")
            elif (stale := _resume_generation_refusal(self.chat_id, self.drafts)) is not None:
                # The second of two staleness rules, kept adjacent to the first
                # so they read as one idea: `run_token` catches a rewritten
                # manifest, this catches a draft that moved without one.
                out = Outcome(False, "stale_run", stale)
            elif read_provision_failure(provision_failure_path(manifest_path)) is None:
```

Also update `resume`'s docstring: it currently says the route has "Two gates the Telegram buttons get for free". Make it three, and say why the third has no Telegram equivalent:

```python
        Three gates. Two are the ones the Telegram buttons get for free from
        being drawn only under a failure card: an outstanding
        `provision-failed.json` (without it, "resume" on a finished batch rents
        a pod to do nothing) and the run's current token (the manifest must not
        have changed since the phone read it). The third is the confirm stamp —
        the draft must not have changed since the confirm that wrote this
        manifest, which no Telegram button needs because Telegram's recovery
        buttons are redrawn on every manifest write and so cannot go stale the
        way a phone screen left open can. Fails open when no app confirm ever
        wrote one; see _resume_generation_refusal.

        The app's draft is deliberately left alone — a resume is about a
        manifest that was confirmed long ago. Reading its generation is not
        reading the draft's contents.
        """
```

Finally, the construction site at `:7789`:

```python
        server.app_pod = AppPod(tg, chat_id, server.drafts, idem)
```

`server.drafts` is in scope — `:7785` already passes it to `AppRuns`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd "$REPO" && python3 -m unittest scripts.tests.test_batch_control_botpod 2>&1 | tail -15`

Expected: `OK`. Then the whole suite and the invariants, which pin where the money calls may be started from:

Run: `cd "$REPO" && make batch-test 2>&1 | tail -15`

Expected: `OK` (possibly `OK (skipped=1)`). `test_batch_control_invariants` must stay green — this task adds no `start_drain` call site.

- [ ] **Step 6: Commit**

```bash
cd "$REPO"
motions-studio/setup/scrub-secrets.sh --check
git add scripts/tgbot/bot.py scripts/tests/test_batch_control_botpod.py
git commit -m "Retry rental: refuse a resume whose draft moved since the confirm" -m "resume re-rents the manifest on disk and never reads the draft, and _run_token only moves when a manifest is rewritten - so a free draft edit left Retry rental offering to rent a pod that still ran the job the user had just dropped. The app's in-memory latch closed that, and an app relaunch lost it.

An accepted confirm now stamps the draft generation, and resume refuses stale_run when the draft has moved. Fails open with no stamp: a Telegram confirm writes none, and _do_confirm cannot write one because the app draft is not what a Telegram user reviewed.

Co-Authored-By: Qwen Code <noreply@qwen.com>"
```

---

### Task 2: Server — an accepted confirm writes the stamp

> **CORRECTION, found during this task's implementation (2026-09-24).** Step 3 below instructs the
> implementer to read the generation near the top of the locked block, on the stated ground that
> "`clear()` preserves the generation, so reading it anywhere inside that block gives the same
> number." **That ground is false.** `clear()` (`drafts.py:485-490`) seeds `_fresh` with the current
> number and then returns through `_changed`, which does `generation += 1` — it increments. Measured:
> two clears on a fresh store give 0 → 1 → 2. Following Step 3 literally stamps `G` while the confirm
> leaves the draft at `G+1`, so `_resume_generation_refusal` refuses **every** legitimate app Retry
> rental. Fail-closed, so never a wrong spend, but the feature would be silently dead for every user.
>
> **The correct placement is inside the `if out:` block, after both `clear()` calls** — still one
> read, one write, one place, still under `BOT_LOCK`. That is what shipped. Spec §2 and §3 carry the
> corrected fact and the reasoning; Task 1's `_resume_generation_refusal` docstring repeated the false
> claim and was corrected in the same commit. The trap-catcher test in Step 1 below is corrected in
> place, because it is the one a re-runner would copy.

**Files:**
- Modify: `scripts/tgbot/bot.py` — `AppRuns.confirm` (`:6998-7048`)
- Test: `scripts/tests/test_batch_control_botruns.py` — append to the `AppRuns.confirm` test class

**Interfaces:**
- Consumes: `_save_confirm_stamp(chat_id, generation)` and `RESUME_STALE_GENERATION` from Task 1; `_PodFixture`'s `self.store` and `self.pod` for the end-to-end half.
- Produces: nothing new. After this task the stamp has a writer, so Task 1's gate is live rather than test-only.

- [ ] **Step 1: Write the failing tests**

Append to the `AppRuns.confirm` test class in `scripts/tests/test_batch_control_botruns.py` (the one containing `test_confirm_after_phase_a_resumes`, `:437`):

```python
    # -- the confirm stamp (2026-09-24 follow-ups spec §3) ------------------

    def test_an_accepted_fresh_confirm_stamps_the_generation(self):
        self._seed_draft()
        before = self.store.runnable()[2]
        with mock.patch("tgbot.bot._do_confirm",
                        return_value=Outcome(True, "started")) as do_confirm:
            status, _body = self.runs.confirm(self.runs.run_id, self._body(), "k1")
        self.assertEqual(status, 202)
        do_confirm.assert_called_once()
        self.assertEqual(bot._load_confirm_stamp(ME), before)

    def test_an_accepted_resume_branch_confirm_stamps_the_generation(self):
        """The other accepted branch (bot.py:7017-7029). It reaches `_do_resume`
        rather than `_do_confirm`, and it clears the draft too, so it must stamp
        as well — otherwise a rental confirmed straight after Phase A has no
        latch at all."""
        job = self._seed_draft()
        self._seed_journal(job)
        bot._PHASE_A_OFFERED[ME] = bot._run_token(ME)
        before = self.store.runnable()[2]
        with mock.patch("tgbot.bot._do_resume",
                        return_value=Outcome(True, "started")) as do_resume:
            status, _body = self.runs.confirm(self.runs.run_id, self._body(), "k2")
        self.assertEqual(status, 202)
        do_resume.assert_called_once()
        self.assertEqual(bot._load_confirm_stamp(ME), before)

    def test_the_confirms_own_clear_leaves_the_stamp_matching_the_draft_so_a_retry_is_allowed(self):
        """The trap in spec §2, pinned from both sides. `confirm` clears the
        draft on acceptance (bot.py:7029 and :7039) and `clear()` INCREMENTS the
        generation (drafts.py:485-490, via `_changed`) — so the stamp matches
        only if it is read after the clears. Seeded from 7, never from 0: from 0
        a reset and a no-op are the same number and this would pass under a
        broken `clear()` too. Asserted through the real predicate rather than as
        a bare number comparison, so a future change to either half fails here
        instead of silently breaking Retry rental for every user."""
        self._seed_draft()
        d = self.store._load()
        d.generation = 7
        self.store._save(d)
        before = self.store.runnable()[2]
        self.assertEqual(before, 7)     # the seed took; a silent 0 would make
                                        # a reset and a no-op the same number
        # `_body()` reads `panel_token()`, which carries the generation — so it
        # must be evaluated after the seeding above, not before it.
        with mock.patch("tgbot.bot._do_confirm",
                        return_value=Outcome(True, "started")):
            self.assertEqual(self.runs.confirm(self.runs.run_id, self._body(), "k3")[0], 202)
        self.assertEqual(self.store.runnable()[0], [])          # the draft really is empty
        # `before + 1`, not a literal 8: this is the assertion that fails under a
        # reset-to-0 `clear()`, so it pins that the counter only rises — the
        # property `_resume_generation_refusal`'s docstring relies on. It is not
        # the retry invariant itself (the predicate assertion below is), and it
        # will also fire if `clear()` is ever legitimately changed to preserve
        # the generation while the stamp stays consistent.
        self.assertEqual(self.store.runnable()[2], before + 1)  # ...and counted up, not reset
        self.assertEqual(bot._load_confirm_stamp(ME), before + 1)   # the stamp followed it
        # The assertion that matters: through the predicate `resume` actually
        # uses, not a number comparison that could hold while the gate refuses.
        self.assertIsNone(bot._resume_generation_refusal(ME, self.store))

    def test_a_refused_confirm_stamps_nothing(self):
        self._seed_draft()
        body = self._body()
        body["panel_token"] = "not-the-token"
        status, resp = self.runs.confirm(self.runs.run_id, body, "k4")
        self.assertEqual(status, 409)
        self.assertEqual(resp["error"]["code"], "stale_panel")
        self.assertIsNone(bot._load_confirm_stamp(ME))

    def test_a_not_validated_confirm_stamps_nothing(self):
        self._seed_draft(validated=False)
        with mock.patch("tgbot.bot._do_confirm") as do_confirm:
            status, resp = self.runs.confirm(self.runs.run_id, self._body(), "k5")
        self.assertEqual(status, 422)
        self.assertEqual(resp["error"]["code"], "not_validated")
        do_confirm.assert_not_called()
        self.assertIsNone(bot._load_confirm_stamp(ME))
```

And the end-to-end half, in `TestAppPodResume` in `scripts/tests/test_batch_control_botpod.py` — this is the test that proves the two halves are one mechanism rather than two coincidences:

```python
    def test_a_confirm_then_a_moved_draft_refuses_the_retry(self):
        """Confirm (stamp written) → the draft moves → resume refused. The
        stamp is written by AppRuns and read by AppPod, so this is the only
        test that exercises both halves against one real DraftStore."""
        runs = bot.AppRuns(self.tg, ME, self.store, self.idem)
        job = self._job("app")
        d = self.store._load()
        d.job, d.validated = job, True
        self.store._save(d)
        body = {"provider": "runpod", "tryon": None, "panel_token": runs.panel_token()}
        with mock.patch("tgbot.bot._do_confirm",
                        return_value=Outcome(True, "started")):
            self.assertEqual(runs.confirm(runs.run_id, body, "k-confirm")[0], 202)

        self._seed_failure()
        self.assertEqual(self.pod.resume(self.pod.run_id, self._body(), "k-ok")[0], 202)

        # A free draft mutation — the drop Phase 6 added, or any PATCH.
        d = self.store._load()
        d.generation += 1
        self.store._save(d)
        status, resp = self.pod.resume(self.pod.run_id, self._body(), "k-stale")
        self.assertEqual(status, 409)
        self.assertEqual(resp["error"]["code"], "stale_run")
        self.assertEqual(resp["error"]["message"], bot.RESUME_STALE_GENERATION)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd "$REPO" && python3 -m unittest scripts.tests.test_batch_control_botruns scripts.tests.test_batch_control_botpod 2>&1 | tail -25`

Expected: FAIL — `AssertionError: None != 4` on the stamp assertions (nothing writes one yet), and `test_a_confirm_then_a_moved_draft_refuses_the_retry` failing with `409 != 202` on the *first* resume (no stamp) or `202 != 409` on the second, depending on how far it gets.

- [ ] **Step 3: Write the stamp into `AppRuns.confirm`**

In `AppRuns.confirm`, at the existing `if out:` that builds the 202 response (`:7040`) — **after** both `self.drafts.clear()` calls — read the generation and stamp it in one place:

```python
            if out:
                # Accepted, so this is the generation the user agreed to spend
                # on — the value `resume` compares the draft against later.
                # Both accepted branches clear the draft above and neither is
                # reached on a refusal, so one read and one write here covers
                # both and nothing else.
                #
                # Read HERE, after the clears, and not before them: `clear()`
                # routes through `_changed`, which does `generation += 1`
                # (drafts.py:275-279), so a pre-clear read stamps G while the
                # confirm leaves the draft at G+1 — `_resume_generation_refusal`
                # would then refuse every app Retry rental that ever existed.
                # Measured 2026-09-24: two clears on a fresh store give
                # generation 0 → 1 → 2. `clear()` "keeps counting" in the sense
                # of not resetting, not in the sense of standing still.
                #
                # Still one value read under one lock acquisition: BOT_LOCK is
                # held for this whole block, so nothing outside it can move the
                # counter between the clear and this read.
                _save_confirm_stamp(self.chat_id, self.drafts.runnable()[2])
                response = (202, {"run_id": self.run_id, "outcome": out.code})
```

Do not add a second `_save_confirm_stamp` call next to either `self.drafts.clear()`, and do not hoist the read above them; one read and one write at the single acceptance point, after the clears, is the whole reason this is safe to read later.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd "$REPO" && make batch-test 2>&1 | tail -15`

Expected: `OK`. If `test_an_accepted_resume_branch_confirm_stamps_the_generation` fails with `stale_panel` instead of reaching `_do_resume`, the manifest `_seed_journal` wrote does not match the draft's job fingerprints — compare against `test_confirm_after_phase_a_resumes` (`:437`), which sets the same three preconditions and passes.

- [ ] **Step 5: Commit**

```bash
cd "$REPO"
motions-studio/setup/scrub-secrets.sh --check
git add scripts/tgbot/bot.py scripts/tests/test_batch_control_botruns.py scripts/tests/test_batch_control_botpod.py
git commit -m "Confirm: stamp the draft generation it was accepted at" -m "The writer for resume's new gate. One write at the single acceptance point covers both accepted branches and nothing else, so a refused confirm stamps nothing.

Pinned separately: confirm clears the draft on acceptance, and clear() counts the generation UP - so the stamp is read after the clears and a test asserts the two halves still agree through the real predicate. Break either and Retry rental dies for every user while every other gate stays green.

Co-Authored-By: Qwen Code <noreply@qwen.com>"
```

---

### Task 3: App — the relaunch regression, and one sentence for one rule

**Files:**
- Modify: `ios/MotionKit/Sources/MotionKit/Stores/RunFlowStore.swift:96-114` (the `canRetryRental` / `retryRentalBlockReason` doc comments and the block string)
- Test: `ios/MotionKit/Tests/MotionKitTests/RunFlowTests.swift` — append after `aDropAfterAConfirmWithdrawsTheRentalRetry` (`:545`)

**Interfaces:**
- Consumes: the server's `RESUME_STALE_GENERATION` text from Task 1, asserted as a literal here so the two sides cannot drift silently.
- Produces: nothing. `SpendIntent.resume` is unchanged — this task adds no request field, which is the point (spec §3: no app change is needed for correctness).

- [ ] **Step 1: Write the failing test**

Append to `RunFlowTests` in `ios/MotionKit/Tests/MotionKitTests/RunFlowTests.swift`:

```swift
    /// The regression this branch exists for. `confirmedGeneration` is in
    /// memory, so a relaunch loses it and the client latch opens — the server's
    /// confirm stamp is what closes it (2026-09-24 follow-ups spec §3). The
    /// app's job on that path is to surface the refusal verbatim and re-read the
    /// run, not to swallow it and leave the button offering the same tap again.
    ///
    /// The message literal is `RESUME_STALE_GENERATION` in bot.py. Asserting it
    /// here rather than a paraphrase is what keeps the two sides honest: the
    /// server has its own test on the same string.
    @Test func aRelaunchSurfacesTheServersStaleResumeRefusal() async throws {
        let stale = ("the draft changed since this rental was confirmed — "
                     + "Confirm again to rent what is in the draft now")
        let routes = Routes()                       // podIdle carries failed_rental
        let gate = FakeSpendGate([.refused(status: 409, code: "stale_run",
                                           message: stale, panelToken: nil)])
        let flow = make(routes, gate: gate)
        await flow.start(.existing)

        // A relaunch, exactly: no confirm happened during this store's lifetime.
        #expect(flow.confirmedGeneration == nil)
        #expect(flow.canRetryRental)                // the client latch is open
        #expect(flow.retryRentalBlockReason == nil)

        let tryonReads = StubURLProtocol.requests.filter {
            ($0.url?.path ?? "").hasSuffix("/tryon")
        }.count
        await flow.retryRental()

        #expect(await gate.intents.count == 1)      // the tap really was sent
        #expect(await gate.intents.first?.kind == .resume)
        #expect(flow.message == stale)              // verbatim, not swallowed
        #expect(!flow.needsRecheck)                 // a refusal is definitive
        // ("stale_run", .resume) re-reads the run, which is the recovery.
        #expect(StubURLProtocol.requests.filter {
            ($0.url?.path ?? "").hasSuffix("/tryon")
        }.count > tryonReads)
    }
```

- [ ] **Step 2: Run the test to verify it fails or passes for the right reason**

Run: `cd "$REPO/ios/MotionKit" && swift test --filter RunFlowTests 2>&1 | tail -20`

This test is expected to **pass already** — `applyRefusal`'s `("stale_run", .resume)` case shipped in Phase 4. That is the correct outcome and not a wasted test: it is the app-side half of a two-sided contract that no test pinned before, and it is what stops a future refactor of `applyRefusal` from silently dropping the case. Record in the commit message that it passed on arrival.

If it fails, the likely cause is the `/tryon` suffix filter also matching `/tryon-library`. Narrow it to `$0.url?.path?.hasSuffix("/tryon") == true && !($0.url?.path ?? "").contains("library")`.

- [ ] **Step 3: Align the client's wording and name the server as the authority**

In `RunFlowStore.swift`, replace `retryRentalBlockReason`'s returned string (`:113`) so one rule has one sentence:

```swift
        return "The draft changed since this rental was confirmed — Confirm again to rent what is in the draft now."
```

Then extend the doc comment on `canRetryRental` (`:96-104`). Keep every existing line — the `_run_token` and `panel_token` reasoning is still load-bearing — and append:

```swift
    ///
    /// Since 2026-09-24 this is the pre-tap copy of a server rule, not the
    /// rule itself: `AppPod.resume` refuses `409 stale_run` from a confirm
    /// stamp on disk (`_resume_generation_refusal`, bot.py), so the window this
    /// closes stays closed across an app relaunch, which `confirmedGeneration`
    /// alone did not. Keep both — this one says why before the tap, and costs
    /// nothing.
```

And amend the `confirmedGeneration` declaration comment (`:53-58`), whose last clause is now out of date. Replace

```swift
    /// which is the state after an app relaunch, and refusing there would break
    /// the legitimate retry-after-relaunch flow.
```

with

```swift
    /// which is the state after an app relaunch. Refusing there would break the
    /// legitimate retry-after-relaunch flow, so this latch fails open — and the
    /// server's confirm stamp is what actually covers the relaunch case.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd "$REPO/ios/MotionKit" && swift test 2>&1 | tail -12`

Expected: `✔ Test run with 250 tests in 23 suites passed.` (249 before this task). The wording change breaks no test: the three existing `retryRentalBlockReason` assertions (`RunFlowTests.swift:534,541,569`) all compare against `nil`, and none asserts the old sentence.

- [ ] **Step 5: Commit**

```bash
cd "$REPO"
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionKit/Sources/MotionKit/Stores/RunFlowStore.swift ios/MotionKit/Tests/MotionKitTests/RunFlowTests.swift
git commit -m "RunFlow: pin the relaunch half of the stale-resume contract" -m "The new test passed on arrival - applyRefusal has handled (stale_run, .resume) since Phase 4. It is here because nothing pinned the app side of a contract that now has a server side, and a refactor of applyRefusal could drop the case silently.

Also aligns retryRentalBlockReason with the server's sentence so one rule has one wording, and corrects the confirmedGeneration comment, which claimed refusing after a relaunch would be the only option.

Co-Authored-By: Qwen Code <noreply@qwen.com>"
```

---

### Task 4: App — the `MigrateFlow` drop guard

> **CORRECTION, found during this task's implementation (2026-09-24).** Four things below are wrong
> and the shipped code deviates from all four. Spec §4 carries the corrected reasoning.
>
> 1. **Step 3's guard order makes its own choke point dead code.** It puts `guard canMigrate(…)` first
>    and the drop guard second, while also adding `!runFlow.isDropping` to `canMigrate`. Both are
>    `@MainActor` with no `await` between them, so during a drop `canMigrate` is false, the first guard
>    returns silently, and the second is unreachable — the refusal never reaches the user. Implemented
>    verbatim, this task's own test failed with `migrate.message → nil`. **The drop guard goes first.**
>    `RunFlow.spend`'s equivalent is not dead in the same shape because `regenerate`, `retryRental` and
>    `choose` skip `canConfirm`; `migrate()` is `MigrateFlow`'s only entry point, so the Phase 6 pattern
>    does not transfer by analogy.
> 2. **Step 3's `MotionApp.swift:94` line does not compile.** `self.runFlow` is `RunFlow?`
>    (`MotionApp.swift:37`), so it cannot be passed where a `RunFlow` is required. It needs a local
>    `let`, mirroring the file's own `let pod = …; self.pod = pod` convention — which also proves
>    `AppModel` and `MigrateFlow` hold the *same instance*. Only `make ios-build` catches this, because
>    `swift test` never compiles `MotionApp`.
> 3. **`RunFlow.isDropping` is at `RunFlowStore.swift:39`, not `:44`.**
> 4. **The "~95 s PATCH + ~90 s validate" figures conflate two different numbers.** Both client
>    timeouts are **95 s** (`RunFlowStore.swift:308`, `:314`); 90 s is the *server's* validate budget
>    (`:311-312`). Quoting the server's number understates the client-side window.
> 5. **Step 1's two tests shipped stronger than written, after review.** `recheckAndReplayAreNotBlockedByADrop`
>    claimed `replayPendingOnce()` coverage its body never exercised, so it was split into
>    `recheckIsNotBlockedByADrop` plus a new `replayPendingOnceIsNotBlockedByADrop` with its own gate
>    scripting; `step != .choose` became `== .started(toDc: "EU-CZ-1")`; and the money-path assertion
>    `allSatisfy { $0.kind != .migrate }` was vacuously true on an empty intent list, so the test that
>    never spends uses `isEmpty` and the two that deliberately spend once use `intents.count == 1` plus
>    a `rechecks`/`replays` counter. Read the shipped tests, not the snippet below.

**Files:**
- Modify: `ios/MotionKit/Sources/MotionKit/Stores/MigrateFlow.swift:36-48` (init), `:63-68` (`canMigrate`), `:113-127` (`migrate`)
- Modify: `ios/MotionApp/MotionApp.swift:94`
- Test: `ios/MotionKit/Tests/MotionKitTests/MigrateFlowTests.swift:7-20` (`Routes`), `:29-34` (`make`), append two tests

**Interfaces:**
- Consumes: `RunFlow.isDropping: Bool` (already `public private(set)`, `RunFlowStore.swift:39`). Nothing new is exposed.
- Produces: `MigrateFlow.init(client: APIClient, gate: any SpendSending, pod: PodStore, runFlow: RunFlow, now: @escaping @Sendable () -> Date)` — a **required** parameter, inserted before `now`.

- [ ] **Step 1: Write the failing tests**

In `MigrateFlowTests.swift`, extend `Routes` so it can serve the run flow's whole route table. Replace the `answer` method (`:13-19`) and add one stored property:

```swift
    final class Routes: @unchecked Sendable {
        private let lock = NSLock()
        private var _ask = TestSupport.json(Fixtures.migrateAsk)
        /// The drop-guard test needs an in-flight `RunFlow.drop`, which needs the
        /// run flow's whole route table. Delegating keeps one copy of it instead
        /// of a second table that can drift. `/v1/pod` is served by both and both
        /// answer `Fixtures.podIdle`, so the delegation changes nothing for the
        /// existing migrate-only tests.
        let runs = RunFlowTests.Routes()
        var ask: (Int, [String: String], Data) {
            get { lock.withLock { _ask } } set { lock.withLock { _ask = newValue } }
        }
        func answer(_ request: URLRequest) -> (Int, [String: String], Data) {
            switch request.url?.path ?? "" {
            case "/v1/pod/migrate/ask": return ask
            default: return runs.answer(request)
            }
        }
    }
```

Replace `make` (`:29-34`) with a pair, keeping the old signature working for every existing test:

```swift
    private func make(_ routes: Routes = Routes(), gate: any SpendSending = FakeSpendGate(),
                      clock: Clock = Clock()) -> MigrateFlow {
        makePaired(routes, gate: gate, clock: clock).0
    }

    /// Both flows over one stub and one client, the way `MotionApp.reconnect()`
    /// builds them (`MotionApp.swift:93-94`) — `MigrateFlow` reads
    /// `RunFlow.isDropping`, so a test of that guard needs the real pairing.
    private func makePaired(_ routes: Routes = Routes(), gate: any SpendSending = FakeSpendGate(),
                            clock: Clock = Clock()) -> (MigrateFlow, RunFlow) {
        StubURLProtocol.install { routes.answer($0) }
        let client = TestSupport.client()
        let runFlow = RunFlow(client: client, gate: gate, sleep: { _ in })
        return (MigrateFlow(client: client, gate: gate, pod: PodStore(client: client),
                            runFlow: runFlow, now: { clock.now }), runFlow)
    }
```

Then append the two tests:

```swift
    /// Phase 6 put `guard !isDropping` in `RunFlow.spend`, the single funnel its
    /// four spend entry points share. `MigrateFlow.migrate()` calls
    /// `gate.perform` directly and so escaped it — this is the fifth entry
    /// point, guarded on its own terms. Probed mid-drop with the same bounded
    /// spin `RunFlowTests` uses, so the assertion can never be vacuous.
    @Test func migrateIsRefusedWhileADropIsInFlight() async throws {
        let clock = Clock()
        let routes = Routes()
        routes.runs.draft = RunFlowTests.Routes.draftTwoJobs
        routes.runs.tryon = Fixtures.tryonDone
        let gate = FakeSpendGate([.accepted(runID: "tg-1000", outcome: "started")])
        let (migrate, flow) = makePaired(routes, gate: gate, clock: clock)
        await migrate.ask(toDc: "EU-CZ-1")
        migrate.typed = "EU-CZ-1"
        #expect(migrate.canMigrate(at: clock.now))          // ready before the drop
        await flow.start(.existing)
        let blazer = try #require(flow.tryon?.previews.first { $0.run == "model__blazer" })

        async let dropTask: Void = flow.drop(blazer)
        var observed = false
        for _ in 0..<1000 {
            if flow.isDropping { observed = true; break }
            await Task.yield()
        }
        #expect(observed)               // never vacuous: a drop really was in flight

        #expect(!migrate.canMigrate(at: clock.now))         // the button is off the glass
        let stepBefore = migrate.step
        await migrate.migrate()
        #expect(migrate.message == "A batch drop is still in flight — wait for it before moving the volume.")
        #expect(migrate.step == stepBefore)                 // still on the typed confirm
        #expect(migrate.currentAsk?.confirmToken != nil)    // ...and its token was not consumed
        #expect(await gate.intents.allSatisfy { $0.kind != .migrate })   // nothing was sent

        await dropTask
        #expect(!flow.isDropping)
        #expect(migrate.canMigrate(at: clock.now))          // temporary, not sticky
    }

    /// Review Focus 4. `canMigrate` gained a term; if `recheck()` or
    /// `replayPendingOnce()` consulted it, an unanswered migrate — the one
    /// state the whole pending-notice machinery exists to resolve — could
    /// become unresolvable behind an unrelated drop. They must not.
    @Test func recheckAndReplayAreNotBlockedByADrop() async throws {
        let clock = Clock()
        let routes = Routes()
        routes.runs.draft = RunFlowTests.Routes.draftTwoJobs
        routes.runs.tryon = Fixtures.tryonDone
        let gate = FakeSpendGate([.unreachable(detail: "timed out"),
                                  .accepted(runID: "tg-1000", outcome: "started")])
        let (migrate, flow) = makePaired(routes, gate: gate, clock: clock)
        await migrate.ask(toDc: "EU-CZ-1")
        migrate.typed = "EU-CZ-1"
        await migrate.migrate()
        #expect(migrate.needsRecheck)                       // the first attempt never landed

        await flow.start(.existing)
        let blazer = try #require(flow.tryon?.previews.first { $0.run == "model__blazer" })
        async let dropTask: Void = flow.drop(blazer)
        var observed = false
        for _ in 0..<1000 {
            if flow.isDropping { observed = true; break }
            await Task.yield()
        }
        #expect(observed)

        await migrate.recheck()                             // resolves anyway
        #expect(!migrate.needsRecheck)
        #expect(migrate.step != .choose)
        await dropTask
    }
```

`MigrateAsk` (`Models/PodCost.swift:102-110`) is `Decodable` with `public let` fields and no public initializer, so a test cannot construct one — which is why the step assertion above compares against a captured `stepBefore` rather than naming a `.confirm(…)` value.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd "$REPO/ios/MotionKit" && swift test --filter MigrateFlowTests 2>&1 | tail -20`

Expected: FAIL to compile — `extra argument 'runFlow' in call` / `missing argument for parameter 'runFlow'`, because `MigrateFlow.init` does not take it yet.

- [ ] **Step 3: Write the guard**

In `MigrateFlow.swift`, add the property next to the other `private let`s (`:38-41`) and the init parameter (`:43-48`):

```swift
    private let client: APIClient
    private let gate: any SpendSending
    private let pod: PodStore
    /// Read for `isDropping` only. `RunFlow.spend` guards its own four entry
    /// points; `migrate()` calls `gate.perform` directly and so needs the same
    /// guard here, or a drop's two 95 s client timeouts — the slot PATCH and
    /// the validate (RunFlowStore.swift:308, :314) — are a window the most
    /// destructive call in the API can be launched inside.
    private let runFlow: RunFlow
    private let now: @Sendable () -> Date
    private var deadline: Date?
    private var didReplay = false

    public init(client: APIClient, gate: any SpendSending, pod: PodStore, runFlow: RunFlow,
                now: @escaping @Sendable () -> Date = { Date() }) {
        self.client = client
        self.gate = gate
        self.pod = pod
        self.runFlow = runFlow
        self.now = now
    }
```

`canMigrate(at:)` gains one term:

```swift
    public func canMigrate(at date: Date) -> Bool {
        guard let ask = currentAsk, let deadline else { return false }
        return typed == ask.toDc && date < deadline
            && !isSending && !needsRecheck && pendingNotice == nil
            && !runFlow.isDropping
    }
```

And `migrate()` gains the choke-point guard — **ahead of** the `canMigrate` guard, not behind it. Both levels exist because a button-level guard alone is what Phase 6 found insufficient, but the order is load-bearing: `canMigrate` now carries the same term, both guards are `@MainActor` with no `await` between them, so checking the drop second would make it unreachable and the refusal silent.

```swift
    public func migrate() async {
        // Ahead of `canMigrate`, deliberately. `canMigrate` carries the same
        // `!runFlow.isDropping` term and both guards are `@MainActor` with no
        // `await` between them, so checking the drop second would make this
        // guard unreachable and the refusal silent — a dead guard reads as
        // coverage it does not provide. Kept as the choke point regardless,
        // mirroring `RunFlow.spend`: a caller that skips the button check still
        // cannot launch the one call that deletes a volume while the draft is
        // moving under it. `recheck()` and `replayPendingOnce()` deliberately do
        // NOT come through here — they resolve a request already sent, and
        // blocking them would strand a pending migration behind an unrelated
        // drop.
        guard !runFlow.isDropping else {
            message = "A batch drop is still in flight — wait for it before moving the volume."
            return
        }
        guard canMigrate(at: now()), let ask = currentAsk else { return }
        let label = "Migrate volume to \(ask.toDc)"
```

Leave `recheck()` and `replayPendingOnce()` untouched.

In `ios/MotionApp/MotionApp.swift`, the construction needs a local `let` because `self.runFlow` is `RunFlow?` (`:37`) and `MigrateFlow` takes it non-optional — mirroring the file's own `let pod = …; self.pod = pod` convention:

```swift
        // A local `let`, like `pod` and `draft` above: `self.runFlow` is a
        // `RunFlow?`, and `MigrateFlow` takes the dependency non-optional so an
        // absent one cannot silently no-op its drop guard.
        let runFlow = RunFlow(client: client, gate: gate)
        self.runFlow = runFlow
        migrate = MigrateFlow(client: client, gate: gate, pod: pod, runFlow: runFlow)
```

This is also what makes the two stores provably hold the **same instance**. Two separate `RunFlow` objects would make the guard inert while every test still passed, because the tests build their own pairing. Both are rebuilt together on `reconnect()`, and the `guard let credentials = vault.load() else { … }` teardown nils both, so no dangling pairing.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd "$REPO/ios/MotionKit" && swift test 2>&1 | tail -12`

Expected: `✔ Test run with 252 tests in 23 suites passed.` (250 after Task 3; the fix round then added a third test, so Task 4 ends at 253 and every later task's baseline is 253.)

- [ ] **Step 5: Build the app, which is the only check that the wiring compiles**

Run: `cd "$REPO" && make ios-build 2>&1 | tail -12; echo "EXIT=$?"`

Expected: `EXIT=0`. `swift test` never compiles `MotionApp`, so a wrong argument at `MotionApp.swift:94` is invisible until this step.

- [ ] **Step 6: Commit**

```bash
cd "$REPO"
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionKit/Sources/MotionKit/Stores/MigrateFlow.swift ios/MotionKit/Tests/MotionKitTests/MigrateFlowTests.swift ios/MotionApp/MotionApp.swift
git commit -m "MigrateFlow: refuse a migration while a batch drop is in flight" -m "Phase 6 guarded RunFlow.spend, the single funnel its four spends share. migrate() calls gate.perform directly, so it was the fifth entry point and the only unguarded one - a drop's two 95s client timeouts were a window the most destructive call in the API could be launched inside.

Guarded at both levels, as Phase 6 guards confirm, with the drop guard ahead of canMigrate: canMigrate carries the same term, so checking it first would make the choke point unreachable and the refusal silent. recheck() and replayPendingOnce() stay unguarded on purpose: they resolve a request already sent, and blocking them would strand a pending migration behind an unrelated drop.

Co-Authored-By: Qwen Code <noreply@qwen.com>"
```

---

### Task 5: App — human copy for a failed validation

**Files:**
- Modify: `ios/MotionKit/Sources/MotionKit/API/APIError.swift:19-38`
- Modify: `ios/MotionApp/Components/StatusViews.swift:34-51` (`ErrorBanner`)
- Test: `ios/MotionKit/Tests/MotionKitTests/APIClientTests.swift:295-306` (`userMessages`)
- Test: `ios/MotionKit/Tests/MotionKitTests/DraftStoreTests.swift:249` (an assertion this change breaks)

**Interfaces:**
- Consumes: nothing new.
- Produces: `APIError.detailMessage: String?` — non-nil only for `.server(422, "invalid", …)` with non-blank text.

- [ ] **Step 1: Write the failing tests**

In `APIClientTests.swift`, append to `userMessages()` (`:295`) before its closing brace:

```swift
        // A 422 `invalid` carries the validator's raw stdout+stderr
        // (drafts.py:535,544-548), the literal "make batch-validate failed"
        // (drafts.py:566), Telegram-facing copy telling the reader to "send the
        // file(s) again" (bot.py:6064), or an empty string (bot.py:6051, where
        // _render_and_validate already sent the real reason to Telegram). All
        // four are developer-facing, so the banner leads with a headline and
        // the raw text moves behind a disclosure.
        #expect(APIError.server(status: 422, code: "invalid",
                                message: "scripts/batch_run.py: boom").userMessage
                == "This draft didn't pass validation, so it can't run yet.")
        #expect(APIError.server(status: 422, code: "invalid",
                                message: "make batch-validate failed").userMessage
                == "This draft didn't pass validation, so it can't run yet.")
        #expect(APIError.server(status: 422, code: "invalid",
                                message: "").userMessage
                == "This draft didn't pass validation, so it can't run yet.")
        // The raw text is preserved for the disclosure — except when blank, so
        // bot.py:6051's empty message renders no empty disclosure.
        #expect(APIError.server(status: 422, code: "invalid",
                                message: "scripts/batch_run.py: boom").detailMessage
                == "scripts/batch_run.py: boom")
        #expect(APIError.server(status: 422, code: "invalid", message: "").detailMessage == nil)
        #expect(APIError.server(status: 422, code: "invalid", message: "  \n ").detailMessage == nil)
        // Every other error keeps its text and has no detail. `missing_slots` is
        // the 422 whose server copy is already written for a human
        // (DraftStoreTests.swift:196) and must stay verbatim.
        #expect(APIError.server(status: 422, code: "missing_slots",
                                message: "Assign driver before validating.").userMessage
                == "Assign driver before validating.")
        #expect(APIError.server(status: 422, code: "missing_slots",
                                message: "Assign driver before validating.").detailMessage == nil)
        #expect(APIError.server(status: 409, code: "stale_run", message: "x").detailMessage == nil)
        #expect(APIError.transport("offline").detailMessage == nil)
        #expect(APIError.decoding("shape").detailMessage == nil)
        #expect(APIError.accessDenied(status: 403).detailMessage == nil)
```

In `DraftStoreTests.swift`, the assertion at `:249` breaks — update it and add the detail alongside:

```swift
        #expect(store.error == .server(
            status: 422, code: "invalid", message: "Driver video is unreadable."))
        // `error` keeps the raw text; only its rendering moved (spec §5).
        #expect(store.message == "This draft didn't pass validation, so it can't run yet.")
        #expect(store.error?.detailMessage == "Driver video is unreadable.")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd "$REPO/ios/MotionKit" && swift test --filter 'APIClientTests|DraftStoreTests' 2>&1 | tail -25`

Expected: FAIL — `detailMessage` does not exist (compile error), and once it does, `userMessage` still returns the raw text so the headline assertions fail.

- [ ] **Step 3: Write the mapping**

In `APIError.swift`, add the case inside `userMessage`'s inner `switch status`, after `case 404:`:

```swift
            case 404: return "Not found — it may have been removed from Telegram."
            case 422 where code == "invalid":
                // The server's text for this one code is never written for a
                // reader: see `detailMessage`.
                return "This draft didn't pass validation, so it can't run yet."
            case 502: return "RunPod/Vast didn't answer. Try again."
```

Then add the accessor below `userMessage`, inside the enum:

```swift
    /// The server's own text when `userMessage` replaces it with a headline, so
    /// a view can offer it behind a disclosure instead of discarding it. `nil`
    /// for every other error — and `nil` for a blank one, so `bot.py:6051`'s
    /// empty `invalid` message renders no empty disclosure.
    ///
    /// Only `422 invalid` needs this. Its message is one of: the validator's raw
    /// stdout+stderr, path-stripped and truncated (`drafts.py:535,544-548`); the
    /// literal `make batch-validate failed` (`drafts.py:566`); Telegram-facing
    /// copy telling the reader to "send the file(s) again" into a chat they are
    /// not in (`bot.py:6064`); or an empty string, because `_render_and_validate`
    /// already sent the real reason to Telegram (`bot.py:6048-6051`). Before
    /// 2026-09-24 all four reached the phone verbatim through `userMessage`'s
    /// `default` branch, and the empty one rendered a banner with no text in it.
    public var detailMessage: String? {
        guard case let .server(status, code, message) = self,
              status == 422, code == "invalid" else { return nil }
        guard !message.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
        return message
    }
```

- [ ] **Step 4: Render the disclosure in `ErrorBanner`**

Replace `ErrorBanner`'s `body` in `ios/MotionApp/Components/StatusViews.swift`:

```swift
struct ErrorBanner: View {
    let error: APIError
    var retry: (() async -> Void)?
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "exclamationmark.triangle").foregroundStyle(Theme.red)
                Text(error.userMessage).font(Theme.sans(13)).foregroundStyle(Theme.ink1)
                Spacer(minLength: 0)
                if let retry {
                    Button("Retry") { Task { await retry() } }
                        .font(Theme.sans(13, .semibold)).foregroundStyle(Theme.lime)
                }
            }
            // Only a 422 `invalid` has a detail (APIError.detailMessage), so the
            // other five ErrorBanner call sites render exactly as they did
            // before this VStack existed. Collapsed by default: the raw text is
            // the validator's own output, and it is the only thing that names
            // the real problem, so it is reachable but not in the way.
            if let detail = error.detailMessage {
                DisclosureGroup("Details") {
                    Text(detail)
                        .font(Theme.mono(11))
                        .foregroundStyle(Theme.ink2)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .font(Theme.sans(12, .semibold))
                .foregroundStyle(Theme.ink2)
                .tint(Theme.lime)
            }
        }
        .padding(12)
        .background(Theme.redDim, in: .rect(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Theme.redLine))
    }
}
```

`Theme.mono`, `Theme.ink2`, `Theme.sans` and `Theme.lime` are all already used elsewhere in `MotionApp` (`NewJobView.swift:149`, `:206`), so no new theme surface.

- [ ] **Step 5: Run the tests and the build**

Run: `cd "$REPO/ios/MotionKit" && swift test 2>&1 | tail -12`

Expected: `✔ Test run with 253 tests in 23 suites passed.` (no new suites; `userMessages` grew in place).

Run: `cd "$REPO" && make ios-build 2>&1 | tail -12; echo "EXIT=$?"`

Expected: `EXIT=0`. This is the only gate that compiles `ErrorBanner`.

- [ ] **Step 6: Commit**

```bash
cd "$REPO"
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionKit/Sources/MotionKit/API/APIError.swift ios/MotionKit/Tests/MotionKitTests/APIClientTests.swift ios/MotionKit/Tests/MotionKitTests/DraftStoreTests.swift ios/MotionApp/Components/StatusViews.swift
git commit -m "APIError: lead a failed validation with a headline, not the validator's stdout" -m "A 422 invalid reaches the phone as one of four developer-facing strings: the validator's raw stdout+stderr, the literal 'make batch-validate failed', Telegram copy telling the reader to send files again into a chat they are not in, or - bot.py:6051 - an empty string, because _render_and_validate already sent the real reason to Telegram. That last one rendered a red banner with no text in it.

userMessage now headlines the case and detailMessage keeps the raw text for a collapsed disclosure, nil when blank so the empty message renders no empty box. Client-side on purpose: the server's text is right for Telegram, where the transcript above it is the context.

Co-Authored-By: Qwen Code <noreply@qwen.com>"
```

---

### Task 6: App — `Fixtures` `mask` → `background`

**Files:**
- Modify: `ios/MotionKit/Tests/MotionKitTests/Fixtures.swift:159,160,170`
- Modify: `ios/MotionKit/Tests/MotionKitTests/ModelsTests.swift:176,192,254`
- Modify: `ios/MotionKit/Tests/MotionKitTests/RunFlowTests.swift:33`
- Modify: `ios/MotionKit/Tests/MotionKitTests/BatchComposerTests.swift:102`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing. Test fixtures only — no shipped code changes, so no interface moves.

- [ ] **Step 1: Confirm the seven sites and nothing else**

Run: `cd "$REPO" && grep -rn '"mask"\|\[mask\]\|:mask\|mask"' ios/ --include=*.swift`

Expected: exactly the seven sites listed above (two in `Fixtures.pipelines`, one in `Fixtures.draft`, three in `ModelsTests`, one each in `RunFlowTests` and `BatchComposerTests`), plus no hits under `ios/MotionKit/Sources/` or `ios/MotionApp/`. Any hit in shipped code means this task's premise is wrong — stop and re-read spec §6.

Note `in: .userDomainMask` also matches a loose `mask` grep; it is a Foundation constant in `UploadCheckpoint.swift:71`, `ImportStaging.swift:49` and `IdempotencyLedger.swift:78` and must not be touched. Use the quoted pattern above, not a bare `mask`.

- [ ] **Step 2: Rename in `Fixtures.swift`**

`:159-160` — keep the kind `"future_kind"`. The role's *name* was never the subject of `ModelsTests:176`; an unknown role **kind** decoding as `.unknown` is, and that only holds if the kind stays unrecognised:

```swift
      {"id":"tryon-motion-enhance","stages":["tryon","motion","enhance"],
       "required":["character","driver","outfit"],"optional":["background"],
       "roles":{"character":"image","driver":"video","outfit":"image","background":"future_kind"},
       "providers":[{"id":"gemini","label":"Gemini"},{"id":"qwen-max","label":"Qwen Max"}]}
```

`:170` in `Fixtures.draft`:

```swift
     "required":["character","driver","outfit"],"optional":["background"],
```

Add a comment above `static let pipelines` recording why the name matters, so the next person does not "fix" it back:

```swift
    /// The optional role is `background`, matching the live catalog and the
    /// server's own pipelines (`scripts/batchlib/pipelines.py:54,81`). It was
    /// `mask` until 2026-09-24, a name that occurs nowhere in `scripts/**` as a
    /// role — a fixture that disagrees with the catalog cannot catch a
    /// role-handling regression, which is the only reason it exists. Its *kind*
    /// stays the unrecognised `"future_kind"`: `ModelsTests`'
    /// `pipelineAndDraftModelsDecode`'s unknown-kind assertion is about the kind, not the name.
```

- [ ] **Step 3: Rename in the three test files**

`ModelsTests.swift:176` — the assertion keeps its meaning:

```swift
        #expect(catalog.pipelines[1].roles["background"] == .unknown)
```

`ModelsTests.swift:192` and `:254`, and `BatchComposerTests.swift:102` — the same `"optional":["mask"]` → `"optional":["background"]` substitution in each inline JSON literal.

`RunFlowTests.swift:33` — inside `Routes.draftJSON`:

```swift
                + slots + #"},"required":["character","driver","outfit"],"optional":["background"],"missing":[],"validated":true,"batch":["#
```

- [ ] **Step 4: Run the tests**

Run: `cd "$REPO/ios/MotionKit" && swift test 2>&1 | tail -12`

Expected: `✔ Test run with 253 tests in 23 suites passed.` — the **same count as before this task**. A rename that changes the count means a test stopped compiling into the run or an anchor silently no-op'd.

- [ ] **Step 5: Verify Review Focus 5 — that no `.replacingOccurrences` anchor silently no-op'd**

Spec §6 read all nine anchors and none contains `mask`, so none should have moved. Prove it rather than trust the reading — and grep the **whole** test directory, not a brace list of three files. This step originally scoped itself to `{ModelsTests,TryonLibraryStoreTests,DraftStoreTests}.swift` and so excluded `RunFlowTests.swift`, which is the one file whose anchor operates on a string this task edits:

Run: `cd "$REPO" && grep -rn 'replacingOccurrences' ios/MotionKit/Tests/MotionKitTests/`

Expected: nine call sites plus one doc comment mentioning the method (`TryonLibraryStoreTests.swift:22`), so ten matching lines. The nine anchors are `"estimate_min":null}` (`ModelsTests.swift:231`), `"provider":"gemini","slots":{"character"` (`:233`), `"driver":null}}]` and `"jobs":1,"estimate_min":null}` (`TryonLibraryStoreTests.swift:29-31`, `:32-34`), `"stale":false` twice (`DraftStoreTests.swift:131-132`, `:152-153`), `"generation":4` (`:401-402`), `"estimate_min":null}` (`:405-407`), and `"validated":true` (`RunFlowTests.swift:59-60`). None contains `mask`. The last one is the only anchor whose target this task edits, so it is the one that could have no-op'd. Then confirm the suite that would fail loudly if one had broken still runs:

Run: `cd "$REPO/ios/MotionKit" && swift test --filter 'usersListsBasketRunsBeforeTheEditedJob|draftSeedIsOptionalAndDecodes' 2>&1 | tail -10`

Expected: both pass. `TryonLibraryStoreTests.swift:22-26` documents that this is the intended failure mode — "If an anchor ever stops matching the replacement no-ops, and `usersListsBasketRunsBeforeTheEditedJob` then fails loudly instead of passing vacuously."

- [ ] **Step 6: Commit**

```bash
cd "$REPO"
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionKit/Tests/MotionKitTests/Fixtures.swift ios/MotionKit/Tests/MotionKitTests/ModelsTests.swift ios/MotionKit/Tests/MotionKitTests/RunFlowTests.swift ios/MotionKit/Tests/MotionKitTests/BatchComposerTests.swift
git commit -m "Fixtures: name the optional role background, as the catalog does" -m "mask occurs nowhere in scripts/** as a role - the server's pipelines use background (batchlib/pipelines.py:54,81). A fixture that disagrees with the live catalog cannot catch a role-handling regression, which is the only reason it exists.

The role kind stays the unrecognised future_kind: ModelsTests' unknown-kind assertion is about the kind, not the name. All six .replacingOccurrences anchors across the three surgery suites were checked and none contains mask, so the blocker Phase 6 recorded does not fire; the suite count is unchanged and the anchor-canary test still passes.

Co-Authored-By: Qwen Code <noreply@qwen.com>"
```

---

### Task 7: App — a `Clear` in Batch mode

**Files:**
- Modify: `ios/MotionApp/NewJob/NewJobView.swift:105-115` (the if/else), `:202` (`readiness`), `:219-243` (`editorActions`)
- Modify: `ios/MotionAppUITests/Phase6SmokeTests.swift:57-70` (the skip path), `:105-113` (the final clear)
- Test: the live `make ios-ui-test` run in Task 9 — this task has no unit-test surface, because `NewJobView` is in `MotionApp`, which has no test target

**Interfaces:**
- Consumes: `DraftStore.clear()`, already used by the Single arm.
- Produces: `NewJobView.clearAction: some View` — a computed property, not a method, because it needs no `draft`.

- [ ] **Step 1: Extract the Clear button**

In `NewJobView.swift`, replace the `Button("Clear", role: .destructive) { … }` block inside `editorActions` (`:231-241`) with a call to the new property:

```swift
    private func editorActions(_ draft: Draft) -> some View {
        HStack(spacing: 10) {
            Button("Add to batch") {
                Task { await store.addToBatch() }
            }
            .font(Theme.sans(14, .semibold))
            .foregroundStyle(Theme.limeInk)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 12)
            .background(Theme.lime, in: .rect(cornerRadius: 12))
            .disabled(!draft.missing.isEmpty || store.isBusy || composer.isRunning)

            clearAction
        }
    }

    /// One implementation for both arms, so they cannot drift. Batch mode had
    /// no Clear at all until 2026-09-24: `editorActions` — the only Clear — sat
    /// in the Single arm of `editor`'s if/else alone, so emptying the draft from
    /// Batch meant switching to Single first. That was not theoretical;
    /// `Phase6SmokeTests` had to do exactly it, twice.
    ///
    /// No accessibility identifier: the smokes find this by its `"Clear"` label
    /// (`Phase4Draft.revealButton`), as they did before, and an identifier
    /// nothing queries is noise.
    private var clearAction: some View {
        Button("Clear", role: .destructive) {
            Task { await store.clear() }
        }
        .font(Theme.sans(14, .semibold))
        .foregroundStyle(Theme.red)
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .background(Theme.redDim, in: .rect(cornerRadius: 12))
        .disabled(store.isBusy || composer.isRunning)
    }
```

- [ ] **Step 2: Render it in the Batch arm**

In `editor`'s if/else (`:107-115`):

```swift
                if isBatch {
                    BatchComposerSection(store: store, composer: composer,
                                         materials: materials, pipeline: pipeline,
                                         onPickRole: { selectedRole = $0 })
                    clearAction
                } else {
                    slots(draft: draft, pipeline: pipeline)
                    seedBadge(draft)
                    readiness(draft)
                    editorActions(draft)
                }
```

`batch(draft)` and `validation(draft)` follow the if/else and are unchanged — both already render in both modes.

- [ ] **Step 3: Say why `readiness` stays Single-only**

Replace the doc comment situation at `readiness` (`:202`), which currently has none:

```swift
    /// Single mode only, deliberately. This reports the *edited job*'s required
    /// and missing roles, and the Batch arm does not render the edited job — it
    /// renders shared slots plus an outfit multi-select. A "2 of 3 required
    /// slots assigned" line under a cross-build form would describe a job the
    /// user is not looking at. Batch mode's own readiness is `BatchComposer`'s
    /// `canRun`, which the run button's disabled state already shows.
    private func readiness(_ draft: Draft) -> some View {
```

- [ ] **Step 4: Drop the smoke's two mode switches**

In `Phase6SmokeTests.swift`, add the local helper inside the class, above `testCrossBuildDropAndLibrary`:

```swift
    /// Batch mode has had its own Clear since 2026-09-24, but
    /// `Phase4Draft.clear(in:)` still cannot be reused from here: its
    /// post-condition is the readiness line ("0 of 3 required slots assigned"),
    /// which is Single-only and stays Single-only. `"0 jobs"` is the header's
    /// count (`NewJobView.swift:149`), rendered in both arms, and is the same
    /// literal this smoke's precondition already asserts.
    @MainActor
    private func clearFromBatch(_ app: XCUIApplication) {
        let clear = Phase4Draft.revealButton("Clear", in: app)
        // `revealButton` only waits for `isHittable`. Clear is
        // `.disabled(store.isBusy || composer.isRunning)`, and a tap on a
        // disabled SwiftUI control is a silent no-op — so wait for `isEnabled`
        // separately, or the draft stays full and the assertion below is the
        // only thing that notices.
        XCTAssertTrue(Phase4Draft.waitUntil(timeout: 10) { clear.isEnabled },
                      "Clear must re-enable before it is tapped")
        clear.tap()
        XCTAssertTrue(app.staticTexts["0 jobs"].waitForExistence(timeout: 10),
                      "Batch mode's Clear emptied the draft")
    }
```

Replace the skip path's cleanup (`:57-69`) — the whole comment block and the three statements after `app.buttons["Done"].tap()`:

```swift
            // Character and Driver were assigned above, so clean up. Batch mode
            // has its own Clear, so this no longer switches to Single first.
            app.buttons["Done"].tap()
            clearFromBatch(app)
            throw XCTSkip("Fewer than two image materials to use as outfits.")
```

Replace the final clear (`:105-113`) — the comment and the mode switch:

```swift
        // Leave the live draft empty, as the Phase 3-5 smokes do. Batch mode
        // clears directly now; the drop's DELETE and trailing refresh can still
        // hold `store.isBusy` true, which `clearFromBatch` waits out.
        clearFromBatch(app)
```

Do **not** touch `Phase3SmokeTests.swift:67,119-120`. Those run in Single mode, where `Phase4Draft.clear(in:)`'s readiness-line post-condition is still correct.

- [ ] **Step 5: Build, and prove the UI-test target still compiles**

Run: `cd "$REPO" && make ios-build 2>&1 | tail -12; echo "EXIT=$?"`

Expected: `EXIT=0`. This compiles `MotionApp` only — the scheme builds `MotionAppUITests` for the `test` action, not `build`.

Run: `cd "$REPO" && make ios-gen && xcodebuild -project ios/MotionApp.xcodeproj -scheme MotionApp -destination 'generic/platform=iOS Simulator' build-for-testing > /tmp/p6fu-bft.log 2>&1; echo "EXIT=$?"; tail -5 /tmp/p6fu-bft.log`

Expected: `EXIT=0`, and `grep -c "Phase6SmokeTests" /tmp/p6fu-bft.log` is non-zero — the object file being compiled is what proves the UI-test target was part of this action. No simulator is booted: `generic/platform=iOS Simulator` is a compile-only destination.

`make ios-gen` must run first: the `.xcodeproj` is generated from `ios/project.yml` and gitignored, so it may not exist. The `-project ios/MotionApp.xcodeproj -scheme MotionApp` form and the repo-root working directory are what `scripts/ios-ui-test.sh:65-66` uses; only the destination and the action differ, because that script boots a named simulator and runs `test`.

- [ ] **Step 6: Commit**

```bash
cd "$REPO"
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionApp/NewJob/NewJobView.swift ios/MotionAppUITests/Phase6SmokeTests.swift
git commit -m "New Job: a Clear in Batch mode" -m "editorActions - the only Clear - sat in the Single arm of editor's if/else alone, so emptying the draft from Batch meant switching to Single first. Phase6SmokeTests had to do exactly that twice, which is the evidence it was a real papercut and not a hypothetical one.

Extracted so both arms share one implementation and cannot drift. readiness stays Single-only: it reports the edited job's slots, and Batch does not render the edited job.

The smoke now clears from Batch directly, asserting the header's job count rather than Phase4Draft.clear's readiness-line post-condition, which Batch does not render.

Co-Authored-By: Qwen Code <noreply@qwen.com>"
```

---

### Task 8: Docs — amend the control-plane API design

**Files:**
- Modify: `docs/superpowers/specs/2026-09-21-vps-control-plane-api-design.md` — §5.9's "`resume` is for a failed rental only" bullet (`:285-291`)
- Modify: `docs/superpowers/specs/2026-09-24-phase-6-follow-ups-design.md` — the Status line, and one new line about citation drift
- Modify: `ios/README.md` — the "Retry rental is latched to the draft that was confirmed" bullet (`:185-190`)

**Interfaces:** none. Docs only.

`ios/README.md` was added to this task by controller ruling after Task 3's review found it: no task in the original plan owned that file, so the branch would have merged with the README describing the shipped fix as future work. Do not edit `docs/superpowers/swiftui-app-progress.md` — Global Constraints reserve it for after the merge.

- [ ] **Step 1: Amend §5.9 in place**

The route table at `:185` does **not** change: no new route, no new request field, no new response field. That is worth stating in the bullet, because it is why both deploy orders work.

Replace §5.9's third bullet with:

```markdown
- **`resume` is for a failed rental only.** It requires an outstanding `provision-failed.json` for the
  run (the condition Telegram's recovery buttons are drawn under), the run's current `run_token`, and —
  since 2026-09-24 — an unchanged draft. `confirm` stamps the app draft's `generation` when it is
  accepted (`tg-{chat}.confirmed-generation.json`, `_kill_result_path`'s convention), and `resume`
  refuses `409 stale_run` when the draft has moved since, because it re-rents the manifest on disk and
  a draft edit rewrites no manifest. `_run_token` is the manifest's `mtime_ns`, so it cannot see one;
  this is the gap `panel_token` already closes for `confirm` by joining `.generation`. **Fails open when
  no stamp exists** — a Telegram-initiated confirm writes none, and `_do_confirm` must not write one,
  because it is shared with the Telegram flow where the app's draft is not what the user reviewed. So
  the latch covers app-initiated confirms only. No request or response field changed, so an old app
  build is protected by the server and a new build works against an old server: both deploy orders are
  safe. `provider` is required, as on `confirm`. `gpu`, when sent, must equal `.env`'s `GPU` or the
  answer is `409 stale_panel`; the same optional `gpu` is now checked on `confirm` — the price the app
  showed was for one GPU.
```

Also add to §5.9's closing "Not in slice 5" bullet, or a new line under it:

```markdown
- Also not covered: a rental confirmed from **Telegram** and retried from the phone is unstamped and so
  unchecked, and the stamp is per chat, so one slot's confirm overwrites another's. That matches the
  one-run-slot model every other `tg-{chat}.*` record uses.
```

- [ ] **Step 2: Update the Status line in this branch's spec**

In `docs/superpowers/specs/2026-09-24-phase-6-follow-ups-design.md`, change the Status line at the top from

```
Date: 2026-09-24 · Status: design approved in chat, not yet implemented · Branch: `feat/phase-6-follow-ups` (one branch, one PR)
```

to

```
Date: 2026-09-24 · Status: implemented on `feat/phase-6-follow-ups`, gates not yet swept, not merged · Branch: `feat/phase-6-follow-ups` (one branch, one PR)
```

Leave the gate-record table alone. Task 9 fills it — the gates have not run yet at this point, and writing a result before the command that produces it is how a false claim gets into a durable doc. Task 9 Step 6 also flips this Status line to its final wording.

Then add one line to the spec, immediately under its Status line at the top of the file:

```markdown
Line-number citations of `scripts/tgbot/bot.py` in §2 and §3 are as of `fd8464b`, before implementation. Tasks 1 and 2 inserted roughly 120 lines into that file, nearly all above `:7040`, so every citation pointing below it has drifted — `:7413` for the `run_token` check and `_kill_result_path` at `:7247-7274` among them. Read by symbol name (`AppPod.resume`, `_kill_result_path`); the names did not move. Code comments are held to a stricter standard and were corrected as each task touched them.
```

- [ ] **Step 3: Correct `ios/README.md`**

The bullet at `ios/README.md:185-190` currently ends:

```markdown
  (`retryRentalBlockReason`). The latch is in memory, so an app relaunch clears it; the durable fix is
  a server-side `generation` check on `resume`.
```

That describes this branch's headline change as future work. Replace those two lines with what shipped:

```markdown
  (`retryRentalBlockReason`). Since 2026-09-24 the durable half is server-side: an accepted confirm
  stamps the draft's `generation` (`tg-<chat>.confirmed-generation.json`) and `resume` refuses
  `409 stale_run` when the draft has moved, so a relaunch no longer clears the guard. The in-memory
  latch stays as the pre-tap copy — it says why before the tap and costs nothing. It fails open after a
  relaunch, and the server is what covers that case.
```

Check the surrounding bullet's tense and style before writing, and keep the README's own voice — it is a shipped-feature reference, not a changelog, so state what is true now rather than narrating the change. Do not touch any other bullet in that file.

Then verify nothing else in the tracked tree still calls this future work:

Run: `cd "$REPO" && grep -rn "durable fix is a server-side\|in memory, so an app relaunch" --include=*.md . | grep -v "^./.superpowers"`

Expected: only `docs/superpowers/swiftui-app-progress.md`, which Global Constraints reserve for after the merge. Any other hit is a doc this plan never inventoried — report it rather than fixing it silently.

- [ ] **Step 4: Commit**

```bash
cd "$REPO"
motions-studio/setup/scrub-secrets.sh --check
git add docs/superpowers/specs/2026-09-21-vps-control-plane-api-design.md docs/superpowers/specs/2026-09-24-phase-6-follow-ups-design.md ios/README.md
git commit -m "docs(api): resume's third gate, the confirm stamp" -m "Amended in place, as each slice has. No route and no field changed, which is why both deploy orders are safe and the route table at 5.2 does not move.

ios/README.md described the server-side latch as future work; it shipped on this branch. Added by controller ruling after Task 3's review found that no task owned the file.

Co-Authored-By: Qwen Code <noreply@qwen.com>"
```

---

### Task 9: Gate sweep

**Files:** none. This task only runs commands and records results.

**Interfaces:** none.

- [ ] **Step 1: The free Python gates**

Run: `cd "$REPO" && make batch-test 2>&1 | tail -10; echo "EXIT=$?"`

Expected: `EXIT=0`, `OK` or `OK (skipped=1)`.

Run: `cd "$REPO" && python3 -m unittest scripts.tests.test_batch_control_invariants 2>&1 | tail -8`

Expected: `OK`. This is the static check that the money calls are still reachable from exactly the pinned call sites; Tasks 1 and 2 added a gate, not a call site, so it must be unaffected. If it fails, something moved `start_drain` — stop and diagnose, do not edit the expectation.

- [ ] **Step 2: The free Swift gates**

Run: `cd "$REPO/ios/MotionKit" && swift test 2>&1 | tail -12; echo "EXIT=$?"`

Expected: `EXIT=0`, `✔ Test run with 253 tests in 23 suites passed.`

Run: `cd "$REPO" && make ios-build > /tmp/p6fu-build.log 2>&1; echo "EXIT=$?"; tail -5 /tmp/p6fu-build.log`

Expected: `EXIT=0`. Checked explicitly, not through a pipe — `make … | tail` reports `tail`'s status.

- [ ] **Step 3: The secrets gate**

Run: `cd "$REPO" && motions-studio/setup/scrub-secrets.sh --check > /dev/null 2>&1; echo "EXIT=$?"`

Expected: `EXIT=0`. Must pass before every commit, and once more here over the whole tree.

Run: `cd "$REPO" && git status --short`

Expected: empty. If `ios/Secrets.xcconfig`, `.env` or `ios/MotionApp.xcodeproj` appears, it was staged by mistake — unstage it and find out which task did that before continuing.

- [ ] **Step 4: The live contract gate**

Run: `cd "$REPO" && make ios-contract > /tmp/p6fu-contract.log 2>&1; echo "EXIT=$?"; tail -20 /tmp/p6fu-contract.log`

Expected: `EXIT=0`, 14/14 ok. This is a live read-only run against the VPS — GETs only, no spend. It must be 14/14 and not 15: this branch adds no route. If a route fails to decode, the server has drifted, not this branch.

Note what this run proves. The VPS is still on the **pre-merge** server, so this is the "new app against an old server" half of the deploy-order property spec §3 claims. The other half — an old app against the new server — needs no run, because nothing in the request or response shape changed. The controller re-runs this gate after the deploy and records it as a separate row.

- [ ] **Step 5: The live UI gate, outside the sandbox**

This must run with the command sandbox disabled — simulator control is killed inside it. It sends live reads and free draft mutations only, launches with `-UITestRecordingSpendGate`, and asserts zero recorded spends. **It must not send Phase A, confirm, kill, migrate or resume.**

Run: `cd "$REPO" && make ios-ui-test > /tmp/p6fu-ui.log 2>&1; echo "MAKE_EXIT=$?"`

Expected: `MAKE_EXIT=0`. Then read the bundle — `-quiet` prints no per-test output, so a skipped case is invisible in the log:

Run: `cd "$REPO" && BUNDLE=$(ls -td ~/Library/Developer/Xcode/DerivedData/MotionApp-*/Logs/Test/*.xcresult 2>/dev/null | head -1); echo "$BUNDLE"; xcrun xcresulttool get test-results summary --path "$BUNDLE" 2>/dev/null | head -30`

Expected: `result: Passed`, `totalTestCount: 4`, `passed: 4`, `failed: 0`, **`skipped: 0`**. A non-zero `skipped` means `Phase6SmokeTests` hit a precondition guard — most likely fewer than two image materials, or a missing Character/Driver material, which hard-fails rather than skips. That is an environment gap: restore test material, do not weaken the assertion.

Then confirm the Phase 6 case actually ran and the spend counter was zero:

Run: `xcrun xcresulttool get test-results tests --path "$BUNDLE" 2>/dev/null | grep -iE 'Phase6Smoke|testCrossBuildDropAndLibrary|Passed|Skipped' | head -20`

Expected: `Phase6SmokeTests.testCrossBuildDropAndLibrary` listed as Passed. Its closing `uitest.recordedSpends == "0"` assertion is inside that test, so a Pass is the zero-spend proof.

This run is also the only live evidence that Task 7's Batch-mode Clear works: `clearFromBatch` taps it and asserts both the header's `"0 jobs"` and that `Character`'s slot value returns to `"Missing required"`. If it passed, the button is real and it actually emptied the draft — the slot assertion is the load-bearing one, because `"0 jobs"` is already true on the skip path before the tap (`drafts.py:294-307` counts an incomplete edited job as zero).

Three things this run must settle that no free gate can:

- **If the skip path fires, confirm the cleanup assertions would still be heard.** `clearFromBatch` is non-throwing and its `XCTAssertTrue`s record failures without stopping execution, then `throw XCTSkip` follows immediately. Whether XCTest reports that combination as `failed` or `skipped` was not establishable by reading — Task 7's review raised it and the controller could not settle it either. If the run reports `skipped: 0`, the question is moot for this run; record that. If it skips, record which way XCTest resolved it, because a skip that masks a failed cleanup assertion means the next run silently fails its precondition instead of reporting the real cause.
- **Look at the Batch-mode Clear's placement in the screenshots.** It is leading-aligned and content-sized inside the Batch arm's `VStack`, while Single's sits trailing inside `editorActions`'s `HStack` because the sibling "Add to batch" carries `.frame(maxWidth: .infinity)`. Same button, same modifiers, different horizontal position between modes. Deferred here rather than guessed at. If it looks unbalanced, the fix belongs at the call site (`NewJobView.swift:111`) and **not** on the shared `clearAction` property — the one-implementation invariant is worth more than the alignment.
- **If `clearFromBatch` fails on its slot assertion while `"0 jobs"` would have passed, read `TG_PIPELINE` on the VPS before touching the test.** `clear()` resets `draft.pipeline` to the server default (`drafts.py:243`, `:485-490`), and `BatchComposerSection` renders its amber text *instead of any slot row* when the pipeline is not batch-supported (`BatchComposerSection.swift:20-26`, `supports` at `BatchComposer.swift:53-56`) — so `app.buttons["Character"]` would never appear and the wait would time out with Clear having worked correctly. All three `tryon-*` pipelines are batch-supported and all six carry `character` in `required`, so only the `supports` gate can hide the row. `scripts/vps/README.md:962` records the VPS default as `tryon-character-swap-enhance`, measured through the tunnel 2026-09-21. **Do not delete the assertion** if this fires: it fails loudly, in the safe direction, and it self-heals — the server-side clear already succeeded, so the draft is empty and the next run's precondition passes. That is the inverse of the vacuous-pass defect it replaced.

- [ ] **Step 6: Record every result**

Add each gate's exact result to the gate record in `docs/superpowers/specs/2026-09-24-phase-6-follow-ups-design.md`, replacing the `_not yet run_` rows for the gates that ran, with the date and who ran it. Record numbers, not adjectives — test counts, exit codes, and the `.xcresult` bundle name for the UI run, as Phase 6's record does, so each result is traceable to an artifact. Leave the VPS-check, deploy and post-deploy rows as `_not yet run_`: they are the controller's, after the merge.

Do not restate a result in prose elsewhere in the file — the table is the single place a gate result is written down.

Flip the Status line to `implemented on \`feat/phase-6-follow-ups\`, free and live gates swept, awaiting the VPS check and merge`.

```bash
cd "$REPO"
motions-studio/setup/scrub-secrets.sh --check
git add docs/superpowers/specs/2026-09-24-phase-6-follow-ups-design.md
git commit -m "docs(ios): record the phase 6 follow-up gates" -m "Numbers and the xcresult bundle name, so each result is traceable to an artifact rather than resting on the word passed.

Co-Authored-By: Qwen Code <noreply@qwen.com>"
```

---

## Handoff to the controller

Not tasks — these need a human decision or shared-state access, and the executor must stop and ask rather than do them.

1. **VPS pre-merge check.** This branch touches `scripts/**`, so merging auto-deploys `motion-bot` and restarts the phone API with it. Before merging, check the VPS for a live drain, Phase A, pod lease or migration:
   `doctl compute ssh motion-vps --ssh-command 'cd /opt/motion-clone && pgrep -af "[d]rain.py|[b]atch_run.py|[p]hase_a" ; cat batch/*.state.json 2>/dev/null | head -40 ; grep -c GPU_INSTANCE_ID .env'`
   Use `--ssh-command`, not a positional command — the positional form opens an interactive shell and hangs. The `[d]` bracket trick keeps `pgrep` from matching itself. A restart mid-drain does not lose the job (state is on disk), but do the check anyway.
2. **Push and open the PR.** `main` carries one unpushed docs commit (`c931306`, this branch's spec); decide whether to push `main` first or let the PR carry it. Creating a PR needs `GH_TOKEN="$(gh auth token --user doanhthuc)" gh pr create …` — the active `spartan-thucpham` account is read-only. Use `--body-file`, never a heredoc body: backticks in a heredoc execute.
3. **Ask which merge method before merging.** Phase 6 was merged with `--merge` and then rewritten to a squash on request. Ask first; do not assume.
4. **Verify the deploy.** The workflow is `.github/workflows/deploy-bot.yml`, triggered by `scripts/**`. `scripts/vps/deploy-bot.sh` does `git fetch` + `git reset --hard origin/main` + `systemctl restart motion-bot`, so it follows rewritten history too. Record the run id.
5. **Post-deploy `make ios-contract`.** 14/14, and confirm a resume refusal still decodes. Record it as a separate gate row from the pre-deploy run.
6. **Amend `docs/superpowers/swiftui-app-progress.md`.** Replace the four follow-up entries this closes with what shipped; the `MigrateFlow` residual loses its "Residual" qualifier; the Batch-mode `Clear` papercut entry goes; "Next work" is rewritten; the gate record gains this branch's rows. The `Phase6SmokeTests` "switch to Single before clearing" evidence in §"Notes on the `make ios-ui-test` run" is now historical — re-word it, do not delete it, because it was the proof the papercut was real. Two items stay open and must remain recorded: the `not_found` collision (spec §1) and the `app/` owner coupling (spec §10).
