import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib_ext.lease import Lease
from batchlib_ext.watchdog import decide, in_flight_stage

MIN = 60.0
LEASE = Lease(pod_id="p1", provisioned_at=0.0, manifest="batch/x.yaml", abs_max_min=240)


def state_with(stage: str, status: str) -> dict:
    return {"batch": "b1", "runs": {"r1": {"status": "running",
                                           "stages": {stage: {"status": status}}}}}


class TestInFlightStage(unittest.TestCase):
    def test_finds_the_running_stage(self):
        self.assertEqual(in_flight_stage(state_with("enhance", "running")), "enhance")

    def test_none_when_nothing_running(self):
        self.assertIsNone(in_flight_stage(state_with("enhance", "done")))

    def test_none_on_empty_state(self):
        self.assertIsNone(in_flight_stage({}))


class TestTier1(unittest.TestCase):
    def test_alive_well_inside_the_stage_timeout(self):
        # enhance has timeout_min=90; journal touched 10 min ago
        v = decide(lease=LEASE, state=state_with("enhance", "running"),
                   journal_mtime=0.0, now=10 * MIN)
        self.assertFalse(v.kill)

    def test_killed_past_stage_timeout_plus_slack(self):
        # 90 + 15 = 105 min budget; 106 min of silence means the runner is gone
        v = decide(lease=LEASE, state=state_with("enhance", "running"),
                   journal_mtime=0.0, now=106 * MIN)
        self.assertTrue(v.kill)
        self.assertIn("enhance", v.reason)

    def test_short_stage_gets_a_short_leash(self):
        # tryon is timeout_min=20, so 36 min of silence is already fatal —
        # this is the whole point of deriving the leash per stage
        v = decide(lease=LEASE, state=state_with("tryon", "running"),
                   journal_mtime=0.0, now=36 * MIN)
        self.assertTrue(v.kill)

    def test_no_stage_running_falls_back_to_longest_timeout(self):
        # Between stages, or before the first job is submitted, there is no
        # in-flight stage to size the leash from. Use the longest stage
        # timeout so we never kill a batch that is merely about to start
        # something slow.
        v = decide(lease=LEASE, state=state_with("enhance", "done"),
                   journal_mtime=0.0, now=104 * MIN)
        self.assertFalse(v.kill)
        v = decide(lease=LEASE, state=state_with("enhance", "done"),
                   journal_mtime=0.0, now=106 * MIN)
        self.assertTrue(v.kill)


class TestTier2(unittest.TestCase):
    def test_absolute_ceiling_ignores_a_healthy_heartbeat(self):
        # The runner is alive and touching the journal every minute, but it has
        # been going for longer than the ceiling: a stuck loop still bills.
        v = decide(lease=LEASE, state=state_with("enhance", "running"),
                   journal_mtime=241 * MIN, now=241 * MIN)
        self.assertTrue(v.kill)
        self.assertIn("ceiling", v.reason)

    def test_ceiling_wins_when_both_tiers_would_fire(self):
        # Both tier 1 and tier 2 conditions are true. The ordering matters:
        # if tier 1 is checked first, the reason will contain "silent" (journal
        # timeout). If tier 2 is checked first, the reason will contain "ceiling"
        # and NOT "silent". This test pins the implementation order.
        # age_min=300 > 240 (ceiling fires) AND silent_min=300 > 105 (enhance's
        # 90+15 budget, so tier 1 would also fire).
        v = decide(lease=LEASE, state=state_with("enhance", "running"),
                   journal_mtime=0.0, now=300 * MIN)
        self.assertTrue(v.kill)
        self.assertIn("ceiling", v.reason)
        self.assertNotIn("silent", v.reason)


class TestUnknownStageNeverRaises(unittest.TestCase):
    """I2: lease.py's docstring makes it a house rule — this must never raise."""

    def test_unknown_stage_does_not_raise(self):
        # An old journal replayed after a stage rename, or a hand-edited state
        # file. A KeyError here propagated out of the whole tick and took tiers
        # 1, 2 and 3 down with it, forever.
        v = decide(lease=LEASE, state=state_with("no-such-stage", "running"),
                   journal_mtime=0.0, now=10 * MIN)
        self.assertFalse(v.kill)

    def test_unknown_stage_falls_back_to_the_longest_timeout(self):
        # Same 105 min budget (enhance 90 + 15 slack) as "no stage running":
        # the fallback can only ever delay a kill, never cause an early one.
        self.assertFalse(decide(lease=LEASE,
                                state=state_with("no-such-stage", "running"),
                                journal_mtime=0.0, now=104 * MIN).kill)
        v = decide(lease=LEASE, state=state_with("no-such-stage", "running"),
                   journal_mtime=0.0, now=106 * MIN)
        self.assertTrue(v.kill)
        self.assertIn("unknown to pipelines.py", v.reason)


if __name__ == "__main__":
    unittest.main()
