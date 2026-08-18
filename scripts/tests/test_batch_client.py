import json, sys, tempfile, threading, unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.client import (JobError, download_output, encode_multipart, health_ok,
                             poll_job, submit_job)
from batchlib.config import Settings

STATE = {"poll_calls": 0, "statuses": [], "last_post": None, "output": b"", "health": 200,
         "download_status": 200, "garbage_polls": 0, "always_garbage": False}


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
                     download_status=200, garbage_polls=0, always_garbage=False)


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
        STATE["statuses"] = [{"status": "error", "error": "ComfyUI 400 node type not found"}]
        with self.assertRaises(JobError) as cm:
            poll_job(self.settings, "job-1", timeout_min=5, sleep=lambda _s: None)
        self.assertIn("node type not found", str(cm.exception))

    def test_cancelled_cung_la_loi(self):
        STATE["statuses"] = [{"status": "cancelled"}]
        with self.assertRaises(JobError):
            poll_job(self.settings, "job-1", timeout_min=5, sleep=lambda _s: None)

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
