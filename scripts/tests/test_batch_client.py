import itertools
import json, sys, tempfile, threading, unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.client import (JobError, JobFailed, JobGone, download_output, encode_multipart,
                            health_ok, poll_job, submit_job)
from batchlib.config import Settings

STATE = {"poll_calls": 0, "statuses": [], "last_post": None, "output": b"", "health": 200,
         "download_status": 200, "garbage_polls": 0, "always_garbage": False,
         "poll_status": 200, "poll_body": b""}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_a):  # im lặng trong test
        pass

    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self.send_response(STATE["health"]); self.send_header("content-length", "0"); self.end_headers()
        elif self.path.endswith("/download"):
            code = STATE["download_status"]
            body = STATE["output"]
            self.send_response(code)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif STATE["poll_status"] != 200:
            # GET /jobs/<id> trả mã lỗi thật (404 pod dựng lại, 401 key đổi, 500 API ngã).
            STATE["poll_calls"] += 1
            body = STATE["poll_body"]
            self.send_response(STATE["poll_status"])
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif STATE["always_garbage"] or STATE["poll_calls"] < STATE["garbage_polls"]:
            # Giả lập rớt mạng / trả lời không phải JSON — để test nhánh except của poll_job.
            STATE["poll_calls"] += 1
            body = b"khong-phai-json"
            self.send_response(200)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            i = min(STATE["poll_calls"] - STATE["garbage_polls"], len(STATE["statuses"]) - 1)
            STATE["poll_calls"] += 1
            self._json(200, STATE["statuses"][i])

    def do_POST(self):
        raw = self.rfile.read(int(self.headers["content-length"]))
        STATE["last_post"] = (self.headers.get("content-type", ""), raw,
                              self.headers.get("x-api-key", ""))
        self._json(202, {"id": "job-1", "status": "queued"})


class ServerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        host, port = cls.server.server_address
        cls.settings = Settings(domain=f"{host}:{port}", api_key="mk_test", instance_id="i-1")
        # Server giả không có TLS nên base_url phải là http://. Đây là vá ở cấp LỚP, nên
        # PHẢI khôi phục ở tearDownClass: `unittest discover` chạy mọi module trong CÙNG
        # một tiến trình, và một base_url rò rỉ biến cả suite thành phụ thuộc thứ tự chạy.
        cls._base_url_goc = Settings.base_url
        Settings.base_url = property(lambda s: f"http://{s.domain}")

    @classmethod
    def tearDownClass(cls):
        Settings.base_url = cls._base_url_goc
        cls.server.shutdown()
        cls.server.server_close()  # đóng hẳn socket lắng nghe, tránh ResourceWarning rò rỉ

    def setUp(self):
        STATE.update(poll_calls=0, statuses=[], last_post=None, output=b"", health=200,
                     download_status=200, garbage_polls=0, always_garbage=False,
                     poll_status=200, poll_body=b"")


class TestMultipart(unittest.TestCase):
    def test_giu_nguyen_byte_nhi_phan(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.bin"
            blob = bytes(range(256)) * 4
            p.write_bytes(blob)
            body, ctype = encode_multipart({"type": "motion"}, {"ref": p})
            self.assertIn("multipart/form-data; boundary=", ctype)
            self.assertIn(blob, body)
            self.assertIn(b'name="type"', body)
            self.assertIn(b'name="ref"; filename="a.bin"', body)
            boundary = ctype.split("boundary=")[1].encode()
            self.assertTrue(body.rstrip().endswith(b"--" + boundary + b"--"))

    def test_boundary_khong_dung_lai_giua_hai_lan(self):
        b1 = encode_multipart({"a": "1"}, {})[1]
        b2 = encode_multipart({"a": "1"}, {})[1]
        self.assertNotEqual(b1, b2)


class TestHealth(ServerCase):
    def test_200_la_up(self):
        self.assertTrue(health_ok(self.settings))

    def test_503_la_down(self):
        STATE["health"] = 503
        self.assertFalse(health_ok(self.settings))


class TestSubmit(ServerCase):
    def test_gui_api_key_va_params_json_roi_tra_id(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "ref.jpg"
            p.write_bytes(b"x" * 10)
            job_id = submit_job(self.settings, "motion", {"quality": "540p"}, {"ref": p})
            self.assertEqual(job_id, "job-1")
            ctype, raw, key = STATE["last_post"]
            self.assertEqual(key, "mk_test")
            self.assertIn(b'name="type"', raw)
            self.assertIn(b"motion", raw)
            self.assertIn(b'{"quality": "540p"}', raw)


class TestPoll(ServerCase):
    def test_chay_toi_khi_done(self):
        STATE["statuses"] = [{"status": "queued", "progress": 0},
                             {"status": "running", "progress": 0.4},
                             {"status": "done", "progress": 1}]
        seen = []
        result = poll_job(self.settings, "job-1", timeout_min=5,
                          on_progress=lambda d: seen.append(d["status"]), sleep=lambda _s: None)
        self.assertEqual(result["status"], "done")
        self.assertIn("running", seen)

    def test_error_thi_nem_kem_ly_do_cua_worker(self):
        # JobFailed, KHÔNG phải JobError trần: --resume dựa vào đúng lớp này để biết
        # "job đã chạy và hỏng thật → gửi lại là đúng", khác hẳn "quá hạn → job vẫn
        # đang chạy, gửi lại là trả tiền GPU hai lần" (runner._try_reattach).
        STATE["statuses"] = [{"status": "error", "error": "ComfyUI 400 node type not found"}]
        with self.assertRaises(JobFailed) as cm:
            poll_job(self.settings, "job-1", timeout_min=5, sleep=lambda _s: None)
        self.assertIn("node type not found", str(cm.exception))

    def test_cancelled_cung_la_loi(self):
        STATE["statuses"] = [{"status": "cancelled"}]
        with self.assertRaises(JobFailed):
            poll_job(self.settings, "job-1", timeout_min=5, sleep=lambda _s: None)

    def test_404_chet_ngay_va_phan_biet_duoc_voi_dang_chay(self):
        # Trước bản sửa: mọi mã != 200 thành `data = {}` -> status "" -> "vẫn đang chạy",
        # nên một 404 vĩnh viễn (pod dựng lại, hàng job mất) in "None 0%" mỗi 10 giây
        # cho tới hết timeout của chặng — enhance là 90 PHÚT pod tính tiền.
        # Đồng hồ giả có trần (deadline = 0+60) để một regression không treo test 60 phút.
        STATE["poll_status"] = 404
        STATE["poll_body"] = b'{"error":"Khong tim thay"}'
        clock = itertools.count(0, 10)
        with self.assertRaises(JobGone) as cm:
            poll_job(self.settings, "job-1", timeout_min=1, sleep=lambda _s: None,
                     now=lambda: next(clock))
        msg = str(cm.exception)
        self.assertIn("404", msg)
        self.assertIn("Khong tim thay", msg)   # thân trả lời của server phải đi kèm
        self.assertIn("RESUME=1", msg)         # và phải nói làm gì tiếp
        self.assertEqual(STATE["poll_calls"], 1)   # đúng MỘT lần, không poll tới hết hạn

    def test_401_chet_ngay_nhung_khong_phai_JobGone(self):
        # 401 (key đã đổi khi dựng lại pod, auth.js:7) cũng vô vọng nếu poll tiếp, nhưng
        # KHÔNG được gộp vào JobGone: JobGone nghĩa là "gửi job mới đi", mà gửi mới với
        # key sai thì cũng 401 — chỉ tốn thêm một round-trip và làm mờ lý do thật.
        STATE["poll_status"] = 401
        STATE["poll_body"] = b'{"error":"Sai hoac thieu X-API-Key"}'
        clock = itertools.count(0, 10)
        with self.assertRaises(JobError) as cm:
            poll_job(self.settings, "job-1", timeout_min=1, sleep=lambda _s: None,
                     now=lambda: next(clock))
        self.assertNotIsInstance(cm.exception, JobGone)
        msg = str(cm.exception)
        self.assertIn("401", msg)
        self.assertIn("Sai hoac thieu X-API-Key", msg)
        self.assertIn(".env", msg)
        self.assertEqual(STATE["poll_calls"], 1)

    def test_500_lien_tuc_dung_o_tran_thu_lai_chu_khong_cham_deadline(self):
        # 500 KHÔNG chết ngay (API restart được), nhưng phải TÍNH vào misses. Trước bản
        # sửa nó reset misses về 0 nên vòng lặp chạy tới deadline. Đồng hồ giả bước 10s,
        # timeout_min=60 -> deadline 3600 -> 360 vòng nếu misses bị reset; 31 vòng nếu
        # đếm đúng. Cả hai assertion dưới đây đổi giá trị theo đúng cái khác biệt đó.
        STATE["poll_status"] = 500
        STATE["poll_body"] = b"upstream ngat"
        clock = itertools.count(0, 10)
        with self.assertRaises(JobError) as cm:
            poll_job(self.settings, "job-1", timeout_min=60, sleep=lambda _s: None,
                     now=lambda: next(clock))
        msg = str(cm.exception)
        self.assertIn("mất liên lạc", msg)
        self.assertIn("500", msg)
        self.assertIn("upstream ngat", msg)
        self.assertEqual(STATE["poll_calls"], 31)

    def test_qua_han_thi_nem_kem_job_id_de_resume(self):
        # deadline = now()(=0) + 60. Đồng hồ cho phép 3 vòng lặp thật (10, 20, 30 < 60)
        # rồi mới vượt hạn (9999) — khác bản cũ, nơi giá trị thứ hai (10_000) đã vượt
        # hạn ngay từ vòng đầu nên while không chạy thân vòng lặp lần nào, sleep()
        # không được gọi và server không hề bị polling — không chứng minh được gì
        # về hành vi SAU khi đã polling thật.
        STATE["statuses"] = [{"status": "running", "progress": 0.5}]
        clock = iter([0, 10, 20, 30, 9_999])
        sleep_calls = []
        with self.assertRaises(JobError) as cm:
            poll_job(self.settings, "job-1", timeout_min=1,
                     sleep=lambda s: sleep_calls.append(s), now=lambda: next(clock))
        self.assertIn("job-1", str(cm.exception))
        self.assertEqual(len(sleep_calls), 3)
        # Quá hạn KHÔNG phải JobFailed/JobGone: job vẫn đang chạy trên pod. Gộp nó vào
        # hai lớp kia làm runner._try_reattach gửi job mới đè lên một job 39 phút còn
        # sống — đúng khoản tiền GPU mà cả cơ chế resume tồn tại để không phải trả lần hai.
        self.assertNotIsInstance(cm.exception, (JobFailed, JobGone))

    def test_hoi_phuc_sau_vai_lan_roi_moi_ket_noi_lai(self):
        # Vài lần poll đầu trả về rác (không phải JSON) — mô phỏng rớt mạng thoáng qua.
        # poll_job KHÔNG được coi đó là job hỏng: job vẫn đang chạy thật trên pod.
        STATE["garbage_polls"] = 2
        STATE["statuses"] = [{"status": "done", "progress": 1}]
        result = poll_job(self.settings, "job-1", timeout_min=5, sleep=lambda _s: None)
        self.assertEqual(result["status"], "done")

    def test_rot_mang_lien_tuc_bi_chan_boi_tran_thu_lai(self):
        # Server luôn trả rác — số lần thử lại phải có TRẦN (misses > 30), không phải
        # vòng lặp vô hạn chỉ dừng khi hết thời gian chờ (timeout_min=60 thật lớn để
        # chứng minh chính trần thử lại, chứ không phải deadline, là thứ chặn vòng lặp).
        STATE["always_garbage"] = True
        with self.assertRaises(JobError) as cm:
            poll_job(self.settings, "job-1", timeout_min=60, sleep=lambda _s: None)
        msg = str(cm.exception)
        self.assertIn("RESUME=1", msg)
        self.assertEqual(STATE["poll_calls"], 31)


class TestPollRotMangThat(unittest.TestCase):
    """Hai nhánh URLError/OSError của cùng tuple except — vòng review trước chỉ phủ
    ValueError (JSON hỏng) và để trống hai nhánh này.

    Không dùng server giả: rớt Wi-Fi / socket đứt không tái tạo được bằng HTTP 200,
    nên patch thẳng _request để nó ném đúng exception đó. Đây là nhánh quyết định
    "job đang chạy trên pod có bị giết oan vì mạng của máy local hay không".
    """

    settings = Settings(domain="x.test", api_key="mk_test", instance_id="i-1")

    @staticmethod
    def _scripted(items):
        calls = []

        def fake(*_a, **_k):
            item = items[min(len(calls), len(items) - 1)]
            calls.append(item)
            if isinstance(item, Exception):
                raise item
            return item
        return fake, calls

    def test_urlerror_chi_la_miss_roi_poll_lai_duoc(self):
        fake, calls = self._scripted([urllib.error.URLError("mang rot"),
                                      urllib.error.URLError("mang rot"),
                                      (200, b'{"status":"done","progress":1}')])
        with mock.patch("batchlib.client._request", fake):
            result = poll_job(self.settings, "job-1", timeout_min=5, sleep=lambda _s: None)
        self.assertEqual(result["status"], "done")
        self.assertEqual(len(calls), 3)   # hai lần rớt rồi lần thứ ba mới có kết quả

    def test_oserror_chi_la_miss_roi_poll_lai_duoc(self):
        fake, calls = self._scripted([OSError("socket dut"),
                                      (200, b'{"status":"done","progress":1}')])
        with mock.patch("batchlib.client._request", fake):
            result = poll_job(self.settings, "job-1", timeout_min=5, sleep=lambda _s: None)
        self.assertEqual(result["status"], "done")
        self.assertEqual(len(calls), 2)

    def test_urlerror_lien_tuc_van_dung_o_tran_thu_lai(self):
        fake, calls = self._scripted([urllib.error.URLError("mang rot hoan toan")])
        clock = itertools.count(0, 10)
        with mock.patch("batchlib.client._request", fake):
            with self.assertRaises(JobError) as cm:
                poll_job(self.settings, "job-1", timeout_min=60, sleep=lambda _s: None,
                         now=lambda: next(clock))
        msg = str(cm.exception)
        self.assertIn("RESUME=1", msg)
        self.assertIn("mang rot hoan toan", msg)   # lý do cuối phải đi kèm
        self.assertEqual(len(calls), 31)           # trần 30 lần miss, không phải deadline


class TestDownload(ServerCase):
    def test_tai_ve_va_tra_so_byte(self):
        STATE["output"] = b"v" * 200_000
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "out.mp4"
            self.assertEqual(download_output(self.settings, "job-1", dest, 100_000), 200_000)
            self.assertEqual(dest.stat().st_size, 200_000)

    def test_duoi_san_thi_nem_va_xoa_file_rac(self):
        # "job báo done nhưng MinIO trả về gần rỗng" — đúng bẫy pod-smoke.sh dựng sàn để bắt.
        STATE["output"] = b"v" * 10
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "out.mp4"
            with self.assertRaises(JobError) as cm:
                download_output(self.settings, "job-1", dest, 100_000)
            self.assertIn("10", str(cm.exception))
            self.assertFalse(dest.exists())

    def test_khong_200_thi_giu_lai_ly_do_cua_server(self):
        # Chỉ assert mã lỗi thì test này pass ngay cả TRƯỚC khi sửa Finding A — phải
        # khoá cả đoạn thân trả lời từ server, vì 404 "chưa có output" và 404/500
        # "lỗi khác" là hai vấn đề khác nhau, đòi hỏi hai bước tiếp theo khác nhau.
        STATE["download_status"] = 404
        STATE["output"] = b"Chua co output cho job nay"
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "out.mp4"
            with self.assertRaises(JobError) as cm:
                download_output(self.settings, "job-1", dest, 100)
            msg = str(cm.exception)
            self.assertIn("404", msg)
            self.assertIn("Chua co output cho job nay", msg)
            self.assertFalse(dest.exists())


if __name__ == "__main__":
    unittest.main()
