"""Ước lượng gain của swap_color_anchor — khoá lại đúng hai cách làm SAI đã tốn tiền GPU để phát hiện.

Toàn bộ test ở đây chạy trên frame tổng hợp, KHÔNG gọi ffmpeg: chúng kiểm phần ước lượng thuần
(frame_ratio), là chỗ cả hai lần hỏng đều nằm. Phần encode/sendcmd đã có motion_drift_fix lo và đã
được kiểm bằng chính output thật trong out/2026-08-23-1732.
"""
import array
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
from swap_color_anchor import PX, PROBE_W, frame_ratio  # noqa: E402


def _frame(fill):
    """fill(i) -> (r,g,b) cho pixel thứ i của khung 64×64."""
    a = array.array("B", bytes(PX * 3))
    for i in range(PX):
        r, g, b = fill(i)
        a[i * 3] = max(0, min(255, int(round(r))))
        a[i * 3 + 1] = max(0, min(255, int(round(g))))
        a[i * 3 + 2] = max(0, min(255, int(round(b))))
    return a


def _flat(r, g, b, person=None, person_frac=0.0):
    """Nền phẳng (r,g,b); person_frac phần đầu khung được tô màu `person` khác hẳn."""
    cut = int(PX * person_frac)
    return _frame(lambda i: person if (person and i < cut) else (r, g, b))


class TestUocLuongGain(unittest.TestCase):
    def test_nen_phang_bi_nen_chroma_thi_doi_lai_dung_gain(self):
        # Driver nền ấm; output bị nén chroma về phía xám (R giảm, B tăng) — đúng bệnh đo được.
        drv = _flat(160, 140, 120)
        out = _flat(150, 140, 128)
        kr, kb, _ = frame_ratio(out, drv, keep=0.95)
        # gain cần = (Rd/Gd)/(Ro/Go) = (160/140)/(150/140) = 1.0667
        self.assertAlmostEqual(kr, 160 / 150, places=3)
        self.assertAlmostEqual(kb, 120 / 128, places=3)

    def test_khong_lech_thi_gain_bang_1(self):
        f = _flat(160, 140, 120)
        kr, kb, _ = frame_ratio(f, f, keep=0.95)
        self.assertAlmostEqual(kr, 1.0, places=4)
        self.assertAlmostEqual(kb, 1.0, places=4)

    def test_vung_nguoi_mau_khac_han_KHONG_duoc_keo_lech_gain(self):
        """Hồi quy cho bug 23/08: bản dùng trung bình trần làm HỎNG clip vốn lành.

        Nền của output ĐÚNG y driver; 35% khung là "người" mang màu hoàn toàn khác (đến từ ảnh mẫu,
        đây là hợp lệ chứ không phải lỗi). Gain đúng phải ≈ 1 — pass không được đụng vào clip này.
        Bản trung bình trần trả gain lệch hẳn khỏi 1 và đẩy clip lành ra xa driver.
        """
        drv = _flat(160, 140, 120, person=(90, 190, 110), person_frac=0.35)
        out = _flat(160, 140, 120, person=(210, 150, 205), person_frac=0.35)
        kr, kb, _ = frame_ratio(out, drv, keep=0.95)
        self.assertAlmostEqual(kr, 1.0, delta=0.01, msg="vùng người kéo lệch gain R")
        self.assertAlmostEqual(kb, 1.0, delta=0.01, msg="vùng người kéo lệch gain B")

    def test_van_bat_duoc_benh_khi_co_ca_vung_nguoi(self):
        """Đối trọng của test trên: có người mà nền THẬT SỰ bị nén thì vẫn phải trả gain đúng.

        Nếu ai đó "sửa" bằng cách kẹp gain về 1 cho an toàn thì test này gãy.
        """
        drv = _flat(160, 140, 120, person=(90, 190, 110), person_frac=0.35)
        out = _flat(150, 140, 128, person=(210, 150, 205), person_frac=0.35)
        kr, kb, _ = frame_ratio(out, drv, keep=0.95)
        self.assertAlmostEqual(kr, 160 / 150, delta=0.01)
        self.assertAlmostEqual(kb, 120 / 128, delta=0.01)

    def test_trung_vi_khong_duoc_dung_lam_uoc_luong(self):
        """Hồi quy cho lần hỏng THỨ HAI: dùng trung vị per-pixel làm ước lượng.

        Khung nửa gần-xám nửa bão hoà, chỉ phần bão hoà bị nén. Trung vị sẽ do các pixel gần xám
        (tỉ lệ ≈ 1) quyết định và báo "sạch"; trung bình gộp thấy đúng là có lệch cần sửa.
        """
        half = PX // 2
        # Nén ~4,6% — cỡ lệch THẬT đo được (2–5%). Cố ý không dựng lệch to hơn: lệch >9% bị hàng rào
        # MAD loại là đúng thiết kế, dựng thế thì test đang kiểm hàng rào chứ không kiểm ước lượng.
        drv = _frame(lambda i: (128, 128, 128) if i < half else (180, 140, 100))
        out = _frame(lambda i: (128, 128, 128) if i < half else (172, 140, 105))
        kr, _kb, _ = frame_ratio(out, drv, keep=0.95)
        self.assertGreater(kr, 1.02, "ước lượng bị các pixel gần xám kéo về 1 — đang dùng trung vị?")

    def test_frame_toan_den_thi_khong_ket_luan(self):
        """Không đủ pixel hợp lệ (dưới sàn G_FLOOR) → trả gain 1, để yên, không đoán bừa."""
        drv = _flat(2, 3, 2)
        out = _flat(2, 3, 2)
        kr, kb, _ = frame_ratio(out, drv, keep=0.95)
        self.assertEqual((kr, kb), (1.0, 1.0))


class TestNoiVaoWorker(unittest.TestCase):
    def test_swap_dung_neo_driver_con_motion_dung_drift_fix(self):
        """Hai luồng phải gọi HAI pass khác nhau — xem chú thích ở run_motion (linux.py)."""
        src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "worker_runtime", "linux.py")
        with open(src, encoding="utf-8") as f:
            body = f.read()
        self.assertIn("_apply_swap_color_anchor(out_mp4, motion_local, tmp, params, job_id)", body,
                      "swap phải neo màu theo motion_local (đoạn driver đã nạp vào Wan)")
        self.assertIn("_apply_motion_drift_fix(out_mp4, ref_local, tmp, params, job_id)", body)

    def test_pass_tat_duoc_bang_param(self):
        import types
        try:
            import requests  # noqa: F401
        except ModuleNotFoundError:
            stub = types.ModuleType("requests")
            stub.exceptions = types.SimpleNamespace(ConnectionError=ConnectionError,
                                                    RequestException=Exception)
            sys.modules["requests"] = stub
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from worker_runtime.linux import _apply_swap_color_anchor
        self.assertEqual(
            _apply_swap_color_anchor("/khong/ton/tai.mp4", "/cung/khong.mp4", "/tmp",
                                     {"swapColorAnchor": "0"}, "job"),
            "/khong/ton/tai.mp4")


if __name__ == "__main__":
    unittest.main()
