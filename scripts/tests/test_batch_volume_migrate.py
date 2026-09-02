import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import volume_migrate
from batchlib.config import env_get
from batchlib_ext.podctl import PodInfo


class TestCreateVolume(unittest.TestCase):
    @mock.patch("volume_migrate.sh")
    def test_reads_size_and_dc_from_the_source_writes_a_new_volume(self, mock_sh):
        get_result = mock.Mock(stdout=json.dumps(
            {"size": 100, "dataCenterId": "EU-RO-1", "name": "motion-100"}))
        create_result = mock.Mock(stdout=json.dumps({"id": "vol-new"}))
        mock_sh.side_effect = [get_result, create_result]
        new_id, size_gb, source_dc = volume_migrate.create_volume("vol-old", "EU-CZ-1")
        self.assertEqual(new_id, "vol-new")
        self.assertEqual(size_gb, 100)
        self.assertEqual(source_dc, "EU-RO-1")
        create_call = mock_sh.call_args_list[1].args
        self.assertIn("--size", create_call)
        self.assertIn("100", create_call)
        self.assertIn("--data-center-id", create_call)
        self.assertIn("EU-CZ-1", create_call)


class TestWriteProgress(unittest.TestCase):
    def test_writes_phase_and_extra_fields(self):
        tmpdir = Path(tempfile.mkdtemp())
        with mock.patch.object(volume_migrate, "PROGRESS_PATH", tmpdir / "p.json"):
            volume_migrate.write_progress("sync", pod_a="a", pod_b="b")
            payload = json.loads((tmpdir / "p.json").read_text())
        self.assertEqual(payload["phase"], "sync")
        self.assertEqual(payload["pod_a"], "a")
        self.assertIn("at", payload)


SECRET = "rp_test123_do_not_leak_me"


class _FakeResponse:
    def __init__(self, body: str):
        self._body = body.encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestProvisionTempPod(unittest.TestCase):
    def _env(self, contents: str = f"RUNPOD_API_KEY={SECRET}\n") -> Path:
        env_path = Path(tempfile.mkdtemp()) / ".env"
        env_path.write_text(contents, encoding="utf-8")
        return env_path

    def test_posts_the_expected_rest_body_and_returns_the_pod_id(self):
        with mock.patch.object(volume_migrate, "ENV_PATH", self._env()), \
             mock.patch("urllib.request.urlopen",
                        return_value=_FakeResponse(json.dumps({"id": "pod-a"}))) as opened:
            pod_id = volume_migrate.provision_temp_pod(
                "migrate-tmp-a", "vol-old", "EU-RO-1", 120)
        self.assertEqual(pod_id, "pod-a")
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, volume_migrate.RUNPOD_PODS_URL)
        body = json.loads(request.data)
        self.assertEqual(body["name"], "migrate-tmp-a")
        self.assertEqual(body["vcpuCount"], 4)
        self.assertEqual(body["networkVolumeId"], "vol-old")
        self.assertEqual(body["dataCenterIds"], ["EU-RO-1"])
        self.assertEqual(body["containerDiskInGb"], 120)

    def test_the_api_key_never_reaches_a_subprocess_argv(self):
        # Critical 3. With curl, the bearer token sat on the argv — and
        # CalledProcessError's own repr() carries the full argv, which main()
        # then wrote into the progress file, which the bot renders into a
        # Telegram message. urllib keeps the header inside this process.
        with mock.patch.object(volume_migrate, "ENV_PATH", self._env()), \
             mock.patch("urllib.request.urlopen",
                        return_value=_FakeResponse(json.dumps({"id": "pod-a"}))), \
             mock.patch("subprocess.run") as mock_run, \
             mock.patch("subprocess.Popen") as mock_popen:
            volume_migrate.provision_temp_pod("migrate-tmp-a", "vol-old", "EU-RO-1", 120)
        mock_run.assert_not_called()
        mock_popen.assert_not_called()

    def test_a_transport_failure_does_not_put_the_api_key_in_the_exception(self):
        # A DNS/TLS/connection failure — the case `-sS` never suppressed and
        # `check=True` turned into a CalledProcessError carrying the argv.
        import urllib.error
        with mock.patch.object(volume_migrate, "ENV_PATH", self._env()), \
             mock.patch("urllib.request.urlopen",
                        side_effect=urllib.error.URLError("name resolution failed")):
            with self.assertRaises(RuntimeError) as cm:
                volume_migrate.provision_temp_pod("migrate-tmp-a", "vol-old",
                                                  "EU-RO-1", 120)
        self.assertNotIn(SECRET, str(cm.exception))
        self.assertNotIn(SECRET, repr(cm.exception))
        self.assertIn("migrate-tmp-a", str(cm.exception))

    def test_missing_api_key_raises_a_clear_error(self):
        with mock.patch.object(volume_migrate, "ENV_PATH", self._env("")):
            with self.assertRaises(RuntimeError) as cm:
                volume_migrate.provision_temp_pod("migrate-tmp-a", "vol-old", "EU-RO-1", 120)
        self.assertIn("RUNPOD_API_KEY", str(cm.exception))

    def test_a_response_with_no_id_raises_rather_than_returning_none(self):
        body = json.dumps({"error": "insufficient capacity"})
        with mock.patch.object(volume_migrate, "ENV_PATH", self._env()), \
             mock.patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
            with self.assertRaises(RuntimeError) as cm:
                volume_migrate.provision_temp_pod("migrate-tmp-a", "vol-old", "EU-RO-1", 120)
        self.assertIn("migrate-tmp-a", str(cm.exception))


class TestSafeReason(unittest.TestCase):
    """Critical 3, defence in depth: whatever becomes `reason=` in the progress
    file is rendered verbatim into a Telegram message by
    tgbot.bot.tick_migration_progress, so an unbounded repr(exc) of an
    exception that may carry secrets is the wrong shape regardless of which
    exception it is today."""

    def test_a_called_process_error_argv_is_scrubbed_of_the_api_key(self):
        import subprocess as sp
        env_path = Path(tempfile.mkdtemp()) / ".env"
        env_path.write_text(f"RUNPOD_API_KEY={SECRET}\n", encoding="utf-8")
        exc = sp.CalledProcessError(
            7, ["curl", "-H", f"Authorization: Bearer {SECRET}"])
        with mock.patch.object(volume_migrate, "ENV_PATH", env_path):
            reason = volume_migrate.safe_reason(exc)
        self.assertNotIn(SECRET, reason)

    def test_a_long_exception_is_truncated(self):
        reason = volume_migrate.safe_reason(RuntimeError("x" * 5000))
        self.assertLessEqual(len(reason), volume_migrate.REASON_MAX_CHARS + 1)

    def test_an_exception_with_no_message_still_names_its_type(self):
        self.assertIn("RuntimeError", volume_migrate.safe_reason(RuntimeError()))


class TestWaitForSsh(unittest.TestCase):
    def test_returns_host_and_port_once_ssh_info_has_both_and_a_probe_succeeds(self):
        ssh_info = mock.Mock(stdout=json.dumps({"host": "1.2.3.4", "port": 40001}))
        probe_ok = mock.Mock(returncode=0)
        with mock.patch.object(volume_migrate, "sh", return_value=ssh_info), \
             mock.patch("subprocess.run", return_value=probe_ok):
            host, port = volume_migrate.wait_for_ssh("pod-a", timeout_min=1)
        self.assertEqual((host, port), ("1.2.3.4", 40001))

    def test_gives_up_after_the_timeout_with_no_endpoint_ever_appearing(self):
        not_ready = mock.Mock(stdout=json.dumps({"error": "pod not ready"}))
        with mock.patch.object(volume_migrate, "sh", return_value=not_ready), \
             mock.patch("time.sleep"), \
             mock.patch("time.time", side_effect=[0, 0, 61, 61]):
            with self.assertRaises(RuntimeError) as cm:
                volume_migrate.wait_for_ssh("pod-a", timeout_min=1)
        self.assertIn("pod-a", str(cm.exception))


class TestMainDryRun(unittest.TestCase):
    def test_no_yes_flag_creates_nothing_and_quotes_the_source_size(self):
        # A dry run used to CREATE the destination volume before it looked at
        # --yes, so "just checking what this would do" left a real, billable
        # volume behind for someone to delete by hand. `network-volume get` on
        # the SOURCE is a free read and says everything the plan line needs.
        tmpdir = Path(tempfile.mkdtemp())
        env_path = tmpdir / ".env"
        env_path.write_text("POD_VOLUME_ID=vol-old\n", encoding="utf-8")
        with mock.patch.object(volume_migrate, "ENV_PATH", env_path), \
             mock.patch.object(volume_migrate, "create_volume") as mock_create, \
             mock.patch.object(volume_migrate, "describe_volume",
                               return_value=(100, "EU-RO-1", "motion-100")), \
             mock.patch.object(volume_migrate, "provision_temp_pod") as mock_provision, \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = volume_migrate.main(["--to-dc", "EU-CZ-1"])
        self.assertEqual(rc, 0)
        mock_create.assert_not_called()
        mock_provision.assert_not_called()
        printed = out.getvalue()
        self.assertIn("100", printed)
        self.assertIn("EU-RO-1", printed)
        self.assertIn("EU-CZ-1", printed)

    def test_no_pod_volume_id_in_env_is_refused(self):
        tmpdir = Path(tempfile.mkdtemp())
        env_path = tmpdir / ".env"
        env_path.write_text("", encoding="utf-8")
        with mock.patch.object(volume_migrate, "ENV_PATH", env_path):
            rc = volume_migrate.main(["--to-dc", "EU-CZ-1"])
        self.assertEqual(rc, 1)


class TestCountPendingChanges(unittest.TestCase):
    def test_a_clean_verify_with_only_the_directory_line_is_zero(self):
        # Real rsync -avnc output when nothing differs: -a always lists the
        # top-level directory itself even when its contents are identical.
        output = "./\n\nsent 123 bytes  received 45 bytes  336.00 bytes/sec\n" \
                "total size is 79000000000  speedup is 999999.00 (DRY RUN)\n"
        self.assertEqual(volume_migrate.count_pending_changes(output), 0)

    def test_one_changed_file_counts_as_one(self):
        output = "./\nmodel1.safetensors\n\nsent 123 bytes  received 45 bytes\n"
        self.assertEqual(volume_migrate.count_pending_changes(output), 1)

    def test_several_changed_files_and_a_nested_path_all_count(self):
        output = ("./\nmodel1.safetensors\nsubdir/\nsubdir/model2.gguf\n\n"
                  "sent 123 bytes  received 45 bytes\n")
        self.assertEqual(volume_migrate.count_pending_changes(output), 3)

    def test_completely_empty_output_is_zero_not_an_error(self):
        self.assertEqual(volume_migrate.count_pending_changes(""), 0)

    def test_real_gnu_rsync_3_2_7_clean_output_is_zero(self):
        # Captured verbatim from `rsync -avnc` (GNU rsync 3.2.7, Debian 12,
        # 2026-09-02) on two identical directories. Unlike the hand-written
        # fixture above, real rsync's default incremental-recursion sender
        # prints "sending incremental file list" FIRST, and in this run
        # never even printed "./" at all — both must be handled.
        output = ("sending incremental file list\n\n"
                  "sent 164 bytes  received 13 bytes  354.00 bytes/sec\n"
                  "total size is 12  speedup is 0.07 (DRY RUN)\n")
        self.assertEqual(volume_migrate.count_pending_changes(output), 0)

    def test_real_gnu_rsync_header_line_with_one_changed_file(self):
        # Same real-rsync capture, this time with one file actually changed.
        output = ("sending incremental file list\n"
                  "file1.txt\n\n"
                  "sent 179 bytes  received 20 bytes  398.00 bytes/sec\n"
                  "total size is 14  speedup is 0.07 (DRY RUN)\n")
        self.assertEqual(volume_migrate.count_pending_changes(output), 1)

    def test_header_line_and_directory_line_together_do_not_double_count(self):
        output = ("sending incremental file list\n"
                  "./\n"
                  "model1.safetensors\n\n"
                  "sent 123 bytes  received 45 bytes\n")
        self.assertEqual(volume_migrate.count_pending_changes(output), 1)

    def test_an_extra_blank_line_before_the_summary_does_not_undercount(self):
        # Nothing guarantees rsync only ever emits exactly one blank line
        # right before "sent" — a parser that stops at the FIRST blank line
        # would silently drop every change reported after an earlier one.
        output = ("sending incremental file list\n"
                  "model1.safetensors\n\n"
                  "subdir/model2.gguf\n\n"
                  "sent 123 bytes  received 45 bytes\n")
        self.assertEqual(volume_migrate.count_pending_changes(output), 2)

    def test_windows_style_line_endings_are_handled(self):
        output = ("./\r\nmodel1.safetensors\r\n\r\n"
                  "sent 123 bytes  received 45 bytes\r\n")
        self.assertEqual(volume_migrate.count_pending_changes(output), 1)


class TestSyncAndVerify(unittest.TestCase):
    def test_sync_runs_one_ssh_per_subdir_from_pod_a(self):
        fake_proc = mock.Mock()
        fake_proc.wait.return_value = 0
        with mock.patch("subprocess.Popen", return_value=fake_proc) as mock_popen:
            volume_migrate.sync("host-a", 1001, "host-b", 1002, ["loras", "checkpoints"])
        self.assertEqual(mock_popen.call_count, 2)
        first_call_argv = mock_popen.call_args_list[0].args[0]
        self.assertIn("root@host-a", first_call_argv)

    def test_sync_raises_if_any_leg_exits_non_zero(self):
        fake_proc = mock.Mock()
        fake_proc.wait.return_value = 1
        with mock.patch("subprocess.Popen", return_value=fake_proc):
            with self.assertRaises(RuntimeError):
                volume_migrate.sync("host-a", 1001, "host-b", 1002, ["loras"])

    def test_a_failing_leg_does_not_leave_a_sibling_leg_unwaited(self):
        # Task 6 review finding: the first non-zero .wait() used to raise
        # immediately, leaving any OTHER Popen already launched in that same
        # batch running untracked in the background. Two legs, launched in
        # the same batch — the first one's failure must not skip waiting on
        # the second.
        proc_fail = mock.Mock()
        proc_fail.wait.return_value = 1
        proc_ok = mock.Mock()
        proc_ok.wait.return_value = 0
        with mock.patch("subprocess.Popen", side_effect=[proc_fail, proc_ok]):
            with self.assertRaises(RuntimeError):
                volume_migrate.sync("host-a", 1001, "host-b", 1002,
                                    ["loras", "checkpoints"])
        proc_fail.wait.assert_called_once()
        proc_ok.wait.assert_called_once()

    def test_a_nested_unit_keeps_its_full_relative_path_on_both_sides(self):
        # Sync units are now relative paths from the mount, and comfy-models'
        # children are nested. `rsync -aR` with the /./ marker is what makes
        # one command shape work for a nested directory, a top-level directory
        # AND a top-level FILE (the .motion-volume sentinel) — and it is the
        # only form that creates /workspace/comfy-models on pod B, whose
        # volume is brand new and empty. Verified against real GNU rsync 3.2.7
        # (docker debian:12-slim, 2026-09-02): the trailing-slash form this
        # replaced fails with `mkdir "/dst/comfy-models/loras" failed: No such
        # file or directory` when the parent does not exist yet.
        cmd = volume_migrate._rsync_cmd("/workspace", "comfy-models/loras",
                                        "host-b", 1002, dry_run=False)
        self.assertIn("-aR", cmd)
        self.assertIn("/workspace/./comfy-models/loras", cmd)
        self.assertIn("root@host-b:/workspace/", cmd)

    def test_a_top_level_file_unit_uses_the_same_command_shape(self):
        cmd = volume_migrate._rsync_cmd("/workspace", ".motion-volume",
                                        "host-b", 1002, dry_run=True)
        self.assertIn("-avncR", cmd)
        self.assertIn("/workspace/./.motion-volume", cmd)

    def test_verify_sums_pending_changes_across_every_subdir(self):
        clean = mock.Mock(stdout="sending incremental file list\n\nsent 1 bytes\n")
        dirty = mock.Mock(stdout="sending incremental file list\n"
                                 "comfy-models/loras/file.gguf\n\nsent 1 bytes\n")
        same_listing = mock.Mock(stdout="comfy-models\nminio\n")
        with mock.patch("subprocess.run",
                        side_effect=[clean, dirty, same_listing, same_listing]):
            result = volume_migrate.verify("host-a", 1001, "host-b", 1002,
                                           ["comfy-models/loras", "minio"])
        self.assertEqual(result.pending_changes, 1)
        self.assertFalse(result.ok)


class TestVerifyCoverage(unittest.TestCase):
    """Critical 2: per-unit checksums say nothing about units nobody enumerated.

    `count_pending_changes` returning 0 for every unit in a list is only a
    proof of "the copy matched" if that LIST covered the volume. A whole
    top-level directory left out of the plan produces exactly the same zero.
    """

    def _clean(self, n: int):
        return [mock.Mock(stdout="sending incremental file list\n\nsent 1 bytes\n")
                for _ in range(n)]

    def test_identical_top_level_sets_verify_clean(self):
        listing = mock.Mock(stdout=REAL_TOP_LEVEL)
        with mock.patch("subprocess.run",
                        side_effect=self._clean(2) + [listing, listing]):
            result = volume_migrate.verify("host-a", 1001, "host-b", 1002,
                                           ["minio", "pgdata"])
        self.assertEqual(result.pending_changes, 0)
        self.assertTrue(result.ok)

    def test_a_whole_directory_missing_on_b_is_a_mismatch_not_a_clean_zero(self):
        # The exact shape of the data-loss bug: every ENUMERATED unit checksums
        # clean, so pending_changes is 0 — but pod B is missing comfy-models,
        # hf-cache, ollama-models and pgdata entirely.
        on_a = mock.Mock(stdout=REAL_TOP_LEVEL)
        on_b = mock.Mock(stdout="minio\n.motion-volume\n")
        with mock.patch("subprocess.run",
                        side_effect=self._clean(1) + [on_a, on_b]):
            result = volume_migrate.verify("host-a", 1001, "host-b", 1002, ["minio"])
        self.assertEqual(result.pending_changes, 0)
        self.assertFalse(result.ok)
        self.assertIn("comfy-models", result.missing_on_b)
        self.assertIn("pgdata", result.missing_on_b)
        self.assertIn("comfy-models", result.reason())

    def test_an_unexpected_extra_entry_on_b_is_also_a_mismatch(self):
        on_a = mock.Mock(stdout="minio\n")
        on_b = mock.Mock(stdout="minio\nleftovers\n")
        with mock.patch("subprocess.run",
                        side_effect=self._clean(1) + [on_a, on_b]):
            result = volume_migrate.verify("host-a", 1001, "host-b", 1002, ["minio"])
        self.assertFalse(result.ok)
        self.assertIn("leftovers", result.extra_on_b)


class TestKeyExchange(unittest.TestCase):
    def _keypair(self):
        """A REAL ed25519 keypair, always cleaned up. These tests used to leak
        one private-key directory into /tmp per run, permanently (I7)."""
        priv, pub = volume_migrate.make_temp_keypair()
        self.addCleanup(volume_migrate.discard_temp_keypair, priv)
        return priv, pub

    def test_make_temp_keypair_creates_a_private_and_public_file(self):
        priv, pub = self._keypair()
        self.assertTrue(priv.is_file())
        self.assertTrue(pub.is_file())
        self.assertEqual(pub.name, priv.name + ".pub")

    def test_install_key_on_pipes_the_public_key_over_stdin(self):
        priv, pub = self._keypair()
        with mock.patch("subprocess.run") as mock_run:
            volume_migrate.install_key_on("host-b", 1002, pub)
        self.assertEqual(mock_run.call_args.kwargs.get("input"), pub.read_text())

    def test_discard_temp_keypair_removes_the_whole_directory(self):
        priv, _pub = volume_migrate.make_temp_keypair()
        tmpdir = priv.parent
        self.assertTrue(tmpdir.is_dir())
        volume_migrate.discard_temp_keypair(priv)
        self.assertFalse(tmpdir.exists())

    def test_discard_temp_keypair_tolerates_none_and_a_second_call(self):
        volume_migrate.discard_temp_keypair(None)
        priv, _pub = volume_migrate.make_temp_keypair()
        volume_migrate.discard_temp_keypair(priv)
        volume_migrate.discard_temp_keypair(priv)

    def test_discard_refuses_a_path_that_is_not_one_of_our_temp_dirs(self):
        # rmtree on a caller-supplied parent is a foot-gun; the prefix
        # make_temp_keypair itself sets is the only thing this may delete.
        guarded = Path(tempfile.mkdtemp(prefix="not-ours-"))
        (guarded / "precious").write_text("keep me", encoding="utf-8")
        self.addCleanup(shutil.rmtree, guarded, True)
        volume_migrate.discard_temp_keypair(guarded / "id_migrate")
        self.assertTrue((guarded / "precious").is_file())

    def test_a_keygen_failure_does_not_leak_the_directory(self):
        made: list[Path] = []
        real_mkdtemp = tempfile.mkdtemp

        def spy(*a, **kw):
            path = real_mkdtemp(*a, **kw)
            made.append(Path(path))
            return path

        with mock.patch("tempfile.mkdtemp", side_effect=spy), \
             mock.patch("subprocess.run", side_effect=OSError("ssh-keygen missing")):
            with self.assertRaises(OSError):
                volume_migrate.make_temp_keypair()
        self.assertTrue(made)
        self.assertFalse(made[0].exists())


# What `ls -A /workspace` actually prints on a real Network Volume, per
# motions-studio/setup/pod-volume.sh:85-89 (MODELS/HFCACHE/OLLAMA/PGDATA/MINIO
# plus the .motion-volume sentinel it writes at line 96-99). Every test below
# that enumerates sync units builds its listing from THESE names rather than a
# convenient two-entry fixture: the bug this file now guards against
# (MODEL_SUBDIRS naming children of comfy-models as if they were top-level
# entries) was invisible to a simplified layout and only fatal against a real
# one.
REAL_TOP_LEVEL = "comfy-models\nhf-cache\nollama-models\npgdata\nminio\n.motion-volume\n"
REAL_COMFY_CHILDREN = ("diffusion_models\ntext_encoders\nloras\ncheckpoints\n"
                       "clip_vision\nvae\nupscale_models\n")


def _listings(*stdouts: str):
    """subprocess.run side_effect for a sequence of `ls -A` calls."""
    return [mock.Mock(stdout=s, returncode=0) for s in stdouts]


class TestExistingSubdirs(unittest.TestCase):
    def test_a_realistic_volume_layout_is_covered_entry_for_entry(self):
        # The regression test for the data-loss bug. A real volume's top level
        # is comfy-models/ hf-cache/ ollama-models/ pgdata/ minio/ and the
        # sentinel file; the old hardcoded MODEL_SUBDIRS intersected to just
        # ["minio"], so a real run synced 1 of 6 entries, verified clean
        # against that scope, and deleted the source volume.
        with mock.patch("subprocess.run",
                        side_effect=_listings(REAL_TOP_LEVEL, REAL_COMFY_CHILDREN)):
            units = volume_migrate.existing_subdirs("host-a", 1001)

        # Every top-level entry is represented, either directly or (for
        # comfy-models) by at least one child unit under it.
        for entry in ("hf-cache", "ollama-models", "pgdata", "minio",
                      ".motion-volume"):
            self.assertIn(entry, units)
        # comfy-models is the ONE entry expanded a level deeper, so its 33-55GB
        # can be carried by 8 parallel rsync legs.
        for child in ("diffusion_models", "text_encoders", "loras", "checkpoints",
                      "clip_vision", "vae", "upscale_models"):
            self.assertIn(f"comfy-models/{child}", units)
        # ...and never ALSO as one undivided unit, which would copy everything twice.
        self.assertNotIn("comfy-models", units)

    def test_pgdata_is_lowercase_on_a_real_volume(self):
        # pod-volume.sh:88 writes `PGDATA="$VOL/pgdata"` — the shell VARIABLE is
        # uppercase, the DIRECTORY is not. The old list said "PGDATA", which
        # matches nothing on disk.
        with mock.patch("subprocess.run",
                        side_effect=_listings(REAL_TOP_LEVEL, REAL_COMFY_CHILDREN)):
            units = volume_migrate.existing_subdirs("host-a", 1001)
        self.assertIn("pgdata", units)
        self.assertNotIn("PGDATA", units)

    def test_a_top_level_entry_nobody_predicted_is_synced_too(self):
        # The whole point of enumerating instead of allowlisting: a directory
        # added to the volume next year must not be silently left behind.
        listing = REAL_TOP_LEVEL + "something-new\n"
        with mock.patch("subprocess.run",
                        side_effect=_listings(listing, REAL_COMFY_CHILDREN)):
            units = volume_migrate.existing_subdirs("host-a", 1001)
        self.assertIn("something-new", units)

    def test_a_volume_with_no_comfy_models_still_enumerates_the_rest(self):
        # A smaller/newer volume. Only ONE ls call is expected — there is no
        # comfy-models to descend into.
        with mock.patch("subprocess.run",
                        side_effect=_listings("minio\npgdata\n")) as mock_run:
            units = volume_migrate.existing_subdirs("host-a", 1001)
        self.assertEqual(sorted(units), ["minio", "pgdata"])
        self.assertEqual(mock_run.call_count, 1)

    def test_an_empty_comfy_models_is_still_one_unit(self):
        with mock.patch("subprocess.run",
                        side_effect=_listings("comfy-models\nminio\n", "")):
            units = volume_migrate.existing_subdirs("host-a", 1001)
        self.assertIn("comfy-models", units)
        self.assertIn("minio", units)

    def test_an_empty_volume_returns_no_units_rather_than_pretending(self):
        with mock.patch("subprocess.run", side_effect=_listings("")):
            self.assertEqual(volume_migrate.existing_subdirs("host-a", 1001), [])

    def test_there_is_no_hardcoded_sync_unit_name_list_any_more(self):
        # Guards the fix itself, not just its output: reintroducing a module
        # level allowlist is how this bug comes back.
        self.assertFalse(hasattr(volume_migrate, "MODEL_SUBDIRS"))


class FakePods:
    """A PodControl (batchlib_ext.podctl.PodControl) that can be told to lie —
    the same shape scripts/tests/test_batch_pod_watchdog.py uses to prove
    destroy_verified's re-list actually happens."""

    def __init__(self, pods: list[str], *, really_delete: bool = True,
                 raise_on: set[str] | None = None):
        self.pods = list(pods)
        self.really_delete = really_delete
        self.raise_on = raise_on or set()
        self.destroyed: list[str] = []

    def list_pods(self):
        return [PodInfo(pod_id=p, name="migrate-tmp") for p in self.pods]

    def destroy(self, pod_id: str) -> None:
        self.destroyed.append(pod_id)
        if pod_id in self.raise_on:
            raise RuntimeError(f"runpodctl pod delete {pod_id} failed: nope")
        if self.really_delete and pod_id in self.pods:
            self.pods.remove(pod_id)


class TestTeardownTempPods(unittest.TestCase):
    def test_deletes_both_verifies_them_gone_and_clears_the_lease(self):
        pods = FakePods(["pod-a", "pod-b"])
        with mock.patch("volume_migrate.clear_migrate_lease") as mock_clear:
            volume_migrate.teardown_temp_pods("pod-a", "pod-b", pods_api=pods)
        self.assertEqual(set(pods.destroyed), {"pod-a", "pod-b"})
        self.assertEqual(pods.pods, [])
        mock_clear.assert_called_once()

    def test_an_unverified_delete_keeps_the_lease_for_the_watchdog(self):
        # I2. Clearing the lease over a pod that is still in `runpodctl pod
        # list` hands a STILL-BILLING pod to tier 3's lease-less orphan path,
        # which needs a 10-minute grace window before it will act — and
        # nothing points at the pod in the meantime. Keeping the lease means
        # pod_watchdog's tier 1/2 retries it on the next 60s tick.
        pods = FakePods(["pod-a", "pod-b"], really_delete=False)
        with mock.patch("volume_migrate.clear_migrate_lease") as mock_clear:
            volume_migrate.teardown_temp_pods("pod-a", "pod-b", pods_api=pods)
        mock_clear.assert_not_called()

    def test_one_confirmed_and_one_not_still_keeps_the_lease(self):
        pods = FakePods(["pod-a", "pod-b"], raise_on={"pod-b"})
        with mock.patch("volume_migrate.clear_migrate_lease") as mock_clear:
            volume_migrate.teardown_temp_pods("pod-a", "pod-b", pods_api=pods)
        # pod-a really went; pod-b's delete raised, so it is still listed.
        self.assertEqual(pods.pods, ["pod-b"])
        mock_clear.assert_not_called()

    def test_a_delete_that_raises_does_not_stop_the_other_pod_being_deleted(self):
        pods = FakePods(["pod-a", "pod-b"], raise_on={"pod-a"})
        with mock.patch("volume_migrate.clear_migrate_lease"):
            volume_migrate.teardown_temp_pods("pod-a", "pod-b", pods_api=pods)
        self.assertEqual(pods.destroyed, ["pod-a", "pod-b"])

    def test_tolerates_one_pod_never_having_been_provisioned(self):
        pods = FakePods(["pod-a"])
        with mock.patch("volume_migrate.clear_migrate_lease") as mock_clear:
            volume_migrate.teardown_temp_pods("pod-a", None, pods_api=pods)
        self.assertEqual(pods.destroyed, ["pod-a"])
        mock_clear.assert_called_once()

    def test_nothing_provisioned_at_all_still_clears_a_lease_that_cannot_exist(self):
        pods = FakePods([])
        with mock.patch("volume_migrate.clear_migrate_lease") as mock_clear:
            volume_migrate.teardown_temp_pods(None, None, pods_api=pods)
        self.assertEqual(pods.destroyed, [])
        mock_clear.assert_called_once()


class TestSwap(unittest.TestCase):
    def test_writes_env_and_deletes_the_old_volume(self):
        env_path = Path(tempfile.mkdtemp()) / ".env"
        env_path.write_text("POD_VOLUME_ID=vol-old\n", encoding="utf-8")
        ok = mock.Mock(returncode=0, stderr="")
        with mock.patch.object(volume_migrate, "ENV_PATH", env_path), \
             mock.patch.object(volume_migrate, "PROGRESS_PATH",
                               env_path.parent / "p.json"), \
             mock.patch("subprocess.run", return_value=ok) as mock_run:
            volume_migrate.swap(new_volume_id="vol-new", old_volume_id="vol-old")
        self.assertEqual(env_get(env_path, "POD_VOLUME_ID"), "vol-new")
        self.assertIn("vol-old", mock_run.call_args.args[0])

    def test_a_failed_delete_is_reported_but_env_is_still_swapped(self):
        env_path = Path(tempfile.mkdtemp()) / ".env"
        env_path.write_text("POD_VOLUME_ID=vol-old\n", encoding="utf-8")
        failed = mock.Mock(returncode=1, stderr="still referenced")
        with mock.patch.object(volume_migrate, "ENV_PATH", env_path), \
             mock.patch.object(volume_migrate, "PROGRESS_PATH",
                               env_path.parent / "p.json"), \
             mock.patch("subprocess.run", return_value=failed):
            volume_migrate.swap(new_volume_id="vol-new", old_volume_id="vol-old")
        self.assertEqual(env_get(env_path, "POD_VOLUME_ID"), "vol-new")
        payload = json.loads((env_path.parent / "p.json").read_text())
        self.assertIn("still referenced", payload["warning"])


def _clean_result():
    return volume_migrate.VerifyResult(pending_changes=0, missing_on_b=[],
                                       extra_on_b=[])


class TestMainEndToEnd(unittest.TestCase):
    def _env(self, tmpdir):
        env_path = tmpdir / ".env"
        env_path.write_text("POD_VOLUME_ID=vol-old\n", encoding="utf-8")
        return env_path

    @contextlib.contextmanager
    def _wired(self, tmpdir, *, units=("comfy-models/loras", "minio"),
               verify_result=None, provision=("pod-a", "pod-b")):
        """Every collaborator of main() faked, so the assertions below are
        about main()'s own control flow and nothing else."""
        with contextlib.ExitStack() as stack:
            def patch(name, **kw):
                return stack.enter_context(mock.patch.object(volume_migrate, name, **kw))

            patch("ENV_PATH", new=self._env(tmpdir))
            patch("PROGRESS_PATH", new=tmpdir / "p.json")
            patch("LEASE_PATH", new=tmpdir / "lease.json")
            patch("create_volume", return_value=("vol-new", 10, "EU-RO-1"))
            patch("describe_volume", return_value=(10, "EU-RO-1", "motion-10"))
            patch("provision_temp_pod", side_effect=list(provision))
            patch("wait_for_ssh", side_effect=[("host-a", 1), ("host-b", 2)])
            patch("make_temp_keypair",
                  return_value=(Path("/tmp/migrate-ssh-fake/id_migrate"),
                                Path("/tmp/migrate-ssh-fake/id_migrate.pub")))
            patch("discard_temp_keypair")
            patch("install_key_on")
            patch("place_key_on")
            patch("existing_subdirs", return_value=list(units))
            patch("sync")
            patch("verify", return_value=verify_result or _clean_result())
            patch("teardown_temp_pods")
            patch("swap")
            yield stack

    def test_a_verify_mismatch_aborts_without_ever_calling_swap(self):
        # `swap` is the ONLY function that deletes the source volume. The
        # previous version of this test asserted on subprocess.run's call
        # list, which is always empty here because every collaborator is
        # function-level mocked — a vacuous assertion dressed as the safety
        # gate. Assert the invariant itself.
        tmpdir = Path(tempfile.mkdtemp())
        dirty = volume_migrate.VerifyResult(pending_changes=2, missing_on_b=[],
                                            extra_on_b=[])
        with self._wired(tmpdir, verify_result=dirty):
            rc = volume_migrate.main(["--to-dc", "EU-CZ-1", "--yes"])
            self.assertEqual(rc, 1)
            volume_migrate.swap.assert_not_called()
            volume_migrate.teardown_temp_pods.assert_called_once_with("pod-a", "pod-b")
        payload = json.loads((tmpdir / "p.json").read_text())
        self.assertEqual(payload["phase"], "failed")

    def test_a_whole_directory_missing_on_b_also_blocks_the_delete(self):
        tmpdir = Path(tempfile.mkdtemp())
        gap = volume_migrate.VerifyResult(pending_changes=0,
                                          missing_on_b=["comfy-models", "pgdata"],
                                          extra_on_b=[])
        with self._wired(tmpdir, verify_result=gap):
            rc = volume_migrate.main(["--to-dc", "EU-CZ-1", "--yes"])
            self.assertEqual(rc, 1)
            volume_migrate.swap.assert_not_called()
        payload = json.loads((tmpdir / "p.json").read_text())
        self.assertIn("comfy-models", payload["reason"])

    def test_an_empty_sync_unit_list_never_reaches_sync_verify_or_swap(self):
        # Critical 2. count_pending_changes over an EMPTY list of units is 0,
        # so a plan that enumerated nothing would sail through the one gate
        # between "copied" and "delete the original".
        tmpdir = Path(tempfile.mkdtemp())
        with self._wired(tmpdir, units=()):
            with self.assertRaises(RuntimeError) as cm:
                volume_migrate.main(["--to-dc", "EU-CZ-1", "--yes"])
            volume_migrate.sync.assert_not_called()
            volume_migrate.verify.assert_not_called()
            volume_migrate.swap.assert_not_called()
            volume_migrate.teardown_temp_pods.assert_called_once_with("pod-a", "pod-b")
        self.assertIn("no entries", str(cm.exception))

    def test_a_provisioning_failure_still_tears_down_whatever_was_created(self):
        tmpdir = Path(tempfile.mkdtemp())
        with self._wired(tmpdir, provision=("pod-a", RuntimeError("no capacity"))):
            with self.assertRaises(RuntimeError):
                volume_migrate.main(["--to-dc", "EU-CZ-1", "--yes"])
            # pod-a was created before pod-b failed — it must still be torn
            # down, and pod-b (never assigned) must be passed as None.
            volume_migrate.teardown_temp_pods.assert_called_once_with("pod-a", None)

    def test_the_temp_private_key_directory_is_always_removed(self):
        tmpdir = Path(tempfile.mkdtemp())
        dirty = volume_migrate.VerifyResult(pending_changes=1, missing_on_b=[],
                                            extra_on_b=[])
        with self._wired(tmpdir, verify_result=dirty):
            volume_migrate.main(["--to-dc", "EU-CZ-1", "--yes"])
            volume_migrate.discard_temp_keypair.assert_called_once_with(
                Path("/tmp/migrate-ssh-fake/id_migrate"))

    def test_a_failure_reason_is_scrubbed_and_bounded_not_a_raw_repr(self):
        tmpdir = Path(tempfile.mkdtemp())
        with self._wired(tmpdir):
            volume_migrate.existing_subdirs.side_effect = RuntimeError("y" * 4000)
            with self.assertRaises(RuntimeError):
                volume_migrate.main(["--to-dc", "EU-CZ-1", "--yes"])
        payload = json.loads((tmpdir / "p.json").read_text())
        self.assertEqual(payload["phase"], "failed")
        self.assertLessEqual(len(payload["reason"]),
                             volume_migrate.REASON_MAX_CHARS + 1)

    def test_a_clean_verify_swaps_env_and_reports_done(self):
        tmpdir = Path(tempfile.mkdtemp())
        env_path = self._env(tmpdir)
        with mock.patch.object(volume_migrate, "ENV_PATH", env_path), \
             mock.patch.object(volume_migrate, "PROGRESS_PATH", tmpdir / "p.json"), \
             mock.patch.object(volume_migrate, "LEASE_PATH", tmpdir / "lease.json"), \
             mock.patch.object(volume_migrate, "create_volume",
                               return_value=("vol-new", 10, "EU-RO-1")), \
             mock.patch.object(volume_migrate, "provision_temp_pod",
                               side_effect=["pod-a", "pod-b"]), \
             mock.patch.object(volume_migrate, "wait_for_ssh",
                               side_effect=[("host-a", 1), ("host-b", 2)]), \
             mock.patch.object(volume_migrate, "make_temp_keypair",
                               return_value=(Path("/tmp/migrate-ssh-fake/id_migrate"),
                                             Path("/tmp/migrate-ssh-fake/id_migrate.pub"))), \
             mock.patch.object(volume_migrate, "discard_temp_keypair"), \
             mock.patch.object(volume_migrate, "install_key_on"), \
             mock.patch.object(volume_migrate, "place_key_on"), \
             mock.patch.object(volume_migrate, "existing_subdirs",
                               return_value=["comfy-models/loras", "minio"]), \
             mock.patch.object(volume_migrate, "sync"), \
             mock.patch.object(volume_migrate, "verify", return_value=_clean_result()), \
             mock.patch.object(volume_migrate, "teardown_temp_pods"), \
             mock.patch("subprocess.run", return_value=mock.Mock(returncode=0, stderr="")):
            rc = volume_migrate.main(["--to-dc", "EU-CZ-1", "--yes"])
        # env_path is read again AFTER the with-block — mock.patch.object
        # restores volume_migrate.ENV_PATH on __exit__, so asserting via
        # `volume_migrate.ENV_PATH` here would read the real repo .env.
        self.assertEqual(rc, 0)
        self.assertEqual(env_get(env_path, "POD_VOLUME_ID"), "vol-new")
        payload = json.loads((tmpdir / "p.json").read_text())
        self.assertEqual(payload["phase"], "done")


if __name__ == "__main__":
    unittest.main()
