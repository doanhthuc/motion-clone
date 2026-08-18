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
        # pod-smoke.sh:292 và FE đều gửi nó — chặn nó là chặn nhầm.
        self.assertNotIn("quality", self.ast.get("motion", {}))
        known = known_params("motion", ast_params=self.ast, curated=self.curated)
        self.assertIn("quality", known)
        self.assertEqual(known["quality"].source, "api")
        # …và KÈM preset drv-* thì nó thật sự có tác dụng, nên không được báo lỗi.
        self.assertEqual(
            validate_params("motion", {"quality": "720p", "preset": "drv-5s"},
                            ast_params=self.ast, curated=self.curated), [])

    def test_quality_khong_kem_preset_drv_thi_bi_chan(self):
        # ĐÂY là ca bug: batch/example.yaml từng dạy `{ quality: 540p, frames: 33 }`
        # không có preset. enforceMotionResolution (motion-resolution.js:20) return SỚM
        # nếu preset không phải drv-*, và worker không đọc quality → param validate xong
        # rồi biến mất. 540p vô hại (đúng mặc định) nhưng 720p thì ra 540p không ai biết.
        errs = validate_params("motion", {"quality": "720p", "frames": 33},
                               ast_params=self.ast, curated=self.curated)
        self.assertEqual(len(errs), 1)
        self.assertIn("BỎ QUA", errs[0])
        self.assertIn("drv-5s", errs[0])                 # nói rõ giá trị preset cần có
        self.assertIn("motion-resolution.js:20", errs[0])  # và chỗ code chứng minh điều đó

    def test_quality_kem_preset_khong_phai_drv_van_bi_chan(self):
        # preset CÓ, nhưng không thuộc DRIVER_PRESETS -> enforceMotionResolution vẫn
        # return sớm. Luật là "preset phải là drv-*", không phải "có preset là được".
        errs = validate_params("motion", {"quality": "720p", "preset": "10s-720p"},
                               ast_params=self.ast, curated=self.curated)
        self.assertEqual(len(errs), 1)
        self.assertIn("BỎ QUA", errs[0])

    def test_quality_sai_gia_tri_van_bi_chan(self):
        errs = validate_params("motion", {"quality": "4k"},
                               ast_params=self.ast, curated=self.curated)
        self.assertEqual(len(errs), 1)
        self.assertIn("720p", errs[0])

    def test_render_profile_max_bi_chan_kem_duong_lui_bang_env(self):
        # render_profile: "max" validate sạch rồi biến mất: jobs.js:33-34
        # (normalizeMotionDriverSegment) ép "fast" VÔ ĐIỀU KIỆN, trước cả early-return.
        # Đây là knob người dùng thật sự với tới (20-step A/B), nên thông báo phải chỉ
        # đúng đường còn lại là env MOTION_FORCE_QUALITY của worker.
        errs = validate_params("motion", {"render_profile": "max"},
                               ast_params=self.ast, curated=self.curated)
        self.assertEqual(len(errs), 1)
        # Đo lại 18/08/2026: assignment thật ở jobs.js:36-37, không phải 33-34 như bản
        # đầu ghi. Ghim số dòng trong test là con dao hai lưỡi — nó bắt được thay đổi
        # (chính test này bắt tôi lúc sửa citation), nhưng cũng khoá luôn số SAI nếu
        # không ai đi đo. Sửa citation thì sửa cả đây.
        self.assertIn("jobs.js:36-37", errs[0])
        self.assertIn("MOTION_FORCE_QUALITY", errs[0])

    def test_render_profile_fast_khong_bi_chan(self):
        # "fast" đúng bằng giá trị API ép -> không có gì bị mất, không báo lỗi.
        self.assertEqual(
            validate_params("motion", {"render_profile": "fast"},
                            ast_params=self.ast, curated=self.curated), [])

    def test_example_yaml_trong_repo_qua_duoc_validate(self):
        # batch/example.yaml là bản người dùng CHÉP ĐI rồi sửa. Nó dạy sai thì mọi
        # manifest sinh ra từ nó đều mang cùng một param vô hiệu — nên chính nó phải
        # qua được luật ở trên (chỉ trừ lỗi "không thấy file", vì .smoke/ đã gitignore).
        import yaml
        raw = yaml.safe_load((REPO / "batch" / "example.yaml").read_text(encoding="utf-8"))
        defaults = raw.get("defaults") or {}
        for entry in raw["runs"]:
            for stage in ("tryon", "motion", "enhance"):
                merged = {**(defaults.get(stage) or {}), **(entry.get(stage) or {})}
                if not merged:
                    continue
                self.assertEqual(
                    validate_params(stage, merged, ast_params=self.ast, curated=self.curated),
                    [], f"{entry['id']} · {stage}")


class TestGhiDeCoDieuKien(unittest.TestCase):
    """Quan hệ thứ ba, khác cả `requires` lẫn `overridden` không điều kiện.

    preset drv-Ns là TRẦN THỜI LƯỢNG: worker ffprobe driver rồi gán
    params["frames"]/["render_fps"] (linux.py:4141-4142), ghi đè bất kể manifest viết gì.
    Đo 18/08/2026 với driver 30fps: `preset: drv-5s` + `frames: 33` thật ra chạy ~151
    frame — gấp 4,6 lần, tức gấp 4,6 lần tiền GPU so với con số người dùng tưởng.
    Validate KHÔNG bắt được nếu thiếu luật này, vì `frames` là param CÓ THẬT của worker
    nên nó qua cổng rồi mới bị vứt.
    """

    def setUp(self):
        self.ast = extract_from_ast(LINUX_PY)
        self.curated = load_curated(CURATED)

    def _v(self, params):
        return validate_params("motion", params, ast_params=self.ast, curated=self.curated)

    def test_co_preset_drv_thi_frames_va_render_fps_bi_chan(self):
        errs = self._v({"preset": "drv-5s", "frames": 33, "render_fps": 16})
        self.assertEqual(len(errs), 2, errs)
        joined = "\n".join(errs)
        self.assertIn("frames", joined)
        self.assertIn("render_fps", joined)
        # Thông báo phải chỉ ra ĐƯỜNG RA, không chỉ nói "sẽ bị ghi đè" rồi thôi.
        self.assertIn("Bỏ preset drv-*", joined)

    def test_khong_preset_thi_frames_duoc_ton_trong(self):
        # Đây là hình dạng pod-smoke.sh:293 dùng thật — chặn nó là chặn nhầm.
        self.assertEqual(self._v({"frames": 33, "render_fps": 16}), [])

    def test_preset_drv_mot_minh_khong_bi_chan(self):
        # Không khai frames thì không có gì bị ghi đè để mà cảnh báo.
        self.assertEqual(self._v({"preset": "drv-30s"}), [])

    def test_ghi_de_KHONG_dieu_kien_van_chan_nhu_cu(self):
        # render_profile không có "when" → phải chặn bất kể có preset hay không.
        self.assertEqual(len(self._v({"render_profile": "max"})), 1)
        self.assertEqual(len(self._v({"preset": "drv-5s", "render_profile": "max"})), 1)

    def test_cong_chan_when_khai_nua_voi(self):
        # "when" thiếu param/values = luật im lặng: đọc file tưởng có luật, chạy thì không.
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d) / "fake.py"
            fake.write_text(FAKE, encoding="utf-8")
            cur = Path(d) / "curated.json"
            cur.write_text(
                '{"motion": {"extra": {}, "api": {}, "allowed": {},'
                ' "overridden": {"frames": {"forced": "x", "when": {"param": "preset"}}}},'
                ' "enhance": {"extra": {"fpsInterp": {"why": "x"}, "fps_interp": {"why": "x"},'
                ' "fpsTarget": {"why": "x"}}, "api": {}, "allowed": {}}}',
                encoding="utf-8")
            errs = check_drift(fake, cur)
            self.assertTrue(any("when" in e and "frames" in e for e in errs), errs)


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

    def test_requires_tro_vao_param_khong_co_that_thi_do(self):
        # Luật im lặng là loại tệ nhất: đọc file thì tưởng có luật, mà validate không
        # bao giờ chạm tới key đó. Cùng lý do .allowed bị kiểm từ đầu.
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "fake.py"
            p.write_text(FAKE, encoding="utf-8")
            c = Path(d) / "curated.json"
            c.write_text(
                '{"motion": {"extra": {}, "allowed": {},'
                ' "requires": {"khong_co_that": {"param": "preset", "values": ["drv-5s"]}}}}',
                encoding="utf-8")
            errs = check_drift(p, c)
            self.assertTrue(any("khong_co_that" in e and "requires" in e for e in errs), errs)

    def test_requires_thieu_values_thi_do(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "fake.py"
            p.write_text(FAKE, encoding="utf-8")
            c = Path(d) / "curated.json"
            c.write_text('{"motion": {"extra": {}, "allowed": {},'
                         ' "requires": {"frames": {"param": "preset"}}}}', encoding="utf-8")
            errs = check_drift(p, c)
            self.assertTrue(any("values" in e for e in errs), errs)

    def test_overridden_thieu_forced_thi_do(self):
        # Không có "forced" thì validate không biết so giá trị người dùng gõ với cái gì,
        # nên luật không chặn được gì.
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "fake.py"
            p.write_text(FAKE, encoding="utf-8")
            c = Path(d) / "curated.json"
            c.write_text('{"motion": {"extra": {}, "allowed": {},'
                         ' "overridden": {"frames": {"where": "x.js:1"}}}}', encoding="utf-8")
            errs = check_drift(p, c)
            self.assertTrue(any("forced" in e for e in errs), errs)

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
