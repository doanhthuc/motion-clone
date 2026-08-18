import json, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.client import JobError
from batchlib.config import Settings
from batchlib.manifest import load_manifest, load_state, save_state, state_path_for
from batchlib.runner import batch_id_now, run_batch, write_index

SETTINGS = Settings(domain="x.test", api_key="mk_test", instance_id="i-1")

MANIFEST = """
runs:
  - id: runA
    pipeline: motion-enhance
    inputs:
      character: char.jpg
      driver: drv.mp4
  - id: runB
    pipeline: motion-enhance
    inputs:
      character: char.jpg
      driver: drv.mp4
"""


MANIFEST_MOT_RUN = """
runs:
  - id: runA
    pipeline: motion-enhance
    inputs:
      character: char.jpg
      driver: drv.mp4
"""


def _fixture(tmp: Path, text: str = MANIFEST) -> Path:
    (tmp / "char.jpg").write_bytes(b"x")
    (tmp / "drv.mp4").write_bytes(b"x")
    p = tmp / "b.yaml"
    p.write_text(text, encoding="utf-8")
    return p


class FakePod:
    """Pod giả: mỗi job xong ngay, output là byte đủ lớn."""

    def __init__(self, fail_on: set[str] | None = None):
        self.fail_on = fail_on or set()
        self.submitted: list[tuple[str, dict, dict]] = []

    def submit(self, _s, job_type, params, files):
        self.submitted.append((job_type, params, {k: v.name for k, v in files.items()}))
        return f"job-{len(self.submitted)}"

    def poll(self, _s, job_id, *_a, **_k):
        if job_id in self.fail_on:
            raise JobError(f"job {job_id} error: pod giả cố ý hỏng")
        return {"status": "done", "progress": 1}

    def download(self, _s, _job_id, dest: Path, _min_bytes):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"v" * 200_000)
        return 200_000


def _run(tmp: Path, pod: FakePod, **kwargs):
    manifest_path = kwargs.pop("manifest_path", None) or _fixture(tmp)
    # log mặc định của run_batch/run_one là print() — đúng cho production (đó là thứ
    # duy nhất người dùng thấy suốt một lô 40 phút), nhưng làm ngợp stdout của test
    # suite. Test không assert trên log nào, nên câm nó ở đây — chỉ ở đây, KHÔNG đổi
    # default trong runner.py.
    kwargs.setdefault("log", lambda _msg: None)
    with mock.patch("batchlib.runner.submit_job", pod.submit), \
         mock.patch("batchlib.runner.poll_job", pod.poll), \
         mock.patch("batchlib.runner.download_output", pod.download):
        return run_batch(
            settings=SETTINGS,
            manifest=load_manifest(manifest_path),
            out_root=tmp / "out",
            batch_id="2026-08-18-1430",
            **kwargs,
        )


class TestBatchId(unittest.TestCase):
    def test_dinh_dang_sap_xep_duoc_theo_thu_tu_chu(self):
        import datetime
        self.assertEqual(batch_id_now(datetime.datetime(2026, 8, 18, 14, 30)), "2026-08-18-1430")


class TestChayThanhCong(unittest.TestCase):
    def test_chay_het_va_de_ra_bo_cuc_dung(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            result = _run(tmp, FakePod())
            out = tmp / "out" / "2026-08-18-1430"
            self.assertEqual(result.done, ["runA", "runB"])
            self.assertEqual(result.failed, {})
            self.assertTrue((out / "_final" / "runA.mp4").is_file())
            self.assertTrue((out / "_final" / "runB.mp4").is_file())
            self.assertTrue((out / "_index.tsv").is_file())
            self.assertTrue((out / "manifest.yaml").is_file())
            self.assertTrue((out / "runs" / "runA" / "01-motion.mp4").is_file())
            self.assertTrue((out / "runs" / "runA" / "02-enhance.mp4").is_file())
            self.assertTrue((out / "runs" / "runA" / "run.json").is_file())

    def test_final_la_hardlink_khong_ton_them_dia(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _run(tmp, FakePod())
            out = tmp / "out" / "2026-08-18-1430"
            self.assertEqual((out / "_final" / "runA.mp4").stat().st_ino,
                             (out / "runs" / "runA" / "02-enhance.mp4").stat().st_ino)

    def test_output_chang_truoc_thanh_input_chang_sau(self):
        with tempfile.TemporaryDirectory() as d:
            pod = FakePod()
            _run(Path(d), pod)
            enhance_calls = [c for c in pod.submitted if c[0] == "enhance"]
            self.assertTrue(enhance_calls)
            self.assertEqual(list(enhance_calls[0][2]), ["input"])
            self.assertTrue(enhance_calls[0][2]["input"].startswith("01-motion"))

    def test_manifest_goc_khong_bi_sua(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = _fixture(tmp)
            before = p.read_text(encoding="utf-8")
            _run(tmp, FakePod(), manifest_path=p)
            self.assertEqual(p.read_text(encoding="utf-8"), before)

    def test_run_json_ghi_param_that_da_gui(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _run(tmp, FakePod())
            data = json.loads((tmp / "out" / "2026-08-18-1430" / "runs" / "runA" / "run.json")
                              .read_text(encoding="utf-8"))
            self.assertIn("motion", data["stages"])
            self.assertIn("params_sent", data["stages"]["motion"])
            self.assertIn("job_id", data["stages"]["motion"])


class TestHong(unittest.TestCase):
    def test_mot_run_hong_khong_giet_ca_lo(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            result = _run(tmp, FakePod(fail_on={"job-1"}))
            self.assertEqual(result.done, ["runB"])
            self.assertIn("runA", result.failed)

    def test_fail_fast_thi_dung_ngay(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            result = _run(tmp, FakePod(fail_on={"job-1"}), fail_fast=True)
            self.assertEqual(result.done, [])
            self.assertEqual(list(result.failed), ["runA"])

    def test_hong_van_ghi_state_de_resume(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = _fixture(tmp)
            _run(tmp, FakePod(fail_on={"job-1"}), manifest_path=p)
            state = load_state(state_path_for(p))
            self.assertEqual(state["runs"]["runA"]["status"], "error")
            self.assertEqual(state["runs"]["runB"]["status"], "done")


class TestResume(unittest.TestCase):
    def test_resume_bo_qua_run_da_done(self):
        # Cả hai run đã "done" ở mức RUN — đây là skip NGOÀI (run_batch's outer loop),
        # run_one không hề được gọi. Test này KHÔNG chứng minh gì về skip TRONG (per-
        # stage, bên trong run_one) — xem test_resume_bo_qua_chang_da_xong_o_run_chua_done
        # cho việc đó.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = _fixture(tmp)
            _run(tmp, FakePod(), manifest_path=p)
            pod2 = FakePod()
            _run(tmp, pod2, manifest_path=p, resume=True)
            self.assertEqual(pod2.submitted, [])

    def test_resume_chi_chay_lai_chang_con_thieu(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = _fixture(tmp)
            _run(tmp, FakePod(fail_on={"job-2"}), manifest_path=p)   # motion xong, enhance hỏng
            pod2 = FakePod()
            _run(tmp, pod2, manifest_path=p, resume=True)
            # assertTrue(all(...)) một mình là vacuously true trên list rỗng — một mutation
            # bỏ qua luôn cả chặng enhance đang hỏng (tức không resume gì hết) vẫn qua được.
            # Chốt thêm: đúng MỘT job phải được gửi lại (enhance của runA).
            self.assertEqual(len(pod2.submitted), 1)
            self.assertTrue(all(c[0] == "enhance" for c in pod2.submitted),
                            f"chỉ được chạy lại enhance, nhưng chạy: {[c[0] for c in pod2.submitted]}")

    def test_resume_bo_qua_chang_da_xong_o_run_chua_done(self):
        # Cô lập skip TRONG (per-stage, trong run_one) khỏi skip NGOÀI (run-level):
        # manifest chỉ có MỘT run, nên không có run nào khác để "ẩn nấp" đằng sau —
        # nếu skip trong bị gãy thì motion (đã xong, file còn nguyên) sẽ bị gửi lại,
        # lộ ngay trong danh sách submit của pod2 mà không có run thứ hai che khuất.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = _fixture(tmp, text=MANIFEST_MOT_RUN)
            _run(tmp, FakePod(fail_on={"job-2"}), manifest_path=p)   # motion xong, enhance hỏng
            state = load_state(state_path_for(p))
            self.assertEqual(state["runs"]["runA"]["status"], "error")  # KHÔNG "done" -> không bị skip ngoài che
            pod2 = FakePod()
            _run(tmp, pod2, manifest_path=p, resume=True)
            self.assertEqual([c[0] for c in pod2.submitted], ["enhance"])

    def test_resume_chang_done_trong_journal_nhung_mat_file_thi_chay_lai(self):
        # Điều kiện skip trong là HAI vế: journal nói "done" VÀ file còn trên đĩa
        # (runner.py: `resume and recorded.get("status") == "done" and dest.is_file()`).
        # Test này khoá riêng vế thứ hai: xoá file chặng motion khỏi đĩa (mô phỏng
        # make batch-clean lỡ tay, hay đĩa hỏng) trong khi journal vẫn nói "done" —
        # chặng đó PHẢI chạy lại, không được tin journal suông.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = _fixture(tmp)
            _run(tmp, FakePod(), manifest_path=p)   # cả hai run xong xuôi

            out = tmp / "out" / "2026-08-18-1430"
            (out / "runs" / "runA" / "01-motion.mp4").unlink()

            state_file = state_path_for(p)
            state = load_state(state_file)
            # Đặt lại status run về khác "done" để không bị skip NGOÀI che — mô phỏng
            # đúng tình huống thật: batch coi run là dở (vd sau một crash), không phải
            # coi cả run là xong trong khi file đã mất.
            state["runs"]["runA"]["status"] = "running"
            save_state(state_file, state)

            pod2 = FakePod()
            _run(tmp, pod2, manifest_path=p, resume=True)
            self.assertEqual([c[0] for c in pod2.submitted], ["motion"])

    def test_khong_resume_thi_chay_lai_tu_dau(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = _fixture(tmp)
            _run(tmp, FakePod(), manifest_path=p)
            pod2 = FakePod()
            _run(tmp, pod2, manifest_path=p, resume=False)
            self.assertEqual(len(pod2.submitted), 4)


class TestIndex(unittest.TestCase):
    def test_index_tsv_co_header_va_mot_dong_moi_chang(self):
        # Một dòng mỗi CHẶNG chứ không phải mỗi run: 2 run x 2 chặng (motion, enhance)
        # = 4 dòng + header. Đếm dòng không thôi thì một bộ 4 dòng SAI (vd 2 run x 2
        # dòng rác nào đó) vẫn qua được test — nên còn phải kiểm cả hai tên chặng
        # thật sự xuất hiện dưới CÙNG một run id.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _run(tmp, FakePod())
            lines = (tmp / "out" / "2026-08-18-1430" / "_index.tsv").read_text(
                encoding="utf-8").strip().splitlines()
            self.assertTrue(lines[0].startswith("run\t"))
            self.assertEqual(len(lines), 5)
            rows = [line.split("\t") for line in lines[1:]]
            runA_stages = {r[2] for r in rows if r[0] == "runA"}
            self.assertEqual(runA_stages, {"motion", "enhance"})

    def test_index_tsv_giu_dong_chang_hong_cua_run_loi(self):
        # Run lỗi vẫn phải để lại đúng dòng của chặng đã làm nó dừng — đây là cách
        # người đọc _index.tsv biết run hỏng ở chặng nào mà không cần mở run.json.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _run(tmp, FakePod(fail_on={"job-1"}))  # job-1 = chặng motion của runA
            lines = (tmp / "out" / "2026-08-18-1430" / "_index.tsv").read_text(
                encoding="utf-8").strip().splitlines()
            rows = [line.split("\t") for line in lines[1:]]
            runA_rows = [r for r in rows if r[0] == "runA"]
            self.assertEqual([r[2] for r in runA_rows], ["motion"])
            self.assertEqual(runA_rows[0][1], "error")


if __name__ == "__main__":
    unittest.main()
