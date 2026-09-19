# scripts/tests/test_batch_tgrun.py
import ast
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.manifest import state_path_for
from batchlib.pipelines import STAGES
from batchlib_ext.lease import Lease, write_lease
import tgbot.run as run_mod
from tgbot.run import drain_running, progress_text

STATE = {"batch": "2026-08-31-2140",
         "runs": {"job": {"status": "running",
                          "stages": {"motion": {"status": "done", "sec": 247},
                                     "enhance": {"status": "running"}}}}}


class TestProgressText(unittest.TestCase):
    def setUp(self):
        self.manifest = Path(tempfile.mkdtemp()) / "m.yaml"
        self.manifest.write_text("runs: []", encoding="utf-8")
        import json
        state_path_for(self.manifest).write_text(json.dumps(STATE), encoding="utf-8")
        # The journal shape Phase A actually produces: a batch id but no run
        # recorded yet. STATE above has runs recorded, so the "nothing recorded
        # yet" line never fires for it — asserting on self.manifest would test
        # a branch that cannot run.
        self.empty = Path(tempfile.mkdtemp()) / "none.yaml"
        self.empty.write_text("runs: []", encoding="utf-8")
        state_path_for(self.empty).write_text(
            json.dumps({"batch": "2026-09-16-0900", "runs": {}}), encoding="utf-8")

    def test_names_every_stage_and_its_status(self):
        text = progress_text(self.manifest, lease=None)
        self.assertIn("motion", text)
        self.assertIn("enhance", text)

    def test_reads_the_journal_so_it_still_works_after_the_pod_is_gone(self):
        # lease=None means no pod. The journal is the source of truth, exactly
        # as batch_status uses it, so progress stays reportable after destroy.
        text = progress_text(self.manifest, lease=None)
        self.assertIn("2026-08-31-2140", text)

    def test_runs_not_in_this_manifest_are_not_listed(self):
        # Reported live 2026-09-18: the journal still held a finished batch's
        # runs, and the panel listed them above the ones actually running.
        manifest = Path(tempfile.mkdtemp()) / "m.yaml"
        manifest.write_text(
            "runs:\n  - id: newRun\n    pipeline: motion-enhance\n"
            "    inputs: {character: /tmp/c.png, driver: /tmp/d.mp4}\n",
            encoding="utf-8")
        state_path_for(manifest).write_text(json.dumps({
            "batch": "2026-09-16-1706", "runs": {
                "oldRun": {"status": "done", "stages": {
                    "motion": {"status": "done"}, "enhance": {"status": "done"}}},
                "newRun": {"status": "running", "stages": {
                    "motion": {"status": "running"}}}}}), encoding="utf-8")
        text = progress_text(manifest, lease=None)
        self.assertEqual(text.count("enhance"), 1)
        self.assertNotIn("2/2", text)

    def test_no_state_file_is_reported_not_crashed(self):
        empty = Path(tempfile.mkdtemp()) / "none.yaml"
        empty.write_text("runs: []", encoding="utf-8")
        self.assertIsInstance(progress_text(empty, lease=None), str)

    def test_phase_a_does_not_claim_to_be_waiting_for_a_pod(self):
        # There is no pod to wait for during Phase A — that is the entire
        # point of running it first. Inferring the phase from the absence of
        # a lease is what made this line a lie.
        text = progress_text(self.empty, lease=None, phase="local")
        self.assertNotIn("waiting for the pod", text)
        self.assertIn("try-on", text.lower())

    def test_no_phase_keeps_the_pod_wording(self):
        self.assertIn("waiting for the pod",
                      progress_text(self.empty, lease=None))

    def test_phase_a_replaces_the_nothing_recorded_line(self):
        empty = Path(tempfile.mkdtemp()) / "none.yaml"
        empty.write_text("runs: []", encoding="utf-8")
        state_path_for(empty).write_text(
            json.dumps({"batch": "2026-09-16-0900", "runs": {}}), encoding="utf-8")
        text = progress_text(empty, lease=None, phase="local")
        self.assertNotIn("waiting for the pod", text)
        self.assertIn("running the try-on", text.lower())


class _FakeProc:
    """Stands in for subprocess.Popen: only .poll() is ever read by drain_running."""
    def __init__(self, poll_return):
        self._poll_return = poll_return

    def poll(self):
        return self._poll_return


class TestDrainRunning(unittest.TestCase):
    def setUp(self):
        # Isolate every test from module-global state: _RUNNING is a process-wide
        # dict, and LEASE_PATH is monkeypatched per test rather than pointing at
        # the real batch/pod-lease.json (which must never be touched by a test).
        self._orig_running = dict(run_mod._RUNNING)
        self._orig_lease_path = run_mod.LEASE_PATH
        run_mod._RUNNING.clear()

    def tearDown(self):
        run_mod._RUNNING.clear()
        run_mod._RUNNING.update(self._orig_running)
        run_mod.LEASE_PATH = self._orig_lease_path

    def test_false_when_no_lease_and_no_process(self):
        m = Path(tempfile.mkdtemp()) / "m.yaml"
        m.write_text("runs: []", encoding="utf-8")
        run_mod.LEASE_PATH = Path(tempfile.mkdtemp()) / "no-lease.json"
        self.assertFalse(drain_running(m))

    def test_true_while_the_process_is_alive(self):
        m = Path(tempfile.mkdtemp()) / "m.yaml"
        m.write_text("runs: []", encoding="utf-8")
        run_mod.LEASE_PATH = Path(tempfile.mkdtemp()) / "no-lease.json"
        run_mod._RUNNING[m.resolve()] = _FakeProc(poll_return=None)
        self.assertTrue(drain_running(m))

    def test_false_once_the_process_has_exited(self):
        m = Path(tempfile.mkdtemp()) / "m.yaml"
        m.write_text("runs: []", encoding="utf-8")
        run_mod.LEASE_PATH = Path(tempfile.mkdtemp()) / "no-lease.json"
        run_mod._RUNNING[m.resolve()] = _FakeProc(poll_return=0)
        self.assertFalse(drain_running(m))

    def test_true_when_a_lease_names_this_manifest_even_with_no_process(self):
        # Regression test for the review finding: a bot restart (systemd
        # Restart=always) empties _RUNNING, and SIGKILL of the drain child
        # (KillMode=control-group) skips drain.py's `finally: teardown()`, so
        # the lease is the only signal left that a pod may still be rented.
        #
        # PLATFORM NOTE (2026-08-31): this test also happens to catch dropping
        # the `.resolve()` in lease_for's comparison — but only on macOS, where
        # tempfile.mkdtemp() returns /var/... and /var is a symlink to
        # /private/var, so the unresolved and resolved spellings differ. On
        # Linux (the VPS, and any future CI) mkdtemp returns an already-canonical
        # path, the two spellings are identical, and that mutation stops being
        # caught here silently. If this ever runs on Linux, pin it with an
        # explicit symlinked temp dir instead of relying on the platform.
        m = Path(tempfile.mkdtemp()) / "m.yaml"
        m.write_text("runs: []", encoding="utf-8")
        lease_path = Path(tempfile.mkdtemp()) / "pod-lease.json"
        write_lease(lease_path, Lease(pod_id="pod-x", provisioned_at=0.0,
                                      manifest=str(m.resolve()), abs_max_min=180))
        run_mod.LEASE_PATH = lease_path
        self.assertTrue(drain_running(m))

    def test_false_when_a_lease_names_a_different_manifest(self):
        m = Path(tempfile.mkdtemp()) / "m.yaml"
        m.write_text("runs: []", encoding="utf-8")
        other = Path(tempfile.mkdtemp()) / "other.yaml"
        lease_path = Path(tempfile.mkdtemp()) / "pod-lease.json"
        write_lease(lease_path, Lease(pod_id="pod-x", provisioned_at=0.0,
                                      manifest=str(other.resolve()), abs_max_min=180))
        run_mod.LEASE_PATH = lease_path
        self.assertFalse(drain_running(m))


from tgbot.run import estimate_minutes, final_files, start_drain, summary_text
from tgbot.job import Job


class TestStartDrain(unittest.TestCase):
    """The single most money-critical line in this repo: `CONFIRM=yes` is
    appended only when dry_run is False (tgbot/run.py:118-120), and passing it
    reaches pod-provision.sh and rents an RTX 5090 at $0.99/hour.

    Until 2026-08-31 nothing asserted on that argv at all — every bot test
    patches `tgbot.bot.start_drain`, so an inverted condition here would have
    shipped as a pod nobody asked for and no gate would have caught it. These
    tests patch Popen, so they invoke nothing and cost nothing.
    """

    def setUp(self):
        self._orig_running = dict(run_mod._RUNNING)
        run_mod._RUNNING.clear()
        self.manifest = Path(tempfile.mkdtemp()) / "m.yaml"
        self.manifest.write_text("runs: []", encoding="utf-8")

    def tearDown(self):
        run_mod._RUNNING.clear()
        run_mod._RUNNING.update(self._orig_running)

    def _argv_for(self, *, dry_run: bool):
        with mock.patch("tgbot.run.subprocess.Popen") as popen:
            start_drain(self.manifest, dry_run=dry_run)
        popen.assert_called_once()
        return popen.call_args[0][0]

    def test_dry_run_never_writes_confirm(self):
        argv = self._argv_for(dry_run=True)
        self.assertEqual(argv, ["make", "drain", f"FILE={self.manifest}"])
        self.assertNotIn("CONFIRM=yes", argv)

    def test_a_real_run_appends_confirm(self):
        argv = self._argv_for(dry_run=False)
        self.assertEqual(argv, ["make", "drain", f"FILE={self.manifest}", "CONFIRM=yes"])

    def test_output_goes_to_a_log_file_beside_the_manifest_not_a_pipe(self):
        # A drain runs for the lifetime of a rented pod. A Popen pipe nobody
        # reads fills its OS buffer and deadlocks the child mid-batch.
        with mock.patch("tgbot.run.subprocess.Popen") as popen:
            start_drain(self.manifest, dry_run=True)
        self.assertTrue(self.manifest.with_suffix(".drain.log").exists())
        self.assertIsNot(popen.call_args.kwargs["stdout"], subprocess.PIPE)

    def test_runs_in_its_own_process_group(self):
        # F2/C2: a bare `proc.terminate()` (SIGTERM to this one process) does not reach
        # drain.py's `finally` or the vast_rent.py it spawns, and the bot's SIGTERM used to be
        # sent to only THIS Popen — an orphaned vast_rent.py kept renting after /kill gave up.
        # start_new_session=True puts make/drain.py/vast_rent together in one process group so
        # a single killpg reaches all of them (bot.py's _do_kill).
        with mock.patch("tgbot.run.subprocess.Popen") as popen:
            start_drain(self.manifest, dry_run=True)
        self.assertTrue(popen.call_args.kwargs.get("start_new_session"))


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
        self._orig_phase_a_rc = dict(run_mod._PHASE_A_RC)
        self._orig_lease_path = run_mod.LEASE_PATH
        run_mod._RUNNING.clear()
        run_mod._PHASE_A.clear()
        # Cleared as well as saved, matching _RUNNING and _PHASE_A above:
        # start_phase_a pops from this dict and phase_a_exit writes into it,
        # so an uncleared one carries a previous test's exit code forward.
        # Keys are tempdir-unique today, which is exactly the sort of accident
        # an isolation fixture is supposed to make impossible.
        run_mod._PHASE_A_RC.clear()
        run_mod.LEASE_PATH = Path(tempfile.mkdtemp()) / "no-lease.json"

    def tearDown(self):
        run_mod._RUNNING.clear()
        run_mod._RUNNING.update(self._orig_running)
        run_mod._PHASE_A.clear()
        run_mod._PHASE_A.update(self._orig_phase_a)
        # _PHASE_A_RC is cleared here as well as saved: phase_a_exit() writes
        # into it by design (that is what makes the tick idempotent), so two of
        # these tests mutate it and an unrestored dict would carry an exit code
        # into whatever runs next.
        run_mod._PHASE_A_RC.clear()
        run_mod._PHASE_A_RC.update(self._orig_phase_a_rc)
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
        # is the repo-wide grep run.py:259 has always asked a human to do, made
        # a test: a third launcher added later that appends CONFIRM=yes
        # somewhere else fails here instead of silently becoming a second way
        # to rent a pod.
        #
        # AST, not text search. Measured 2026-09-17, re-measured after
        # stop_phase_a landed (it sits above drain_running, so every line
        # number below it moved): `grep -n CONFIRM=yes run.py` returns NINE
        # lines, of which exactly one (run.py:283) is executable. The other
        # eight sit in FOUR docstring bodies — the AST nodes at lines 1
        # (module), 256 (start_drain), 298 (start_phase_a) and 386
        # (drain_running) — and two of them defeat the obvious shortcuts
        # directly: line 274 wraps the string in literal double quotes, so
        # searching for the quoted form finds prose, and line 402 wraps it in
        # backticks, so it is not even greppable as `"CONFIRM=yes"`. Neither
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

    def test_runs_drain_phase_a_only(self):
        argv = self._argv()
        self.assertTrue(argv[1].endswith("drain.py"), argv)
        self.assertIn("--phase-a-only", argv)

    def test_forwards_resume_and_force_local(self):
        argv = self._argv(resume=True, force_local=True)
        self.assertIn("--resume", argv)
        self.assertIn("--force-local", argv)

    def test_exit_needs_pod_reaches_phase_a_exit_unchanged(self):
        # A real child, not a mocked Popen — every other test here mocks it,
        # which is how 2026-09-18 shipped: start_phase_a ran `make drain`, and
        # GNU make exits 2 for ANY failing recipe, so drain.py's
        # EXIT_NEEDS_POD (3) arrived as 2 and the bot told the user a try-on
        # that had finished "failed (exit 2)" instead of offering the pod.
        fake_root = Path(tempfile.mkdtemp())
        (fake_root / "scripts").mkdir()
        (fake_root / "scripts" / "drain.py").write_text(
            "import sys\nsys.exit(3)\n", encoding="utf-8")
        with mock.patch.object(run_mod, "ROOT", fake_root):
            proc = run_mod.start_phase_a(self.manifest)
        proc.wait(timeout=30)
        self.assertEqual(run_mod.phase_a_exit(self.manifest), 3)

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
        # A live handle first, not an empty dict: without it this assertion
        # passes through phase_a_exit's no-handle branch (_PHASE_A.get() is
        # None -> _PHASE_A_RC.get() is None) and never reaches the "still
        # running" branch its name claims to cover.
        run_mod._PHASE_A[self.manifest.resolve()] = _FakeProc(poll_return=None)
        self.assertIsNone(run_mod.phase_a_exit(self.manifest))
        run_mod._PHASE_A[self.manifest.resolve()] = _FakeProc(poll_return=3)
        self.assertEqual(run_mod.phase_a_exit(self.manifest), 3)

    def test_the_recorded_code_survives_the_handle_being_reaped(self):
        # The record-once half, exercised by actually removing the handle
        # rather than by polling one fake twice. Task 9's tick reads the code
        # after the child is gone; if phase_a_exit only ever answered from a
        # live poll, that tick would get None and leave the chat holding a
        # progress message and no panel.
        key = self.manifest.resolve()
        run_mod._PHASE_A[key] = _FakeProc(poll_return=3)
        self.assertEqual(run_mod.phase_a_exit(self.manifest), 3)
        run_mod._PHASE_A.pop(key)
        self.assertEqual(run_mod.phase_a_exit(self.manifest), 3)

    def test_a_fresh_start_discards_the_previous_runs_exit_code(self):
        # start_phase_a's _PHASE_A_RC.pop keeps that dict from ever holding a
        # code that predates the current live handle. No tick can observe the
        # stale code today — after a re-run the live handle is back in
        # _PHASE_A, so phase_a_exit returns at its "still running" branch
        # (rc is None) and never consults _PHASE_A_RC at all. The invariant is
        # for any future DIRECT reader of the dict, which would have no such
        # protection.
        key = self.manifest.resolve()
        run_mod._PHASE_A_RC[key] = 7
        with mock.patch.object(run_mod.subprocess, "Popen") as popen:
            popen.return_value = _FakeProc(poll_return=None)
            run_mod.start_phase_a(self.manifest)
        self.assertNotIn(key, run_mod._PHASE_A_RC)

    def test_start_registers_the_handle_phase_a_running_reads(self):
        # The whole chain, with nothing installed by hand. _argv() mocks Popen
        # and throws the registration away, and every phase_a_running test
        # above puts the handle into _PHASE_A itself — so a typo in
        # start_phase_a's `key = manifest_path.resolve()` (run.py:323) would
        # otherwise pass this entire suite while every real Phase A ran
        # invisible to busy() and to the tick.
        with mock.patch.object(run_mod.subprocess, "Popen") as popen:
            popen.return_value = _FakeProc(poll_return=None)
            run_mod.start_phase_a(self.manifest)
        self.assertTrue(run_mod.phase_a_running(self.manifest))
        self.assertTrue(run_mod.busy(self.manifest))
        self.assertFalse(run_mod.drain_running(self.manifest))

    def test_a_finished_phase_a_is_no_longer_busy(self):
        run_mod._PHASE_A[self.manifest.resolve()] = _FakeProc(poll_return=0)
        self.assertFalse(run_mod.phase_a_running(self.manifest))
        self.assertFalse(run_mod.busy(self.manifest))
        # This fake answers 0 on every poll and is never removed, so what this
        # covers is a finished-but-still-registered child. The code surviving
        # an actual reap is test_the_recorded_code_survives_the_handle_being_
        # reaped above.
        self.assertEqual(run_mod.phase_a_exit(self.manifest), 0)


class _StopProc:
    """A Phase A handle stop_phase_a can act on.

    Not _FakeProc: that one only models .poll(), and stopping a child is
    terminate() -> wait() -> kill(). A real Popen starts reporting the
    negative signal number once the child is down, which is what lets
    phase_a_exit answer after a stop, so this one models that too.
    """

    def __init__(self, *, poll_return=None, wait_raises=False):
        self._poll_return = poll_return
        self._wait_raises = wait_raises
        self.terminated = 0
        self.killed = 0

    def poll(self):
        return self._poll_return

    def terminate(self):
        self.terminated += 1
        self._poll_return = -15          # SIGTERM, as a real Popen reports it

    def wait(self, timeout=None):
        if self._wait_raises:
            raise subprocess.TimeoutExpired(cmd="make drain", timeout=timeout)
        return self._poll_return

    def kill(self):
        self.killed += 1
        self._poll_return = -9           # SIGKILL


class TestStopPhaseA(unittest.TestCase):
    """stop_phase_a is the only brake on a Phase A that is already spending.

    It exists because moving Phase A out of the drain's Popen took it out of
    _RUNNING, which is the dict _do_kill terminates — after that move /kill
    gates on drain_running (False during Phase A) and answers "there is no pod
    to kill" while a 12-run batch of hosted try-on calls keeps going.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.manifest = self.tmp / "tg-1.yaml"
        self.manifest.write_text("runs: []\n", encoding="utf-8")
        self._orig_phase_a = dict(run_mod._PHASE_A)
        self._orig_phase_a_rc = dict(run_mod._PHASE_A_RC)
        self._orig_lease_path = run_mod.LEASE_PATH
        run_mod._PHASE_A.clear()
        run_mod._PHASE_A_RC.clear()
        # Redirected even though stop_phase_a never reads it: the last test
        # here calls busy(), which reaches drain_running() -> lease_for() ->
        # read_lease(LEASE_PATH). That is the real batch/pod-lease.json on any
        # machine that has run a drain, and TestDrainRunning.setUp above states
        # the rule — a test must never touch it.
        run_mod.LEASE_PATH = Path(tempfile.mkdtemp()) / "no-lease.json"

    def tearDown(self):
        run_mod._PHASE_A.clear()
        run_mod._PHASE_A.update(self._orig_phase_a)
        run_mod._PHASE_A_RC.clear()
        run_mod._PHASE_A_RC.update(self._orig_phase_a_rc)
        run_mod.LEASE_PATH = self._orig_lease_path

    def test_false_when_no_phase_a_was_started(self):
        # Nothing to stop is not an error: the caller has no way to know
        # whether the child already finished and was reaped.
        self.assertFalse(run_mod.stop_phase_a(self.manifest))

    def test_false_when_the_child_already_exited(self):
        proc = _StopProc(poll_return=0)
        run_mod._PHASE_A[self.manifest.resolve()] = proc
        self.assertFalse(run_mod.stop_phase_a(self.manifest))
        self.assertEqual(proc.terminated, 0,
                         "an already-exited child was signalled anyway")

    def test_terminates_a_live_child_and_returns_true(self):
        proc = _StopProc()
        run_mod._PHASE_A[self.manifest.resolve()] = proc
        self.assertTrue(run_mod.stop_phase_a(self.manifest))
        self.assertEqual(proc.terminated, 1)
        self.assertEqual(proc.killed, 0, "SIGKILL without waiting for SIGTERM")

    def test_escalates_to_kill_when_sigterm_is_ignored(self):
        # Same three-step shape _do_kill uses (bot.py:4169-4176): a child that
        # ignores SIGTERM must not be allowed to outlive the stop and keep
        # billing Gemini quota.
        proc = _StopProc(wait_raises=True)
        run_mod._PHASE_A[self.manifest.resolve()] = proc
        self.assertTrue(run_mod.stop_phase_a(self.manifest))
        self.assertEqual(proc.terminated, 1)
        self.assertEqual(proc.killed, 1)

    def test_leaves_the_handle_so_the_exit_code_is_still_reportable(self):
        # Deliberately NOT popped, and NOT written into _PHASE_A_RC either:
        # phase_a_exit is the single writer of that dict, and it records the
        # negative signal code on its next poll. That code is what Task 9's
        # failure branch shows the user, so popping here would strand the chat.
        key = self.manifest.resolve()
        proc = _StopProc()
        run_mod._PHASE_A[key] = proc
        self.assertTrue(run_mod.stop_phase_a(self.manifest))
        self.assertIn(key, run_mod._PHASE_A)
        self.assertNotIn(key, run_mod._PHASE_A_RC)
        self.assertEqual(run_mod.phase_a_exit(self.manifest), -15)
        # And the stopped child no longer holds the manifest, so the
        # file-safety guards release and the user can /clear straight away.
        self.assertFalse(run_mod.phase_a_running(self.manifest))
        self.assertFalse(run_mod.busy(self.manifest))


class TestEstimateMinutes(unittest.TestCase):
    def test_sums_the_measured_medians_for_the_pipeline(self):
        # docs/batch-runner.md section 7, batch 2026-08-18-2105: tryon 351s,
        # motion 247s, enhance 114s = 712s -> 12 min.
        job = Job(slots={}, probes={}, pipeline="tryon-motion-enhance")
        self.assertEqual(estimate_minutes(job), 12)

    def test_a_stage_with_no_measurement_falls_back_to_its_timeout_ceiling(self):
        # character-swap has no measured median as of 2026-08-31, so the
        # estimate uses STAGES[...].timeout_min — deliberately the pessimistic
        # number rather than a made-up measurement.
        job = Job(slots={}, probes={}, pipeline="character-swap-enhance")
        expected = round((STAGES["character-swap"].timeout_min * 60 + 114) / 60)
        self.assertEqual(estimate_minutes(job), expected)


class TestDelivery(unittest.TestCase):
    def test_final_files_lists_only_the_final_directory(self):
        root = Path(tempfile.mkdtemp())
        batch = root / "out" / "2026-08-31-2140"
        (batch / "_final").mkdir(parents=True)
        (batch / "runs" / "job").mkdir(parents=True)
        (batch / "_final" / "job.mp4").write_bytes(b"x" * 200_000)
        (batch / "runs" / "job" / "02-motion.mp4").write_bytes(b"y" * 200_000)
        found = final_files(batch)
        self.assertEqual([p.name for p in found], ["job.mp4"])

    def test_summary_names_the_failed_run_and_its_local_log(self):
        # teardown already pulled the pod logs down before destroying the pod,
        # so the bot attaches what is on disk and never reaches for the pod.
        root = Path(tempfile.mkdtemp())
        batch = root / "out" / "b"
        (batch / "runs" / "job").mkdir(parents=True)
        (batch / "runs" / "job" / "pod-job.log").write_text("boom", encoding="utf-8")
        text = summary_text(batch)
        self.assertIn("job", text)

class TestProgressBar(unittest.TestCase):
    """The bar, added 2026-08-31 with the auto-updating progress message."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.manifest = self.root / "m.yaml"
        self.manifest.write_text("runs: []\n", encoding="utf-8")

    def _state(self, payload):
        state_path_for(self.manifest).write_text(json.dumps(payload),
                                                encoding="utf-8")

    def test_the_bar_counts_against_the_planned_stages_not_the_seen_ones(self):
        """Why `stages` is a parameter at all.

        The journal records only stages that have already begun, so a bar
        computed from it alone would read 1/1 while the first of three ran, and
        never move. The denominator has to come from the pipeline.
        """
        self._state({"batch": "b", "runs": {"job": {"status": "running", "stages": {
            "tryon": {"status": "done", "sec": 351},
            "motion": {"status": "running"}}}}})
        text = progress_text(self.manifest, lease=None,
                             stages=["tryon", "motion", "enhance"])
        self.assertIn("1/3", text)
        self.assertIn("▰▱▱", text)
        # The running stage is named beside the bar. Since 2026-09-01 a spinner
        # stood where the word "running" used to; since 2026-09-04 that is an
        # animated custom emoji instead (see run._ICON_LOADING_CE), but the
        # assertion's point is unchanged — the name plus a moving icon, not
        # the old phrasing.
        self.assertIn("motion", text)
        self.assertIn(run_mod._ICON_LOADING_CE, text)
        # A stage not started yet is listed, not omitted — otherwise the user
        # cannot see what is still to come.
        self.assertIn("enhance", text)

    def test_each_run_shows_only_its_own_pipelines_stages(self):
        """Regression: a batch mixing pipelines rendered EVERY job with the
        union of all jobs' stages (2026-09-12, reported live) — a
        character-swap-enhance run showed unchecked camera-tryon/camera-motion
        boxes it would never run, and a tryon-camera-motion-enhance run showed
        an unchecked character-swap box it would never run. The per-run
        checklist must come from that run's OWN pipeline in the manifest, not
        the flat `stages` list (which stays only a fallback denominator)."""
        self.manifest.write_text(
            "runs:\n"
            "  - id: job-swap\n"
            "    pipeline: character-swap-enhance\n"
            "    inputs: { character: c.jpg, driver: d.mp4 }\n"
            "  - id: job-camera\n"
            "    pipeline: tryon-camera-motion-enhance\n"
            "    inputs: { character: c.jpg, outfit: o.jpg, background: b.jpg, driver: d.mp4 }\n"
            "    camera-motion: { preset: drv-5s }\n",
            encoding="utf-8")
        self._state({"batch": "b", "runs": {
            "job-swap": {"status": "running", "stages": {
                "character-swap": {"status": "done"},
                "enhance": {"status": "running"}}},
            "job-camera": {"status": "running", "stages": {
                "camera-tryon": {"status": "done"},
                "camera-motion": {"status": "running"}}},
        }})
        text = progress_text(self.manifest, lease=None,
                             stages=["character-swap", "enhance",
                                     "camera-tryon", "camera-motion"])
        # Each run's bar must read against ITS OWN pipeline length (2 and 3
        # stages respectively), not the 4-stage batch-wide union both would
        # have shown as "1/4" under the bug.
        self.assertIn("1/2", text)
        self.assertIn("1/3", text)
        self.assertNotIn("1/4", text)
        # Split the message into its two per-run blocks at each bar line
        # ("▰..." / "N/M") — runs are rendered sorted by id, so job-camera's
        # block ("c" < "s") comes first.
        lines = text.splitlines()
        bar_idxs = [i for i, l in enumerate(lines) if l.startswith("▰")]
        self.assertEqual(len(bar_idxs), 2, "expected one bar per run")
        camera_block = "\n".join(lines[bar_idxs[0]:bar_idxs[1]])
        swap_block = "\n".join(lines[bar_idxs[1]:])
        # job-camera's checklist must not list the other job's character-swap.
        self.assertIn("camera-tryon", camera_block)
        self.assertIn("camera-motion", camera_block)
        self.assertNotIn("character-swap", camera_block)
        # job-swap's checklist must not list the other job's camera stages.
        self.assertIn("character-swap", swap_block)
        self.assertIn("enhance", swap_block)
        self.assertNotIn("camera-tryon", swap_block)
        self.assertNotIn("camera-motion", swap_block)

    def test_elapsed_is_the_only_thing_that_changes_between_two_real_ticks(self):
        """The line between animation and lying, redrawn 2026-09-04.

        The animated custom emoji icons (run._ICON_LOADING_CE etc.) move on
        their own, client-side — they need no edit to do that, and staying
        static between two `progress_text` calls is correct, not a bug. What
        has to change is `run._elapsed()`: a real, still-ticking mm:ss, the
        one thing here that still says "this process is alive" the way the
        old hand-cycled frame used to, since a drain that died mid-stage
        leaves exactly the same `running` record as one still working. Strip
        the elapsed figure and two calls two real seconds apart must be
        byte-identical.
        """
        self._state({"batch": "b", "runs": {"job": {"status": "running", "stages": {
            "tryon": {"status": "done", "sec": 351},
            "motion": {"status": "running"}}}}})
        stages = ["tryon", "motion", "enhance"]
        now = time.time()
        lease = Lease(pod_id="p1", provisioned_at=now - 10.0,
                      manifest=str(self.manifest), abs_max_min=240)
        with mock.patch("time.time", return_value=now):
            a = progress_text(self.manifest, lease=lease, stages=stages)
        with mock.patch("time.time", return_value=now + 2.0):
            b = progress_text(self.manifest, lease=lease, stages=stages)
        self.assertNotEqual(a, b, "the message does not animate at all")
        self.assertEqual(a.replace("0m10s", ""), b.replace("0m12s", ""),
                         "something other than the elapsed figure changed")

    def test_the_waiting_for_the_pod_line_shows_elapsed_time(self):
        """The longest silent stretch of the whole render.

        Provision + bootstrap is ~10 minutes in which the journal says nothing
        whatsoever — the one phase where the only real question is whether it
        is alive, which `run._elapsed()` (not a hand-cycled frame, since
        2026-09-04) answers.
        """
        self._state({"batch": "b", "runs": {}})
        lease = Lease(pod_id="p1", provisioned_at=time.time() - 90.0,
                      manifest=str(self.manifest), abs_max_min=240)
        text = progress_text(self.manifest, lease=lease, stages=["tryon"])
        self.assertIn("waiting for the pod", text)
        self.assertIn("1m30s", text)

    def test_the_waiting_for_the_pod_line_survives_no_lease_yet(self):
        """No lease on disk yet must not raise — it means "not provisioned",
        not "unreadable"; run._elapsed returns "" for exactly this case."""
        self._state({"batch": "b", "runs": {}})
        text = progress_text(self.manifest, lease=None, stages=["tryon"])
        self.assertIn("waiting for the pod", text)

    def test_a_failed_stage_is_marked_and_the_run_is_called_failed(self):
        self._state({"batch": "b", "runs": {"job": {"status": "error", "stages": {
            "tryon": {"status": "done", "sec": 351},
            "motion": {"status": "error"}}}}})
        text = progress_text(self.manifest, lease=None,
                            stages=["tryon", "motion", "enhance"])
        self.assertIn("❌", text)
        self.assertIn("this run failed", text)

    def test_before_the_pod_reports_anything_it_says_so(self):
        self._state({"batch": "b", "runs": {}})
        self.assertIn("waiting for the pod",
                      progress_text(self.manifest, lease=None, stages=["tryon"]))

    def test_the_lease_line_reports_money_already_spent(self):
        # Elapsed, not predicted: the pod bills from provisioned_at whether or
        # not a stage is moving, so this is the number that costs money.
        self._state({"batch": "b", "runs": {}})
        lease = Lease(pod_id="p1", provisioned_at=time.time() - 3600,
                      manifest=str(self.manifest), abs_max_min=240)
        text = progress_text(self.manifest, lease=lease, stages=["tryon"])
        self.assertIn("60m00s on the pod", text)
        self.assertIn("$0.99", text)


if __name__ == "__main__":
    unittest.main()
