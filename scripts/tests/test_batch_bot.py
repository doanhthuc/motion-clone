import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tgbot.bot import allowed

ME = 12345


def update_from(user_id: int) -> dict:
    return {"message": {"from": {"id": user_id}, "chat": {"id": user_id}, "text": "/start"}}


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


if __name__ == "__main__":
    unittest.main()
