import sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.scan import ScanError, build_runs, collect, slugify


def _materials(tmp: Path, chars=2, outfits=0, bgs=0, drivers=2) -> Path:
    root = tmp / "materials"
    for name, count, ext in (("characters", chars, ".jpg"), ("outfits", outfits, ".jpg"),
                             ("backgrounds", bgs, ".jpg"), ("drivers", drivers, ".mp4")):
        d = root / name
        d.mkdir(parents=True, exist_ok=True)
        for i in range(count):
            (d / f"{name[:-1]}-{i}{ext}").write_bytes(b"x")
    return root


class TestSlugify(unittest.TestCase):
    def test_bo_dau_cach_va_ky_tu_la(self):
        self.assertEqual(slugify("Vay Đỏ (2).jpg"), "vay-do-2-jpg")

    def test_gop_dau_noi_lien_tiep_va_cat_hai_dau(self):
        self.assertEqual(slugify("--a__  b--"), "a-b")

    def test_rong_thi_tra_chuoi_thay_the_chu_khong_tra_rong(self):
        self.assertEqual(slugify("!!!"), "x")


class TestCollect(unittest.TestCase):
    def test_gom_theo_thu_muc_vai_tro_va_sap_xep_on_dinh(self):
        with tempfile.TemporaryDirectory() as d:
            found = collect(_materials(Path(d), chars=3, drivers=2))
            self.assertEqual(len(found["character"]), 3)
            self.assertEqual([p.name for p in found["character"]], sorted(p.name for p in found["character"]))
            self.assertEqual(found["outfit"], [])

    def test_bo_qua_file_an_va_thu_muc_con(self):
        with tempfile.TemporaryDirectory() as d:
            root = _materials(Path(d))
            (root / "characters" / ".DS_Store").write_bytes(b"x")
            (root / "characters" / "sub").mkdir()
            self.assertEqual(len(collect(root)["character"]), 2)

    def test_thu_muc_khong_ton_tai_bao_ro(self):
        with self.assertRaises(ScanError) as cm:
            collect(Path("/khong/co/that"))
        self.assertIn("characters", str(cm.exception))

    def test_khong_co_character_hoac_driver_thi_bao(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ScanError) as cm:
                collect(_materials(Path(d), chars=0, drivers=2))
            self.assertIn("characters", str(cm.exception))


class TestBuildRuns(unittest.TestCase):
    def test_pair_dung_o_ngan_ngan_nhat(self):
        with tempfile.TemporaryDirectory() as d:
            runs = build_runs(collect(_materials(Path(d), chars=3, outfits=2, drivers=2)), "pair")
            self.assertEqual(len(runs), 2)
            self.assertEqual(runs[0].pipeline, "tryon-motion-enhance")

    def test_pair_khong_outfit_thi_pipeline_ngan(self):
        with tempfile.TemporaryDirectory() as d:
            runs = build_runs(collect(_materials(Path(d), chars=2, drivers=2)), "pair")
            self.assertEqual([r.pipeline for r in runs], ["motion-enhance"] * 2)
            self.assertNotIn("outfit", runs[0].inputs)

    def test_cross_la_tich_descartes_khong_tinh_background(self):
        with tempfile.TemporaryDirectory() as d:
            runs = build_runs(collect(_materials(Path(d), chars=3, outfits=2, bgs=1, drivers=2)), "cross")
            self.assertEqual(len(runs), 12)

    def test_background_xoay_vong_khong_gioi_han_so_run(self):
        # Một file nền dùng chung KHÔNG được bóp lô 12 run xuống 1 run.
        with tempfile.TemporaryDirectory() as d:
            runs = build_runs(collect(_materials(Path(d), chars=3, outfits=2, bgs=1, drivers=2)), "pair")
            self.assertEqual(len(runs), 2)
            self.assertTrue(all("background" in r.inputs for r in runs))

    def test_khong_co_background_thi_khong_co_khoa_do(self):
        with tempfile.TemporaryDirectory() as d:
            runs = build_runs(collect(_materials(Path(d), chars=2, outfits=2, drivers=2)), "pair")
            self.assertNotIn("background", runs[0].inputs)

    def test_id_sinh_tu_ten_material(self):
        with tempfile.TemporaryDirectory() as d:
            runs = build_runs(collect(_materials(Path(d), chars=2, outfits=2, drivers=2)), "pair")
            self.assertEqual(runs[0].id, "character-0__outfit-0__driver-0")

    def test_id_trung_thi_them_hau_to(self):
        with tempfile.TemporaryDirectory() as d:
            root = _materials(Path(d), chars=0, drivers=1)
            for name in ("a.jpg", "a.png"):     # cùng stem -> cùng slug
                (root / "characters" / name).write_bytes(b"x")
            (root / "drivers" / "drv.mp4").write_bytes(b"x")
            runs = build_runs(collect(root), "cross")
            self.assertEqual(len({r.id for r in runs}), len(runs))
            self.assertTrue(any(r.id.endswith("-2") for r in runs))

    def test_mode_la_bi_chan(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ScanError):
                build_runs(collect(_materials(Path(d))), "khong-co")

    def test_ket_qua_on_dinh_giua_hai_lan_quet(self):
        with tempfile.TemporaryDirectory() as d:
            found = collect(_materials(Path(d), chars=3, outfits=2, drivers=2))
            self.assertEqual([r.id for r in build_runs(found, "cross")],
                             [r.id for r in build_runs(found, "cross")])


if __name__ == "__main__":
    unittest.main()
