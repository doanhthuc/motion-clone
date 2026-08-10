"""box_ram: ngân sách frame phải tính theo trần cgroup, không theo RAM host.

Số thật lấy từ pod motion lúc điều tra cgroup OOM-kill (commit 82c9e58):
    memory.max  59999997952  = 55,9 GiB  (trần container)
    MemTotal    129429860 kB = 123 GiB   (RAM host — con số SAI để tính ngân sách)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from worker_runtime.box_ram import box_ram_bytes, box_ram_gb  # noqa: E402

GIB = 1024 ** 3
POD_CGROUP_LIMIT = 59999997952      # 55,9 GiB
POD_HOST_RAM = 129429860 * 1024     # 123 GiB


def _write(tmpdir, name, text):
    path = os.path.join(tmpdir, name)
    with open(path, "w") as f:
        f.write(text)
    return path


class BoxRamTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def test_pod_runpod_dung_tran_cgroup_khong_dung_ram_host(self):
        v2 = _write(self.tmp, "memory.max", str(POD_CGROUP_LIMIT))
        self.assertEqual(box_ram_bytes(host_bytes=POD_HOST_RAM, paths=(v2,)), POD_CGROUP_LIMIT)

    def test_tran_cgroup_quyet_dinh_nhanh_fcap_481_thay_vi_601(self):
        """Đây mới là lý do fix tồn tại: cùng pod, RAM host cho 601f, trần cgroup cho 481f."""
        v2 = _write(self.tmp, "memory.max", str(POD_CGROUP_LIMIT))
        ram_gb = box_ram_gb(host_bytes=POD_HOST_RAM, paths=(v2,))
        self.assertLess(ram_gb, 120, "trần cgroup 55,9 GiB phải rơi vào nhánh 481f")
        self.assertGreaterEqual(POD_HOST_RAM / GIB, 120, "RAM host 123 GiB là nhánh 601f cũ — sai")

    def test_cgroup_v2_ghi_max_la_khong_gioi_han(self):
        v2 = _write(self.tmp, "memory.max", "max")
        self.assertEqual(box_ram_bytes(host_bytes=POD_HOST_RAM, paths=(v2,)), POD_HOST_RAM)

    def test_cgroup_v1_so_khong_lo_la_khong_gioi_han(self):
        v1 = _write(self.tmp, "limit_in_bytes", str(2 ** 63 - 1))
        self.assertEqual(box_ram_bytes(host_bytes=POD_HOST_RAM, paths=(v1,)), POD_HOST_RAM)

    def test_fallback_sang_v1_khi_v2_vang_mat(self):
        missing = os.path.join(self.tmp, "khong-ton-tai")
        v1 = _write(self.tmp, "limit_in_bytes", str(POD_CGROUP_LIMIT))
        self.assertEqual(box_ram_bytes(host_bytes=POD_HOST_RAM, paths=(missing, v1)), POD_CGROUP_LIMIT)

    def test_khong_co_cgroup_thi_giu_nguyen_ram_host(self):
        """Máy thật / pod không giới hạn / dev macOS — không được đổi hành vi cũ."""
        missing = os.path.join(self.tmp, "khong-ton-tai")
        self.assertEqual(box_ram_bytes(host_bytes=POD_HOST_RAM, paths=(missing,)), POD_HOST_RAM)

    def test_tran_cgroup_rong_hon_ram_host_thi_khong_kep(self):
        v2 = _write(self.tmp, "memory.max", str(POD_HOST_RAM))
        self.assertEqual(box_ram_bytes(host_bytes=8 * GIB, paths=(v2,)), 8 * GIB)

    def test_file_rac_khong_lam_vo(self):
        junk = _write(self.tmp, "memory.max", "khong-phai-so")
        self.assertEqual(box_ram_bytes(host_bytes=POD_HOST_RAM, paths=(junk,)), POD_HOST_RAM)

    def test_khong_doc_duoc_ram_host_thi_tin_tran_cgroup(self):
        v2 = _write(self.tmp, "memory.max", str(POD_CGROUP_LIMIT))
        self.assertEqual(box_ram_bytes(host_bytes=0, paths=(v2,)), POD_CGROUP_LIMIT)


if __name__ == "__main__":
    unittest.main()
