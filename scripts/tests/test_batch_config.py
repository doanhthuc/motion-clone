import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.config import ConfigError, Settings, env_get, env_set, load_settings


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


class TestEnvSet(unittest.TestCase):
    """env_set — added 2026-09-02 so /gpu's Run-time flow can switch GPU=
    itself, mirroring pod-provision.sh's own env_set (its only prior
    implementation)."""

    def test_replaces_an_existing_key_in_place(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text("A=one\nGPU=NVIDIA GeForce RTX 5090\nB=two\n",
                        encoding="utf-8")
            env_set(p, "GPU", "NVIDIA GeForce RTX 4090")
            self.assertEqual(env_get(p, "GPU"), "NVIDIA GeForce RTX 4090")
            # Neighbouring keys untouched, same order.
            self.assertEqual(p.read_text(encoding="utf-8"),
                            "A=one\nGPU=NVIDIA GeForce RTX 4090\nB=two\n")

    def test_appends_when_the_key_is_missing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text("A=one\n", encoding="utf-8")
            env_set(p, "GPU", "NVIDIA GeForce RTX 4090")
            self.assertEqual(env_get(p, "GPU"), "NVIDIA GeForce RTX 4090")
            self.assertEqual(p.read_text(encoding="utf-8"),
                            "A=one\nGPU=NVIDIA GeForce RTX 4090\n")

    def test_works_against_a_file_that_does_not_exist_yet(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            env_set(p, "GPU", "NVIDIA GeForce RTX 4090")
            self.assertEqual(env_get(p, "GPU"), "NVIDIA GeForce RTX 4090")

    def test_no_leftover_tmp_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text("A=one\n", encoding="utf-8")
            env_set(p, "GPU", "NVIDIA GeForce RTX 4090")
            self.assertFalse((Path(d) / ".env.tmp").exists())


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
        # DOMAIN không phải secret — hiển thị giá trị thực
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "DOMAIN=api.example.test  \n", "NUXT_MOTION_API_KEY=mk_x\n")
            with self.assertRaises(ConfigError) as cm:
                load_settings(root)
            msg = str(cm.exception)
            self.assertIn("api.example.test  ", msg)  # giá trị thực hiển thị
            self.assertIn(".env", msg)
            self.assertIn("khoảng trắng", msg)

    def test_api_key_co_khoang_trang_bi_cham_dung(self):
        # NUXT_MOTION_API_KEY có khoảng trắng → reject với che giấu
        # API key là secret — che giấu ký tự nhưng giữ khoảng trắng
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "DOMAIN=api.example.test\n", "NUXT_MOTION_API_KEY=mk_secret123   \n")
            with self.assertRaises(ConfigError) as cm:
                load_settings(root)
            msg = str(cm.exception)
            # Không chứa key thực
            self.assertNotIn("mk_secret123", msg)
            # Chứa dấu che giấu (•)
            self.assertIn("•", msg)
            # Hiển thị token che giấu chính xác: '••••••••••••   ' (12 ký tự gốc → 12 •, 3 khoảng trắng giữ)
            # assertIn("   ") là tệ vì "   " cũng xuất hiện trong boilerplate indent ở config.py,
            # nên assertion đó không phân biệt được giữa "khoảng trắng được giữ" và "không".
            self.assertIn("'••••••••••••   '", msg)
            # Tên file đúng
            self.assertIn("motions/.env", msg)
            self.assertIn("khoảng trắng", msg)

    def test_domain_co_braces_khong_crash(self):
        # Regression: DOMAIN có ký tự { hoặc } không gây KeyError trong error message
        # env_get() trả về giá trị malformed nguyên vẹn, _reject_whitespace() phải handle chúng
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "DOMAIN=api.example.test {oops}  \n", "NUXT_MOTION_API_KEY=mk_x\n")
            # Phải raise ConfigError, không phải KeyError hay ValueError
            with self.assertRaises(ConfigError) as cm:
                load_settings(root)
            msg = str(cm.exception)
            # Message phải chứa giá trị thực để user thấy được lỗi
            self.assertIn("{oops}", msg)
            self.assertIn("khoảng trắng", msg)

    def test_gemini_api_key_co_thi_doc_duoc(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "DOMAIN=api.example.test\nGEMINI_API_KEY=AIzaFakeKeyFakeKeyFakeKeyFake\n",
                   "NUXT_MOTION_API_KEY=mk_x\n")
            s = load_settings(root)
            self.assertEqual(s.gemini_api_key, "AIzaFakeKeyFakeKeyFakeKeyFake")

    def test_thieu_gemini_api_key_van_load_duoc(self):
        # KHÔNG bắt buộc — không phải batch nào cũng cần try-on local (provider gemini).
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "DOMAIN=api.example.test\n", "NUXT_MOTION_API_KEY=mk_x\n")
            s = load_settings(root)
            self.assertEqual(s.gemini_api_key, "")


if __name__ == "__main__":
    unittest.main()
