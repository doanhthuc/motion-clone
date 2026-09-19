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

    def _destroy(self, listing: str, rc: int = 0, env_id: str | None = None,
                 destroy_rc: int = 0) -> tuple[subprocess.CompletedProcess, bool]:
        d = Path(tempfile.mkdtemp())
        # env_id lets a test put trailing whitespace on the id in .env.
        gpu_id = env_id if env_id is not None else self.ID
        (d / ".env").write_text(f"GPU_PROVIDER=runpod\nGPU_INSTANCE_ID={gpu_id}\n", encoding="utf-8")
        (d / "Makefile").write_text((ROOT / "Makefile").read_text(encoding="utf-8"), encoding="utf-8")
        (d / "scripts").mkdir()
        (d / "scripts" / "env-clear-pod.sh").write_text(
            f'#!/bin/bash\ntouch "{d}/env-cleared"\n', encoding="utf-8")
        (d / "listing.txt").write_text(listing, encoding="utf-8")
        bin_dir = d / "bin"
        bin_dir.mkdir()
        (bin_dir / "vastai").write_text(
            '#!/bin/bash\n'
            f'if [ "$1" = destroy ]; then cat >/dev/null; exit {destroy_rc}; fi\n'
            f'cat "{d}/listing.txt"; exit {rc}\n', encoding="utf-8")
        (bin_dir / "sleep").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        for f in bin_dir.iterdir():
            f.chmod(0o755)
        env = {k: v for k, v in os.environ.items() if k not in ("GPU_PROVIDER", "GPU_INSTANCE_ID")}
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
        out = subprocess.run(["make", "gpu-destroy", "GPU_PROVIDER=vast"], cwd=d, env=env,
                             capture_output=True, text=True)
        return out, (d / "env-cleared").exists()

    def test_a_destroy_error_for_an_instance_already_gone_still_verifies_and_clears(self):
        # F3/I1: the destroy line had no `|| true` (unlike the RunPod branch's `pod delete …
        # || true`), so a destroy error for an instance already gone aborted `make` before the
        # verify below ever ran, and .env was never cleared even though the goal — the
        # instance not existing — was already met.
        out, cleared = self._destroy('{"instances": [], "next_token": null}', destroy_rc=1)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("verified gone", out.stdout)
        self.assertTrue(cleared)

    def test_a_destroy_error_for_an_instance_still_listed_is_still_alive(self):
        out, cleared = self._destroy(
            f'{{"instances": [{{"id": {self.ID}, "label": "x"}}]}}', destroy_rc=1)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("STILL ALIVE", out.stdout)
        self.assertFalse(cleared)

    def test_a_listing_that_errors_is_not_read_as_gone(self):
        out, cleared = self._destroy("Traceback: unknown command", rc=2)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("COULD NOT VERIFY", out.stdout)
        self.assertIn("Traceback: unknown command", out.stdout)
        self.assertNotIn("verified gone", out.stdout)
        self.assertFalse(cleared, ".env was cleared although nothing was verified")

    def test_an_instance_still_listed_is_still_alive_and_keeps_env(self):
        out, cleared = self._destroy(
            f'{{"instances": [\n  {{\n    "id": {self.ID},\n    "label": "x"\n  }}\n], "next_token": null}}')
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("STILL ALIVE", out.stdout)
        self.assertFalse(cleared)

    def test_a_longer_id_that_merely_starts_with_ours_does_not_count(self):
        out, cleared = self._destroy(f'{{"instances": [{{"id": {self.ID}6, "label": "x"}}]}}')
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("verified gone from 'vastai show instances-v1'", out.stdout)
        self.assertTrue(cleared)

    def test_a_gone_instance_is_verified_and_env_cleared(self):
        out, cleared = self._destroy('{"instances": [], "next_token": null}')
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("verified gone", out.stdout)
        self.assertTrue(cleared)

    def test_output_that_is_not_a_listing_is_not_read_as_gone_even_with_exit_zero(self):
        # An auth error printed by a CLI that still exits 0 contains no instance id either, and
        # used to read as "verified gone" while the instance kept billing.
        out, cleared = self._destroy("Error: invalid API key", rc=0)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("COULD NOT VERIFY", out.stdout)
        self.assertFalse(cleared, ".env was cleared although nothing was verified")

    def test_an_empty_listing_object_is_gone(self):
        out, cleared = self._destroy('{"instances": [], "next_token": null}')
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("verified gone", out.stdout)
        self.assertTrue(cleared)

    def test_trailing_whitespace_on_the_env_id_still_finds_a_live_instance(self):
        # `GPU_INSTANCE_ID=12345   ` expanded into the id regex and never matched a real row, so a
        # live instance read as gone.
        out, cleared = self._destroy(
            '{"instances": [{"id": 12345, "label": "x"}]}', env_id="12345   ")
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("STILL ALIVE", out.stdout)
        self.assertFalse(cleared)


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


class TestOwnerMarkerDecidesTheDestroyProvider(unittest.TestCase):
    """F1/C1: a rent that fails AFTER creating a Vast instance (STILL BILLING abandon,
    AmbiguousCreate, an unwind that could not destroy) leaves GPU_INSTANCE_ID set with NO
    lease at all — the lease rule above cannot help. GPU_INSTANCE_OWNER=vast:<id> is
    vast_rent.py's own receipt and must count only when its id matches the instance being
    destroyed. Runs the real Makefile in a scratch dir with its own .env — never the real
    repo's — so this can never read or steer a real rented instance."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "Makefile").write_text((ROOT / "Makefile").read_text(encoding="utf-8"),
                                           encoding="utf-8")
        (self.tmp / "print.mk").write_text("print-%: ; @echo $($*)\n", encoding="utf-8")

    def _eff(self, env_file: str, *extra: str, env: dict | None = None) -> str:
        (self.tmp / ".env").write_text(env_file, encoding="utf-8")
        e = {k: v for k, v in os.environ.items() if k not in ("GPU_PROVIDER", "GPU_INSTANCE_ID")}
        e.update(env or {})
        out = subprocess.run(
            ["make", "-s", "-f", "Makefile", "-f", "print.mk", "print-GPU_PROVIDER_EFF", *extra],
            cwd=self.tmp, env=e, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip()

    def test_a_matching_owner_marker_picks_vast(self):
        self.assertEqual(
            self._eff("GPU_PROVIDER=runpod\nGPU_INSTANCE_ID=777\nGPU_INSTANCE_OWNER=vast:777\n"),
            "vast")

    def test_a_mismatched_owner_marker_is_ignored(self):
        self.assertEqual(
            self._eff("GPU_PROVIDER=runpod\nGPU_INSTANCE_ID=777\nGPU_INSTANCE_OWNER=vast:999\n"),
            "runpod")

    def test_no_owner_key_falls_through_to_dotenv(self):
        self.assertEqual(self._eff("GPU_PROVIDER=runpod\nGPU_INSTANCE_ID=777\n"), "runpod")

    def test_an_exported_provider_beats_the_marker(self):
        self.assertEqual(
            self._eff("GPU_PROVIDER=runpod\nGPU_INSTANCE_ID=777\nGPU_INSTANCE_OWNER=vast:777\n",
                      env={"GPU_PROVIDER": "runpod"}),
            "runpod")

    def test_a_matching_lease_beats_a_mismatching_marker(self):
        # "mismatching" here means the marker's PROVIDER disagrees with the lease's, while both
        # ids match the instance being destroyed — precedence, not id-guarding, is under test.
        lease = self.tmp / "pod-lease.json"
        lease.write_text(json.dumps({"pod_id": "777", "provider": "runpod",
                                     "manifest": "batch/x.yaml"}, indent=2), encoding="utf-8")
        self.assertEqual(
            self._eff("GPU_PROVIDER=\nGPU_INSTANCE_ID=777\nGPU_INSTANCE_OWNER=vast:777\n",
                      f"LEASE_FILE={lease}"),
            "runpod")


class TestGpuDestroyFollowsTheOwnerMarker(unittest.TestCase):
    """End to end (F1 test f): a fake vastai AND a fake runpodctl on PATH, .env exactly as in
    the reproduced C1 bug (runpod-flavoured .env, dangling Vast id, no lease). `make
    gpu-destroy` must pick vast from the owner marker and never call runpodctl."""

    def test_a_dangling_owner_marker_destroys_via_vastai_not_runpodctl(self):
        d = Path(tempfile.mkdtemp())
        (d / ".env").write_text(
            "GPU_PROVIDER=runpod\nGPU_INSTANCE_ID=777\nGPU_INSTANCE_OWNER=vast:777\n",
            encoding="utf-8")
        (d / "Makefile").write_text((ROOT / "Makefile").read_text(encoding="utf-8"),
                                    encoding="utf-8")
        (d / "scripts").mkdir()
        (d / "scripts" / "env-clear-pod.sh").write_text(
            f'#!/bin/bash\ntouch "{d}/env-cleared"\n', encoding="utf-8")
        bin_dir = d / "bin"
        bin_dir.mkdir()
        (bin_dir / "vastai").write_text(
            '#!/bin/bash\n'
            f'if [ "$1" = destroy ]; then cat >/dev/null; touch "{d}/vastai-destroy-called"; '
            'exit 0; fi\n'
            'echo \'{"instances": [], "next_token": null}\'; exit 0\n', encoding="utf-8")
        (bin_dir / "runpodctl").write_text(
            f'#!/bin/bash\ntouch "{d}/runpodctl-called"\nexit 0\n', encoding="utf-8")
        (bin_dir / "sleep").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        for f in bin_dir.iterdir():
            f.chmod(0o755)
        env = {k: v for k, v in os.environ.items() if k not in ("GPU_PROVIDER", "GPU_INSTANCE_ID")}
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
        out = subprocess.run(["make", "gpu-destroy"], cwd=d, env=env,
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertTrue((d / "vastai-destroy-called").exists())
        self.assertFalse((d / "runpodctl-called").exists())


class TestEnvClearPodClearsTheOwnerMarker(unittest.TestCase):
    """F1 test g: env-clear-pod.sh must clear GPU_INSTANCE_OWNER along with the other three
    keys, keep the file's line count (its own gate), and no-op when the key is absent. Runs
    against an explicit tmp .env passed on the command line — never the real one."""

    def test_clears_the_owner_key_and_keeps_the_line_count(self):
        d = Path(tempfile.mkdtemp())
        env_file = d / ".env"
        env_file.write_text(
            "GPU_PROVIDER=runpod\nGPU_INSTANCE_ID=777\nGPU_INSTANCE_OWNER=vast:777\n"
            "GPU_SSH_HOST=1.2.3.4\nGPU_SSH_PORT=40022\n", encoding="utf-8")
        before = len(env_file.read_text(encoding="utf-8").splitlines())
        out = subprocess.run(["bash", str(ROOT / "scripts" / "env-clear-pod.sh"), str(env_file)],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        after_text = env_file.read_text(encoding="utf-8")
        self.assertEqual(len(after_text.splitlines()), before)
        self.assertIn("GPU_INSTANCE_OWNER=\n", after_text)
        self.assertNotIn("vast:777", after_text)

    def test_a_missing_owner_key_is_a_no_op(self):
        d = Path(tempfile.mkdtemp())
        env_file = d / ".env"
        env_file.write_text("GPU_PROVIDER=runpod\nGPU_INSTANCE_ID=777\n", encoding="utf-8")
        out = subprocess.run(["bash", str(ROOT / "scripts" / "env-clear-pod.sh"), str(env_file)],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(env_file.read_text(encoding="utf-8"),
                         "GPU_PROVIDER=runpod\nGPU_INSTANCE_ID=\n")


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
    def test_the_vast_probe_uses_a_quoted_variable_for_the_script_path(self):
        # F8/I7: was a literal, unquoted `python3 $(cd ... )/vast_rent.py` — word-split on any
        # path containing a space and re-computed the cd/pwd on every single poll.
        text = (ROOT / "scripts" / "pod-wait.sh").read_text(encoding="utf-8")
        self.assertIn('VAST_RENT="$(cd', text)
        self.assertIn('"$VAST_RENT" --ssh-target', text)
        # the proxy values are still read, as the fallback when ssh-url gives nothing
        self.assertIn('"ssh_host"', text)
        self.assertIn('"ssh_port"', text)

    def test_the_direct_address_falls_back_to_the_proxy_after_four_failures(self):
        # F8/I7: a bogus "direct" address (or a real one that just never answers) used to lock
        # pod-wait.sh onto it for the rest of the run, with no way back to the proxy values.
        text = (ROOT / "scripts" / "pod-wait.sh").read_text(encoding="utf-8")
        self.assertIn("DIRECT_FAILS=0", text)
        self.assertIn("USING_DIRECT", text)
        self.assertIn('"$DIRECT_FAILS" -lt 4', text)


if __name__ == "__main__":
    unittest.main()
