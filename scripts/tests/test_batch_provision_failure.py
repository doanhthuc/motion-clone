import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib_ext.provision_failure import (ProvisionFailure,
                                            clear_provision_failure,
                                            provision_failure_path,
                                            read_provision_failure,
                                            write_provision_failure)


class TestPath(unittest.TestCase):
    def test_named_from_the_manifest_stem(self):
        original = Path("/batch/tg-1.yaml")
        self.assertEqual(provision_failure_path(original),
                         Path("/batch/tg-1.provision-failed.json"))


class TestWriteReadRoundTrip(unittest.TestCase):
    def test_round_trips_every_field(self):
        path = Path(tempfile.mkdtemp()) / "tg-1.provision-failed.json"
        write_provision_failure(path, ProvisionFailure(
            gpu="NVIDIA GeForce RTX 5090", datacenter="EU-RO-1",
            stock_out=True, detail="het may"))
        self.assertEqual(read_provision_failure(path),
                         ProvisionFailure(gpu="NVIDIA GeForce RTX 5090",
                                          datacenter="EU-RO-1", stock_out=True,
                                          detail="het may"))

    def test_datacenter_can_be_none(self):
        path = Path(tempfile.mkdtemp()) / "tg-1.provision-failed.json"
        write_provision_failure(path, ProvisionFailure(
            gpu="NVIDIA GeForce RTX 5090", datacenter=None,
            stock_out=False, detail="runpodctl: connection refused"))
        self.assertIsNone(read_provision_failure(path).datacenter)

    def test_missing_file_returns_none_rather_than_raising(self):
        self.assertIsNone(read_provision_failure(
            Path(tempfile.mkdtemp()) / "nope.json"))

    def test_malformed_json_returns_none_rather_than_raising(self):
        path = Path(tempfile.mkdtemp()) / "bad.json"
        path.write_text("not json", encoding="utf-8")
        self.assertIsNone(read_provision_failure(path))

    def test_missing_required_key_returns_none_rather_than_raising(self):
        path = Path(tempfile.mkdtemp()) / "bad.json"
        path.write_text('{"datacenter": "EU-RO-1"}', encoding="utf-8")
        self.assertIsNone(read_provision_failure(path))

    def test_write_is_atomic_no_leftover_tmp(self):
        path = Path(tempfile.mkdtemp()) / "tg-1.provision-failed.json"
        write_provision_failure(path, ProvisionFailure(
            gpu="x", datacenter=None, stock_out=False, detail=""))
        self.assertFalse(path.with_suffix(path.suffix + ".tmp").exists())


class TestClear(unittest.TestCase):
    def test_removes_the_file(self):
        path = Path(tempfile.mkdtemp()) / "tg-1.provision-failed.json"
        write_provision_failure(path, ProvisionFailure(
            gpu="x", datacenter=None, stock_out=False, detail=""))
        clear_provision_failure(path)
        self.assertFalse(path.exists())

    def test_missing_file_is_not_an_error(self):
        clear_provision_failure(Path(tempfile.mkdtemp()) / "nope.json")


if __name__ == "__main__":
    unittest.main()
