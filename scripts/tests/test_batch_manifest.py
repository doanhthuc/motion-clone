import json, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.manifest import (ManifestError, dump_runs, load_manifest, load_state,
                               save_state, state_path_for, validate_manifest)
from batchlib.params import extract_from_ast, load_curated

REPO = Path(__file__).resolve().parents[2]
AST = extract_from_ast(REPO / "motions-studio" / "worker" / "worker_runtime" / "linux.py")
CURATED = load_curated(REPO / "scripts" / "batch-params.json")

GOOD = """
defaults:
  enhance: { targetRes: 1080p, fpsInterp: "60" }
runs:
  - id: mauA
    pipeline: tryon-motion-enhance
    inputs:
      character: char.jpg
      outfit: vay.jpg
      background: bg.jpg
      driver: drv.mp4
    motion: { preset: drv-30s }
  - id: mauB
    pipeline: motion-enhance
    inputs:
      character: char.jpg
      driver: drv.mp4
    enhance: { fpsInterp: "48" }
"""


def _fixture(tmp: Path, text: str = GOOD) -> Path:
    for name in ("char.jpg", "vay.jpg", "bg.jpg", "drv.mp4"):
        (tmp / name).write_bytes(b"x")
    p = tmp / "b.yaml"
    p.write_text(text, encoding="utf-8")
    return p


class TestLoad(unittest.TestCase):
    def test_doc_duoc_run_va_giai_duong_dan_tuong_doi(self):
        with tempfile.TemporaryDirectory() as d:
            m = load_manifest(_fixture(Path(d)))
            self.assertEqual([r.id for r in m.runs], ["mauA", "mauB"])
            self.assertTrue(m.runs[0].inputs["character"].is_absolute())
            self.assertTrue(m.runs[0].inputs["character"].is_file())

    def test_defaults_duoc_ap_va_run_ghi_de_duoc(self):
        with tempfile.TemporaryDirectory() as d:
            m = load_manifest(_fixture(Path(d)))
            self.assertEqual(m.runs[0].stage_params["enhance"]["fpsInterp"], "60")
            self.assertEqual(m.runs[0].stage_params["enhance"]["targetRes"], "1080p")
            self.assertEqual(m.runs[1].stage_params["enhance"]["fpsInterp"], "48")
            self.assertEqual(m.runs[1].stage_params["enhance"]["targetRes"], "1080p")

    def test_defaults_khong_lan_sang_pipeline_khong_chay_chang_do(self):
        # defaults cho tryon KHÔNG được dính vào run motion-enhance, nếu không validate
        # sẽ báo sai "có param cho chặng tryon" cho một mặc định hoàn toàn vô hại.
        with tempfile.TemporaryDirectory() as d:
            text = GOOD.replace("defaults:\n", "defaults:\n  tryon: { provider: qwen }\n")
            m = load_manifest(_fixture(Path(d), text))
            self.assertIn("tryon", m.runs[0].stage_params)          # tryon-motion-enhance
            self.assertNotIn("tryon", m.runs[1].stage_params)       # motion-enhance
            self.assertEqual(validate_manifest(m, ast_params=AST, curated=CURATED), [])

    def test_param_ghi_thang_cho_chang_khong_chay_van_bi_bat(self):
        # Khác với defaults: viết thẳng `tryon:` vào một run motion-enhance là lỗi cố ý,
        # và im lặng bỏ qua nó nghĩa là người dùng tưởng mình đã chỉnh được cái gì đó.
        with tempfile.TemporaryDirectory() as d:
            text = GOOD.replace("    enhance: { fpsInterp: \"48\" }",
                                "    enhance: { fpsInterp: \"48\" }\n    tryon: { provider: qwen }")
            errs = validate_manifest(load_manifest(_fixture(Path(d), text)),
                                     ast_params=AST, curated=CURATED)
            self.assertTrue(any("tryon" in e for e in errs))

    def test_trung_id_bi_chan(self):
        with tempfile.TemporaryDirectory() as d:
            text = GOOD.replace("id: mauB", "id: mauA")
            with self.assertRaises(ManifestError) as cm:
                load_manifest(_fixture(Path(d), text))
            self.assertIn("mauA", str(cm.exception))

    def test_khong_co_runs_bi_chan(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "b.yaml"
            p.write_text("defaults: {}\n", encoding="utf-8")
            with self.assertRaises(ManifestError):
                load_manifest(p)


class TestValidate(unittest.TestCase):
    def test_manifest_tot_thi_khong_loi(self):
        with tempfile.TemporaryDirectory() as d:
            m = load_manifest(_fixture(Path(d)))
            self.assertEqual(validate_manifest(m, ast_params=AST, curated=CURATED), [])

    def test_thieu_file_bi_bat_truoc_khi_ton_gpu(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = _fixture(tmp)
            (tmp / "drv.mp4").unlink()
            errs = validate_manifest(load_manifest(p), ast_params=AST, curated=CURATED)
            self.assertTrue(any("drv.mp4" in e for e in errs))

    def test_thieu_material_bat_buoc_bi_bat(self):
        with tempfile.TemporaryDirectory() as d:
            text = GOOD.replace("      outfit: vay.jpg\n", "")
            errs = validate_manifest(load_manifest(_fixture(Path(d), text)),
                                     ast_params=AST, curated=CURATED)
            self.assertTrue(any("outfit" in e for e in errs))

    def test_param_la_bi_bat(self):
        with tempfile.TemporaryDirectory() as d:
            text = GOOD.replace('fpsInterp: "48"', 'fpsinterp: "48"')
            errs = validate_manifest(load_manifest(_fixture(Path(d), text)),
                                     ast_params=AST, curated=CURATED)
            self.assertTrue(any("fpsInterp" in e for e in errs))

    def test_pipeline_la_bi_bat(self):
        with tempfile.TemporaryDirectory() as d:
            text = GOOD.replace("pipeline: motion-enhance", "pipeline: khong-co")
            errs = validate_manifest(load_manifest(_fixture(Path(d), text)),
                                     ast_params=AST, curated=CURATED)
            self.assertTrue(any("khong-co" in e for e in errs))

    def test_gom_HET_loi_chu_khong_dung_o_cai_dau_tien(self):
        with tempfile.TemporaryDirectory() as d:
            text = GOOD.replace('fpsInterp: "48"', 'fpsinterp: "48"') \
                       .replace("pipeline: tryon-motion-enhance", "pipeline: khong-co")
            errs = validate_manifest(load_manifest(_fixture(Path(d), text)),
                                     ast_params=AST, curated=CURATED)
            self.assertGreaterEqual(len(errs), 2)


class TestState(unittest.TestCase):
    def test_duong_dan_state_nam_canh_manifest(self):
        self.assertEqual(state_path_for(Path("/x/batch/a.yaml")), Path("/x/batch/a.state.json"))

    def test_chua_co_state_thi_tra_rong_khong_no(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(load_state(Path(d) / "chua-co.json"), {"version": 1, "runs": {}})

    def test_ghi_roi_doc_lai_khop(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            state = {"version": 1, "batch": "2026-08-18-1430",
                     "runs": {"mauA": {"status": "done", "stages": {
                         "motion": {"job_id": "j1", "status": "done",
                                    "elapsed_sec": 310, "file": "out/x/02-motion.mp4"}}}}}
            save_state(p, state)
            self.assertEqual(load_state(p), state)

    def test_ghi_la_atomic_khong_de_lai_file_tam(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            save_state(p, {"version": 1, "runs": {}})
            self.assertEqual([f.name for f in Path(d).iterdir()], ["s.json"])

    def test_state_hong_khong_giet_lo_dang_chay(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            p.write_text("{ khong phai json", encoding="utf-8")
            self.assertEqual(load_state(p), {"version": 1, "runs": {}})


class TestDumpRuns(unittest.TestCase):
    def test_dump_roi_load_lai_ra_dung_the(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            m = load_manifest(_fixture(tmp))
            out = tmp / "again.yaml"
            out.write_text(dump_runs(m.runs), encoding="utf-8")
            again = load_manifest(out)
            self.assertEqual([r.id for r in again.runs], ["mauA", "mauB"])
            self.assertEqual(again.runs[1].stage_params["enhance"]["fpsInterp"], "48")


if __name__ == "__main__":
    unittest.main()
