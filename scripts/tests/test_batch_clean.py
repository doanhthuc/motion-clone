import contextlib
import io
import shutil
import sys, tempfile, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import batch_clean
from batch_clean import prune


def _batches(root: Path, names: list[str]) -> None:
    for name in names:
        (root / name / "runs" / "r1").mkdir(parents=True)
        (root / name / "runs" / "r1" / "01-motion.mp4").write_bytes(b"x")
        (root / name / "_final").mkdir(parents=True)
        (root / name / "_final" / "r1.mp4").write_bytes(b"x")


class TestPrune(unittest.TestCase):
    def test_giu_n_lo_moi_nhat_xoa_runs_cua_phan_con_lai(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _batches(root, ["2026-08-01-1000", "2026-08-02-1000", "2026-08-03-1000"])
            removed = prune(root, keep=2)
            self.assertEqual([p.parent.name for p in removed], ["2026-08-01-1000"])
            self.assertFalse((root / "2026-08-01-1000" / "runs").exists())
            self.assertTrue((root / "2026-08-02-1000" / "runs").exists())

    def test_final_khong_bao_gio_bi_dung_toi(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _batches(root, ["2026-08-01-1000", "2026-08-02-1000"])
            prune(root, keep=1)
            self.assertTrue((root / "2026-08-01-1000" / "_final" / "r1.mp4").is_file())

    def test_dry_run_liet_ke_ma_khong_xoa(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _batches(root, ["2026-08-01-1000", "2026-08-02-1000"])
            removed = prune(root, keep=1, dry_run=True)
            self.assertEqual(len(removed), 1)
            self.assertTrue((root / "2026-08-01-1000" / "runs").exists())

    def test_it_hon_keep_thi_khong_xoa_gi(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _batches(root, ["2026-08-01-1000"])
            self.assertEqual(prune(root, keep=3), [])

    def test_dung_bang_keep_thi_khong_xoa_gi(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            names = ["2026-08-01-1000", "2026-08-02-1000", "2026-08-03-1000"]
            _batches(root, names)
            self.assertEqual(prune(root, keep=3), [])
            for name in names:
                self.assertTrue((root / name / "runs").exists())

    def test_keep_0_bi_chan(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                prune(Path(d), keep=0)

    def test_lo_qua_han_nhung_khong_co_runs_thi_bo_qua_khong_loi(self):
        # Một batch quá KEEP nhưng đã sạch runs/ từ trước (vd một lượt batch-clean
        # trước đó, hoặc lô chỉ có _final vì mọi chặng đều bắt lại được job cũ) không
        # được làm prune() vấp lỗi hay bị liệt kê là "vừa xoá" — không có gì để xoá.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _batches(root, ["2026-08-01-1000", "2026-08-02-1000", "2026-08-03-1000"])
            shutil.rmtree(root / "2026-08-01-1000" / "runs")
            removed = prune(root, keep=1)
            self.assertEqual([p.parent.name for p in removed], ["2026-08-02-1000"])
            self.assertTrue((root / "2026-08-01-1000" / "_final" / "r1.mp4").is_file())

    def test_bo_qua_symlink_latest(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _batches(root, ["2026-08-01-1000", "2026-08-02-1000"])
            (root / "latest").symlink_to("2026-08-02-1000")
            removed = prune(root, keep=1)
            self.assertEqual([p.parent.name for p in removed], ["2026-08-01-1000"])


def _goi_main(argv: list[str], root: Path):
    """Gọi batch_clean.main() với ROOT patch vào tempdir.

    BẮT BUỘC patch ROOT: main() thật dùng ROOT/"out" (đường dẫn repo thật) — không
    patch thì test này sẽ dọn dẹp (tức là XOÁ) thư mục out/ thật của repo.
    """
    out = io.StringIO()
    err = io.StringIO()
    with mock.patch.object(batch_clean, "ROOT", root), \
         contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = batch_clean.main(argv)
    return code, out.getvalue(), err.getvalue()


class TestMainCli(unittest.TestCase):
    """main() của batch_clean.py — trước bản sửa này 22/45 dòng, phần thiếu chính là
    đường CLI thật (parse --keep, đường mặc định, thông điệp lỗi)."""

    def test_mac_dinh_giu_3_lo_gan_nhat(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            names = [f"2026-08-0{i}-1000" for i in range(1, 6)]   # 5 lô
            _batches(root / "out", names)
            code, out, _err = _goi_main([], root)
            self.assertEqual(code, 0)
            for old in names[:2]:
                self.assertFalse((root / "out" / old / "runs").exists())
                self.assertIn(old, out)
            for kept in names[2:]:
                self.assertTrue((root / "out" / kept / "runs").exists())

    def test_keep_1_chi_giu_lo_gan_nhat(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            names = ["2026-08-01-1000", "2026-08-02-1000", "2026-08-03-1000"]
            _batches(root / "out", names)
            code, _out, _err = _goi_main(["--keep", "1"], root)
            self.assertEqual(code, 0)
            self.assertFalse((root / "out" / names[0] / "runs").exists())
            self.assertFalse((root / "out" / names[1] / "runs").exists())
            self.assertTrue((root / "out" / names[2] / "runs").exists())

    def test_dry_run_liet_ke_nhung_khong_xoa_gi(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            names = ["2026-08-01-1000", "2026-08-02-1000"]
            _batches(root / "out", names)
            code, out, _err = _goi_main(["--keep", "1", "--dry-run"], root)
            self.assertEqual(code, 0)
            self.assertIn("sẽ xoá", out)
            self.assertNotIn("đã xoá", out)
            # Đây là assertion đắt nhất của test này: DRY=1 không được đụng gì tới đĩa.
            self.assertTrue((root / "out" / names[0] / "runs").exists())
            self.assertTrue((root / "out" / names[0] / "runs" / "r1" / "01-motion.mp4").is_file())

    def test_keep_khong_phai_so_nguyen_thi_chan_va_khong_dung_gi(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            names = ["2026-08-01-1000", "2026-08-02-1000"]
            _batches(root / "out", names)
            code, _out, err = _goi_main(["--keep", "abc"], root)
            self.assertEqual(code, 1)
            self.assertIn("không phải số nguyên", err)
            for n in names:
                self.assertTrue((root / "out" / n / "runs").exists())

    def test_keep_0_bi_chan_qua_cli(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            names = ["2026-08-01-1000"]
            _batches(root / "out", names)
            code, _out, err = _goi_main(["--keep", "0"], root)
            self.assertEqual(code, 1)
            self.assertIn("≥ 1", err)
            self.assertTrue((root / "out" / names[0] / "runs").exists())

    def test_khong_co_gi_de_don_khi_it_hon_keep(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _batches(root / "out", ["2026-08-01-1000"])
            code, out, _err = _goi_main([], root)
            self.assertEqual(code, 0)
            self.assertIn("Không có gì để dọn", out)

    def test_out_khong_ton_tai_thi_khong_loi(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            code, out, _err = _goi_main([], root)
            self.assertEqual(code, 0)
            self.assertIn("Không có gì để dọn", out)

    def test_final_khong_bao_gio_bi_dung_toi_qua_cli(self):
        # Bất biến quan trọng nhất của cả script: bất kể KEEP bao nhiêu, _final/ của
        # MỌI lô (kể cả lô vừa bị prune) phải còn nguyên.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            names = ["2026-08-01-1000", "2026-08-02-1000", "2026-08-03-1000"]
            _batches(root / "out", names)
            _goi_main(["--keep", "1"], root)
            for n in names:
                self.assertTrue((root / "out" / n / "_final" / "r1.mp4").is_file())


if __name__ == "__main__":
    unittest.main()
