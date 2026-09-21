import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from control.idempotency import IdempotencyError, IdempotencyStore


class TestIdempotencyStore(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.store = IdempotencyStore(self.tmp / "idem")

    def test_first_begin_returns_none_and_replay_returns_the_stored_response(self):
        self.assertIsNone(self.store.begin("confirm", "k1"))
        self.store.finish("confirm", "k1", 202, {"outcome": "started"})
        self.assertEqual(self.store.begin("confirm", "k1"), (202, {"outcome": "started"}))

    def test_a_pending_key_is_outcome_unknown_not_a_second_attempt(self):
        self.assertIsNone(self.store.begin("confirm", "k1"))
        status, body = self.store.begin("confirm", "k1")
        self.assertEqual((status, body["error"]["code"]), (409, "outcome_unknown"))

    def test_scopes_do_not_collide(self):
        self.assertIsNone(self.store.begin("confirm", "k"))
        self.assertIsNone(self.store.begin("phase-a", "k"))

    def test_forget_allows_a_retry(self):
        self.store.begin("confirm", "k")
        self.store.forget("confirm", "k")
        self.assertIsNone(self.store.begin("confirm", "k"))

    def test_bad_keys(self):
        for key in ("", None, "x" * 201, 5):
            with self.subTest(key=key), self.assertRaises(IdempotencyError):
                self.store.begin("confirm", key)

    def test_key_is_hashed_so_no_client_text_reaches_the_filesystem(self):
        self.store.begin("confirm", "../../etc/passwd")
        names = [p.name for p in (self.tmp / "idem").iterdir()]
        self.assertEqual(len(names), 1)
        self.assertNotIn("..", names[0])

    def test_prune_removes_only_expired_records(self):
        self.store.begin("confirm", "old"); self.store.finish("confirm", "old", 202, {})
        self.store.begin("confirm", "new"); self.store.finish("confirm", "new", 202, {})
        old = next(p for p in (self.tmp / "idem").iterdir()
                   if json.loads(p.read_text())["key_hint"] == "old")
        past = time.time() - 25 * 3600
        os.utime(old, (past, past))
        self.assertEqual(self.store.prune(time.time()), 1)
        self.assertIsNone(self.store.begin("confirm", "old"))
        self.assertIsNotNone(self.store.begin("confirm", "new"))


if __name__ == "__main__":
    unittest.main()
