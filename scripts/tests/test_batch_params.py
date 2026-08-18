import contextlib
import io
import sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.params import (check_drift, dynamic_param_names, extract_from_ast,
                             known_params, load_curated, validate_params)
import batch_params

REPO = Path(__file__).resolve().parents[2]
LINUX_PY = REPO / "motions-studio" / "worker" / "worker_runtime" / "linux.py"
CURATED = REPO / "scripts" / "batch-params.json"

FAKE = '''
def run_motion(job):
    params = job.get("params", {}) or {}
    preset = params.get("preset")
    frames = params.get("frames", 81)
    fps = params.get("render_fps", 16)

def run_enhance(job):
    params = job.get("params", {}) or {}
    target = params.get("targetRes") or params.get("target_res")
    _k = next((k for k in ("fpsInterp", "fps_interp", "fpsTarget") if k in params), None)
'''


class TestExtractor(unittest.TestCase):
    def test_rut_duoc_ten_default_va_dong(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "fake.py"
            p.write_text(FAKE, encoding="utf-8")
            got = extract_from_ast(p)
            self.assertEqual(set(got), {"motion", "enhance"})
            self.assertEqual(got["motion"]["frames"].default, 81)
            self.assertIsNone(got["motion"]["preset"].default)
            self.assertGreater(got["motion"]["render_fps"].line, 0)
            self.assertEqual(got["motion"]["frames"].source, "ast")

    def test_khong_thay_param_doc_dong(self):
        # Đây LÀ cái lỗ, và test này khoá nó lại để không ai tưởng extractor đủ.
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "fake.py"
            p.write_text(FAKE, encoding="utf-8")
            self.assertNotIn("fpsInterp", extract_from_ast(p)["enhance"])

    def test_dynamic_param_names_bat_duoc_dung_cai_lo_do(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "fake.py"
            p.write_text(FAKE, encoding="utf-8")
            self.assertEqual(dynamic_param_names(p)["enhance"],
                             {"fpsInterp", "fps_interp", "fpsTarget"})

    def test_chay_duoc_tren_linux_py_that(self):
        got = extract_from_ast(LINUX_PY)
        self.assertIn("motion", got)
        self.assertIn("preset", got["motion"])
        self.assertIn("targetRes", got["enhance"])
        self.assertIn("garmentType", got["tryon"])


class TestValidate(unittest.TestCase):
    def setUp(self):
        self.ast = extract_from_ast(LINUX_PY)
        self.curated = load_curated(CURATED)

    def test_key_hop_le_thi_khong_loi(self):
        self.assertEqual(
            validate_params("enhance", {"targetRes": "1080p", "fpsInterp": "60"},
                            ast_params=self.ast, curated=self.curated), [])

    def test_key_la_bi_chan_kem_goi_y(self):
        errs = validate_params("enhance", {"targetres": "1080p"},
                               ast_params=self.ast, curated=self.curated)
        self.assertEqual(len(errs), 1)
        self.assertIn("targetRes", errs[0])

    def test_gia_tri_ngoai_danh_sach_bi_chan(self):
        errs = validate_params("enhance", {"fpsInterp": "120"},
                               ast_params=self.ast, curated=self.curated)
        self.assertEqual(len(errs), 1)
        self.assertIn("60", errs[0])

    def test_fpsInterp_duoc_chap_nhan_du_AST_khong_thay(self):
        self.assertIn("fpsInterp", known_params("enhance", ast_params=self.ast, curated=self.curated))

    def test_quality_duoc_chap_nhan_du_worker_khong_doc_no(self):
        # quality là param TẦNG API: run_motion không gọi params.get("quality"),
        # enforceMotionResolution (motion-resolution.js:23) dịch nó thành width/height.
        # pod-smoke.sh:292 và batch/example.yaml đều gửi nó — chặn nó là chặn nhầm.
        self.assertNotIn("quality", self.ast.get("motion", {}))
        known = known_params("motion", ast_params=self.ast, curated=self.curated)
        self.assertIn("quality", known)
        self.assertEqual(known["quality"].source, "api")
        self.assertEqual(
            validate_params("motion", {"quality": "540p", "frames": 33},
                            ast_params=self.ast, curated=self.curated), [])

    def test_quality_sai_gia_tri_van_bi_chan(self):
        errs = validate_params("motion", {"quality": "4k"},
                               ast_params=self.ast, curated=self.curated)
        self.assertEqual(len(errs), 1)
        self.assertIn("720p", errs[0])


class TestCongChongTroi(unittest.TestCase):
    def test_repo_hien_tai_khong_troi(self):
        self.assertEqual(check_drift(LINUX_PY, CURATED), [])

    def test_param_doc_dong_moi_ma_chua_khai_thi_do(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "fake.py"
            p.write_text(FAKE, encoding="utf-8")
            c = Path(d) / "curated.json"
            c.write_text('{"enhance": {"extra": {}, "allowed": {}}}', encoding="utf-8")
            errs = check_drift(p, c)
            self.assertTrue(any("fpsInterp" in e for e in errs))

    def test_api_khong_bi_doi_phai_co_trong_AST(self):
        # Key ở .api KHÔNG BAO GIỜ xuất hiện trong AST của worker — đó là định nghĩa
        # của nó. Cổng đòi điều đó là đòi một điều không bao giờ đúng.
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "fake.py"
            p.write_text(FAKE, encoding="utf-8")
            c = Path(d) / "curated.json"
            c.write_text(
                '{"motion": {"extra": {}, "api": {"quality": {"where": "x.js:1"}},'
                ' "allowed": {"quality": ["540p"]}},'
                ' "enhance": {"extra": {"fpsInterp": {"why": "x"}, "fps_interp": {"why": "x"},'
                ' "fpsTarget": {"why": "x"}}, "api": {}, "allowed": {}}}',
                encoding="utf-8")
            self.assertEqual(check_drift(p, c), [])

    def test_khai_tay_thua_thi_do(self):
        # Key đã hiện ra trong AST mà vẫn nằm ở "extra" = khai tay đã cũ, phải gỡ.
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "fake.py"
            p.write_text(FAKE, encoding="utf-8")
            c = Path(d) / "curated.json"
            c.write_text(
                '{"motion": {"extra": {"frames": {"why": "cu"}}, "allowed": {}},'
                ' "enhance": {"extra": {"fpsInterp": {"why": "doc dong"},'
                ' "fps_interp": {"why": "x"}, "fpsTarget": {"why": "x"}}, "allowed": {}}}',
                encoding="utf-8")
            errs = check_drift(p, c)
            self.assertTrue(any("frames" in e for e in errs))


class TestCliJobTypeSai(unittest.TestCase):
    def test_job_type_sai_thi_liet_ke_job_type_that(self):
        # Không chỉ đòi exit code khác 0 — phải đòi message liệt kê được job type
        # thật, nếu không thì regression về message cụt (chỉ báo lỗi, không nói
        # tiếp phải làm gì) sẽ lọt qua test này.
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = batch_params.main(["moiton"])
        self.assertEqual(code, 1)
        output = err.getvalue()
        self.assertIn("motion", output)


if __name__ == "__main__":
    unittest.main()
