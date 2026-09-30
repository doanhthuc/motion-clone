import sys, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib_ext.gpu_probe import MIN_CLOCK_RATIO, judge, run_probe
import drain

# 2026-09-30, Vast machine 140110: the samples the probe would have seen under load.
BROKEN = "210, 3090, 55.1\n210, 3090, 61.2\n210, 3090, 57.9\n"
HEALTHY = "2790, 3090, 380.0\n2820, 3090, 395.5\n2805, 3090, 391.2\n"


class TestJudge(unittest.TestCase):
    def test_the_throttled_5090_is_slow(self):
        v = judge(BROKEN)
        self.assertEqual(v.state, "slow")
        self.assertLess(v.ratio, MIN_CLOCK_RATIO)

    def test_a_card_at_speed_is_ok(self):
        self.assertEqual(judge(HEALTHY).state, "ok")

    def test_no_usable_samples_is_unknown_not_slow(self):
        # Fails open: a probe that cannot run must not destroy a healthy, already-paid pod.
        for out in ("", "ModuleNotFoundError: torch\n", "N/A, N/A, N/A\n"):
            self.assertEqual(judge(out).state, "unknown", out)

    def test_one_stray_low_sample_does_not_condemn_a_card(self):
        self.assertEqual(judge("210, 3090, 55\n" + HEALTHY).state, "ok")

    def test_ssh_failure_is_unknown(self):
        with mock.patch("batchlib_ext.gpu_probe.subprocess.run", side_effect=OSError("no ssh")):
            self.assertEqual(run_probe("h", "22").state, "unknown")


class TestCheckGpuSpeed(unittest.TestCase):
    def _check(self, provider, verdict):
        with mock.patch.object(drain, "effective_provider", return_value=provider), \
             mock.patch.object(drain, "env_get", return_value="x"), \
             mock.patch.object(drain, "run_probe", return_value=verdict) as probe, \
             mock.patch.object(drain, "blacklist_machine") as bl:
            try:
                drain.check_gpu_speed("iid")
                raised = False
            except drain.SlowGpu:
                raised = True
        return raised, probe, bl

    def test_slow_vast_gpu_is_blacklisted_and_raises(self):
        raised, _, bl = self._check("vast", judge(BROKEN))
        self.assertTrue(raised)
        bl.assert_called_once()

    def test_healthy_or_unknown_passes(self):
        for out in (HEALTHY, ""):
            raised, _, bl = self._check("vast", judge(out))
            self.assertFalse(raised)
            bl.assert_not_called()

    def test_runpod_is_never_probed(self):
        raised, probe, _ = self._check("runpod", judge(BROKEN))
        self.assertFalse(raised)
        probe.assert_not_called()


class TestSlowStageHint(unittest.TestCase):
    def test_hint_reports_the_gpu_state(self):
        from batchlib import runner
        with mock.patch("batchlib.config.env_get", return_value="x"), \
             mock.patch.object(runner, "run_probe", return_value=judge(BROKEN)):
            hint = runner._gpu_speed_hint(Path("/nonexistent"))
        self.assertEqual(hint[0], "slow")
        self.assertIn("7%", hint[1])

    def test_hint_never_raises(self):
        from batchlib import runner
        with mock.patch("batchlib.config.env_get", side_effect=RuntimeError("boom")):
            self.assertIn("could not read", runner._gpu_speed_hint(Path("/nonexistent"))[1])

    def test_no_pod_ssh_is_reported_not_probed(self):
        from batchlib import runner
        with mock.patch("batchlib.config.env_get", return_value=""), \
             mock.patch.object(runner, "run_probe") as probe:
            self.assertIn("no pod ssh", runner._gpu_speed_hint(Path("/nonexistent"))[1])
        probe.assert_not_called()


class TestDrainRetry(unittest.TestCase):
    """drain.main renting a replacement when the first pod's GPU is throttled."""

    def _run(self, probe_results):
        import tempfile
        d = tempfile.mkdtemp()
        f = Path(d) / "m.yaml"
        f.write_text("runs:\n  - id: a\n    pipeline: motion-enhance\n"
                     "    inputs: {character: /tmp/c.png, driver: /tmp/d.mp4}\n")
        calls = {"provision": 0, "teardown": 0, "chain": 0, "batch": []}

        def provision(**kw):
            calls["provision"] += 1
            return f"pod{calls['provision']}"

        def batch_run(*a):
            calls["batch"].append(a)
            return drain.EXIT_NEEDS_POD if "--no-start" in a else 0

        probes = iter(probe_results)

        def check(pod_id):
            if next(probes):
                raise drain.SlowGpu("7% of max")

        patches = [
            mock.patch.object(sys, "argv", ["drain.py", "--file", str(f), "--yes"]),
            mock.patch.object(drain, "provision", side_effect=provision),
            mock.patch.object(drain, "write_lease"),
            mock.patch.object(drain, "wait_and_bootstrap"),
            mock.patch.object(drain, "check_gpu_speed", side_effect=check),
            mock.patch.object(drain, "batch_run", side_effect=batch_run),
            mock.patch.object(drain, "teardown",
                              side_effect=lambda p: calls.__setitem__("teardown", calls["teardown"] + 1)),
            mock.patch.object(drain, "chain_or_teardown",
                              side_effect=lambda p: calls.__setitem__("chain", calls["chain"] + 1)),
        ]
        for p in patches:
            p.start()
        try:
            rc = drain.main()
        finally:
            mock.patch.stopall()
        return rc, calls

    def test_slow_first_pod_is_destroyed_and_replaced(self):
        rc, calls = self._run([True, False])
        self.assertEqual(rc, 0)
        self.assertEqual(calls["provision"], 2)
        self.assertEqual(calls["teardown"], 1)   # the slow pod: destroyed, never chained
        self.assertEqual(calls["chain"], 1)      # the good pod
        self.assertEqual(sum("--resume" in a and "--no-start" not in a for a in calls["batch"]), 1)

    def test_healthy_first_pod_rents_once(self):
        rc, calls = self._run([False])
        self.assertEqual((rc, calls["provision"], calls["teardown"]), (0, 1, 0))

    def test_two_slow_pods_give_up_without_running_the_batch(self):
        rc, calls = self._run([True, True])
        self.assertEqual(rc, 1)
        self.assertEqual(calls["provision"], 2)
        self.assertEqual(calls["teardown"], 2)
        self.assertFalse([a for a in calls["batch"] if "--no-start" not in a])


if __name__ == "__main__":
    unittest.main()
