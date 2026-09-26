import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib_ext.lease import Lease
import control.runs as runs
import tgbot.run as run_mod

STATE = {"version": 1, "batch": "2026-09-20-1000", "runs": {
    "a": {"status": "done", "stages": {
        "motion": {"status": "done", "elapsed_sec": 247,
                   "file": "/opt/motion-clone/out/2026-09-20-1000/runs/a/01-motion.mp4",
                   "params_sent": {"cfg": 1}},
        "enhance": {"status": "done", "elapsed_sec": 90}}},
    "b": {"status": "error", "stages": {"motion": {"status": "error"}}}}}


def write_run(batch_dir: Path, run_id: str, state: dict, mtime: float | None = None) -> Path:
    manifest = batch_dir / f"{run_id}.yaml"
    manifest.write_text("runs: []\n", encoding="utf-8")
    state_file = batch_dir / f"{run_id}.state.json"
    state_file.write_text(json.dumps(state), encoding="utf-8")
    if mtime is not None:
        os.utime(state_file, (mtime, mtime))
    return manifest


class RunsTestBase(unittest.TestCase):
    def setUp(self):
        self.batch = Path(tempfile.mkdtemp())
        self.out = Path(tempfile.mkdtemp())
        patches = [mock.patch.object(run_mod, "drain_running", return_value=False),
                   mock.patch.object(run_mod, "phase_a_running", return_value=False),
                   mock.patch.object(run_mod, "lease_for", return_value=None)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)


class TestListRuns(RunsTestBase):
    def test_newest_first_and_only_journals_with_a_manifest(self):
        write_run(self.batch, "old", STATE, mtime=1000)
        write_run(self.batch, "new", STATE, mtime=2000)
        (self.batch / "orphan.state.json").write_text(json.dumps(STATE))   # no .yaml
        (self.batch / "pod-lease.json").write_text("{}")
        self.assertEqual([r["id"] for r in runs.list_runs(self.batch, self.out)], ["new", "old"])

    def test_counts(self):
        write_run(self.batch, "r", STATE)
        [summary] = runs.list_runs(self.batch, self.out)
        self.assertEqual((summary["jobs_total"], summary["jobs_done"]), (2, 1))
        self.assertEqual(summary["batch"], "2026-09-20-1000")

    def test_state_file_deleted_between_glob_and_stat_is_skipped(self):
        # /clear or batch-clean can delete a state file after glob() found it
        # but before _summary() stats it. That race used to raise out of
        # list_runs and turn the whole list into a 500 for one vanished run.
        write_run(self.batch, "gone", STATE)
        write_run(self.batch, "keep", STATE)
        real_stat = Path.stat

        def flaky_stat(path_self, *args, **kwargs):
            if path_self.name == "gone.state.json":
                raise FileNotFoundError(path_self)
            return real_stat(path_self, *args, **kwargs)

        with mock.patch.object(Path, "stat", flaky_stat):
            result = runs.list_runs(self.batch, self.out)
        self.assertEqual([r["id"] for r in result], ["keep"])


class TestStatus(RunsTestBase):
    def test_error_when_any_job_errored_and_nothing_runs(self):
        write_run(self.batch, "r", STATE)
        self.assertEqual(runs.run_detail(self.batch, self.out, "r")["status"], "error")

    def test_done_when_every_job_is_done(self):
        state = {"batch": "x", "runs": {"a": STATE["runs"]["a"]}}
        write_run(self.batch, "r", state)
        self.assertEqual(runs.run_detail(self.batch, self.out, "r")["status"], "done")

    def test_stopped_when_nothing_recorded(self):
        write_run(self.batch, "r", {"batch": "x", "runs": {}})
        self.assertEqual(runs.run_detail(self.batch, self.out, "r")["status"], "stopped")

    def test_a_live_drain_wins_over_the_journal(self):
        write_run(self.batch, "r", STATE)
        with mock.patch.object(run_mod, "drain_running", return_value=True):
            self.assertEqual(runs.run_detail(self.batch, self.out, "r")["status"], "running")

    def test_phase_a(self):
        write_run(self.batch, "r", STATE)
        with mock.patch.object(run_mod, "phase_a_running", return_value=True):
            self.assertEqual(runs.run_detail(self.batch, self.out, "r")["status"], "phase_a")


class TestDetail(RunsTestBase):
    def test_unknown_or_hostile_id_is_none(self):
        for bad in ("nope", "../etc", "a/b", ""):
            self.assertIsNone(runs.run_detail(self.batch, self.out, bad), bad)

    def test_never_leaks_absolute_paths_or_sent_params(self):
        write_run(self.batch, "r", STATE)
        body = json.dumps(runs.run_detail(self.batch, self.out, "r"))
        self.assertNotIn("/opt/motion-clone", body)
        self.assertNotIn("params_sent", body)

    def test_stages_keep_journal_order(self):
        write_run(self.batch, "r", STATE)
        job = runs.run_detail(self.batch, self.out, "r")["jobs"][0]
        self.assertEqual([s["name"] for s in job["stages"]], ["motion", "enhance"])
        self.assertEqual(job["stages"][0]["elapsed_sec"], 247)

    def test_lease_is_summarised(self):
        write_run(self.batch, "r", STATE)
        provisioned_at = time.time() - 65
        lease = Lease(pod_id="p1", provisioned_at=provisioned_at,
                      manifest=str(self.batch / "r.yaml"), abs_max_min=120, provider="vast")
        with mock.patch.object(run_mod, "lease_for", return_value=lease):
            got = runs.run_detail(self.batch, self.out, "r")["lease"]
        self.assertEqual(got["provider"], "vast")
        self.assertEqual(got["abs_max_min"], 120)
        # provisioned_at, not a derived elapsed_sec: elapsed_sec changed every
        # second, which meant the ETag (a hash of the whole body) could never
        # repeat while a pod was live, so 304 never fired (review finding 2a).
        self.assertEqual(got["provisioned_at"], provisioned_at)
        self.assertNotIn("elapsed_sec", got)

    def test_run_detail_lease_carries_a_quote(self):
        # A quote, not the invoice (CLAUDE.md: cost claims need the invoice):
        # the flat $0.99/h the bot's own /kill text uses for RunPod. Vast's
        # lease does not carry the offer's price, so its quote is None.
        write_run(self.batch, "r", STATE)

        def lease(provider: str) -> Lease:
            return Lease(pod_id="p1", provisioned_at=1_800_000_000.0,
                         manifest=str(self.batch / "r.yaml"), abs_max_min=120,
                         provider=provider)

        with mock.patch.object(run_mod, "lease_for", return_value=lease("runpod")):
            first = runs.run_detail(self.batch, self.out, "r")
            second = runs.run_detail(self.batch, self.out, "r")
        self.assertEqual(first["lease"]["quoted_usd_per_hr"], 0.99)
        self.assertEqual(runs.RUNPOD_FLAT_USD_PER_HR, 0.99)
        # ETag stability (spec 5.3): the quote is a constant, so two polls of
        # an unchanged run are byte-identical.
        self.assertEqual(first, second)
        with mock.patch.object(run_mod, "lease_for", return_value=lease("vast")):
            vast = runs.run_detail(self.batch, self.out, "r")
        self.assertIsNone(vast["lease"]["quoted_usd_per_hr"])

    def test_outputs_list_final_files_only(self):
        write_run(self.batch, "r", STATE)
        final = self.out / "2026-09-20-1000" / "_final"
        final.mkdir(parents=True)
        (final / "a.mp4").write_bytes(b"v")
        (final / "notes.txt").write_text("x")
        self.assertEqual(runs.run_detail(self.batch, self.out, "r")["outputs"], ["a.mp4"])


class TestOutcome(unittest.TestCase):
    def test_truthiness_and_status(self):
        import control
        from control.runs import Outcome, status_for
        self.assertTrue(Outcome(True, "started"))
        self.assertFalse(Outcome(False, "migration", "wait"))
        self.assertEqual(status_for(Outcome(True, "queued")), 202)
        self.assertEqual(status_for(Outcome(False, "not_validated")), 422)
        self.assertEqual(status_for(Outcome(False, "no_such_code")), 409)
        self.assertEqual(status_for(Outcome(False, "not_found")), 404)
        with control.BOT_LOCK:
            with control.BOT_LOCK:
                pass
        self.assertEqual(control.BOT_LOCK_TIMEOUT_SEC, 60)


if __name__ == "__main__":
    unittest.main()


class TestJobSetup(RunsTestBase):
    """What each job was made from, for the phone's batch details. Material
    ids only — the manifest's absolute paths never leave the box."""

    MANIFEST = (
        "runs:\n"
        "  - id: a\n"
        "    pipeline: tryon-character-swap-enhance\n"
        "    inputs:\n"
        "      character: /opt/motion-clone/batch/tg-staging/app/IMG_7145.png\n"
        "      driver: /opt/motion-clone/batch/tg-staging/app/tiktok-1.mp4\n"
        "      outfit: /opt/motion-clone/batch/tg-staging/app/IMG_7112.jpeg\n"
        "    character-swap: { preset: drv-20s }\n"
        "    tryon: { provider: qwen-max }\n"
        "  - id: b\n"
        "    pipeline: motion-enhance\n"
        "    inputs:\n"
        "      character: /opt/motion-clone/batch/tg-staging/app/IMG_7145.png\n"
        "      driver: /opt/motion-clone/batch/tg-staging/app/tiktok-2.mp4\n"
    )

    def test_each_job_carries_pipeline_provider_and_material_ids(self):
        manifest = write_run(self.batch, "r", STATE)
        manifest.write_text(self.MANIFEST, encoding="utf-8")
        jobs = {j["id"]: j for j in runs.run_detail(self.batch, self.out, "r")["jobs"]}
        self.assertEqual(jobs["a"]["setup"], {
            "pipeline": "tryon-character-swap-enhance", "provider": "qwen-max",
            "inputs": {"character": "app/IMG_7145.png", "driver": "app/tiktok-1.mp4",
                       "outfit": "app/IMG_7112.jpeg"}})
        self.assertEqual(jobs["b"]["setup"]["provider"], None)
        self.assertNotIn("/opt", json.dumps(jobs))

    def test_unreadable_manifest_leaves_setup_null(self):
        write_run(self.batch, "r", STATE)   # "runs: []" does not load
        jobs = runs.run_detail(self.batch, self.out, "r")["jobs"]
        self.assertTrue(all(j["setup"] is None for j in jobs))


class TestDeleteRun(RunsTestBase):
    """Deleting a run from the phone (2026-09-27): the journal always goes,
    the videos only when asked, and nothing while the run is held."""

    def setUp(self):
        super().setUp()
        self.manifest = write_run(self.batch, "r", STATE)
        (self.batch / "r.handoff.json").write_text("{}")
        (self.batch / "r.draft.json").write_text("{}")      # the chat's, not the run's
        self.out_batch = self.out / "2026-09-20-1000"
        (self.out_batch / "runs" / "a").mkdir(parents=True)
        (self.out_batch / "runs" / "a" / "01-tryon.png").write_bytes(b"x")
        (self.out_batch / "manifest.yaml").write_text("runs: []\n")
        (self.out_batch / "_final").mkdir()
        (self.out_batch / "_final" / "a.mp4").write_bytes(b"v")
        (self.out / "latest").symlink_to(self.out_batch)

    def test_keeps_videos_by_default(self):
        outcome, videos = runs.delete_run(self.batch, self.out, "r", with_videos=False)
        self.assertEqual((bool(outcome), videos), (True, 0))
        self.assertEqual(sorted(p.name for p in self.batch.iterdir()), ["r.draft.json"])
        self.assertEqual(sorted(p.name for p in self.out_batch.iterdir()), ["_final"])
        self.assertTrue((self.out_batch / "_final" / "a.mp4").exists())
        self.assertEqual(runs.list_runs(self.batch, self.out), [])

    def test_with_videos_removes_the_batch_and_the_dangling_latest_link(self):
        outcome, videos = runs.delete_run(self.batch, self.out, "r", with_videos=True)
        self.assertEqual((bool(outcome), videos), (True, 1))
        self.assertEqual(list(self.out.iterdir()), [])

    def test_refused_while_held(self):
        for name in ("drain_running", "phase_a_running"):
            with mock.patch.object(run_mod, name, return_value=True):
                outcome, _ = runs.delete_run(self.batch, self.out, "r", with_videos=True)
            self.assertEqual((bool(outcome), outcome.code, runs.status_for(outcome)),
                             (False, "run_busy", 409))
        with mock.patch.object(run_mod, "lease_for", return_value=object()):
            self.assertEqual(runs.delete_run(self.batch, self.out, "r", with_videos=True)[0].code,
                             "run_busy")
        self.assertTrue(self.manifest.exists())
        self.assertTrue((self.out_batch / "_final" / "a.mp4").exists())

    def test_unknown_or_unsafe_ids_are_not_found(self):
        for run_id in ("nope", "../r", "", "example"):
            outcome, _ = runs.delete_run(self.batch, self.out, run_id, with_videos=True)
            self.assertEqual(outcome.code, "not_found")
        (self.batch / "example.yaml").write_text("runs: []\n")   # a manifest with no journal
        self.assertEqual(runs.delete_run(self.batch, self.out, "example", with_videos=True)[0].code,
                         "not_found")
        self.assertTrue((self.batch / "example.yaml").exists())

    def test_a_batch_another_run_still_names_is_left_alone(self):
        write_run(self.batch, "other", STATE)
        runs.delete_run(self.batch, self.out, "r", with_videos=True)
        self.assertTrue((self.out_batch / "_final" / "a.mp4").exists())
        self.assertTrue((self.out_batch / "runs").exists())
