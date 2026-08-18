import json, re, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.client import JobError, JobFailed, JobGone
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


MANIFEST_MOT_RUN_CO_PARAM = """
runs:
  - id: runA
    pipeline: motion-enhance
    inputs:
      character: char.jpg
      driver: drv.mp4
    motion: { frames: 33 }
"""


def _fixture(tmp: Path, text: str = MANIFEST) -> Path:
    (tmp / "char.jpg").write_bytes(b"x")
    (tmp / "drv.mp4").write_bytes(b"x")
    p = tmp / "b.yaml"
    p.write_text(text, encoding="utf-8")
    return p


class FakePod:
    """Pod giả có HÀNG JOB thật, không phải một hàm "poll gì cũng done".

    Bắt buộc phải như vậy từ khi --resume BẮT LẠI job cũ bằng job_id
    (runner._try_reattach): một pod giả luôn trả done sẽ nói dối đúng chỗ đắt nhất —
    nó làm job_id do pod của LƯỢT TRƯỚC cấp trông như đang chạy trên pod này.

      - job id pod này chưa từng cấp và không có trong `known` -> JobGone (404), giống
        pod vừa dựng lại: DB mới, hàng job cũ mất sạch.
      - `fail_on`  -> JobFailed: job đã CHẠY và hỏng thật. Kiểm trước `known`/`issued`
        để diễn được cả "job cũ của lượt trước đã hỏng".
      - `known`    -> job pod này còn nhớ từ lượt trước: nhánh bắt lại THÀNH CÔNG.
      - `hang_on`  -> JobError trần (quá hạn): job VẪN đang chạy. Đây là nhánh runner
        KHÔNG được phép gửi job mới.
    """

    def __init__(self, fail_on: set[str] | None = None, known: set[str] | None = None,
                 hang_on: set[str] | None = None):
        self.fail_on = set(fail_on or ())
        self.known = set(known or ())
        self.hang_on = set(hang_on or ())
        self.submitted: list[tuple[str, dict, dict]] = []
        self.issued: list[str] = []
        self.polled: list[str] = []
        self.params_by_id: dict[str, dict] = {}

    def submit(self, _s, job_type, params, files):
        self.submitted.append((job_type, params, {k: v.name for k, v in files.items()}))
        job_id = f"job-{len(self.submitted)}"
        self.issued.append(job_id)
        self.params_by_id[job_id] = dict(params or {})
        return job_id

    def _stored(self, job_id: str) -> dict:
        # API NẮN params trước khi ghi DB: normalizeMotionDriverSegment ép
        # renderProfile/steps (jobs.js:33-46) và jobs.js:110-113 ép detailUpscale=false.
        # Pod giả phải nắn y hệt, nếu không thì test "run.json ghi param THẬT" không
        # phân biệt được param của manifest với param đã lưu — nó sẽ xanh cả khi runner
        # ghi lại nguyên manifest.
        return {**self.params_by_id.get(job_id, {}),
                "renderProfile": "fast", "steps": 4, "detailUpscale": False}

    def poll(self, _s, job_id, *_a, **_k):
        self.polled.append(job_id)
        if job_id in self.hang_on:
            raise JobError(f"job {job_id} chưa xong sau 60 phút. Nó VẪN đang chạy trên pod")
        if job_id in self.fail_on:
            raise JobFailed(f"job {job_id} error: pod giả cố ý hỏng")
        if job_id not in self.issued and job_id not in self.known:
            raise JobGone(f"job {job_id} không còn trên pod (404)")
        return {"status": "done", "progress": 1, "params": self._stored(job_id)}

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
        # params_sent phải là param API ĐÃ GHI VÀO DB (đọc từ DTO của GET /jobs/<id>),
        # không phải param trong manifest. Khoá bằng detailUpscale + renderProfile: hai
        # key KHÔNG có trong manifest, chỉ API mới thêm/ép (jobs.js:33-46 và :110-113).
        # Nếu runner quay về ghi `run.stage_params[...]` thì hai key đó biến mất -> đỏ.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = _fixture(tmp, text=MANIFEST_MOT_RUN_CO_PARAM)
            _run(tmp, FakePod(), manifest_path=p)
            data = json.loads((tmp / "out" / "2026-08-18-1430" / "runs" / "runA" / "run.json")
                              .read_text(encoding="utf-8"))
            motion = data["stages"]["motion"]
            self.assertIn("job_id", motion)
            self.assertEqual(motion["params_sent"]["detailUpscale"], False)
            self.assertEqual(motion["params_sent"]["renderProfile"], "fast")
            self.assertEqual(motion["params_sent"]["frames"], 33)
            # …và param của manifest vẫn còn, nhưng ở khoá KHÁC: "xin gì" vs "được gì".
            self.assertEqual(motion["params_manifest"], {"frames": 33})

    def test_index_tsv_cot_params_sent_cho_param_da_luu(self):
        # Cột params_sent của _index.tsv phải chở param THẬT. Trước bản sửa nó chở
        # nguyên manifest, nên bảng tra là tiếng vọng của chính manifest.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = _fixture(tmp, text=MANIFEST_MOT_RUN_CO_PARAM)
            _run(tmp, FakePod(), manifest_path=p)
            lines = (tmp / "out" / "2026-08-18-1430" / "_index.tsv").read_text(
                encoding="utf-8").strip().splitlines()
            header = lines[0].split("\t")
            rows = [line.split("\t") for line in lines[1:]]
            motion_row = next(r for r in rows if r[2] == "motion")
            sent = json.loads(motion_row[header.index("params_sent")])
            self.assertEqual(sent["detailUpscale"], False)
            self.assertEqual(json.loads(motion_row[header.index("params_manifest")]),
                             {"frames": 33})


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


def _seed_journal(manifest_path: Path, *, job_id: str = "job-cu", status: str = "running",
                  stage: str = "motion", run_status: str = "running") -> None:
    """Journal của một lượt đã đứt giữa chặng: chặng `stage` có job_id, chưa done.

    Đúng thứ còn lại trên đĩa sau timeout / rớt mạng / Ctrl-C giữa lô. Không có file
    output nào trong out/ — job còn đang chạy trên pod thì chưa có gì để tải về.
    """
    save_state(state_path_for(manifest_path), {
        "version": 1, "batch": "2026-08-18-1430",
        "runs": {"runA": {"status": run_status, "stages": {
            stage: {"job_id": job_id, "status": status, "params_manifest": {}},
        }}},
    })


class TestResumeBatLaiJobCu(unittest.TestCase):
    """--resume phải BẮT LẠI job còn đang chạy, không gửi job mới (spec §8).

    Trước bản sửa, cửa duy nhất là `status == "done" and dest.is_file()`, nên một chặng
    ghi "running"/"error" (trạng thái sau timeout, rớt mạng, Ctrl-C) rơi thẳng xuống
    submit_job — trong khi ba thông báo đã shipped lại hứa ngược lại và đẩy người dùng
    vào đúng RESUME=1. Một enhance 40 phút đứt ở phút 39 tốn 40 phút GPU lần thứ hai.
    """

    def test_bat_lai_duoc_job_dang_chay_thi_khong_gui_job_moi(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = _fixture(tmp, text=MANIFEST_MOT_RUN)
            _seed_journal(p)
            pod = FakePod(known={"job-cu"})   # pod vẫn nhớ job cũ và nó đã xong
            result = _run(tmp, pod, manifest_path=p, resume=True)

            self.assertIn("job-cu", pod.polled)                     # có thử bắt lại
            self.assertEqual([c[0] for c in pod.submitted], ["enhance"])  # motion KHÔNG gửi lại
            out = tmp / "out" / "2026-08-18-1430"
            self.assertTrue((out / "runs" / "runA" / "01-motion.mp4").is_file())
            state = load_state(state_path_for(p))
            motion = state["runs"]["runA"]["stages"]["motion"]
            self.assertEqual(motion["job_id"], "job-cu")   # vẫn là job CŨ, không phải job mới
            self.assertEqual(motion["status"], "done")
            self.assertEqual(result.done, ["runA"])

    def test_log_noi_ro_da_bat_lai_duoc_job_nao(self):
        # Bắt lại là kết cục TỐT và tốn tiền của người dùng — họ phải thấy nó xảy ra,
        # không được im lặng như thể chặng đó tự nhiên xong.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = _fixture(tmp, text=MANIFEST_MOT_RUN)
            _seed_journal(p)
            lines: list[str] = []
            _run(tmp, FakePod(known={"job-cu"}), manifest_path=p, resume=True,
                 log=lines.append)
            self.assertTrue(any("BẮT LẠI được job cũ job-cu" in line for line in lines),
                            f"không có dòng nào nói đã bắt lại: {lines}")

    def test_job_cu_khong_con_tren_pod_thi_gui_job_moi(self):
        # 404 = pod đã dựng lại, hàng job mất (jobs.js:154). Bắt lại là vô nghĩa, và
        # KHÔNG được biến thành lỗi chết: gửi job mới mới là việc đúng.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = _fixture(tmp, text=MANIFEST_MOT_RUN)
            _seed_journal(p)
            pod = FakePod()   # pod mới toanh, không biết job-cu
            result = _run(tmp, pod, manifest_path=p, resume=True)
            self.assertIn("job-cu", pod.polled)
            self.assertEqual([c[0] for c in pod.submitted], ["motion", "enhance"])
            self.assertEqual(result.done, ["runA"])

    def test_job_cu_hong_that_thi_gui_job_moi(self):
        # JobFailed = job đã chạy và hỏng thật -> chạy lại là đúng.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = _fixture(tmp, text=MANIFEST_MOT_RUN)
            _seed_journal(p, status="error")
            pod = FakePod(known={"job-cu"}, fail_on={"job-cu"})
            result = _run(tmp, pod, manifest_path=p, resume=True)
            self.assertIn("job-cu", pod.polled)
            self.assertEqual([c[0] for c in pod.submitted], ["motion", "enhance"])
            self.assertEqual(result.done, ["runA"])

    def test_job_cu_qua_han_thi_KHONG_duoc_gui_job_moi(self):
        # Assertion đắt nhất của cả file: "quá hạn" nghĩa là job VẪN đang chạy trên pod.
        # Gửi job mới ở đây là trả tiền GPU hai lần — đúng cái mà cơ chế resume tồn tại
        # để tránh. Nên lô phải HỎNG, không được submit gì cả.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = _fixture(tmp, text=MANIFEST_MOT_RUN)
            _seed_journal(p)
            pod = FakePod(known={"job-cu"}, hang_on={"job-cu"})
            result = _run(tmp, pod, manifest_path=p, resume=True)
            self.assertEqual(pod.submitted, [])
            self.assertIn("runA", result.failed)
            self.assertIn("VẪN đang chạy", result.failed["runA"])
            # job_id phải CÒN trong journal để lần RESUME=1 sau còn bắt lại được lần nữa.
            motion = load_state(state_path_for(p))["runs"]["runA"]["stages"]["motion"]
            self.assertEqual(motion["job_id"], "job-cu")
            self.assertEqual(motion["status"], "error")

    def test_khong_resume_thi_khong_bat_lai_job_cu(self):
        # Không có --resume thì journal cũ không được ảnh hưởng gì: người dùng đang cố ý
        # chạy lại từ đầu.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = _fixture(tmp, text=MANIFEST_MOT_RUN)
            _seed_journal(p)
            pod = FakePod(known={"job-cu"})
            _run(tmp, pod, manifest_path=p, resume=False)
            self.assertNotIn("job-cu", pod.polled)
            self.assertEqual([c[0] for c in pod.submitted], ["motion", "enhance"])


class TestRunLog(unittest.TestCase):
    """spec §6 đòi runs/<id>/run.log, và trước bản sửa grep cả scripts/ không ra chữ nào.

    Quan trọng hơn một file thiếu bình thường: tool chạy 30-60 phút không người trông,
    stdout là bản ghi DUY NHẤT, đóng terminal là mất.
    """

    def test_run_log_co_moi_dong_stdout_va_stdout_khong_doi(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            lines: list[str] = []
            # Một run duy nhất: mọi dòng thụt lề đều thuộc về nó, không lẫn của run khác.
            _run(tmp, FakePod(), manifest_path=_fixture(tmp, text=MANIFEST_MOT_RUN),
                 log=lines.append)
            content = (tmp / "out" / "2026-08-18-1430" / "runs" / "runA" / "run.log").read_text(
                encoding="utf-8")
            # Dòng tiêu đề "[1/1] runA · …" là của run_batch (mức LÔ), không thuộc run.log
            # của một run; các dòng thụt lề mới là của run_one.
            cua_runA = [line for line in lines if line.startswith("    ")]
            self.assertEqual(len(cua_runA), 4)   # gửi/xong × motion/enhance
            for line in cua_runA:
                self.assertIn(line, content)      # tee thiếu dòng nào -> đỏ
            # stdout PHẢI y nguyên: dấu thời gian chỉ có trong file. Thêm nó vào stdout
            # là đổi thứ duy nhất người dùng nhìn suốt một lô 40 phút.
            self.assertFalse([line for line in lines if re.match(r"^\d{4}-\d{2}-\d{2}", line)])
            self.assertTrue(re.search(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} ", content,
                                      re.MULTILINE))

    def test_run_log_giu_ly_do_hong(self):
        # Dòng cần nhất của một lô hỏng: vì sao nó hỏng. run_batch in nó ra stdout —
        # tức là mất khi đóng terminal — nên run_one phải ghi thẳng nó xuống run.log.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _run(tmp, FakePod(fail_on={"job-1"}))   # job-1 = chặng motion của runA
            content = (tmp / "out" / "2026-08-18-1430" / "runs" / "runA" / "run.log").read_text(
                encoding="utf-8")
            self.assertIn("✗ motion", content)
            self.assertIn("pod giả cố ý hỏng", content)

    def test_run_log_ghi_them_chu_khong_de_len_khi_resume(self):
        # Lượt trước chính là thứ cần đọc để hiểu vì sao phải resume — mở bằng "w" là
        # xoá đúng nó.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = _fixture(tmp, text=MANIFEST_MOT_RUN)
            _run(tmp, FakePod(fail_on={"job-2"}), manifest_path=p)   # enhance hỏng
            _run(tmp, FakePod(), manifest_path=p, resume=True)
            content = (tmp / "out" / "2026-08-18-1430" / "runs" / "runA" / "run.log").read_text(
                encoding="utf-8")
            self.assertEqual(content.count("=== runA"), 2)
            self.assertIn("pod giả cố ý hỏng", content)   # lý do của lượt ĐẦU còn nguyên
            self.assertIn("RESUME=1", content)            # và lượt thứ hai tự khai là resume

    def test_default_log_van_la_print(self):
        # Ràng buộc cố ý: default print() là dấu hiệu "còn sống" duy nhất suốt một lô dài.
        # Test suite câm được là vì mọi test tự truyền log riêng, KHÔNG vì runner đổi default.
        import inspect
        self.assertIs(inspect.signature(run_batch).parameters["log"].default, print)


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
