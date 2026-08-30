import json, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.manifest import load_manifest, state_path_for
import drain
from drain import (abs_max_min, collect_diagnostics, failed_job_ids,
                   pod_max_hours, teardown)

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


if __name__ == "__main__":
    unittest.main()
