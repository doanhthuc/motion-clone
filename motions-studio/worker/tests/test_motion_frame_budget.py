"""Preset drv-Ns là TRẦN THỜI LƯỢNG; RAM do ngân sách frame lo bằng cách hạ fps, không cắt clip.

Đây là hợp đồng khiến template Motion Control chỉ cần 2 file upload: user không phải chọn số giây,
và không bao giờ bị mất đuôi clip một cách âm thầm.
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# linux.py chỉ cần requests khi worker thật gọi API (giống test_wan_anchored_context).
try:
    import requests  # noqa: F401
except ModuleNotFoundError:
    _stub = types.ModuleType("requests")
    _stub.exceptions = types.SimpleNamespace(ConnectionError=ConnectionError, RequestException=Exception)
    sys.modules["requests"] = _stub

from worker_runtime.linux import (  # noqa: E402
    _motion_fit_frame_budget,
    _motion_frame_cap,
    _wan_cover_frames,
)

POD_CAP = 481   # trần cgroup 55,9 GiB → nhánh dưới-120GB
OLD_CAP = 601   # nhánh RAM host 123 GiB — con số cũ đọc sai


class FrameBudgetTest(unittest.TestCase):
    def test_clip_vua_ngan_sach_thi_giu_nguyen_fps_driver(self):
        for sec in (5, 10, 15):
            fps, frames, drop = _motion_fit_frame_budget(sec, 30, POD_CAP)
            self.assertEqual(fps, 30, f"clip {sec}s phải giữ 30fps")
            self.assertIsNone(drop)
            self.assertLessEqual(frames, POD_CAP)

    def test_clip_dai_thi_ha_fps_chu_khong_cat_thoi_luong(self):
        """Điểm mấu chốt: 20s vào → vẫn 20s ra, chỉ fps giảm."""
        for sec in (20, 25, 30):
            fps, frames, drop = _motion_fit_frame_budget(sec, 30, POD_CAP)
            self.assertIsNotNone(drop, f"clip {sec}s @30fps phải vượt trần {POD_CAP}")
            self.assertLess(fps, 30)
            self.assertGreaterEqual(fps, 12)
            self.assertLessEqual(frames, POD_CAP, f"clip {sec}s vẫn phải nằm trong ngân sách")
            # Thời lượng phủ đủ: frames/fps không được ngắn hơn clip gốc.
            self.assertGreaterEqual(frames / fps, sec - 1e-6, f"clip {sec}s bị cắt ngắn")

    def test_453_frame_da_nghiem_thu_van_chay_nguyen_30fps(self):
        """15s@30fps = 453f là job duy nhất đã chạy thật trên box này (82c9e58) — không được đổi."""
        self.assertEqual(_wan_cover_frames(15, 30), 453)
        fps, frames, drop = _motion_fit_frame_budget(15, 30, POD_CAP)
        self.assertEqual((fps, frames, drop), (30, 453, None))

    def test_601_frame_bi_chan_boi_tran_dung_nhung_lot_tran_cu(self):
        """Chính là hồi quy mà fix cgroup nhắm tới."""
        self.assertEqual(_wan_cover_frames(20, 30), 601)
        _, _, drop_old = _motion_fit_frame_budget(20, 30, OLD_CAP)
        self.assertIsNone(drop_old, "trần 601 (đọc RAM host) để lọt 601 frame — đúng hành vi CŨ")
        _, frames_new, drop_new = _motion_fit_frame_budget(20, 30, POD_CAP)
        self.assertIsNotNone(drop_new, "trần 481 (đọc cgroup) phải chặn 601 frame")
        self.assertLessEqual(frames_new, POD_CAP)

    def test_driver_fps_thap_thi_clip_dai_van_giu_nguyen_fps(self):
        """Driver 24fps: 20s = 481f vừa khít trần, không cần hạ."""
        fps, frames, drop = _motion_fit_frame_budget(20, 24, POD_CAP)
        self.assertEqual(fps, 24)
        self.assertIsNone(drop)
        self.assertLessEqual(frames, POD_CAP)

    def test_khong_bao_gio_ha_duoi_12fps(self):
        fps, _, _ = _motion_fit_frame_budget(120, 30, POD_CAP)
        self.assertEqual(fps, 12)

    def test_env_override_van_con_tac_dung(self):
        os.environ["MOTION_DRV_MAX_FRAMES"] = "321"
        self.addCleanup(os.environ.pop, "MOTION_DRV_MAX_FRAMES", None)
        self.assertEqual(_motion_frame_cap(), 321)

    def test_tran_mac_dinh_chi_nhan_2_gia_tri(self):
        os.environ.pop("MOTION_DRV_MAX_FRAMES", None)
        self.assertIn(_motion_frame_cap(), (481, 601))


if __name__ == "__main__":
    unittest.main()
