"""The bot's four run functions as the phone reaches them: an Outcome back,
refusals kept off Telegram for an app call, and the app's jobs run through the
Telegram chat's one run slot without touching the Telegram draft (spec §5.8).

Everything here is free: start_drain / start_phase_a are patched, so no pod
is rented and no try-on API is called.
"""
import json, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.manifest import load_manifest
from batchlib_ext.handoff import mailbox_path
from control.runs import Outcome
import tgbot.bot as bot
from tgbot.ingest import Probe
from tgbot.job import Job

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


if __name__ == "__main__":
    unittest.main()
