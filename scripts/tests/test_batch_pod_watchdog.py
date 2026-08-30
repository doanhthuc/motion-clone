import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib_ext.lease import Lease, write_lease
from batchlib_ext.podctl import PodInfo
from batchlib_ext.watchdog import DESTROYABLE_NAMES
import pod_watchdog


class FakePods:
    """Mock pod API for testing."""
    def __init__(self):
        self.destroyed = []
        self._list_error = None

    def list_pods(self) -> list[PodInfo]:
        if self._list_error:
            raise RuntimeError(self._list_error)
        return self.pods if hasattr(self, "pods") else []

    def destroy(self, pod_id: str) -> None:
        self.destroyed.append(pod_id)


class TestPodWatchdog(unittest.TestCase):
    """Test the four safety properties of the watchdog daemon."""

    def setUp(self):
        """Set up temporary lease file for each test."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.lease_path = self.temp_path / "pod-lease.json"
        self.patcher = patch.object(pod_watchdog, "LEASE_PATH", self.lease_path)
        self.patcher.start()

    def tearDown(self):
        """Clean up."""
        self.patcher.stop()
        self.temp_dir.cleanup()

    def test_dry_run_never_destroys(self):
        """Property 1: dry_run=True prevents destroy() calls and does not clear the lease."""
        # Set up a lease far in the past with a small ceiling, so tier 2 fires
        lease = Lease(
            pod_id="pod-old",
            provisioned_at=0.0,  # epoch
            manifest="batch/test.yaml",
            abs_max_min=10  # 10 minutes
        )
        write_lease(self.lease_path, lease)

        pods_api = FakePods()
        first_seen = {}

        # Run tick with now far in the future, so tier 2 fires
        returned = pod_watchdog.tick(
            pods_api, first_seen, now=1000.0 * 60.0, dry_run=True
        )

        # Assert: nothing was destroyed
        self.assertEqual(pods_api.destroyed, [])

        # Assert: lease file still exists
        self.assertTrue(self.lease_path.is_file())

    def test_kill_destroys_and_clears_the_lease(self):
        """Property 2: dry_run=False actually destroys the pod and clears the lease."""
        # Same lease as above
        lease = Lease(
            pod_id="pod-old",
            provisioned_at=0.0,
            manifest="batch/test.yaml",
            abs_max_min=10
        )
        write_lease(self.lease_path, lease)

        pods_api = FakePods()
        first_seen = {}

        # Run tick with dry_run=False
        returned = pod_watchdog.tick(
            pods_api, first_seen, now=1000.0 * 60.0, dry_run=False
        )

        # Assert: pod was destroyed
        self.assertEqual(pods_api.destroyed, ["pod-old"])

        # Assert: lease file is gone
        self.assertFalse(self.lease_path.is_file())

    def test_list_pods_failure_preserves_first_seen_and_destroys_nothing(self):
        """Property 3: when list_pods() raises, first_seen is preserved and nothing is destroyed."""
        pods_api = FakePods()
        pods_api._list_error = "API key not configured"

        first_seen_in = {"pod-1": 100.0, "pod-2": 200.0}
        first_seen_out = pod_watchdog.tick(
            pods_api, first_seen_in.copy(), now=300.0, dry_run=False
        )

        # Assert: first_seen is preserved unchanged
        self.assertEqual(first_seen_out, first_seen_in)

        # Assert: nothing was destroyed (no pods were even listed)
        self.assertEqual(pods_api.destroyed, [])

    def test_orphan_survives_grace_then_dies_on_a_later_tick(self):
        """Property 4: first_seen carries across ticks; orphan survives grace period then is destroyed."""
        # No lease on disk
        pods_api = FakePods()

        # Tick 1: see an orphan pod with a destroyable name, but within grace window (10 min)
        orphan = PodInfo(pod_id="orphan-1", name=list(DESTROYABLE_NAMES)[0])
        pods_api.pods = [orphan]
        first_seen = {}

        now_1 = 100.0
        returned_1 = pod_watchdog.tick(
            pods_api, first_seen, now=now_1, dry_run=False
        )

        # Assert: not destroyed (still within 10 min grace)
        self.assertEqual(pods_api.destroyed, [])

        # Assert: pod is now in first_seen
        self.assertIn("orphan-1", returned_1)
        self.assertEqual(returned_1["orphan-1"], now_1)

        # Tick 2: same pod, but now past grace window (11 min later = 660 sec later)
        pods_api.destroyed = []  # reset
        now_2 = now_1 + (11 * 60.0)  # 11 minutes later
        returned_2 = pod_watchdog.tick(
            pods_api, returned_1, now=now_2, dry_run=False
        )

        # Assert: now destroyed (past grace window)
        self.assertEqual(pods_api.destroyed, ["orphan-1"])

        # Assert: first_seen is updated
        self.assertIn("orphan-1", returned_2)


if __name__ == "__main__":
    unittest.main()
