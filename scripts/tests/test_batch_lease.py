import sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib_ext.lease import Lease, clear_lease, read_lease, write_lease


class TestLease(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "pod-lease.json"

    def test_roundtrip(self):
        lease = Lease(pod_id="abc123", provisioned_at=1000.0,
                      manifest="batch/x.yaml", abs_max_min=240)
        write_lease(self.tmp, lease)
        self.assertEqual(read_lease(self.tmp), lease)

    def test_missing_file_is_none(self):
        self.assertIsNone(read_lease(self.tmp))

    def test_corrupt_file_is_none_not_crash(self):
        # A watchdog that crashes on a half-written lease stops guarding the
        # thing it exists to guard. Truncated JSON must read as "no lease".
        self.tmp.write_text('{"pod_id": "abc', encoding="utf-8")
        self.assertIsNone(read_lease(self.tmp))

    def test_clear_is_idempotent(self):
        write_lease(self.tmp, Lease("a", 1.0, "b.yaml", 240))
        clear_lease(self.tmp)
        clear_lease(self.tmp)
        self.assertIsNone(read_lease(self.tmp))

    def test_provider_defaults_to_runpod(self):
        self.assertEqual(Lease("a", 1.0, "b.yaml", 240).provider, "runpod")

    def test_provider_roundtrip(self):
        lease = Lease(pod_id="777", provisioned_at=1000.0, manifest="batch/x.yaml",
                      abs_max_min=240, provider="vast")
        write_lease(self.tmp, lease)
        self.assertEqual(read_lease(self.tmp), lease)

    def test_a_lease_written_before_providers_existed_reads_as_runpod(self):
        # Leases already on the VPS have no "provider" key. They must stay valid, or the
        # watchdog would treat a live RunPod pod's lease as garbage and reap it as an orphan.
        self.tmp.write_text(
            '{"pod_id": "abc", "provisioned_at": 5.0, "manifest": "batch/x.yaml", '
            '"abs_max_min": 90}', encoding="utf-8")
        self.assertEqual(read_lease(self.tmp),
                         Lease("abc", 5.0, "batch/x.yaml", 90, provider="runpod"))

    def test_an_empty_provider_reads_as_runpod(self):
        self.tmp.write_text(
            '{"pod_id": "abc", "provisioned_at": 5.0, "manifest": "m", '
            '"abs_max_min": 90, "provider": ""}', encoding="utf-8")
        self.assertEqual(read_lease(self.tmp).provider, "runpod")


if __name__ == "__main__":
    unittest.main()
