import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from batchlib_ext.vast_scoreboard import (BLACKLIST_TTL_S, WORST_KNOWN_PULL_S,
                                          MachineRecord, Scoreboard, load_board,
                                          save_board)


def rec(machine_id=1, pull_s=300.0, outcome="ok", measured_at=1000.0, **kw):
    return MachineRecord(machine_id=machine_id, pull_s=pull_s,
                         model_mbps=kw.get("model_mbps"), gb=kw.get("gb"),
                         measured_at=measured_at, outcome=outcome)


class TestScoreboard(unittest.TestCase):
    def test_an_unknown_machine_is_scored_as_the_slowest_ever_seen(self):
        # Optimism about a machine nobody measured is how a 556 s pull gets rented twice.
        self.assertEqual(Scoreboard({}).ready_estimate_s(7), WORST_KNOWN_PULL_S)

    def test_a_measured_ok_machine_uses_its_own_pull_time(self):
        board = Scoreboard({})
        board.record(rec(machine_id=144253, pull_s=35.0))
        self.assertEqual(board.ready_estimate_s(144253), 35.0)

    def test_a_failed_machine_is_not_trusted_for_its_time_even_after_the_ttl(self):
        board = Scoreboard({})
        board.record(rec(machine_id=2, pull_s=481.0, outcome="slow_pull"))
        self.assertEqual(board.ready_estimate_s(2), WORST_KNOWN_PULL_S)

    def test_a_slow_machine_is_blacklisted_until_the_ttl_passes(self):
        board = Scoreboard({})
        board.record(rec(machine_id=2, outcome="slow_pull", measured_at=1000.0))
        self.assertTrue(board.is_blacklisted(2, now=1000.0 + BLACKLIST_TTL_S - 1))
        self.assertFalse(board.is_blacklisted(2, now=1000.0 + BLACKLIST_TTL_S))

    def test_an_ok_machine_is_never_blacklisted(self):
        board = Scoreboard({})
        board.record(rec(machine_id=3, outcome="ok", measured_at=0.0))
        self.assertFalse(board.is_blacklisted(3, now=10.0))

    def test_an_unknown_machine_is_not_blacklisted(self):
        self.assertFalse(Scoreboard({}).is_blacklisted(9, now=1.0))

    def test_known_good_is_fastest_first_ok_only_and_limited(self):
        board = Scoreboard({})
        board.record(rec(machine_id=1, pull_s=300.0))
        board.record(rec(machine_id=2, pull_s=35.0))
        board.record(rec(machine_id=3, pull_s=100.0))
        board.record(rec(machine_id=4, pull_s=10.0, outcome="failed"))
        board.record(rec(machine_id=5, pull_s=None, outcome="ok"))
        self.assertEqual(board.known_good(limit=2), [2, 3])

    def test_a_newer_measurement_replaces_the_older_one(self):
        board = Scoreboard({})
        board.record(rec(machine_id=1, pull_s=300.0, measured_at=1.0))
        board.record(rec(machine_id=1, pull_s=40.0, measured_at=2.0))
        self.assertEqual(board.get(1).pull_s, 40.0)


class TestPersistence(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "vast-machines.json"

    def test_roundtrip(self):
        board = Scoreboard({})
        board.record(rec(machine_id=144253, pull_s=35.0, model_mbps=241.7, gb=34.4))
        save_board(self.path, board)
        again = load_board(self.path)
        self.assertEqual(again.get(144253), board.get(144253))

    def test_a_missing_file_is_an_empty_board(self):
        self.assertEqual(load_board(self.path).records(), {})

    def test_a_corrupt_file_is_an_empty_board_not_a_crash(self):
        # A rent that crashes on a half-written history would block the fallback exactly when
        # RunPod is out of stock. Unreadable means "no history".
        self.path.write_text('{"1": {"machine_id": 1,', encoding="utf-8")
        self.assertEqual(load_board(self.path).records(), {})

    def test_save_leaves_no_temp_file_behind(self):
        save_board(self.path, Scoreboard({}))
        self.assertEqual([p.name for p in self.path.parent.iterdir()], [self.path.name])


class TestGitignore(unittest.TestCase):
    def test_the_scoreboard_file_is_git_ignored(self):
        # It is per-machine state about rented hosts, not repo content, and the repo is public.
        out = subprocess.run(["git", "check-ignore", "-q", "batch/vast-machines.json"],
                             cwd=ROOT)
        self.assertEqual(out.returncode, 0, "batch/vast-machines.json is not git-ignored")


if __name__ == "__main__":
    unittest.main()
