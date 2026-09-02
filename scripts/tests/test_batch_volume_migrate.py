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
