import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib_ext.gpu_stock import Stock, stock_at, volume_datacenter

_GPU_LIST_JSON = json.dumps([
    {"gpuId": "NVIDIA GeForce RTX 5090", "displayName": "RTX 5090",
     "securePricePerHr": 0.99,
     "dataCenterAvailability": [
         {"dataCenterId": "EU-RO-1", "stockStatus": "Low"},
         {"dataCenterId": "EU-CZ-1", "stockStatus": "High"},
     ]},
    {"gpuId": "NVIDIA GeForce RTX 4090", "displayName": "RTX 4090",
     "securePricePerHr": 0.74,
     "dataCenterAvailability": [
         {"dataCenterId": "EU-RO-1", "stockStatus": "Medium"},
     ]},
])


class TestVolumeDatacenter(unittest.TestCase):
    def test_empty_volume_id_returns_none_without_a_subprocess_call(self):
        with patch("subprocess.run") as mock_run:
            self.assertIsNone(volume_datacenter(""))
        mock_run.assert_not_called()

    @patch("subprocess.run")
    def test_reads_the_datacenter_id_from_json(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout=json.dumps({"dataCenterId": "EU-RO-1"}), stderr="")
        self.assertEqual(volume_datacenter("vol-1"), "EU-RO-1")
        self.assertEqual(mock_run.call_args[0][0],
                         ["runpodctl", "network-volume", "get", "vol-1", "-o", "json"])

    @patch("subprocess.run")
    def test_non_zero_exit_returns_none_rather_than_raising(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="not found")
        self.assertIsNone(volume_datacenter("vol-1"))

    @patch("subprocess.run")
    def test_malformed_json_returns_none_rather_than_raising(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="not json", stderr="")
        self.assertIsNone(volume_datacenter("vol-1"))

    @patch("subprocess.run", side_effect=FileNotFoundError("no such file: runpodctl"))
    def test_a_missing_runpodctl_binary_returns_none_rather_than_raising(self, _run):
        # FileNotFoundError is an OSError, a SIBLING of subprocess.SubprocessError
        # — not a subclass. Catching only the latter let this raise straight
        # through a function documented as "never raises".
        self.assertIsNone(volume_datacenter("vol-1"))

    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="runpodctl", timeout=30))
    def test_a_hung_runpodctl_returns_none_rather_than_raising(self, _run):
        self.assertIsNone(volume_datacenter("vol-1"))


class TestStockAt(unittest.TestCase):
    @patch("subprocess.run")
    def test_returns_one_entry_per_datacenter_for_requested_gpus_only(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout=_GPU_LIST_JSON, stderr="")
        result = stock_at(["NVIDIA GeForce RTX 5090", "NVIDIA GeForce RTX 4090"])
        self.assertEqual(mock_run.call_args[0][0],
                         ["runpodctl", "gpu", "list", "-o", "json"])
        self.assertEqual(len(result["NVIDIA GeForce RTX 5090"]), 2)
        self.assertEqual(len(result["NVIDIA GeForce RTX 4090"]), 1)
        five090_eu_ro_1 = next(e for e in result["NVIDIA GeForce RTX 5090"]
                               if e.datacenter_id == "EU-RO-1")
        self.assertEqual(five090_eu_ro_1,
                         Stock(gpu_id="NVIDIA GeForce RTX 5090", display_name="RTX 5090",
                              price_per_hr=0.99, datacenter_id="EU-RO-1",
                              stock_status="Low"))

    @patch("subprocess.run")
    def test_a_gpu_id_not_requested_is_excluded(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout=_GPU_LIST_JSON, stderr="")
        result = stock_at(["NVIDIA GeForce RTX 5090"])
        self.assertNotIn("NVIDIA GeForce RTX 4090", result)

    @patch("subprocess.run")
    def test_a_gpu_id_runpodctl_never_lists_is_simply_absent(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout=_GPU_LIST_JSON, stderr="")
        result = stock_at(["NVIDIA RTX PRO 4500 Blackwell"])
        self.assertEqual(result, {})

    @patch("subprocess.run")
    def test_non_zero_exit_raises_runtime_error(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="",
                                          stderr="API key not configured")
        with self.assertRaises(RuntimeError) as cm:
            stock_at(["NVIDIA GeForce RTX 5090"])
        self.assertIn("API key not configured", str(cm.exception))

    @patch("subprocess.run")
    def test_malformed_json_raises_runtime_error(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="not json", stderr="")
        with self.assertRaises(RuntimeError) as cm:
            stock_at(["NVIDIA GeForce RTX 5090"])
        self.assertIn("invalid JSON", str(cm.exception))

    @patch("subprocess.run", side_effect=FileNotFoundError("no such file: runpodctl"))
    def test_a_missing_runpodctl_binary_raises_runtime_error(self, _run):
        # Unlike volume_datacenter, this function's contract IS to raise on
        # failure — callers (bot.py's _report_gpu_stock, _offer_run_confirm)
        # decide how to degrade. A missing binary must become the same
        # RuntimeError as every other failure mode here, not a different
        # exception type those callers never learned to catch.
        with self.assertRaises(RuntimeError) as cm:
            stock_at(["NVIDIA GeForce RTX 5090"])
        self.assertIn("runpodctl", str(cm.exception))

    @patch("subprocess.run",
          side_effect=subprocess.TimeoutExpired(cmd="runpodctl", timeout=30))
    def test_a_hung_runpodctl_raises_runtime_error(self, _run):
        with self.assertRaises(RuntimeError):
            stock_at(["NVIDIA GeForce RTX 5090"])


if __name__ == "__main__":
    unittest.main()
