import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib_ext.lease import Lease, write_lease
from batchlib_ext.migrate_lease import MigrateLease, write_migrate_lease
from batchlib_ext.podctl import PodInfo
from batchlib_ext.watchdog import DESTROYABLE_NAMES, MIGRATE_DESTROYABLE_NAMES
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


class NoOpDestroyPods:
    """A pod API whose destroy() exits cleanly and does nothing.

    This is the real failure Makefile:139-142 records: a destroy that reports
    success without taking effect. The pod stays in list_pods() afterwards.
    """
    def __init__(self, pods: list[PodInfo]):
        self.pods = pods
        self.destroy_calls: list[str] = []

    def list_pods(self) -> list[PodInfo]:
        return list(self.pods)

    def destroy(self, pod_id: str) -> None:
        self.destroy_calls.append(pod_id)   # exits 0, changes nothing


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


class TestDestroyIsVerified(unittest.TestCase):
    """C3: a destroy that exits 0 without taking effect must not clear the lease."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.lease_path = Path(self.temp_dir.name) / "pod-lease.json"
        self.patcher = patch.object(pod_watchdog, "LEASE_PATH", self.lease_path)
        self.patcher.start()
        self.logs: list[str] = []
        self.log_patcher = patch.object(pod_watchdog, "log", self.logs.append)
        self.log_patcher.start()

    def tearDown(self):
        self.log_patcher.stop()
        self.patcher.stop()
        self.temp_dir.cleanup()

    def _expired_lease(self, pod_id: str = "pod-old") -> Lease:
        return Lease(pod_id=pod_id, provisioned_at=0.0,
                     manifest="batch/test.yaml", abs_max_min=10)

    def test_unverified_destroy_keeps_the_lease(self):
        # Clearing the lease here would hand a still-billing pod to tier 3, which
        # needs a 10-minute grace window to notice it. Keeping it means the next
        # tick retries in 60s.
        write_lease(self.lease_path, self._expired_lease())
        pods_api = NoOpDestroyPods([PodInfo("pod-old", "motion-transfer")])

        pod_watchdog.tick(pods_api, {}, now=1000.0 * 60.0, dry_run=False)

        self.assertEqual(pods_api.destroy_calls, ["pod-old"])
        self.assertTrue(self.lease_path.is_file(),
                        "lease was cleared over a destroy that did nothing")

    def test_unverified_destroy_logs_loudly_and_claims_no_success(self):
        write_lease(self.lease_path, self._expired_lease())
        pods_api = NoOpDestroyPods([PodInfo("pod-old", "motion-transfer")])

        pod_watchdog.tick(pods_api, {}, now=1000.0 * 60.0, dry_run=False)

        joined = "\n".join(self.logs)
        self.assertIn("DESTROY NOT CONFIRMED", joined)
        self.assertIn("STILL BILLING", joined)
        self.assertIn("pod-old", joined)

    def test_verified_destroy_clears_the_lease(self):
        # The other direction: when the pod really is gone, the lease must go too,
        # or every later tick would try to destroy a pod that no longer exists.
        write_lease(self.lease_path, self._expired_lease())
        pods_api = NoOpDestroyPods([])   # nothing visible => confirmed gone

        pod_watchdog.tick(pods_api, {}, now=1000.0 * 60.0, dry_run=False)

        self.assertEqual(pods_api.destroy_calls, ["pod-old"])
        self.assertFalse(self.lease_path.is_file())


class TestLeaseBranchCannotDisableTierThree(unittest.TestCase):
    """I2: one raise in the tier-1/2 branch must not skip reconciliation."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.lease_path = Path(self.temp_dir.name) / "pod-lease.json"
        self.patcher = patch.object(pod_watchdog, "LEASE_PATH", self.lease_path)
        self.patcher.start()
        self.logs: list[str] = []
        self.log_patcher = patch.object(pod_watchdog, "log", self.logs.append)
        self.log_patcher.start()

    def tearDown(self):
        self.log_patcher.stop()
        self.patcher.stop()
        self.temp_dir.cleanup()

    def test_a_raising_decide_still_reaches_reconciliation(self):
        # The lease names "mine"; a DIFFERENT pod is running and long past grace.
        # Before this guard, decide() raising took tiers 1, 2 AND 3 down together
        # and main() logged "tick failed, continuing" forever.
        write_lease(self.lease_path, Lease(pod_id="mine", provisioned_at=0.0,
                                           manifest="batch/test.yaml",
                                           abs_max_min=10_000))
        pods_api = FakePods()
        pods_api.pods = [PodInfo("stray", "motion-transfer")]

        with patch.object(pod_watchdog, "decide",
                          side_effect=KeyError("unknown-stage")):
            pod_watchdog.tick(pods_api, {"stray": 0.0}, now=11 * 60.0,
                              dry_run=False)

        self.assertEqual(pods_api.destroyed, ["stray"])
        self.assertIn("falling through to tier 3", "\n".join(self.logs))

    def test_tier_three_reports_how_many_pods_it_saw(self):
        # I4: "saw nothing" and "saw things and matched nothing" used to be the
        # same silence, which is what hid tier 3 never running at all.
        pods_api = FakePods()
        pods_api.pods = [PodInfo("a", "cpu-failover-temp"),
                         PodInfo("b", "cpu-failover-temp")]

        pod_watchdog.tick(pods_api, {}, now=0.0, dry_run=True)

        self.assertIn("tier 3: 2 pod(s) visible", "\n".join(self.logs))

    def test_untouched_message_names_the_real_reason(self):
        # A motion-transfer pod inside its grace window is left alone BECAUSE of
        # the grace window, not because its name is unmanaged. Saying the latter
        # would be a false statement in the one log a reviewer reads after a bill.
        pods_api = FakePods()
        pods_api.pods = [PodInfo("young", "motion-transfer"),
                         PodInfo("other", "cpu-failover-temp")]

        pod_watchdog.tick(pods_api, {}, now=0.0, dry_run=True)

        joined = "\n".join(self.logs)
        self.assertIn("grace window", joined)
        self.assertIn("not a name tier 3 may destroy", joined)
        self.assertNotIn("young ('motion-transfer') alone — not a name", joined)


class TestMigrationTiers(unittest.TestCase):
    """The two TEMPORARY CPU pods a volume migration rents get the same
    tier-1/2 (ceiling) and tier-3 (orphan reconciliation) protection as the
    real GPU pod, via a SEPARATE lease file (MIGRATE_LEASE_PATH) so the two
    protections cannot be confused with each other.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        temp_path = Path(self.temp_dir.name)
        self.lease_path = temp_path / "pod-lease.json"
        self.migrate_lease_path = temp_path / "migrate-lease.json"
        self.lease_patcher = patch.object(pod_watchdog, "LEASE_PATH", self.lease_path)
        self.lease_patcher.start()
        self.migrate_patcher = patch.object(pod_watchdog, "MIGRATE_LEASE_PATH",
                                            self.migrate_lease_path)
        self.migrate_patcher.start()
        self.logs: list[str] = []
        self.log_patcher = patch.object(pod_watchdog, "log", self.logs.append)
        self.log_patcher.start()

    def tearDown(self):
        self.log_patcher.stop()
        self.migrate_patcher.stop()
        self.lease_patcher.stop()
        self.temp_dir.cleanup()

    def test_a_migration_past_ceiling_is_killed_and_lease_cleared(self):
        write_migrate_lease(self.migrate_lease_path, MigrateLease(
            pod_a_id="tmp-a", pod_b_id="tmp-b", started_at=0.0, to_dc="EU-CZ-1"))
        # No .pods set: list_pods() returns [] (FakePods' hasattr fallback),
        # i.e. both temp pods are gone by the time destroy_verified re-lists —
        # same convention as test_kill_destroys_and_clears_the_lease above.
        pods_api = FakePods()

        pod_watchdog.tick(pods_api, {}, now=100000.0, dry_run=False)

        self.assertIn("tmp-a", pods_api.destroyed)
        self.assertIn("tmp-b", pods_api.destroyed)
        self.assertFalse(self.migrate_lease_path.exists())

    def test_migration_dry_run_never_destroys_and_keeps_the_lease(self):
        write_migrate_lease(self.migrate_lease_path, MigrateLease(
            pod_a_id="tmp-a", pod_b_id="tmp-b", started_at=0.0, to_dc="EU-CZ-1"))
        pods_api = FakePods()
        pods_api.pods = [PodInfo(pod_id="tmp-a", name="migrate-tmp-a"),
                         PodInfo(pod_id="tmp-b", name="migrate-tmp-b")]

        pod_watchdog.tick(pods_api, {}, now=100000.0, dry_run=True)

        self.assertEqual(pods_api.destroyed, [])
        self.assertTrue(self.migrate_lease_path.exists())

    def test_migration_within_ceiling_is_left_alone(self):
        write_migrate_lease(self.migrate_lease_path, MigrateLease(
            pod_a_id="tmp-a", pod_b_id="tmp-b", started_at=0.0, to_dc="EU-CZ-1"))
        pods_api = FakePods()
        pods_api.pods = [PodInfo(pod_id="tmp-a", name="migrate-tmp-a"),
                         PodInfo(pod_id="tmp-b", name="migrate-tmp-b")]

        # 20 min in, well under the 40 min ceiling.
        pod_watchdog.tick(pods_api, {}, now=20 * 60.0, dry_run=False)

        self.assertEqual(pods_api.destroyed, [])
        self.assertTrue(self.migrate_lease_path.exists())

    def test_unverified_migration_destroy_keeps_the_lease(self):
        write_migrate_lease(self.migrate_lease_path, MigrateLease(
            pod_a_id="tmp-a", pod_b_id="tmp-b", started_at=0.0, to_dc="EU-CZ-1"))
        pods_api = NoOpDestroyPods([PodInfo("tmp-a", "migrate-tmp-a"),
                                   PodInfo("tmp-b", "migrate-tmp-b")])

        pod_watchdog.tick(pods_api, {}, now=100000.0, dry_run=False)

        self.assertEqual(sorted(pods_api.destroy_calls), ["tmp-a", "tmp-b"])
        self.assertTrue(self.migrate_lease_path.is_file(),
                        "migrate lease was cleared over a destroy that did nothing")
        joined = "\n".join(self.logs)
        self.assertIn("DESTROY NOT CONFIRMED", joined)

    def test_tier_three_reconciles_an_orphaned_migration_pod_past_grace(self):
        # No migrate lease on disk at all — same as the real GPU pod's tier 3,
        # an unclaimed pod with a destroyable name past the grace window is an
        # orphan.
        pods_api = FakePods()
        pods_api.pods = [PodInfo(pod_id="orphan-a", name="migrate-tmp-a")]

        first_seen = {"orphan-a": 0.0}
        pod_watchdog.tick(pods_api, first_seen, now=11 * 60.0, dry_run=False)

        self.assertEqual(pods_api.destroyed, ["orphan-a"])

    def test_tier_three_leaves_a_young_orphaned_migration_pod_alone(self):
        pods_api = FakePods()
        pods_api.pods = [PodInfo(pod_id="orphan-a", name="migrate-tmp-a")]

        pod_watchdog.tick(pods_api, {}, now=0.0, dry_run=False)

        self.assertEqual(pods_api.destroyed, [])
        joined = "\n".join(self.logs)
        self.assertIn("grace window", joined)

    def test_real_gpu_pod_tiers_are_unaffected_by_an_active_migration_lease(self):
        # The two protections must not interfere: an active migration lease
        # must not change what happens to the real GPU pod.
        write_lease(self.lease_path, Lease(pod_id="pod-old", provisioned_at=0.0,
                                           manifest="batch/test.yaml", abs_max_min=10))
        write_migrate_lease(self.migrate_lease_path, MigrateLease(
            pod_a_id="tmp-a", pod_b_id="tmp-b", started_at=0.0, to_dc="EU-CZ-1"))
        pods_api = FakePods()
        pods_api.pods = []

        pod_watchdog.tick(pods_api, {}, now=1000.0 * 60.0, dry_run=False)

        self.assertEqual(pods_api.destroyed, ["pod-old"])
        self.assertFalse(self.lease_path.is_file())


class TestOnceExitCode(unittest.TestCase):
    """Minor: --once must fail loudly. make watchdog-dry is acceptance step A1."""

    def test_once_returns_nonzero_when_the_tick_raised(self):
        with patch.object(sys, "argv", ["pod_watchdog.py", "--once", "--dry-run"]), \
             patch.object(pod_watchdog, "RunpodCtl", lambda: object()), \
             patch.object(pod_watchdog, "log", lambda *_: None), \
             patch.object(pod_watchdog, "tick", side_effect=RuntimeError("boom")):
            self.assertEqual(pod_watchdog.main(), 1)

    def test_once_returns_zero_on_a_clean_tick(self):
        with patch.object(sys, "argv", ["pod_watchdog.py", "--once", "--dry-run"]), \
             patch.object(pod_watchdog, "RunpodCtl", lambda: object()), \
             patch.object(pod_watchdog, "log", lambda *_: None), \
             patch.object(pod_watchdog, "tick", return_value={}):
            self.assertEqual(pod_watchdog.main(), 0)


if __name__ == "__main__":
    unittest.main()
