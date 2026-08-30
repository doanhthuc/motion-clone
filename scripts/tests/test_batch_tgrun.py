# scripts/tests/test_batch_tgrun.py
import sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.manifest import state_path_for
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


from tgbot.run import final_files, summary_text


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


if __name__ == "__main__":
    unittest.main()
