"""Wiring that tests elsewhere cannot see: the Makefile and shell scripts that read only .env.

These run `make -n` (prints the recipe, executes nothing) and small bash snippets; no rental,
no network.
"""
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from batchlib_ext.watchdog import DESTROYABLE_NAMES

LIB = ROOT / "scripts" / "lib-gpu-provider.sh"


def _make(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    e = {k: v for k, v in os.environ.items() if k != "GPU_PROVIDER"}
    e.update(env or {})
    return subprocess.run(["make", "-n", *args], cwd=ROOT, env=e,
                          capture_output=True, text=True)


class TestDrainTarget(unittest.TestCase):
    def test_provider_is_forwarded_to_drain_py(self):
        out = _make("drain", "FILE=batch/x.yaml", "PROVIDER=vast", "CONFIRM=yes")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("--provider vast", out.stdout)
        self.assertIn("--yes", out.stdout)

    def test_no_provider_adds_no_flag(self):
        out = _make("drain", "FILE=batch/x.yaml")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertNotIn("--provider", out.stdout)


if __name__ == "__main__":
    unittest.main()
