import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from control.paths import safe_child


class TestSafeChild(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "ok.mp4").write_bytes(b"x")

    def test_plain_name_resolves_under_root(self):
        self.assertEqual(safe_child(self.root, "ok.mp4"), (self.root / "ok.mp4").resolve())

    def test_refuses_traversal_absolute_and_separators(self):
        for bad in ("..", "../x", "a/b", "a\\b", "/etc/passwd", "", "   ", ".",
                    "x\x00.mp4", "\x00"):
            self.assertIsNone(safe_child(self.root, bad), bad)

    def test_refuses_a_symlink_that_escapes_the_root(self):
        outside = Path(tempfile.mkdtemp()) / "secret"
        outside.write_text("s")
        os.symlink(outside, self.root / "link")
        self.assertIsNone(safe_child(self.root, "link"))

    def test_bot_alias_is_the_same_function(self):
        import tgbot.bot as bot
        self.assertIs(bot._safe_child, safe_child)


if __name__ == "__main__":
    unittest.main()
