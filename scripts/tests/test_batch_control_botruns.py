"""The bot's four run functions as the phone reaches them: an Outcome back,
refusals kept off Telegram for an app call, and the app's jobs run through the
Telegram chat's one run slot without touching the Telegram draft (spec §5.8).

Everything here is free: start_drain / start_phase_a are patched, so no pod
is rented and no try-on API is called.
"""
import json, sys, tempfile, threading, time, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.manifest import load_manifest, state_path_for
from batchlib_ext.gpu_stock import Stock
from batchlib_ext.handoff import mailbox_path
import control.drafts as drafts
from control.idempotency import IdempotencyStore
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
        self.store = drafts.DraftStore(self.root / "batch", staging, "app",
                                       default_pipeline="motion-enhance",
                                       default_provider="gemini")
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
                         [{"index": "0", "run": run_id, "status": "done", "has_image": True}])

        got = self.runs.tryon_image(self.runs.run_id, "0")
        self.assertEqual(got, image.resolve())

        # The same slot, now pointing outside out/ — tryon_image refuses it
        # even though the journal calls it "done".
        outside = Path(tempfile.mkdtemp()) / "elsewhere.png"
        outside.write_bytes(b"img")
        self._write_journal(run_id, tryon={"status": "done", "file": str(outside)})
        self.assertIsNone(self.runs.tryon_image(self.runs.run_id, "0"))

    def test_tryon_wrong_run_id_is_404(self):
        status, body = self.runs.tryon("not-the-run-id")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "not_found")

    def test_tryon_image_wrong_run_id_is_none(self):
        self.assertIsNone(self.runs.tryon_image("not-the-run-id", "0"))


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
