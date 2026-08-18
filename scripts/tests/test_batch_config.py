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
        # Các giá trị dưới được đo trên GNU Make thực tế ngày 18/08/2026. Nếu thay đổi
        # Makefile:30, phải cập nhật cả hai chỗ.
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text(
                'A=  co-space-truoc\n'
                'B=co-space-sau   \n'
                'C=binh-thuong  # ghi chu\n'
                'D=first\n'
                'D=second\n'
                'QUOTED="giu-nguyen"\n'
                'EMPTY=\n'
                'NOTME=khong-lay\n',
                encoding="utf-8",
            )
            # Các trường hợp từ oracle (GNU Make 18/08/2026)
            self.assertEqual(env_get(p, "A"), "  co-space-truoc")  # khoảng trắng đầu giữ
            self.assertEqual(env_get(p, "B"), "co-space-sau   ")   # khoảng trắng cuối giữ
            self.assertEqual(env_get(p, "C"), "binh-thuong")       # comment cắt đi
            self.assertEqual(env_get(p, "D"), "first second")      # khóa lặp nối bằng dấu cách
            # Các trường hợp hiện có vẫn đúng
            self.assertEqual(env_get(p, "DOMAIN"), "")             # khóa không có
            self.assertEqual(env_get(p, "QUOTED"), "giu-nguyen")   # dấu nháy xoá
            self.assertEqual(env_get(p, "EMPTY"), "")              # giá trị rỗng
            self.assertEqual(env_get(p, "VANG_MAT"), "")           # khóa không tồn tại

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

    def test_domain_co_khoang_trang_bi_cham_dung(self):
        # DOMAIN có khoảng trắng (cuối dòng hoặc lặp khoá) → reject
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "DOMAIN=api.example.test  \n", "NUXT_MOTION_API_KEY=mk_x\n")
            with self.assertRaises(ConfigError) as cm:
                load_settings(root)
            self.assertIn(".env", str(cm.exception))
            self.assertIn("khoảng trắng", str(cm.exception))

    def test_api_key_co_khoang_trang_bi_cham_dung(self):
        # NUXT_MOTION_API_KEY có khoảng trắng → reject
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "DOMAIN=api.example.test\n", "NUXT_MOTION_API_KEY=mk_x  \n")
            with self.assertRaises(ConfigError) as cm:
                load_settings(root)
            self.assertIn("motions/.env", str(cm.exception))
            self.assertIn("khoảng trắng", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
