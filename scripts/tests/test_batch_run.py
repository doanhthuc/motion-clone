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
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.config import Settings
from batchlib.manifest import save_state, state_path_for
from batchlib.runner import BatchResult
import batch_run
from batch_run import resolve_batch_id

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


if __name__ == "__main__":
    unittest.main()
