import base64
import json
import math
import sys
import tempfile
import threading
import time
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
        call_idx = GEMINI_STATE["calls"] - 1  # 0-based index for this request
        length = int(self.headers["content-length"])
        body_bytes = self.rfile.read(length)
        GEMINI_STATE.setdefault("bodies", []).append(json.loads(body_bytes))
        # Support per-call modes via "modes" dict (key = 0-based call index)
        # for tests that need different response types on different calls
        mode = GEMINI_STATE.get("modes", {}).get(call_idx) or GEMINI_STATE["mode"]
        if mode == "http_error":
            self.send_response(400)
            body = b'{"error":"bad request"}'
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if mode == "hang":
            # Không trả lời gì cả, chỉ ngủ rồi đóng — mô phỏng Gemini treo: client hết giờ
            # đọc response. KHÔNG ghi byte nào sau khi ngủ: client đã bỏ đi, ghi vào đó là
            # BrokenPipeError trong thread server → socketserver in traceback bẩn cả suite.
            time.sleep(GEMINI_STATE.get("hang_sec", 0.6))
            self.close_connection = True
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
        cls.server.server_close()  # đóng hẳn socket lắng nghe, tránh ResourceWarning rò rỉ

    def setUp(self):
        GEMINI_STATE["mode"] = "image"
        GEMINI_STATE["calls"] = 0
        GEMINI_STATE.pop("modes", None)
        GEMINI_STATE.pop("text_reply", None)
        GEMINI_STATE.pop("hang_sec", None)
        GEMINI_STATE.pop("bodies", None)


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

    def test_image_size_gui_trong_imageconfig(self):
        # 16/09/2026: không truyền imageSize thì gemini-3-pro-image tự mặc định 1K (~1MP) bất kể
        # ảnh gốc to đến đâu — đây là field bị thiếu, không phải bug crop đã sửa 4aca8c9.
        with tempfile.TemporaryDirectory() as d:
            lt.gemini_edit([(b"x", "image/png")], "p", "AIzafake", Path(d) / "o.png",
                           aspect_ratio="9:16", image_size="2K", base_url=self.base_url)
        self.assertEqual(GEMINI_STATE["bodies"][-1]["generationConfig"]["imageConfig"],
                         {"aspectRatio": "9:16", "imageSize": "2K"})

    def test_image_size_omitted_when_not_passed(self):
        with tempfile.TemporaryDirectory() as d:
            lt.gemini_edit([(b"x", "image/png")], "p", "AIzafake", Path(d) / "o.png",
                           aspect_ratio="9:16", base_url=self.base_url)
        self.assertEqual(GEMINI_STATE["bodies"][-1]["generationConfig"]["imageConfig"],
                         {"aspectRatio": "9:16"})


class TestPostJsonLoiMang(GeminiServerCase):
    """urllib CHỈ bọc lỗi socket thành URLError ở khâu GỬI request.

    Hết giờ khi ĐỌC response ném TimeoutError trần — không phải URLError. Trước bản sửa,
    `_post_json` chỉ bắt URLError nên nó bay thẳng qua run_local_tryon, qua
    run_local_phase._one() (chỉ bắt JobError) và giết CẢ Pha A vì một run hết giờ.
    """

    def test_het_gio_doc_response_raise_joberror_khong_phai_timeouterror(self):
        GEMINI_STATE["mode"] = "hang"
        GEMINI_STATE["hang_sec"] = 0.6
        with self.assertRaises(JobError) as cm:
            lt._post_json(f"{self.base_url}/v1beta/models/m:generateContent",
                          {"key": "AIzafake"}, {"contents": []}, timeout=0.15)
        self.assertIn("không phản hồi", str(cm.exception))

    def test_oserror_bat_ky_thanh_joberror(self):
        # Phòng thủ theo LỚP lỗi, không theo từng loại: ConnectionResetError, socket drop,
        # DNS hỏng… đều là OSError. Bắt đúng lớp cha là bắt hết một lần.
        with mock.patch("urllib.request.urlopen",
                        side_effect=ConnectionResetError("connection reset by peer")):
            with self.assertRaises(JobError):
                lt._post_json("http://127.0.0.1:1/x", {"key": "k"}, {}, timeout=1)

    def test_gemini_edit_het_gio_cung_ra_joberror(self):
        # Cùng lỗ hổng nhìn từ điểm vào thật sự của Pha A, không chỉ hàm nội bộ.
        with tempfile.TemporaryDirectory() as d:
            with mock.patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
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


DASHSCOPE_STATE = {"mode": "image", "calls": 0}


class DashscopeHandler(BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def do_POST(self):
        DASHSCOPE_STATE["calls"] += 1
        length = int(self.headers["content-length"])
        self.rfile.read(length)
        mode = DASHSCOPE_STATE["mode"]
        if mode == "http_error":
            self.send_response(400)
            body = b'{"code":"InvalidApiKey","message":"bad key"}'
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if mode == "no_image":
            payload = {"output": {"choices": [{"message": {"content": []}}]}}
        else:
            payload = {"output": {"choices": [{"message": {"content": [
                {"image": f"{self.server.base_url}/img.png"}]}}]}}
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/img.png":
            body = b"fake-qwen-png-bytes"
            self.send_response(200)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()


class DashscopeServerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), DashscopeHandler)
        host, port = cls.server.server_address
        cls.base_url = f"http://{host}:{port}"
        cls.server.base_url = cls.base_url  # đọc lại trong handler để build URL ảnh trả về
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        DASHSCOPE_STATE["mode"] = "image"
        DASHSCOPE_STATE["calls"] = 0


class TestQwenImageUrl(unittest.TestCase):
    def test_thieu_workspace_raise_joberror(self):
        with mock.patch.object(lt, "QWEN_IMAGE_WORKSPACE", ""), \
             mock.patch.object(lt, "QWEN_IMAGE_BASE", ""):
            with self.assertRaises(JobError):
                lt._qwen_image_url()

    def test_co_workspace_dung_pattern_maas(self):
        with mock.patch.object(lt, "QWEN_IMAGE_WORKSPACE", "ws-123"), \
             mock.patch.object(lt, "QWEN_IMAGE_REGION", "ap-southeast-1"), \
             mock.patch.object(lt, "QWEN_IMAGE_BASE", ""):
            url = lt._qwen_image_url()
        self.assertEqual(url, "https://ws-123.ap-southeast-1.maas.aliyuncs.com"
                              "/api/v1/services/aigc/multimodal-generation/generation")

    def test_base_override_thang_dung_khong_can_workspace(self):
        with mock.patch.object(lt, "QWEN_IMAGE_WORKSPACE", ""), \
             mock.patch.object(lt, "QWEN_IMAGE_BASE", "https://custom.example.com/"):
            url = lt._qwen_image_url()
        self.assertEqual(url, "https://custom.example.com/api/v1/services/aigc/multimodal-generation/generation")


class TestQwenMaxConfigured(unittest.TestCase):
    """Whether a Qwen retry can work at all. The bot offers the button only when
    this says yes, so it has to read the same places the call itself does.

    The .env fallback is the point: the VPS's motion-bot.service has no
    EnvironmentFile, so a QWEN_IMAGE_WORKSPACE written to .env (as bot.py's
    own comment instructs) never reached os.environ, and the Qwen call raised
    "cần QWEN_IMAGE_WORKSPACE" with the value sitting right there in the file.
    """

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self._env = mock.patch.dict(lt.os.environ, {}, clear=False)
        self._env.start()
        self.addCleanup(self._env.stop)
        for name in ("DASHSCOPE_API_KEY", "QWEN_IMAGE_WORKSPACE", "QWEN_IMAGE_BASE"):
            lt.os.environ.pop(name, None)

    def _dotenv(self, text):
        (self.root / ".env").write_text(text, encoding="utf-8")

    def test_key_and_workspace_in_dotenv_is_configured(self):
        self._dotenv("DASHSCOPE_API_KEY=sk-x\nQWEN_IMAGE_WORKSPACE=ws-1\n")
        self.assertTrue(lt.qwen_max_configured(self.root))

    def test_base_instead_of_workspace_is_configured(self):
        self._dotenv("DASHSCOPE_API_KEY=sk-x\nQWEN_IMAGE_BASE=https://q.example\n")
        self.assertTrue(lt.qwen_max_configured(self.root))

    def test_missing_key_is_not_configured(self):
        self._dotenv("QWEN_IMAGE_WORKSPACE=ws-1\n")
        self.assertFalse(lt.qwen_max_configured(self.root))

    def test_missing_workspace_is_not_configured(self):
        self._dotenv("DASHSCOPE_API_KEY=sk-x\n")
        self.assertFalse(lt.qwen_max_configured(self.root))

    def test_process_env_wins_over_dotenv(self):
        self._dotenv("DASHSCOPE_API_KEY=sk-x\n")
        lt.os.environ["QWEN_IMAGE_WORKSPACE"] = "ws-env"
        self.assertEqual(lt.qwen_setting("QWEN_IMAGE_WORKSPACE", self.root), "ws-env")
        self.assertTrue(lt.qwen_max_configured(self.root))


class TestQwenMaxEdit(DashscopeServerCase):
    def test_thanh_cong_ghi_ra_file(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out.png"
            with mock.patch.object(lt, "QWEN_IMAGE_BASE", self.base_url):
                result = lt.qwen_max_edit([(b"model-bytes", "image/jpeg")], "edit prompt", "sk-fake", out)
            self.assertEqual(result, out)
            self.assertEqual(out.read_bytes(), b"fake-qwen-png-bytes")

    def test_http_loi_raise_joberror(self):
        DASHSCOPE_STATE["mode"] = "http_error"
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(lt, "QWEN_IMAGE_BASE", self.base_url):
                with self.assertRaises(JobError) as cm:
                    lt.qwen_max_edit([(b"x", "image/png")], "p", "sk-fake", Path(d) / "o.png")
            self.assertIn("400", str(cm.exception))

    def test_khong_tra_anh_raise_joberror(self):
        DASHSCOPE_STATE["mode"] = "no_image"
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(lt, "QWEN_IMAGE_BASE", self.base_url):
                with self.assertRaises(JobError):
                    lt.qwen_max_edit([(b"x", "image/png")], "p", "sk-fake", Path(d) / "o.png")


class TestQwenTryonPrompts(unittest.TestCase):
    def test_auto_khong_bia_do(self):
        pos, neg = lt.qwen_tryon_prompts("auto")
        self.assertIn("Copy the ENTIRE look", pos)
        self.assertIn("different person", neg)

    def test_shoes_rieng(self):
        pos, _ = lt.qwen_tryon_prompts("shoes")
        self.assertIn("BOTH", pos)

    def test_reveal_khac_multi(self):
        pos_reveal, _ = lt.qwen_tryon_prompts("bikini")
        pos_multi, _ = lt.qwen_tryon_prompts("set")
        self.assertIn("garter belt", pos_reveal)
        self.assertIn("NORMAL everyday outfit", pos_multi)
        self.assertNotEqual(pos_reveal, pos_multi)

    def test_extra_duoc_chen_vao(self):
        pos, _ = lt.qwen_tryon_prompts("auto", extra="keep the necklace")
        self.assertIn("keep the necklace", pos)


class TestIsLocalProvider(unittest.TestCase):
    def test_gemini_la_local(self):
        self.assertTrue(lt.is_local_provider("gemini"))
        self.assertTrue(lt.is_local_provider("Gemini"))
        self.assertTrue(lt.is_local_provider("  gemini  "))

    def test_qwen_khong_phai_local(self):
        self.assertFalse(lt.is_local_provider("qwen"))
        self.assertFalse(lt.is_local_provider(""))
        self.assertFalse(lt.is_local_provider(None))

    def test_qwen_max_la_local(self):
        self.assertTrue(lt.is_local_provider("qwen-max"))
        self.assertTrue(lt.is_local_provider("Qwen-Max"))


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

    def test_bo_qua_nguoi_mau_trong_anh_san_pham(self):
        # Đo 28/08/2026 (c1-o8-m2-b3): outfit photo là ảnh người mẫu, không phải flat-lay —
        # thiếu câu này thì Gemini có thể chép tóc/mặt người mẫu trong ảnh outfit qua kết quả.
        # Đo 15/09/2026 (batch 2026-09-15-0030): câu cũ không cấm chép NỀN của người mẫu đó —
        # tăng cường thêm "room/background/setting visible in image 2".
        for gt in ("auto", "shoes", "upper", "set"):
            p = lt.gemini_tryon_prompt(gt)
            self.assertIn("ignore that wearer and their surroundings entirely", p)
            self.assertIn("room/background/setting visible in image 2", p)


from unittest import mock

from batchlib.config import Settings
from batchlib.manifest import Run


class TestPostprocess(unittest.TestCase):
    def test_khong_co_tham_so_thi_giu_nguyen_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "in.png"
            p.write_bytes(b"x")
            self.assertEqual(lt.postprocess(p, {}), p)

    def test_co_brightness_thi_goi_ffmpeg(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "in.png"
            p.write_bytes(b"x")
            dst = p.with_suffix(".pp.png")

            def fake_run(cmd, **kwargs):
                dst.write_bytes(b"processed" * 200)   # > 1024 byte để qua ngưỡng
                return mock.Mock(returncode=0)

            with mock.patch("subprocess.run", fake_run):
                out = lt.postprocess(p, {"brightness": 0.2})
            self.assertEqual(out, dst)

    def test_ffmpeg_loi_thi_giu_anh_goc(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "in.png"
            p.write_bytes(b"x")
            with mock.patch("subprocess.run", side_effect=OSError("khong co ffmpeg")):
                out = lt.postprocess(p, {"brightness": 0.2})
            self.assertEqual(out, p)


class TestCameraAwareGuide(unittest.TestCase):
    def test_camera_prompt_is_loaded_from_the_worker_asset(self):
        # 15/09/2026 - camera compose tách làm 2 lệnh (bg swap 2 ảnh + camera reframe 2 ảnh):
        # asset này giờ chỉ còn 2 vai (Image 1 = người đã đúng nền, Image 2 = camera guide),
        # không còn Image 3.
        positive, negative = lt.load_camera_compose_prompt()
        self.assertIn("Image 1", positive)
        self.assertIn("Image 2", positive)
        self.assertNotIn("Image 3", positive)
        self.assertIn("do not copy", positive.lower())
        self.assertIn("different location", negative.lower())

    def test_midpoint_respects_selected_segment(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[0] == "ffprobe" and "format=duration" in cmd:
                return mock.Mock(returncode=0, stdout="40.0\n", stderr="")
            if cmd[0] == "ffprobe":
                return mock.Mock(returncode=0, stdout="720x1280\n", stderr="")
            Path(cmd[-1]).write_bytes(b"png")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as d, mock.patch.object(lt.subprocess, "run", fake_run):
            dims = lt.extract_camera_guide_frame(
                Path("driver.mp4"), Path(d) / "guide.png",
                {"driverStartSec": 10, "driverDurSec": 12},
            )
        ffmpeg = next(cmd for cmd in calls if cmd[0] == "ffmpeg")
        self.assertEqual(ffmpeg[ffmpeg.index("-ss") + 1], "16.000000")
        self.assertEqual(dims, (720, 1280))

    def test_empty_or_zero_duration_guide_is_strictly_rejected(self):
        with mock.patch.object(
            lt.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="0\n", stderr="")
        ):
            with self.assertRaises(JobError) as cm:
                lt.extract_camera_guide_frame(Path("driver.mp4"), Path("guide.png"), {})
        self.assertIn("camera guide", str(cm.exception))

    def test_camera_guide_frame_other_than_middle_is_rejected(self):
        with self.assertRaises(JobError) as cm:
            lt.extract_camera_guide_frame(
                Path("driver.mp4"), Path("guide.png"), {"cameraGuideFrame": "start"}
            )
        self.assertIn("camera guide", str(cm.exception))

    def test_non_finite_segment_start_is_rejected(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return mock.Mock(returncode=0, stdout="40\n", stderr="")

        with mock.patch.object(lt.subprocess, "run", fake_run):
            with self.assertRaises(JobError):
                lt.extract_camera_guide_frame(
                    Path("driver.mp4"), Path("guide.png"), {"driverStartSec": float("nan")}
                )
        self.assertFalse(any(cmd[0] == "ffmpeg" for cmd in calls))


def _run_gemini(tmp: Path, background: bool = False) -> Run:
    (tmp / "char.jpg").write_bytes(b"char-bytes")
    (tmp / "outfit.jpg").write_bytes(b"outfit-bytes")
    inputs = {"character": tmp / "char.jpg", "outfit": tmp / "outfit.jpg"}
    if background:
        (tmp / "bg.jpg").write_bytes(b"bg-bytes")
        inputs["background"] = tmp / "bg.jpg"
    return Run(id="runA", pipeline="tryon-motion-enhance", inputs=inputs,
              stage_params={"tryon": {"provider": "gemini"}})


class TestCameraComposition(GeminiServerCase):
    def test_ordinary_path_ignores_driver_and_preserves_legacy_aspect(self):
        from PIL import Image
        for provider in ("gemini", "qwen-max"):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as d:
                tmp = Path(d)
                run = _run_gemini(tmp, background=True)
                Image.new("RGB", (160, 90), "red").save(run.inputs["character"])
                run.inputs["driver"] = tmp / "does-not-exist.mp4"
                calls = []
                def edit(images, prompt, key, out_path, **kwargs):
                    calls.append((images, kwargs))
                    Image.new("RGB", (160, 90), "red").save(out_path)
                    return out_path
                with mock.patch.object(lt, "gemini_edit", edit), mock.patch.object(lt, "qwen_max_edit", edit):
                    lt.run_local_tryon(run, {"provider": provider, "cameraAware": "false"},
                                      Settings(domain="x", api_key="x", instance_id="x", dashscope_api_key="fake",
                                               gemini_api_key="AIza" + "x" * 35), tmp / "out.png")
                self.assertEqual([len(refs) for refs, _ in calls], [2, 2])
                self.assertEqual(lt.img_size(tmp / "out.png"), (160, 90))
                if provider == "gemini":
                    self.assertEqual([kw["aspect_ratio"] for _, kw in calls], ["16:9", "16:9"])
                else:
                    self.assertTrue(all("size" not in kw for _, kw in calls))

    def test_qwen_camera_size_is_serialized_and_ordinary_size_is_omitted(self):
        bodies = []
        def urlopen(request, **kwargs):
            response = mock.MagicMock()
            if isinstance(request, str):
                response.__enter__.return_value.read.return_value = b"image"
            else:
                bodies.append(json.loads(request.data))
                response.__enter__.return_value.read.return_value = json.dumps({"output": {"choices": [
                    {"message": {"content": [{"image": "https://example.test/image"}]}}]}}).encode()
            return response
        with tempfile.TemporaryDirectory() as d, mock.patch.object(lt.urllib.request, "urlopen", urlopen), \
             mock.patch.object(lt, "QWEN_IMAGE_BASE", "https://example.test"):
            self.assertIn("size", __import__("inspect").signature(lt.qwen_max_edit).parameters)
            lt.qwen_max_edit([(b"x", "image/png")], "prompt", "fake", Path(d) / "camera.png", size="752*1328")
            lt.qwen_max_edit([(b"x", "image/png")], "prompt", "fake", Path(d) / "ordinary.png")
        self.assertEqual(bodies[0]["parameters"]["size"], "752*1328")
        self.assertEqual(bodies[1]["parameters"], {"watermark": False})

    def test_camera_local_two_step_compose_and_guide_aspect(self):
        # 15/09/2026 - camera compose tách làm 2 lệnh riêng (root cause #2, batch 2026-09-15-0030):
        # bước A ghép nền (2 ảnh: pass1 result + background, dùng lại TRYON_BG_POS) rồi bước B
        # xoay góc máy (2 ảnh: kết quả bước A + camera guide, dùng camera-aware-tryon.json).
        from PIL import Image, ImageDraw
        for provider in ("gemini", "qwen-max"):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as d:
                tmp = Path(d)
                run = _run_gemini(tmp, background=True)
                driver = tmp / "driver.mp4"
                lt.subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                                   "color=c=blue:s=90x160:d=1", str(driver)], check=True)
                run.inputs["driver"] = driver
                calls, generated = [], []
                def edit(images, prompt, key, out_path, **kwargs):
                    calls.append((images, prompt, kwargs))
                    picture = Image.new("RGB", (160, 160), "black")
                    ImageDraw.Draw(picture).rectangle((70, 70, 89, 89), fill="red")
                    picture.save(out_path)
                    generated.append(out_path.read_bytes())
                    return out_path
                out = tmp / "out.png"
                settings = Settings(domain="x", api_key="x", instance_id="x",
                                    gemini_api_key="AIza" + "x" * 35, dashscope_api_key="fake")
                with mock.patch.object(lt, "gemini_edit", edit), mock.patch.object(lt, "qwen_max_edit", edit):
                    lt.run_local_tryon(run, {"provider": provider, "cameraAware": True}, settings, out)
                # calls[0] = pass 1 thay đồ (2 ảnh: model + outfit)
                self.assertEqual(len(calls[0][0]), 2)
                # calls[1] = bước A ghép nền (2 ảnh: kết quả pass1 + background), prompt TRYON_BG_POS có sẵn
                self.assertEqual(len(calls[1][0]), 2)
                self.assertEqual(calls[1][0][0][0], generated[0])
                self.assertEqual(calls[1][0][1][0], b"bg-bytes")
                self.assertEqual(calls[1][1], lt.TRYON_BG_POS)
                # calls[2] = bước B xoay góc máy (2 ảnh: kết quả bước A + camera guide)
                self.assertEqual(len(calls[2][0]), 2)
                self.assertEqual(calls[2][0][0][0], generated[1])
                import io
                with Image.open(io.BytesIO(calls[2][0][1][0])) as guide:
                    self.assertEqual(guide.size, (90, 160))
                self.assertEqual(calls[2][1], lt.load_camera_compose_prompt()[0])
                if provider == "gemini":
                    self.assertEqual(calls[2][2]["aspect_ratio"], "9:16")
                    # 16/09/2026 (run IMG67441-IMG6957-IMG68943-tiktok178952 — mặt vẫn mờ sau
                    # 4aca8c9): thiếu imageConfig.imageSize khiến gemini-3-pro-image tự mặc định
                    # 1K bất kể ảnh gốc to đến đâu. Trần này khác bug crop tối 15/09.
                    self.assertEqual(calls[2][2]["image_size"], lt.GEMINI_CAMERA_IMAGE_SIZE)
                else:
                    self.assertEqual(calls[2][2].get("size"), "752*1328")
                # 15/09/2026 (batch 2026-09-15-1120, mặt mờ trên driver nét thấp) - framed KHÔNG còn
                # bị ép về đúng pixel của guide/driver (90x160): candidate sinh ra 160x160 (25600px)
                # lớn hơn guide (14400px) nên diện tích giữ nguyên mức candidate, chỉ tỉ lệ khung đổi
                # theo guide — Wan Animate tự resize ref về render W×H của nó (linux.py:_fit_driver_wh
                # + ImageResizeKJv2), không cần ref khớp pixel driver.
                out_w, out_h = lt.img_size(out)
                self.assertGreaterEqual(out_w * out_h, 160 * 160)
                expected_area = max(90 * 160, 160 * 160)
                expected_w = max(16, round(math.sqrt(expected_area * (90 / 160)) / 16) * 16)
                expected_h = max(16, round(math.sqrt(expected_area / (90 / 160)) / 16) * 16)
                self.assertEqual((out_w, out_h), (expected_w, expected_h))

    def test_camera_local_without_background_skips_background_swap(self):
        from PIL import Image, ImageDraw
        for provider in ("gemini", "qwen-max"):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as d:
                tmp = Path(d)
                run = _run_gemini(tmp, background=False)
                driver = tmp / "driver.mp4"
                lt.subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                                   "color=c=blue:s=90x160:d=1", str(driver)], check=True)
                run.inputs["driver"] = driver
                calls, generated = [], []
                def edit(images, prompt, key, out_path, **kwargs):
                    calls.append((images, prompt, kwargs))
                    picture = Image.new("RGB", (160, 160), "black")
                    ImageDraw.Draw(picture).rectangle((70, 70, 89, 89), fill="red")
                    picture.save(out_path)
                    generated.append(out_path.read_bytes())
                    return out_path
                out = tmp / "out.png"
                settings = Settings(domain="x", api_key="x", instance_id="x",
                                    gemini_api_key="AIza" + "x" * 35, dashscope_api_key="fake")
                with mock.patch.object(lt, "gemini_edit", edit), mock.patch.object(lt, "qwen_max_edit", edit):
                    lt.run_local_tryon(run, {"provider": provider, "cameraAware": True}, settings, out)
                # pass 1 (model + outfit) then the camera reframe; no background swap in between.
                self.assertEqual(len(calls), 2)
                self.assertNotIn(lt.TRYON_BG_POS, [call[1] for call in calls])
                self.assertEqual(len(calls[1][0]), 2)
                self.assertEqual(calls[1][0][0][0], generated[0])
                self.assertEqual(calls[1][1], lt.load_camera_compose_prompt()[0])
                self.assertTrue(lt.img_size(out))

    def test_camera_local_requires_driver(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            run = _run_gemini(tmp, background=True)
            with self.assertRaises(JobError) as cm:
                lt.run_local_tryon(run, {"provider": "gemini", "cameraAware": True},
                                  Settings(domain="x", api_key="x", instance_id="x",
                                           gemini_api_key="AIza" + "x" * 35), tmp / "out.png")
            self.assertIn("driver", str(cm.exception))

    def test_invalid_guide_prevents_garment_call(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            run = _run_gemini(tmp, background=True)
            run.inputs["driver"] = tmp / "invalid.mp4"
            run.inputs["driver"].write_bytes(b"invalid")
            calls = []
            def edit(*args, **kwargs):
                calls.append(args)
                Path(args[3]).write_bytes(b"bad")
                return Path(args[3])
            with mock.patch.object(lt, "gemini_edit", edit):
                with self.assertRaises(JobError):
                    lt.run_local_tryon(run, {"provider": "gemini", "cameraAware": True},
                                      Settings(domain="x", api_key="x", instance_id="x",
                                               gemini_api_key="AIza" + "x" * 35), tmp / "out.png")
            self.assertEqual(calls, [])
            self.assertFalse((tmp / "out.png").exists())

    def test_camera_compose_failure_never_publishes_garment(self):
        # 15/09/2026 - camera compose giờ là 2 lệnh 2-ảnh (bg swap rồi camera reframe), không còn
        # lệnh 3-ảnh duy nhất. Nhận diện bước camera-reframe bằng NỘI DUNG ảnh thứ 2 (camera guide
        # thật, khác b"outfit-bytes" của pass1 và b"bg-bytes" của bước A) thay vì đếm số ảnh.
        from PIL import Image
        for mode in ("error", "decode"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as d:
                tmp = Path(d)
                run = _run_gemini(tmp, background=True)
                driver = tmp / "driver.mp4"
                lt.subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                                   "color=c=blue:s=90x160:d=1", str(driver)], check=True)
                run.inputs["driver"] = driver
                def edit(images, prompt, key, out_path, **kwargs):
                    is_camera_reframe = len(images) == 2 and images[1][0] not in (b"outfit-bytes", b"bg-bytes")
                    if is_camera_reframe:
                        if mode == "error":
                            raise JobError("compose failed")
                        out_path.write_bytes(b"not an image")
                    else:
                        Image.new("RGB", (160, 160), "red").save(out_path)
                    return out_path
                with mock.patch.object(lt, "gemini_edit", edit):
                    with self.assertRaises(JobError):
                        lt.run_local_tryon(run, {"provider": "gemini", "cameraAware": True},
                                          Settings(domain="x", api_key="x", instance_id="x",
                                                   gemini_api_key="AIza" + "x" * 35), tmp / "out.png")
                self.assertFalse((tmp / "out.png").exists())


class TestRunLocalTryon(GeminiServerCase):
    def _settings(self, key="AIza" + "x" * 35):
        return Settings(domain="x.test", api_key="mk_test", instance_id="i-1",
                        gemini_api_key=key)

    def test_thanh_cong_ghi_ra_out_path(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            run = _run_gemini(tmp)
            out = tmp / "01-tryon.png"
            with mock.patch.object(lt, "GEMINI_API_BASE", self.base_url), \
                 mock.patch.object(lt, "img_size", return_value=(1080, 1920)):
                elapsed, size = lt.run_local_tryon(run, run.stage_params["tryon"],
                                                   self._settings(), out)
            self.assertGreaterEqual(elapsed, 0)
            self.assertEqual(size, out.stat().st_size)
            self.assertTrue(out.is_file())

    def test_co_background_thi_goi_pass_2(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            run = _run_gemini(tmp, background=True)
            out = tmp / "01-tryon.png"
            with mock.patch.object(lt, "GEMINI_API_BASE", self.base_url), \
                 mock.patch.object(lt, "img_size", return_value=None):
                lt.run_local_tryon(run, run.stage_params["tryon"], self._settings(), out)
            self.assertEqual(GEMINI_STATE["calls"], 2)   # pass 1 (thay đồ) + pass 2 (ghép nền)

    def test_thieu_key_raise_joberror(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            run = _run_gemini(tmp)
            with self.assertRaises(JobError):
                lt.run_local_tryon(run, run.stage_params["tryon"], self._settings(key=""),
                                   tmp / "out.png")

    def test_key_sai_dinh_dang_raise_joberror(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            run = _run_gemini(tmp)
            with self.assertRaises(JobError):
                lt.run_local_tryon(run, run.stage_params["tryon"], self._settings(key="sk-not-gemini"),
                                   tmp / "out.png")

    def test_provider_chua_ho_tro_raise_not_implemented(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            run = _run_gemini(tmp)
            with self.assertRaises(NotImplementedError):
                lt.run_local_tryon(run, {"provider": "huggingface"}, self._settings(), tmp / "out.png")

    def test_thieu_input_bat_buoc_raise_joberror(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            run = Run(id="runA", pipeline="tryon-motion-enhance",
                     inputs={"character": tmp / "char.jpg"},   # thiếu outfit
                     stage_params={"tryon": {"provider": "gemini"}})
            (tmp / "char.jpg").write_bytes(b"x")
            with self.assertRaises(JobError):
                lt.run_local_tryon(run, run.stage_params["tryon"], self._settings(), tmp / "out.png")

    def test_extraPrompt_vietnamese_thi_goi_translate(self):
        # Regression test: translate_vn_to_en call site in run_local_tryon must pass
        # base_url=GEMINI_API_BASE explicitly so mocking works. This test exercises that
        # call path to ensure it's not accidentally lost in future edits.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            run = _run_gemini(tmp)
            # Set Vietnamese extraPrompt with diacritics to trigger translate call
            run.stage_params["tryon"]["extraPrompt"] = "giữ nguyên vòng cổ"
            out = tmp / "01-tryon.png"

            # Set up handler: call 0 = translate (text), call 1 = pass-1 edit (image)
            GEMINI_STATE["modes"] = {0: "text", 1: "image"}
            GEMINI_STATE["text_reply"] = "keep the necklace"

            with mock.patch.object(lt, "GEMINI_API_BASE", self.base_url), \
                 mock.patch.object(lt, "img_size", return_value=(1080, 1920)):
                elapsed, size = lt.run_local_tryon(run, run.stage_params["tryon"],
                                                   self._settings(), out)

            # Verify both translate and pass-1 edit were called
            self.assertEqual(GEMINI_STATE["calls"], 2)
            self.assertGreaterEqual(elapsed, 0)
            self.assertEqual(size, out.stat().st_size)
            self.assertTrue(out.is_file())


class TestRunLocalTryonQwenMax(DashscopeServerCase):
    def _settings(self, key="dashscope-fake-key"):
        return Settings(domain="x.test", api_key="mk_test", instance_id="i-1",
                        dashscope_api_key=key)

    def test_thanh_cong_qwen_max(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            run = _run_gemini(tmp)
            run.stage_params["tryon"]["provider"] = "qwen-max"
            out = tmp / "01-tryon.png"
            with mock.patch.object(lt, "QWEN_IMAGE_BASE", self.base_url):
                elapsed, size = lt.run_local_tryon(run, run.stage_params["tryon"],
                                                   self._settings(), out)
            self.assertGreaterEqual(elapsed, 0)
            self.assertEqual(size, out.stat().st_size)
            self.assertTrue(out.is_file())

    def test_co_background_thi_goi_pass_2(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            run = _run_gemini(tmp, background=True)
            run.stage_params["tryon"]["provider"] = "qwen-max"
            out = tmp / "01-tryon.png"
            with mock.patch.object(lt, "QWEN_IMAGE_BASE", self.base_url):
                lt.run_local_tryon(run, run.stage_params["tryon"], self._settings(), out)
            self.assertEqual(DASHSCOPE_STATE["calls"], 2)   # pass 1 (thay đồ) + pass 2 (ghép nền)

    def test_thieu_key_raise_joberror(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            run = _run_gemini(tmp)
            run.stage_params["tryon"]["provider"] = "qwen-max"
            with mock.patch.object(lt, "QWEN_IMAGE_BASE", self.base_url):
                with self.assertRaises(JobError):
                    lt.run_local_tryon(run, run.stage_params["tryon"], self._settings(key=""),
                                       tmp / "out.png")


class TestRunLocalTryonGeminiFallback(unittest.TestCase):
    """provider='gemini' + TRYON_GEMINI_FALLBACK bật + Gemini lỗi → tự rớt sang Qwen-Max."""

    @classmethod
    def setUpClass(cls):
        cls.gem_server = HTTPServer(("127.0.0.1", 0), GeminiHandler)
        threading.Thread(target=cls.gem_server.serve_forever, daemon=True).start()
        gh, gp = cls.gem_server.server_address
        cls.gem_url = f"http://{gh}:{gp}"

        cls.dash_server = HTTPServer(("127.0.0.1", 0), DashscopeHandler)
        dh, dp = cls.dash_server.server_address
        cls.dash_url = f"http://{dh}:{dp}"
        cls.dash_server.base_url = cls.dash_url
        threading.Thread(target=cls.dash_server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.gem_server.shutdown(); cls.gem_server.server_close()
        cls.dash_server.shutdown(); cls.dash_server.server_close()

    def setUp(self):
        GEMINI_STATE["mode"] = "http_error"
        GEMINI_STATE["calls"] = 0
        GEMINI_STATE.pop("modes", None)
        GEMINI_STATE.pop("text_reply", None)
        DASHSCOPE_STATE["mode"] = "image"
        DASHSCOPE_STATE["calls"] = 0

    def _settings(self):
        return Settings(domain="x.test", api_key="mk_test", instance_id="i-1",
                        gemini_api_key="AIza" + "x" * 35, dashscope_api_key="dash-fake")

    def test_gemini_loi_tu_rot_qwen_max_khi_bat_co(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            run = _run_gemini(tmp)
            out = tmp / "01-tryon.png"
            with mock.patch.object(lt, "GEMINI_API_BASE", self.gem_url), \
                 mock.patch.object(lt, "QWEN_IMAGE_BASE", self.dash_url), \
                 mock.patch.object(lt, "TRYON_GEMINI_FALLBACK", True), \
                 mock.patch.object(lt, "img_size", return_value=(1080, 1920)):
                lt.run_local_tryon(run, run.stage_params["tryon"], self._settings(), out)
            self.assertTrue(out.is_file())
            self.assertEqual(out.read_bytes(), b"fake-qwen-png-bytes")

    def test_gemini_loi_khong_fallback_khi_co_tat(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            run = _run_gemini(tmp)
            out = tmp / "01-tryon.png"
            with mock.patch.object(lt, "GEMINI_API_BASE", self.gem_url), \
                 mock.patch.object(lt, "QWEN_IMAGE_BASE", self.dash_url), \
                 mock.patch.object(lt, "TRYON_GEMINI_FALLBACK", False), \
                 mock.patch.object(lt, "img_size", return_value=(1080, 1920)):
                with self.assertRaises(JobError):
                    lt.run_local_tryon(run, run.stage_params["tryon"], self._settings(), out)

    def test_bat_co_nhung_thieu_dashscope_key_raise_som(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            run = _run_gemini(tmp)
            settings = Settings(domain="x.test", api_key="mk_test", instance_id="i-1",
                                gemini_api_key="AIza" + "x" * 35, dashscope_api_key="")
            with mock.patch.object(lt, "GEMINI_API_BASE", self.gem_url), \
                 mock.patch.object(lt, "TRYON_GEMINI_FALLBACK", True):
                with self.assertRaises(JobError):
                    lt.run_local_tryon(run, run.stage_params["tryon"], settings, tmp / "out.png")


if __name__ == "__main__":
    unittest.main()
