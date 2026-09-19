import json, os, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.manifest import load_manifest, state_path_for
from batchlib_ext.handoff import Handoff, handoff_path, mailbox_path
from batchlib_ext.lease import Lease
from batchlib_ext.provision_failure import (provision_failure_path,
                                            read_provision_failure,
                                            write_provision_failure,
                                            ProvisionFailure)
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
    def _manifest_path(self) -> Path:
        return Path(tempfile.mkdtemp()) / "tg-1.yaml"

    def test_empty_pod_id_raises_instead_of_writing_a_useless_lease(self):
        # env_get returns "" on ANY failure to read .env (config.py:62-66). A
        # lease with an empty pod_id makes every later kill `runpodctl pod delete
        # ""`, which raises — so the watchdog could never clean up the pod that
        # was just rented.
        with mock.patch.object(drain.subprocess, "run") as mock_run, \
             mock.patch.object(drain, "env_get", return_value=""):
            mock_run.return_value = mock.Mock(returncode=0, stderr="")
            with self.assertRaises(RuntimeError) as cm:
                drain.provision(ceiling_min=120, manifest_path=self._manifest_path())
        self.assertIn("GPU_INSTANCE_ID is empty", str(cm.exception))

    def test_returns_the_pod_id_provisioning_wrote_to_env(self):
        with mock.patch.object(drain.subprocess, "run") as mock_run, \
             mock.patch.object(drain, "env_get", side_effect=["8", "pod-xyz"]):
            mock_run.return_value = mock.Mock(returncode=0, stderr="")
            pod_id = drain.provision(ceiling_min=120, manifest_path=self._manifest_path())
            self.assertEqual(pod_id, "pod-xyz")
        # provision() must NOT wait or bootstrap: main() writes the lease between
        # the two, because the pod bills from the moment provisioning returns.
        self.assertEqual(mock_run.call_count, 1)

    def test_success_clears_a_stale_provision_failure(self):
        # A previous drain's stock-out sentinel must not survive a rental
        # that then succeeds — the bot would otherwise keep offering
        # recovery buttons for a problem that is already resolved.
        manifest_path = self._manifest_path()
        failure_path = provision_failure_path(manifest_path)
        write_provision_failure(failure_path, ProvisionFailure(
            gpu="x", datacenter=None, stock_out=True, detail="stale"))
        with mock.patch.object(drain.subprocess, "run") as mock_run, \
             mock.patch.object(drain, "env_get", side_effect=["8", "pod-xyz"]):
            mock_run.return_value = mock.Mock(returncode=0, stderr="")
            drain.provision(ceiling_min=120, manifest_path=manifest_path)
        self.assertFalse(failure_path.exists())

    def test_stock_out_failure_writes_a_classified_provision_failure(self):
        # The exact stderr text pod-provision.sh's own die() prints on a
        # "no instances available" runpodctl create — see scripts/pod-
        # provision.sh's stock-out branch.
        stderr = ("\033[31m ✗ \033[0mhết máy 'NVIDIA GeForce RTX 5090' "
                  "tại datacenter của volume — không tự xoay "
                  "sang card khác.\n")
        manifest_path = self._manifest_path()
        with mock.patch.object(drain.subprocess, "run") as mock_run, \
             mock.patch.object(drain, "env_get",
                               side_effect=lambda path, key: {
                                   "POD_MAX_HOURS": "8", "GPU": "NVIDIA GeForce RTX 5090",
                                   "POD_VOLUME_ID": "u469c9efga"}[key]), \
             mock.patch.object(drain, "volume_datacenter", return_value="EU-RO-1"):
            mock_run.return_value = mock.Mock(returncode=1, stderr=stderr)
            with self.assertRaises(drain.subprocess.CalledProcessError):
                drain.provision(ceiling_min=120, manifest_path=manifest_path)
        failure = read_provision_failure(provision_failure_path(manifest_path))
        self.assertIsNotNone(failure)
        self.assertTrue(failure.stock_out)
        self.assertEqual(failure.gpu, "NVIDIA GeForce RTX 5090")
        self.assertEqual(failure.datacenter, "EU-RO-1")

    def test_other_failure_writes_a_non_stock_out_provision_failure(self):
        stderr = "✗ runpodctl pod create failed ('NVIDIA GeForce RTX 5090'):\nsome API error\n"
        manifest_path = self._manifest_path()
        with mock.patch.object(drain.subprocess, "run") as mock_run, \
             mock.patch.object(drain, "env_get",
                               side_effect=lambda path, key: {
                                   "POD_MAX_HOURS": "8", "GPU": "NVIDIA GeForce RTX 5090",
                                   "POD_VOLUME_ID": "u469c9efga"}[key]), \
             mock.patch.object(drain, "volume_datacenter", return_value="EU-RO-1"):
            mock_run.return_value = mock.Mock(returncode=1, stderr=stderr)
            with self.assertRaises(drain.subprocess.CalledProcessError):
                drain.provision(ceiling_min=120, manifest_path=manifest_path)
        failure = read_provision_failure(provision_failure_path(manifest_path))
        self.assertIsNotNone(failure)
        self.assertFalse(failure.stock_out)
        self.assertIn("some API error", failure.detail)


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

    def test_a_chained_link_keeps_the_leases_provider(self):
        # The lease is rewritten for every chained job. Dropping the provider would reset a
        # Vast lease to "runpod" mid-batch, and the watchdog would then try to destroy a Vast
        # instance through runpodctl.
        tmpdir = tempfile.mkdtemp()
        original = self._original(tmpdir)
        nxt = Path(tmpdir) / "tg-1-999.yaml"
        nxt.write_text(NEXT_YAML, encoding="utf-8")
        old_lease = Lease(pod_id="777", provisioned_at=1000.0,
                          manifest=str(original.resolve()), abs_max_min=330,
                          provider="vast")
        with mock.patch.object(drain, "claim_mailbox", side_effect=[nxt, None]), \
             mock.patch.object(drain, "batch_run", return_value=0), \
             mock.patch.object(drain, "read_lease", return_value=old_lease), \
             mock.patch.object(drain, "write_lease") as mock_write_lease, \
             mock.patch.object(drain, "teardown"):
            chain_or_teardown(original)
        self.assertEqual(mock_write_lease.call_args[0][1].provider, "vast")

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


class TestPhaseAForwardsForceLocal(unittest.TestCase):
    """--force-local must reach batch_run.py in the Phase A invocation, not
    only in the post-provisioning one.

    A flag that arrives only in the second invocation is not a no-op — that
    invocation is a full batch_run.main, which calls run_local_phase before
    run_batch, so the try-on would still be regenerated ahead of motion and
    enhance. It is worse than a no-op: it regenerates after provision() and
    wait_and_bootstrap(), so the GPU bills while the process waits on a hosted
    Gemini call.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.manifest = self.tmp / "tg-1.yaml"
        self.manifest.write_text(
            "runs:\n  - id: a\n    pipeline: tryon-motion-enhance\n"
            "    inputs: {character: /tmp/c.png, outfit: /tmp/o.png, driver: /tmp/d.mp4}\n"
            "    tryon: { provider: gemini }\n", encoding="utf-8")

    def test_force_local_is_forwarded_to_phase_a(self):
        seen: list[tuple] = []
        with mock.patch.object(drain, "batch_run",
                               side_effect=lambda *a: seen.append(a) or drain.EXIT_NEEDS_POD), \
             mock.patch.object(drain, "provision", side_effect=AssertionError("must not rent")):
            with mock.patch.object(sys, "argv",
                                   ["drain.py", "--file", str(self.manifest),
                                    "--yes", "--force-local"]):
                with self.assertRaises(AssertionError):
                    drain.main()
        self.assertIn("--force-local", seen[0])

    def test_absent_flag_does_not_add_it(self):
        seen: list[tuple] = []
        with mock.patch.object(drain, "batch_run",
                               side_effect=lambda *a: seen.append(a) or drain.EXIT_NEEDS_POD), \
             mock.patch.object(drain, "provision", side_effect=AssertionError("must not rent")):
            with mock.patch.object(sys, "argv",
                                   ["drain.py", "--file", str(self.manifest), "--yes"]):
                with self.assertRaises(AssertionError):
                    drain.main()
        self.assertNotIn("--force-local", seen[0])


class TestPhaseAOnly(unittest.TestCase):
    """--phase-a-only runs the local try-on and stops. It may never reach
    provision(), whatever Phase A returns.

    Also independent of --yes, and that ordering is the trap: main()'s
    existing `if not args.yes` gate prints DRY RUN and returns 0 without
    running anything. Phase A is not a dry run — it spends Gemini quota and
    writes the journal — so a --phase-a-only invocation placed after that
    gate would be a money-adjacent flag that silently does nothing.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.manifest = self.tmp / "tg-1.yaml"
        self.manifest.write_text(
            "runs:\n  - id: a\n    pipeline: tryon-motion-enhance\n"
            "    inputs: {character: /tmp/c.png, outfit: /tmp/o.png, driver: /tmp/d.mp4}\n"
            "    tryon: { provider: gemini }\n", encoding="utf-8")

    def _main(self, *extra: str):
        with mock.patch.object(sys, "argv",
                               ["drain.py", "--file", str(self.manifest), *extra]):
            return drain.main()

    def test_never_provisions_when_phase_a_says_a_pod_is_needed(self):
        with mock.patch.object(drain, "batch_run", return_value=drain.EXIT_NEEDS_POD), \
             mock.patch.object(drain, "provision",
                               side_effect=AssertionError("rented a pod")) as prov, \
             mock.patch.object(drain, "chain_or_teardown",
                               side_effect=AssertionError("tore down")) as co:
            rc = self._main("--phase-a-only", "--yes")
        self.assertEqual(rc, drain.EXIT_NEEDS_POD)
        prov.assert_not_called()
        co.assert_not_called()

    def test_runs_without_yes_rather_than_printing_dry_run(self):
        calls: list[tuple] = []
        with mock.patch.object(drain, "batch_run",
                               side_effect=lambda *a: calls.append(a) or 0):
            rc = self._main("--phase-a-only")
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 1)
        self.assertIn("--no-start", calls[0])

    def test_propagates_a_phase_a_failure_code(self):
        with mock.patch.object(drain, "batch_run", return_value=1), \
             mock.patch.object(drain, "provision",
                               side_effect=AssertionError("rented a pod")):
            self.assertEqual(self._main("--phase-a-only"), 1)

    def test_forwards_resume_and_force_local(self):
        calls: list[tuple] = []
        with mock.patch.object(drain, "batch_run",
                               side_effect=lambda *a: calls.append(a) or 0):
            self._main("--phase-a-only", "--resume", "--force-local")
        self.assertIn("--resume", calls[0])
        self.assertIn("--force-local", calls[0])

    def test_no_yes_and_no_phase_a_is_still_a_dry_run(self):
        # The existing gate must survive unchanged for the renting path.
        with mock.patch.object(drain, "batch_run",
                               side_effect=AssertionError("ran a batch")) as br:
            rc = self._main()
        self.assertEqual(rc, 0)
        br.assert_not_called()


class TestProvider(unittest.TestCase):
    """The provider of a run follows the run — env and lease — never the root .env."""

    def _manifest_path(self) -> Path:
        return Path(tempfile.mkdtemp()) / "tg-1.yaml"

    def test_effective_provider_prefers_the_environment(self):
        with mock.patch.dict(os.environ, {"GPU_PROVIDER": "vast"}), \
             mock.patch.object(drain, "env_get", return_value="runpod"):
            self.assertEqual(drain.effective_provider(), "vast")

    def test_effective_provider_falls_back_to_dotenv_then_vast(self):
        env = {k: v for k, v in os.environ.items() if k != "GPU_PROVIDER"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(drain, "env_get", return_value="runpod"):
                self.assertEqual(drain.effective_provider(), "runpod")
            # pod-provision.sh:21 defaults to vast when nothing says otherwise.
            with mock.patch.object(drain, "env_get", return_value=""):
                self.assertEqual(drain.effective_provider(), "vast")

    def test_provision_passes_no_volume_off_runpod(self):
        # pod-provision.sh dies on POD_VOLUME set with a non-runpod provider. `.env` keeps the
        # RunPod volume for the home provider, so a vast run has to blank it for the child.
        with mock.patch.dict(os.environ, {"GPU_PROVIDER": "vast"}), \
             mock.patch.object(drain.subprocess, "run") as mock_run, \
             mock.patch.object(drain, "env_get", side_effect=["8", "777"]):
            mock_run.return_value = mock.Mock(returncode=0, stderr="")
            drain.provision(ceiling_min=120, manifest_path=self._manifest_path())
        self.assertIn("POD_VOLUME= ", mock_run.call_args[0][0])

    def test_provision_leaves_the_volume_alone_on_runpod(self):
        with mock.patch.dict(os.environ, {"GPU_PROVIDER": "runpod"}), \
             mock.patch.object(drain.subprocess, "run") as mock_run, \
             mock.patch.object(drain, "env_get", side_effect=["8", "pod-xyz"]):
            mock_run.return_value = mock.Mock(returncode=0, stderr="")
            drain.provision(ceiling_min=120, manifest_path=self._manifest_path())
        self.assertNotIn("POD_VOLUME", mock_run.call_args[0][0])

    def test_provision_is_unchanged_when_no_provider_was_chosen(self):
        env = {k: v for k, v in os.environ.items() if k != "GPU_PROVIDER"}
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(drain.subprocess, "run") as mock_run, \
             mock.patch.object(drain, "env_get", side_effect=["8", "pod-xyz"]):
            mock_run.return_value = mock.Mock(returncode=0, stderr="")
            drain.provision(ceiling_min=120, manifest_path=self._manifest_path())
        self.assertNotIn("POD_VOLUME", mock_run.call_args[0][0])

    def test_main_exports_the_provider_and_writes_it_into_the_lease(self):
        tmp = Path(tempfile.mkdtemp())
        manifest = tmp / "tg-1.yaml"
        manifest.write_text(
            "runs:\n  - id: a\n    pipeline: motion-enhance\n"
            "    inputs: {character: /tmp/c.png, driver: /tmp/d.mp4}\n", encoding="utf-8")
        seen_env = {}

        def fake_provision(**_kw):
            seen_env["GPU_PROVIDER"] = os.environ.get("GPU_PROVIDER")
            return "777"

        with mock.patch.dict(os.environ, {}, clear=False), \
             mock.patch.object(sys, "argv", ["drain.py", "--file", str(manifest),
                                             "--yes", "--provider", "vast"]), \
             mock.patch.object(drain, "batch_run", side_effect=[drain.EXIT_NEEDS_POD, 0]), \
             mock.patch.object(drain, "provision", side_effect=fake_provision), \
             mock.patch.object(drain, "write_lease") as mock_write_lease, \
             mock.patch.object(drain, "wait_and_bootstrap"), \
             mock.patch.object(drain, "chain_or_teardown"):
            self.assertEqual(drain.main(), 0)
        self.assertEqual(seen_env["GPU_PROVIDER"], "vast")
        lease = mock_write_lease.call_args[0][1]
        self.assertEqual((lease.pod_id, lease.provider), ("777", "vast"))


if __name__ == "__main__":
    unittest.main()
