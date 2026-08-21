import base64
import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib import local_tryon as lt
from batchlib.client import JobError

GEMINI_STATE = {"mode": "image", "calls": 0}


class GeminiHandler(BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def do_POST(self):
        GEMINI_STATE["calls"] += 1
        length = int(self.headers["content-length"])
        self.rfile.read(length)
        mode = GEMINI_STATE["mode"]
        if mode == "http_error":
            self.send_response(400)
            body = b'{"error":"bad request"}'
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if mode == "malformed_json":
            # Return 200 with non-JSON body — tests robustness of error handling
            self.send_response(200)
            body = b"not json at all"
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if mode == "no_candidates":
            payload = {"candidates": []}
        elif mode == "image":
            fake_png = base64.b64encode(b"fake-png-bytes").decode()
            payload = {"candidates": [{"content": {"parts": [
                {"inlineData": {"mimeType": "image/png", "data": fake_png}}]}}]}
        elif mode == "text":
            payload = {"candidates": [{"content": {"parts": [
                {"text": GEMINI_STATE.get("text_reply", "A cat in a garden.")}]}}]}
        else:
            raise AssertionError(f"mode không rõ: {mode}")
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class GeminiServerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), GeminiHandler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        host, port = cls.server.server_address
        cls.base_url = f"http://{host}:{port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        GEMINI_STATE["mode"] = "image"
        GEMINI_STATE["calls"] = 0


class TestGeminiEdit(GeminiServerCase):
    def test_thanh_cong_ghi_ra_file(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out.png"
            result = lt.gemini_edit([(b"model-bytes", "image/jpeg")], "edit prompt", "AIzafake",
                                    out, base_url=self.base_url)
            self.assertEqual(result, out)
            self.assertEqual(out.read_bytes(), b"fake-png-bytes")

    def test_http_loi_raise_joberror(self):
        GEMINI_STATE["mode"] = "http_error"
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(JobError) as cm:
                lt.gemini_edit([(b"x", "image/png")], "p", "AIzafake", Path(d) / "o.png",
                               base_url=self.base_url)
            self.assertIn("400", str(cm.exception))

    def test_khong_tra_anh_raise_joberror(self):
        GEMINI_STATE["mode"] = "no_candidates"
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(JobError):
                lt.gemini_edit([(b"x", "image/png")], "p", "AIzafake", Path(d) / "o.png",
                               base_url=self.base_url)


class TestTranslateVnToEn(GeminiServerCase):
    def test_khong_co_dau_tieng_viet_thi_khong_goi_mang(self):
        out = lt.translate_vn_to_en("keep the hat", "AIzafake", base_url=self.base_url)
        self.assertEqual(out, "keep the hat")
        self.assertEqual(GEMINI_STATE["calls"], 0)

    def test_rong_thi_khong_goi_mang(self):
        out = lt.translate_vn_to_en("", "AIzafake", base_url=self.base_url)
        self.assertEqual(out, "")
        self.assertEqual(GEMINI_STATE["calls"], 0)

    def test_co_dau_tieng_viet_thi_dich(self):
        GEMINI_STATE["mode"] = "text"
        GEMINI_STATE["text_reply"] = "keep the necklace"
        out = lt.translate_vn_to_en("giữ nguyên vòng cổ", "AIzafake", base_url=self.base_url)
        self.assertEqual(out, "keep the necklace")
        self.assertEqual(GEMINI_STATE["calls"], 1)

    def test_loi_mang_thi_tra_nguyen_van_khong_raise(self):
        GEMINI_STATE["mode"] = "http_error"
        out = lt.translate_vn_to_en("giữ nguyên vòng cổ", "AIzafake", base_url=self.base_url)
        self.assertEqual(out, "giữ nguyên vòng cổ")

    def test_malformed_json_tra_nguyen_van_khong_raise(self):
        # Regression test for Critical issue: 200 response with non-JSON body must
        # NOT raise json.JSONDecodeError; translate_vn_to_en contract is "never raise"
        GEMINI_STATE["mode"] = "malformed_json"
        out = lt.translate_vn_to_en("giữ nguyên vòng cổ", "AIzafake", base_url=self.base_url)
        self.assertEqual(out, "giữ nguyên vòng cổ")


class TestIsLocalProvider(unittest.TestCase):
    def test_gemini_la_local(self):
        self.assertTrue(lt.is_local_provider("gemini"))
        self.assertTrue(lt.is_local_provider("Gemini"))
        self.assertTrue(lt.is_local_provider("  gemini  "))

    def test_qwen_khong_phai_local(self):
        self.assertFalse(lt.is_local_provider("qwen"))
        self.assertFalse(lt.is_local_provider(""))
        self.assertFalse(lt.is_local_provider(None))

    def test_qwen_max_chua_lam_nen_khong_phai_local(self):
        # Interface đã định nghĩa (§3 spec) nhưng chưa implement — is_local_provider
        # phải trả False cho tới khi thật sự thêm "qwen-max" vào LOCAL_PROVIDERS.
        self.assertFalse(lt.is_local_provider("qwen-max"))


class TestGeminiAspect(unittest.TestCase):
    def test_none_dims_tra_none(self):
        self.assertIsNone(lt.gemini_aspect(None))
        self.assertIsNone(lt.gemini_aspect((0, 100)))

    def test_doc_gan_9_16(self):
        self.assertEqual(lt.gemini_aspect((1080, 1920)), "9:16")

    def test_ngang_gan_16_9(self):
        self.assertEqual(lt.gemini_aspect((1920, 1080)), "16:9")

    def test_vuong(self):
        self.assertEqual(lt.gemini_aspect((1000, 1000)), "1:1")


class TestValidGeminiKey(unittest.TestCase):
    def test_key_dung_dinh_dang(self):
        self.assertTrue(lt.valid_gemini_key("AIza" + "x" * 35))

    def test_key_rong_hoac_sai_tien_to(self):
        self.assertFalse(lt.valid_gemini_key(""))
        self.assertFalse(lt.valid_gemini_key("sk-" + "x" * 35))

    def test_key_co_khoang_trang_bi_tu_choi(self):
        self.assertFalse(lt.valid_gemini_key("AIza xyz" + "x" * 30))


class TestMimeOf(unittest.TestCase):
    def test_cac_duoi_biet(self):
        self.assertEqual(lt.mime_of(Path("a.png")), "image/png")
        self.assertEqual(lt.mime_of(Path("a.JPG")), "image/jpeg")
        self.assertEqual(lt.mime_of(Path("a.webp")), "image/webp")

    def test_duoi_la_thi_fallback_jpeg(self):
        self.assertEqual(lt.mime_of(Path("a.bmp")), "image/jpeg")


class TestTryonExtraClause(unittest.TestCase):
    def test_rong_tra_rong(self):
        self.assertEqual(lt.tryon_extra_clause(""), "")
        self.assertEqual(lt.tryon_extra_clause(None), "")

    def test_co_noi_dung_thi_boc_cau_uu_tien_cao(self):
        out = lt.tryon_extra_clause("keep the hat")
        self.assertIn("ADDITIONAL USER INSTRUCTION", out)
        self.assertIn("keep the hat", out)


class TestGeminiTryonPrompt(unittest.TestCase):
    def test_auto_khong_bia_do(self):
        p = lt.gemini_tryon_prompt("auto")
        self.assertIn("fashion product photo", p)
        self.assertIn("Do not invent items", p)

    def test_shoes_rieng(self):
        p = lt.gemini_tryon_prompt("shoes")
        self.assertIn("footwear", p)
        self.assertIn("BOTH", p)

    def test_mot_mon_le_dung_label(self):
        p = lt.gemini_tryon_prompt("upper")
        self.assertIn("top or shirt", p)

    def test_luon_khoa_mat(self):
        # Mọi nhánh đều phải có câu khoá mặt đứng ĐẦU (linux.py:3538-3543)
        for gt in ("auto", "shoes", "upper", "set"):
            self.assertTrue(lt.gemini_tryon_prompt(gt).startswith("CRITICAL: the person's face"))

    def test_extra_duoc_chen_vao_cuoi(self):
        p = lt.gemini_tryon_prompt("auto", extra="keep the necklace")
        self.assertIn("keep the necklace", p)
        self.assertLess(p.index("ADDITIONAL USER INSTRUCTION"), len(p))


if __name__ == "__main__":
    unittest.main()
