import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib_ext.vast_account import account_credit

# The fields that matter from `vastai show user --raw`, as captured 2026-09-19 (other keys omitted).
_USER_JSON = json.dumps({"balance": 0, "credit": 10.701384150700019, "can_pay": True,
                         "username": "x"})


class TestAccountCredit(unittest.TestCase):
    @patch("subprocess.run")
    def test_reads_credit_not_balance(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout=_USER_JSON, stderr="")
        self.assertAlmostEqual(account_credit(), 10.701384150700019)
        self.assertEqual(mock_run.call_args[0][0], ["vastai", "show", "user", "--raw"])

    @patch("subprocess.run")
    def test_non_zero_exit_raises_with_the_cli_message(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="no api key")
        with self.assertRaisesRegex(RuntimeError, "no api key"):
            account_credit()

    @patch("subprocess.run")
    def test_malformed_json_raises(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="not json", stderr="")
        with self.assertRaises(RuntimeError):
            account_credit()

    @patch("subprocess.run")
    def test_missing_credit_raises_rather_than_reporting_zero(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps({"balance": 0}),
                                          stderr="")
        with self.assertRaisesRegex(RuntimeError, "no credit"):
            account_credit()

    @patch("subprocess.run")
    def test_a_boolean_credit_is_not_a_number(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps({"credit": True}),
                                          stderr="")
        with self.assertRaises(RuntimeError):
            account_credit()

    @patch("subprocess.run", side_effect=FileNotFoundError("vastai"))
    def test_a_missing_binary_raises_runtimeerror(self, _run):
        with self.assertRaisesRegex(RuntimeError, "could not run vastai"):
            account_credit()

    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired("vastai", 30))
    def test_a_hang_raises_runtimeerror(self, _run):
        with self.assertRaisesRegex(RuntimeError, "could not run vastai"):
            account_credit()


if __name__ == "__main__":
    unittest.main()
