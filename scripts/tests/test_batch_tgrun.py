# scripts/tests/test_batch_tgrun.py
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

    def test_names_every_stage_and_its_status(self):
        text = progress_text(self.manifest, lease=None)
        self.assertIn("motion", text)
        self.assertIn("enhance", text)

    def test_reads_the_journal_so_it_still_works_after_the_pod_is_gone(self):
        # lease=None means no pod. The journal is the source of truth, exactly
        # as batch_status uses it, so progress stays reportable after destroy.
        text = progress_text(self.manifest, lease=None)
        self.assertIn("2026-08-31-2140", text)

    def test_no_state_file_is_reported_not_crashed(self):
        empty = Path(tempfile.mkdtemp()) / "none.yaml"
        empty.write_text("runs: []", encoding="utf-8")
        self.assertIsInstance(progress_text(empty, lease=None), str)


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
        self.assertIn("motion running", text)
        # A stage not started yet is listed, not omitted — otherwise the user
        # cannot see what is still to come.
        self.assertIn("enhance", text)

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
        from batchlib_ext.lease import Lease
        self._state({"batch": "b", "runs": {}})
        lease = Lease(pod_id="p1", provisioned_at=time.time() - 3600,
                      manifest=str(self.manifest), abs_max_min=240)
        text = progress_text(self.manifest, lease=lease, stages=["tryon"])
        self.assertIn("60 min on the pod", text)
        self.assertIn("$0.99", text)


if __name__ == "__main__":
    unittest.main()
