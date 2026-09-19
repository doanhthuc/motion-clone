import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib_ext import vast_quote
from batchlib_ext.vast_quote import fetch_quote, last_quote

_JSON = json.dumps({"offer_id": 4401, "machine_id": 55, "dph": 0.45, "gpu": "RTX 5090",
                    "location": "Bulgaria, BG", "ready_s": 556.0, "known": False,
                    "bandwidth_usd": 0.07, "gb": 51.8, "qualifying": 6})


def _proc(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class FakeRun:
    """Stands in for subprocess.run: records every call, answers from a queue (last one repeats)."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        answer = self.answers[min(len(self.calls) - 1, len(self.answers) - 1)]
        if isinstance(answer, BaseException):
            raise answer
        return answer


class TestFetchQuote(unittest.TestCase):
    def setUp(self):
        vast_quote._CACHE.clear()
        vast_quote._last = None
        self.clock = [1000.0]

    def _fetch(self, run, gb=51.8, **kw):
        return fetch_quote(gb, run=run, now=lambda: self.clock[0], repo_root=Path("/repo"), **kw)

    def test_parses_the_last_json_line_amid_log_noise(self):
        run = FakeRun(_proc(stdout="\x1b[36m==>\x1b[0m something\nnoise\n" + _JSON + "\n"))
        q = self._fetch(run)
        self.assertEqual((q.offer_id, q.machine_id, q.dph, q.location), (4401, 55, 0.45, "Bulgaria, BG"))
        self.assertFalse(q.known)
        self.assertEqual(q.ready_s, 556.0)

    def test_runs_the_provision_script_in_quote_mode_on_vast_with_no_volume(self):
        run = FakeRun(_proc(stdout=_JSON))
        self._fetch(run)
        argv, kwargs = run.calls[0]
        self.assertEqual(argv, ["bash", "/repo/scripts/pod-provision.sh"])
        self.assertEqual(kwargs["cwd"], Path("/repo"))
        env = kwargs["env"]
        self.assertEqual((env["GPU_PROVIDER"], env["POD_VOLUME"], env["VAST_QUOTE"]),
                         ("vast", "", "1"))
        self.assertEqual(env["VAST_GB"], "51.8")

    def test_confirm_never_reaches_the_child_even_when_the_bot_process_has_it(self):
        run = FakeRun(_proc(stdout=_JSON))
        with mock.patch.dict(os.environ, {"CONFIRM": "yes"}):
            self._fetch(run)
        self.assertNotIn("CONFIRM", run.calls[0][1]["env"])

    def test_a_second_call_inside_the_ttl_does_not_search_again(self):
        run = FakeRun(_proc(stdout=_JSON))
        first = self._fetch(run)
        self.clock[0] += vast_quote.TTL_SEC - 1
        self.assertIs(self._fetch(run), first)
        self.assertEqual(len(run.calls), 1)

    def test_force_and_an_expired_ttl_both_search_again(self):
        run = FakeRun(_proc(stdout=_JSON))
        self._fetch(run)
        self._fetch(run, force=True)
        self.clock[0] += vast_quote.TTL_SEC + 1
        self._fetch(run)
        self.assertEqual(len(run.calls), 3)

    def test_a_different_download_size_is_a_different_cache_entry(self):
        run = FakeRun(_proc(stdout=_JSON))
        self._fetch(run, gb=51.8)
        self._fetch(run, gb=17.4)
        self.assertEqual(len(run.calls), 2)

    def test_a_failure_raises_the_reason_without_ansi_or_the_knobs_paragraph(self):
        err = ("searching: gpu_name=RTX_5090 ...\n\x1b[31m ✗ \x1b[0m0 of 40 offers qualify "
               "(40 over price cap).\n  Knobs (env or .env): MAX_DPH, ...\n")
        run = FakeRun(_proc(returncode=1, stderr=err))
        with self.assertRaises(RuntimeError) as ctx:
            self._fetch(run)
        self.assertEqual(str(ctx.exception), "0 of 40 offers qualify (40 over price cap).")

    def test_a_failure_without_a_marker_reports_the_last_line(self):
        run = FakeRun(_proc(returncode=1, stderr="Traceback (most recent call last):\nValueError: boom\n"))
        with self.assertRaisesRegex(RuntimeError, "ValueError: boom"):
            self._fetch(run)

    def test_no_json_at_all_raises(self):
        with self.assertRaisesRegex(RuntimeError, "no JSON"):
            self._fetch(FakeRun(_proc(stdout="just log lines\n")))

    def test_unreadable_json_raises(self):
        with self.assertRaisesRegex(RuntimeError, "unreadable"):
            self._fetch(FakeRun(_proc(stdout='{"offer_id": "x"}')))

    def test_a_hang_or_a_missing_shell_raises_runtimeerror(self):
        for exc in (subprocess.TimeoutExpired("bash", 120), FileNotFoundError("bash")):
            with self.subTest(exc=type(exc).__name__):
                with self.assertRaisesRegex(RuntimeError, "could not run"):
                    self._fetch(FakeRun(exc))

    def test_last_quote_survives_a_later_failure(self):
        self.assertIsNone(last_quote())
        q = self._fetch(FakeRun(_proc(stdout=_JSON)))
        self.assertIs(last_quote(), q)
        with self.assertRaises(RuntimeError):
            self._fetch(FakeRun(_proc(returncode=1, stderr="x")), gb=99.0)
        self.assertIs(last_quote(), q)


if __name__ == "__main__":
    unittest.main()
