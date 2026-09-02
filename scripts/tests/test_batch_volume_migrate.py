import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import volume_migrate
from batchlib.config import env_get


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


class TestProvisionTempPod(unittest.TestCase):
    def test_posts_the_expected_rest_body_and_returns_the_pod_id(self):
        env_path = Path(tempfile.mkdtemp()) / ".env"
        env_path.write_text("RUNPOD_API_KEY=rp_test123\n", encoding="utf-8")
        fake_curl = mock.Mock(stdout=json.dumps({"id": "pod-a"}), returncode=0)
        with mock.patch.object(volume_migrate, "ENV_PATH", env_path), \
             mock.patch("subprocess.run", return_value=fake_curl) as mock_run:
            pod_id = volume_migrate.provision_temp_pod(
                "migrate-tmp-a", "vol-old", "EU-RO-1", 120)
        self.assertEqual(pod_id, "pod-a")
        body = json.loads(mock_run.call_args.args[0][-1])
        self.assertEqual(body["name"], "migrate-tmp-a")
        self.assertEqual(body["vcpuCount"], 4)
        self.assertEqual(body["networkVolumeId"], "vol-old")
        self.assertEqual(body["dataCenterIds"], ["EU-RO-1"])
        self.assertEqual(body["containerDiskInGb"], 120)

    def test_missing_api_key_raises_a_clear_error(self):
        env_path = Path(tempfile.mkdtemp()) / ".env"
        env_path.write_text("", encoding="utf-8")
        with mock.patch.object(volume_migrate, "ENV_PATH", env_path):
            with self.assertRaises(RuntimeError) as cm:
                volume_migrate.provision_temp_pod("migrate-tmp-a", "vol-old", "EU-RO-1", 120)
        self.assertIn("RUNPOD_API_KEY", str(cm.exception))

    def test_a_response_with_no_id_raises_rather_than_returning_none(self):
        env_path = Path(tempfile.mkdtemp()) / ".env"
        env_path.write_text("RUNPOD_API_KEY=rp_test123\n", encoding="utf-8")
        fake_curl = mock.Mock(stdout=json.dumps({"error": "insufficient capacity"}),
                              returncode=0)
        with mock.patch.object(volume_migrate, "ENV_PATH", env_path), \
             mock.patch("subprocess.run", return_value=fake_curl):
            with self.assertRaises(RuntimeError) as cm:
                volume_migrate.provision_temp_pod("migrate-tmp-a", "vol-old", "EU-RO-1", 120)
        self.assertIn("migrate-tmp-a", str(cm.exception))


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
    def test_no_yes_flag_prints_plan_and_touches_nothing_destructive(self):
        tmpdir = Path(tempfile.mkdtemp())
        env_path = tmpdir / ".env"
        env_path.write_text("POD_VOLUME_ID=vol-old\n", encoding="utf-8")
        with mock.patch.object(volume_migrate, "ENV_PATH", env_path), \
             mock.patch.object(volume_migrate, "create_volume",
                               return_value=("vol-new", 100, "EU-RO-1")) as mock_create, \
             mock.patch.object(volume_migrate, "provision_temp_pod") as mock_provision:
            rc = volume_migrate.main(["--to-dc", "EU-CZ-1"])
        self.assertEqual(rc, 0)
        mock_create.assert_called_once_with("vol-old", "EU-CZ-1")
        mock_provision.assert_not_called()

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

    def test_verify_sums_pending_changes_across_every_subdir(self):
        clean = mock.Mock(stdout="./\n\nsent 1 bytes\n")
        dirty = mock.Mock(stdout="./\nfile.gguf\n\nsent 1 bytes\n")
        with mock.patch("subprocess.run", side_effect=[clean, dirty]):
            total = volume_migrate.verify("host-a", 1001, "host-b", 1002,
                                          ["loras", "checkpoints"])
        self.assertEqual(total, 1)


class TestKeyExchange(unittest.TestCase):
    def test_make_temp_keypair_creates_a_private_and_public_file(self):
        priv, pub = volume_migrate.make_temp_keypair()
        self.assertTrue(priv.is_file())
        self.assertTrue(pub.is_file())
        self.assertEqual(pub.name, priv.name + ".pub")

    def test_install_key_on_pipes_the_public_key_over_stdin(self):
        priv, pub = volume_migrate.make_temp_keypair()
        with mock.patch("subprocess.run") as mock_run:
            volume_migrate.install_key_on("host-b", 1002, pub)
        self.assertEqual(mock_run.call_args.kwargs.get("input"), pub.read_text())


class TestExistingSubdirs(unittest.TestCase):
    def test_only_returns_subdirs_that_are_actually_present(self):
        listing = mock.Mock(stdout="loras\ncheckpoints\nsome_other_dir\n")
        with mock.patch("subprocess.run", return_value=listing):
            present = volume_migrate.existing_subdirs("host-a", 1001)
        self.assertIn("loras", present)
        self.assertIn("checkpoints", present)
        self.assertNotIn("some_other_dir", present)


class TestTeardownTempPods(unittest.TestCase):
    def test_deletes_both_and_clears_the_lease(self):
        with mock.patch("subprocess.run") as mock_run, \
             mock.patch("volume_migrate.clear_migrate_lease") as mock_clear:
            volume_migrate.teardown_temp_pods("pod-a", "pod-b")
        deleted = [c.args[0][-1] for c in mock_run.call_args_list]
        self.assertEqual(set(deleted), {"pod-a", "pod-b"})
        mock_clear.assert_called_once()

    def test_tolerates_one_pod_never_having_been_provisioned(self):
        with mock.patch("subprocess.run") as mock_run, \
             mock.patch("volume_migrate.clear_migrate_lease"):
            volume_migrate.teardown_temp_pods("pod-a", None)
        self.assertEqual(mock_run.call_count, 1)


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


class TestMainEndToEnd(unittest.TestCase):
    def _env(self, tmpdir):
        env_path = tmpdir / ".env"
        env_path.write_text("POD_VOLUME_ID=vol-old\n", encoding="utf-8")
        return env_path

    def test_a_verify_mismatch_aborts_without_deleting_anything(self):
        tmpdir = Path(tempfile.mkdtemp())
        with mock.patch.object(volume_migrate, "ENV_PATH", self._env(tmpdir)), \
             mock.patch.object(volume_migrate, "PROGRESS_PATH", tmpdir / "p.json"), \
             mock.patch.object(volume_migrate, "LEASE_PATH", tmpdir / "lease.json"), \
             mock.patch.object(volume_migrate, "create_volume",
                               return_value=("vol-new", 10, "EU-RO-1")), \
             mock.patch.object(volume_migrate, "provision_temp_pod",
                               side_effect=["pod-a", "pod-b"]), \
             mock.patch.object(volume_migrate, "wait_for_ssh",
                               side_effect=[("host-a", 1), ("host-b", 2)]), \
             mock.patch.object(volume_migrate, "make_temp_keypair",
                               return_value=(Path("/tmp/k"), Path("/tmp/k.pub"))), \
             mock.patch.object(volume_migrate, "install_key_on"), \
             mock.patch.object(volume_migrate, "place_key_on"), \
             mock.patch.object(volume_migrate, "existing_subdirs", return_value=["loras"]), \
             mock.patch.object(volume_migrate, "sync"), \
             mock.patch.object(volume_migrate, "verify", return_value=2), \
             mock.patch.object(volume_migrate, "teardown_temp_pods") as mock_teardown, \
             mock.patch("subprocess.run") as mock_run:
            rc = volume_migrate.main(["--to-dc", "EU-CZ-1", "--yes"])
        self.assertEqual(rc, 1)
        mock_teardown.assert_called_once_with("pod-a", "pod-b")
        # Nothing may call network-volume delete when verify found a mismatch.
        for call in mock_run.call_args_list:
            self.assertNotIn("delete", call.args[0])

    def test_a_provisioning_failure_still_tears_down_whatever_was_created(self):
        tmpdir = Path(tempfile.mkdtemp())
        with mock.patch.object(volume_migrate, "ENV_PATH", self._env(tmpdir)), \
             mock.patch.object(volume_migrate, "PROGRESS_PATH", tmpdir / "p.json"), \
             mock.patch.object(volume_migrate, "LEASE_PATH", tmpdir / "lease.json"), \
             mock.patch.object(volume_migrate, "create_volume",
                               return_value=("vol-new", 10, "EU-RO-1")), \
             mock.patch.object(volume_migrate, "provision_temp_pod",
                               side_effect=["pod-a", RuntimeError("no capacity")]), \
             mock.patch.object(volume_migrate, "teardown_temp_pods") as mock_teardown:
            with self.assertRaises(RuntimeError):
                volume_migrate.main(["--to-dc", "EU-CZ-1", "--yes"])
        # pod-a was created before pod-b failed — it must still be torn down,
        # and pod-b (never assigned) must be passed as None, not omitted.
        mock_teardown.assert_called_once_with("pod-a", None)

    def test_a_clean_verify_swaps_env_and_reports_done(self):
        tmpdir = Path(tempfile.mkdtemp())
        env_path = self._env(tmpdir)
        # env_path is captured here, and read again AFTER the with-block
        # below exits — mock.patch.object restores volume_migrate.ENV_PATH
        # to its pre-patch value on __exit__, so asserting via
        # `volume_migrate.ENV_PATH` at that point would silently read the
        # real repo .env instead of the tmp one main() actually wrote to.
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
                               return_value=(Path("/tmp/k"), Path("/tmp/k.pub"))), \
             mock.patch.object(volume_migrate, "install_key_on"), \
             mock.patch.object(volume_migrate, "place_key_on"), \
             mock.patch.object(volume_migrate, "existing_subdirs", return_value=["loras"]), \
             mock.patch.object(volume_migrate, "sync"), \
             mock.patch.object(volume_migrate, "verify", return_value=0), \
             mock.patch.object(volume_migrate, "teardown_temp_pods"), \
             mock.patch("subprocess.run", return_value=mock.Mock(returncode=0, stderr="")):
            rc = volume_migrate.main(["--to-dc", "EU-CZ-1", "--yes"])
        self.assertEqual(rc, 0)
        self.assertEqual(env_get(env_path, "POD_VOLUME_ID"), "vol-new")
        payload = json.loads((tmpdir / "p.json").read_text())
        self.assertEqual(payload["phase"], "done")


if __name__ == "__main__":
    unittest.main()
