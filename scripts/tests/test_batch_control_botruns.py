"""The bot's four run functions as the phone reaches them: an Outcome back,
refusals kept off Telegram for an app call, and the app's jobs run through the
Telegram chat's one run slot without touching the Telegram draft (spec §5.8).

Everything here is free: start_drain / start_phase_a are patched, so no pod
is rented and no try-on API is called.
"""
import json, sys, tempfile, threading, time, unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.manifest import load_manifest, load_state, save_state, state_path_for
from batchlib_ext.gpu_stock import Stock
from batchlib_ext.handoff import mailbox_path
import control.drafts as drafts
from control import materials
from control.idempotency import IdempotencyStore
from control.tryon_library import TryonLibrary
from control.runs import Outcome
import tgbot.bot as bot
from tgbot.ingest import Probe
from tgbot.job import Job, write_manifest

ME = 12345

MIGRATION_TEXT = ("a volume migration is in progress for this pod's "
                  "datacenter — wait for it to finish before renting")


class FakeTg:
    """Records every send. No catch-all __getattr__: a fake that answers every
    attribute would also answer `app_origin`, and every call would look like
    the app's."""

    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    def send_message(self, chat_id, text, **kwargs):
        self.sent.append((text, kwargs))
        return len(self.sent)


def _clear_state():
    for name in ("_STATE", "_PENDING", "_LAST_VALIDATE", "_CONFIRM_WARNED",
                 "_BASKET", "_ALBUM_KEY", "_FIDELITY", "_STRIP",
                 "_PHASE_A_OFFERED"):
        getattr(bot, name).clear()


class _Fixture(unittest.TestCase):
    def setUp(self):
        self._orig_root = bot.ROOT
        self.root = Path(tempfile.mkdtemp())
        (self.root / "batch").mkdir()
        (self.root / "out").mkdir()
        bot.ROOT = self.root
        _clear_state()
        self.tg = FakeTg()
        self.patches = {}
        for name, value in (("start_drain", None), ("start_phase_a", None),
                            ("drain_running", False), ("phase_a_running", False),
                            ("migration_running", False), ("busy", False),
                            ("stock_at_cached", {}), ("volume_datacenter", "EU-RO-1"),
                            ("_start_progress", None), ("_freeze_panel", None),
                            ("log", None)):
            patcher = mock.patch(f"tgbot.bot.{name}", return_value=value)
            self.patches[name] = patcher.start()
            self.addCleanup(patcher.stop)
        # run_mod.drain_running is what _busy_reason reads.
        patcher = mock.patch("tgbot.run.drain_running", return_value=False)
        self.patches["run_mod.drain_running"] = patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        bot.ROOT = self._orig_root
        _clear_state()

    def _job(self, tag: str) -> Job:
        character = self.root / f"{tag}-character.png"
        driver = self.root / f"{tag}-driver.mp4"
        character.write_bytes(b"c")
        driver.write_bytes(b"d")
        return Job(
            slots={"character": character, "driver": driver},
            probes={"character": Probe(kind="image", width=1024, height=1024,
                                       duration_s=0.0, bitrate_kbps=0,
                                       size_bytes=800_000),
                    "driver": Probe(kind="video", width=1080, height=1920,
                                    duration_s=5.0, bitrate_kbps=3000,
                                    size_bytes=1_500_000)},
            pipeline="motion-enhance")

    def _telegram_draft(self) -> Job:
        job = self._job("telegram")
        bot._STATE[ME] = job
        bot._LAST_VALIDATE[ME] = True
        return job

    def _live(self) -> Path:
        return self.root / "batch" / f"tg-{ME}.yaml"


class TestRefusals(_Fixture):
    def test_refusal_is_sent_to_telegram_and_returned(self):
        self._telegram_draft()
        self.patches["migration_running"].return_value = True
        out = bot._do_confirm(self.tg, ME, dry_run=False)
        self.assertEqual(out, Outcome(False, "migration", MIGRATION_TEXT))
        self.assertEqual(self.tg.sent, [(MIGRATION_TEXT, {})])

    def test_app_refusal_is_returned_but_not_sent(self):
        self._telegram_draft()
        self.patches["migration_running"].return_value = True
        out = bot._do_confirm(bot._AppTg(self.tg), ME, dry_run=False)
        self.assertEqual(out, Outcome(False, "migration", MIGRATION_TEXT))
        self.assertEqual(self.tg.sent, [])

    def test_plain_strips_html(self):
        self.assertEqual(
            bot._plain('⚠️ <b>Not queued</b> — a &amp; b '
                       '<tg-emoji emoji-id="1">🚀</tg-emoji>'),
            "⚠️ Not queued — a & b 🚀")

    def test_plain_strips_the_repo_root(self):
        # A few refusals format an exception whose text starts with the
        # absolute manifest path (ManifestError does), and that text is what
        # the phone is handed.
        self.assertEqual(bot._plain(f"could not regenerate — {self.root}/batch/tg-1.yaml: broken"),
                         "could not regenerate — batch/tg-1.yaml: broken")

    def test_a_broken_manifest_refusal_names_no_absolute_path(self):
        self._telegram_draft()
        broken = f"{self.root}/batch/tg-{ME}.yaml: YAML hỏng"
        with mock.patch("tgbot.bot.load_manifest", side_effect=bot.ManifestError(broken)):
            out = bot._regen_tryon(bot._AppTg(self.tg), ME, "0", bot._run_token(ME),
                                   dry_run=False)
        self.assertEqual(out.code, "manifest_error")
        self.assertNotIn(str(self.root), out.message)
        self.assertIn("YAML hỏng", out.message)

    def test_app_tg_forwards_and_is_marked(self):
        app = bot._AppTg(self.tg)
        self.assertTrue(app.app_origin)
        self.assertFalse(getattr(self.tg, "app_origin", False))
        app.send_message(ME, "hi")
        self.assertEqual(self.tg.sent, [("hi", {})])


class TestConfirmWithAppJobs(_Fixture):
    def test_confirm_with_app_jobs_starts_once_and_leaves_the_telegram_draft_alone(self):
        draft = self._telegram_draft()
        app_jobs = [self._job("app")]
        out = bot._do_confirm(bot._AppTg(self.tg), ME, dry_run=False, jobs=app_jobs)
        self.assertEqual(out, Outcome(True, "started"))
        self.patches["start_drain"].assert_called_once()
        self.assertEqual(self.patches["start_drain"].call_args.args[0], self._live())
        manifest = load_manifest(self._live())
        # Resolved on both sides: on macOS the temp dir is under /var, a symlink
        # to /private/var, and the manifest round-trip returns the resolved form.
        self.assertEqual([Path(r.inputs["character"]).resolve() for r in manifest.runs],
                         [app_jobs[0].slots["character"].resolve()])
        self.assertIs(bot._STATE[ME], draft)
        self.assertIs(bot._LAST_VALIDATE[ME], True)
        self.patches["_freeze_panel"].assert_not_called()
        last = json.loads(bot._last_path(ME).read_text(encoding="utf-8"))
        self.assertEqual(len(last["jobs"]), 1)
        self.assertIn("app-character.png", json.dumps(last))
        self.assertNotIn("telegram-character.png", json.dumps(last))
        # A success still posts to Telegram: the chat is where progress lives.
        self.assertTrue(any("Started." in text for text, _ in self.tg.sent))

    def test_confirm_with_app_jobs_skips_the_validate_gate(self):
        self._telegram_draft()
        bot._LAST_VALIDATE[ME] = False
        out = bot._do_confirm(bot._AppTg(self.tg), ME, dry_run=False,
                              jobs=[self._job("app")])
        self.assertEqual(out, Outcome(True, "started"))
        self.patches["start_drain"].assert_called_once()

    def test_confirm_with_app_jobs_skips_the_unassigned_files_gate(self):
        self._telegram_draft()
        bot._PENDING[ME] = [Path("x.png")]
        out = bot._do_confirm(bot._AppTg(self.tg), ME, dry_run=False,
                              jobs=[self._job("app")])
        self.assertEqual(out, Outcome(True, "started"))
        self.assertEqual(bot._PENDING[ME], [Path("x.png")])
        self.assertNotIn(ME, bot._CONFIRM_WARNED)
        self.assertFalse(any("unassigned" in text for text, _ in self.tg.sent))

    def test_confirm_while_a_drain_runs_queues_and_does_not_rent(self):
        self._telegram_draft()
        self.patches["drain_running"].return_value = True
        out = bot._do_confirm(bot._AppTg(self.tg), ME, dry_run=False,
                              jobs=[self._job("app")])
        self.assertEqual(out, Outcome(True, "queued"))
        self.patches["start_drain"].assert_not_called()
        self.assertTrue((self.root / "batch" / f"tg-{ME}.next.yaml").exists())

    def test_app_confirm_does_not_overwrite_a_job_already_queued(self):
        self._telegram_draft()
        self.patches["drain_running"].return_value = True
        self.patches["busy"].return_value = True
        mailbox = mailbox_path(self._live())
        mailbox.write_text("queued: already\n", encoding="utf-8")
        out = bot._do_confirm(bot._AppTg(self.tg), ME, dry_run=False,
                              jobs=[self._job("app")])
        self.assertEqual(out.code, "queue_full")
        self.assertFalse(out)
        self.assertEqual(mailbox.read_text(encoding="utf-8"), "queued: already\n")
        self.assertEqual(self.tg.sent, [])

    def test_app_chooser_returns_choice_required_without_buttons(self):
        self._telegram_draft()
        with mock.patch("tgbot.bot._preserved_tryon", return_value=(1, 2)):
            out = bot._do_confirm(bot._AppTg(self.tg), ME, dry_run=False,
                                  jobs=[self._job("app")])
        self.assertFalse(out)
        self.assertEqual(out.code, "choice_required")
        self.assertIn("1 of 2", out.message)
        self.assertEqual(self.tg.sent, [])
        self.patches["start_drain"].assert_not_called()

    def test_telegram_chooser_returns_choice_offered_with_buttons(self):
        self._telegram_draft()
        with mock.patch("tgbot.bot._preserved_tryon", return_value=(1, 2)):
            out = bot._do_confirm(self.tg, ME, dry_run=False)
        self.assertEqual((out.ok, out.code), (False, "choice_offered"))
        self.assertIn("buttons", self.tg.sent[-1][1])
        self.patches["start_drain"].assert_not_called()


class TestPhaseA(_Fixture):
    def test_phase_a_with_app_jobs(self):
        draft = self._telegram_draft()
        out = bot._do_phase_a(bot._AppTg(self.tg), ME, dry_run=False,
                              jobs=[self._job("app")])
        self.assertEqual(out, Outcome(True, "started"))
        self.patches["start_phase_a"].assert_called_once()
        self.assertEqual(self.patches["start_phase_a"].call_args.args[0], self._live())
        self.assertIn("app-character.png", self._live().read_text(encoding="utf-8"))
        self.assertIs(bot._STATE[ME], draft)

    def test_phase_a_busy_refusal_codes(self):
        self._telegram_draft()
        self.patches["busy"].return_value = True
        self.patches["phase_a_running"].return_value = True
        out = bot._do_phase_a(bot._AppTg(self.tg), ME, dry_run=False,
                              jobs=[self._job("app")])
        self.assertEqual(out.code, "phase_a_running")
        self.patches["run_mod.drain_running"].return_value = True
        out = bot._do_phase_a(bot._AppTg(self.tg), ME, dry_run=False,
                              jobs=[self._job("app")])
        self.assertEqual(out.code, "drain_running")
        self.patches["start_phase_a"].assert_not_called()
        self.assertEqual(self.tg.sent, [])


class TestResume(_Fixture):
    def _journal(self):
        bot._do_confirm(self.tg, ME, dry_run=False, jobs=[self._job("app")])
        self.patches["start_drain"].reset_mock()
        from batchlib.manifest import state_path_for
        state_path_for(self._live()).write_text(
            json.dumps({"batch": "2026-09-21-1200", "runs": {}}), encoding="utf-8")

    def test_resume_outcome_truthiness(self):
        self._journal()
        self.patches["migration_running"].return_value = True
        out = bot._do_resume(self.tg, ME, self._live(), dry_run=False)
        self.assertFalse(out)
        self.assertEqual(out.code, "migration")
        self.patches["migration_running"].return_value = False
        out = bot._do_resume(self.tg, ME, self._live(), dry_run=False)
        self.assertTrue(out)
        self.assertEqual(out, Outcome(True, "started"))
        self.patches["start_drain"].assert_called_once()


class TestRegen(_Fixture):
    def test_regen_stale_token(self):
        out = bot._regen_tryon(bot._AppTg(self.tg), ME, "0", "not-the-token",
                               dry_run=False)
        self.assertEqual((out.ok, out.code), (False, "stale_panel"))
        self.assertEqual(self.tg.sent, [])


class _AppRunsFixture(_Fixture):
    """A real DraftStore and a real IdempotencyStore over the fixture's temp
    `batch/`, wired into one AppRuns for chat ME — everything AppRuns itself
    is built from, none of it faked."""

    def setUp(self):
        super().setUp()
        staging = self.root / "batch" / "tg-staging"
        staging.mkdir(parents=True, exist_ok=True)
        self.store = drafts.DraftStore(
            self.root / "batch", staging, "app",
            default_pipeline="motion-enhance", default_provider="gemini",
            tryon_library=TryonLibrary(self.root / "batch" / "tryon-library", "app"))
        self.idem = IdempotencyStore(self.root / "batch" / "idempotency")
        self.runs = bot.AppRuns(self.tg, ME, self.store, self.idem)

    def _seed_draft(self, *, validated: bool = True) -> Job:
        # Straight into the store's own persistence rather than patch(): the
        # app's job needs no staged material for these tests, only files
        # DraftStore's completeness check (_missing) can stat.
        job = self._job("app")
        d = self.store._load()
        d.job = job
        d.validated = validated
        self.store._save(d)
        return job

    def _seed_journal(self, job: Job) -> None:
        """A live manifest plus a journal recording a batch id — the proof
        _do_resume requires that this chat's manifest was already confirmed
        once (see its own docstring), so AppRuns.confirm's resume branch has
        something real to resume."""
        write_manifest([job], self._live(), now=time.strftime("%Y-%m-%d %H:%M:%S"))
        state_path_for(self._live()).write_text(
            json.dumps({"batch": "2026-09-21-1200", "runs": {}}), encoding="utf-8")

    def _body(self, provider: str = "runpod", tryon=None) -> dict:
        return {"provider": provider, "tryon": tryon, "panel_token": self.runs.panel_token()}

    def _tryon_job(self, tag: str = "tryon") -> Job:
        """A job whose pipeline has a LOCAL try-on stage. `motion-enhance`
        (every other job in this file) has none — `tryon-motion-enhance`
        does, and needs `provider="gemini"` explicit: the Job dataclass
        default (`qwen`, self-host) is not in LOCAL_PROVIDERS."""
        character = self.root / f"{tag}-character.png"
        driver = self.root / f"{tag}-driver.mp4"
        outfit = self.root / f"{tag}-outfit.png"
        for path, data in ((character, b"c"), (driver, b"d"), (outfit, b"o")):
            path.write_bytes(data)
        return Job(
            slots={"character": character, "driver": driver, "outfit": outfit},
            probes={"character": Probe(kind="image", width=1024, height=1024,
                                       duration_s=0.0, bitrate_kbps=0, size_bytes=800_000),
                    "driver": Probe(kind="video", width=1080, height=1920,
                                    duration_s=5.0, bitrate_kbps=3000, size_bytes=1_500_000),
                    "outfit": Probe(kind="image", width=1024, height=1024,
                                    duration_s=0.0, bitrate_kbps=0, size_bytes=800_000)},
            pipeline="tryon-motion-enhance", provider="gemini")

    def _write_live_manifest(self, job: Job):
        write_manifest([job], self._live(), now=time.strftime("%Y-%m-%d %H:%M:%S"))
        return load_manifest(self._live())

    def _write_journal(self, run_id: str, *, batch: str = "batch1", **stages) -> None:
        state_path_for(self._live()).write_text(
            json.dumps({"batch": batch, "runs": {run_id: {"status": "running",
                                                           "stages": stages}}}),
            encoding="utf-8")


    def _grouped_jobs(self) -> list[Job]:
        """2 outfits x 2 drivers, one character, same provider — the runner's
        tryon_share_key (character, outfit, background, params; driver is
        excluded because this pipeline's `tryon` stage is not camera-aware)
        groups the two drivers of each outfit, o1d1/o2d1 leading."""
        character = self.root / "grp-character.png"
        character.write_bytes(b"c")
        jobs = []
        for outfit_n in (1, 2):
            outfit = self.root / f"grp-outfit{outfit_n}.png"
            outfit.write_bytes(b"o")
            for driver_n in (1, 2):
                driver = self.root / f"grp-driver{outfit_n}-{driver_n}.mp4"
                driver.write_bytes(b"d")
                jobs.append(Job(
                    slots={"character": character, "driver": driver, "outfit": outfit},
                    probes={"character": Probe(kind="image", width=1024, height=1024,
                                               duration_s=0.0, bitrate_kbps=0,
                                               size_bytes=800_000),
                            "driver": Probe(kind="video", width=1080, height=1920,
                                            duration_s=5.0, bitrate_kbps=3000,
                                            size_bytes=1_500_000),
                            "outfit": Probe(kind="image", width=1024, height=1024,
                                            duration_s=0.0, bitrate_kbps=0,
                                            size_bytes=800_000)},
                    pipeline="tryon-motion-enhance", provider="gemini"))
        return jobs


class TestAppRunsPhaseA(_AppRunsFixture):
    def test_phase_a_requires_a_validated_draft(self):
        self._seed_draft(validated=False)
        status, body = self.runs.phase_a("k1")
        self.assertEqual(status, 422)
        self.assertEqual(body["error"]["code"], "not_validated")
        self.patches["start_phase_a"].assert_not_called()

    def test_phase_a_starts_and_replay_does_not_start_twice(self):
        self._seed_draft(validated=True)
        first = self.runs.phase_a("same-key")
        second = self.runs.phase_a("same-key")
        self.assertEqual(first[0], 202)
        self.assertEqual(second, first)
        self.patches["start_phase_a"].assert_called_once()


class TestAppRunsConfirm(_AppRunsFixture):
    def test_confirm_replayed_with_the_same_key_calls_start_drain_once(self):
        self._seed_draft(validated=True)
        body = self._body()
        first = self.runs.confirm(self.runs.run_id, body, "same-key")
        second = self.runs.confirm(self.runs.run_id, body, "same-key")
        self.assertEqual(first[0], 202)
        self.assertEqual(second, first)
        self.patches["start_drain"].assert_called_once()

    def test_confirm_with_a_stale_panel_token_is_409(self):
        self._seed_draft(validated=True)
        body = {"provider": "runpod", "tryon": None, "panel_token": "not-the-token"}
        status, resp = self.runs.confirm(self.runs.run_id, body, "k")
        self.assertEqual(status, 409)
        self.assertEqual(resp["error"]["code"], "stale_panel")
        self.patches["start_drain"].assert_not_called()

    def test_confirm_with_a_different_gpu_is_stale_panel_and_spends_nothing(self):
        # The price the phone showed was for one GPU (spec 5.9); .env's GPU
        # can have moved since (PUT /v1/pod/gpu, or Telegram's own switch).
        (self.root / ".env").write_text("GPU=NVIDIA GeForce RTX 4090\n", encoding="utf-8")
        self._seed_draft(validated=True)
        body = dict(self._body(), gpu="NVIDIA GeForce RTX 5090")
        status, resp = self.runs.confirm(self.runs.run_id, body, "k-mismatch")
        self.assertEqual((status, resp["error"]["code"]), (409, "stale_panel"))
        self.patches["start_drain"].assert_not_called()

        # A matching gpu proceeds, as does an omitted one (an older client).
        body = dict(self._body(), gpu="NVIDIA GeForce RTX 4090")
        status, _ = self.runs.confirm(self.runs.run_id, body, "k-match")
        self.assertEqual(status, 202)
        self.assertEqual(self.patches["start_drain"].call_count, 1)

        self._seed_draft(validated=True)
        status, _ = self.runs.confirm(self.runs.run_id, self._body(), "k-omitted")
        self.assertEqual(status, 202)
        self.assertEqual(self.patches["start_drain"].call_count, 2)

    def test_two_concurrent_confirms_make_one_drain(self):
        self._seed_draft(validated=True)
        # Read once, before either thread starts — the phone would have read
        # one panel and fired two retries of what it believes is one tap.
        token = self.runs.panel_token()
        results = {}

        def call(key: str) -> None:
            results[key] = self.runs.confirm(
                self.runs.run_id,
                {"provider": "runpod", "tryon": None, "panel_token": token}, key)

        threads = [threading.Thread(target=call, args=(key,))
                  for key in ("key-1", "key-2")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)
        self.patches["start_drain"].assert_called_once()
        self.assertEqual(sorted(status for status, _ in results.values()), [202, 409])
        refused = next(body for status, body in results.values() if status == 409)
        self.assertEqual(refused["error"]["code"], "stale_panel")

    def test_confirm_after_phase_a_resumes(self):
        job = self._seed_draft(validated=True)
        self._seed_journal(job)
        bot._PHASE_A_OFFERED[ME] = bot._run_token(ME)
        status, _ = self.runs.confirm(self.runs.run_id, self._body(), "k")
        self.assertEqual(status, 202)
        self.patches["start_drain"].assert_called_once()
        self.assertTrue(self.patches["start_drain"].call_args.kwargs.get("resume"))
        self.assertNotIn(ME, bot._PHASE_A_OFFERED)
        self.assertEqual(self.store.view()["jobs"], 0)

    def test_confirm_clears_the_app_draft_only_on_success(self):
        self._seed_draft(validated=True)
        before = self.store.view()
        self.patches["migration_running"].return_value = True
        status, body = self.runs.confirm(self.runs.run_id, self._body(), "k")
        self.assertEqual(status, 409)
        self.assertEqual(body["error"]["code"], "migration")
        self.patches["start_drain"].assert_not_called()
        self.assertEqual(self.store.view(), before)

    def test_crash_midway_leaves_the_key_pending(self):
        self._seed_draft(validated=True)
        body = self._body()
        self.patches["start_drain"].side_effect = RuntimeError("boom")
        with self.assertRaises(RuntimeError):
            self.runs.confirm(self.runs.run_id, body, "k")
        status, resp = self.runs.confirm(self.runs.run_id, body, "k")
        self.assertEqual(status, 409)
        self.assertEqual(resp["error"]["code"], "outcome_unknown")
        self.patches["start_drain"].assert_called_once()

    def test_bot_busy_is_503_and_retryable(self):
        self._seed_draft(validated=True)
        body = self._body()
        held, release = threading.Event(), threading.Event()

        def hold() -> None:
            with bot.BOT_LOCK:
                held.set()
                release.wait(5)

        t = threading.Thread(target=hold)
        t.start()
        try:
            self.assertTrue(held.wait(5))
            with mock.patch.object(bot, "BOT_LOCK_TIMEOUT_SEC", 0.2):
                status, resp = self.runs.confirm(self.runs.run_id, body, "k")
        finally:
            release.set()
            t.join(5)
        self.assertEqual(status, 503)
        self.assertEqual(resp["error"]["code"], "bot_busy")
        # Not recorded — forgotten, so the exact same key may retry rather
        # than read back an "outcome_unknown" for a call that never ran.
        status, resp = self.runs.confirm(self.runs.run_id, body, "k")
        self.assertEqual(status, 202)
        self.patches["start_drain"].assert_called_once()

    def test_confirm_wrong_run_id_is_404(self):
        self._seed_draft(validated=True)
        status, resp = self.runs.confirm("not-the-run-id", self._body(), "k")
        self.assertEqual(status, 404)
        self.assertEqual(resp["error"]["code"], "not_found")
        self.patches["start_drain"].assert_not_called()

    def test_confirm_bad_provider_is_400(self):
        self._seed_draft(validated=True)
        status, resp = self.runs.confirm(self.runs.run_id, {"provider": "nope"}, "k")
        self.assertEqual(status, 400)
        self.assertEqual(resp["error"]["code"], "bad_request")
        self.patches["start_drain"].assert_not_called()

    # -- the confirm stamp (2026-09-24 follow-ups spec §3) ------------------

    def test_an_accepted_fresh_confirm_stamps_the_generation(self):
        self._seed_draft()
        with mock.patch("tgbot.bot._do_confirm",
                        return_value=Outcome(True, "started")) as do_confirm:
            status, _body = self.runs.confirm(self.runs.run_id, self._body(), "k1")
        self.assertEqual(status, 202)
        do_confirm.assert_called_once()
        # The draft's generation NOW, not the one the panel token certified:
        # `clear()` counts the generation up (`_changed`, drafts.py:275-279)
        # and `resume` compares the stamp against the draft as it stands, so
        # the stamp has to be written in that same frame.
        self.assertEqual(bot._load_confirm_stamp(ME), self.store.runnable()[2])

    def test_an_accepted_resume_branch_confirm_stamps_the_generation(self):
        """The other accepted branch (bot.py:7017-7029). It reaches `_do_resume`
        rather than `_do_confirm`, and it clears the draft too, so it must stamp
        as well — otherwise a rental confirmed straight after Phase A has no
        latch at all."""
        job = self._seed_draft()
        self._seed_journal(job)
        bot._PHASE_A_OFFERED[ME] = bot._run_token(ME)
        with mock.patch("tgbot.bot._do_resume",
                        return_value=Outcome(True, "started")) as do_resume:
            status, _body = self.runs.confirm(self.runs.run_id, self._body(), "k2")
        self.assertEqual(status, 202)
        do_resume.assert_called_once()
        self.assertEqual(bot._load_confirm_stamp(ME), self.store.runnable()[2])

    def test_the_confirms_own_clear_leaves_the_stamp_matching_the_draft_so_a_retry_is_allowed(self):
        """The trap in spec §2, pinned. `confirm` clears the draft on
        acceptance (bot.py:7029 and :7039) and `clear()` counts the generation
        UP by one (`_changed`, drafts.py:275-279) — it does not reset it and it
        does not stand still. So the stamp has to be written after the clear:
        written before it, the stamp is one behind the draft the confirm left
        on disk, `_resume_generation_refusal` answers "moved" for a draft
        nobody touched, and Retry rental breaks for every user while every
        other gate stays green.

        Asserted through the real reader half, not by re-deriving its rule, so
        this fails if either side moves. The generation starts non-zero on
        purpose: from 0 a reset and a no-op are the same number and neither
        would be caught.

        The two `before + 1` assertions are what carry that weight, and they
        are deliberately redundant with the reader-based one below them: because
        the stamp is read AFTER the clear, a `clear()` that reset the counter
        to 0 would leave stamp and draft agreeing at 0, the draft would still
        be empty, and `_resume_generation_refusal` would still answer None.
        Measured 2026-09-24 under exactly that mutation: the emptiness and the
        reader assertions both PASS, and only the two anchored to `before` fail
        (0 != 8). So only those pin the "counter only rises, so a stale stamp
        can never match again by accident" property that
        `_resume_generation_refusal`'s docstring relies on. The cost is that a
        legitimate future `clear()` that preserved the generation would fail
        here, in a test named for the retry; that is the point — it forces the
        change to be made with this invariant in view."""
        self._seed_draft()
        d = self.store._load()
        d.generation = 7          # a draft the user has edited several times
        self.store._save(d)
        before = self.store.runnable()[2]
        self.assertEqual(before, 7)
        with mock.patch("tgbot.bot._do_confirm",
                        return_value=Outcome(True, "started")):
            self.assertEqual(self.runs.confirm(self.runs.run_id, self._body(), "k3")[0], 202)
        self.assertEqual(self.store.runnable()[0], [])        # the draft really is empty
        self.assertEqual(self.store.runnable()[2], before + 1)  # ...and counted up, not reset
        self.assertEqual(bot._load_confirm_stamp(ME), before + 1)
        self.assertIsNone(bot._resume_generation_refusal(ME, self.store))

    def test_a_refused_confirm_stamps_nothing(self):
        self._seed_draft()
        body = self._body()
        body["panel_token"] = "not-the-token"
        status, resp = self.runs.confirm(self.runs.run_id, body, "k4")
        self.assertEqual(status, 409)
        self.assertEqual(resp["error"]["code"], "stale_panel")
        self.patches["start_drain"].assert_not_called()
        self.assertIsNone(bot._load_confirm_stamp(ME))

    def test_a_not_validated_confirm_stamps_nothing(self):
        self._seed_draft(validated=False)
        with mock.patch("tgbot.bot._do_confirm") as do_confirm:
            status, resp = self.runs.confirm(self.runs.run_id, self._body(), "k5")
        self.assertEqual(status, 422)
        self.assertEqual(resp["error"]["code"], "not_validated")
        do_confirm.assert_not_called()
        self.assertIsNone(bot._load_confirm_stamp(ME))

    def test_an_unwritable_stamp_still_reports_the_spend_it_already_made(self):
        """The stamp write sits between an accepted, money-committed confirm and
        its 202. An OSError escaping there skips `idem.finish` and reaches
        httpapi/server.py's catch-all, which answers `500 internal` for a spend
        that already called `start_drain` — the user is told nothing about a pod
        that is being rented, and SpendGate will not take a first 5xx as the
        server's answer, so the phone re-checks a rental that is already running.
        Failing open costs only the latch, which is what
        `_resume_generation_refusal` already does when no stamp exists.

        The real `_do_confirm` runs here (only `start_drain` is patched, by the
        fixture), so the spend really happens and the assertion is that it is
        *reported*, not avoided. The replay is the load-bearing half: it is what
        proves `idem.finish` recorded the 202 rather than leaving the key
        pending, which is the `409 outcome_unknown` that
        `test_crash_midway_leaves_the_key_pending` pins for a raised spend."""
        self._seed_draft(validated=True)
        body = self._body()          # read once; the replay must not need a fresh one
        with mock.patch("tgbot.bot._save_confirm_stamp",
                        side_effect=OSError(28, "No space left on device")):
            first = self.runs.confirm(self.runs.run_id, body, "k6")
            second = self.runs.confirm(self.runs.run_id, body, "k6")
        self.assertEqual(first[0], 202)
        self.assertEqual(first[1]["outcome"], "started")
        self.assertEqual(second, first)          # recorded, not left pending
        self.patches["start_drain"].assert_called_once()
        # The latch is the only thing lost, and it is lost loudly rather than
        # silently: no stamp, so a later resume fails open instead of refusing
        # against a number nobody wrote.
        self.assertIsNone(bot._load_confirm_stamp(ME))
        self.assertIsNone(bot._resume_generation_refusal(ME, self.store))
        self.assertTrue(any("confirm stamp" in str(call)
                            for call in self.patches["log"].call_args_list))


class TestConfirmAfterADroppedBatchJob(_Fixture):
    """§5.10: dropping a job from the draft's basket after Phase A already ran
    for it must shrink what gets rented, not silently rent the stale,
    already-on-disk manifest via the resume branch."""

    def setUp(self):
        super().setUp()
        self.staging = self.root / "batch" / "tg-staging"
        (self.staging / "app").mkdir(parents=True, exist_ok=True)
        self.store = drafts.DraftStore(
            self.root / "batch", self.staging, "app",
            default_pipeline="tryon-motion-enhance", default_provider="gemini",
            tryon_library=TryonLibrary(self.root / "batch" / "tryon-library", "app"),
            probe=self._probe)
        self.idem = IdempotencyStore(self.root / "batch" / "idempotency")
        self.runs = bot.AppRuns(self.tg, ME, self.store, self.idem)

    @staticmethod
    def _probe(path: Path) -> Probe:
        if path.suffix == ".mp4":
            return Probe(kind="video", width=1080, height=1920, duration_s=5.0,
                        bitrate_kbps=3000, size_bytes=10)
        return Probe(kind="image", width=1080, height=1920, duration_s=0.0,
                    bitrate_kbps=0, size_bytes=10)

    def _material(self, name: str, data: bytes) -> str:
        (self.staging / "app" / name).write_bytes(data)
        return f"app/{name}"

    def _mark_validated(self) -> None:
        # Same shortcut _AppRunsFixture._seed_draft uses: the app already
        # validated its draft before calling Phase A/confirm, so these tests
        # go straight to a validated draft rather than shelling out to the
        # real `make batch-validate` (that path belongs to
        # test_batch_control_drafts.py's TestValidate).
        d = self.store._load()
        d.validated = True
        self.store._save(d)

    def test_a_dropped_job_is_not_rented(self):
        character = self._material("character.png", b"c")
        driver = self._material("driver.mp4", b"d")
        outfit1 = self._material("outfit1.png", b"o1")
        outfit2 = self._material("outfit2.png", b"o2")

        # Two distinct basket entries, differing only by outfit — the exact
        # PATCH/add-to-batch sequence test_batch_control_drafts.py's TestBatch
        # uses to build a runnable draft (e.g. test_add_keeps_editing_a_copy).
        self.store.patch({"slots": {"character": character, "driver": driver,
                                    "outfit": outfit1}})
        self.store.add_to_batch()
        self.store.patch({"slots": {"outfit": outfit2}})
        self.store.add_to_batch()
        # Clear the slot still being edited so the draft's runnable jobs are
        # exactly the two basket entries, not the basket plus whatever the
        # (now-unsaved) current job happens to duplicate.
        self.store.patch({"slots": {"outfit": None}})
        self._mark_validated()

        with mock.patch("tgbot.bot.start_phase_a"):
            status, _ = self.runs.phase_a("k1")
        self.assertEqual(status, 202)
        manifest = load_manifest(bot._job_manifest_path(ME))
        self.assertEqual(len(manifest.runs), 2)

        # Phase A "finished": mark both runs' try-on stage done in the
        # journal, as a real Phase A run would, then set the offered-token
        # gate its own completion callback sets — start_phase_a is stubbed
        # above, so nothing else does that here.
        state_file = state_path_for(manifest.path)
        state = {"version": 1, "batch": "2026-09-22-0000", "runs": {}}
        for run in manifest.runs:
            out_file = self.root / f"{run.id}.png"
            out_file.write_bytes(b"img")
            state["runs"][run.id] = {"status": "done", "stages": {
                "tryon": {"status": "done", "file": str(out_file),
                          "params_manifest": {}}}}
        save_state(state_file, state)
        bot._PHASE_A_OFFERED[ME] = bot._run_token(ME)

        # Drop the second basket entry (the outfit2 job) — the app's DELETE
        # /v1/draft/batch/{digest}.
        view = self.store.view()
        self.assertEqual(len(view["batch"]), 2)
        self.store.drop_from_batch(view["batch"][1]["digest"])
        self._mark_validated()

        with mock.patch("tgbot.bot.start_drain") as fake_start_drain:
            status, body = self.runs.confirm(
                self.runs.run_id,
                {"provider": "runpod", "panel_token": self.runs.panel_token()}, "k2")

        self.assertEqual(status, 202)
        self.assertEqual(body["outcome"], "started")
        # The rewritten manifest has one run, not two — _do_confirm re-wrote
        # it for the CURRENT (shrunk) draft instead of _do_resume re-renting
        # the stale, two-run manifest still on disk.
        rewritten = load_manifest(bot._job_manifest_path(ME))
        self.assertEqual(len(rewritten.runs), 1)
        self.assertEqual(rewritten.runs[0].id, manifest.runs[0].id)
        # And it is the shrunk manifest that actually got rented, not just
        # rewritten and ignored.
        fake_start_drain.assert_called_once()
        self.assertEqual(fake_start_drain.call_args.args[0], bot._job_manifest_path(ME))


class TestPhaseAMatchesDraft(_AppRunsFixture):
    """§5.10: `_phase_a_matches_draft` is the one predicate that decides
    whether a confirm resumes the manifest already on disk or re-derives it.
    Run ids alone are not enough — `run_id_for` hashes material file stems
    (job.py:107), so a provider switch or a try-on seed added after Phase A
    leaves every id identical while changing what the run actually costs and
    produces. That is `signature()`'s identity (drafts.py:78), and this
    predicate has to use the same one.
    """

    def _seed_jobs(self, jobs: list[Job], *, validated: bool = True) -> None:
        # Into the basket, not `d.job`: the basket is what `_jobs()` reports
        # verbatim, while the current job is only counted when complete — a
        # fresh (empty) `d.job` keeps these tests to exactly `jobs`.
        d = self.store._load()
        d.basket = list(jobs)
        d.validated = validated
        self.store._save(d)

    def test_an_unchanged_draft_still_matches(self):
        job = self._tryon_job()
        self._seed_jobs([job])
        self._write_live_manifest(job)
        self.assertTrue(self.runs._phase_a_matches_draft())

    def test_a_provider_switch_with_the_same_material_does_not_match(self):
        job = self._tryon_job()
        self._write_live_manifest(job)
        # Same four files, so the same run id — only the provider moved.
        self._seed_jobs([replace(job, provider="qwen-max")])
        self.assertFalse(self.runs._phase_a_matches_draft())

    def test_a_seed_added_after_phase_a_does_not_match(self):
        job = self._tryon_job()
        self._write_live_manifest(job)
        seed = self.root / "saved-tryon.png"
        seed.write_bytes(b"s")
        self._seed_jobs([replace(job, tryon_seed=seed)])
        self.assertFalse(self.runs._phase_a_matches_draft())

    def test_a_seed_cleared_after_phase_a_does_not_match(self):
        seed = self.root / "saved-tryon.png"
        seed.write_bytes(b"s")
        job = self._tryon_job()
        self._write_live_manifest(replace(job, tryon_seed=seed))
        self._seed_jobs([job])
        self.assertFalse(self.runs._phase_a_matches_draft())

    def test_a_confirm_after_a_provider_switch_re_derives_the_manifest(self):
        # The money consequence of the above: the resume branch would rent the
        # manifest still naming gemini for a draft the user moved to qwen-max.
        job = self._tryon_job()
        self._write_live_manifest(job)
        state_path_for(self._live()).write_text(
            json.dumps({"batch": "2026-09-22-0000", "runs": {}}), encoding="utf-8")
        bot._PHASE_A_OFFERED[ME] = bot._run_token(ME)
        self._seed_jobs([replace(job, provider="qwen-max")])

        status, body = self.runs.confirm(
            self.runs.run_id,
            {"provider": "runpod", "panel_token": self.runs.panel_token()}, "k1")
        self.assertEqual((status, body["outcome"]), (202, "started"))
        rewritten = load_manifest(bot._job_manifest_path(ME))
        self.assertEqual(rewritten.runs[0].stage_params["tryon"]["provider"], "qwen-max")


class TestBotLoopLocking(_AppRunsFixture):
    def test_handle_and_ticks_hold_the_bot_lock(self):
        seen = []

        def probe_from_another_thread(*_args, **_kwargs) -> None:
            # RLock is per-thread reentrant, so the probe has to run on a
            # different thread than the one holding BOT_LOCK — this thread
            # could always re-acquire its own lock.
            acquired = []

            def worker() -> None:
                got = bot.BOT_LOCK.acquire(blocking=False)
                acquired.append(got)
                if got:
                    bot.BOT_LOCK.release()

            t = threading.Thread(target=worker)
            t.start(); t.join(5)
            seen.append(acquired[0])

        with mock.patch("tgbot.bot.handle", side_effect=probe_from_another_thread):
            bot._handle_locked(self.tg, {"update_id": 1}, allowed_user_id=ME, dry_run=False)
        with mock.patch("tgbot.bot.tick_progress", side_effect=probe_from_another_thread):
            bot._run_ticks(self.tg, ME, dry_run=False)
        self.assertEqual(seen, [False, False])


class TestRentPanel(_AppRunsFixture):
    """`AppRuns.rent_panel` — the same runpodctl/Vast primitives
    `_offer_run_confirm`/`_offer_vast_panel` read, minus the Telegram send."""

    def _stock(self, status: str = "High", price: float = 0.99) -> dict:
        return {"NVIDIA GeForce RTX 5090": [
            Stock(gpu_id="NVIDIA GeForce RTX 5090", display_name="RTX 5090",
                  datacenter_id="EU-RO-1", stock_status=status, price_per_hr=price)]}

    def test_rent_panel_in_stock(self):
        self._seed_draft(validated=True)
        with mock.patch("tgbot.bot.volume_datacenter", return_value="EU-RO-1"), \
             mock.patch("tgbot.bot.stock_at_cached", return_value=self._stock()), \
             mock.patch("tgbot.bot.vast_fetch_quote", side_effect=RuntimeError("no vastai")), \
             mock.patch("tgbot.bot.vast_credit", return_value=25.0):
            status, body = self.runs.rent_panel(self.runs.run_id, force=False)
        self.assertEqual(status, 200)
        self.assertEqual(body["runpod"], {
            "gpu": "NVIDIA GeForce RTX 5090", "datacenter": "EU-RO-1",
            "stock": "High", "usd_per_hr": 0.99, "sold_out": False})
        self.assertFalse(body["after_phase_a"])
        self.assertEqual(body["jobs"], 1)
        self.assertGreater(body["estimate_min"], 0)
        self.assertEqual(body["run_id"], self.runs.run_id)
        self.assertEqual(body["panel_token"], self.runs.panel_token())

    def test_rent_panel_sold_out_and_runpodctl_failure_fail_open(self):
        self._seed_draft(validated=True)
        with mock.patch("tgbot.bot.volume_datacenter", return_value="EU-RO-1"), \
             mock.patch("tgbot.bot.stock_at_cached", side_effect=RuntimeError("runpodctl down")), \
             mock.patch("tgbot.bot.vast_fetch_quote", side_effect=RuntimeError("no vastai")), \
             mock.patch("tgbot.bot.vast_credit", return_value=25.0):
            status, body = self.runs.rent_panel(self.runs.run_id, force=False)
        self.assertEqual(status, 200)
        self.assertTrue(body["runpod"]["sold_out"])
        self.assertIsNone(body["runpod"]["stock"])

    def test_rent_panel_never_prices_the_telegram_draft(self):
        # _draft_manifest(chat_id) with no jobs falls back to _jobs_for(chat_id),
        # i.e. the TELEGRAM chat's own draft. With the app's draft empty (or
        # unvalidated) and a Telegram draft sitting in _STATE, the panel must
        # describe nothing rather than price the Telegram user's job.
        telegram = self._job("telegram")
        bot._STATE[ME] = telegram
        bot._LAST_VALIDATE[ME] = True
        with mock.patch("tgbot.bot.volume_datacenter", return_value="EU-RO-1"), \
             mock.patch("tgbot.bot.stock_at_cached", return_value=self._stock()), \
             mock.patch("tgbot.bot.vast_fetch_quote", side_effect=RuntimeError("no vastai")), \
             mock.patch("tgbot.bot.vast_credit", return_value=25.0), \
             mock.patch("tgbot.bot._draft_manifest") as draft_manifest:
            status, body = self.runs.rent_panel(self.runs.run_id, force=False)
        self.assertEqual(status, 200)
        self.assertEqual((body["jobs"], body["estimate_min"]), (0, 0))
        draft_manifest.assert_not_called()

    def test_rent_panel_prices_the_draft_when_it_changed_since_phase_a(self):
        # The panel and `confirm` must never disagree about what is being
        # rented: `confirm` re-derives from the draft once
        # `_phase_a_matches_draft()` is False, so a panel still reporting the
        # one-run manifest on disk would quote half the jobs (and half the
        # minutes) of what the spend actually starts.
        first, second = self._job("app-a"), self._job("app-b")
        self._write_live_manifest(first)
        bot._PHASE_A_OFFERED[ME] = bot._run_token(ME)
        d = self.store._load()
        d.basket = [first, second]
        d.validated = True
        self.store._save(d)

        with mock.patch("tgbot.bot.volume_datacenter", return_value="EU-RO-1"), \
             mock.patch("tgbot.bot.stock_at_cached", return_value=self._stock()), \
             mock.patch("tgbot.bot.vast_fetch_quote", side_effect=RuntimeError("no vastai")), \
             mock.patch("tgbot.bot.vast_credit", return_value=25.0):
            status, body = self.runs.rent_panel(self.runs.run_id, force=False)
        self.assertEqual(status, 200)
        self.assertFalse(body["after_phase_a"])
        self.assertEqual(body["jobs"], 2)
        self.assertEqual(body["estimate_min"],
                         sum(bot.estimate_minutes(j) for j in (first, second)))

    def test_rent_panel_still_reads_the_manifest_when_the_draft_is_unchanged(self):
        # The regression the fix above must not cause: an untouched draft
        # still prices the manifest Phase A already ran for.
        job = self._tryon_job()
        self._write_live_manifest(job)
        bot._PHASE_A_OFFERED[ME] = bot._run_token(ME)
        d = self.store._load()
        d.basket = [job]
        d.validated = True
        self.store._save(d)

        with mock.patch("tgbot.bot.volume_datacenter", return_value="EU-RO-1"), \
             mock.patch("tgbot.bot.stock_at_cached", return_value=self._stock()), \
             mock.patch("tgbot.bot.vast_fetch_quote", side_effect=RuntimeError("no vastai")), \
             mock.patch("tgbot.bot.vast_credit", return_value=25.0):
            _, body = self.runs.rent_panel(self.runs.run_id, force=False)
        self.assertTrue(body["after_phase_a"])
        self.assertEqual(body["jobs"], 1)

    def test_rent_panel_token_matches_what_confirm_accepts(self):
        self._seed_draft(validated=True)
        with mock.patch("tgbot.bot.volume_datacenter", return_value="EU-RO-1"), \
             mock.patch("tgbot.bot.stock_at_cached", return_value=self._stock()), \
             mock.patch("tgbot.bot.vast_fetch_quote", side_effect=RuntimeError("no vastai")), \
             mock.patch("tgbot.bot.vast_credit", return_value=25.0):
            _, body = self.runs.rent_panel(self.runs.run_id, force=False)
        confirm_body = {"provider": "runpod", "tryon": None, "panel_token": body["panel_token"]}
        status, resp = self.runs.confirm(self.runs.run_id, confirm_body, "k")
        self.assertEqual(status, 202)
        self.patches["start_drain"].assert_called_once()

    def test_rent_panel_network_runs_outside_the_bot_lock(self):
        self._seed_draft(validated=True)
        seen = []

        def probing_stock(*_args, **_kwargs):
            # A second thread, same reasoning as TestBotLoopLocking: BOT_LOCK
            # is an RLock, so this thread re-acquiring its own hold would
            # always succeed and prove nothing.
            acquired = []

            def worker() -> None:
                got = bot.BOT_LOCK.acquire(blocking=False)
                acquired.append(got)
                if got:
                    bot.BOT_LOCK.release()

            t = threading.Thread(target=worker)
            t.start(); t.join(5)
            seen.append(acquired[0])
            return self._stock()

        with mock.patch("tgbot.bot.volume_datacenter", return_value="EU-RO-1"), \
             mock.patch("tgbot.bot.stock_at_cached", side_effect=probing_stock), \
             mock.patch("tgbot.bot.vast_fetch_quote", side_effect=RuntimeError("no vastai")), \
             mock.patch("tgbot.bot.vast_credit", return_value=25.0):
            status, _ = self.runs.rent_panel(self.runs.run_id, force=False)
        self.assertEqual(status, 200)
        self.assertEqual(seen, [True])

    def test_rent_panel_vast_blockers_are_plain_text(self):
        self._seed_draft(validated=True)   # motion-enhance, no VAST_ENABLED_PIPELINES set
        with mock.patch("tgbot.bot.volume_datacenter", return_value="EU-RO-1"), \
             mock.patch("tgbot.bot.stock_at_cached", return_value=self._stock()), \
             mock.patch("tgbot.bot.vast_fetch_quote", side_effect=RuntimeError("no vastai")), \
             mock.patch("tgbot.bot.vast_credit", return_value=25.0):
            _, body = self.runs.rent_panel(self.runs.run_id, force=False)
        self.assertTrue(body["vast"]["blockers"])
        for reason in body["vast"]["blockers"]:
            self.assertIsInstance(reason, str)
            self.assertNotIn("<", reason)
        self.assertFalse(body["vast"]["can_spend"])


class TestTryonPreviews(_AppRunsFixture):
    """`AppRuns.tryon`/`tryon_image` — the journal `_deliver_tryon_previews`
    itself reads, without the Telegram send."""

    def test_tryon_lists_previews_and_image_path_stays_in_out(self):
        job = self._tryon_job()
        manifest = self._write_live_manifest(job)
        run_id = manifest.runs[0].id
        image = self.root / "out" / "batch1" / "runs" / run_id / "01-tryon.png"
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b"img")
        self._write_journal(run_id, tryon={"status": "done", "file": str(image)})

        status, body = self.runs.tryon(self.runs.run_id)
        self.assertEqual(status, 200)
        self.assertEqual(body["run_token"], bot._run_token(ME))
        self.assertFalse(body["phase_a_running"])
        self.assertEqual(body["previews"],
                         [{"index": "0", "run": run_id, "status": "done", "has_image": True,
                           "shared_from": None, "shares": []}])

        got = self.runs.tryon_image(self.runs.run_id, "0")
        self.assertEqual(got, image.resolve())

        # The same slot, now pointing outside out/ — tryon_image refuses it
        # even though the journal calls it "done".
        outside = Path(tempfile.mkdtemp()) / "elsewhere.png"
        outside.write_bytes(b"img")
        self._write_journal(run_id, tryon={"status": "done", "file": str(outside)})
        self.assertIsNone(self.runs.tryon_image(self.runs.run_id, "0"))

    def test_tryon_previews_carry_the_share_group(self):
        jobs = self._grouped_jobs()
        write_manifest(jobs, self._live(), now=time.strftime("%Y-%m-%d %H:%M:%S"))
        manifest = load_manifest(self._live())
        run_ids = [run.id for run in manifest.runs]
        self.assertEqual(len(run_ids), 4)

        runs_state = {}
        for n, run in enumerate(manifest.runs):
            image = self.root / "out" / "batch1" / "runs" / run.id / "01-tryon.png"
            image.parent.mkdir(parents=True, exist_ok=True)
            image.write_bytes(f"img{n}".encode())
            runs_state[run.id] = {"status": "running",
                                  "stages": {"tryon": {"status": "done", "file": str(image)}}}
        state_path_for(self._live()).write_text(
            json.dumps({"batch": "batch1", "runs": runs_state}), encoding="utf-8")

        status, body = self.runs.tryon(self.runs.run_id)
        self.assertEqual(status, 200)
        previews = body["previews"]
        self.assertEqual([p["index"] for p in previews], ["0", "1", "2", "3"])
        self.assertEqual([p["run"] for p in previews], run_ids)
        self.assertEqual([p["shared_from"] for p in previews], [None, "0", None, "2"])
        self.assertEqual([p["shares"] for p in previews], [["1"], [], ["3"], []])

    def test_tryon_wrong_run_id_is_404(self):
        status, body = self.runs.tryon("not-the-run-id")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "not_found")

    def test_tryon_image_wrong_run_id_is_none(self):
        self.assertIsNone(self.runs.tryon_image("not-the-run-id", "0"))

    def test_tryon_image_bot_busy_is_503_not_404(self):
        # A lock timeout is "try again in a moment", not "no such image" —
        # the two used to share one `None` return and a busy poll during a
        # kill (slice 5 makes 60s+ lock holds routine) read as a vanished
        # preview.
        held, release = threading.Event(), threading.Event()

        def hold() -> None:
            with bot.BOT_LOCK:
                held.set()
                release.wait(5)

        t = threading.Thread(target=hold)
        t.start()
        try:
            self.assertTrue(held.wait(5))
            with mock.patch.object(bot, "BOT_LOCK_TIMEOUT_SEC", 0.2):
                with self.assertRaises(bot.ApiError) as ctx:
                    self.runs.tryon_image(self.runs.run_id, "0")
        finally:
            release.set()
            t.join(5)
        self.assertEqual(ctx.exception.status, 503)
        self.assertEqual(ctx.exception.code, "bot_busy")


class TestRegenViaAppRuns(_AppRunsFixture):
    """`AppRuns.regen` — `_regen_tryon` itself, wrapped in the idempotency
    and the run-id check every other AppRuns method already has."""

    def _seed_tryon_run(self) -> str:
        job = self._tryon_job()
        manifest = self._write_live_manifest(job)
        run_id = manifest.runs[0].id
        image = self.root / "out" / "batch1" / "runs" / run_id / "01-tryon.png"
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b"img")
        self._write_journal(run_id, tryon={"status": "done", "file": str(image)})
        return run_id

    def test_regen_starts_once_per_key_and_refuses_a_stale_run_token(self):
        self._seed_tryon_run()
        token = bot._run_token(ME)
        body = {"run_token": token}
        first = self.runs.regen(self.runs.run_id, "0", body, "same-key")
        second = self.runs.regen(self.runs.run_id, "0", body, "same-key")
        self.assertEqual(first[0], 202)
        self.assertEqual(second, first)
        self.patches["start_phase_a"].assert_called_once()

        stale = self.runs.regen(self.runs.run_id, "0", {"run_token": "not-the-token"}, "key-2")
        self.assertEqual(stale[0], 409)
        self.assertEqual(stale[1]["error"]["code"], "stale_panel")

    def test_regen_on_a_follower_regenerates_its_leader(self):
        # Index 1 shares index 0's image (_grouped_jobs): the leader's file is
        # what the provider made, so it is the one redone; the followers
        # recopy it by source_sha256 on the next resume (runner.follower_reusable).
        write_manifest(self._grouped_jobs(), self._live(),
                       now=time.strftime("%Y-%m-%d %H:%M:%S"))
        manifest = load_manifest(self._live())
        ids = [run.id for run in manifest.runs]
        runs_state, images = {}, {}
        for n, run in enumerate(manifest.runs):
            image = self.root / "out" / "batch1" / "runs" / run.id / "01-tryon.png"
            image.parent.mkdir(parents=True, exist_ok=True)
            image.write_bytes(f"img{n}".encode())
            images[run.id] = image
            rec = {"status": "done", "file": str(image)}
            if n in (1, 3):
                rec.update(shared_from=ids[n - 1], source_sha256="x")
            runs_state[run.id] = {"status": "running", "stages": {"tryon": rec}}
        state_path_for(self._live()).write_text(
            json.dumps({"batch": "batch1", "runs": runs_state}), encoding="utf-8")

        status, _body = self.runs.regen(self.runs.run_id, "1",
                                        {"run_token": bot._run_token(ME)}, "k-follower")
        self.assertEqual(status, 202)
        self.patches["start_phase_a"].assert_called_once()
        state = load_state(state_path_for(self._live()))
        self.assertNotIn("tryon", state["runs"][ids[0]]["stages"])
        self.assertEqual(state["runs"][ids[1]], runs_state[ids[1]])
        self.assertEqual(images[ids[1]].read_bytes(), b"img1")
        self.assertFalse(images[ids[0]].exists())
        self.assertEqual(images[ids[0]].with_name("01-tryon.v1.png").read_bytes(), b"img0")
        _args, kwargs = self.patches["_start_progress"].call_args
        self.assertEqual(kwargs["regen"]["run"], ids[0])
        self.assertIn(ids[1], kwargs["sent_tryon"])

    def test_regen_requires_a_run_token(self):
        self._seed_tryon_run()
        status, body = self.runs.regen(self.runs.run_id, "0", {}, "k")
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "bad_request")
        self.patches["start_phase_a"].assert_not_called()

    def test_regen_wrong_run_id_is_404(self):
        self._seed_tryon_run()
        status, body = self.runs.regen("not-the-run-id", "0", {"run_token": "x"}, "k")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "not_found")

    def test_regen_with_unknown_guidance_is_400_and_calls_nothing(self):
        with mock.patch("tgbot.bot._regen_tryon") as fake_regen:
            status, body = self.runs.regen(
                self.runs.run_id, "0",
                {"run_token": "whatever", "guidance": ["not_a_real_flag"]}, "k1")
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "bad_request")
        fake_regen.assert_not_called()

    def test_regen_with_valid_guidance_reaches_regen_tryon(self):
        with mock.patch("tgbot.bot._regen_tryon") as fake_regen:
            fake_regen.return_value = Outcome(True, "regenerated")
            self.runs.regen(self.runs.run_id, "0",
                            {"run_token": "whatever",
                             "guidance": ["keep_face", "tighter_crop"]}, "k2")
        _args, kwargs = fake_regen.call_args
        self.assertEqual(kwargs.get("guidance"), ["keep_face", "tighter_crop"])

    def _seed_seeded_tryon_run(self) -> str:
        """A run whose try-on came from the saved library: the manifest carries
        `seedImage`, so runner.py's `_one()` copies that file in and never
        calls the provider at all."""
        seed = self.root / "saved-tryon.png"
        seed.write_bytes(b"s")
        job = replace(self._tryon_job(), tryon_seed=seed)
        manifest = self._write_live_manifest(job)
        run_id = manifest.runs[0].id
        image = self.root / "out" / "batch1" / "runs" / run_id / "01-tryon.png"
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b"img")
        self._write_journal(run_id, tryon={"status": "done", "file": str(image)})
        return run_id

    def test_regen_of_a_seeded_run_is_refused_and_changes_nothing(self):
        # There is no provider call behind a seeded try-on, so "regenerate"
        # would re-copy the same saved file — a silent no-op the phone would
        # render as a successful regeneration.
        run_id = self._seed_seeded_tryon_run()
        before = json.loads(state_path_for(self._live()).read_text(encoding="utf-8"))
        status, body = self.runs.regen(
            self.runs.run_id, "0", {"run_token": bot._run_token(ME)}, "k-seeded")
        self.assertEqual(status, 409)
        self.assertEqual(body["error"]["code"], "seeded")
        self.patches["start_phase_a"].assert_not_called()
        after = json.loads(state_path_for(self._live()).read_text(encoding="utf-8"))
        self.assertEqual(after, before)
        self.assertEqual(after["runs"][run_id]["stages"]["tryon"]["status"], "done")

    def test_regen_of_a_seeded_run_is_refused_even_with_guidance(self):
        self._seed_seeded_tryon_run()
        status, body = self.runs.regen(
            self.runs.run_id, "0",
            {"run_token": bot._run_token(ME), "guidance": ["keep_face"]}, "k-seeded-g")
        self.assertEqual((status, body["error"]["code"]), (409, "seeded"))
        self.patches["start_phase_a"].assert_not_called()

    def test_regen_with_guidance_persists_it_into_the_journal(self):
        # _regen_tryon has no Job list to rewrite the manifest from (unlike
        # _retry_tryon's provider switch), and start_phase_a's subprocess
        # re-reads the manifest fresh from disk — so guidance must survive
        # through the journal write _regen_tryon already makes, not a
        # manifest edit that never reaches disk. Real _regen_tryon here
        # (not mocked), so this exercises the actual persistence.
        run_id = self._seed_tryon_run()
        token = bot._run_token(ME)
        status, _body = self.runs.regen(
            self.runs.run_id, "0",
            {"run_token": token, "guidance": ["keep_face", "tighter_crop"]}, "k-guidance")
        self.assertEqual(status, 202)
        state = load_state(state_path_for(self._live()))
        self.assertEqual(state["runs"][run_id]["regen_guidance"],
                         {"tryon": {"keepFace": "1", "tighterCrop": "1"}})


class TestTryonVersionImage(_AppRunsFixture):
    """AppRuns.tryon_version_image — the version history `_regen_tryon`
    already keeps on disk (_tryon_versions), served over HTTP (§5.10, slice 6)."""

    def _seed_tryon_run_with_version(self) -> tuple[str, Path]:
        job = self._tryon_job()
        manifest = self._write_live_manifest(job)
        run_id = manifest.runs[0].id
        image = self.root / "out" / "batch1" / "runs" / run_id / "01-tryon.png"
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b"img")
        version = image.with_name("01-tryon.v1.png")
        version.write_bytes(b"old img")
        self._write_journal(run_id, tryon={"status": "done", "file": str(image)})
        return run_id, version

    def test_version_image_returns_an_older_version(self):
        _run_id, version = self._seed_tryon_run_with_version()
        path = self.runs.tryon_version_image(self.runs.run_id, "0", "1")
        self.assertEqual(path, version.resolve())

    def test_version_image_out_of_range_is_none(self):
        self._seed_tryon_run_with_version()
        self.assertIsNone(self.runs.tryon_version_image(self.runs.run_id, "0", "99"))

    def test_version_image_no_such_preview_is_none(self):
        self.assertIsNone(self.runs.tryon_version_image(self.runs.run_id, "0", "1"))

    def test_version_image_wrong_run_id_is_none(self):
        # Same contract as tryon_image's own first line — a stale/wrong
        # run_id in the URL must 404, never a real (wrong-context) image.
        self._seed_tryon_run_with_version()
        self.assertIsNone(self.runs.tryon_version_image("not-the-run-id", "0", "1"))


class TestTryonSaveInfoMaterialIds(_AppRunsFixture):
    """tryon_save_info builds a saved try-on's material_ids by hand, without
    stat-ing (a past run's inputs may be gone). Pinned against material_item —
    a second, independent builder of the same id — so the two cannot drift.
    Patching APP_OWNER and checking both moved would pass vacuously: both
    sides read the same constant."""

    def test_material_ids_match_material_item(self):
        job = self._tryon_job()
        manifest = self._write_live_manifest(job)
        run = manifest.runs[0]
        image = self.root / "out" / "batch1" / "runs" / run.id / "01-tryon.png"
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b"img")
        self._write_journal(run.id, tryon={"status": "done", "file": str(image)})

        info = self.runs.tryon_save_info("0")
        self.assertIsNotNone(info)
        _image, material_ids, _provider = info
        expected = {role: materials.material_item(materials.APP_OWNER, path)["id"]
                    for role, path in run.inputs.items() if role != "driver"}
        self.assertTrue(expected)
        self.assertEqual(material_ids, expected)


class TestNoAbsolutePaths(_AppRunsFixture):
    def test_no_absolute_paths_in_any_body(self):
        self._seed_draft(validated=True)
        job = self._tryon_job()
        manifest = self._write_live_manifest(job)
        run_id = manifest.runs[0].id
        image = self.root / "out" / "batch1" / "runs" / run_id / "01-tryon.png"
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b"img")
        self._write_journal(run_id, tryon={"status": "done", "file": str(image)})

        bodies = []
        with mock.patch("tgbot.bot.volume_datacenter", return_value="EU-RO-1"), \
             mock.patch("tgbot.bot.stock_at_cached", return_value={}), \
             mock.patch("tgbot.bot.vast_fetch_quote", side_effect=RuntimeError("no vastai")), \
             mock.patch("tgbot.bot.vast_credit", return_value=25.0):
            bodies.append(self.runs.rent_panel(self.runs.run_id, force=False)[1])
        bodies.append(self.runs.tryon(self.runs.run_id)[1])
        bodies.append(self.runs.regen(self.runs.run_id, "0",
                                      {"run_token": bot._run_token(ME)}, "k")[1])
        root = str(self.root)
        for body in bodies:
            self.assertNotIn(root, json.dumps(body))


if __name__ == "__main__":
    unittest.main()


class TestAppRunsDeleteRun(_AppRunsFixture):
    """DELETE /v1/runs/{id} through the bot: the lock, and the wire shape."""

    def _seed_run(self) -> Path:
        manifest = self.root / "batch" / f"{self.runs.run_id}.yaml"
        manifest.write_text("runs: []\n")
        state_path_for(manifest).write_text(json.dumps({"version": 1, "batch": "b1", "runs": {}}))
        return manifest

    def test_deletes_and_answers_the_count(self):
        manifest = self._seed_run()
        status, body = self.runs.delete_run(self.root / "batch", self.root / "out",
                                            self.runs.run_id, False)
        self.assertEqual((status, body), (200, {"deleted": self.runs.run_id, "videos_deleted": 0}))
        self.assertFalse(manifest.exists())

    def test_a_held_run_is_409_and_kept(self):
        manifest = self._seed_run()
        with mock.patch("tgbot.run.busy", return_value=True):
            status, body = self.runs.delete_run(self.root / "batch", self.root / "out",
                                                self.runs.run_id, True)
        self.assertEqual((status, body["error"]["code"]), (409, "run_busy"))
        self.assertTrue(manifest.exists())

    def test_waits_for_the_bot_lock(self):
        self._seed_run()
        with bot.BOT_LOCK, mock.patch.object(bot, "BOT_LOCK_TIMEOUT_SEC", 0.1):
            result = []
            t = threading.Thread(target=lambda: result.append(
                self.runs.delete_run(self.root / "batch", self.root / "out", self.runs.run_id, False)))
            t.start(); t.join(5)
        self.assertEqual(result[0][0], 503)
