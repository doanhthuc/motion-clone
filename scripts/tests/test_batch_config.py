import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.config import ConfigError, Settings, env_get, load_settings


def _write(root: Path, root_env: str, motions_env: str = "") -> None:
    (root / "motions").mkdir(parents=True, exist_ok=True)
    (root / ".env").write_text(root_env, encoding="utf-8")
    (root / "motions" / ".env").write_text(motions_env, encoding="utf-8")


class TestEnvGet(unittest.TestCase):
    def test_khop_hanh_vi_cua_makefile(self):
        # Makefile:30 — cắt từ '#' đầu tiên, xoá MỌI dấu ". Lệch là hai giá trị khác nhau
        # cho cùng một .env ở `make` và ở runner.
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text(
                'DOMAIN=api.example.test   # ghi chú\n'
                'QUOTED="giu-nguyen"\n'
                'EMPTY=\n'
                'NOTME=khong-lay\n',
                encoding="utf-8",
            )
            self.assertEqual(env_get(p, "DOMAIN"), "api.example.test")
            self.assertEqual(env_get(p, "QUOTED"), "giu-nguyen")
            self.assertEqual(env_get(p, "EMPTY"), "")
            self.assertEqual(env_get(p, "VANG_MAT"), "")

    def test_file_khong_ton_tai_tra_rong_khong_no(self):
        self.assertEqual(env_get(Path("/khong/co/that/.env"), "DOMAIN"), "")


class TestLoadSettings(unittest.TestCase):
    def test_du_thi_tra_settings(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "DOMAIN=api.example.test\nGPU_INSTANCE_ID=abc123\n",
                   "NUXT_MOTION_API_KEY=mk_deadbeef\n")
            s = load_settings(root)
            self.assertEqual(s.domain, "api.example.test")
            self.assertEqual(s.api_key, "mk_deadbeef")
            self.assertEqual(s.instance_id, "abc123")
            self.assertEqual(s.base_url, "https://api.example.test")

    def test_thieu_domain_bao_lam_gi_tiep(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "GPU_INSTANCE_ID=abc\n", "NUXT_MOTION_API_KEY=mk_x\n")
            with self.assertRaises(ConfigError) as cm:
                load_settings(root)
            self.assertIn("gpu-preflight", str(cm.exception))

    def test_thieu_api_key_bao_lam_gi_tiep(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "DOMAIN=api.example.test\n", "")
            with self.assertRaises(ConfigError) as cm:
                load_settings(root)
            self.assertIn("gpu-bootstrap", str(cm.exception))

    def test_instance_id_rong_van_load_duoc(self):
        # Chưa thuê pod là trạng thái hợp lệ — preflight mới là chỗ chặn, không phải load.
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "DOMAIN=api.example.test\n", "NUXT_MOTION_API_KEY=mk_x\n")
            self.assertEqual(load_settings(root).instance_id, "")


if __name__ == "__main__":
    unittest.main()
