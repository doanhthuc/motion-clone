import json, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.manifest import load_manifest, state_path_for
import drain
from drain import abs_max_min, collect_diagnostics, failed_job_ids, teardown

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
