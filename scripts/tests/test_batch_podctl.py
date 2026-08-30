import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib_ext.lease import Lease
from batchlib_ext.podctl import PodInfo, RunpodCtl
from batchlib_ext.watchdog import reconcile

MIN = 60.0
LEASE = Lease(pod_id="mine", provisioned_at=0.0, manifest="batch/x.yaml", abs_max_min=240)


class TestReconcile(unittest.TestCase):
    def test_leased_pod_is_left_alone(self):
        kill, _ = reconcile(pods=[PodInfo("mine", "motion-transfer")], lease=LEASE,
                            first_seen={}, now=100 * MIN)
        self.assertEqual(kill, [])

    def test_orphan_inside_grace_is_left_alone(self):
        # Covers the gap between `runpodctl pod create` returning and the lease
        # being written: the pod is real and legitimate, just not recorded yet.
        pods = [PodInfo("stray", "motion-transfer")]
        kill, seen = reconcile(pods=pods, lease=None, first_seen={}, now=0.0)
        self.assertEqual(kill, [])
        self.assertEqual(seen, {"stray": 0.0})

    def test_orphan_past_grace_is_destroyed(self):
        pods = [PodInfo("stray", "motion-transfer")]
        kill, _ = reconcile(pods=pods, lease=None,
                            first_seen={"stray": 0.0}, now=11 * MIN)
        self.assertEqual(kill, ["stray"])

    def test_pod_that_is_not_the_leased_one_is_an_orphan(self):
        # Two machines both believing they own a pod is the failure mode this
        # tier exists for. A lease for "mine" does not protect "other".
        kill, _ = reconcile(pods=[PodInfo("other", "motion-transfer")], lease=LEASE,
                            first_seen={"other": 0.0}, now=11 * MIN)
        self.assertEqual(kill, ["other"])

    def test_vanished_pod_is_forgotten(self):
        _, seen = reconcile(pods=[], lease=None,
                            first_seen={"gone": 0.0}, now=11 * MIN)
        self.assertEqual(seen, {})

    def test_unmanaged_pod_is_never_destroyed(self):
        # Covers the EU-CZ-1 failover runbook: two temporary CPU pods are
        # stood up by hand for 15-25 minutes, and the watchdog daemon runs
        # the whole time. Tier 3 only destroys pods it was meant to provision.
        pods = [PodInfo("temp-cpu-a", "cpu-failover-temp")]
        kill, _ = reconcile(pods=pods, lease=None,
                            first_seen={"temp-cpu-a": 0.0}, now=60 * MIN)
        self.assertEqual(kill, [])

    def test_unmanaged_pod_is_still_tracked_in_seen(self):
        # Authority scoping limits destruction, not observation. Unmanaged pods
        # must still be tracked in seen, so they are included in the next tick's
        # state without resetting their birth time.
        pods = [PodInfo("temp-cpu-a", "cpu-failover-temp")]
        _, seen = reconcile(pods=pods, lease=None,
                            first_seen={"temp-cpu-a": 0.0}, now=60 * MIN)
        self.assertEqual(seen, {"temp-cpu-a": 0.0})


class TestRunpodCtlListPods(unittest.TestCase):
    """Test error handling in RunpodCtl.list_pods()."""

    @patch("subprocess.run")
    def test_uses_pod_list_not_the_deprecated_get_pod(self, mock_run):
        # C1. `runpodctl get pod -o json` is deprecated in runpodctl 2.8 and
        # IGNORES -o: it prints a tab-separated table, so json.loads raised on
        # every call and tier 3 — the outermost net — never executed once.
        # Verified 2026-08-31 against the real CLI: `runpodctl pod list -o json`
        # with no pods rented prints `[]`.
        mock_run.return_value = MagicMock(returncode=0, stdout="[]", stderr="")
        RunpodCtl().list_pods()
        self.assertEqual(mock_run.call_args[0][0],
                         ["runpodctl", "pod", "list", "-o", "json"])

    @patch("time.sleep")
    @patch("subprocess.run")
    def test_destroy_uses_pod_delete_matching_the_makefile(self, mock_run, _sleep):
        # Makefile:159 uses `runpodctl pod delete <id>`, so this matches it.
        # `runpodctl remove pod` is a live deprecated alias, not a dead command
        # (measured 2026-08-31: `runpodctl remove pod --help` exits 0), so this
        # pins a spelling choice, not a bug fix.
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        RunpodCtl().destroy("pod-1")
        self.assertEqual(mock_run.call_args[0][0],
                         ["runpodctl", "pod", "delete", "pod-1"])

    @patch("time.sleep")
    @patch("subprocess.run")
    def test_destroy_raises_on_non_zero_exit(self, mock_run, _sleep):
        # Raising keeps the caller's lease, so the next tick retries.
        mock_run.return_value = MagicMock(returncode=1, stdout="",
                                          stderr="pod not found")
        with self.assertRaises(RuntimeError) as cm:
            RunpodCtl().destroy("pod-1")
        self.assertIn("pod not found", str(cm.exception))

    @patch("subprocess.run")
    def test_successful_list(self, mock_run):
        """Successfully parse valid JSON output."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps([
                {"id": "pod-1", "name": "motion-transfer"},
                {"id": "pod-2", "name": "cpu-box"}
            ]),
            stderr=""
        )
        pods = RunpodCtl().list_pods()
        self.assertEqual(len(pods), 2)
        self.assertEqual(pods[0].pod_id, "pod-1")
        self.assertEqual(pods[0].name, "motion-transfer")

    @patch("subprocess.run")
    def test_empty_list(self, mock_run):
        """Handle empty JSON array."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="[]",
            stderr=""
        )
        pods = RunpodCtl().list_pods()
        self.assertEqual(pods, [])

    @patch("subprocess.run")
    def test_empty_stdout(self, mock_run):
        """Handle empty stdout (defaults to [])."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
            stderr=""
        )
        pods = RunpodCtl().list_pods()
        self.assertEqual(pods, [])

    @patch("subprocess.run")
    def test_nonzero_exit_raises_runtime_error(self, mock_run):
        """Non-zero exit raises RuntimeError with stderr."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="API key not configured"
        )
        with self.assertRaises(RuntimeError) as cm:
            RunpodCtl().list_pods()
        self.assertIn("API key not configured", str(cm.exception))

    @patch("subprocess.run")
    def test_malformed_json_raises_runtime_error(self, mock_run):
        """Malformed JSON output (even with returncode 0) raises RuntimeError."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="ID\tNAME\tGPU\tIMAGE NAME\tSTATUS",  # table format, not JSON
            stderr=""
        )
        with self.assertRaises(RuntimeError) as cm:
            RunpodCtl().list_pods()
        self.assertIn("invalid JSON", str(cm.exception))
        self.assertIn("ID", str(cm.exception))  # snippet in output

    @patch("subprocess.run")
    def test_missing_required_field_raises_runtime_error(self, mock_run):
        """Missing 'id' field in JSON raises RuntimeError."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps([
                {"name": "pod-without-id"}
            ]),
            stderr=""
        )
        with self.assertRaises(RuntimeError) as cm:
            RunpodCtl().list_pods()
        self.assertIn("invalid JSON", str(cm.exception))

    @patch("subprocess.run")
    def test_pod_without_name_gets_empty_string(self, mock_run):
        """Pod without 'name' field gets empty string (not required)."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps([
                {"id": "pod-1"}
            ]),
            stderr=""
        )
        pods = RunpodCtl().list_pods()
        self.assertEqual(len(pods), 1)
        self.assertEqual(pods[0].pod_id, "pod-1")
        self.assertEqual(pods[0].name, "")


if __name__ == "__main__":
    unittest.main()
