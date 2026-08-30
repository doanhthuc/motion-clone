import sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tgbot.bot as bot
from tgbot.bot import allowed

ME = 12345


def update_from(user_id: int) -> dict:
    return {"message": {"from": {"id": user_id}, "chat": {"id": user_id}, "text": "/start"}}


def cmd_from(user_id: int, text: str) -> dict:
    return {"message": {"from": {"id": user_id}, "chat": {"id": user_id}, "text": text}}


class FakeTg:
    """Records calls instead of touching the network — only the two methods
    /tryon and /result actually call."""
    def __init__(self):
        self.messages: list[str] = []
        self.documents: list[tuple] = []

    def send_message(self, chat_id, text):
        self.messages.append(text)
        return 1

    def send_document(self, chat_id, path, caption=""):
        self.documents.append((path, caption))


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


if __name__ == "__main__":
    unittest.main()
