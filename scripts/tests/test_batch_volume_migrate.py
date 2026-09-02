import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import volume_migrate


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


if __name__ == "__main__":
    unittest.main()
