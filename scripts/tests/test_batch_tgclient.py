import json, sys, unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tgbot.tgclient import Tg, TgError


class FakeResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode()
    def read(self):
        return self._body
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


class TestTg(unittest.TestCase):
    def setUp(self):
        self.tg = Tg(token="TESTTOKEN", base_url="http://localhost:8081")

    def test_call_returns_the_result_field(self):
        with patch("urllib.request.urlopen",
                   return_value=FakeResponse({"ok": True, "result": {"message_id": 7}})):
            self.assertEqual(self.tg.call("sendMessage", chat_id=1, text="hi"),
                             {"message_id": 7})

    def test_not_ok_raises_with_the_description(self):
        # Telegram reports failure in the BODY with HTTP 200. Treating a 200 as
        # success would make every error silent.
        with patch("urllib.request.urlopen",
                   return_value=FakeResponse({"ok": False, "description": "chat not found"})):
            with self.assertRaises(TgError) as cm:
                self.tg.call("sendMessage", chat_id=1, text="hi")
        self.assertIn("chat not found", str(cm.exception))

    def test_token_never_appears_in_an_error_message(self):
        # The token is in the URL of every request. An exception that echoes the
        # URL would put it into logs, and this repo is public.
        with patch("urllib.request.urlopen",
                   return_value=FakeResponse({"ok": False, "description": "boom"})):
            with self.assertRaises(TgError) as cm:
                self.tg.call("sendMessage", chat_id=1)
        self.assertNotIn("TESTTOKEN", str(cm.exception))

    def test_send_message_returns_the_message_id(self):
        with patch("urllib.request.urlopen",
                   return_value=FakeResponse({"ok": True, "result": {"message_id": 42}})):
            self.assertEqual(self.tg.send_message(chat_id=1, text="hi"), 42)


if __name__ == "__main__":
    unittest.main()
