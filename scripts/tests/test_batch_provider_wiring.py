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


def _sh(env_file: str, extra_env: dict, snippet: str) -> str:
    """Run `snippet` in bash the way pod-*.sh do: a cwd holding a .env that env_get greps."""
    cwd = Path(tempfile.mkdtemp())
    (cwd / ".env").write_text(env_file, encoding="utf-8")
    script = (
        "env_get() { grep -E \"^$1=\" .env 2>/dev/null | cut -d= -f2- "
        "| sed -E 's/[[:space:]]*#.*$//' | tr -d '\"'; }\n"
        f'. "{LIB}"\n{snippet}\n')
    env = {k: v for k, v in os.environ.items() if k not in ("GPU_PROVIDER", "POD_VOLUME")}
    env.update(extra_env)
    out = subprocess.run(["bash", "-c", script], cwd=cwd, env=env,
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return out.stdout


class TestProviderHelpers(unittest.TestCase):
    DOTENV = "GPU_PROVIDER=runpod\nPOD_VOLUME=/workspace\n"

    def test_provider_comes_from_dotenv(self):
        self.assertEqual(_sh(self.DOTENV, {}, "gpu_provider"), "runpod")

    def test_the_environment_overrides_dotenv(self):
        self.assertEqual(_sh(self.DOTENV, {"GPU_PROVIDER": "vast"}, "gpu_provider"), "vast")

    def test_nothing_set_defaults_to_vast_like_pod_provision(self):
        self.assertEqual(_sh("", {}, "gpu_provider"), "vast")

    def test_runpod_keeps_its_volume(self):
        self.assertEqual(_sh(self.DOTENV, {}, "pod_volume"), "/workspace")

    def test_a_vast_run_never_has_a_volume_whatever_dotenv_says(self):
        # The bug this prevents: .env keeps the RunPod volume for the home provider, and
        # pod-bootstrap.sh would wire /workspace onto a Vast box that has no such mount.
        self.assertEqual(_sh(self.DOTENV, {"GPU_PROVIDER": "vast"}, "pod_volume"), "")


class TestScriptsUseTheHelpers(unittest.TestCase):
    def test_wait_bootstrap_and_smoke_source_the_shared_helper(self):
        for name in ("pod-wait.sh", "pod-bootstrap.sh", "pod-smoke.sh"):
            text = (ROOT / "scripts" / name).read_text(encoding="utf-8")
            self.assertIn("lib-gpu-provider.sh", text, name)

    def test_bootstrap_and_smoke_no_longer_read_the_volume_straight_from_dotenv(self):
        for name in ("pod-bootstrap.sh", "pod-smoke.sh"):
            text = (ROOT / "scripts" / name).read_text(encoding="utf-8")
            self.assertNotIn('POD_VOLUME="$(env_get POD_VOLUME)"', text, name)


class TestGpuDestroyTarget(unittest.TestCase):
    def test_a_vast_run_destroys_with_vastai_and_skips_the_volume_db_backup(self):
        out = _make("gpu-destroy", env={"GPU_PROVIDER": "vast"})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("vastai destroy instance", out.stdout)
        self.assertNotIn("runpodctl pod delete", out.stdout)
        # pod-pgdump.sh needs the RunPod volume; on Vast it can only fail noisily.
        self.assertNotIn("pod-pgdump.sh --dump", out.stdout)

    def test_a_runpod_run_still_destroys_with_runpodctl(self):
        out = _make("gpu-destroy", env={"GPU_PROVIDER": "runpod"})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("runpodctl pod delete", out.stdout)
        self.assertNotIn("vastai destroy instance", out.stdout)
        self.assertIn("pod-pgdump.sh --dump", out.stdout)


class TestVastInstancesAreLabelled(unittest.TestCase):
    def test_create_labels_the_instance_with_a_name_tier_three_may_destroy(self):
        # Two hand-copied lists (this label, watchdog.DESTROYABLE_NAMES) that must agree; the
        # comment above DESTROYABLE_NAMES admits no gate ties them together. This is that gate.
        text = (ROOT / "scripts" / "pod-provision.sh").read_text(encoding="utf-8")
        found = re.search(r"CREATE=\(vastai create instance .*--label (\S+?)\)", text)
        self.assertIsNotNone(found, "the vast create command carries no --label")
        self.assertIn(found.group(1), DESTROYABLE_NAMES)


if __name__ == "__main__":
    unittest.main()
