import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib_ext.lease import Lease
from batchlib_ext.podctl import PodInfo
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


if __name__ == "__main__":
    unittest.main()
