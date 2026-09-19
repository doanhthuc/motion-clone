import functools
import http.server
import json
import os
import platform
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "motions-studio" / "setup" / "preload-models.sh"


def _run(env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(SCRIPT), *args], env=env,
                           capture_output=True, text=True, timeout=30)


def _fake_catalog(tmp: Path, url: str) -> Path:
    catalog = tmp / "catalog.json"
    catalog.write_text(json.dumps({
        "comfy": [{"id": "fake-id", "group": "Fake", "type": "checkpoints",
                   "filename": "fake.safetensors", "url": url,
                   "sizeBytes": 4096}],
        "ollama": [],
    }))
    return catalog


def _fake_catalog_file_url(tmp: Path) -> Path:
    # Used by the --dry-run-only tests below: remote_size() inside the script's embedded Python
    # is plain urllib, which resolves Content-Length for file:// URLs with no network involved,
    # and --dry-run never reaches the aria2c download step, so a file:// fixture is enough here.
    payload = tmp / "payload.bin"
    payload.write_bytes(b"x" * 4096)
    return _fake_catalog(tmp, payload.as_uri())


class TestModelsDirOverride(unittest.TestCase):
    def _env(self, catalog: Path, **extra) -> dict:
        env = os.environ.copy()
        env.pop("POD_VOLUME", None)
        env.pop("MODELS_DIR", None)
        env["CATALOG"] = str(catalog)
        env.update(extra)
        return env

    def test_downloads_straight_into_models_dir_no_volume_needed(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            dest = tmp / "models"
            env = self._env(_fake_catalog_file_url(tmp), MODELS_DIR=str(dest))
            result = _run(env, "--id", "fake-id", "--dry-run")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn("comfy-models", result.stdout)

    @unittest.skipUnless(platform.system() == "Linux",
                         "preload-models.sh:247 verifies download size with GNU `stat -c %s`, "
                         "which BSD/macOS `stat` doesn't support (always reads back 0 there) -- "
                         "pre-existing, unrelated to MODELS_DIR, and the script only ever runs on "
                         "Linux pods (RunPod/Vast) per docs/gpu-pod.md, never on the dev machine")
    def test_actually_writes_the_file_directly_under_models_dir_when_not_dry_run(self):
        # aria2c has no file:// support at all (confirmed: `aria2c -h all` lists Enabled Features
        # as Async DNS/BitTorrent/Firefox3 Cookie/GZip/HTTPS/Message Digest/Metalink/XML-RPC/SFTP
        # -- no local-file scheme -- and a direct `aria2c file:///...` run fails with "Unrecognized
        # URI or unsupported protocol"). remote_size()'s HEAD check is plain urllib and tolerates
        # file://, but the actual download is aria2c, so this one test needs a real -- loopback
        # only, no internet -- HTTP server instead of a file:// fixture.
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            payload = tmp / "payload.bin"
            payload.write_bytes(b"x" * 4096)
            handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp))
            httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{httpd.server_port}/payload.bin"
                dest = tmp / "models"
                env = self._env(_fake_catalog(tmp, url), MODELS_DIR=str(dest))
                result = _run(env, "--id", "fake-id")
            finally:
                httpd.shutdown()
                thread.join(timeout=5)
                httpd.server_close()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue((dest / "checkpoints" / "fake.safetensors").is_file())
            self.assertFalse((dest / "comfy-models").exists())

    def test_no_models_dir_and_no_pod_volume_dies_with_a_clear_message(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            env = self._env(_fake_catalog_file_url(tmp))
            result = _run(env, "--id", "fake-id", "--dry-run")
            self.assertNotEqual(result.returncode, 0)
            # die() prints via printf with no `>&2` redirect, so the message lands on stdout,
            # not stderr -- verified by running the script directly with streams split.
            self.assertIn("MODELS_DIR", result.stdout)

    def test_pod_volume_path_is_completely_unchanged_when_models_dir_is_unset(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            vol = tmp / "workspace"; vol.mkdir()
            env = self._env(_fake_catalog_file_url(tmp), POD_VOLUME=str(vol))
            result = _run(env, "--id", "fake-id", "--dry-run")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            # `mkdir -p "$MODELS"` runs before the --dry-run short-circuit, even in dry-run mode,
            # so the comfy-models subfolder convention is verifiable on disk, not just in stdout.
            # Confirmed by running the script directly: dry-run still leaves $VOL/comfy-models/ on disk.
            self.assertTrue((vol / "comfy-models").is_dir())


if __name__ == "__main__":
    unittest.main()
