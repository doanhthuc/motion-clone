import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib_ext.handoff import (Handoff, claim_mailbox, handoff_path,
                                  mailbox_path, read_handoff, write_handoff)


class TestPaths(unittest.TestCase):
    def test_mailbox_and_handoff_are_named_from_the_original_manifest(self):
        original = Path("/batch/tg-123.yaml")
        self.assertEqual(mailbox_path(original), Path("/batch/tg-123.next.yaml"))
        self.assertEqual(handoff_path(original), Path("/batch/tg-123.handoff.json"))


class TestWriteReadHandoff(unittest.TestCase):
    def test_round_trips_every_field(self):
        path = Path(tempfile.mkdtemp()) / "tg-1.handoff.json"
        write_handoff(path, Handoff(status="failed", manifest="/batch/tg-1-99.yaml",
                                    reason="batch_run exited 1"))
        self.assertEqual(read_handoff(path),
                         Handoff(status="failed", manifest="/batch/tg-1-99.yaml",
                                 reason="batch_run exited 1"))

    def test_reason_defaults_to_none(self):
        path = Path(tempfile.mkdtemp()) / "tg-1.handoff.json"
        write_handoff(path, Handoff(status="running", manifest="/batch/tg-1-99.yaml"))
        self.assertIsNone(read_handoff(path).reason)

    def test_missing_file_returns_none_rather_than_raising(self):
        self.assertIsNone(read_handoff(Path(tempfile.mkdtemp()) / "nope.json"))

    def test_malformed_json_returns_none_rather_than_raising(self):
        path = Path(tempfile.mkdtemp()) / "bad.json"
        path.write_text("not json", encoding="utf-8")
        self.assertIsNone(read_handoff(path))

    def test_missing_status_key_returns_none_rather_than_raising(self):
        path = Path(tempfile.mkdtemp()) / "bad.json"
        path.write_text('{"manifest": "x"}', encoding="utf-8")
        self.assertIsNone(read_handoff(path))

    def test_write_is_atomic_no_leftover_tmp(self):
        path = Path(tempfile.mkdtemp()) / "tg-1.handoff.json"
        write_handoff(path, Handoff(status="running", manifest="/batch/tg-1-99.yaml"))
        self.assertFalse(path.with_suffix(path.suffix + ".tmp").exists())


class TestClaimMailbox(unittest.TestCase):
    def test_nothing_queued_returns_none(self):
        original = Path(tempfile.mkdtemp()) / "tg-1.yaml"
        self.assertIsNone(claim_mailbox(original))

    def test_claims_and_renames_to_a_permanent_working_path(self):
        tmpdir = Path(tempfile.mkdtemp())
        original = tmpdir / "tg-1.yaml"
        mailbox_path(original).write_text("runs: []\n", encoding="utf-8")
        claimed = claim_mailbox(original)
        self.assertIsNotNone(claimed)
        self.assertTrue(claimed.exists())
        self.assertEqual(claimed.read_text(encoding="utf-8"), "runs: []\n")
        self.assertFalse(mailbox_path(original).exists())

    def test_claiming_frees_the_mailbox_for_a_third_job(self):
        # The whole point of renaming rather than reading in place: the
        # instant this returns, bot.py's write guard sees an empty mailbox
        # again and can accept another /confirm while this one runs.
        tmpdir = Path(tempfile.mkdtemp())
        original = tmpdir / "tg-1.yaml"
        mailbox_path(original).write_text("runs: []\n", encoding="utf-8")
        claim_mailbox(original)
        mailbox_path(original).write_text("runs: []\n# a third job\n", encoding="utf-8")
        second_claim = claim_mailbox(original)
        self.assertIsNotNone(second_claim)

    def test_claimed_paths_do_not_collide_across_calls(self):
        tmpdir = Path(tempfile.mkdtemp())
        original = tmpdir / "tg-1.yaml"
        mailbox_path(original).write_text("a\n", encoding="utf-8")
        first = claim_mailbox(original)
        mailbox_path(original).write_text("b\n", encoding="utf-8")
        import time
        time.sleep(1.01)   # the working name is timestamp-based, second resolution
        second = claim_mailbox(original)
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
