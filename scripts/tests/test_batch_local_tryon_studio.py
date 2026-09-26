import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import batchlib.local_tryon as lt
from batchlib.client import JobError


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(routes):
    """routes: list of (predicate(req_or_url) -> bool, bytes). Records requests."""
    seen = []

    def urlopen(req, timeout=None):
        seen.append(req)
        for match, body in routes:
            if match(req):
                return _Resp(body)
        raise AssertionError(f"unexpected request {getattr(req, 'full_url', req)}")
    return urlopen, seen


class TestGeminiHelpers(unittest.TestCase):
    def test_image_bytes_returns_the_first_inline_image(self):
        body = json.dumps({"candidates": [{"content": {"parts": [
            {"text": "here"}, {"inlineData": {"mimeType": "image/png", "data": "aGk="}}]}}]}).encode()
        urlopen, seen = _fake_urlopen([(lambda r: True, body)])
        with mock.patch.object(lt.urllib.request, "urlopen", urlopen):
            out = lt.gemini_image_bytes([], "a cat", "AIza-test", model="gemini-3.1-flash-image",
                                        aspect_ratio="9:16", image_size="2K")
        self.assertEqual(out, b"hi")
        sent = json.loads(seen[0].data)
        self.assertEqual(sent["generationConfig"]["imageConfig"], {"aspectRatio": "9:16", "imageSize": "2K"})
        self.assertIn("gemini-3.1-flash-image:generateContent", seen[0].full_url)

    def test_no_image_reason_prefers_block_reason_and_text(self):
        data = {"promptFeedback": {"blockReason": "SAFETY"},
                "candidates": [{"finishReason": "IMAGE_SAFETY",
                                "content": {"parts": [{"text": "I can't make that image."}]}}]}
        reason = lt.gemini_no_image_reason(data)
        self.assertIn("SAFETY", reason)
        self.assertIn("IMAGE_SAFETY", reason)
        self.assertIn("I can't make that image.", reason)
        self.assertNotIn("{", reason)

    def test_image_bytes_raises_with_the_reason(self):
        body = json.dumps({"candidates": [{"finishReason": "IMAGE_SAFETY",
                                           "content": {"parts": [{"text": "blocked"}]}}]}).encode()
        urlopen, _ = _fake_urlopen([(lambda r: True, body)])
        with mock.patch.object(lt.urllib.request, "urlopen", urlopen):
            with self.assertRaises(JobError) as ctx:
                lt.gemini_image_bytes([], "x", "AIza-test")
        self.assertIn("IMAGE_SAFETY", str(ctx.exception))
        self.assertIn("blocked", str(ctx.exception))


class TestQwenGenerate(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(lt, "_qwen_image_url", return_value="https://ws.example/api")
        p.start(); self.addCleanup(p.stop)

    def _answer(self, urls):
        return json.dumps({"output": {"choices": [
            {"message": {"content": [{"image": u} for u in urls]}}]}}).encode()

    def test_text_only_sends_one_text_part_and_n(self):
        urlopen, seen = _fake_urlopen([
            (lambda r: isinstance(r, str) and r.endswith("/1.png"), b"one"),
            (lambda r: isinstance(r, str) and r.endswith("/2.png"), b"two"),
            (lambda r: not isinstance(r, str), self._answer(["https://o/1.png", "https://o/2.png"])),
        ])
        with mock.patch.object(lt.urllib.request, "urlopen", urlopen):
            out = lt.qwen_image_generate([], "một con mèo", "sk-test", n=2, size="1536*2048",
                                         model="qwen-image-3.0-pro")
        self.assertEqual(out, [b"one", b"two"])
        body = json.loads(seen[0].data)
        self.assertEqual(body["input"]["messages"][0]["content"], [{"text": "một con mèo"}])
        self.assertEqual(body["parameters"]["n"], 2)
        self.assertEqual(body["parameters"]["size"], "1536*2048")
        self.assertEqual(body["model"], "qwen-image-3.0-pro")

    def test_more_than_three_images_is_refused_not_truncated(self):
        with self.assertRaises(JobError):
            lt.qwen_image_generate([(b"x", "image/png")] * 4, "edit", "sk-test")

    def test_qwen_max_edit_still_writes_the_first_image(self):
        urlopen, _ = _fake_urlopen([
            (lambda r: isinstance(r, str), b"img"),
            (lambda r: not isinstance(r, str), self._answer(["https://o/1.png"])),
        ])
        out = Path(self.id().replace(".", "_") + ".png")
        self.addCleanup(lambda: out.unlink(missing_ok=True))
        with mock.patch.object(lt.urllib.request, "urlopen", urlopen):
            lt.qwen_max_edit([(b"a", "image/png")] * 5, "p", "sk-test", out)
        self.assertEqual(out.read_bytes(), b"img")


if __name__ == "__main__":
    unittest.main()
