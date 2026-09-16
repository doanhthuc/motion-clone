# Local try-on before the GPU spend decision — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop a GPU stock-out from re-billing Gemini for try-on work already on disk, and move Phase A ahead of the spend panel so the stock the user decides on is measured at the moment of the decision.

**Architecture:** Two independently shippable changes. **A** (Tasks 1-5) makes the journal's "already done" verdict params-aware, adds the recovery paths that were missing, and makes `_do_confirm` ask rather than guess. **B** (Tasks 6-10) gives `drain.py` a phase-A-only mode and moves the bot's `[Run]` button onto it, so the GPU panel renders after the try-on results exist. Neither change adds a second writer to the journal and neither adds a second path to `CONFIRM=yes`.

**Tech Stack:** Python 3 `unittest`, `unittest.mock`. No new dependencies. No GPU, no pod, no network in any test.

**Spec:** `docs/superpowers/specs/2026-09-16-local-tryon-before-gpu-spend-design.md` — the plan argues from the spec; read both. Spec sections are cited as §N.

## Global Constraints

Every task's requirements implicitly include this section.

- **Language.** New code, comments, commit messages and docs in English. Much of `scripts/` is Vietnamese — that is legacy, not a pattern to copy, and do not translate it in passing (QWEN.md).
- **No `# #region ALD …` markers.** That style is retired.
- **Comments explain *why*,** with the measured number and the date it was measured. Never narrate what the next line does.
- **Indent.** Four spaces in Python. Two in the `Makefile`'s shell lines — but recipe lines must start with a **TAB**, not spaces.
- **Naming.** `snake_case` Python. Tests are `test_<behavior>.py` files with `test_<scenario>` methods.
- **Secrets gate.** `bash motions-studio/setup/scrub-secrets.sh --check` must exit 0 before every commit.
- **Commit trailer.** None. Recent history carries `Co-Authored-By: Claude Sonnet 5`; do not copy it, and do not invent a different one.
- **Staging.** Never `git add -A`. Stage only the paths the task names.
- **Money invariants, unchanged by this plan:**
  - `CONFIRM=yes` appears in exactly one executable line: `scripts/tgbot/run.py:261`, inside `start_drain`'s `if not dry_run`.
  - `start_drain` has exactly two call sites in `scripts/tgbot/bot.py`: `_do_confirm` and `_do_resume`.
  - Nothing outside `scripts/batchlib/` writes `batch/<name>.state.json`.
- **One answer to "is this run's try-on local".** `_local_tryon_stage` (`scripts/batchlib/runner.py:382`) is the only source. No caller re-derives it. Its own docstring states why: two places answering differently means a batch either calls Gemini for the wrong run or waits for a pod it does not need.
- **Test commands.** Whole suite: `make batch-test`. One module: `python3 -m unittest scripts.tests.test_batch_runner -v`. One test: `python3 -m unittest scripts.tests.test_batch_runner.TestX.test_y -v`. All run from the repo root.
- **No task in this plan rents a pod.** If a step appears to need one, the step is wrong.

## File Structure

| File | Responsibility | Touched by |
|---|---|---|
| `scripts/batchlib/runner.py` | Owns the journal and both phases. The only module allowed to decide whether a stage is reusable. | 1, 2, 3 |
| `scripts/batch_run.py` | CLI over the runner. | 2 |
| `scripts/drain.py` | The only thing that rents a pod. | 2, 6 |
| `Makefile` | Target plumbing for the two new flags. | 2, 6 |
| `scripts/tgbot/run.py` | The money gate and every subprocess the bot launches. | 2, 7 |
| `scripts/tgbot/bot.py` | The chat surface: cards, callbacks, ticks. | 4, 5, 8, 9, 10 |
| `scripts/tests/test_batch_runner.py` | Runner unit tests. | 1, 2, 3 |
| `scripts/tests/test_batch_run.py` | `batch_run.main()` tests. | 2 |
| `scripts/tests/test_batch_drain.py` | `drain.py` tests. | 2, 6 |
| `scripts/tests/test_batch_tgrun.py` | `tgbot/run.py` tests. | 2, 7 |
| `scripts/tests/test_batch_bot.py` | Bot surface tests. | 4, 5, 8, 9, 10 |

**On "two commits."** The spec ships A and B as two changes, A first, because A stops a live money leak and is testable without B. Each *task* still ends in its own commit — that is what makes a task independently reviewable and bisectable. So A is five commits and B is five. If you want two literal commits, squash each series at the end; do not squash across the A/B boundary.

---

# Commit A — retry must not re-bill Gemini

## Task 1: `local_tryon_reusable`, and the Phase A skip becomes params-aware

**Files:**
- Modify: `scripts/batchlib/runner.py` (new helper beside `_local_tryon_stage`, ~line 382; the skip inside `run_local_phase._one`, ~line 482)
- Test: `scripts/tests/test_batch_runner.py`

**Interfaces:**
- Consumes: `effective_stage_params` (already imported from `.pipelines`), `Run`, `Path`
- Produces: `local_tryon_reusable(run: Run, stage_name: str, recorded: dict, dest: Path) -> bool` — used by Task 3's `preserved_local_tryon`, Task 4's card count and Task 5's chooser. **The signature must not change after this task.**

- [ ] **Step 1: Write the failing tests**

Add to `scripts/tests/test_batch_runner.py`. The class needs the module-level fixtures already in that file: `MANIFEST_TRYON_GEMINI`, `_fixture_tryon`, `GEMINI_SETTINGS`, `load_manifest`, `run_local_phase`, `tempfile`, `mock`, `Path`.

Also add `local_tryon_reusable` to the existing `from batchlib.runner import (...)` block at the top of the file.

```python
class TestLocalTryonReuseIsParamsAware(unittest.TestCase):
    """A journalled try-on may stand in for a request ONLY at the same params.

    run_id_for (tgbot/job.py:90) hashes material file stems and nothing else,
    so gemini and qwen-max over the same four files produce the SAME run id.
    A journal-only check therefore hands the gemini image to a qwen-max
    request: manifest valid, nothing raised, only the output wrong.
    """

    BATCH = "2026-09-16-0900"

    def _first_pass(self, tmp: Path) -> None:
        manifest = load_manifest(_fixture_tryon(tmp, MANIFEST_TRYON_GEMINI))

        def fake(run, params, settings_, out_path):
            out_path.write_bytes(b"png")
            return 1, 3

        with mock.patch("batchlib.runner.run_local_tryon", fake):
            run_local_phase(settings=GEMINI_SETTINGS, manifest=manifest,
                            out_root=tmp / "out", batch_id=self.BATCH,
                            resume=False, log=lambda _m: None)

    def _second_pass(self, tmp: Path, text: str, **kwargs):
        manifest = load_manifest(_fixture_tryon(tmp, text))
        calls: list[dict] = []

        def fake(run, params, settings_, out_path):
            calls.append(dict(params))
            out_path.write_bytes(b"png2")
            return 1, 4

        with mock.patch("batchlib.runner.run_local_tryon", fake):
            result = run_local_phase(settings=GEMINI_SETTINGS, manifest=manifest,
                                     out_root=tmp / "out", batch_id=self.BATCH,
                                     resume=True, log=lambda _m: None, **kwargs)
        return calls, result

    def test_same_params_second_pass_calls_gemini_zero_times(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            self._first_pass(tmp)
            calls, result = self._second_pass(tmp, MANIFEST_TRYON_GEMINI)
            self.assertEqual(calls, [])
            # `done` is what THIS invocation actually did — a reused stage is
            # not new work and must not be reported as such (run_local_phase's
            # own docstring makes that distinction).
            self.assertEqual(result.done, [])
            self.assertEqual(result.failed, {})

    def test_different_provider_reruns_instead_of_reusing_the_image(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            self._first_pass(tmp)
            calls, result = self._second_pass(
                tmp, MANIFEST_TRYON_GEMINI.replace("provider: gemini",
                                                   "provider: qwen-max"))
            self.assertEqual([c.get("provider") for c in calls], ["qwen-max"])
            self.assertEqual(result.done, ["runA"])

    def test_helper_is_false_when_the_file_has_been_cleaned_away(self):
        # `make batch-clean` deletes runs/ and keeps _final/, so "journal says
        # done" outliving the file is a normal state, not a corrupt one.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            self._first_pass(tmp)
            manifest = load_manifest(_fixture_tryon(tmp, MANIFEST_TRYON_GEMINI))
            run = manifest.runs[0]
            run_dir = tmp / "out" / self.BATCH / "runs" / run.id
            dest = stage_dest(run, run_dir, "tryon")
            recorded = {"status": "done",
                        "params_manifest": effective_stage_params(
                            "tryon", run.stage_params.get("tryon"))}
            self.assertTrue(local_tryon_reusable(run, "tryon", recorded, dest))
            dest.unlink()
            self.assertFalse(local_tryon_reusable(run, "tryon", recorded, dest))
```

The third test also needs `stage_dest` and `effective_stage_params` imported. `stage_dest` is already in that file's `from batchlib.runner import (...)` block; add `from batchlib.pipelines import effective_stage_params` next to the existing `from batchlib.pipelines import PIPELINES`.

- [ ] **Step 2: Run them, verify they fail for the right reason**

Run: `python3 -m unittest scripts.tests.test_batch_runner.TestLocalTryonReuseIsParamsAware -v`

Expected: `test_different_provider_reruns_instead_of_reusing_the_image` FAILS — today the skip is params-blind, so `calls` is `[]` instead of `["qwen-max"]`. The other two fail with `ImportError: cannot import name 'local_tryon_reusable'`. If instead the provider test *passes*, stop: something already compares params and this task's premise is wrong.

- [ ] **Step 3: Implement the helper**

In `scripts/batchlib/runner.py`, immediately after `_local_tryon_stage` (so the two sit together — one asks "is this stage local", the other "may we reuse what it produced"):

```python
def local_tryon_reusable(run: Run, stage_name: str, recorded: dict, dest: Path) -> bool:
    """True when a try-on already on disk may stand in for THIS run's request.

    Params are part of the question, not just "done + file exists" (2026-09-16):
    run_id_for (tgbot/job.py:90) hashes material file stems and nothing else,
    so gemini and qwen-max over the same four files produce the SAME run id.
    A journal-only verdict hands the gemini image to a qwen-max request — the
    manifest is valid, nothing raises, only the output is wrong. That is the
    failure shape _local_tryon_eligible's own docstring describes for cleanOnly.

    Compared against effective_stage_params so both sides of the == come from
    one derivation. No normalisation layer and no coercion that could drift
    from what Phase A actually sent.

    A false negative costs one Gemini call — cents. That is why this check is
    affordable here and NOT in run_one; see the spec's §5 for what the same
    check would cost on a paid stage.

    Three callers on purpose: run_local_phase's skip, preserved_local_tryon
    below, and through it the bot's reuse-or-rerun chooser and the stock-out
    card's "N/M preserved" count. A card counting with a looser rule than the
    runner skips with would promise preservation the runner declines to honour.
    """
    if recorded.get("status") != "done" or not dest.is_file():
        return False
    return recorded.get("params_manifest") == effective_stage_params(
        stage_name, run.stage_params.get(stage_name))
```

- [ ] **Step 4: Use it in the Phase A skip**

In `run_local_phase._one` (~line 482), replace:

```python
        if recorded.get("status") == "done" and dest.is_file():
            log(f"    {run.id}/{stage_name}: bỏ qua (đã xong local, {dest.name})")
            return False, None
```

with:

```python
        if local_tryon_reusable(run, stage_name, recorded, dest):
            log(f"    {run.id}/{stage_name}: bỏ qua (đã xong local, {dest.name})")
            return False, None
```

Keep the two-line Vietnamese comment above it ("Hai vế, giống hệt run_one…") — it explains why both halves are required, which is still true; the params clause is explained by the helper's own docstring, so do not duplicate it here.

- [ ] **Step 5: Run the new tests, verify they pass**

Run: `python3 -m unittest scripts.tests.test_batch_runner.TestLocalTryonReuseIsParamsAware -v`
Expected: 3 tests, OK.

- [ ] **Step 6: Run the whole suite — this changes a skip condition other tests depend on**

Run: `make batch-test`
Expected: OK, no failures. `TestCameraAlias.test_local_phase_keeps_alias_and_effective_params` and every `run_local_phase` test in `test_batch_runner.py` exercise this path; a regression here means the helper's params derivation disagrees with the one `_one` already had.

- [ ] **Step 7: Scrub and commit**

```bash
bash motions-studio/setup/scrub-secrets.sh --check
git add scripts/batchlib/runner.py scripts/tests/test_batch_runner.py
git commit -F - <<'EOF'
Batch runner: make the local try-on skip params-aware

run_id_for hashes material file stems and nothing else, so gemini and
qwen-max over the same four files produce the SAME run id. The skip in
run_local_phase checked only status=="done" and the file on disk, so a
provider switch reused the previous provider's image: manifest valid,
nothing raised, only the output wrong.

Extracted as local_tryon_reusable rather than inlined, because the same
question is about to be asked from two more places — the stock-out
card's "N/M preserved" count and the bot's reuse-or-rerun chooser — and
a card that counts with a looser rule than the runner skips with would
promise preservation the runner then declines to honour.

A false negative costs one Gemini call. That is why the same check is
deliberately NOT added to run_one: there it compares a journalled
params_manifest against effective_stage_params recomputed at resume
time, so any change to params.py defaults would silently invalidate
every stage marked done and re-submit a 40-minute enhance to a GPU
billing $0.99/h.
EOF
```

---

## Task 2: `force` — re-run local try-on without losing the batch

**Files:**
- Modify: `scripts/batchlib/runner.py` (`run_local_phase` signature ~line 421, the skip from Task 1)
- Modify: `scripts/batch_run.py` (argparse ~line 106, the `run_local_phase` call ~line 158)
- Modify: `scripts/drain.py` (argparse ~line 297, `phase_a` list ~line 324)
- Modify: `Makefile` (`drain:` target, lines 81-84)
- Modify: `scripts/tgbot/run.py` (`start_drain`, lines 241-266)
- Test: `scripts/tests/test_batch_runner.py`, `scripts/tests/test_batch_run.py`, `scripts/tests/test_batch_drain.py`, `scripts/tests/test_batch_tgrun.py`

**Interfaces:**
- Consumes: `local_tryon_reusable` (Task 1)
- Produces:
  - `run_local_phase(..., force: bool = False)`
  - `batch_run.py --force-local`
  - `drain.py --force-local`
  - `make drain FILE=… FORCE_LOCAL=1`
  - `start_drain(manifest_path, *, dry_run: bool, resume: bool = False, force_local: bool = False)`

Tasks 5 and 10 call `start_drain(..., force_local=True)`; the keyword name is load-bearing.

- [ ] **Step 1: Write the failing runner test**

Add a method to `TestLocalTryonReuseIsParamsAware` in `scripts/tests/test_batch_runner.py` (it already has `_first_pass` and `_second_pass`):

```python
    def test_force_reruns_a_tryon_the_journal_says_is_done(self):
        # The user's other intent at the chooser: "that image came out wrong,
        # roll it again." Force must bypass the skip WITHOUT minting a new
        # batch id — a fresh id would orphan any pod stage already done,
        # which is the bug resolve_batch_id exists to prevent.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            self._first_pass(tmp)
            calls, result = self._second_pass(tmp, MANIFEST_TRYON_GEMINI, force=True)
            self.assertEqual(len(calls), 1)
            self.assertEqual(result.done, ["runA"])
            self.assertEqual(result.state["batch"], self.BATCH)
```

- [ ] **Step 2: Run it, verify it fails**

Run: `python3 -m unittest scripts.tests.test_batch_runner.TestLocalTryonReuseIsParamsAware.test_force_reruns_a_tryon_the_journal_says_is_done -v`
Expected: FAIL — `TypeError: run_local_phase() got an unexpected keyword argument 'force'`.

- [ ] **Step 3: Implement `force` in `run_local_phase`**

In `scripts/batchlib/runner.py`, extend the signature (~line 421):

```python
def run_local_phase(*, settings: Settings, manifest: Manifest, out_root: Path, batch_id: str,
                    resume: bool, fail_fast: bool = False, log: Callable[[str], None] = print,
                    pool_size: int = 4, force: bool = False) -> LocalPhaseResult:
```

Add to its docstring, after the existing paragraph about the pool:

```
    `force` bypasses the reuse check for EVERY run in the batch, not only the
    ones whose params changed. Per-run forcing would need a notion of "which
    run did the user actually edit" and guessing wrong there silently reuses
    the bad image the user was trying to get away from — worse than one extra
    Gemini call. It never touches the batch id or any other stage's journal
    entry: force re-runs try-on, it does not start a new batch.
```

And change the skip (Task 1's line) to:

```python
        if not force and local_tryon_reusable(run, stage_name, recorded, dest):
```

- [ ] **Step 4: Run the runner test, verify it passes**

Run: `python3 -m unittest scripts.tests.test_batch_runner.TestLocalTryonReuseIsParamsAware -v`
Expected: 4 tests, OK.

- [ ] **Step 5: Write the failing `batch_run.py` CLI test**

Add to `TestMainPhaA` in `scripts/tests/test_batch_run.py`. It reuses that class's existing harness shape — patch the three things that touch money (`load_settings`, `health_ok`, `run_batch`) plus `run_local_tryon`, and silence stdout/stderr:

```python
    def test_force_local_reaches_run_local_phase(self):
        with tempfile.TemporaryDirectory() as d:
            p = _manifest_tryon_gemini(Path(d))

            def fake_run_local_tryon(run, params, settings_, out_path):
                out_path.write_bytes(b"fake")
                return 2, out_path.stat().st_size

            with mock.patch("batch_run.load_settings",
                            return_value=Settings(domain="pod.test", api_key="mk_test",
                                                  instance_id="", gemini_api_key="AIza" + "x" * 35)), \
                 mock.patch("batch_run.health_ok", return_value=False), \
                 mock.patch("batchlib.runner.run_local_tryon", fake_run_local_tryon), \
                 mock.patch("batch_run.run_local_phase", wraps=None) as m_phase, \
                 contextlib.redirect_stdout(io.StringIO()), \
                 contextlib.redirect_stderr(io.StringIO()):
                m_phase.return_value = LocalPhaseResult(ran=False)
                batch_run.main(["--file", str(p), "--force-local"])
            self.assertIs(m_phase.call_args.kwargs["force"], True)

    def test_no_force_local_flag_means_force_false(self):
        with tempfile.TemporaryDirectory() as d:
            p = _manifest_tryon_gemini(Path(d))
            with mock.patch("batch_run.load_settings",
                            return_value=Settings(domain="pod.test", api_key="mk_test",
                                                  instance_id="", gemini_api_key="AIza" + "x" * 35)), \
                 mock.patch("batch_run.health_ok", return_value=False), \
                 mock.patch("batch_run.run_local_phase") as m_phase, \
                 contextlib.redirect_stdout(io.StringIO()), \
                 contextlib.redirect_stderr(io.StringIO()):
                m_phase.return_value = LocalPhaseResult(ran=False)
                batch_run.main(["--file", str(p)])
            self.assertIs(m_phase.call_args.kwargs["force"], False)
```

- [ ] **Step 6: Run them, verify they fail**

Run: `python3 -m unittest scripts.tests.test_batch_run.TestMainPhaA -v`
Expected: the two new tests FAIL — `argparse` exits 2 on the unrecognised `--force-local`.

- [ ] **Step 7: Implement the CLI flag**

In `scripts/batch_run.py`, add next to the existing `--no-start` argument:

```python
    ap.add_argument("--force-local", action="store_true",
                    help="re-run local try-on even when the journal says it is done")
```

The help string is English even though every other string in this file's argparse surface is Vietnamese. Deliberate, and it is the only such exception in this plan: QWEN.md's "do not translate it in passing" forbids rewriting *existing* Vietnamese, it does not license writing *new* Vietnamese, and the identical flag is documented in English one layer down in `drain.py`. Two languages describing one switch across two layers is the worse inconsistency. This brief originally mandated the Vietnamese string; the Task 2 review caught it and the controller ruled against its own plan.

and pass it through at the `run_local_phase` call (~line 158):

```python
        local_result = run_local_phase(settings=settings, manifest=manifest, out_root=ROOT / "out",
                                       batch_id=decision.batch_id, resume=decision.resumed,
                                       fail_fast=args.fail_fast, pool_size=pool_size,
                                       force=args.force_local)
```

Run: `python3 -m unittest scripts.tests.test_batch_run.TestMainPhaA -v` — expected OK.

- [ ] **Step 8: Write the failing `drain.py` test**

Add to `scripts/tests/test_batch_drain.py`:

```python
class TestPhaseAForwardsForceLocal(unittest.TestCase):
    """--force-local must reach batch_run.py in the Phase A invocation, not
    only in the post-provisioning one.

    A flag that arrives only in the second invocation is not a no-op — that
    invocation is a full batch_run.main, which calls run_local_phase before
    run_batch, so the try-on would still be regenerated ahead of motion and
    enhance. It is worse than a no-op: it regenerates after provision() and
    wait_and_bootstrap(), so the GPU bills while the process waits on a hosted
    Gemini call.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.manifest = self.tmp / "tg-1.yaml"
        self.manifest.write_text(
            "runs:\n  - id: a\n    pipeline: tryon-motion-enhance\n"
            "    inputs: {character: /tmp/c.png, outfit: /tmp/o.png, driver: /tmp/d.mp4}\n"
            "    tryon: { provider: gemini }\n", encoding="utf-8")

    def test_force_local_is_forwarded_to_phase_a(self):
        seen: list[tuple] = []
        with mock.patch.object(drain, "batch_run",
                               side_effect=lambda *a: seen.append(a) or drain.EXIT_NEEDS_POD), \
             mock.patch.object(drain, "provision", side_effect=AssertionError("must not rent")):
            with mock.patch.object(sys, "argv",
                                   ["drain.py", "--file", str(self.manifest),
                                    "--yes", "--force-local"]):
                with self.assertRaises(AssertionError):
                    drain.main()
        self.assertIn("--force-local", seen[0])

    def test_absent_flag_does_not_add_it(self):
        seen: list[tuple] = []
        with mock.patch.object(drain, "batch_run",
                               side_effect=lambda *a: seen.append(a) or drain.EXIT_NEEDS_POD), \
             mock.patch.object(drain, "provision", side_effect=AssertionError("must not rent")):
            with mock.patch.object(sys, "argv",
                                   ["drain.py", "--file", str(self.manifest), "--yes"]):
                with self.assertRaises(AssertionError):
                    drain.main()
        self.assertNotIn("--force-local", seen[0])
```

This needs `sys` imported in `test_batch_drain.py` — it already is (line 3).

- [ ] **Step 9: Run it, verify it fails**

Run: `python3 -m unittest scripts.tests.test_batch_drain.TestPhaseAForwardsForceLocal -v`
Expected: FAIL — `argparse` exits 2, raised as `SystemExit`, not `AssertionError`.

- [ ] **Step 10: Implement in `drain.py`**

Add the argument next to `--resume` (~line 300):

```python
    ap.add_argument("--force-local", action="store_true",
                    help="re-run local try-on even when the journal says it is done")
```

and extend the `phase_a` list (~line 324):

```python
    phase_a = ["--file", str(manifest_path), "--no-start"]
    if args.resume:
        phase_a.append("--resume")
    if args.force_local:
        phase_a.append("--force-local")
```

Run: `python3 -m unittest scripts.tests.test_batch_drain.TestPhaseAForwardsForceLocal -v` — expected OK.

- [ ] **Step 11: Wire the Makefile**

In `Makefile`, replace the `drain:` target (lines 81-84). **The two recipe lines must begin with a TAB.**

```make
drain: ## Rent a pod, run FILE, destroy it (dry run unless CONFIRM=yes)
	@test -n "$(FILE)" || { echo "usage: make drain FILE=batch/….yaml [CONFIRM=yes] [RESUME=1] [FORCE_LOCAL=1]"; exit 1; }
	@python3 scripts/drain.py --file "$(FILE)" \
		$(if $(filter yes,$(CONFIRM)),--yes) $(if $(RESUME),--resume) \
		$(if $(FORCE_LOCAL),--force-local)
```

Verify the recipe lines are tabs, not spaces:

Run: `grep -nP '^\t' Makefile | sed -n '1,5p'`
Expected: the `drain:` recipe lines appear. If `grep -c $'^\t\t\$(if $(FORCE_LOCAL)' Makefile` returns 0 the indentation is wrong.

- [ ] **Step 12: Write the failing `run.py` test**

Add to `scripts/tests/test_batch_tgrun.py`. It follows `TestDrainRunning`'s isolation pattern — save and restore the module globals, and point the log somewhere disposable:

```python
class TestStartDrainArgv(unittest.TestCase):
    """start_drain's argv is the money gate's only output. These assert on the
    list it would run, never on a real `make`."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.manifest = self.tmp / "tg-1.yaml"
        self.manifest.write_text("runs: []\n", encoding="utf-8")
        self._orig_running = dict(run_mod._RUNNING)

    def tearDown(self):
        run_mod._RUNNING.clear()
        run_mod._RUNNING.update(self._orig_running)

    def _argv(self, **kwargs) -> list[str]:
        with mock.patch.object(run_mod.subprocess, "Popen") as popen:
            popen.return_value = _FakeProc(poll_return=None)
            run_mod.start_drain(self.manifest, **kwargs)
        return popen.call_args.args[0]

    def test_force_local_becomes_the_make_variable(self):
        self.assertIn("FORCE_LOCAL=1",
                      self._argv(dry_run=False, resume=True, force_local=True))

    def test_omitted_force_local_adds_nothing(self):
        self.assertNotIn("FORCE_LOCAL=1", self._argv(dry_run=False))

    def test_force_local_does_not_imply_confirm(self):
        # A dry run stays a dry run no matter what else is set: CONFIRM=yes is
        # gated on dry_run alone, and that gate is the whole money invariant.
        argv = self._argv(dry_run=True, force_local=True)
        self.assertIn("FORCE_LOCAL=1", argv)
        self.assertNotIn("CONFIRM=yes", argv)
```

- [ ] **Step 13: Run it, verify it fails**

Run: `python3 -m unittest scripts.tests.test_batch_tgrun.TestStartDrainArgv -v`
Expected: FAIL — `TypeError: start_drain() got an unexpected keyword argument 'force_local'`.

- [ ] **Step 14: Implement in `run.py`**

```python
def start_drain(manifest_path: Path, *, dry_run: bool,
                resume: bool = False, force_local: bool = False) -> subprocess.Popen:
```

and in the body, after the existing `if resume:` block and **before** `if not dry_run:`:

```python
    if force_local:
        argv.append("FORCE_LOCAL=1")
```

Add to the docstring, after the paragraph about `resume`:

```
    `force_local` forwards FORCE_LOCAL=1 (drain.py's --force-local) for the
    other half of the bot's reuse-or-rerun chooser: the user looked at a
    finished try-on and asked for a different one. It is placed before the
    dry_run gate deliberately — the gate must stay the last thing appended so
    that "CONFIRM=yes appears iff dry_run is False" remains readable as a
    single trailing condition.
```

- [ ] **Step 15: Run the tests, verify they pass**

Run: `python3 -m unittest scripts.tests.test_batch_tgrun.TestStartDrainArgv -v`
Expected: 3 tests, OK.

- [ ] **Step 16: Full suite, then verify the money invariant by hand**

Run: `make batch-test`
Expected: OK.

Run: `grep -rn 'CONFIRM=yes' scripts/tgbot/ --include=*.py`
Expected: hits in `run.py` only — the docstrings plus exactly one `argv.append("CONFIRM=yes")`.

- [ ] **Step 17: Scrub and commit**

```bash
bash motions-studio/setup/scrub-secrets.sh --check
git add scripts/batchlib/runner.py scripts/batch_run.py scripts/drain.py \
        Makefile scripts/tgbot/run.py scripts/tests/test_batch_runner.py \
        scripts/tests/test_batch_run.py scripts/tests/test_batch_drain.py \
        scripts/tests/test_batch_tgrun.py
git commit -F - <<'EOF'
Batch runner: add --force-local to re-run try-on without losing the batch

The other half of reuse-or-rerun: sometimes the finished try-on is the
thing the user wants gone (/tryon exists for exactly that). Deleting the
journal entries from the bot would have been less plumbing but puts a
second writer on state.json, which run_local_phase and run_one both
mutate under a lock — a bot-side edit races whichever is running.

Threaded the same way RESUME=1 already is: batch_run -> drain ->
Makefile -> run.py. force bypasses the reuse check for every run in the
batch rather than only the edited ones; per-run forcing needs a notion
of "which run did the user actually edit", and guessing wrong silently
reuses the bad image, which is worse than one extra Gemini call.

Placed before start_drain's dry_run gate so "CONFIRM=yes appears iff
dry_run is False" still reads as one trailing condition.
EOF
```

---

## Task 3: provenance stamp, and a count the card can trust

**Files:**
- Modify: `scripts/batchlib/runner.py` (`run_local_phase._one`'s journal write ~line 524; `run_one`'s skip ~line 172; new helpers)
- Test: `scripts/tests/test_batch_runner.py`

**Interfaces:**
- Consumes: `local_tryon_reusable` (Task 1), `_local_tryon_stage` (existing)
- Produces:
  - Journal entries written by Phase A carry `"phase": "local"`.
  - `preserved_local_tryon(manifest: Manifest, state: dict, out_root: Path) -> tuple[int, int]` returning `(reusable, total)`. Task 4 and Task 5 both call it.

- [ ] **Step 1: Write the failing tests**

Add to `scripts/tests/test_batch_runner.py`. Add `preserved_local_tryon` to the `from batchlib.runner import (...)` block.

```python
class TestLocalProvenance(unittest.TestCase):
    """Phase A stamps its journal entries so run_one will not pass a local
    image off as a pod stage's output.

    Reachable from the bot: /provider switches gemini -> qwen, the stage
    stops being local-eligible, and run_one would otherwise skip it on the
    strength of an entry Phase A wrote for a different provider.
    """

    BATCH = "2026-09-16-0900"

    def _phase_a(self, tmp: Path, text: str = MANIFEST_TRYON_GEMINI):
        manifest = load_manifest(_fixture_tryon(tmp, text))

        def fake(run, params, settings_, out_path):
            out_path.write_bytes(b"png")
            return 1, 3

        with mock.patch("batchlib.runner.run_local_tryon", fake):
            result = run_local_phase(settings=GEMINI_SETTINGS, manifest=manifest,
                                     out_root=tmp / "out", batch_id=self.BATCH,
                                     resume=False, log=lambda _m: None)
        return manifest, result

    def test_phase_a_stamps_its_entries(self):
        with tempfile.TemporaryDirectory() as d:
            _manifest, result = self._phase_a(Path(d))
            stage = result.state["runs"]["runA"]["stages"]["tryon"]
            self.assertEqual(stage["phase"], "local")
            # On disk, not just in memory — run_one reads the file.
            on_disk = load_state(result.state_file)["runs"]["runA"]["stages"]["tryon"]
            self.assertEqual(on_disk["phase"], "local")

    def test_run_one_refuses_a_local_entry_for_a_stage_that_moved_to_the_pod(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            self._phase_a(tmp)
            # Same run id (run_id_for hashes file stems only), different
            # provider: no longer local-eligible, so `tryon` belongs to the pod.
            pod_manifest = load_manifest(_fixture_tryon(
                tmp, MANIFEST_TRYON_GEMINI.replace("provider: gemini", "provider: qwen")))
            out_dir = tmp / "out" / self.BATCH
            state_file = state_path_for(pod_manifest.path)
            state = load_state(state_file)
            submitted = self._run_pod_stages(pod_manifest, out_dir, state, state_file)
            self.assertIn("tryon", submitted)

    def test_run_one_still_reuses_an_unstamped_entry(self):
        # A journal written before the stamp exists. Unknown provenance must
        # keep today's behaviour, or every batch in flight across the upgrade
        # re-runs its try-on on the pod.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            self._phase_a(tmp)
            manifest = load_manifest(_fixture_tryon(tmp, MANIFEST_TRYON_GEMINI))
            state_file = state_path_for(manifest.path)
            state = load_state(state_file)
            del state["runs"]["runA"]["stages"]["tryon"]["phase"]
            save_state(state_file, state)
            out_dir = tmp / "out" / self.BATCH
            submitted = self._run_pod_stages(manifest, out_dir, state, state_file)
            self.assertNotIn("tryon", submitted)

    def _run_pod_stages(self, manifest, out_dir, state, state_file) -> list[str]:
        """Run run_one with the whole pod surface faked, return what it submitted.

        download_output must really create the file: run_one promotes the last
        stage's output into _final/ with hardlink_to and falls back to copy2,
        and a mock that returns a byte count without writing anything makes
        both raise FileNotFoundError — an unrelated failure that would hide
        the assertion this helper exists to make.
        """
        submitted: list[str] = []

        def fake_submit(settings_, job_type, params, files):
            submitted.append(job_type)
            return f"job-{job_type}"

        def fake_download(settings_, job_id, dest, min_bytes):
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            Path(dest).write_bytes(b"x" * 4096)
            return 4096

        with mock.patch("batchlib.runner.submit_job", side_effect=fake_submit), \
             mock.patch("batchlib.runner.poll_job",
                        return_value={"status": "done", "params": {}}), \
             mock.patch("batchlib.runner.download_output", side_effect=fake_download):
            run_one(settings=SETTINGS, run=manifest.runs[0], out_dir=out_dir,
                    state=state, state_file=state_file,
                    resume=True, log=lambda _m: None)
        return submitted


class TestPreservedLocalTryon(unittest.TestCase):
    def test_counts_only_what_a_resume_would_actually_skip(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            manifest = load_manifest(_fixture_tryon(tmp, MANIFEST_HAI_RUN_GEMINI))

            def fake(run, params, settings_, out_path):
                out_path.write_bytes(b"png")
                return 1, 3

            with mock.patch("batchlib.runner.run_local_tryon", fake):
                result = run_local_phase(settings=GEMINI_SETTINGS, manifest=manifest,
                                         out_root=tmp / "out", batch_id="2026-09-16-0900",
                                         resume=False, log=lambda _m: None)
            self.assertEqual(
                preserved_local_tryon(manifest, result.state, tmp / "out"), (2, 2))

    def test_a_provider_change_drops_the_reusable_count_not_the_total(self):
        # The exact lie this function exists to prevent: a card reading
        # "2/2 preserved" for a batch about to re-run both.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            manifest = load_manifest(_fixture_tryon(tmp, MANIFEST_HAI_RUN_GEMINI))

            def fake(run, params, settings_, out_path):
                out_path.write_bytes(b"png")
                return 1, 3

            with mock.patch("batchlib.runner.run_local_tryon", fake):
                result = run_local_phase(settings=GEMINI_SETTINGS, manifest=manifest,
                                         out_root=tmp / "out", batch_id="2026-09-16-0900",
                                         resume=False, log=lambda _m: None)
            changed = load_manifest(_fixture_tryon(
                tmp, MANIFEST_HAI_RUN_GEMINI.replace("provider: gemini", "provider: qwen-max")))
            self.assertEqual(
                preserved_local_tryon(changed, result.state, tmp / "out"), (0, 2))

    def test_a_manifest_with_no_local_tryon_counts_zero_zero(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            manifest = load_manifest(_fixture(tmp, MANIFEST_MOT_RUN))
            self.assertEqual(preserved_local_tryon(manifest, {"runs": {}}, tmp / "out"),
                             (0, 0))
```

`save_state`, `load_state`, `state_path_for`, `run_one`, `SETTINGS`, `JobError`, `MANIFEST_HAI_RUN_GEMINI`, `MANIFEST_MOT_RUN`, `_fixture` are all already imported or defined at the top of that file.

- [ ] **Step 2: Run them, verify they fail**

Run: `python3 -m unittest scripts.tests.test_batch_runner.TestLocalProvenance scripts.tests.test_batch_runner.TestPreservedLocalTryon -v`
Expected: `ImportError: cannot import name 'preserved_local_tryon'`, and `test_phase_a_stamps_its_entries` FAILS with `KeyError: 'phase'`.

- [ ] **Step 3: Stamp the journal entry**

In `run_local_phase._one`'s success path (~line 524):

```python
        with lock:
            entry["stages"][stage_name] = {
                "status": "done", "elapsed_sec": elapsed, "file": str(dest), "bytes": size,
                "params_sent": dict(params), "params_manifest": dict(params),
                # Provenance, so run_one can tell a pod stage's output from a
                # local one. Without it a /provider switch away from a local
                # provider leaves run_one skipping a stage that now belongs to
                # the pod, on the strength of an image a different provider
                # made. Entries predating the stamp have no key and keep
                # today's behaviour — see _local_provenance_stale.
                "phase": "local"}
            save_state(state_file, state)
```

- [ ] **Step 4: Add the staleness predicate and use it in `run_one`**

In `scripts/batchlib/runner.py`, next to `local_tryon_reusable`:

```python
def _local_provenance_stale(run: Run, stage_name: str, recorded: dict) -> bool:
    """True when a journal entry Phase A wrote can no longer stand in for this run.

    Narrow on purpose — this is NOT a params check, and §5 of the spec records
    why: comparing a journalled params_manifest against effective_stage_params
    recomputed at resume time means any change to the stage defaults in
    pipelines.py silently invalidates every stage marked done, and at Phase B
    that re-submits a 40-minute enhance to a GPU billing $0.99/h.

    What it does catch is the one hole reachable from the bot: /provider moves
    a stage off the local providers, so _local_tryon_stage stops naming it, and
    the image Gemini made must not be passed off as the pod's output.

    A missing "phase" key means the entry predates the stamp. Unknown
    provenance gets today's behaviour (reuse), so a batch in flight across the
    upgrade is unaffected rather than silently re-run.
    """
    if recorded.get("phase") != "local":
        return False
    return _local_tryon_stage(run) != stage_name
```

In `run_one`'s skip check (~line 172), change:

```python
        if recorded.get("status") == "done" and dest.is_file():
```

to:

```python
        if (recorded.get("status") == "done" and dest.is_file()
                and not _local_provenance_stale(run, stage_name, recorded)):
```

Leave the long comment above it intact; it explains the deliberate absence of a `resume` gate, which is unchanged.

- [ ] **Step 5: Add `preserved_local_tryon`**

In `scripts/batchlib/runner.py`, after `_local_provenance_stale`:

```python
def preserved_local_tryon(manifest: Manifest, state: dict, out_root: Path) -> tuple[int, int]:
    """(reusable, total) — how many of this manifest's local try-ons a resume would skip.

    Lives here rather than in the bot because the answer has to come from the
    same two predicates run_local_phase skips with: _local_tryon_stage for
    "is this stage local at all", local_tryon_reusable for "may we reuse what
    it produced". A bot-side reimplementation is the second opinion
    _local_tryon_eligible's docstring warns about, and here it would show up as
    a stock-out card promising "4/4 preserved" for a batch whose provider
    changed and which is therefore about to re-run all four.

    Reads no journal of its own: the caller passes the state it already loaded,
    so the count and whatever else the caller does with that state cannot
    disagree about which file they read.
    """
    batch_id = str(state.get("batch") or "")
    runs = state.get("runs") or {}
    total = reusable = 0
    for run in manifest.runs:
        stage_name = _local_tryon_stage(run)
        if stage_name is None:
            continue
        total += 1
        recorded = ((runs.get(run.id) or {}).get("stages") or {}).get(stage_name) or {}
        run_dir = out_root / batch_id / "runs" / run.id
        if local_tryon_reusable(run, stage_name, recorded,
                                stage_dest(run, run_dir, stage_name)):
            reusable += 1
    return reusable, total
```

- [ ] **Step 6: Run the new tests, verify they pass**

Run: `python3 -m unittest scripts.tests.test_batch_runner.TestLocalProvenance scripts.tests.test_batch_runner.TestPreservedLocalTryon -v`
Expected: 6 tests, OK.

If `test_run_one_refuses_a_local_entry_for_a_stage_that_moved_to_the_pod` fails with an error other than the expected `JobError`, the fixture's pipeline is being walked differently than assumed — read the traceback before adjusting the assertion, do not weaken it.

- [ ] **Step 7: Full suite**

Run: `make batch-test`
Expected: OK. `run_one`'s skip is exercised by many `run_batch` tests; the added clause must be a no-op for every entry without a `phase` key.

- [ ] **Step 8: Scrub and commit**

```bash
bash motions-studio/setup/scrub-secrets.sh --check
git add scripts/batchlib/runner.py scripts/tests/test_batch_runner.py
git commit -F - <<'EOF'
Batch runner: stamp local try-on entries, and count them with one predicate

Two additions, both about the journal not lying.

Phase A now writes "phase": "local" into each entry, and run_one refuses
to reuse a stamped entry once _local_tryon_stage stops naming that stage.
That is the /provider path: gemini -> qwen moves the try-on onto the pod,
and without the stamp run_one skips it on the strength of an image a
different provider made. Entries with no key predate the stamp and keep
today's behaviour, so a batch in flight across the upgrade is unaffected.

This is deliberately NOT a params check at Phase B. Comparing a journalled
params_manifest against effective_stage_params recomputed at resume time
means any change to params.py defaults silently invalidates every stage
marked done — re-submitting a 40-minute enhance to a GPU billing $0.99/h,
on a resume that exists to avoid paying twice. Spec §5.

preserved_local_tryon gives the bot a count built from the same two
predicates the runner skips with, so a stock-out card cannot announce
"4/4 preserved" for a batch about to re-run all four.
EOF
```

---

## Task 4: the stock-out card gets a Retry button that resumes

**Files:**
- Modify: `scripts/tgbot/bot.py` (imports ~line 28-31; `_CB_RECOVER_*` constants ~line 1360; `_deliver_provision_failure` ~line 553; the callback dispatch ~line 1617)
- Test: `scripts/tests/test_batch_bot.py`

**Interfaces:**
- Consumes: `preserved_local_tryon` (Task 3), `_do_resume` (existing, `bot.py:4253`)
- Produces: `_CB_RECOVER_RETRY = "rec:retry:"` (Task 9's copy references it)

- [ ] **Step 1: Write the failing tests**

Add to `TestProvisionFailureRecovery` in `scripts/tests/test_batch_bot.py` (the class that renders the card) and `TestProvisionFailureRecoveryButtons` (the class that taps it). Both `setUp` methods already build a temp `bot.ROOT`, a `.env`, a manifest and a `ProvisionFailure`.

The existing `TestProvisionFailureRecovery._stock()` fixture marks the 5090 sold out at `EU-RO-1`, and its manifest is `tg-1.yaml`. For the count assertions, `TestProvisionFailureRecoveryButtons.setUp` writes a `motion-enhance` manifest — no local try-on — so its count is `(0, 0)`. Add a try-on manifest case in `TestProvisionFailureRecovery` instead, whose `_write_failure` helper already exists:

```python
    def _write_tryon_journal(self, *, reusable: int) -> None:
        """A manifest with two gemini try-ons and a journal saying `reusable`
        of them are done with matching params.

        params_manifest is computed with effective_stage_params, NOT written as
        a literal {"provider": "gemini"}: local_tryon_reusable compares against
        that function's output, which merges the stage's own defaults in, so a
        hand-written subset would never match and the count would read 0/2 for a
        batch that really is fully reusable.
        """
        self.manifest.write_text(
            "runs:\n"
            "  - id: runA\n    pipeline: tryon-motion-enhance\n"
            "    inputs: {character: /tmp/c.png, outfit: /tmp/o.png, driver: /tmp/d.mp4}\n"
            "    tryon: { provider: gemini }\n"
            "  - id: runB\n    pipeline: tryon-motion-enhance\n"
            "    inputs: {character: /tmp/c.png, outfit: /tmp/o.png, driver: /tmp/d.mp4}\n"
            "    tryon: { provider: gemini }\n", encoding="utf-8")
        params = effective_stage_params("tryon", {"provider": "gemini"})
        batch_dir = bot.ROOT / "out" / "2026-09-16-0900"
        stages = {}
        for i, run_id in enumerate(("runA", "runB")):
            run_dir = batch_dir / "runs" / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            dest = run_dir / "01-tryon.png"
            if i < reusable:
                dest.write_bytes(b"png")
                stages[run_id] = {"status": "done", "phase": "local", "file": str(dest),
                                  "params_manifest": params}
            else:
                stages[run_id] = {"status": "error", "phase": "local"}
        state_path_for(self.manifest).write_text(json.dumps(
            {"batch": "2026-09-16-0900", "runs": stages}), encoding="utf-8")

    def test_stock_out_card_names_how_many_tryons_survive(self):
        self._write_tryon_journal(reusable=2)
        self._write_failure(stock_out=True)
        with mock.patch("tgbot.bot.volume_datacenter", return_value="EU-RO-1"), \
             mock.patch("tgbot.bot.stock_at_cached", return_value=self._stock()):
            bot._deliver_provision_failure(
                self.tg, ME, self.manifest,
                read_provision_failure(provision_failure_path(self.manifest)))
        self.assertIn("2/2", self.tg.messages[-1])

    def test_a_provider_change_is_not_reported_as_preserved(self):
        # The card must count with the runner's own predicate. Saying "2/2
        # preserved" for a batch about to re-run both is the lie Task 3's
        # preserved_local_tryon exists to prevent.
        self._write_tryon_journal(reusable=2)
        self.manifest.write_text(
            self.manifest.read_text(encoding="utf-8").replace("provider: gemini",
                                                              "provider: qwen-max"),
            encoding="utf-8")
        self._write_failure(stock_out=True)
        with mock.patch("tgbot.bot.volume_datacenter", return_value="EU-RO-1"), \
             mock.patch("tgbot.bot.stock_at_cached", return_value=self._stock()):
            bot._deliver_provision_failure(
                self.tg, ME, self.manifest,
                read_provision_failure(provision_failure_path(self.manifest)))
        self.assertIn("0/2", self.tg.messages[-1])
```

`read_provision_failure` needs adding to the existing `from batchlib_ext.provision_failure import (...)` block at the top of the test file, and `effective_stage_params` to a new `from batchlib.pipelines import effective_stage_params` line beside the existing `from batchlib.manifest import ...`.

And in `TestProvisionFailureRecoveryButtons`:

```python
    def test_retry_resumes_without_touching_the_gpu_setting(self):
        with mock.patch("tgbot.bot.drain_running", return_value=False), \
             mock.patch("tgbot.bot.migration_running", return_value=False), \
             mock.patch("tgbot.bot.start_drain") as start_drain:
            bot.handle(self.tg, cb_from(ME, f"{bot._CB_RECOVER_RETRY}tg-1"),
                       allowed_user_id=ME)
        start_drain.assert_called_once_with(self.manifest, dry_run=False, resume=True)
        # Retry keeps the GPU the batch already failed on — switching is the
        # other button's job, and silently rewriting .env's GPU= would change
        # what every LATER batch rents too.
        self.assertEqual(env_get(self.root / ".env", "GPU"),
                         "NVIDIA GeForce RTX 5090")
        self.assertFalse(provision_failure_path(self.manifest).exists())

    def test_retry_refuses_a_stale_stem(self):
        with mock.patch("tgbot.bot.start_drain") as start_drain:
            bot.handle(self.tg, cb_from(ME, f"{bot._CB_RECOVER_RETRY}"),
                       allowed_user_id=ME)
        start_drain.assert_not_called()

    def test_wait_copy_no_longer_points_at_the_path_that_loses_the_tryon(self):
        # "/confirm again later" reaches _do_confirm, which did not resume:
        # new batch id, empty journal, every try-on re-run and Gemini billed
        # twice. The button may dismiss, it may not advise that.
        with mock.patch("tgbot.bot.start_drain"):
            bot.handle(self.tg, cb_from(ME, bot._CB_RECOVER_WAIT), allowed_user_id=ME)
        self.assertNotIn("/confirm again", self.tg.messages[-1])

    def test_stock_out_card_offers_retry(self):
        with mock.patch("tgbot.bot.volume_datacenter", return_value="EU-RO-1"), \
             mock.patch("tgbot.bot.stock_at_cached", return_value={}):
            bot.deliver_result(self.tg, ME, self.manifest)
        flat = [data for row in self.tg.buttons[-1] for _, data, *_ in row]
        self.assertIn(f"{bot._CB_RECOVER_RETRY}tg-1", flat)
```

- [ ] **Step 2: Run them, verify they fail**

Run: `python3 -m unittest scripts.tests.test_batch_bot.TestProvisionFailureRecoveryButtons -v`
Expected: FAIL — `AttributeError: module 'tgbot.bot' has no attribute '_CB_RECOVER_RETRY'`.

Run: `python3 -m unittest scripts.tests.test_batch_bot.TestProvisionFailureRecovery -v`
Expected: the two new count tests FAIL (`ImportError` on `read_provision_failure` if the import was missed, else no "2/2" in the message).

- [ ] **Step 3: Add the constant and the import**

In `scripts/tgbot/bot.py`, extend the import block (~line 28). `batchlib.runner` is already transitively imported via `drain`, so there is no cycle:

```python
from batchlib.runner import preserved_local_tryon
```

Next to the existing `_CB_RECOVER_*` constants (~line 1360):

```python
# Same-GPU retry. The other three recovery buttons all change something —
# GPU type, datacenter, or nothing at all (Đợi) — and before this existed a
# user whose card had scrolled away had no way to resume without /confirm,
# which minted a new batch id and re-ran every try-on.
_CB_RECOVER_RETRY = "rec:retry:"   # + "<manifest stem>"
```

- [ ] **Step 4: Render the count and the button**

In `_deliver_provision_failure` (~line 553), replace the `lines = [...]` block:

```python
    dc = failure.datacenter or "?"
    reusable, total = _preserved_tryon(manifest_path)
    lines = [f"{ICON_ERROR_CE} <b>Could not rent a pod</b> for {_esc(stem)}", "",
             f"No stock for <b>{_esc(failure.gpu)}</b> at {_esc(dc)} — the only "
             "datacenter your Network Volume can mount in.", ""]
    if total:
        lines.append(f"{ICON_OK_CE} <b>Try-on {reusable}/{total} finished and is "
                     "preserved.</b> Retrying will not call Gemini again for those.")
    else:
        lines.append("Your batch is safe — nothing already finished was lost, it's "
                     "just stuck waiting for a pod.")
    lines.append("")
```

and replace the button seed:

```python
    buttons = [[(f"Thử lại — giữ try-on đã chạy", f"{_CB_RECOVER_RETRY}{stem}",
                 _ce_id(ICON_ROCKET_CE))],
               [("Đợi", _CB_RECOVER_WAIT)]]
```

Add the helper just above `_deliver_provision_failure`:

```python
def _preserved_tryon(manifest_path: Path) -> tuple[int, int]:
    """(reusable, total) local try-ons for this manifest, or (0, 0) if unknown.

    Delegates to runner.preserved_local_tryon rather than counting here: the
    card and the runner must give the same answer, and the only way to
    guarantee that is one implementation. A manifest that will not load is
    (0, 0) — the card falls back to its generic "your batch is safe" line
    rather than claiming a number it could not check.
    """
    try:
        manifest = load_manifest(manifest_path)
    except (ManifestError, OSError):
        return 0, 0
    state = load_state(state_path_for(manifest_path))
    if not state.get("batch"):
        return 0, 0
    return preserved_local_tryon(manifest, state, ROOT / "out")
```

- [ ] **Step 5: Handle the callback, and fix `Đợi`'s copy**

In `_handle_callback`, next to the `_CB_RECOVER_SWITCH` branch (~line 1622):

```python
        elif data.startswith(_CB_RECOVER_RETRY):
            stem = data[len(_CB_RECOVER_RETRY):]
            if not stem:
                tg.send_message(chat_id, "that button is from an older "
                                         "version of the bot; check /status")
            else:
                _do_resume(tg, chat_id, ROOT / "batch" / f"{stem}.yaml",
                           dry_run=dry_run)
```

and replace the `_CB_RECOVER_WAIT` branch (~line 1617):

```python
        elif data == _CB_RECOVER_WAIT:
            tg.send_message(chat_id, "OK — parked. The try-on images are kept, "
                                     "nothing is lost. Tap <b>Thử lại</b> above "
                                     "when you want to rent again.",
                            parse_mode=PARSE_HTML)
```

`PARSE_HTML` is required here — the body carries `<b>`, and `FakeTg._check_markup` fails a message whose tags and `parse_mode` disagree.

- [ ] **Step 6: Update the existing button-set test**

`test_stock_out_offers_wait_switch_migrate_and_subscribe_buttons` (`test_batch_bot.py:4515`) asserts on the card's button list. Add the retry assertion to its `flat` checks:

```python
        self.assertIn(f"{bot._CB_RECOVER_RETRY}tg-1", flat)
```

Its sibling `test_switch_and_subscribe_buttons_carry_the_same_animated_icons_as_elsewhere` asserts `Đợi` is bare (2-tuple, no icon). That stays true — do not add an icon to `Đợi`. Add, next to it:

```python
        # Retry resumes a paid rental immediately, same as the switch button,
        # so it carries the same rocket.
        retry_row = next(r for r in rows if r[0][1].startswith(bot._CB_RECOVER_RETRY))
        self.assertEqual(retry_row[0][2], bot._ce_id(bot.ICON_ROCKET_CE))
```

- [ ] **Step 7: Run the bot tests**

Run: `python3 -m unittest scripts.tests.test_batch_bot.TestProvisionFailureRecovery scripts.tests.test_batch_bot.TestProvisionFailureRecoveryButtons -v`
Expected: OK.

Run: `make batch-test`
Expected: OK.

- [ ] **Step 8: Scrub and commit**

```bash
bash motions-studio/setup/scrub-secrets.sh --check
git add scripts/tgbot/bot.py scripts/tests/test_batch_bot.py
git commit -F - <<'EOF'
Telegram bot: a stock-out Retry button that actually resumes

The card offered four ways out and none of them was "try the same GPU
again later". Đợi dismissed and then advised "/confirm again later",
which reaches _do_confirm — no resume, so a new batch id, an empty
journal, and every try-on re-run with Gemini billed a second time for
images already on disk. The switch and migrate buttons did resume; the
one a user reaches for when they just want to wait did not.

The card now also names how many try-ons survive, counted by
runner.preserved_local_tryon so it cannot disagree with what the runner
will actually skip. Before this it said only "your batch is safe",
which is the claim that needed checking.
EOF
```

---

## Task 5: `_do_confirm` asks instead of guessing

**Files:**
- Modify: `scripts/tgbot/bot.py` (`_CB_*` constants ~line 1360; `_handle_callback` ~line 1538; `_do_confirm` ~line 4301)
- Test: `scripts/tests/test_batch_bot.py`

**Interfaces:**
- Consumes: `_preserved_tryon` (Task 4), `start_drain(..., force_local=)` (Task 2), `_run_token` (existing, `bot.py:1394`)
- Produces: `_do_confirm(tg, chat_id, *, dry_run: bool, phase_a_choice: str | None = None)`; `_CB_PHASE_A_REUSE`, `_CB_PHASE_A_RERUN`

- [ ] **Step 1: Write the failing tests**

Add a class to `scripts/tests/test_batch_bot.py`. It subclasses `TestFlow` so the real-file fixture, the faked `probe()` and `_fill_required_slots` come for free.

Extend the file's imports first:

```python
from batchlib.pipelines import PIPELINES, STAGES, effective_stage_params
from batchlib.runner import stage_dest
from tgbot.job import missing_slots, write_manifest
```

(`missing_slots` is already imported from `tgbot.job`; add `write_manifest` to that same line rather than importing twice.)

```python
class TestConfirmAsksBeforeReusingTryon(TestFlow):
    """/confirm must not silently pick between "reuse the try-on" and "roll it
    again". The two intents are indistinguishable from inside _do_confirm, and
    guessing wrong in the re-roll direction hands back the exact image the
    user was trying to get away from — silently, which is what makes it worse
    than one extra Gemini call.
    """

    def _journal_for_draft(self) -> Path:
        """Fill the draft, point it at a LOCAL provider, write the real manifest,
        then fake a journal saying its try-on is already done.

        Three things here are load-bearing and each one is a way this fixture
        could silently test nothing:

        - provider must be set explicitly. DEFAULT_PROVIDER is "qwen", the
          self-host GPU path, which render_manifest omits from the YAML and
          _local_tryon_stage therefore rejects — with the default there is no
          Phase A at all and the chooser never appears, so every test below
          would pass against a code path that never ran.
        - the stage name is looked up, not written as "tryon". The default
          pipeline may call it camera-tryon.
        - params_manifest comes from effective_stage_params, not a literal
          {"provider": "gemini"}: local_tryon_reusable compares against that
          function's output, which merges the stage defaults in.
        """
        self._fill_required_slots()
        bot._job_for(ME).provider = "gemini"
        bot._LAST_VALIDATE[ME] = True
        manifest = bot._job_manifest_path(ME)
        write_manifest(bot._jobs_for(ME), manifest, now="2026-09-16 09:00:00")
        run = load_manifest(manifest).runs[0]
        stage_name = next(s for s in PIPELINES[run.pipeline]
                          if STAGES[s].job_type == "tryon")
        run_dir = bot.ROOT / "out" / "2026-09-16-0900" / "runs" / run.id
        dest = stage_dest(run, run_dir, stage_name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"png")
        state_path_for(manifest).write_text(json.dumps({
            "batch": "2026-09-16-0900",
            "runs": {run.id: {"status": "running", "stages": {stage_name: {
                "status": "done", "phase": "local", "file": str(dest),
                "params_manifest": effective_stage_params(
                    stage_name, run.stage_params.get(stage_name))}}}}),
            encoding="utf-8")
        return manifest

    def _chooser_token(self) -> str:
        """The token off the rendered chooser, not _run_token(ME) read earlier.

        Reading it before /confirm is wrong and looks right: the first entry
        rewrites the manifest, _run_token IS that file's mtime_ns, so a
        pre-captured token no longer matches and every tap below would be
        refused as stale. Taking it off the buttons tests the real round trip.
        """
        flat = [data for row in self.tg.buttons[-1] for _, data, *_ in row]
        reuse = next(d for d in flat if d.startswith(bot._CB_PHASE_A_REUSE))
        return reuse[len(bot._CB_PHASE_A_REUSE):]

    def test_a_reusable_journal_offers_the_choice_and_spends_nothing_yet(self):
        self._journal_for_draft()
        with mock.patch("tgbot.bot.start_drain") as start_drain, \
             mock.patch("tgbot.bot.drain_running", return_value=False):
            bot.handle(self.tg, cmd_from(ME, "/confirm"), allowed_user_id=ME)
        start_drain.assert_not_called()
        flat = [data for row in self.tg.buttons[-1] for _, data, *_ in row]
        self.assertTrue(any(d.startswith(bot._CB_PHASE_A_REUSE) for d in flat))
        self.assertTrue(any(d.startswith(bot._CB_PHASE_A_RERUN) for d in flat))
        # The job must survive the chooser: _do_confirm clears _STATE on the
        # way out, and a cleared draft cannot be confirmed a second time.
        self.assertIsNotNone(bot._STATE.get(ME))
        # And the panel must not be frozen as "submitted" — nothing was.
        self.assertNotIn("submitted", panel_text(self.tg))

    def test_reuse_resumes_without_forcing(self):
        self._journal_for_draft()
        with mock.patch("tgbot.bot.start_drain") as start_drain, \
             mock.patch("tgbot.bot.drain_running", return_value=False):
            bot.handle(self.tg, cmd_from(ME, "/confirm"), allowed_user_id=ME)
            token = self._chooser_token()
            bot.handle(self.tg, cb_from(ME, bot._CB_PHASE_A_REUSE + token),
                       allowed_user_id=ME)
        start_drain.assert_called_once()
        self.assertIs(start_drain.call_args.kwargs["resume"], True)
        self.assertIs(start_drain.call_args.kwargs["force_local"], False)

    def test_rerun_resumes_and_forces(self):
        self._journal_for_draft()
        with mock.patch("tgbot.bot.start_drain") as start_drain, \
             mock.patch("tgbot.bot.drain_running", return_value=False):
            bot.handle(self.tg, cmd_from(ME, "/confirm"), allowed_user_id=ME)
            token = self._chooser_token()
            bot.handle(self.tg, cb_from(ME, bot._CB_PHASE_A_RERUN + token),
                       allowed_user_id=ME)
        start_drain.assert_called_once()
        self.assertIs(start_drain.call_args.kwargs["resume"], True)
        self.assertIs(start_drain.call_args.kwargs["force_local"], True)

    def test_the_choice_does_not_rewrite_the_manifest_and_invalidate_its_own_buttons(self):
        # _run_token IS the manifest's mtime_ns. A second write_manifest would
        # bump it, and the button the user just tapped would then fail its own
        # staleness check — "that button is from an older version of the bot".
        # Measured AFTER /confirm: the first entry writes legitimately, so a
        # pre-captured mtime would fail for the wrong reason.
        manifest = self._journal_for_draft()
        with mock.patch("tgbot.bot.start_drain") as start_drain, \
             mock.patch("tgbot.bot.drain_running", return_value=False):
            bot.handle(self.tg, cmd_from(ME, "/confirm"), allowed_user_id=ME)
            token = self._chooser_token()
            after_first = manifest.stat().st_mtime_ns
            bot.handle(self.tg, cb_from(ME, bot._CB_PHASE_A_REUSE + token),
                       allowed_user_id=ME)
        self.assertEqual(manifest.stat().st_mtime_ns, after_first)
        self.assertEqual(token, bot._run_token(ME))
        start_drain.assert_called_once()

    def test_a_stale_choice_button_is_refused(self):
        self._journal_for_draft()
        with mock.patch("tgbot.bot.start_drain") as start_drain, \
             mock.patch("tgbot.bot.drain_running", return_value=False):
            bot.handle(self.tg, cmd_from(ME, "/confirm"), allowed_user_id=ME)
            bot.handle(self.tg, cb_from(ME, bot._CB_PHASE_A_REUSE + "0"),
                       allowed_user_id=ME)
        start_drain.assert_not_called()

    def test_no_journal_means_no_chooser_and_one_tap_still_starts(self):
        with mock.patch("tgbot.bot.start_drain") as start_drain, \
             mock.patch("tgbot.bot.drain_running", return_value=False):
            self._fill_required_slots()
            bot.handle(self.tg, cmd_from(ME, "/confirm"), allowed_user_id=ME)
        start_drain.assert_called_once()
        self.assertIs(start_drain.call_args.kwargs.get("resume"), False)
        self.assertIs(start_drain.call_args.kwargs.get("force_local"), False)

    def test_a_default_provider_draft_has_no_phase_a_and_gets_no_chooser(self):
        # The mirror of _journal_for_draft's first load-bearing detail: "qwen"
        # is the self-host path, so there is nothing to reuse and no choice to
        # offer. If this ever starts showing a chooser, _local_tryon_stage grew
        # an opinion of its own.
        self._fill_required_slots()
        bot._LAST_VALIDATE[ME] = True
        with mock.patch("tgbot.bot.start_drain") as start_drain, \
             mock.patch("tgbot.bot.drain_running", return_value=False):
            bot.handle(self.tg, cmd_from(ME, "/confirm"), allowed_user_id=ME)
        flat = [data for row in (self.tg.buttons[-1] or []) for _, data, *_ in row]
        self.assertFalse(any(d.startswith(bot._CB_PHASE_A_REUSE) for d in flat))
        start_drain.assert_called_once()

    def test_a_job_queued_behind_a_live_drain_gets_no_chooser(self):
        # The mailbox branch never reaches the money gate, so it must not reach
        # the chooser either: that job runs later on a pod already paid for.
        self._journal_for_draft()
        with mock.patch("tgbot.bot.start_drain") as start_drain, \
             mock.patch("tgbot.bot.drain_running", return_value=True):
            bot.handle(self.tg, cmd_from(ME, "/confirm"), allowed_user_id=ME)
        flat = [data for row in (self.tg.buttons[-1] or []) for _, data, *_ in row]
        self.assertFalse(any(d.startswith(bot._CB_PHASE_A_REUSE) for d in flat))
        start_drain.assert_not_called()
```

`panel_text` is a module-level helper already in that test file.

- [ ] **Step 2: Run them, verify they fail**

Run: `python3 -m unittest scripts.tests.test_batch_bot.TestConfirmAsksBeforeReusingTryon -v`
Expected: FAIL — `AttributeError: module 'tgbot.bot' has no attribute '_CB_PHASE_A_REUSE'`. Two tests should already PASS: `test_no_journal_means_no_chooser_and_one_tap_still_starts` and `test_a_default_provider_draft_has_no_phase_a_and_gets_no_chooser`. If either of those fails, the fixture is wrong, not the implementation — debug it before writing any production code, because both are the control group that proves the chooser is conditional rather than unconditional.

- [ ] **Step 3: Add the constants**

Next to `_CB_RECOVER_RETRY`:

```python
# The reuse-or-rerun chooser _do_confirm sends when the journal already holds
# a matching try-on. Both carry _run_token for the same reason the spend
# button does: Telegram keyboards stay tappable forever, and a chooser minted
# for one manifest must not be answerable after it was rewritten.
_CB_PHASE_A_REUSE = "pa:reuse:"   # + _run_token
_CB_PHASE_A_RERUN = "pa:rerun:"   # + _run_token
```

- [ ] **Step 4: Change `_do_confirm`'s signature and gate the rewrite**

```python
def _do_confirm(tg: Tg, chat_id: int, *, dry_run: bool,
                phase_a_choice: str | None = None) -> None:
```

Wrap the existing `write_manifest` call (~line 4402):

```python
    if phase_a_choice is None:
        # Skipped on the second entry, and not as an optimisation: _run_token
        # IS this file's mtime_ns, so rewriting it would invalidate the
        # chooser button the user just tapped. The bytes on disk are already
        # the ones the chooser was minted from.
        write_manifest(queued, manifest_path, now=time.strftime("%Y-%m-%d %H:%M:%S"))
```

- [ ] **Step 5: Insert the chooser**

After the `stages` list is built and **before** `_freeze_panel` (~line 4411). Placement matters twice over: freezing the panel would mark it "submitted" when nothing was, and the `_STATE` clear at the end of the function would leave the second entry with no job to confirm.

```python
    if not running and phase_a_choice is None:
        reusable, total = _preserved_tryon(manifest_path)
        if reusable:
            token = _run_token(chat_id)
            tg.send_message(
                chat_id,
                f"{ICON_ASK_CE} <b>Try-on already ran</b> for these exact inputs "
                f"({reusable}/{total} run(s)).\n"
                "Reusing it costs no Gemini quota. Re-running replaces those "
                "images and pays for them again.",
                parse_mode=PARSE_HTML,
                buttons=[[("Reuse — no Gemini spend", _CB_PHASE_A_REUSE + token,
                           _ce_id(ICON_OK_CE)),
                          ("Re-run try-on", _CB_PHASE_A_RERUN + token,
                           _ce_id(ICON_ROCKET_CE))]])
            return
```

- [ ] **Step 6: Pass the choice through to `start_drain`**

Replace the call at ~line 4420:

```python
    if not running:
        # THE money gate (see docstring) — the only line that may rent a pod.
        # Queuing (the `running` branch below) never reaches this: the
        # mailbox file alone is drain.py's signal, claimed by the process
        # already running, on the pod already paid for.
        #
        # resume is True only when the chooser ran: phase_a_choice is set
        # exactly when a journal with reusable try-on exists, and resume is
        # what makes that try-on skipped rather than paid for twice. A
        # stale button answering after the journal vanished lands in
        # resolve_batch_id's own "RESUME=1 but nothing to continue" branch,
        # which reports it and runs as a new batch — safe, not silent.
        start_drain(manifest_path, dry_run=dry_run,
                    resume=phase_a_choice is not None,
                    force_local=phase_a_choice == "rerun")
```

- [ ] **Step 7: Handle the two callbacks**

In `_handle_callback`, immediately after the `_CB_RUN_GO` branch (~line 1538), mirroring its token check:

```python
        elif data.startswith(_CB_PHASE_A_REUSE) or data.startswith(_CB_PHASE_A_RERUN):
            reuse = data.startswith(_CB_PHASE_A_REUSE)
            prefix = _CB_PHASE_A_REUSE if reuse else _CB_PHASE_A_RERUN
            if data[len(prefix):] != _run_token(chat_id):
                tg.send_message(chat_id,
                                "the job changed since that button was sent, so "
                                "nothing ran. Check the manifest above and "
                                "confirm again.")
            else:
                _do_confirm(tg, chat_id, dry_run=dry_run,
                            phase_a_choice="reuse" if reuse else "rerun")
```

The two prefixes share no common start, so `startswith` dispatch order against the existing `_CB_*` keys is safe; verify by checking neither `pa:reuse:` nor `pa:rerun:` is a prefix of any other constant:

Run: `grep -n '^_CB_[A-Z_]* = ' scripts/tgbot/bot.py | grep -E '"pa:'`
Expected: exactly the two new lines.

- [ ] **Step 8: Update the pinned test**

`test_confirm_calls_start_drain_once_with_dry_run_false` (`test_batch_bot.py:3349`) still holds — with no journal there is no chooser — but add the two new kwargs to its assertions so the default is pinned rather than assumed:

```python
        start_drain.assert_called_once()
        _, kwargs = start_drain.call_args
        self.assertEqual(kwargs.get("dry_run"), False)
        # No journal for this chat, so no chooser and no resume: a fresh
        # /confirm on a fresh job must still start a fresh batch.
        self.assertIs(kwargs.get("resume"), False)
        self.assertIs(kwargs.get("force_local"), False)
```

- [ ] **Step 9: Run the tests**

Run: `python3 -m unittest scripts.tests.test_batch_bot.TestConfirmAsksBeforeReusingTryon -v`
Expected: 8 tests, OK.

Run: `make batch-test`
Expected: OK.

- [ ] **Step 10: Verify the money invariants still hold**

Run: `grep -rn 'start_drain(' scripts/tgbot/bot.py | grep -v '^\s*#' | grep -v mock`
Expected: exactly two call sites — `_do_confirm` and `_do_resume`.

Run: `grep -rn 'CONFIRM=yes' scripts/tgbot/ --include=*.py`
Expected: `run.py` only, one `argv.append`.

- [ ] **Step 11: Scrub and commit**

```bash
bash motions-studio/setup/scrub-secrets.sh --check
git add scripts/tgbot/bot.py scripts/tests/test_batch_bot.py
git commit -F - <<'EOF'
Telegram bot: ask before reusing a try-on the journal says is done

/confirm on a manifest whose try-on already ran had two defensible
answers and no way to tell them apart. Auto-resuming is right for a
stock-out retry and wrong for a re-roll — and wrong silently, handing
back the exact image the user was trying to get away from. /tryon
exists because that re-roll is a real workflow, so the chooser appears
only when a reusable journal exists and costs one tap the rest of the
time.

Both answers pass resume=True: the batch id and any pod stage already
done survive either way. What differs is force_local, which bypasses
the reuse check without orphaning the batch.

The second entry skips write_manifest, and not as an optimisation —
_run_token IS that file's mtime_ns, so rewriting it would invalidate
the button the user just tapped. The early return sits before
_freeze_panel and before the _STATE clear for the same reason: a
frozen panel claims "submitted" for something that was not, and a
cleared draft cannot be confirmed twice.
EOF
```

**Commit A is complete here.** Verify before starting B:

Run: `make batch-test && make check-job-types && make check-batch-params`
Expected: all green. B builds on A's `start_drain(force_local=)` and `_preserved_tryon`, so do not start it on a red suite.

---

# Commit B — Phase A before the spend panel

## Task 6: `drain.py --phase-a-only`

**Files:**
- Modify: `scripts/drain.py` (argparse ~line 297, `main()` body ~lines 305-340)
- Modify: `Makefile` (`drain:` target)
- Test: `scripts/tests/test_batch_drain.py`

**Interfaces:**
- Consumes: `batch_run` (existing, `drain.py:161`), `EXIT_NEEDS_POD`
- Produces: `drain.py --phase-a-only`; `make drain FILE=… PHASE_A=1`. Task 7 calls the make form.

- [ ] **Step 1: Write the failing tests**

Add to `scripts/tests/test_batch_drain.py`:

```python
class TestPhaseAOnly(unittest.TestCase):
    """--phase-a-only runs the local try-on and stops. It may never reach
    provision(), whatever Phase A returns.

    Also independent of --yes, and that ordering is the trap: main()'s
    existing `if not args.yes` gate prints DRY RUN and returns 0 without
    running anything. Phase A is not a dry run — it spends Gemini quota and
    writes the journal — so a --phase-a-only invocation placed after that
    gate would be a money-adjacent flag that silently does nothing.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.manifest = self.tmp / "tg-1.yaml"
        self.manifest.write_text(
            "runs:\n  - id: a\n    pipeline: tryon-motion-enhance\n"
            "    inputs: {character: /tmp/c.png, outfit: /tmp/o.png, driver: /tmp/d.mp4}\n"
            "    tryon: { provider: gemini }\n", encoding="utf-8")

    def _main(self, *extra: str):
        with mock.patch.object(sys, "argv",
                               ["drain.py", "--file", str(self.manifest), *extra]):
            return drain.main()

    def test_never_provisions_when_phase_a_says_a_pod_is_needed(self):
        with mock.patch.object(drain, "batch_run", return_value=drain.EXIT_NEEDS_POD), \
             mock.patch.object(drain, "provision",
                               side_effect=AssertionError("rented a pod")) as prov:
            rc = self._main("--phase-a-only", "--yes")
        self.assertEqual(rc, drain.EXIT_NEEDS_POD)
        prov.assert_not_called()

    def test_runs_without_yes_rather_than_printing_dry_run(self):
        calls: list[tuple] = []
        with mock.patch.object(drain, "batch_run",
                               side_effect=lambda *a: calls.append(a) or 0):
            rc = self._main("--phase-a-only")
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 1)
        self.assertIn("--no-start", calls[0])

    def test_propagates_a_phase_a_failure_code(self):
        with mock.patch.object(drain, "batch_run", return_value=1), \
             mock.patch.object(drain, "provision",
                               side_effect=AssertionError("rented a pod")):
            self.assertEqual(self._main("--phase-a-only"), 1)

    def test_forwards_resume_and_force_local(self):
        calls: list[tuple] = []
        with mock.patch.object(drain, "batch_run",
                               side_effect=lambda *a: calls.append(a) or 0):
            self._main("--phase-a-only", "--resume", "--force-local")
        self.assertIn("--resume", calls[0])
        self.assertIn("--force-local", calls[0])

    def test_no_yes_and_no_phase_a_is_still_a_dry_run(self):
        # The existing gate must survive unchanged for the renting path.
        with mock.patch.object(drain, "batch_run",
                               side_effect=AssertionError("ran a batch")) as br:
            rc = self._main()
        self.assertEqual(rc, 0)
        br.assert_not_called()
```

- [ ] **Step 2: Run them, verify they fail**

Run: `python3 -m unittest scripts.tests.test_batch_drain.TestPhaseAOnly -v`
Expected: FAIL — `argparse` exits 2 (`SystemExit`) on the unknown `--phase-a-only`.

- [ ] **Step 3: Implement**

In `scripts/drain.py`'s `main()`, add the argument:

```python
    ap.add_argument("--phase-a-only", action="store_true",
                    help="run the local try-on phase and exit; never rent a pod")
```

Then restructure the body. The `phase_a` argv must be built **before** the `--yes` gate, and the `--phase-a-only` branch must sit **before** it too. Replace the region from `if not args.yes:` through `rc = batch_run(*phase_a)` with:

```python
    # Built before the --yes gate because --phase-a-only needs it and is
    # deliberately NOT gated on --yes: Phase A spends Gemini quota and writes
    # the journal, so it is not a dry run. Only renting is.
    #
    # --no-start so it cannot quietly resume a stopped pod behind our back:
    # renting is this script's job, and it must be the only one doing it.
    # Local Gemini try-on happens here, before any GPU clock starts, so a 429
    # costs nothing. See docs/batch-runner.md section 2.9.
    #
    # --resume must be forwarded to phase A, not only to the run after
    # provisioning. Without it, resolve_batch_id (batch_run.py:44) mints a NEW
    # batch id on a re-drain, so every local try-on runs again and Gemini is
    # billed a second time — silently destroying the "defer preserves the
    # try-on you already paid for" guarantee in the design spec.
    phase_a = ["--file", str(manifest_path), "--no-start"]
    if args.resume:
        phase_a.append("--resume")
    if args.force_local:
        phase_a.append("--force-local")

    if args.phase_a_only:
        # The bot's pre-spend step: run the try-on, hand back the exit code,
        # and let a human decide about the GPU afterwards. Returning
        # EXIT_NEEDS_POD unchanged is what tells the caller a pod is still
        # wanted; collapsing it to 0 here would make "done, no pod needed" and
        # "stopped, needs a pod" indistinguishable.
        return batch_run(*phase_a)

    if not args.yes:
        print(f"DRY RUN. {len(manifest.runs)} runs, tier-2 ceiling {ceiling} min.")
        print("Re-run with --yes to rent a pod.")
        return 0

    rc = batch_run(*phase_a)
```

Everything after (`if rc == 0:` … `provision(...)` …) is unchanged.

- [ ] **Step 4: Run the tests**

Run: `python3 -m unittest scripts.tests.test_batch_drain.TestPhaseAOnly -v`
Expected: 5 tests, OK.

Run: `python3 -m unittest scripts.tests.test_batch_drain -v`
Expected: OK — `TestProvision`, `TestPodMaxHours` and the `chain_or_teardown` tests must be unaffected by the reordering.

- [ ] **Step 5: Wire the Makefile**

Replace the `drain:` target. **Recipe lines begin with a TAB.**

```make
drain: ## Rent a pod, run FILE, destroy it (dry run unless CONFIRM=yes; PHASE_A=1 stops before renting)
	@test -n "$(FILE)" || { echo "usage: make drain FILE=batch/….yaml [CONFIRM=yes] [RESUME=1] [FORCE_LOCAL=1] [PHASE_A=1]"; exit 1; }
	@python3 scripts/drain.py --file "$(FILE)" \
		$(if $(filter yes,$(CONFIRM)),--yes) $(if $(RESUME),--resume) \
		$(if $(FORCE_LOCAL),--force-local) $(if $(PHASE_A),--phase-a-only)
```

Verify by expansion, without running anything:

Run: `make -n drain FILE=batch/example.yaml PHASE_A=1`
Expected: a `python3 scripts/drain.py --file batch/example.yaml --phase-a-only` line, and **no** `--yes`.

Run: `make -n drain FILE=batch/example.yaml PHASE_A=1 CONFIRM=yes`
Expected: both `--phase-a-only` and `--yes`. (Task 7 never passes CONFIRM with PHASE_A; this only proves the target composes.)

- [ ] **Step 6: Full suite, scrub, commit**

```bash
make batch-test
bash motions-studio/setup/scrub-secrets.sh --check
git add scripts/drain.py Makefile scripts/tests/test_batch_drain.py
git commit -F - <<'EOF'
drain: add --phase-a-only, the pre-spend local try-on step

Runs Phase A and returns its exit code without ever reaching provision().
The bot needs this to put the GPU decision after the try-on results
exist, so the stock a user decides on is measured at the moment of the
decision instead of minutes earlier — the window in which a 5090 at
EU-RO-1 can disappear and take the batch down with it.

Deliberately not gated on --yes. main()'s existing dry-run gate returns
0 without running anything, and Phase A is not a dry run: it spends
Gemini quota and writes the journal. A money-adjacent flag placed after
that gate would silently do nothing.

EXIT_NEEDS_POD is returned unchanged rather than collapsed to 0, because
"finished, no pod needed" and "stopped, still needs a pod" are the two
outcomes the caller branches on.
EOF
```

---

## Task 7: `start_phase_a`, and a `busy()` that is not `drain_running()`

**Files:**
- Modify: `scripts/tgbot/run.py` (`_RUNNING` ~line 63, `drain_running` ~line 293, new functions after it)
- Modify: `scripts/tests/test_batch_bot.py` (`reset_bot_state`'s name tuple ~line 146)
- Test: `scripts/tests/test_batch_tgrun.py`, `scripts/tests/test_batch_bot.py`

**Interfaces:**
- Consumes: `make drain … PHASE_A=1` (Task 6)
- Produces:
  - `_PHASE_A: dict[Path, subprocess.Popen]` and `_PHASE_A_RC: dict[Path, int]`
  - `start_phase_a(manifest_path: Path, *, resume: bool = False, force_local: bool = False) -> subprocess.Popen`
  - `phase_a_running(manifest_path: Path) -> bool`
  - `busy(manifest_path: Path) -> bool`
  - `phase_a_exit(manifest_path: Path) -> int | None`

Tasks 9 and 10 depend on all five function names exactly.

- [ ] **Step 1: Write the failing tests**

Add to `scripts/tests/test_batch_tgrun.py`. It needs `import ast` added to the stdlib import block at the top (the file already imports `json`, `subprocess`, `sys`, `tempfile`, `time`, `unittest`, `Path`, `mock`). The AST-based invariant test below was verified against the repo as it stands on 2026-09-16 and returns exactly one hit, `run.py:261: 'CONFIRM=yes'`.

```python
class TestStartPhaseA(unittest.TestCase):
    """Phase A is a second subprocess launcher sitting next to the one that
    holds the money gate, so its argv is asserted with the same care.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.manifest = self.tmp / "tg-1.yaml"
        self.manifest.write_text("runs: []\n", encoding="utf-8")
        self._orig_running = dict(run_mod._RUNNING)
        self._orig_phase_a = dict(run_mod._PHASE_A)
        self._orig_lease_path = run_mod.LEASE_PATH
        run_mod._RUNNING.clear()
        run_mod._PHASE_A.clear()
        run_mod.LEASE_PATH = Path(tempfile.mkdtemp()) / "no-lease.json"

    def tearDown(self):
        run_mod._RUNNING.clear()
        run_mod._RUNNING.update(self._orig_running)
        run_mod._PHASE_A.clear()
        run_mod._PHASE_A.update(self._orig_phase_a)
        run_mod.LEASE_PATH = self._orig_lease_path

    def _argv(self, **kwargs) -> list[str]:
        with mock.patch.object(run_mod.subprocess, "Popen") as popen:
            popen.return_value = _FakeProc(poll_return=None)
            run_mod.start_phase_a(self.manifest, **kwargs)
        return popen.call_args.args[0]

    def test_never_appends_confirm_yes(self):
        # THE invariant. grep -rn CONFIRM scripts/tgbot/ must still show one
        # executable hit, inside start_drain's dry_run gate, and this function
        # must not be a second one.
        for kwargs in ({}, {"resume": True}, {"force_local": True},
                       {"resume": True, "force_local": True}):
            self.assertNotIn("CONFIRM=yes", self._argv(**kwargs))

    def test_confirm_yes_still_appears_in_exactly_one_executable_line(self):
        # The argv assertion above only covers start_phase_a's own call. This
        # is the repo-wide grep run.py:246 has always asked a human to do, made
        # a test: a third launcher added later that appends CONFIRM=yes
        # somewhere else fails here instead of silently becoming a second way
        # to rent a pod.
        #
        # AST, not text search. run.py mentions the string in FOUR docstring
        # bodies (lines 5, 246, 304, 310 as of 2026-09-16), and one of those
        # even contains it inside literal quote characters — so neither
        # "skip lines starting with a comment or a triple quote" nor "search
        # for the quoted form" gives one hit. Walking the tree and excluding
        # docstring nodes does, and comments are not nodes at all.
        root = Path(run_mod.__file__).resolve().parent
        hits = []
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            docstrings = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                     ast.AsyncFunctionDef)) \
                        and node.body and isinstance(node.body[0], ast.Expr) \
                        and isinstance(node.body[0].value, ast.Constant) \
                        and isinstance(node.body[0].value.value, str):
                    docstrings.add(id(node.body[0].value))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                        and "CONFIRM=yes" in node.value and id(node) not in docstrings:
                    hits.append(f"{path.name}:{node.lineno}: {node.value!r}")
        self.assertEqual(
            len(hits), 1,
            "CONFIRM=yes must appear in exactly one executable string literal "
            f"in scripts/tgbot/ — start_drain's dry_run gate. Found: {hits}")
        self.assertIn("run.py", hits[0])

    def test_sets_the_phase_a_variable(self):
        self.assertIn("PHASE_A=1", self._argv())

    def test_forwards_resume_and_force_local(self):
        argv = self._argv(resume=True, force_local=True)
        self.assertIn("RESUME=1", argv)
        self.assertIn("FORCE_LOCAL=1", argv)

    def test_a_live_phase_a_does_not_make_drain_running_true(self):
        # drain_running True routes a job into the mailbox so chain_or_teardown
        # picks it up on a pod already paid for. Phase A has no pod, so
        # reusing _RUNNING would queue a job into its own mailbox.
        run_mod._PHASE_A[self.manifest.resolve()] = _FakeProc(poll_return=None)
        self.assertFalse(run_mod.drain_running(self.manifest))
        self.assertTrue(run_mod.phase_a_running(self.manifest))
        self.assertTrue(run_mod.busy(self.manifest))

    def test_busy_is_true_for_a_drain_too(self):
        run_mod._RUNNING[self.manifest.resolve()] = _FakeProc(poll_return=None)
        self.assertTrue(run_mod.busy(self.manifest))
        self.assertFalse(run_mod.phase_a_running(self.manifest))

    def test_exit_code_is_none_while_running_and_read_once_finished(self):
        self.assertIsNone(run_mod.phase_a_exit(self.manifest))
        run_mod._PHASE_A[self.manifest.resolve()] = _FakeProc(poll_return=3)
        self.assertEqual(run_mod.phase_a_exit(self.manifest), 3)

    def test_a_finished_phase_a_is_no_longer_busy(self):
        run_mod._PHASE_A[self.manifest.resolve()] = _FakeProc(poll_return=0)
        self.assertFalse(run_mod.phase_a_running(self.manifest))
        self.assertFalse(run_mod.busy(self.manifest))
        # The exit code survives the process being reaped, so the tick that
        # collects it cannot race itself between two polls.
        self.assertEqual(run_mod.phase_a_exit(self.manifest), 0)
```

And in `scripts/tests/test_batch_bot.py`, add `_PHASE_A` awareness to `reset_bot_state`'s expectation. That helper iterates a hardcoded tuple of `bot.py` dict names and uses `getattr`, so a name that disappears fails loudly. `_PHASE_A` lives in `tgbot.run`, not `tgbot.bot`, so add a separate assertion that the bot module re-exports nothing stale — instead, extend the tuple only if Task 10 adds a bot-side dict. For now add a test that the bot's guards see `busy`:

```python
class TestPhaseABlocksManifestMutation(unittest.TestCase):
    """A live Phase A reads the same manifest file a drain does, so the guards
    that exist because drain.py reads that file have to cover it too."""

    def setUp(self):
        self._orig_root = bot.ROOT
        self.root = Path(tempfile.mkdtemp())
        (self.root / "batch").mkdir()
        bot.ROOT = self.root
        reset_bot_state()
        self.manifest = self.root / "batch" / "tg-1.yaml"
        self.manifest.write_text("runs: []\n", encoding="utf-8")
        self.tg = FakeTg()

    def tearDown(self):
        bot.ROOT = self._orig_root

    def test_clear_is_refused_while_phase_a_runs(self):
        with mock.patch("tgbot.bot.busy", return_value=True):
            bot.handle(self.tg, cmd_from(ME, "/clear"), allowed_user_id=ME)
        self.assertIn("drain is running", self.tg.messages[-1])
```

- [ ] **Step 2: Run them, verify they fail**

Run: `python3 -m unittest scripts.tests.test_batch_tgrun.TestStartPhaseA -v`
Expected: FAIL — `AttributeError: module 'tgbot.run' has no attribute '_PHASE_A'`.

- [ ] **Step 3: Implement in `run.py`**

Next to `_RUNNING` (~line 63):

```python
# Popen handles for Phase A runs (local try-on, no pod). Kept SEPARATE from
# _RUNNING: drain_running() being True makes bot._do_confirm route the job
# into the mailbox so chain_or_teardown picks it up on a pod already paid for,
# and Phase A has no pod — sharing the dict would queue a job into its own
# mailbox. busy() is the union, for the guards that exist because drain.py
# READS the manifest file rather than because a pod is billed.
_PHASE_A: dict[Path, subprocess.Popen] = {}
# Exit codes collected from finished Phase A processes, keyed the same way.
# Kept after the handle is reaped so the tick that acts on the code cannot
# race itself between two polls, and so a code is never lost by being read
# twice.
_PHASE_A_RC: dict[Path, int] = {}
```

After `start_drain`:

```python
def start_phase_a(manifest_path: Path, *, resume: bool = False,
                  force_local: bool = False) -> subprocess.Popen:
    """Launch the local try-on phase only. NEVER appends CONFIRM=yes.

    That is the whole point of this function existing beside start_drain
    rather than inside it: `grep -rn CONFIRM scripts/tgbot/` must keep showing
    exactly one executable hit, in start_drain's dry_run gate, and this is a
    second subprocess launcher that must not become a second one. Phase A
    spends Gemini quota and writes the journal; it cannot rent anything,
    because drain.py's --phase-a-only returns before provision().

    Same log-file-not-a-pipe shape as start_drain, for the same reason: a
    Popen pipe nobody reads fills its OS buffer and deadlocks the child.
    Phase A is minutes rather than hours, but a 12-run batch of Gemini calls
    is enough output to matter.
    """
    argv = ["make", "drain", f"FILE={manifest_path}", "PHASE_A=1"]
    if resume:
        argv.append("RESUME=1")
    if force_local:
        argv.append("FORCE_LOCAL=1")

    log_path = manifest_path.with_suffix(".phase-a.log")
    with open(log_path, "ab") as log_file:
        proc = subprocess.Popen(argv, cwd=ROOT, stdout=log_file, stderr=subprocess.STDOUT)
    key = manifest_path.resolve()
    _PHASE_A[key] = proc
    _PHASE_A_RC.pop(key, None)   # a fresh run invalidates the last one's code
    return proc
```

After `drain_running`:

```python
def phase_a_running(manifest_path: Path) -> bool:
    """True while THIS process has a Phase A child alive.

    Popen-only, no lease fallback the way drain_running has: a lease is
    written at provision time and Phase A never provisions, so there is
    nothing on disk to recover from. The consequence is deliberate and
    acceptable — a restarted bot loses track of an in-flight Phase A. It
    rents nothing, so the worst case is that the user taps Run again, and
    with resume=True the finished try-ons are skipped rather than re-billed.
    """
    proc = _PHASE_A.get(manifest_path.resolve())
    return proc is not None and proc.poll() is None


def phase_a_exit(manifest_path: Path) -> int | None:
    """The finished Phase A's exit code, or None if it is still running.

    Recorded once and kept, so the tick that acts on it is idempotent: a
    second poll after the handle is reaped still answers, and answering None
    there would strand the chat with a progress message and no panel.
    """
    key = manifest_path.resolve()
    proc = _PHASE_A.get(key)
    if proc is None:
        return _PHASE_A_RC.get(key)
    rc = proc.poll()
    if rc is None:
        return None
    _PHASE_A_RC[key] = rc
    return rc


def busy(manifest_path: Path) -> bool:
    """Anything holding this manifest — a billed drain OR an unpaid Phase A.

    Use this for the guards that exist because drain.py READS the manifest
    file (/clear, /wipe, _render_and_validate's write guard): overwriting a
    file a running child is about to re-read corrupts its input. Keep using
    drain_running() for the guards that exist because a POD IS BILLED —
    conflating them would let an unpaid try-on phase block a kill.
    """
    return drain_running(manifest_path) or phase_a_running(manifest_path)
```

- [ ] **Step 4: Run the `run.py` tests**

Run: `python3 -m unittest scripts.tests.test_batch_tgrun -v`
Expected: OK, including the new class.

- [ ] **Step 5: Import `busy` into `bot.py` and switch the two file-safety guards**

In `scripts/tgbot/bot.py`, find the existing `from tgbot.run import ...` line and add `busy`. Then change the two guards that protect the manifest *file*:

`/clear` (~line 3120):
```python
    if busy(_job_manifest_path(chat_id)):
```
`/wipe` (~line 3194):
```python
    if busy(_job_manifest_path(chat_id)):
```

Leave `_render_and_validate`'s write guard (~line 944) on `drain_running` **for now** — Task 10 changes it together with the Run button, because a Phase A in flight must not be able to queue into a mailbox it has no pod for, and that logic changes shape in the same edit.

Update the two user-facing strings only if they say "drain": read each message before changing it. `/clear`'s says "a drain is running for this job — clearing …", which stays accurate enough for a Phase A (the test above asserts that substring); do not reword it in this task.

- [ ] **Step 6: Run the bot tests**

Run: `python3 -m unittest scripts.tests.test_batch_bot.TestPhaseABlocksManifestMutation -v`
Expected: OK.

Run: `make batch-test`
Expected: OK.

- [ ] **Step 7: Verify the invariant**

Run: `grep -rn 'CONFIRM=yes' scripts/tgbot/ --include=*.py`
Expected: `run.py` only; `start_phase_a` contributes no hit.

- [ ] **Step 8: Scrub and commit**

```bash
bash motions-studio/setup/scrub-secrets.sh --check
git add scripts/tgbot/run.py scripts/tgbot/bot.py \
        scripts/tests/test_batch_tgrun.py scripts/tests/test_batch_bot.py
git commit -F - <<'EOF'
Telegram bot: a Phase A launcher that cannot rent anything

start_phase_a sits beside start_drain and never appends CONFIRM=yes —
grep -rn CONFIRM scripts/tgbot/ still shows one executable hit. It runs
make drain PHASE_A=1, which returns before provision().

Its handles live in their own dict, not _RUNNING. drain_running() being
True is what makes _do_confirm route a job into the mailbox for
chain_or_teardown to pick up on a pod already paid for; Phase A has no
pod, so sharing the dict would queue a job into its own mailbox. busy()
is the union and goes on the guards that exist because drain.py READS
the manifest file. The guards that exist because a POD IS BILLED stay on
drain_running — conflating them would let an unpaid try-on phase block
a kill.

phase_a_running has no lease fallback the way drain_running does, since
a lease is written at provision time. So a restarted bot loses track of
an in-flight Phase A. That is acceptable and deliberate: it rents
nothing, so the worst case is a second Run tap, and resume=True skips
the try-ons already finished rather than re-billing them.
EOF
```

---

## Task 8: `_offer_run_confirm` takes its spend destination as a parameter

A pure refactor. No behaviour change, every existing test stays green.

**Files:**
- Modify: `scripts/tgbot/bot.py` (`_offer_run_confirm` only, ~line 3866)
- Test: `scripts/tests/test_batch_bot.py`

Its four call sites (`bot.py:1465`, `:1479`, `:1496`, `:1512`) are **not** touched here — both new parameters default to today's behaviour, which is what makes this a refactor. Task 10 rewires them.

**Interfaces:**
- Produces: `_offer_run_confirm(tg, chat_id, *, message_id=None, force=False, spend_cb=None, heading=None)`. Task 9 passes both new arguments.

- [ ] **Step 1: Write the failing test**

```python
class TestRunConfirmPanelIsParameterised(unittest.TestCase):
    """Task 9 renders this same panel a second time, after Phase A, with a
    different spend destination. Pinning the parameterisation here keeps the
    refactor honest: the default must still be _CB_RUN_GO, or every existing
    Run button in the chat changes meaning.
    """

    def setUp(self):
        self._orig_root = bot.ROOT
        self.root = Path(tempfile.mkdtemp())
        (self.root / "batch").mkdir()
        bot.ROOT = self.root
        (self.root / ".env").write_text(
            "GPU=NVIDIA GeForce RTX 5090\nPOD_VOLUME_ID=vol-1\n", encoding="utf-8")
        reset_bot_state()
        self.manifest = self.root / "batch" / "tg-1.yaml"
        self.manifest.write_text("runs: []\n", encoding="utf-8")
        self.tg = FakeTg()

    def tearDown(self):
        bot.ROOT = self._orig_root

    def _stock(self):
        return {"NVIDIA GeForce RTX 5090": [
            Stock(gpu_id="NVIDIA GeForce RTX 5090", display_name="RTX 5090",
                  datacenter_id="EU-RO-1", stock_status="available", price_per_hr=0.99)]}

    def test_default_spend_callback_is_unchanged(self):
        with mock.patch("tgbot.bot.volume_datacenter", return_value="EU-RO-1"), \
             mock.patch("tgbot.bot.stock_at_cached", return_value=self._stock()):
            bot._offer_run_confirm(self.tg, ME)
        flat = [data for row in self.tg.buttons[-1] for _, data, *_ in row]
        self.assertTrue(any(d.startswith(bot._CB_RUN_GO) for d in flat))

    def test_a_caller_supplied_spend_callback_replaces_it(self):
        with mock.patch("tgbot.bot.volume_datacenter", return_value="EU-RO-1"), \
             mock.patch("tgbot.bot.stock_at_cached", return_value=self._stock()):
            bot._offer_run_confirm(self.tg, ME, spend_cb="rec:retry:tg-1")
        flat = [data for row in self.tg.buttons[-1] for _, data, *_ in row]
        self.assertIn("rec:retry:tg-1", flat)
        self.assertFalse(any(d.startswith(bot._CB_RUN_GO) for d in flat))

    def test_the_no_stock_data_branch_honours_it_too(self):
        # The fail-open branch mints its own spend button. Missing it would
        # leave a panel that renders with one destination and spends with
        # another, only when the stock check fails — i.e. only in production.
        with mock.patch("tgbot.bot.volume_datacenter", return_value="EU-RO-1"), \
             mock.patch("tgbot.bot.stock_at_cached", side_effect=RuntimeError("runpodctl down")):
            bot._offer_run_confirm(self.tg, ME, spend_cb="rec:retry:tg-1")
        flat = [data for row in self.tg.buttons[-1] for _, data, *_ in row]
        self.assertIn("rec:retry:tg-1", flat)

    def test_a_heading_replaces_the_choose_gpu_line(self):
        with mock.patch("tgbot.bot.volume_datacenter", return_value="EU-RO-1"), \
             mock.patch("tgbot.bot.stock_at_cached", return_value=self._stock()):
            bot._offer_run_confirm(self.tg, ME, heading="Try-on done — now rent?")
        self.assertIn("Try-on done — now rent?", self.tg.screen[-1])
        self.assertNotIn("Choose GPU", self.tg.screen[-1])
```

- [ ] **Step 2: Run it, verify it fails**

Run: `python3 -m unittest scripts.tests.test_batch_bot.TestRunConfirmPanelIsParameterised -v`
Expected: FAIL — `TypeError: _offer_run_confirm() got an unexpected keyword argument 'spend_cb'`.

- [ ] **Step 3: Implement**

Change the signature:

```python
def _offer_run_confirm(tg: Tg, chat_id: int, *, message_id: int | None = None,
                       force: bool = False, spend_cb: str | None = None,
                       heading: str | None = None) -> None:
```

Immediately after the `configured` / `volume_id` / `home_dc` reads and before the stock try, resolve the two defaults:

```python
    # Defaults resolved here rather than at each of the two mint sites below:
    # the fail-open branch (no stock data) and the normal one each build a
    # spend button, and two call sites defaulting independently is how a panel
    # ends up rendering one destination and spending with another — only when
    # the stock check fails, i.e. only in production.
    spend_cb = spend_cb or (_CB_RUN_GO + _run_token(chat_id))
```

In the fail-open branch, replace `_CB_RUN_GO + _run_token(chat_id)` with `spend_cb`. In the main branch's `buttons.append([...])`, do the same.

Replace the `lines = [...]` seed:

```python
    lines = [heading or (f"{ICON_NVIDIA_CE} <b>Choose GPU</b> — renting at "
                         f"{_esc(home_dc)}"), ""]
```

Add to the docstring, after the paragraph about failing open:

```
    `spend_cb` and `heading` exist because this panel is rendered twice with
    different meanings (2026-09-16): once before anything has run, where
    [Yes, spend] starts Phase A, and once after Phase A has finished, where it
    rents the pod for a batch whose try-on is already on disk. The stock
    rendering is identical in both — that is the reason to parameterise rather
    than duplicate — and the second one is the whole point of the change, since
    it measures stock at the moment of the decision instead of minutes before.
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m unittest scripts.tests.test_batch_bot.TestRunConfirmPanelIsParameterised -v`
Expected: 4 tests, OK.

Run: `make batch-test`
Expected: OK. The existing Choose-GPU tests must pass untouched — that is what proves this was a refactor.

- [ ] **Step 5: Scrub and commit**

```bash
bash motions-studio/setup/scrub-secrets.sh --check
git add scripts/tgbot/bot.py scripts/tests/test_batch_bot.py
git commit -F - <<'EOF'
Telegram bot: parameterise the Choose GPU panel's spend destination

Pure refactor, no behaviour change — the default is still _CB_RUN_GO and
every existing Run button keeps its meaning.

Task 9 renders this same panel a second time, after Phase A, where
[Yes, spend] has to route to _do_resume instead. The stock rendering is
identical in both, which is the reason to parameterise rather than
duplicate a 100-line function whose fail-open and sold-out branches have
each already been fixed once against a live report.

Both defaults resolve at the top rather than at the two mint sites: the
fail-open branch builds its own spend button, and two call sites
defaulting independently is how a panel ends up rendering one
destination and spending with another — only when the stock check fails.
EOF
```

---

## Task 9: `tick_phase_a` — collect the exit code and act on it

**Files:**
- Modify: `scripts/tgbot/bot.py` (`_start_progress` ~line 2686; `tick_progress`'s early return ~line 2780; new `tick_phase_a` after `tick_progress`; the `_CB_PHASE_A_SPEND` constant and its handler; the poll loop ~line 4973; the poll-cadence condition)
- Modify: `scripts/tgbot/run.py` (`progress_text` ~line 150)
- Test: `scripts/tests/test_batch_bot.py`, `scripts/tests/test_batch_tgrun.py`

**Interfaces:**
- Consumes: `phase_a_running`, `phase_a_exit`, `busy` (Task 7); `_offer_run_confirm(spend_cb=, heading=)` (Task 8); `_do_resume` (existing)
- Produces: `tick_phase_a(tg, chat_id, *, dry_run: bool = False) -> None`; `_CB_PHASE_A_SPEND = "pa:spend:"`; `_start_progress(..., *, phase: str | None = None)`; `progress_text(..., phase: str | None = None)`. Task 10 calls `_start_progress(..., phase="local")`.

- [ ] **Step 1: Write the failing `progress_text` test**

Add to `TestProgressText` in `scripts/tests/test_batch_tgrun.py`:

```python
    def test_phase_a_does_not_claim_to_be_waiting_for_a_pod(self):
        # There is no pod to wait for during Phase A — that is the entire
        # point of running it first. Inferring the phase from the absence of
        # a lease is what made this line a lie.
        text = progress_text(self.manifest, lease=None, phase="local")
        self.assertNotIn("waiting for the pod", text)
        self.assertIn("try-on", text.lower())

    def test_no_phase_keeps_the_pod_wording(self):
        self.assertIn("waiting for the pod",
                      progress_text(self.manifest, lease=None))
```

The `STATE` fixture in that file has runs recorded, so the "nothing recorded yet" branch will not fire. Add a second manifest with an empty journal for that branch:

```python
    def test_phase_a_replaces_the_nothing_recorded_line(self):
        empty = Path(tempfile.mkdtemp()) / "none.yaml"
        empty.write_text("runs: []", encoding="utf-8")
        state_path_for(empty).write_text(
            json.dumps({"batch": "2026-09-16-0900", "runs": {}}), encoding="utf-8")
        text = progress_text(empty, lease=None, phase="local")
        self.assertNotIn("waiting for the pod", text)
        self.assertIn("running the try-on", text.lower())
```

- [ ] **Step 2: Implement the `phase` parameter**

In `scripts/tgbot/run.py`, change the signature:

```python
def progress_text(manifest_path: Path, *, lease,
                  stages: list[str] | None = None,
                  phase: str | None = None) -> str:
```

Add to the docstring:

```
    `phase` is "local" while Phase A (the API try-on) is running and None
    otherwise. It exists because the no-lease case used to mean exactly one
    thing — "the pod is being provisioned" — and Phase A broke that: there is
    no pod yet and none is coming until the user says so. Inferring a phase
    from the absence of a lease is how the message came to say "waiting for
    the pod" about a step that deliberately runs before any pod exists.
```

Replace the `if not runs:` block:

```python
    if not runs:
        if phase == "local":
            lines.append(f"{_ICON_EYES_CE} running the try-on over the API — "
                         "no pod rented yet")
        else:
            # This is the provision + bootstrap window, the longest stretch
            # (~10 min) in which the journal says nothing whatsoever — the one
            # phase where the only real question is whether anything is
            # happening at all, which `elapsed` answers and the journal cannot.
            tail = f" ({elapsed})" if elapsed else ""
            lines.append(f"{_ICON_EYES_CE} waiting for the pod — "
                         f"nothing recorded yet{tail}")
```

And guard the pod-cost footer, which is meaningless before a pod exists:

```python
    if lease is not None:
```
— unchanged; `_elapsed` already returns `""` with no lease, so the footer is already suppressed. Verify rather than assume: the existing `if lease is not None:` block is the only place `$…so far` is emitted.

- [ ] **Step 3: Run the `run.py` tests**

Run: `python3 -m unittest scripts.tests.test_batch_tgrun.TestProgressText -v`
Expected: OK, including the three new tests.

- [ ] **Step 4: Write the failing `tick_phase_a` tests**

Add to `scripts/tests/test_batch_bot.py`:

```python
class TestProgressMessageOwnership(unittest.TestCase):
    """tick_progress and tick_phase_a read the SAME progress file, and
    tick_progress runs first in the poll loop.

    Without an explicit owner, the tick after Phase A exits goes: tick_progress
    reads the file, finds drain_running() False (Phase A writes no lease and
    registers no _RUNNING entry), takes its "Finished" branch, unlinks the file
    and calls deliver_result. tick_phase_a then finds no file and returns. The
    user gets a half-finished batch reported as done and never sees the rent
    panel. This is the single easiest way to get commit B wrong and have every
    unit test still pass, because nothing else in the suite runs both ticks.
    """

    def setUp(self):
        self._orig_root = bot.ROOT
        self.root = Path(tempfile.mkdtemp())
        (self.root / "batch").mkdir()
        (self.root / "out").mkdir()
        bot.ROOT = self.root
        reset_bot_state()
        self.manifest = self.root / "batch" / "tg-1.yaml"
        self.manifest.write_text("runs: []\n", encoding="utf-8")
        state_path_for(self.manifest).write_text(json.dumps(
            {"batch": "2026-09-16-0900", "runs": {}}), encoding="utf-8")
        self.tg = FakeTg()
        self._lease = mock.patch("tgbot.bot.lease_for", return_value=None)
        self._lease.start()
        self.addCleanup(self._lease.stop)

    def tearDown(self):
        bot.ROOT = self._orig_root

    def _payload(self) -> dict:
        return json.loads(bot._progress_path(ME).read_text(encoding="utf-8"))

    def test_start_progress_records_the_phase(self):
        with mock.patch("tgbot.bot._start_progress", wraps=bot._start_progress):
            bot._start_progress(self.tg, ME, self.manifest, ["tryon"], phase="local")
        self.assertEqual(self._payload()["phase"], "local")

    def test_the_default_phase_is_absent_so_drains_keep_working(self):
        bot._start_progress(self.tg, ME, self.manifest, ["tryon"])
        self.assertIsNone(self._payload().get("phase"))

    def test_tick_progress_ignores_a_phase_a_message(self):
        bot._start_progress(self.tg, ME, self.manifest, ["tryon"], phase="local")
        with mock.patch("tgbot.bot.deliver_result") as deliver, \
             mock.patch("tgbot.bot.drain_running", return_value=False):
            bot.tick_progress(self.tg, ME)
        deliver.assert_not_called()
        self.assertTrue(bot._progress_path(ME).exists())   # not unlinked

    def test_tick_phase_a_ignores_a_drain_message(self):
        bot._start_progress(self.tg, ME, self.manifest, ["tryon"])
        with mock.patch("tgbot.bot.phase_a_exit", return_value=3), \
             mock.patch("tgbot.bot._offer_run_confirm") as offer:
            bot.tick_phase_a(self.tg, ME)
        offer.assert_not_called()

    def test_the_handoff_to_a_drain_gives_tick_progress_ownership_back(self):
        # After the user taps spend, _do_resume calls _start_progress with no
        # phase, rewriting the file. tick_phase_a must stop handling it even
        # though _PHASE_A_RC still remembers exit 3 — a stale code plus a fresh
        # file with no "offered" latch would re-send the rent panel over the
        # top of a running drain.
        bot._start_progress(self.tg, ME, self.manifest, ["tryon"], phase="local")
        with mock.patch("tgbot.bot.phase_a_running", return_value=False), \
             mock.patch("tgbot.bot.phase_a_exit", return_value=3), \
             mock.patch("tgbot.bot.volume_datacenter", return_value="EU-RO-1"), \
             mock.patch("tgbot.bot.stock_at_cached", return_value={}), \
             mock.patch("tgbot.bot._offer_run_confirm") as offer:
            bot.tick_phase_a(self.tg, ME)
        offer.assert_called_once()
        bot._start_progress(self.tg, ME, self.manifest, ["tryon", "motion"])
        with mock.patch("tgbot.bot.phase_a_exit", return_value=3), \
             mock.patch("tgbot.bot._offer_run_confirm") as offer2:
            bot.tick_phase_a(self.tg, ME)
        offer2.assert_not_called()
```

Then the tick itself:

```python
class TestTickPhaseA(unittest.TestCase):
    """Phase A finishes while nobody is watching; this tick is what turns its
    exit code into the next thing the user sees.

    Exit 3 is the interesting one: it means "local work done, a pod is still
    wanted", and it is where the spend decision now lives.
    """

    def setUp(self):
        self._orig_root = bot.ROOT
        self.root = Path(tempfile.mkdtemp())
        (self.root / "batch").mkdir()
        (self.root / "out").mkdir()
        bot.ROOT = self.root
        reset_bot_state()
        self.manifest = self.root / "batch" / "tg-1.yaml"
        self.manifest.write_text(
            "runs:\n  - id: runA\n    pipeline: tryon-motion-enhance\n"
            "    inputs: {character: /tmp/c.png, outfit: /tmp/o.png, driver: /tmp/d.mp4}\n"
            "    tryon: { provider: gemini }\n", encoding="utf-8")
        state_path_for(self.manifest).write_text(json.dumps(
            {"batch": "2026-09-16-0900", "runs": {}}), encoding="utf-8")
        self.tg = FakeTg()
        # lease_for reads run.LEASE_PATH, which points at the repo's real
        # batch/pod-lease.json. _start_progress calls it, so without this patch
        # the test reads whatever lease happens to be on the machine running
        # the suite — and on a box mid-drain that is a real one naming a real
        # manifest. Patched rather than redirected: Phase A has no lease by
        # definition, so None is also the truthful value.
        self._lease = mock.patch("tgbot.bot.lease_for", return_value=None)
        self._lease.start()
        self.addCleanup(self._lease.stop)
        # phase="local" is what makes tick_phase_a own this message at all —
        # without it the tick returns at its first guard and every test below
        # passes while asserting nothing.
        bot._start_progress(self.tg, ME, self.manifest, ["tryon", "motion", "enhance"],
                            phase="local")

    def tearDown(self):
        bot.ROOT = self._orig_root

    def test_still_running_only_redraws(self):
        with mock.patch("tgbot.bot.phase_a_running", return_value=True), \
             mock.patch("tgbot.bot.phase_a_exit", return_value=None):
            bot.tick_phase_a(self.tg, ME)
        flat = [data for row in (self.tg.buttons[-1] or []) for _, data, *_ in row]
        self.assertFalse(any(d.startswith(bot._CB_PHASE_A_SPEND) for d in flat))

    def test_exit_three_offers_the_rent_decision_with_fresh_stock(self):
        with mock.patch("tgbot.bot.phase_a_running", return_value=False), \
             mock.patch("tgbot.bot.phase_a_exit", return_value=3), \
             mock.patch("tgbot.bot.volume_datacenter", return_value="EU-RO-1"), \
             mock.patch("tgbot.bot.stock_at_cached", return_value={}), \
             mock.patch("tgbot.bot._offer_run_confirm") as offer:
            bot.tick_phase_a(self.tg, ME)
        offer.assert_called_once()
        # The whole point: the panel is rendered AFTER Phase A, so the stock it
        # shows was measured now, and its spend button must not route back
        # through _do_confirm (whose _STATE this chat no longer has).
        self.assertTrue(offer.call_args.kwargs["spend_cb"].startswith(bot._CB_PHASE_A_SPEND))

    def test_the_panel_it_offers_reaches_start_drain_with_state_cleared(self):
        # The §6.3 condition that rules _do_confirm out, end to end: by the
        # time Phase A finishes, _do_confirm has already cleared _STATE, so a
        # panel wired to _CB_RUN_GO would answer "no complete job yet" for a
        # batch whose try-on images are on disk. _do_resume needs no _STATE.
        bot._STATE.clear()
        bot._BASKET.clear()
        with mock.patch("tgbot.bot.phase_a_running", return_value=False), \
             mock.patch("tgbot.bot.phase_a_exit", return_value=3), \
             mock.patch("tgbot.bot.volume_datacenter", return_value="EU-RO-1"), \
             mock.patch("tgbot.bot.stock_at_cached", return_value={}), \
             mock.patch("tgbot.bot.drain_running", return_value=False), \
             mock.patch("tgbot.bot.migration_running", return_value=False), \
             mock.patch("tgbot.bot._start_progress"), \
             mock.patch("tgbot.bot.start_drain") as start_drain:
            bot.tick_phase_a(self.tg, ME)
            flat = [data for row in self.tg.buttons[-1] for _, data, *_ in row]
            spend = next(d for d in flat if d.startswith(bot._CB_PHASE_A_SPEND))
            bot.handle(self.tg, cb_from(ME, spend), allowed_user_id=ME)
        start_drain.assert_called_once()
        self.assertIs(start_drain.call_args.kwargs["resume"], True)
        self.assertEqual(start_drain.call_args.args[0], self.manifest)

    def test_exit_zero_delivers_results_and_offers_nothing_to_rent(self):
        with mock.patch("tgbot.bot.phase_a_running", return_value=False), \
             mock.patch("tgbot.bot.phase_a_exit", return_value=0), \
             mock.patch("tgbot.bot.deliver_result") as deliver, \
             mock.patch("tgbot.bot._offer_run_confirm") as offer:
            bot.tick_phase_a(self.tg, ME)
        deliver.assert_called_once()
        offer.assert_not_called()

    def test_a_failed_phase_a_neither_rents_nor_delivers(self):
        # drain.py:331 already refuses to rent after a failed local phase. The
        # bot must not become a second, laxer copy of that rule.
        with mock.patch("tgbot.bot.phase_a_running", return_value=False), \
             mock.patch("tgbot.bot.phase_a_exit", return_value=1), \
             mock.patch("tgbot.bot.deliver_result") as deliver, \
             mock.patch("tgbot.bot._offer_run_confirm") as offer:
            bot.tick_phase_a(self.tg, ME)
        deliver.assert_not_called()
        offer.assert_not_called()
        self.assertIn("try-on", self.tg.messages[-1].lower())

    def test_no_progress_file_is_a_silent_no_op(self):
        bot._progress_path(ME).unlink()
        with mock.patch("tgbot.bot.phase_a_exit", return_value=3), \
             mock.patch("tgbot.bot._offer_run_confirm") as offer:
            bot.tick_phase_a(self.tg, ME)
        offer.assert_not_called()

    def test_the_panel_is_offered_once_not_every_tick(self):
        # phase_a_exit keeps answering 3 after the process is reaped, by design.
        # Without a latch the poll loop would re-send the panel every 2s for
        # as long as the chat lives.
        with mock.patch("tgbot.bot.phase_a_running", return_value=False), \
             mock.patch("tgbot.bot.phase_a_exit", return_value=3), \
             mock.patch("tgbot.bot.volume_datacenter", return_value="EU-RO-1"), \
             mock.patch("tgbot.bot.stock_at_cached", return_value={}), \
             mock.patch("tgbot.bot._offer_run_confirm") as offer:
            bot.tick_phase_a(self.tg, ME)
            bot.tick_phase_a(self.tg, ME)
        offer.assert_called_once()
```

- [ ] **Step 5: Run them, verify they fail**

Run: `python3 -m unittest scripts.tests.test_batch_bot.TestProgressMessageOwnership scripts.tests.test_batch_bot.TestTickPhaseA -v`
Expected: FAIL — `AttributeError: module 'tgbot.bot' has no attribute 'tick_phase_a'`. The two `_start_progress(phase=…)` tests fail with `TypeError: _start_progress() got an unexpected keyword argument 'phase'`.

- [ ] **Step 6: Implement the ownership marker, then `tick_phase_a`**

First `_start_progress` (~line 2686) gains a `phase` argument and records it:

```python
def _start_progress(tg: Tg, chat_id: int, manifest_path: Path,
                    stages: list[str], *, phase: str | None = None) -> None:
    """Send the first progress message and record it for later edits.

    `phase` names which tick owns the resulting message: "local" while Phase A
    runs, absent otherwise. Both ticks read this same file and tick_progress
    runs first in the poll loop, so without an owner the tick after Phase A
    exits would find drain_running() False (Phase A writes no lease and
    registers no _RUNNING entry), take tick_progress's "Finished" branch,
    unlink the file and deliver_result — reporting a half-finished batch as
    done and never showing the rent panel.
    """
    text = progress_text(manifest_path, lease=lease_for(manifest_path),
                         stages=stages, phase=phase)
    message_id = tg.send_message(chat_id, text, parse_mode=PARSE_HTML)
    _progress_path(chat_id).write_text(json.dumps({
        "manifest": str(manifest_path), "message_id": message_id,
        "stages": stages, "sent_tryon": [],
        **({"phase": phase} if phase else {})}, indent=2), encoding="utf-8")
```

Then `tick_progress` (~line 2758) yields the message when Phase A owns it. Insert immediately after its `try/except` that unpacks `payload`:

```python
    if payload.get("phase") == "local":
        return      # tick_phase_a owns this message — see _start_progress
```

Placed before `_deliver_tryon_previews`, not after: `tick_phase_a` calls that itself, and delivering the same preview twice would send the user two copies of one image.

Now the tick itself. Add the constant next to `_CB_PHASE_A_REUSE`:

```python
# The post-Phase-A spend button. Routes to _do_resume, never _do_confirm:
# _do_confirm starts from _STATE and clears it before returning, so by the
# time Phase A finishes minutes later the draft job is gone and a panel wired
# to _CB_RUN_GO would answer "no complete job yet" for a batch whose try-on
# images are sitting on disk. _do_resume needs no _STATE — it loads the
# manifest and requires only that the journal has a batch id, which Phase A
# writes before its first Gemini call. Carries the manifest stem, not
# _run_token: the manifest is not rewritten between the panel and the tap,
# and the stem is what _do_resume needs anyway.
_CB_PHASE_A_SPEND = "pa:spend:"   # + "<manifest stem>"
```

Import `phase_a_running` and `phase_a_exit` alongside `busy` in the `from tgbot.run import ...` line.

Add after `tick_progress` (~line 2880):

```python
def tick_phase_a(tg: Tg, chat_id: int, *, dry_run: bool = False) -> None:
    """Turn a finished Phase A into the next thing the user sees.

    Separate from tick_progress rather than folded into it: that function's
    completion branch calls deliver_result, which is right when a drain ends
    and wrong when Phase A ends — Phase A ending with exit 3 means the batch
    is HALF done and the next step is a human decision about renting.

    The latch is `offered` in the progress file, not a module dict: a Phase A
    can outlive a bot restart (systemd Restart=always plus a 12-run batch of
    Gemini calls), and phase_a_exit deliberately keeps answering after the
    handle is reaped. Without a durable latch the 2s poll would re-send the
    panel for as long as the chat lives.

    Ownership is the `phase` key in that same file, checked both ways: this
    function returns unless the message is marked "local", and tick_progress
    returns if it is. See _start_progress for what goes wrong otherwise. The
    check matters most on the handoff back — after the user taps spend,
    _do_resume rewrites the file with no phase, and phase_a_exit still
    remembers 3, so without this guard the next tick would re-send the rent
    panel over the top of a drain that is already running.
    """
    path = _progress_path(chat_id)
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        manifest_path = Path(payload["manifest"])
        message_id = int(payload["message_id"])
        stages = list(payload.get("stages") or [])
    except (ValueError, KeyError, TypeError) as exc:
        log(f"progress file for chat {chat_id} is unreadable, dropping it: {exc!r}")
        path.unlink(missing_ok=True)
        return

    if payload.get("phase") != "local":
        return          # a drain owns this message — tick_progress handles it

    if phase_a_running(manifest_path):
        # Try-on previews are worth sending during Phase A too: they are the
        # one piece of visible progress in a phase that records nothing else
        # until a whole stage finishes.
        _deliver_tryon_previews(tg, chat_id, manifest_path, payload)
        text = progress_text(manifest_path, lease=None, stages=stages, phase="local")
        if time.time() >= _ANIM_PAUSE.get(chat_id, 0.0):
            try:
                tg.edit_message(chat_id, message_id, text, parse_mode=PARSE_HTML)
            except TgError as exc:
                wait = exc.retry_after or 60.0
                _ANIM_PAUSE[chat_id] = time.time() + wait
                log(f"phase-A progress edit throttled, pausing {wait:.0f}s: {exc}")
        return

    rc = phase_a_exit(manifest_path)
    if rc is None:
        return          # not ours — a drain, or nothing at all
    if payload.get("offered"):
        return          # already handed the decision to the user

    payload["offered"] = True
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _ANIM_PAUSE.pop(chat_id, None)
    text = progress_text(manifest_path, lease=None, stages=stages, phase="local")
    tg.edit_message(chat_id, message_id, text, parse_mode=PARSE_HTML)

    if rc == EXIT_NEEDS_POD:
        # Stock is measured HERE, not when [Run] was tapped — that gap is the
        # whole reason Phase A moved. Reusing _offer_run_confirm rather than a
        # second panel: its sold-out branch already drops the spend button
        # instead of leaving it enabled on a promise that can only fail
        # (2026-09-12, reported live: "5090 đã hết mà nút spend vẫn enable").
        _offer_run_confirm(
            tg, chat_id,
            spend_cb=f"{_CB_PHASE_A_SPEND}{manifest_path.stem}",
            heading=f"{ICON_NVIDIA_CE} <b>Try-on finished</b> — now rent a GPU?")
    elif rc == 0:
        path.unlink(missing_ok=True)
        deliver_result(tg, chat_id, manifest_path)
    else:
        path.unlink(missing_ok=True)
        tg.send_message(
            chat_id,
            f"{ICON_ERROR_CE} <b>The try-on phase failed</b> (exit {rc}) — "
            "nothing was rented and no GPU time was spent.\n"
            f"<code>{_esc(manifest_path.stem)}.phase-a.log</code> on the box has "
            "the detail. Fix it and tap Run again; the try-ons that did finish "
            "are kept.",
            parse_mode=PARSE_HTML)
```

`EXIT_NEEDS_POD` must be imported into `bot.py`. It lives in `scripts/batch_run.py`; `bot.py` already imports from `drain`, which itself imports it, so add to the existing `from drain import failed_job_ids` line:

```python
from batch_run import EXIT_NEEDS_POD
from drain import failed_job_ids
```

- [ ] **Step 7: Handle the spend callback**

In `_handle_callback`, next to the `_CB_PHASE_A_REUSE` branch:

```python
        elif data.startswith(_CB_PHASE_A_SPEND):
            stem = data[len(_CB_PHASE_A_SPEND):]
            if not stem:
                tg.send_message(chat_id, "that button is from an older "
                                         "version of the bot; check /status")
            else:
                # _do_resume, not _do_confirm — see _CB_PHASE_A_SPEND's own
                # comment for why _STATE cannot be relied on here.
                _do_resume(tg, chat_id, ROOT / "batch" / f"{stem}.yaml",
                           dry_run=dry_run)
```

- [ ] **Step 8: Wire the tick into the poll loop**

At ~line 4973, next to the existing ticks:

```python
            tick_progress(tg, allowed_user_id)
            tick_phase_a(tg, allowed_user_id, dry_run=args.dry_run)
            tick_migration_progress(tg, allowed_user_id, dry_run=args.dry_run)
```

`tick_phase_a` goes **before** `tick_migration_progress` and after `tick_progress`: it is the faster-moving of the two batch ticks (2s cadence while Phase A runs) and shares `_progress_path` with `tick_progress`, so ordering them keeps one owner of that file per tick.

Also extend the poll-cadence condition that drops from 50s to 2s while a drain runs, so Phase A animates too. Find the line that tests `drain_running(...)` for the cadence and make it `busy(...)`.

- [ ] **Step 9: Run the tests**

Run: `python3 -m unittest scripts.tests.test_batch_bot.TestProgressMessageOwnership -v`
Expected: 5 tests, OK.

Run: `python3 -m unittest scripts.tests.test_batch_bot.TestTickPhaseA -v`
Expected: 7 tests, OK.

Run: `make batch-test`
Expected: OK.

- [ ] **Step 10: Scrub and commit**

```bash
bash motions-studio/setup/scrub-secrets.sh --check
git add scripts/tgbot/bot.py scripts/tgbot/run.py \
        scripts/tests/test_batch_bot.py scripts/tests/test_batch_tgrun.py
git commit -F - <<'EOF'
Telegram bot: turn a finished Phase A into the rent decision

tick_phase_a is separate from tick_progress rather than folded in,
because tick_progress's completion branch calls deliver_result — right
when a drain ends, wrong when Phase A ends with exit 3, which means the
batch is half done and the next step is a human decision about renting.

The panel is _offer_run_confirm with a different spend_cb, so stock is
measured here rather than when [Run] was tapped. That gap is the reason
Phase A moved: the old order read stock, spent several minutes on
Gemini, and only then tried to rent — long enough for a 5090 at EU-RO-1
to disappear and take the batch with it.

Its spend button routes to _do_resume, not _do_confirm. _do_confirm
starts from _STATE and clears it before returning, so minutes later the
draft job is gone and a _CB_RUN_GO panel would answer "no complete job
yet" for a batch whose try-on images are on disk.

The offered-latch lives in the progress file, not a module dict: a Phase
A can outlive a bot restart, and phase_a_exit keeps answering after the
handle is reaped, so an in-memory latch would re-send the panel every 2s.

That same file now carries a phase marker, because tick_progress reads
it too and runs first in the poll loop. Without an owner, the tick
after Phase A exits finds drain_running() False — Phase A writes no
lease and registers no _RUNNING entry — takes the "Finished" branch,
unlinks the file and delivers a half-finished batch as done. The rent
panel never appears. Both ticks now check the marker in opposite
directions, which also covers the handoff back: _do_resume rewrites
the file with no phase while phase_a_exit still remembers 3.

progress_text gains a phase argument for the same underlying reason —
inferring "waiting for the pod" from the absence of a lease became a
lie once something deliberately ran before any pod existed.
EOF
```

---

## Task 10: `[Run]` starts Phase A, and the copy catches up

**Files:**
- Modify: `scripts/batchlib/runner.py` (new `has_local_tryon`, next to `preserved_local_tryon`)
- Modify: `scripts/tgbot/bot.py` (imports; new `_manifest_write_ok` / `_offer_run_for_chat` / `_job_has_local_tryon` / `_do_phase_a` / `_start_phase_a_and_report`; the `_CB_RUN_GO` branch ~line 1538; the four `_offer_run_confirm` call sites at ~1465/1479/1496/1512; `_render_and_validate`'s write guard ~line 944; `BOT_COMMANDS` ~line 4898; `/start`'s money line ~line 4865; the poll-cadence condition)
- Test: `scripts/tests/test_batch_runner.py`, `scripts/tests/test_batch_bot.py`

**Interfaces:**
- Consumes: `start_phase_a`, `busy` (Task 7); `has_local_tryon` (added to `runner.py` in this task); `_offer_run_confirm(phase_a=)` (Task 8); `_start_progress`, `_jobs_for`, `_job_manifest_path`, `render_manifest`, `write_manifest` (all existing)
- Produces: `_offer_run_for_chat`, `_job_has_local_tryon`, `_do_phase_a`, `_start_phase_a_and_report`, `_manifest_write_ok` in `bot.py`; `has_local_tryon(manifest: Manifest) -> bool` in `runner.py`. Nothing after this task depends on them — it is the wiring task.

- [ ] **Step 1: Write the failing tests**

First the runner predicate, in `scripts/tests/test_batch_runner.py`. Add `has_local_tryon` to that file's `from batchlib.runner import (...)` block:

```python
class TestHasLocalTryon(unittest.TestCase):
    """The bot's answer to "does [Run] start Phase A, or go straight to the
    spend panel?". It must be _local_tryon_stage's answer, not a second one.
    """

    def test_true_for_a_gemini_tryon_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            manifest = load_manifest(_fixture_tryon(Path(d), MANIFEST_TRYON_GEMINI))
            self.assertTrue(has_local_tryon(manifest))

    def test_false_for_a_pure_motion_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            manifest = load_manifest(_fixture(Path(d), MANIFEST_MOT_RUN))
            self.assertFalse(has_local_tryon(manifest))

    def test_false_when_clean_only_puts_the_tryon_back_on_the_pod(self):
        # _local_tryon_stage checks cleanOnly BEFORE provider, matching the
        # pod's own order (linux.py:4794). A cleanOnly run is a Gemini call
        # that would produce the wrong image, so it must not start Phase A.
        with tempfile.TemporaryDirectory() as d:
            manifest = load_manifest(
                _fixture_tryon(Path(d), MANIFEST_TRYON_GEMINI_CLEANONLY))
            self.assertFalse(has_local_tryon(manifest))

    def test_true_when_any_one_run_of_a_batch_is_local(self):
        # One pod runs the whole manifest, so a single local try-on anywhere
        # means Phase A has work to do. The second run's id is renamed: both
        # module constants call their run "runA", and manifest.py refuses a
        # manifest with a repeated id — concatenating them raw raises
        # ManifestError instead of testing anything.
        with tempfile.TemporaryDirectory() as d:
            text = MANIFEST_TRYON_GEMINI + MANIFEST_MOT_RUN.split("runs:\n")[1].replace(
                "- id: runA", "- id: runB")
            manifest = load_manifest(_fixture_tryon(Path(d), text))
            self.assertEqual([r.id for r in manifest.runs], ["runA", "runB"])
            self.assertTrue(has_local_tryon(manifest))
```

Then the bot surface, in `scripts/tests/test_batch_bot.py`:

```python
class TestRunStartsPhaseAFirst(unittest.TestCase):
    """[Yes, spend] on a manifest with local try-on now starts Phase A, not a
    drain. The spend decision moves to the panel tick_phase_a renders.
    """

    def setUp(self):
        self._orig_root = bot.ROOT
        self.root = Path(tempfile.mkdtemp())
        (self.root / "batch").mkdir()
        (self.root / "out").mkdir()
        bot.ROOT = self.root
        # _offer_run_confirm reads GPU= and POD_VOLUME_ID= from here; without
        # it the panel takes its fail-open branch for the wrong reason.
        (self.root / ".env").write_text(
            "GPU=NVIDIA GeForce RTX 5090\nPOD_VOLUME_ID=vol-1\n", encoding="utf-8")
        reset_bot_state()
        self.tg = FakeTg()

    def tearDown(self):
        bot.ROOT = self._orig_root

    def _write(self, text: str) -> Path:
        p = self.root / "batch" / "tg-1.yaml"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    TRYON = ("runs:\n  - id: runA\n    pipeline: tryon-motion-enhance\n"
             "    inputs: {character: /tmp/c.png, outfit: /tmp/o.png, driver: /tmp/d.mp4}\n"
             "    tryon: { provider: gemini }\n")
    PLAIN = ("runs:\n  - id: runA\n    pipeline: motion-enhance\n"
             "    inputs: {character: /tmp/c.png, driver: /tmp/d.mp4}\n")

    def test_a_tryon_manifest_starts_phase_a_and_does_not_rent(self):
        manifest = self._write(self.TRYON)
        with mock.patch("tgbot.bot.start_phase_a") as phase_a, \
             mock.patch("tgbot.bot.start_drain") as start_drain, \
             mock.patch("tgbot.bot.busy", return_value=False), \
             mock.patch("tgbot.bot._start_progress") as progress:
            bot._start_phase_a_and_report(self.tg, ME, manifest, ["tryon", "motion"])
        phase_a.assert_called_once()
        start_drain.assert_not_called()
        # Asserted here, not only in TestProgressMessageOwnership: that class
        # proves the marker works, this proves the launcher actually sets it.
        # Without it the message stays owned by tick_progress, which reports a
        # half-finished batch as done instead of showing the rent panel.
        self.assertEqual(progress.call_args.kwargs.get("phase"), "local")

    def test_phase_a_is_started_with_resume_when_a_journal_exists(self):
        manifest = self._write(self.TRYON)
        state_path_for(manifest).write_text(json.dumps(
            {"batch": "2026-09-16-0900", "runs": {}}), encoding="utf-8")
        with mock.patch("tgbot.bot.start_phase_a") as phase_a, \
             mock.patch("tgbot.bot.busy", return_value=False), \
             mock.patch("tgbot.bot._start_progress"):
            bot._start_phase_a_and_report(self.tg, ME, manifest, ["tryon"])
        self.assertIs(phase_a.call_args.kwargs["resume"], True)

    def test_the_run_button_says_it_spends_gemini_quota(self):
        # The trade the user accepted: quota goes before the money
        # confirmation. It has to be on the button, not in a docstring — and
        # the assertion is on the LABEL, because the fail-open branch puts the
        # spend wording in the button rather than the message body.
        with mock.patch("tgbot.bot.volume_datacenter", return_value="EU-RO-1"), \
             mock.patch("tgbot.bot.stock_at_cached", return_value={}), \
             mock.patch("tgbot.bot._run_token", return_value="1"):
            bot._offer_run_confirm(self.tg, ME, phase_a=True)
        labels = [label for row in self.tg.buttons[-1] for label, *_ in row]
        self.assertTrue(any("Gemini" in label for label in labels),
                        f"no button mentions the quota: {labels}")

    def test_without_phase_a_the_button_still_quotes_the_hourly_price(self):
        with mock.patch("tgbot.bot.volume_datacenter", return_value="EU-RO-1"), \
             mock.patch("tgbot.bot.stock_at_cached", return_value={}), \
             mock.patch("tgbot.bot._run_token", return_value="1"):
            bot._offer_run_confirm(self.tg, ME)
        labels = [label for row in self.tg.buttons[-1] for label, *_ in row]
        self.assertTrue(any("$0.99/h" in label for label in labels), labels)

    def test_a_plain_draft_is_offered_the_unchanged_single_tap(self):
        # _offer_run_for_chat asks the DRAFT, not a manifest on disk: at
        # _CB_RUN_ASK time the file may not have been written yet.
        with mock.patch("tgbot.bot._job_has_local_tryon", return_value=False), \
             mock.patch("tgbot.bot._offer_run_confirm") as offer:
            bot._offer_run_for_chat(self.tg, ME)
        # phase_a, not spend_cb: the two flows differ in what the button SAYS
        # and where the _CB_RUN_GO handler GOES. spend_cb stays at its
        # _CB_RUN_GO default in both.
        self.assertIs(offer.call_args.kwargs["phase_a"], False)

    def test_a_tryon_draft_is_offered_the_two_step_flow(self):
        with mock.patch("tgbot.bot._job_has_local_tryon", return_value=True), \
             mock.patch("tgbot.bot._offer_run_confirm") as offer:
            bot._offer_run_for_chat(self.tg, ME)
        self.assertIs(offer.call_args.kwargs["phase_a"], True)

    def test_message_id_and_force_are_forwarded(self):
        # Three of the four call sites re-render an existing message rather
        # than sending a new one, and Refresh passes force=True to bypass the
        # stock cache. Dropping either would silently regress the 2026-09-12
        # edit-in-place behaviour.
        with mock.patch("tgbot.bot._job_has_local_tryon", return_value=True), \
             mock.patch("tgbot.bot._offer_run_confirm") as offer:
            bot._offer_run_for_chat(self.tg, ME, message_id=42, force=True)
        self.assertEqual(offer.call_args.kwargs["message_id"], 42)
        self.assertIs(offer.call_args.kwargs["force"], True)

    def test_run_go_starts_phase_a_for_a_tryon_draft(self):
        # The actual user-facing behaviour of commit B, and the one nothing
        # else in this task covers: [Yes, spend] on a try-on draft must NOT
        # reach _do_confirm, which would rent a pod before the try-on exists.
        with mock.patch("tgbot.bot._run_token", return_value="1"), \
             mock.patch("tgbot.bot._job_has_local_tryon", return_value=True), \
             mock.patch("tgbot.bot._do_phase_a") as do_phase_a, \
             mock.patch("tgbot.bot._do_confirm") as do_confirm:
            bot.handle(self.tg, cb_from(ME, bot._CB_RUN_GO + "1"), allowed_user_id=ME)
        do_phase_a.assert_called_once()
        do_confirm.assert_not_called()

    def test_run_go_still_confirms_directly_for_a_plain_draft(self):
        with mock.patch("tgbot.bot._run_token", return_value="1"), \
             mock.patch("tgbot.bot._job_has_local_tryon", return_value=False), \
             mock.patch("tgbot.bot._do_phase_a") as do_phase_a, \
             mock.patch("tgbot.bot._do_confirm") as do_confirm:
            bot.handle(self.tg, cb_from(ME, bot._CB_RUN_GO + "1"), allowed_user_id=ME)
        do_confirm.assert_called_once()
        do_phase_a.assert_not_called()

    def test_run_go_still_refuses_a_stale_token(self):
        with mock.patch("tgbot.bot._run_token", return_value="2"), \
             mock.patch("tgbot.bot._job_has_local_tryon", return_value=True), \
             mock.patch("tgbot.bot._do_phase_a") as do_phase_a, \
             mock.patch("tgbot.bot._do_confirm") as do_confirm:
            bot.handle(self.tg, cb_from(ME, bot._CB_RUN_GO + "1"), allowed_user_id=ME)
        do_phase_a.assert_not_called()
        do_confirm.assert_not_called()

    def test_the_write_guard_covers_phase_a_too(self):
        # _render_and_validate rewrites the manifest file. drain.py reads that
        # file, and --phase-a-only is drain.py, so a rewrite mid-Phase-A
        # corrupts the input of a running child.
        self._write(self.PLAIN)
        with mock.patch("tgbot.bot.busy", return_value=True):
            self.assertFalse(bot._manifest_write_ok(ME))
        with mock.patch("tgbot.bot.busy", return_value=False):
            self.assertTrue(bot._manifest_write_ok(ME))
```

- [ ] **Step 2: Run them, verify they fail**

Run: `python3 -m unittest scripts.tests.test_batch_runner.TestHasLocalTryon -v`
Expected: FAIL — `ImportError: cannot import name 'has_local_tryon'`.

Run: `python3 -m unittest scripts.tests.test_batch_bot.TestRunStartsPhaseAFirst -v`
Expected: FAIL — `AttributeError: module 'tgbot.bot' has no attribute '_start_phase_a_and_report'`.

- [ ] **Step 3: Add the predicate and the launcher**

Import `_local_tryon_stage`'s public face. It is private to `runner.py`, so use `preserved_local_tryon`'s sibling instead — add a tiny public predicate to `scripts/batchlib/runner.py` next to `preserved_local_tryon`:

```python
def has_local_tryon(manifest: Manifest) -> bool:
    """True if ANY run in this manifest has a try-on Phase A can do locally.

    The bot's answer to "does [Run] start Phase A, or go straight to the spend
    panel?". A manifest with none keeps today's single-tap flow: adding a step
    that runs nothing and reports nothing would cost a round trip for no
    information. Asks _local_tryon_stage rather than re-deriving, per that
    function's own docstring — this is its fourth caller and must not be a
    second opinion.
    """
    return any(_local_tryon_stage(run) is not None for run in manifest.runs)
```

Add `has_local_tryon` to `bot.py`'s `from batchlib.runner import (...)` line.

In `scripts/tgbot/bot.py`, add near `_do_confirm`:

```python
def _manifest_write_ok(chat_id: int) -> bool:
    """May this chat's manifest file be rewritten right now?

    One predicate for the three guards that exist because a child process
    READS that file (drain.py, in both its modes): _render_and_validate,
    /clear and /wipe. busy() rather than drain_running(), since --phase-a-only
    is drain.py too and a rewrite mid-Phase-A corrupts a running child's
    input.
    """
    return not busy(_job_manifest_path(chat_id))


def _offer_run_for_chat(tg: Tg, chat_id: int, *, message_id: int | None = None,
                        force: bool = False) -> None:
    """[Run]'s first screen, choosing between the two flows by manifest content.

    A chat whose draft has local try-on gets the two-step flow (Phase A, then
    decide about the GPU with the try-on results in hand and stock measured
    now). One without it keeps the single tap it has always had: there is
    nothing for Phase A to run, so an extra screen would report nothing and
    cost a round trip.

    All FOUR of _offer_run_confirm's call sites route through here rather than
    calling it directly (_CB_RUN_ASK, _CB_RUN_SWITCH, _CB_RUN_BACK and
    _CB_RUN_REFRESH). They have to, or the panel re-rendered after a GPU switch
    or a Refresh would drop back to the one-step flow and its button would
    promise a rental that the first screen said was two steps away.

    Asks the DRAFT, not the manifest on disk: at _CB_RUN_ASK time the file may
    not have been written yet.
    """
    _offer_run_confirm(tg, chat_id, message_id=message_id, force=force,
                       phase_a=_job_has_local_tryon(chat_id))


def _start_phase_a_and_report(tg: Tg, chat_id: int, manifest_path: Path,
                              stages: list[str]) -> None:
    """Launch Phase A and say what it is about to cost.

    resume=True whenever a journal already exists for this manifest: Phase A
    writes its batch id before the first Gemini call, so a journal means
    try-ons may already be paid for, and resume is what makes them skipped
    rather than billed twice. When there is no journal, resolve_batch_id's own
    "RESUME=1 but nothing to continue" branch reports it and runs as new.
    """
    has_journal = bool(load_state(state_path_for(manifest_path)).get("batch"))
    start_phase_a(manifest_path, resume=has_journal)
    tg.send_message(
        chat_id,
        f"{ICON_ROCKET_CE} <b>Running the try-on over the API.</b>\n"
        "This spends Gemini quota, not GPU time — no pod is rented yet. When "
        "it finishes I will show live GPU stock and ask before spending "
        "anything.",
        parse_mode=PARSE_HTML)
    # phase="local" hands the progress message to tick_phase_a. Omitting it
    # leaves the message owned by tick_progress, which sees no lease and no
    # _RUNNING entry, concludes the batch finished, and delivers a
    # half-finished result instead of the rent panel.
    _start_progress(tg, chat_id, manifest_path, stages, phase="local")
```

Import `start_phase_a` alongside `busy` in the `from tgbot.run import ...` line.

- [ ] **Step 4: Add the `phase_a` parameter to `_offer_run_confirm`**

Extend Task 8's signature:

```python
def _offer_run_confirm(tg: Tg, chat_id: int, *, message_id: int | None = None,
                       force: bool = False, spend_cb: str | None = None,
                       heading: str | None = None, phase_a: bool = False) -> None:
```

Where the spend button label is built (both branches), make the label carry what it now costs:

```python
    spend_label = (f"Yes — run try-on first (Gemini quota, no GPU yet)"
                   if phase_a else f"Yes, spend ${price:.2f}/h")
```

and use `spend_label` in both `buttons.append` / fallback sites, keeping `spend_cb` as the destination. Add to the docstring:

```
    `phase_a` relabels the spend button, because with the try-on moved ahead
    of the rental the first tap no longer rents anything: it spends Gemini
    quota. Leaving the label at "Yes, spend $0.99/h" would make the button
    promise a pod it does not start, and the GPU decision now happens on a
    second panel rendered after the try-on results exist.
```

Then rewire **all four** existing call sites to go through `_offer_run_for_chat`, so none of them can drop back to the one-step flow. As of 2026-09-16 they are at `bot.py:1465` (`_CB_RUN_ASK`), `:1479` (`_CB_RUN_SWITCH`), `:1496` (`_CB_RUN_BACK`) and `:1512` (`_CB_RUN_REFRESH`'s `else` branch):

```python
                _offer_run_for_chat(tg, chat_id)                                   # was _offer_run_confirm(tg, chat_id)
                _offer_run_for_chat(tg, chat_id, message_id=msg_id)                # x2
                _offer_run_for_chat(tg, chat_id, message_id=msg_id, force=True)    # the Refresh branch
```

`_CB_RUN_REFRESH`'s other two branches (`view == "s"` and `view == "g"`) call `_offer_run_switch_menu` / `_offer_run_migrate_menu` and are **not** touched — those are submenus, not the spend panel.

Verify none were missed:

Run: `grep -n '_offer_run_confirm(' scripts/tgbot/bot.py`
Expected: exactly one hit — the `def` itself. Every call goes through `_offer_run_for_chat`.

- [ ] **Step 5: Route `_CB_RUN_GO` through the new flow**

Replace the `_CB_RUN_GO` branch body (~line 1538). The token check is unchanged:

```python
        elif data.startswith(_CB_RUN_GO):
            if data[len(_CB_RUN_GO):] != _run_token(chat_id):
                tg.send_message(chat_id,
                                "the job changed since that button was sent, so "
                                "nothing ran. Check the manifest above and "
                                "confirm again.")
            elif _job_has_local_tryon(chat_id):
                # Two-step flow: Phase A first, rent afterwards. _do_confirm is
                # NOT called here — it clears _STATE, and the panel
                # tick_phase_a renders minutes later needs the manifest on disk
                # and the journal to have a batch id, both of which Phase A
                # produces. Nothing has been spent yet at this point.
                _do_phase_a(tg, chat_id, dry_run=dry_run)
            else:
                _do_confirm(tg, chat_id, dry_run=dry_run)
```

Add the two helpers. `_job_has_local_tryon` asks the manifest the chat would write:

```python
def _job_has_local_tryon(chat_id: int) -> bool:
    """Would this chat's next batch have a Phase A?

    Reads the drafted jobs, not the manifest on disk: at [Run] time the file
    may be stale or absent, and the jobs in _STATE/_BASKET are what
    write_manifest is about to render.

    Renders to a throwaway file and loads it back rather than inspecting the
    jobs directly, because the alternative is a second opinion — _local_tryon_stage
    is the only thing allowed to answer this, and it takes a loaded Run. A
    TemporaryDirectory, not mkdtemp(): this runs on every [Run] tap, and a
    leaked directory per tap on a long-lived VPS process is how /tmp fills up
    with something nobody owns.

    Fails towards False, i.e. towards _do_confirm and today's behaviour: a
    manifest that will not render here will not render in write_manifest
    either, and _do_confirm reports that properly. Silently doing nothing would
    be worse than falling through.
    """
    queued = _jobs_for(chat_id)
    if not queued:
        return False
    try:
        text = render_manifest(queued, now=time.strftime("%Y-%m-%d %H:%M:%S"))
        with tempfile.TemporaryDirectory() as d:
            probe = Path(d) / "probe.yaml"
            probe.write_text(text, encoding="utf-8")
            return has_local_tryon(load_manifest(probe))
    except (ManifestError, OSError):
        return False


def _do_phase_a(tg: Tg, chat_id: int, *, dry_run: bool) -> None:
    """The pre-spend half of _do_confirm: same guards, same manifest write,
    no pod and no CONFIRM=yes.

    Shares _do_confirm's guard order deliberately — migration_running first,
    then completeness, then the unanswered-file queue — so the two entry
    points cannot drift into accepting a job the other would refuse. What it
    does NOT do is freeze the panel or clear _STATE: Phase A is not a
    submission, and the spend decision still has to happen afterwards.
    """
    if migration_running():
        tg.send_message(chat_id, "a volume migration is in progress for this pod's "
                                 "datacenter — wait for it to finish before renting")
        return
    queued = _jobs_for(chat_id)
    if not queued:
        tg.send_message(chat_id, "no complete job yet — send the required files first")
        return
    pending = _PENDING.get(chat_id) or []
    if pending and chat_id not in _CONFIRM_WARNED:
        _CONFIRM_WARNED.add(chat_id)
        tg.send_message(chat_id,
                        f"{ICON_FLAG_CE} {len(pending)} file(s) still unassigned — answer "
                        f"them, or send /confirm again to run without them",
                        parse_mode=PARSE_HTML)
        return
    if not _manifest_write_ok(chat_id):
        tg.send_message(chat_id, "a batch is already running for this job — "
                                 "/status shows it")
        return
    live_path = _job_manifest_path(chat_id)
    write_manifest(queued, live_path, now=time.strftime("%Y-%m-%d %H:%M:%S"))
    stages: list[str] = []
    for other in queued:
        for stage in PIPELINES[other.pipeline]:
            if stage not in stages:
                stages.append(stage)
    _start_phase_a_and_report(tg, chat_id, live_path, stages)
```

`render_manifest` and `tempfile` need importing into `bot.py`: add `render_manifest` to the existing `from tgbot.job import ...` line, and `import tempfile` to the stdlib block if it is not already there.

- [ ] **Step 6: Switch the remaining write guard**

In `_render_and_validate` (~line 944), replace the `drain_running(live_path)` write-guard test with `_manifest_write_ok(chat_id)`. Read the surrounding branch first: it currently chooses between writing the live manifest and the mailbox, and that choice must stay on `drain_running` — only the *refuse-to-write* condition changes.

- [ ] **Step 7: Update the copy**

`BOT_COMMANDS` (~line 4898). `confirm` is no longer the only spend path, and `Run` now has two meanings depending on the manifest:

```python
    ("confirm", "SPENDS MONEY - rents a GPU at $0.99/h and starts"),
```
becomes
```python
    ("confirm", "SPENDS MONEY - rents a GPU at $0.99/h and starts (Run may ask first)"),
```

Also update `/start`'s money line (~line 4865), which currently reads
`f"{ICON_SPEND_CE} <b>Nothing spends money until you tap Run and confirm.</b>\n\n"`.
That is now false for Gemini quota:

```python
        f"{ICON_SPEND_CE} <b>Nothing rents a GPU until you tap Run and confirm.</b>\n"
        "A batch with API try-on spends Gemini quota first, before any pod "
        "exists — you get asked about the GPU afterwards, with live stock.\n\n"
```

Check every other place that promises "nothing spends":

Run: `grep -n 'spends money\|spend money\|SPENDS MONEY' scripts/tgbot/bot.py`
Expected: the hits are the ones above plus `run.py`'s docstrings. Any others get the same correction — a copy line that promises no spend while a path spends Gemini quota is the same class of bug as the `Đợi` button advising `/confirm again`.

- [ ] **Step 8: Run the tests**

Run: `python3 -m unittest scripts.tests.test_batch_runner.TestHasLocalTryon -v`
Expected: 4 tests, OK.

Run: `python3 -m unittest scripts.tests.test_batch_bot.TestRunStartsPhaseAFirst -v`
Expected: 11 tests, OK.

Run: `make batch-test`
Expected: OK. Watch specifically for the `TestFlow` confirm tests — `_CB_RUN_GO` now has three branches, and a `tryon`-pipeline fixture in `TestFlow` would take the Phase A path instead of `_do_confirm`. If one does, that test was pinning the old order and must be updated to assert the new one, not reverted.

- [ ] **Step 9: Verify the money invariants one final time**

Run: `grep -rn 'start_drain(' scripts/tgbot/bot.py | grep -v mock | grep -v '^\s*#'`
Expected: exactly two call sites (`_do_confirm`, `_do_resume`).

Run: `grep -rn 'CONFIRM=yes' scripts/tgbot/ --include=*.py`
Expected: `run.py` only, one `argv.append`.

Run: `grep -rn 'start_phase_a(' scripts/tgbot/bot.py | grep -v mock`
Expected: one call site, in `_start_phase_a_and_report`.

- [ ] **Step 10: Full gates**

```bash
make batch-test
make check-job-types
make check-comfy-nodes
make check-batch-params
bash motions-studio/setup/scrub-secrets.sh --check
```
Expected: all green. None of these rents a pod.

- [ ] **Step 11: Commit**

```bash
git add scripts/batchlib/runner.py scripts/tgbot/bot.py \
        scripts/tests/test_batch_bot.py scripts/tests/test_batch_runner.py
git commit -F - <<'EOF'
Telegram bot: run the try-on before asking about the GPU

[Run] on a manifest with API try-on now starts Phase A instead of a
drain. The GPU panel is rendered afterwards, by tick_phase_a, so the
stock a user decides on is measured at the moment of the decision
rather than minutes earlier — the window in which a 5090 at EU-RO-1
disappeared and took a confirmed batch down with it, after Gemini had
already been billed for the try-on.

A manifest with no local try-on keeps its single tap. has_local_tryon
asks _local_tryon_stage, its fourth caller and still not a second
opinion.

_do_phase_a shares _do_confirm's guard order so the two entry points
cannot drift into accepting a job the other would refuse, but it does
not freeze the panel or clear _STATE: Phase A is not a submission. The
write guards move to busy() because --phase-a-only is drain.py too, and
rewriting the manifest mid-Phase-A corrupts a running child's input.

Copy corrected where it promised "nothing spends money until you tap
Run" — that was true of the pod and never of Gemini quota, and a
promise like that is what makes the quota spend feel like a bug.
EOF
```

---

## After both commits

- [ ] **Erratum — correct the `params.py` misattribution in three code comments.** Spec §5 originally said a change to "`params.py`'s defaults" would silently invalidate journalled `params_manifest` values, and that wording propagated into code before the Task 3 re-review caught it. **`params.py` is the wrong module**: `effective_stage_params` merges `STAGES[stage_name].defaults` and `.locked_params` from `pipelines.py:103`, declared at `:35-36` and populated at `:74-84`; `params.py` validates param *names* against `linux.py`'s AST and `batch-params.json`, and `runner.py` does not import it at all. The reasoning is unaffected — a change to the defaults really would invalidate every journalled value — only the module name is wrong, and it sends a reader somewhere that cannot move the number. Fix all three, in one commit:
  - `scripts/batchlib/runner.py:441` — `_local_provenance_stale`'s docstring
  - `scripts/batchlib/runner.py:586` — the Phase A skip comment rewritten in Task 3's fix round
  - `scripts/tests/test_batch_runner.py:1033` — a test comment from Task 1

  The spec is corrected; this plan is not, because the two remaining instances (Task 1's and Task 3's commit-message blocks) are verbatim records of what `c5c480b` and `1e363f6` actually say. History is not being rewritten for a comment-level attribution error, so the plan keeps matching it. Do **not** "fix" `runner.py:390` — its `params.py` reference is correct, it says that module records both spellings as valid params, which is exactly its job.

- [ ] **Update the operator docs.** `docs/batch-runner.md` §2.9 describes the try-on-survives-a-pod-stop behaviour; it needs the params-aware caveat (a provider change invalidates reuse) and `--force-local`. `QWEN.md`'s "Known traps" gains one line: a stock-out retry through `/confirm` used to re-bill Gemini, and the chooser is why it no longer does silently. `docs/gpu-pod.md`'s runbook is unaffected — no pod lifecycle step changed.

- [ ] **State the verification boundary honestly.** Everything above is `make batch-test`: journal logic, argv, button wiring. None of it proves a real drain rents a pod, runs Phase B and destroys it. That needs `make gpu-smoke` or one real batch, which costs money and needs a human decision to start. Report unit coverage and paid end-to-end validation as two separate claims, per QWEN.md.

- [ ] **Record what was measured.** If a real batch is run to validate, put the numbers in `docs/superpowers/specs/` — including Phase A's wall-clock, which nothing has measured yet and which determines how wide the stock-out window in §2 actually is.
