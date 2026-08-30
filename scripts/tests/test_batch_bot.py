import sys, tempfile, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tgbot.bot as bot
from tgbot.bot import allowed
from tgbot.ingest import Probe

ME = 12345


def update_from(user_id: int) -> dict:
    return {"message": {"from": {"id": user_id}, "chat": {"id": user_id}, "text": "/start"}}


def cmd_from(user_id: int, text: str) -> dict:
    return {"message": {"from": {"id": user_id}, "chat": {"id": user_id}, "text": text}}


def doc_from(user_id: int, file_id: str, file_name: str = "f") -> dict:
    return {"message": {"from": {"id": user_id}, "chat": {"id": user_id},
                        "document": {"file_id": file_id, "file_name": file_name}}}


class FakeTg:
    """Records calls instead of touching the network. `call()` only needs to
    answer getFile — /tryon, /result and the document-ingest flow (Task 7)
    are the only handlers that call it, and getFile is the only method any
    of them use."""
    def __init__(self):
        self.messages: list[str] = []
        self.documents: list[tuple] = []
        self.file_paths: dict[str, str] = {}   # file_id -> local path, set by the test

    def send_message(self, chat_id, text):
        self.messages.append(text)
        return 1

    def send_document(self, chat_id, path, caption=""):
        self.documents.append((path, caption))

    def call(self, method, **params):
        if method == "getFile":
            return {"file_path": self.file_paths[params["file_id"]]}
        raise AssertionError(f"FakeTg.call: unexpected method {method!r}")


class TestAllowed(unittest.TestCase):
    def test_the_owner_is_allowed(self):
        self.assertTrue(allowed(update_from(ME), ME))

    def test_anyone_else_is_refused(self):
        # This whitelist is the only thing between a stranger and a button that
        # rents a $0.99/hour GPU. It must be an allowlist, never a blocklist.
        self.assertFalse(allowed(update_from(99999), ME))

    def test_an_update_with_no_sender_is_refused(self):
        # channel_post, edited_channel_post and service updates have no
        # message.from. Defaulting those to allowed would open the door.
        self.assertFalse(allowed({"channel_post": {"chat": {"id": ME}}}, ME))

    def test_a_malformed_update_is_refused_not_crashed(self):
        self.assertFalse(allowed({}, ME))
        self.assertFalse(allowed({"message": {}}, ME))


class TestResultAndTryonPathSafety(unittest.TestCase):
    """Regression tests for the path-containment finding: the allowlist
    restricts WHO can message the bot, not WHAT they type — a single
    mistyped or pasted path must never let /result or /tryon read or send a
    file outside batch/ or out/.
    """

    def setUp(self):
        self._orig_root = bot.ROOT
        self.root = Path(tempfile.mkdtemp())
        (self.root / "batch").mkdir()
        (self.root / "out").mkdir()
        # One level above batch/ — reachable via "../" or an absolute path if
        # /result does not reject either, and must never be reachable.
        (self.root / "secret.yaml").write_text("runs: []", encoding="utf-8")
        # One level above out/, shaped like a real try-on image — reachable
        # via "../" if /tryon's batch id is not rejected.
        evil_run = self.root / "evil" / "runs" / "job"
        evil_run.mkdir(parents=True)
        (evil_run / "01-tryon.png").write_bytes(b"not a real png, just a probe")
        bot.ROOT = self.root

    def tearDown(self):
        bot.ROOT = self._orig_root

    def test_result_refuses_an_absolute_path(self):
        tg = FakeTg()
        payload = str(self.root / "secret.yaml")
        bot.handle(tg, cmd_from(ME, f"/result {payload}"), allowed_user_id=ME, state={})
        self.assertEqual(tg.documents, [])
        self.assertEqual(len(tg.messages), 1)
        self.assertIn("not allowed", tg.messages[0])
        self.assertNotIn(payload, tg.messages[0])

    def test_result_refuses_dotdot_traversal(self):
        tg = FakeTg()
        bot.handle(tg, cmd_from(ME, "/result ../secret.yaml"), allowed_user_id=ME, state={})
        self.assertEqual(tg.documents, [])
        self.assertEqual(len(tg.messages), 1)
        self.assertIn("not allowed", tg.messages[0])

    def test_tryon_refuses_dotdot_traversal(self):
        tg = FakeTg()
        bot.handle(tg, cmd_from(ME, "/tryon ../evil"), allowed_user_id=ME, state={})
        # Pre-fix, this sent evil/runs/job/01-tryon.png — a real file one
        # level above out/ — straight to send_document.
        self.assertEqual(tg.documents, [])
        self.assertEqual(len(tg.messages), 1)
        self.assertIn("not allowed", tg.messages[0])

    def test_result_reports_not_found_for_a_valid_but_missing_name(self):
        tg = FakeTg()
        bot.handle(tg, cmd_from(ME, "/result does-not-exist.yaml"),
                  allowed_user_id=ME, state={})
        self.assertEqual(tg.documents, [])
        self.assertEqual(len(tg.messages), 1)
        # A valid (bare) name that just doesn't exist is a plain "not found",
        # never the safety refusal.
        self.assertNotIn("not allowed", tg.messages[0])

    def test_refusal_does_not_echo_the_offending_input(self):
        tg = FakeTg()
        payload = "../../../../etc/passwd"
        bot.handle(tg, cmd_from(ME, f"/result {payload}"), allowed_user_id=ME, state={})
        self.assertEqual(tg.documents, [])
        self.assertNotIn(payload, tg.messages[0])
        self.assertNotIn("passwd", tg.messages[0])


class TestFlow(unittest.TestCase):
    """The state machine Task 7 adds: files in, an ambiguous image asked
    about (never guessed), the manifest shown once every required slot is
    filled, and /confirm as the only reachable path to start_drain.

    Real files on disk, real `make batch-validate`: only `probe()` is faked
    (no real ffprobe/media needed) — everything downstream of it, including
    the manifest text and the free validation gate, runs for real, against
    the real repo (`bot._REPO_ROOT`), so a passing test here is evidence the
    generated manifest actually validates, not just that a string was built.
    """

    def setUp(self):
        self._orig_root = bot.ROOT
        self.root = Path(tempfile.mkdtemp())
        (self.root / "batch").mkdir()
        (self.root / "out").mkdir()
        bot.ROOT = self.root
        bot._STATE.clear()
        bot._PENDING_SLOT.clear()

        # Real, if empty, files: validate_manifest checks path.is_file().
        self.driver_path = self.root / "driver.mp4"
        self.driver_path.write_bytes(b"d")
        self.character_path = self.root / "character.jpg"
        self.character_path.write_bytes(b"c")
        self.outfit_path = self.root / "outfit.jpg"
        self.outfit_path.write_bytes(b"o")

        self.tg = FakeTg()
        self.tg.file_paths = {
            "driver-id": str(self.driver_path),
            "character-id": str(self.character_path),
            "outfit-id": str(self.outfit_path),
        }
        self.driver_probe = Probe(kind="video", width=1080, height=1920, duration_s=5.0,
                                  bitrate_kbps=3000, size_bytes=1_500_000)
        self.image_probe = Probe(kind="image", width=1024, height=1024, duration_s=0.0,
                                 bitrate_kbps=0, size_bytes=800_000)

    def tearDown(self):
        bot.ROOT = self._orig_root
        bot._STATE.clear()
        bot._PENDING_SLOT.clear()

    def _probe_for(self, mapping):
        def fake_probe(path):
            return mapping[Path(path)]
        return fake_probe

    def _fill_required_slots(self):
        """driver (auto), character and outfit (each an ambiguous image,
        answered by name) — every required role of the pipeline the bot
        hardcodes, per docs/superpowers/specs/2026-08-30-…: "four labelled
        slots". Background is optional and deliberately left unfilled."""
        probe_fn = self._probe_for({
            self.driver_path: self.driver_probe,
            self.character_path: self.image_probe,
            self.outfit_path: self.image_probe,
        })
        with mock.patch("tgbot.bot.probe", side_effect=probe_fn):
            bot.handle(self.tg, doc_from(ME, "driver-id"), allowed_user_id=ME, state={})
            bot.handle(self.tg, doc_from(ME, "character-id"), allowed_user_id=ME, state={})
            bot.handle(self.tg, cmd_from(ME, "character"), allowed_user_id=ME, state={})
            bot.handle(self.tg, doc_from(ME, "outfit-id"), allowed_user_id=ME, state={})
            bot.handle(self.tg, cmd_from(ME, "outfit"), allowed_user_id=ME, state={})

    def test_a_video_fills_the_driver_slot_without_asking(self):
        with mock.patch("tgbot.bot.probe", return_value=self.driver_probe):
            bot.handle(self.tg, doc_from(ME, "driver-id"), allowed_user_id=ME, state={})
        self.assertEqual(len(self.tg.messages), 1)
        self.assertIn("driver", self.tg.messages[0])
        self.assertNotIn("which", self.tg.messages[0].lower())
        self.assertEqual(bot._STATE[ME].slots.get("driver"), self.driver_path)

    def test_an_image_is_asked_about_and_the_answer_fills_the_slot(self):
        with mock.patch("tgbot.bot.probe", return_value=self.image_probe):
            bot.handle(self.tg, doc_from(ME, "outfit-id"), allowed_user_id=ME, state={})
        self.assertIn("which", self.tg.messages[-1].lower())
        self.assertNotIn("outfit", bot._STATE[ME].slots)

        bot.handle(self.tg, cmd_from(ME, "outfit"), allowed_user_id=ME, state={})
        self.assertEqual(bot._STATE[ME].slots.get("outfit"), self.outfit_path)
        self.assertIn("outfit", self.tg.messages[-1])

    def test_an_unrecognised_slot_answer_is_re_asked_not_guessed(self):
        with mock.patch("tgbot.bot.probe", return_value=self.image_probe):
            bot.handle(self.tg, doc_from(ME, "outfit-id"), allowed_user_id=ME, state={})
        bot.handle(self.tg, cmd_from(ME, "banana"), allowed_user_id=ME, state={})
        self.assertNotIn("outfit", bot._STATE[ME].slots)
        self.assertNotIn("character", bot._STATE[ME].slots)
        self.assertIn(ME, bot._PENDING_SLOT)   # still parked, never guessed
        last = self.tg.messages[-1].lower()
        self.assertIn("didn't recognise", last)
        self.assertIn("outfit", last)          # re-asks with the same options, not silence

    def test_the_manifest_is_shown_before_anything_is_confirmed(self):
        with mock.patch("tgbot.bot.start_drain") as start_drain:
            self._fill_required_slots()
            start_drain.assert_not_called()
        joined = "\n".join(self.tg.messages)
        self.assertIn("pipeline", joined)
        self.assertIn("runs:", joined)

    def test_confirm_starts_a_drain_and_nothing_else_does(self):
        with mock.patch("tgbot.bot.start_drain") as start_drain, \
             mock.patch("tgbot.bot.drain_running", return_value=False):
            self._fill_required_slots()
            start_drain.assert_not_called()
            bot.handle(self.tg, cmd_from(ME, "/confirm"), allowed_user_id=ME, state={})
        start_drain.assert_called_once()
        _, kwargs = start_drain.call_args
        self.assertEqual(kwargs.get("dry_run"), False)

    def test_confirm_is_refused_while_a_drain_is_already_running(self):
        with mock.patch("tgbot.bot.start_drain") as start_drain, \
             mock.patch("tgbot.bot.drain_running", return_value=True):
            self._fill_required_slots()
            bot.handle(self.tg, cmd_from(ME, "/confirm"), allowed_user_id=ME, state={})
        start_drain.assert_not_called()

    def test_confirm_without_a_complete_job_is_refused(self):
        with mock.patch("tgbot.bot.start_drain") as start_drain:
            bot.handle(self.tg, cmd_from(ME, "/confirm"), allowed_user_id=ME, state={})
        start_drain.assert_not_called()


if __name__ == "__main__":
    unittest.main()
