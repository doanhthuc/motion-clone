"""Wiring that tests elsewhere cannot see: the Makefile and shell scripts that read only .env.

These run `make -n` (prints the recipe, executes nothing) and small bash snippets; no rental,
no network.
"""
import json
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

    def test_smoke_sources_the_helper_through_root_not_its_own_location(self):
        # pod-smoke.sh does `cd "$(dirname "$0")/.."` before sourcing, so a BASH_SOURCE-relative
        # path is resolved from the NEW cwd and breaks for any relative $0 other than
        # `bash scripts/pod-smoke.sh` from the repo root. Then pod_volume is "command not found",
        # POD_VOLUME comes out empty, and layers 5-6 print a false "POD_VOLUME not set" skip.
        text = (ROOT / "scripts" / "pod-smoke.sh").read_text(encoding="utf-8")
        src = [ln for ln in text.splitlines()
               if ln.startswith(". ") and "lib-gpu-provider.sh" in ln]
        self.assertEqual(len(src), 1, src)
        self.assertIn("$ROOT/scripts/lib-gpu-provider.sh", src[0])
        self.assertNotIn("BASH_SOURCE", src[0])

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


class TestGpuDestroyVerifiesAgainstTheV1Listing(unittest.TestCase):
    def test_the_vast_verify_uses_instances_v1_not_the_deprecated_listing(self):
        # `vastai show instances 2>/dev/null | grep -q` printed "destroyed — verified gone" over a
        # live instance whenever the deprecated command errored (stderr dropped, grep finds
        # nothing). VastCtl.list_pods already reads instances-v1 --raw --all.
        out = _make("gpu-destroy", env={"GPU_PROVIDER": "vast"})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("instances-v1", out.stdout)
        self.assertNotIn("vastai show instances 2>/dev/null", out.stdout)
        self.assertIn("COULD NOT VERIFY", out.stdout)


class TestGpuDestroyVastVerifyBehaviour(unittest.TestCase):
    """Run the real recipe against a fake `vastai` in a scratch dir: its own .env, a stubbed
    env-clear-pod.sh (the marker file says whether .env WOULD have been wiped) and a no-op sleep.
    Nothing here can reach a real instance or the real .env."""

    ID = "12345"

    def _destroy(self, listing: str, rc: int = 0) -> tuple[subprocess.CompletedProcess, bool]:
        d = Path(tempfile.mkdtemp())
        (d / ".env").write_text(f"GPU_PROVIDER=runpod\nGPU_INSTANCE_ID={self.ID}\n", encoding="utf-8")
        (d / "Makefile").write_text((ROOT / "Makefile").read_text(encoding="utf-8"), encoding="utf-8")
        (d / "scripts").mkdir()
        (d / "scripts" / "env-clear-pod.sh").write_text(
            f'#!/bin/bash\ntouch "{d}/env-cleared"\n', encoding="utf-8")
        (d / "listing.txt").write_text(listing, encoding="utf-8")
        bin_dir = d / "bin"
        bin_dir.mkdir()
        (bin_dir / "vastai").write_text(
            '#!/bin/bash\n'
            'if [ "$1" = destroy ]; then cat >/dev/null; exit 0; fi\n'
            f'cat "{d}/listing.txt"; exit {rc}\n', encoding="utf-8")
        (bin_dir / "sleep").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        for f in bin_dir.iterdir():
            f.chmod(0o755)
        env = {k: v for k, v in os.environ.items() if k not in ("GPU_PROVIDER", "GPU_INSTANCE_ID")}
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
        out = subprocess.run(["make", "gpu-destroy", "GPU_PROVIDER=vast"], cwd=d, env=env,
                             capture_output=True, text=True)
        return out, (d / "env-cleared").exists()

    def test_a_listing_that_errors_is_not_read_as_gone(self):
        out, cleared = self._destroy("Traceback: unknown command", rc=2)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("COULD NOT VERIFY", out.stdout)
        self.assertIn("Traceback: unknown command", out.stdout)
        self.assertNotIn("verified gone", out.stdout)
        self.assertFalse(cleared, ".env was cleared although nothing was verified")

    def test_an_instance_still_listed_is_still_alive_and_keeps_env(self):
        out, cleared = self._destroy(f'[\n  {{\n    "id": {self.ID},\n    "label": "x"\n  }}\n]')
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("STILL ALIVE", out.stdout)
        self.assertFalse(cleared)

    def test_a_longer_id_that_merely_starts_with_ours_does_not_count(self):
        out, cleared = self._destroy(f'[{{"id": {self.ID}6, "label": "x"}}]')
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("verified gone from 'vastai show instances-v1'", out.stdout)
        self.assertTrue(cleared)

    def test_a_gone_instance_is_verified_and_env_cleared(self):
        out, cleared = self._destroy("[]")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("verified gone", out.stdout)
        self.assertTrue(cleared)


class TestLeaseDecidesTheDestroyProvider(unittest.TestCase):
    """A hand-typed `make gpu-destroy` after a Vast run runs in a shell where GPU_PROVIDER is unset,
    so .env's runpod would win: `runpodctl pod delete <vast id> || true`, a RunPod re-list that
    finds nothing, "destroyed — verified", and .env cleared over a still-billing Vast instance.
    The lease knows the provider, but only counts when its pod id is the one being destroyed."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        # make 3.81 has no --eval: print a variable through a second makefile instead.
        self.print_mk = self.tmp / "print.mk"
        self.print_mk.write_text("print-%: ; @echo $($*)\n", encoding="utf-8")
        self.absent = str(self.tmp / "no-such-lease.json")

    def _lease(self, **fields) -> str:
        path = self.tmp / "pod-lease.json"
        # Same shape as batchlib_ext.lease.write_lease: json.dumps(indent=2), one key per line.
        path.write_text(json.dumps(fields, indent=2), encoding="utf-8")
        return str(path)

    def _eff(self, lease_file: str, *extra: str, env: dict | None = None) -> str:
        e = {k: v for k, v in os.environ.items() if k not in ("GPU_PROVIDER", "GPU_INSTANCE_ID")}
        e.update(env or {})
        out = subprocess.run(
            ["make", "-s", "-f", "Makefile", "-f", str(self.print_mk), "print-GPU_PROVIDER_EFF",
             f"LEASE_FILE={lease_file}", *extra],
            cwd=ROOT, env=e, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip()

    def test_a_matching_vast_lease_picks_vast(self):
        lease = self._lease(pod_id="777", provider="vast", manifest="batch/x.yaml")
        self.assertEqual(self._eff(lease, "GPU_INSTANCE_ID=777"), "vast")

    def test_a_lease_for_another_pod_is_ignored(self):
        lease = self._lease(pod_id="999", provider="vast", manifest="batch/x.yaml")
        self.assertEqual(self._eff(lease, "GPU_INSTANCE_ID=777"),
                         self._eff(self.absent, "GPU_INSTANCE_ID=777"))

    def test_a_lease_without_a_provider_key_falls_through(self):
        lease = self._lease(pod_id="777", manifest="batch/x.yaml")
        self.assertEqual(self._eff(lease, "GPU_INSTANCE_ID=777"),
                         self._eff(self.absent, "GPU_INSTANCE_ID=777"))

    def test_an_exported_provider_beats_a_matching_lease(self):
        lease = self._lease(pod_id="777", provider="vast", manifest="batch/x.yaml")
        self.assertEqual(
            self._eff(lease, "GPU_INSTANCE_ID=777", env={"GPU_PROVIDER": "runpod"}), "runpod")

    def test_gpu_destroy_follows_a_matching_vast_lease(self):
        lease = self._lease(pod_id="777", provider="vast", manifest="batch/x.yaml")
        out = _make("gpu-destroy", f"LEASE_FILE={lease}", "GPU_INSTANCE_ID=777")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("vastai destroy instance", out.stdout)
        self.assertNotIn("runpodctl pod delete", out.stdout)


class TestVastInstancesAreLabelled(unittest.TestCase):
    def test_the_rent_function_labels_with_a_name_tier_three_may_destroy(self):
        # Two hand-copied names that must agree: the label vast_rent.py puts on every instance
        # and watchdog.DESTROYABLE_NAMES. This is the gate the comment above DESTROYABLE_NAMES
        # says did not exist.
        import vast_rent
        self.assertIn(vast_rent.VAST_LABEL, DESTROYABLE_NAMES)

    def test_pod_provision_hands_the_vast_branch_to_the_rent_function(self):
        text = (ROOT / "scripts" / "pod-provision.sh").read_text(encoding="utf-8")
        self.assertIn("vast_rent.py", text)
        # The old cheapest-first search and the raw `vastai create` are gone from the script.
        self.assertNotIn("vastai create instance", text)
        self.assertNotIn("vastai search offers", text)


class TestPodWaitDirectAddress(unittest.TestCase):
    def test_the_vast_probe_asks_for_the_direct_address_and_keeps_the_proxy_as_fallback(self):
        text = (ROOT / "scripts" / "pod-wait.sh").read_text(encoding="utf-8")
        self.assertIn("vast_rent.py --ssh-target", text)
        # the proxy values are still read, as the fallback when ssh-url gives nothing
        self.assertIn('"ssh_host"', text)
        self.assertIn('"ssh_port"', text)


if __name__ == "__main__":
    unittest.main()
