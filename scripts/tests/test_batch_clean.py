import sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
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

    def test_keep_0_bi_chan(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                prune(Path(d), keep=0)

    def test_bo_qua_symlink_latest(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _batches(root, ["2026-08-01-1000", "2026-08-02-1000"])
            (root / "latest").symlink_to("2026-08-02-1000")
            removed = prune(root, keep=1)
            self.assertEqual([p.parent.name for p in removed], ["2026-08-01-1000"])


if __name__ == "__main__":
    unittest.main()
