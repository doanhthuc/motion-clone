import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib_ext.runpod_account import account_balance

# Shape of `runpodctl user -o json`, runpodctl 2.14.0, captured 2026-09-18.
_USER_JSON = json.dumps({
    "clientBalance": 7.2482651598, "currentSpendPerHr": 0.01,
    "email": "x@example.com", "id": "user_x", "notifyLowBalance": True,
    "notifyPodsGeneral": True, "notifyPodsStale": True, "spendLimit": 80,
})


class TestAccountBalance(unittest.TestCase):
    @patch("subprocess.run")
    def test_reads_client_balance(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout=_USER_JSON, stderr="")
        self.assertAlmostEqual(account_balance(), 7.2482651598)
        self.assertEqual(mock_run.call_args[0][0], ["runpodctl", "user", "-o", "json"])

    @patch("subprocess.run")
    def test_non_zero_exit_raises(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="unauthorized")
        with self.assertRaisesRegex(RuntimeError, "unauthorized"):
            account_balance()

    @patch("subprocess.run")
    def test_malformed_json_raises(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="not json", stderr="")
        with self.assertRaises(RuntimeError):
            account_balance()

    @patch("subprocess.run")
    def test_missing_balance_field_raises_rather_than_reporting_zero(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
        with self.assertRaisesRegex(RuntimeError, "clientBalance"):
            account_balance()

    @patch("subprocess.run", side_effect=FileNotFoundError("runpodctl"))
    def test_missing_binary_becomes_runtime_error(self, _run):
        with self.assertRaises(RuntimeError):
            account_balance()

    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="runpodctl", timeout=30))
    def test_hung_cli_becomes_runtime_error(self, _run):
        with self.assertRaises(RuntimeError):
            account_balance()


if __name__ == "__main__":
    unittest.main()
