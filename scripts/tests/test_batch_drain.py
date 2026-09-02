import json, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.manifest import load_manifest, state_path_for
from batchlib_ext.handoff import Handoff, handoff_path, mailbox_path
from batchlib_ext.lease import Lease
import drain
from drain import (abs_max_min, chain_or_teardown, collect_diagnostics,
                   failed_job_ids, pod_max_hours, teardown)

YAML = """
runs:
  - id: a
    pipeline: motion-enhance
    inputs: {character: /tmp/c.png, driver: /tmp/d.mp4}
  - id: b
    pipeline: character-swap-enhance
    inputs: {character: /tmp/c.png, driver: /tmp/d.mp4}
"""


class TestAbsMax(unittest.TestCase):
    def test_sums_stage_timeouts_plus_30(self):
        # motion 60 + enhance 90 + character-swap 60 + enhance 90 = 300, +30
        path = Path(tempfile.mkdtemp()) / "m.yaml"
        path.write_text(YAML, encoding="utf-8")
        self.assertEqual(abs_max_min(load_manifest(path)), 330)


class TestPodMaxHours(unittest.TestCase):
    """The RunPod-side --stop-after net may only be tightened, never loosened."""

    def test_a_long_manifest_is_capped_at_the_configured_default(self):
        # batch/2026-08-28-lanczos-6cap.yaml: ceiling 1030 min = 18h uncapped.
        # Measured 2026-08-31: 4 of the 7 manifests in batch/ exceed 8 hours.
        self.assertEqual(pod_max_hours(1030, "8"), "8")

    def test_a_short_manifest_tightens_below_the_default(self):
        # batch/2026-08-24-relight-sweep.yaml: ceiling 150 min -> 3h.
        self.assertEqual(pod_max_hours(150, "8"), "3")

    def test_rounds_up_never_down(self):
        # 330 min is 5.5h; granting 5 would stop the pod mid-batch.
        self.assertEqual(pod_max_hours(330, "8"), "6")

    def test_never_returns_zero_hours_for_a_tiny_manifest(self):
        # A 30-minute ceiling must not become --stop-after 0 hours.
        self.assertEqual(pod_max_hours(30, "8"), "1")

    def test_zero_disables_the_net_and_stays_zero(self):
        # pod-provision.sh:70 documents POD_MAX_HOURS=0 as "no net". Substituting
        # a number would re-enable a net the operator deliberately switched off.
        self.assertEqual(pod_max_hours(1030, "0"), "0")

    def test_unset_falls_back_to_the_documented_default(self):
        # env_get returns "" for a missing key AND for an unreadable .env
        # (config.py:62-66); pod-provision.sh:28 defaults that to 8.
        self.assertEqual(pod_max_hours(1030, ""), "8")

    def test_garbage_is_handed_through_for_pod_provision_to_reject(self):
        # pod-provision.sh:72 dies by name on a non-numeric value. Guessing here
        # would hide a typo in .env behind a rent that looks fine.
        self.assertEqual(pod_max_hours(1030, "eight"), "eight")


class TestProvision(unittest.TestCase):
    def test_empty_pod_id_raises_instead_of_writing_a_useless_lease(self):
        # env_get returns "" on ANY failure to read .env (config.py:62-66). A
        # lease with an empty pod_id makes every later kill `runpodctl pod delete
        # ""`, which raises — so the watchdog could never clean up the pod that
        # was just rented.
        with mock.patch.object(drain.subprocess, "run") as mock_run, \
             mock.patch.object(drain, "env_get", return_value=""):
            mock_run.return_value = mock.Mock(returncode=0)
            with self.assertRaises(RuntimeError) as cm:
                drain.provision(ceiling_min=120)
        self.assertIn("GPU_INSTANCE_ID is empty", str(cm.exception))

    def test_returns_the_pod_id_provisioning_wrote_to_env(self):
        with mock.patch.object(drain.subprocess, "run") as mock_run, \
             mock.patch.object(drain, "env_get", side_effect=["8", "pod-xyz"]):
            mock_run.return_value = mock.Mock(returncode=0)
            self.assertEqual(drain.provision(ceiling_min=120), "pod-xyz")
        # provision() must NOT wait or bootstrap: main() writes the lease between
        # the two, because the pod bills from the moment provisioning returns.
        self.assertEqual(mock_run.call_count, 1)


class TestFailedJobIds(unittest.TestCase):
    def test_collects_job_ids_from_error_stages(self):
        state = {"runs": {
            "r1": {"status": "error", "stages": {
                "motion": {"status": "done", "job_id": "j1"},
                "enhance": {"status": "error", "job_id": "j2"}}},
            "r2": {"status": "done", "stages": {
                "motion": {"status": "done", "job_id": "j3"}}},
        }}
        self.assertEqual(failed_job_ids(state), [("r1", "j2")])

    def test_error_stage_without_a_job_id_is_skipped(self):
        # A run can fail before a job was ever submitted — there is nothing
        # to fetch logs for, and inventing an id would 404 noisily.
        state = {"runs": {"r1": {"status": "error",
                                 "stages": {"motion": {"status": "error"}}}}}
        self.assertEqual(failed_job_ids(state), [])


def _write_manifest_and_state(tmpdir: Path, state: dict) -> Path:
    """A manifest_path plus a `.state.json` beside it, named via state_path_for
    itself — so tests exercise the real path derivation instead of mocking it away."""
    manifest_path = Path(tmpdir) / "m.yaml"
    manifest_path.write_text("runs: []\n", encoding="utf-8")
    state_path_for(manifest_path).write_text(json.dumps(state), encoding="utf-8")
    return manifest_path


FAILED_STATE = {
    "batch": "b1",
    "runs": {"r1": {"status": "error", "stages": {
        "motion": {"status": "error", "job_id": "j1"}}}},
}

DONE_STATE = {
    "batch": "b1",
    "runs": {"r1": {"status": "done", "stages": {
        "motion": {"status": "done", "job_id": "j1"}}}},
}


class TestTeardown(unittest.TestCase):
    def test_teardown_destroys_even_when_diagnostics_raise(self):
        # Regression test for: mkdir living outside collect_diagnostics's try
        # let a disk-full/permission error escape teardown() entirely and
        # skip `make gpu-destroy` — leaving a $0.99/hour pod running.
        tmpdir = tempfile.mkdtemp()
        manifest_path = _write_manifest_and_state(tmpdir, FAILED_STATE)
        with mock.patch.object(drain, "load_settings", return_value=object()), \
             mock.patch.object(drain, "collect_diagnostics",
                               side_effect=OSError("disk full")), \
             mock.patch.object(drain, "sh") as mock_sh, \
             mock.patch.object(drain, "clear_lease") as mock_clear:
            teardown(manifest_path)
        mock_sh.assert_called_once_with("make", "gpu-destroy")
        mock_clear.assert_called_once()

    def test_teardown_collects_before_destroying(self):
        tmpdir = tempfile.mkdtemp()
        manifest_path = _write_manifest_and_state(tmpdir, FAILED_STATE)
        calls = []
        with mock.patch.object(drain, "load_settings", return_value=object()), \
             mock.patch.object(drain, "collect_diagnostics",
                               side_effect=lambda *a, **k: calls.append("diagnostics")), \
             mock.patch.object(drain, "sh",
                               side_effect=lambda *a: calls.append("destroy")), \
             mock.patch.object(drain, "clear_lease"):
            teardown(manifest_path)
        self.assertEqual(calls, ["diagnostics", "destroy"])

    def test_teardown_skips_diagnostics_when_nothing_failed(self):
        tmpdir = tempfile.mkdtemp()
        manifest_path = _write_manifest_and_state(tmpdir, DONE_STATE)
        with mock.patch.object(drain, "load_settings", return_value=object()), \
             mock.patch.object(drain, "collect_diagnostics") as mock_collect, \
             mock.patch.object(drain, "sh") as mock_sh, \
             mock.patch.object(drain, "clear_lease"):
            teardown(manifest_path)
        mock_collect.assert_not_called()
        mock_sh.assert_called_once_with("make", "gpu-destroy")


class TestCollectDiagnostics(unittest.TestCase):
    def test_collect_diagnostics_survives_one_failing_fetch(self):
        # One bad job must not abort collection for the others.
        state = {"runs": {
            "r1": {"status": "error", "stages": {
                "motion": {"status": "error", "job_id": "jobA"}}},
            "r2": {"status": "error", "stages": {
                "motion": {"status": "error", "job_id": "jobB"}}},
        }}
        out_dir = Path(tempfile.mkdtemp())

        def fake_request(settings, path, **kwargs):
            if "jobA" in path:
                raise OSError("network blip")
            return 200, b"ok"

        with mock.patch("batchlib.client._request", side_effect=fake_request), \
             mock.patch.object(drain.subprocess, "run") as mock_run:
            mock_run.return_value = mock.Mock(stdout="", stderr="")
            collect_diagnostics(object(), state, out_dir)

        a_log = (out_dir / "runs" / "r1" / "pod-job.log").read_text(encoding="utf-8")
        b_log = (out_dir / "runs" / "r2" / "pod-job.log").read_bytes()
        self.assertIn("could not fetch job logs", a_log)
        self.assertEqual(b_log, b"ok")


NEXT_YAML = """
runs:
  - id: a
    pipeline: motion-enhance
    inputs: {character: /tmp/c.png, driver: /tmp/d.mp4}
"""


class TestChainOrTeardown(unittest.TestCase):
    """Checked exactly once per link, at the spot teardown() used to be
    called unconditionally — no polling, no arbitrary grace period."""

    def _original(self, tmpdir: Path) -> Path:
        path = Path(tmpdir) / "tg-1.yaml"
        path.write_text(YAML, encoding="utf-8")
        return path

    def test_nothing_queued_destroys_immediately_like_before(self):
        tmpdir = tempfile.mkdtemp()
        original = self._original(tmpdir)
        with mock.patch.object(drain, "claim_mailbox", return_value=None) as mock_claim, \
             mock.patch.object(drain, "batch_run") as mock_run, \
             mock.patch.object(drain, "teardown") as mock_teardown:
            chain_or_teardown(original)
        mock_claim.assert_called_once_with(original)
        mock_run.assert_not_called()
        mock_teardown.assert_called_once_with(original)

    def test_a_queued_job_runs_on_the_same_pod_without_a_teardown_in_between(self):
        tmpdir = tempfile.mkdtemp()
        original = self._original(tmpdir)
        nxt = Path(tmpdir) / "tg-1-999.yaml"
        nxt.write_text(NEXT_YAML, encoding="utf-8")
        with mock.patch.object(drain, "claim_mailbox", side_effect=[nxt, None]), \
             mock.patch.object(drain, "batch_run", return_value=0) as mock_run, \
             mock.patch.object(drain, "read_lease", return_value=None), \
             mock.patch.object(drain, "teardown") as mock_teardown:
            chain_or_teardown(original)
        mock_run.assert_called_once_with("--file", str(nxt))
        # Only ever destroyed once the SECOND claim finds nothing — the
        # picked-up job itself never triggers its own teardown call.
        mock_teardown.assert_called_once_with(nxt)

    def test_success_reports_a_running_handoff_and_extends_the_lease_total(self):
        tmpdir = tempfile.mkdtemp()
        original = self._original(tmpdir)
        nxt = Path(tmpdir) / "tg-1-999.yaml"
        nxt.write_text(NEXT_YAML, encoding="utf-8")
        old_lease = Lease(pod_id="pod-1", provisioned_at=1000.0,
                          manifest=str(original.resolve()), abs_max_min=330)
        with mock.patch.object(drain, "claim_mailbox", side_effect=[nxt, None]), \
             mock.patch.object(drain, "batch_run", return_value=0), \
             mock.patch.object(drain, "read_lease", return_value=old_lease), \
             mock.patch.object(drain, "write_lease") as mock_write_lease, \
             mock.patch.object(drain, "teardown"):
            chain_or_teardown(original)
        new_lease = mock_write_lease.call_args[0][1]
        # provisioned_at untouched (tier 2 bounds TOTAL lifetime, not per link)
        # and the ceiling grows by the next link's own — never resets.
        self.assertEqual(new_lease.pod_id, "pod-1")
        self.assertEqual(new_lease.provisioned_at, 1000.0)
        self.assertEqual(new_lease.manifest, str(nxt.resolve()))
        self.assertEqual(new_lease.abs_max_min, 330 + abs_max_min(load_manifest(nxt)))
        handoff = json.loads(handoff_path(original).read_text(encoding="utf-8"))
        self.assertEqual(handoff["status"], "running")
        self.assertEqual(handoff["manifest"], str(nxt))

    def test_no_lease_on_disk_does_not_crash_the_handoff(self):
        # A missing lease is possible if something else already cleared it —
        # the handoff must not depend on it existing to report success.
        tmpdir = tempfile.mkdtemp()
        original = self._original(tmpdir)
        nxt = Path(tmpdir) / "tg-1-999.yaml"
        nxt.write_text(NEXT_YAML, encoding="utf-8")
        with mock.patch.object(drain, "claim_mailbox", side_effect=[nxt, None]), \
             mock.patch.object(drain, "batch_run", return_value=0), \
             mock.patch.object(drain, "read_lease", return_value=None), \
             mock.patch.object(drain, "write_lease") as mock_write_lease, \
             mock.patch.object(drain, "teardown"):
            chain_or_teardown(original)
        mock_write_lease.assert_not_called()

    def test_batch_run_failure_is_reported_and_the_pod_still_destroyed(self):
        tmpdir = tempfile.mkdtemp()
        original = self._original(tmpdir)
        nxt = Path(tmpdir) / "tg-1-999.yaml"
        nxt.write_text(NEXT_YAML, encoding="utf-8")
        with mock.patch.object(drain, "claim_mailbox", return_value=nxt), \
             mock.patch.object(drain, "batch_run", return_value=1), \
             mock.patch.object(drain, "teardown") as mock_teardown:
            chain_or_teardown(original)
        handoff = json.loads(handoff_path(original).read_text(encoding="utf-8"))
        self.assertEqual(handoff["status"], "failed")
        self.assertIn("1", handoff["reason"])
        # Destroyed for `original` — the job that had actually finished —
        # not for the one that failed to pick up.
        mock_teardown.assert_called_once_with(original)

    def test_batch_run_raising_is_reported_rather_than_crashing_the_drain(self):
        tmpdir = tempfile.mkdtemp()
        original = self._original(tmpdir)
        nxt = Path(tmpdir) / "tg-1-999.yaml"
        nxt.write_text(NEXT_YAML, encoding="utf-8")
        with mock.patch.object(drain, "claim_mailbox", return_value=nxt), \
             mock.patch.object(drain, "batch_run", side_effect=OSError("no such file")), \
             mock.patch.object(drain, "teardown") as mock_teardown:
            chain_or_teardown(original)
        handoff = json.loads(handoff_path(original).read_text(encoding="utf-8"))
        self.assertEqual(handoff["status"], "failed")
        self.assertIn("no such file", handoff["reason"])
        mock_teardown.assert_called_once_with(original)

    def test_a_broken_next_manifest_is_reported_without_calling_batch_run(self):
        tmpdir = tempfile.mkdtemp()
        original = self._original(tmpdir)
        nxt = Path(tmpdir) / "tg-1-999.yaml"
        nxt.write_text("not: [valid, yaml, :::", encoding="utf-8")
        with mock.patch.object(drain, "claim_mailbox", return_value=nxt), \
             mock.patch.object(drain, "batch_run") as mock_run, \
             mock.patch.object(drain, "teardown") as mock_teardown:
            chain_or_teardown(original)
        mock_run.assert_not_called()
        handoff = json.loads(handoff_path(original).read_text(encoding="utf-8"))
        self.assertEqual(handoff["status"], "failed")
        mock_teardown.assert_called_once_with(original)

    def test_a_second_link_that_fails_destroys_for_the_first_not_the_original(self):
        # Chain of two successful hops, then a third link fails to run — the
        # pod was doing link 2's work, so teardown must name link 2.
        tmpdir = tempfile.mkdtemp()
        original = self._original(tmpdir)
        link2 = Path(tmpdir) / "tg-1-111.yaml"
        link2.write_text(NEXT_YAML, encoding="utf-8")
        link3 = Path(tmpdir) / "tg-1-222.yaml"
        link3.write_text(NEXT_YAML, encoding="utf-8")
        with mock.patch.object(drain, "claim_mailbox", side_effect=[link2, link3]), \
             mock.patch.object(drain, "batch_run", side_effect=[0, 1]), \
             mock.patch.object(drain, "read_lease", return_value=None), \
             mock.patch.object(drain, "teardown") as mock_teardown:
            chain_or_teardown(original)
        mock_teardown.assert_called_once_with(link2)


if __name__ == "__main__":
    unittest.main()
