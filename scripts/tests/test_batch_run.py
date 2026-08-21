"""Test cho scripts/batch_run.py — resolve_batch_id() và các cổng trong main().

Đây chính là lỗ hổng đã để lọt bug --resume mint id mới: TRƯỚC bản sửa này,
không có gì trong batch_run.py có thể test được từ bên ngoài (mọi logic nằm
thẳng trong main(), không tách hàm). resolve_batch_id() được tách ra làm hàm
riêng, thuần (không gọi mạng, không gọi GPU) chính là để việc này test được.

main() cũng test được, không cần pod: patch load_settings/health_ok/run_batch
(ba mặt tiếp xúc duy nhất với tiền), và bọc stdout/stderr để suite vẫn câm.
"""
import contextlib
import datetime
import io
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.client import JobError
from batchlib.config import ConfigError, Settings
from batchlib.manifest import save_state, state_path_for
from batchlib.runner import BatchResult, LocalPhaseResult
import batch_run
from batch_run import preflight, resolve_batch_id

NOW = datetime.datetime(2026, 8, 18, 14, 30)

MANIFEST_CHAY_DUOC = """
runs:
  - id: runA
    pipeline: motion-enhance
    inputs:
      character: char.jpg
      driver: drv.mp4
"""


def _manifest_path(tmp: Path) -> Path:
    p = tmp / "b.yaml"
    p.write_text("runs: []\n", encoding="utf-8")
    return p


def _manifest_chay_duoc(tmp: Path) -> Path:
    (tmp / "char.jpg").write_bytes(b"x")
    (tmp / "drv.mp4").write_bytes(b"x")
    p = tmp / "b.yaml"
    p.write_text(MANIFEST_CHAY_DUOC, encoding="utf-8")
    return p


class TestResolveBatchId(unittest.TestCase):
    def test_khong_resume_thi_luon_la_lo_moi(self):
        with tempfile.TemporaryDirectory() as d:
            p = _manifest_path(Path(d))
            decision = resolve_batch_id(p, resume=False, now=NOW)
            self.assertEqual(decision.batch_id, "2026-08-18-1430")
            self.assertFalse(decision.resumed)

    def test_resume_co_state_thi_dung_lai_id_cu(self):
        # Nhánh 1: resume + state có batch id -> dùng lại NGUYÊN id đó, không mint mới.
        with tempfile.TemporaryDirectory() as d:
            p = _manifest_path(Path(d))
            save_state(state_path_for(p), {"version": 1, "batch": "2026-08-01-0900", "runs": {}})
            decision = resolve_batch_id(p, resume=True, now=NOW)
            self.assertEqual(decision.batch_id, "2026-08-01-0900")
            self.assertTrue(decision.resumed)
            self.assertIn("2026-08-01-0900", decision.note)

    def test_resume_khong_co_state_thi_bao_ro_va_chay_lo_moi(self):
        # Nhánh 2: resume + không có state (lần chạy đầu tiên) -> lô MỚI, và note phải
        # NÓI RÕ điều đó thay vì im lặng coi như đang resume một thứ chưa từng tồn tại.
        with tempfile.TemporaryDirectory() as d:
            p = _manifest_path(Path(d))
            decision = resolve_batch_id(p, resume=True, now=NOW)
            self.assertFalse(decision.resumed)
            self.assertEqual(decision.batch_id, "2026-08-18-1430")
            self.assertIn("chưa có lô nào", decision.note)

    def test_resume_state_khong_co_khoa_batch_cung_la_lo_moi(self):
        # Biến thể của nhánh 2: state file có thật nhưng không có khoá "batch" (vd
        # state cũ từ trước khi runner.py ghi khoá này) -> vẫn phải coi là lô mới.
        with tempfile.TemporaryDirectory() as d:
            p = _manifest_path(Path(d))
            save_state(state_path_for(p), {"version": 1, "runs": {}})
            decision = resolve_batch_id(p, resume=True, now=NOW)
            self.assertFalse(decision.resumed)
            self.assertEqual(decision.batch_id, "2026-08-18-1430")

    def test_resume_thu_muc_cu_da_bi_xoa_van_dung_lai_id(self):
        # Nhánh 3: state nói batch "2026-07-01-0000" nhưng out/2026-07-01-0000/ không hề
        # tồn tại trên đĩa (vd make batch-clean chỉ xoá runs/, giữ _final/, hoặc người
        # dùng xoá tay) -> KHÔNG được bịa id mới, việc đó sẽ mồ côi _final/ cũ đang có
        # thật. resolve_batch_id chỉ đọc state, không đụng tới filesystem của out/, nên
        # phải trả về đúng id cũ bất kể thư mục có tồn tại hay không.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = _manifest_path(tmp)
            save_state(state_path_for(p), {"version": 1, "batch": "2026-07-01-0000", "runs": {}})
            self.assertFalse((tmp / "out" / "2026-07-01-0000").exists())
            decision = resolve_batch_id(p, resume=True, now=NOW)
            self.assertEqual(decision.batch_id, "2026-07-01-0000")
            self.assertTrue(decision.resumed)


class TestMainTruyenCoResumeXuongRunBatch(unittest.TestCase):
    """main() phải truyền decision.resumed, KHÔNG phải args.resume.

    Hai giá trị đó lệch nhau ở đúng nhánh "RESUME=1 nhưng chưa có lô nào để tiếp":
    lúc đó batch_id là id MỚI, mà resume=True lại bảo run_batch đọc journal cũ — run
    "done" của lô trước bị bỏ qua nên không được hardlink vào _final/ mới, còn chặng
    đã xong thì mất file nên chạy lại. Đúng bug --resume đã sửa một lần.
    """

    def _goi_main(self, argv: list[str], tmp: Path):
        thu_duoc: dict = {}

        def fake_run_batch(**kwargs):
            thu_duoc.update(kwargs)
            return BatchResult(batch_id=kwargs["batch_id"],
                              out_dir=tmp / "out" / kwargs["batch_id"], done=["runA"])

        with mock.patch("batch_run.load_settings",
                        return_value=Settings(domain="pod.test", api_key="mk_test",
                                              instance_id="i-1")), \
             mock.patch("batch_run.health_ok", return_value=True), \
             mock.patch("batch_run.run_batch", fake_run_batch), \
             contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            code = batch_run.main(argv)
        return code, thu_duoc

    def test_resume_nhung_chua_co_lo_nao_thi_KHONG_truyen_resume(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = _manifest_chay_duoc(tmp)
            code, thu_duoc = self._goi_main(["--file", str(p), "--resume"], tmp)
            self.assertEqual(code, 0)
            self.assertFalse(thu_duoc["resume"])   # args.resume là True — giá trị SAI ở đây

    def test_resume_co_lo_that_thi_van_truyen_resume_va_dung_id_cu(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = _manifest_chay_duoc(tmp)
            save_state(state_path_for(p),
                       {"version": 1, "batch": "2026-08-01-0900", "runs": {}})
            code, thu_duoc = self._goi_main(["--file", str(p), "--resume"], tmp)
            self.assertEqual(code, 0)
            self.assertTrue(thu_duoc["resume"])
            self.assertEqual(thu_duoc["batch_id"], "2026-08-01-0900")

    def test_khong_resume_thi_khong_truyen_resume(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = _manifest_chay_duoc(tmp)
            code, thu_duoc = self._goi_main(["--file", str(p)], tmp)
            self.assertEqual(code, 0)
            self.assertFalse(thu_duoc["resume"])


class TestThieuNguonBangParam(unittest.TestCase):
    """Thiếu motions-studio/ (hoặc batch-params.json) phải ra câu nói được, không traceback.

    batch_params.py đã có đúng câu đó từ đầu; batch_run.py thì gọi thẳng
    extract_from_ast()/load_curated() không rào, nên một bản clone chưa rsync
    motions-studio/ nhận FileNotFoundError trần.
    """

    def _goi_main_voi_nguon_thieu(self, tmp: Path, **patches):
        p = _manifest_chay_duoc(tmp)
        err = io.StringIO()
        with contextlib.ExitStack() as stack:
            for name, value in patches.items():
                stack.enter_context(mock.patch.object(batch_run, name, value))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(contextlib.redirect_stderr(err))
            code = batch_run.main(["--file", str(p), "--validate-only"])
        return code, err.getvalue()

    def test_thieu_linux_py_thi_noi_ro_phai_lam_gi(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            code, msg = self._goi_main_voi_nguon_thieu(
                tmp, LINUX_PY=tmp / "khong-co" / "motions-studio" / "linux.py")
            self.assertEqual(code, 1)         # exit code, KHÔNG phải FileNotFoundError
            self.assertIn("linux.py", msg)
            self.assertIn("cd vào", msg)      # một trong hai nguyên nhân thật
            self.assertIn("rsync", msg)       # và nguyên nhân còn lại
            self.assertIn("validate", msg)    # vì sao nó chặn ở đây

    def test_thieu_batch_params_json_thi_bao_cach_khoi_phuc(self):
        # File này nằm TRONG repo, nên nguyên nhân khác hẳn: không phải "chưa rsync
        # motions-studio" mà là bị xoá/đổi tên — câu gợi ý phải khác theo.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            code, msg = self._goi_main_voi_nguon_thieu(tmp, CURATED=tmp / "khong-co.json")
            self.assertEqual(code, 1)
            self.assertIn("git checkout --", msg)
            self.assertNotIn("rsync", msg)


class TestValidateOnly(unittest.TestCase):
    """--validate-only phải nổ TRƯỚC khi tốn GPU — tức là TRƯỚC cả load_settings()."""

    def test_manifest_tot_thi_tra_ve_0_va_khong_dung_toi_settings(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = _manifest_chay_duoc(tmp)
            out = io.StringIO()
            with mock.patch("batch_run.load_settings") as m_settings, \
                 contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = batch_run.main(["--file", str(p), "--validate-only"])
            self.assertEqual(code, 0)
            self.assertIn("hợp lệ", out.getvalue())
            m_settings.assert_not_called()

    def test_manifest_sai_thi_tra_ve_1_va_khong_dung_toi_settings(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = tmp / "b.yaml"
            p.write_text(
                "runs:\n  - id: runA\n    pipeline: khong-co-that\n    inputs: {}\n",
                encoding="utf-8",
            )
            err = io.StringIO()
            with mock.patch("batch_run.load_settings") as m_settings, \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                code = batch_run.main(["--file", str(p), "--validate-only"])
            self.assertEqual(code, 1)
            msg = err.getvalue()
            self.assertIn("lỗi", msg)
            self.assertIn("pipeline không có thật", msg)
            m_settings.assert_not_called()


class TestManifestYamlHong(unittest.TestCase):
    def test_yaml_sai_cu_phap_thi_bao_va_tra_ve_1(self):
        # Khác hẳn "pipeline không có thật" (manifest ĐỌC được, chỉ validate sai) —
        # đây là file .yaml không parse nổi, bắt ở load_manifest() TRƯỚC validate.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = tmp / "b.yaml"
            p.write_text("runs: [ { id: x, pipeline: [broken\n", encoding="utf-8")
            err = io.StringIO()
            with mock.patch("batch_run.load_settings") as m_settings, \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                code = batch_run.main(["--file", str(p)])
            self.assertEqual(code, 1)
            self.assertIn("YAML hỏng", err.getvalue())
            m_settings.assert_not_called()


class TestConfigErrorTuLoadSettings(unittest.TestCase):
    def test_thieu_env_thi_bao_dung_loi_that_va_tra_ve_1(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = _manifest_chay_duoc(tmp)
            err = io.StringIO()
            with mock.patch("batch_run.load_settings",
                            side_effect=ConfigError("Thiếu DOMAIN trong .env.")), \
                 mock.patch("batch_run.health_ok") as m_health, \
                 mock.patch("batch_run.run_batch") as m_run_batch, \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                code = batch_run.main(["--file", str(p)])
            self.assertEqual(code, 1)
            self.assertIn("Thiếu DOMAIN", err.getvalue())
            m_health.assert_not_called()
            m_run_batch.assert_not_called()


class TestKetThucCoRunHong(unittest.TestCase):
    def test_co_run_hong_thi_tra_ve_1_va_goi_y_resume(self):
        # Lô CHẠY XONG (run_batch không ném gì) nhưng có run hỏng — nhánh đuôi của
        # main() phải nói rõ pod vẫn chạy, chỉ lệnh xem log, VÀ đúng lệnh RESUME=1
        # (chỉ chạy lại phần thiếu — không phải trả tiền GPU lại từ đầu).
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = _manifest_chay_duoc(tmp)
            fake_result = BatchResult(
                batch_id="2026-08-18-1430", out_dir=tmp / "out" / "2026-08-18-1430",
                done=[], failed={"runA": "job hỏng: ComfyUI 400"}, gpu_seconds=120,
            )
            out = io.StringIO()
            with mock.patch("batch_run.load_settings",
                            return_value=Settings(domain="pod.test", api_key="mk_test",
                                                  instance_id="i-1")), \
                 mock.patch("batch_run.health_ok", return_value=True), \
                 mock.patch("batch_run.run_batch", return_value=fake_result), \
                 contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = batch_run.main(["--file", str(p)])
            self.assertEqual(code, 1)
            text = out.getvalue()
            self.assertIn("runA", text)
            self.assertIn("job hỏng: ComfyUI 400", text)
            self.assertIn("make gpu-logs LOG=worker", text)
            self.assertIn(f"make batch FILE={p} RESUME=1", text)


class TestPreflightHam(unittest.TestCase):
    """preflight() thuần — không qua argv/main(), năm nhánh của nó."""

    @staticmethod
    def _settings(instance_id: str) -> Settings:
        return Settings(domain="pod.test", api_key="mk_test", instance_id=instance_id)

    def test_pod_dang_chay_thi_qua_ngay_khong_dung_toi_gpu_up(self):
        with mock.patch("batch_run.health_ok", return_value=True), \
             mock.patch("batch_run.subprocess.run") as m_run, \
             contextlib.redirect_stdout(io.StringIO()):
            ok = preflight(self._settings("i-1"), allow_start=True)
        self.assertTrue(ok)
        m_run.assert_not_called()

    def test_khong_co_instance_id_thi_tu_choi_va_neu_gpu_provision(self):
        err = io.StringIO()
        with mock.patch("batch_run.health_ok", return_value=False), \
             mock.patch("batch_run.subprocess.run") as m_run, \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            ok = preflight(self._settings(""), allow_start=True)
        self.assertFalse(ok)
        self.assertIn("gpu-provision", err.getvalue())
        m_run.assert_not_called()

    def test_co_instance_id_nhung_no_start_thi_tu_choi_khong_tu_bat(self):
        err = io.StringIO()
        with mock.patch("batch_run.health_ok", return_value=False), \
             mock.patch("batch_run.subprocess.run") as m_run, \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            ok = preflight(self._settings("i-1"), allow_start=False)
        self.assertFalse(ok)
        self.assertIn("gpu-up", err.getvalue())
        m_run.assert_not_called()

    def test_allow_start_va_gpu_up_thanh_cong_thi_kiem_tra_lai_va_qua(self):
        calls: list = []

        def fake_health(_settings):
            calls.append("health")
            return len(calls) > 1   # lần đầu False (pod đang dừng), sau gpu-up thì True

        def fake_run(cmd, **_kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0)

        with mock.patch("batch_run.health_ok", fake_health), \
             mock.patch("batch_run.subprocess.run", fake_run), \
             contextlib.redirect_stdout(io.StringIO()):
            ok = preflight(self._settings("i-1"), allow_start=True)
        self.assertTrue(ok)
        self.assertIn(["make", "gpu-up"], calls)

    def test_gpu_up_that_bai_thi_tra_ve_false(self):
        def fake_run(cmd, **_kwargs):
            return subprocess.CompletedProcess(cmd, 1)

        err = io.StringIO()
        with mock.patch("batch_run.health_ok", return_value=False), \
             mock.patch("batch_run.subprocess.run", fake_run), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            ok = preflight(self._settings("i-1"), allow_start=True)
        self.assertFalse(ok)
        self.assertIn("gpu-up thất bại", err.getvalue())


class TestMainPreflightKhongCoPod(unittest.TestCase):
    def test_khong_co_pod_qua_main_thi_tu_choi_va_khong_goi_run_batch(self):
        # Đây đúng là nhánh spec đòi: không có pod nào tồn tại -> refuse, nêu tên
        # make gpu-provision, và TUYỆT ĐỐI không được đụng tới run_batch (không job
        # nào được submit).
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = _manifest_chay_duoc(tmp)
            err = io.StringIO()
            with mock.patch("batch_run.load_settings",
                            return_value=Settings(domain="pod.test", api_key="mk_test",
                                                  instance_id="")), \
                 mock.patch("batch_run.health_ok", return_value=False), \
                 mock.patch("batch_run.run_batch") as m_run_batch, \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                code = batch_run.main(["--file", str(p)])
            self.assertEqual(code, 1)
            self.assertIn("gpu-provision", err.getvalue())
            m_run_batch.assert_not_called()


class TestNoStartTruyenXuongPreflight(unittest.TestCase):
    def _goi(self, argv: list[str], tmp: Path):
        captured: dict = {}

        def fake_preflight(_settings, *, allow_start):
            captured["allow_start"] = allow_start
            return False   # dừng sớm — test này chỉ kiểm tra wiring của cờ, không hơn

        p = _manifest_chay_duoc(tmp)
        with mock.patch("batch_run.load_settings",
                        return_value=Settings(domain="pod.test", api_key="mk_test",
                                              instance_id="i-1")), \
             mock.patch("batch_run.preflight", fake_preflight), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = batch_run.main(argv + ["--file", str(p)])
        return code, captured

    def test_co_no_start_thi_allow_start_false(self):
        with tempfile.TemporaryDirectory() as d:
            code, captured = self._goi(["--no-start"], Path(d))
            self.assertEqual(code, 1)
            self.assertFalse(captured["allow_start"])

    def test_khong_co_no_start_thi_allow_start_true(self):
        with tempfile.TemporaryDirectory() as d:
            code, captured = self._goi([], Path(d))
            self.assertEqual(code, 1)
            self.assertTrue(captured["allow_start"])


class TestMatKetNoiGiuaChungLopCloudflare(unittest.TestCase):
    """URLError/OSError bọc quanh run_batch (batch_run.py) — nhánh CHƯA từng chạy.

    Đây là đúng lớp bug 403/Cloudflare đã phơi ra trên pod thật: 143 test cũ xanh vì
    chỉ bắn vào http.server giả trên 127.0.0.1. Job vừa gửi có thể VẪN đang chạy trên
    pod, nên thông báo phải cấm gửi lại và chỉ đúng một đường: RESUME=1.
    """

    def _goi(self, exc: Exception, tmp: Path):
        p = _manifest_chay_duoc(tmp)

        def boom(**_kwargs):
            raise exc

        err = io.StringIO()
        with mock.patch("batch_run.load_settings",
                        return_value=Settings(domain="pod.test", api_key="mk_test",
                                              instance_id="i-1")), \
             mock.patch("batch_run.health_ok", return_value=True), \
             mock.patch("batch_run.run_batch", boom), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = batch_run.main(["--file", str(p)])
        return code, err.getvalue(), p

    def test_urlerror_bao_dung_va_cam_gui_lai(self):
        with tempfile.TemporaryDirectory() as d:
            code, msg, p = self._goi(urllib.error.URLError("mang rot giua chung"), Path(d))
            self.assertEqual(code, 1)
            self.assertIn("Mất kết nối", msg)
            self.assertIn("VẪN đang chạy", msg)
            self.assertIn("đừng chạy lại từ đầu", msg)
            self.assertIn("RESUME=1", msg)
            self.assertIn(str(p), msg)   # gợi ý lệnh phải chỉ đúng FILE này

    def test_oserror_cung_duoc_bat_giong_urlerror(self):
        with tempfile.TemporaryDirectory() as d:
            code, msg, _p = self._goi(OSError("socket dut"), Path(d))
            self.assertEqual(code, 1)
            self.assertIn("Mất kết nối", msg)
            self.assertIn("RESUME=1", msg)


MANIFEST_TRYON_GEMINI = """
runs:
  - id: runA
    pipeline: tryon-motion-enhance
    inputs:
      character: char.jpg
      outfit: outfit.jpg
      driver: drv.mp4
    tryon: { provider: gemini }
"""


def _manifest_tryon_gemini(tmp: Path) -> Path:
    (tmp / "char.jpg").write_bytes(b"x")
    (tmp / "outfit.jpg").write_bytes(b"x")
    (tmp / "drv.mp4").write_bytes(b"x")
    p = tmp / "b.yaml"
    p.write_text(MANIFEST_TRYON_GEMINI, encoding="utf-8")
    return p


class TestMainPhaA(unittest.TestCase):
    """Try-on local chạy TRƯỚC preflight — và pod chưa sẵn sàng thì báo rõ đã xong Pha A."""

    def test_khong_co_pod_sau_khi_xong_pha_a_thi_bao_ro_va_khong_goi_run_batch(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = _manifest_tryon_gemini(tmp)

            def fake_run_local_tryon(run, params, settings_, out_path):
                out_path.write_bytes(b"fake")
                return 2, out_path.stat().st_size

            err = io.StringIO()
            with mock.patch("batch_run.load_settings",
                            return_value=Settings(domain="pod.test", api_key="mk_test",
                                                  instance_id="", gemini_api_key="AIza" + "x" * 35)), \
                 mock.patch("batch_run.health_ok", return_value=False), \
                 mock.patch("batchlib.runner.run_local_tryon", fake_run_local_tryon), \
                 mock.patch("batch_run.run_batch") as m_run_batch, \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                code = batch_run.main(["--file", str(p)])
            self.assertEqual(code, 1)
            self.assertIn("gpu-provision", err.getvalue())
            self.assertIn("RESUME=1", err.getvalue())
            m_run_batch.assert_not_called()

    def test_pod_san_sang_thi_chay_tiep_ca_pha_b_voi_state_da_chuan_bi(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = _manifest_tryon_gemini(tmp)

            def fake_run_local_tryon(run, params, settings_, out_path):
                out_path.write_bytes(b"fake")
                return 2, out_path.stat().st_size

            captured: dict = {}

            def fake_run_batch(**kwargs):
                captured.update(kwargs)
                return BatchResult(batch_id=kwargs["batch_id"],
                                   out_dir=kwargs.get("prepared", (tmp / "out" / kwargs["batch_id"],))[0],
                                   done=["runA"])

            with mock.patch("batch_run.load_settings",
                            return_value=Settings(domain="pod.test", api_key="mk_test",
                                                  instance_id="i-1", gemini_api_key="AIza" + "x" * 35)), \
                 mock.patch("batch_run.health_ok", return_value=True), \
                 mock.patch("batchlib.runner.run_local_tryon", fake_run_local_tryon), \
                 mock.patch("batch_run.run_batch", fake_run_batch), \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = batch_run.main(["--file", str(p)])
            self.assertEqual(code, 0)
            self.assertIn("prepared", captured)
            out_dir, state, _state_file = captured["prepared"]
            self.assertEqual(state["runs"]["runA"]["stages"]["tryon"]["status"], "done")

    def test_fail_fast_pha_a_hong_thi_dung_han_khong_dung_pod(self):
        # --fail-fast = "dừng cả lô ngay khi một run hỏng". Đi tiếp sau khi Pha A hỏng
        # nghĩa là run_one gửi LẠI chính chặng try-on đó lên pod (journal ghi "error",
        # không phải "done") — tiêu tiền GPU cho đúng lô người dùng bảo hãy dừng.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = _manifest_tryon_gemini(tmp)

            def fake_run_local_tryon(run, params, settings_, out_path):
                raise JobError("gemini 429 het quota")

            err = io.StringIO()
            with mock.patch("batch_run.load_settings",
                            return_value=Settings(domain="pod.test", api_key="mk_test",
                                                  instance_id="i-1",
                                                  gemini_api_key="AIza" + "x" * 35)), \
                 mock.patch("batch_run.health_ok", return_value=True), \
                 mock.patch("batch_run.preflight") as m_preflight, \
                 mock.patch("batchlib.runner.run_local_tryon", fake_run_local_tryon), \
                 mock.patch("batch_run.run_batch") as m_run_batch, \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                code = batch_run.main(["--file", str(p), "--fail-fast"])
            self.assertEqual(code, 1)
            m_run_batch.assert_not_called()
            m_preflight.assert_not_called()   # không cả HỎI pod, chứ đừng nói bật nó
            self.assertIn("fail-fast", err.getvalue())

    def test_khong_fail_fast_thi_pha_a_hong_van_chay_tiep_len_pod(self):
        # Mặt còn lại của cùng một cổng: KHÔNG có cờ thì hành vi cũ giữ nguyên — Pha A
        # hỏng một run vẫn đi tiếp để pod tự chữa (đó chính là lý do run_local_phase
        # không chặn tự chữa ở Pha B).
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = _manifest_tryon_gemini(tmp)

            def fake_run_local_tryon(run, params, settings_, out_path):
                raise JobError("gemini 429 het quota")

            with mock.patch("batch_run.load_settings",
                            return_value=Settings(domain="pod.test", api_key="mk_test",
                                                  instance_id="i-1",
                                                  gemini_api_key="AIza" + "x" * 35)), \
                 mock.patch("batch_run.health_ok", return_value=True), \
                 mock.patch("batch_run.run_batch",
                            return_value=BatchResult(batch_id="b", out_dir=tmp / "out",
                                                     done=["runA"])) as m_run_batch, \
                 mock.patch("batchlib.runner.run_local_tryon", fake_run_local_tryon), \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = batch_run.main(["--file", str(p)])
            self.assertEqual(code, 0)
            m_run_batch.assert_called_once()


class TestLocalTryonWorkers(unittest.TestCase):
    """LOCAL_TRYON_WORKERS (spec §4): số job Gemini bay cùng lúc, chỉnh từ env."""

    def _goi(self, env: dict, tmp: Path):
        captured: dict = {}

        def fake_run_local_phase(**kwargs):
            captured.update(kwargs)
            return LocalPhaseResult(ran=False)

        p = _manifest_tryon_gemini(tmp)
        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch("batch_run.load_settings",
                        return_value=Settings(domain="pod.test", api_key="mk_test",
                                              instance_id="i-1",
                                              gemini_api_key="AIza" + "x" * 35)), \
             mock.patch("batch_run.run_local_phase", fake_run_local_phase), \
             mock.patch("batch_run.health_ok", return_value=False), \
             mock.patch("batch_run.run_batch") as m_run_batch, \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = batch_run.main(["--file", str(p), "--no-start"])
        return code, captured, m_run_batch

    def test_doc_env_va_truyen_xuong_pool_size(self):
        with tempfile.TemporaryDirectory() as d:
            _code, captured, _ = self._goi({"LOCAL_TRYON_WORKERS": "7"}, Path(d))
            self.assertEqual(captured["pool_size"], 7)

    def test_khong_dat_env_thi_mac_dinh_4(self):
        with tempfile.TemporaryDirectory() as d:
            env = {k: v for k, v in os.environ.items() if k != "LOCAL_TRYON_WORKERS"}
            with mock.patch.dict(os.environ, env, clear=True):
                _code, captured, _ = self._goi({}, Path(d))
            self.assertEqual(captured["pool_size"], 4)

    def test_env_khong_phai_so_thi_bao_loi_chu_khong_traceback(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = _manifest_tryon_gemini(tmp)
            err = io.StringIO()
            with mock.patch.dict(os.environ, {"LOCAL_TRYON_WORKERS": "nhieu"}), \
                 mock.patch("batch_run.load_settings",
                            return_value=Settings(domain="pod.test", api_key="mk_test",
                                                  instance_id="i-1",
                                                  gemini_api_key="AIza" + "x" * 35)), \
                 mock.patch("batch_run.run_batch") as m_run_batch, \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                code = batch_run.main(["--file", str(p)])
            self.assertEqual(code, 1)
            self.assertIn("LOCAL_TRYON_WORKERS", err.getvalue())
            m_run_batch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
