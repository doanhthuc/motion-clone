"""Test cho scripts/batch_run.py — hiện chỉ resolve_batch_id().

Đây chính là lỗ hổng đã để lọt bug --resume mint id mới: TRƯỚC bản sửa này,
không có gì trong batch_run.py có thể test được từ bên ngoài (mọi logic nằm
thẳng trong main(), không tách hàm). resolve_batch_id() được tách ra làm hàm
riêng, thuần (không gọi mạng, không gọi GPU) chính là để việc này test được.
"""
import datetime
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.manifest import save_state, state_path_for
from batch_run import resolve_batch_id

NOW = datetime.datetime(2026, 8, 18, 14, 30)


def _manifest_path(tmp: Path) -> Path:
    p = tmp / "b.yaml"
    p.write_text("runs: []\n", encoding="utf-8")
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


if __name__ == "__main__":
    unittest.main()
