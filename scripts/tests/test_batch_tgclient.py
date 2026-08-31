import io
import json
import sys
import tempfile
import unittest
import urllib.error
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

    def test_http_error_400_with_message_not_modified_does_not_raise_in_edit_message(self):
        # HTTPError 400 from Telegram carries the JSON response body in exc.read().
        # edit_message must detect "message is not modified" in that body and return
        # normally, not raise. This test would have failed before the HTTPError fix.
        error_body = json.dumps({"ok": False, "description": "message is not modified"}).encode()
        error = urllib.error.HTTPError("http://test", 400, "Bad Request", {}, io.BytesIO(error_body))
        with patch("urllib.request.urlopen", side_effect=error):
            # Should not raise
            self.tg.edit_message(chat_id=1, message_id=1, text="hi")

    def test_http_error_400_with_other_description_raises_in_edit_message(self):
        # When HTTPError 400 has a different description, edit_message must raise.
        error_body = json.dumps({"ok": False, "description": "message not found"}).encode()
        error = urllib.error.HTTPError("http://test", 400, "Bad Request", {}, io.BytesIO(error_body))
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(TgError) as cm:
                self.tg.edit_message(chat_id=1, message_id=1, text="hi")
            self.assertIn("message not found", str(cm.exception))

    def test_call_handles_http_error_and_reads_body(self):
        # Telegram returns real HTTP statuses (400, 401, 403, 429). The JSON error
        # body lives in exc.read(), not in the response.
        error_body = json.dumps({"ok": False, "description": "chat not found"}).encode()
        error = urllib.error.HTTPError("http://test", 400, "Bad Request", {}, io.BytesIO(error_body))
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(TgError) as cm:
                self.tg.call("sendMessage", chat_id=1, text="hi")
            self.assertIn("chat not found", str(cm.exception))

    def test_call_token_not_in_error_even_with_http_error(self):
        # Even though HTTPError includes the URL, TgError must scrub it.
        error_body = json.dumps({"ok": False, "description": "boom"}).encode()
        error = urllib.error.HTTPError("http://test", 400, "Bad Request", {}, io.BytesIO(error_body))
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(TgError) as cm:
                self.tg.call("sendMessage", chat_id=1)
            self.assertNotIn("TESTTOKEN", str(cm.exception))

    def test_send_document_multipart_framing_and_round_trip(self):
        # Verify multipart body is correctly formed: fields present, absent as
        # appropriate, and document bytes round-trip exactly. Include a
        # boundary-like sequence to ensure framing is correct.
        payload_with_boundary_sequence = b"This is\r\n--boundary-like--\r\ntext"
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(payload_with_boundary_sequence)
            temp_path = Path(f.name)
        try:
            captured_request = None
            def capture_request(req, **kwargs):
                nonlocal captured_request
                captured_request = req
                return FakeResponse({"ok": True, "result": {"file_id": "abc123"}})

            with patch("urllib.request.urlopen", side_effect=capture_request):
                self.tg.send_document(chat_id=42, path=temp_path, caption="test caption")

            # Verify the multipart body structure
            self.assertIsNotNone(captured_request)
            # Assert on the FRAMED field, not on a bare b'42' anywhere in the
            # body: the multipart boundary is a uuid4 hex string, so a loose
            # search for "42" was satisfied by the boundary alone in roughly
            # 10-12% of runs (computed 2026-08-31: 31 positions x 1/256 per
            # position, P(at least one) = 1 - (255/256)^31 ~= 0.114) — a check
            # satisfied by coincidence one run in nine is worse than no check.
            self.assertIn(b'name="chat_id"\r\n\r\n42\r\n', captured_request.data)
            self.assertIn(b'name="caption"', captured_request.data)
            self.assertIn(b'test caption', captured_request.data)
            self.assertIn(b'name="document"', captured_request.data)
            self.assertIn(payload_with_boundary_sequence, captured_request.data)
        finally:
            temp_path.unlink()

    def test_send_document_ok_false_raises(self):
        # send_document has its own ok:false check outside call(). It must work.
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test data")
            temp_path = Path(f.name)
        try:
            with patch("urllib.request.urlopen",
                       return_value=FakeResponse({"ok": False, "description": "file too large"})):
                with self.assertRaises(TgError) as cm:
                    self.tg.send_document(chat_id=42, path=temp_path)
                self.assertIn("file too large", str(cm.exception))
        finally:
            temp_path.unlink()

    def test_send_document_token_not_in_error(self):
        # Even in send_document's own path, token must not leak.
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test data")
            temp_path = Path(f.name)
        try:
            with patch("urllib.request.urlopen",
                       return_value=FakeResponse({"ok": False, "description": "boom"})):
                with self.assertRaises(TgError) as cm:
                    self.tg.send_document(chat_id=42, path=temp_path)
                self.assertNotIn("TESTTOKEN", str(cm.exception))
        finally:
            temp_path.unlink()


class TestKeyboard(unittest.TestCase):
    """Inline keyboards, added 2026-08-31."""

    def test_a_keyboard_becomes_the_bot_api_shape(self):
        markup = Tg.keyboard([[("Run", "run:ask"), ("Cancel", "run:no")]])
        self.assertEqual(markup, {"inline_keyboard": [
            [{"text": "Run", "callback_data": "run:ask"},
             {"text": "Cancel", "callback_data": "run:no"}]]})

    def test_callback_data_over_64_bytes_is_refused_loudly(self):
        # The Bot API caps callback_data at 64 bytes and rejects the whole
        # sendMessage when a button exceeds it — so the user would see NO
        # message at all, not a broken button. Failing here names the button.
        with self.assertRaises(ValueError) as cm:
            Tg.keyboard([[("x", "pipe:" + "a" * 60)]])
        self.assertIn("64", str(cm.exception))
        self.assertIn("'x'", str(cm.exception))

    def test_the_limit_is_bytes_not_characters(self):
        # Multi-byte labels are fine, but multi-byte DATA counts double or
        # more. A character count would pass this and Telegram would reject it.
        with self.assertRaises(ValueError):
            Tg.keyboard([[("nhãn", "á" * 33)]])   # 33 chars, 66 bytes

    def test_answer_callback_query_swallows_failures(self):
        # A callback id expires after ~15 minutes, and the acknowledgement is
        # cosmetic: a failure must not abort the handler that already did the
        # real work.
        with patch("urllib.request.urlopen",
                   return_value=FakeResponse({"ok": False,
                                              "description": "query is too old"})):
            self.tg = Tg(token="TESTTOKEN", base_url="http://127.0.0.1:8081")
            self.tg.answer_callback_query("stale-id")   # must not raise


if __name__ == "__main__":
    unittest.main()
