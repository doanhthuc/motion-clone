import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib_ext.migrate_lease import (MigrateLease, clear_migrate_lease,
                                        read_migrate_lease, write_migrate_lease)


class TestMigrateLease(unittest.TestCase):
    def test_round_trips_every_field(self):
        path = Path(tempfile.mkdtemp()) / "lease.json"
        lease = MigrateLease(pod_a_id="pod-a", pod_b_id="pod-b",
                             started_at=1000.5, to_dc="EU-CZ-1")
        write_migrate_lease(path, lease)
        self.assertEqual(read_migrate_lease(path), lease)

    def test_missing_file_returns_none_rather_than_raising(self):
        self.assertIsNone(read_migrate_lease(Path(tempfile.mkdtemp()) / "nope.json"))

    def test_malformed_json_returns_none_rather_than_raising(self):
        path = Path(tempfile.mkdtemp()) / "bad.json"
        path.write_text("not json", encoding="utf-8")
        self.assertIsNone(read_migrate_lease(path))

    def test_missing_field_returns_none_rather_than_raising(self):
        path = Path(tempfile.mkdtemp()) / "bad.json"
        path.write_text('{"pod_a_id": "a"}', encoding="utf-8")
        self.assertIsNone(read_migrate_lease(path))

    def test_write_is_atomic_no_leftover_tmp(self):
        path = Path(tempfile.mkdtemp()) / "lease.json"
        write_migrate_lease(path, MigrateLease(pod_a_id="a", pod_b_id="b",
                                               started_at=1.0, to_dc="EU-CZ-1"))
        self.assertFalse(path.with_suffix(path.suffix + ".tmp").exists())

    def test_clear_removes_the_file_and_tolerates_it_missing(self):
        path = Path(tempfile.mkdtemp()) / "lease.json"
        write_migrate_lease(path, MigrateLease(pod_a_id="a", pod_b_id="b",
                                               started_at=1.0, to_dc="EU-CZ-1"))
        clear_migrate_lease(path)
        self.assertFalse(path.exists())
        clear_migrate_lease(path)   # must not raise the second time


if __name__ == "__main__":
    unittest.main()
