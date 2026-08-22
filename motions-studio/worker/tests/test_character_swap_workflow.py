import os
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

# linux.py chỉ cần requests khi worker thật gọi API — stub như test_wan_anchored_context.py
try:
    import requests  # noqa: F401
except ModuleNotFoundError:
    requests_stub = types.ModuleType("requests")
    requests_stub.exceptions = types.SimpleNamespace(
        ConnectionError=ConnectionError,
        RequestException=Exception,
    )
    sys.modules["requests"] = requests_stub

from worker_runtime import linux  # noqa: E402
from worker_runtime.linux import (  # noqa: E402
    RefFramingTooFar,
    _apply_swap_to_wan_workflow,
    _fit_driver_wh,
    _ref_framing_crop_box,
    build_scail2_swap_workflow,
    build_swap_headprobe_workflow,
    build_wan_workflow,
)


class ApplySwapToWanWorkflowTests(unittest.TestCase):
    def _wf(self, p=None):
        params = {"width": 544, "height": 960, "frames": 81, "steps": 4, **(p or {})}
        return _apply_swap_to_wan_workflow(
            build_wan_workflow("ref.png", "drv.mp4", params), params)

    def test_embeds_nhan_bg_va_mask(self):
        wf = self._wf()
        self.assertEqual(wf["81"]["inputs"]["bg_images"], ["206", 0])
        self.assertEqual(wf["81"]["inputs"]["mask"], ["205", 0])

    def test_chuoi_mask_sam3_dung_thu_tu_kijai(self):
        wf = self._wf()
        self.assertEqual(wf["200"]["class_type"], "CheckpointLoaderSimple")
        self.assertEqual(wf["200"]["inputs"]["ckpt_name"], "sam3.1_multiplex_fp16.safetensors")
        self.assertEqual(wf["202"]["class_type"], "SAM3_VideoTrack")
        self.assertEqual(wf["202"]["inputs"]["images"], ["12", 0])   # frame driver
        self.assertEqual(wf["203"]["class_type"], "SAM3_TrackToMask")
        self.assertEqual(wf["204"]["class_type"], "GrowMaskWithBlur")
        self.assertEqual(wf["205"]["class_type"], "BlockifyMask")
        self.assertEqual(wf["206"]["class_type"], "DrawMaskOnImage")
        self.assertEqual(wf["206"]["inputs"]["image"], ["12", 0])
        self.assertEqual(wf["206"]["inputs"]["mask"], ["205", 0])
        self.assertEqual(wf["206"]["inputs"]["color"], "0, 0, 0")

    def test_param_chinh_mask(self):
        wf = self._wf({"sam3Prompt": "woman in red dress", "maskGrow": 20, "maskBlockify": 16})
        self.assertEqual(wf["201"]["inputs"]["text"], "woman in red dress")
        self.assertEqual(wf["204"]["inputs"]["expand"], 20)
        self.assertEqual(wf["205"]["inputs"]["block_size"], 16)

    def test_blockify_mac_dinh_16_khong_phai_32(self):
        # Đo thật 21/08: mask blockify 32 nới vùng vẽ lại rộng hơn hẳn dáng người → Wan có đất
        # bịa vật thể (bó hoa) khi keypoint tay mơ hồ. 16 bám sát người hơn mà vẫn thẳng lưới latent.
        self.assertEqual(self._wf()["205"]["inputs"]["block_size"], 16)

    def test_negative_prompt_chan_bia_vat_the(self):
        neg = self._wf()["60"]["inputs"]["negative_prompt"]
        self.assertIn("bouquet", neg)
        self.assertIn("holding objects", neg)
        # NỐI THÊM, không thay: negative gốc lo da bóng/cháy sáng, mất là hỏng thứ khác.
        self.assertIn("过曝", neg)

    def test_negative_extra_ghi_de_va_tat_duoc(self):
        self.assertIn("no handbag", self._wf({"swapNegativeExtra": "no handbag"})["60"]["inputs"]["negative_prompt"])
        # Rỗng = TẮT hẳn: mẫu cầm sản phẩm (túi xách) là kịch bản thật của tool thời trang.
        neg_off = self._wf({"swapNegativeExtra": ""})["60"]["inputs"]["negative_prompt"]
        self.assertNotIn("bouquet", neg_off)
        self.assertIn("过曝", neg_off)

    def test_mask_phu_qua_nua_khung_thi_THU_lai_va_bo_blockify(self):
        """Đo thật 22/08: mask người của driver cận cảnh phủ 61% khung (bbox 79%). Wan phải vẽ lại
        hai phần ba khung mỗi frame trong khi DWPose chỉ có vài khớp đầu-vai để dẫn → nó lấp chỗ
        trống bằng vật thể bịa (5/8 lần ra CÙNG một cây đàn guitar). Nới mask thêm 10px rồi blockify
        chỉ làm chỗ trống rộng thêm, nên khi mask đã quá lớn thì phải ĂN MÒN ngược lại."""
        wf = self._wf({"_driverMaskCoverage": 0.61})
        self.assertLess(wf["204"]["inputs"]["expand"], 0)      # erode, không grow
        self.assertNotIn("205", wf)                            # bỏ blockify
        self.assertEqual(wf["206"]["inputs"]["mask"], ["204", 0])
        self.assertEqual(wf["81"]["inputs"]["mask"], ["204", 0])

    def test_mask_nho_thi_giu_nguyen_hanh_vi_cu(self):
        wf = self._wf({"_driverMaskCoverage": 0.30})
        self.assertEqual(wf["204"]["inputs"]["expand"], 10)
        self.assertEqual(wf["205"]["inputs"]["block_size"], 16)
        self.assertEqual(wf["81"]["inputs"]["mask"], ["205", 0])

    def test_chua_do_duoc_do_phu_thi_giu_nguyen_hanh_vi_cu(self):
        # Probe hỏng / bị tắt → không có số đo. Không được đoán bừa là mask lớn.
        wf = self._wf()
        self.assertEqual(wf["204"]["inputs"]["expand"], 10)
        self.assertIn("205", wf)

    def test_nguoi_dung_ep_tay_thi_ton_trong(self):
        wf = self._wf({"_driverMaskCoverage": 0.61, "maskGrow": 4, "maskBlockify": 32})
        self.assertEqual(wf["204"]["inputs"]["expand"], 4)
        self.assertEqual(wf["205"]["inputs"]["block_size"], 32)

    def test_negative_chan_luon_nhac_cu(self):
        # Bằng chứng chứ không phòng xa: 5/8 lần chạy dandong5 ra đúng một cây đàn guitar.
        neg = self._wf()["60"]["inputs"]["negative_prompt"]
        self.assertIn("guitar", neg)
        self.assertIn("musical instrument", neg)

    def test_positive_neo_canh_va_tat_duoc(self):
        pos = self._wf()["60"]["inputs"]["positive_prompt"]
        self.assertIn("empty hands", pos)
        self.assertIn("natural body proportions", pos)         # NỐI THÊM, không thay chuỗi gốc
        self.assertNotIn("empty hands", self._wf({"swapPositiveExtra": ""})["60"]["inputs"]["positive_prompt"])

    def test_seed_doi_duoc_va_mac_dinh_van_42(self):
        # Phát hiện 22/08 khi định chạy A/B nhiều seed: WanVideoSampler gắn CỨNG seed 42, nên param
        # 'seed' — vốn đã khai trong batch-params cho character-swap — xưa nay là no-op. Không đổi
        # được seed thì không chạy lại được khi Wan bịa vật thể, và không đo được nhiễu chạy-tới-chạy.
        self.assertEqual(self._wf()["90"]["inputs"]["seed"], 42)
        self.assertEqual(self._wf({"seed": 12345})["90"]["inputs"]["seed"], 12345)

    def test_khong_dung_vao_graph_motion_goc(self):
        params = {"width": 544, "height": 960, "frames": 81, "steps": 4}
        wf = build_wan_workflow("ref.png", "drv.mp4", params)
        self.assertNotIn("bg_images", wf["81"]["inputs"])
        self.assertNotIn("200", wf)


class BuildScail2SwapWorkflowTests(unittest.TestCase):
    def _params(self, **overrides):
        return {"width": 544, "height": 960, "frames": 81, "render_fps": 16, **overrides}

    def test_kich_thuoc_boi_32_va_cap_81_frame(self):
        wf = build_scail2_swap_workflow("ref.png", "drv.mp4", self._params(width=550, height=970, frames=161))
        n70 = wf["70"]["inputs"]
        self.assertEqual(n70["width"] % 32, 0)
        self.assertEqual(n70["height"] % 32, 0)
        self.assertEqual(n70["length"], 81)

    def test_wiring_theo_template_chinh_thuc(self):
        wf = build_scail2_swap_workflow("ref.png", "drv.mp4", self._params())
        n70 = wf["70"]["inputs"]
        self.assertEqual(wf["70"]["class_type"], "WanSCAILToVideo")
        self.assertIs(n70["replacement_mode"], True)
        self.assertEqual(n70["pose_video"], ["12", 0])          # frame driver
        self.assertEqual(n70["pose_video_mask"], ["25", 0])     # SCAIL2ColoredMask output 0
        self.assertEqual(n70["reference_image_mask"], ["25", 1])
        self.assertIs(wf["25"]["inputs"]["replacement_mode"], True)
        self.assertEqual(wf["30"]["inputs"]["unet_name"], "wan2.1_14B_SCAIL_2_fp8_scaled.safetensors")
        self.assertEqual(wf["90"]["inputs"]["sigmas"], ["81", 0])
        self.assertEqual(wf["100"]["inputs"]["samples"], ["90", 1])   # denoised output
        self.assertEqual(wf["110"]["inputs"]["audio"], ["12", 2])     # giữ audio driver

    def test_turbo_defaults(self):
        wf = build_scail2_swap_workflow("ref.png", "drv.mp4", self._params())
        self.assertEqual(wf["81"]["inputs"]["steps"], 6)
        self.assertEqual(wf["90"]["inputs"]["cfg"], 1.0)
        self.assertEqual(wf["32"]["inputs"]["strength_model"], 0.8)   # lightx2v rank64
        self.assertEqual(wf["31"]["inputs"]["strength_model"], 1.0)   # DPO


class FitDriverMultipleTests(unittest.TestCase):
    """Khung render đi theo TỈ LỆ DRIVER (không theo ảnh ref). Bội của khung khác nhau theo engine:
    Wan bội 16, còn WanSCAILToVideo khai io.Int step=32 nên builder scail2 floor về bội 32 — nếu
    FIT DRIVER trả bội 16 lẻ thì driver 3:4 rơi 720→704 và VHS kéo dẹt khung ~2.2%."""

    def test_wananimate_giu_boi_16(self):
        self.assertEqual(_fit_driver_wh(1080, 1920, 544, 968, 16), (544, 960))   # 9:16
        self.assertEqual(_fit_driver_wh(1080, 1440, 544, 968, 16), (544, 720))   # 3:4
        self.assertEqual(_fit_driver_wh(1000, 1000, 544, 968, 16), (544, 544))   # 1:1

    def test_scail2_boi_32_builder_khong_con_floor(self):
        w, h = _fit_driver_wh(1080, 1440, 544, 968, 32)     # 3:4 — ca duy nhất trước đây bị dẹt
        self.assertEqual((w, h), (544, 736))
        wf = build_scail2_swap_workflow("ref.png", "drv.mp4",
                                        {"width": w, "height": h, "frames": 81, "render_fps": 16})
        self.assertEqual((wf["70"]["inputs"]["width"], wf["70"]["inputs"]["height"]), (w, h))

    def test_ngang_thi_canh_dai_la_chieu_rong(self):
        self.assertEqual(_fit_driver_wh(1920, 1080, 544, 968, 32), (960, 544))

    def test_lam_tron_len_khong_duoc_vuot_tran_vram(self):
        # Trần lẻ (990) + làm tròn lên 32 sẽ ra 992 — phải lùi một bậc chứ không phá ngân sách VRAM.
        w, h = _fit_driver_wh(1080, 1920, 560, 990, 32)
        self.assertLessEqual(max(w, h), 990)
        self.assertEqual((w % 32, h % 32), (0, 0))


class RefFramingCropBoxTests(unittest.TestCase):
    """Khớp KHUNG HÌNH ảnh ref với driver, neo bằng cái đầu.

    Đo thật 22/08: driver dandong5 là selfie cận sát (chỉ đầu + một vai) còn ref là ảnh toàn thân
    → Wan nhét cả bố cục toàn thân của ref vào khung chỉ đủ chỗ cho cái đầu, ra giải phẫu bịa
    (tay khổng lồ quấn chân váy xám của ref). Đầu là bộ phận DUY NHẤT chắc chắn có trong cả hai
    ảnh, nên lấy nó làm mốc: phóng ref cho đầu cao bằng đầu driver rồi dóng tâm đầu.

    Test khẳng định BẤT BIẾN chứ không so số ma thuật: sau khi crop theo box rồi scale ×s, tâm đầu
    và chiều cao đầu của ref phải trùng của driver. Sai số 1e-6 vì box trả về là float — làm tròn
    là việc của chỗ gọi PIL, không phải của phép toán.
    """

    W, H = 544, 960
    REF_W, REF_H = 1156, 2047

    @staticmethod
    def _center(b):
        return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)

    def _assert_dau_khop(self, box, s, ref_head, drv_head):
        rcx, rcy = self._center(ref_head)
        dcx, dcy = self._center(drv_head)
        self.assertAlmostEqual((rcx - box[0]) * s, dcx, places=6)
        self.assertAlmostEqual((rcy - box[1]) * s, dcy, places=6)
        self.assertAlmostEqual((ref_head[3] - ref_head[1]) * s, drv_head[3] - drv_head[1], places=6)

    def _assert_khung_dung_ty_le(self, box, s):
        self.assertAlmostEqual(box[2] - box[0], self.W / s, places=6)
        self.assertAlmostEqual(box[3] - box[1], self.H / s, places=6)

    def test_driver_can_canh_phong_ref_len(self):
        ref_head, drv_head = (500, 200, 650, 400), (150, 100, 400, 500)   # cao 200 → 400
        box, s = _ref_framing_crop_box(self.REF_W, self.REF_H, ref_head, drv_head, self.W, self.H, 4.0)
        self.assertAlmostEqual(s, 2.0)
        self._assert_dau_khop(box, s, ref_head, drv_head)
        self._assert_khung_dung_ty_le(box, s)

    def test_driver_rong_hon_ref_thu_nho(self):
        # s < 1: driver lùi xa hơn ảnh ref → cửa sổ cắt RỘNG hơn khung render. Đầu đặt thấp trong
        # ref để cửa sổ 725×1280 lọt trọn 1156×2047 mà không phải dời (dời thì bất biến dưới sai).
        ref_head, drv_head = (500, 600, 700, 1000), (172, 330, 372, 630)   # cao 400 → 300
        box, s = _ref_framing_crop_box(self.REF_W, self.REF_H, ref_head, drv_head, self.W, self.H, 4.0)
        self.assertAlmostEqual(s, 0.75)
        self._assert_dau_khop(box, s, ref_head, drv_head)
        self._assert_khung_dung_ty_le(box, s)

    def test_lech_qua_xa_thi_bao_loi_chu_khong_doan(self):
        # Ref toàn thân + driver cận cực sát: phóng 7× thì ảnh ref nát, thà báo ngay còn hơn đốt
        # 7 phút GPU để ra thứ chắc chắn tệ.
        with self.assertRaises(RefFramingTooFar):
            _ref_framing_crop_box(self.REF_W, self.REF_H, (500, 200, 650, 300),
                                  (100, 100, 400, 800), self.W, self.H, 4.0)

    def test_ref_khong_du_khung_thi_bo_qua(self):
        # Cửa sổ cắt rộng hơn cả ảnh ref → không cắt được, trả None để dùng ref nguyên bản.
        self.assertIsNone(_ref_framing_crop_box(600, 800, (250, 300, 350, 450),
                                                (200, 400, 340, 460), self.W, self.H, 4.0))

    def test_dau_sat_mep_thi_don_cua_so_vao_trong_anh(self):
        ref_head, drv_head = (20, 10, 120, 110), (222, 430, 322, 530)
        box, s = _ref_framing_crop_box(self.REF_W, self.REF_H, ref_head, drv_head, self.W, self.H, 4.0)
        self.assertAlmostEqual(s, 1.0)
        self.assertGreaterEqual(box[0], 0.0)
        self.assertGreaterEqual(box[1], 0.0)
        self.assertLessEqual(box[2], self.REF_W)
        self.assertLessEqual(box[3], self.REF_H)
        self._assert_khung_dung_ty_le(box, s)   # dời chứ KHÔNG bóp méo cửa sổ


class HeadProbeWorkflowTests(unittest.TestCase):
    def _wf(self, **kw):
        return build_swap_headprobe_workflow("ref.png", "drv.mp4", 544, 960, stride=40, **kw)

    def test_hai_nhanh_dung_chung_mot_checkpoint_sam3(self):
        wf = self._wf()
        self.assertEqual(wf["22"]["inputs"]["images"], ["10", 0])    # nhánh ref
        self.assertEqual(wf["31"]["inputs"]["images"], ["30", 0])    # nhánh driver
        for nid in ("22", "31"):
            self.assertEqual(wf[nid]["class_type"], "SAM3_VideoTrack")
            self.assertEqual(wf[nid]["inputs"]["model"], ["20", 0])
            self.assertEqual(wf[nid]["inputs"]["conditioning"], ["21", 0])

    def test_driver_nap_dung_khung_render(self):
        # Mask driver phải nằm SẴN trong hệ toạ độ khung render, khỏi quy đổi ở Python.
        n30 = self._wf()["30"]["inputs"]
        self.assertEqual((n30["custom_width"], n30["custom_height"]), (544, 960))
        self.assertEqual(n30["frame_load_cap"], 3)      # 3 frame rải đều → lấy trung vị
        self.assertEqual(n30["select_every_nth"], 40)

    def test_hai_prefix_tach_biet_de_lay_dung_anh(self):
        wf = self._wf(prefix="pb")
        self.assertEqual(wf["25"]["class_type"], "SaveImage")
        self.assertEqual(wf["25"]["inputs"]["filename_prefix"], "pb-ref")
        self.assertEqual(wf["34"]["inputs"]["filename_prefix"], "pb-drv")
        self.assertEqual(wf["24"]["class_type"], "MaskToImage")

    def test_probe_do_luon_do_phu_mask_nguoi(self):
        """Nhánh thứ ba đo mask NGƯỜI của driver — quyết định có phải thu mask hay không. Đi kèm
        trong cùng graph vì SAM3 đã nạp sẵn, thêm nhánh gần như miễn phí; tách ra một job riêng là
        trả tiền nạp checkpoint hai lần."""
        wf = self._wf(prefix="pb")
        self.assertEqual(wf["26"]["inputs"]["text"], "person")
        self.assertEqual(wf["40"]["inputs"]["images"], ["30", 0])       # driver, đúng khung render
        self.assertEqual(wf["40"]["inputs"]["conditioning"], ["26", 0])
        self.assertEqual(wf["43"]["inputs"]["filename_prefix"], "pb-per")

    def test_mac_dinh_do_bang_FACE_khong_phai_head(self):
        """Đo thật 22/08: prompt 'head' trả về đầu CỘNG TOÀN BỘ TÓC, và ở driver bbox chạm mép khung
        (544, 960) nên phép đo tự bão hoà — nó đo khung còn lại bao nhiêu chứ không đo cái đầu.
        'face' cho cửa sổ chứa 3.58 chiều-cao-mặt, đúng bằng driver; 'head' cho 3.97."""
        self.assertEqual(self._wf()["21"]["inputs"]["text"], "face")
        self.assertEqual(self._wf(p={"refFrameHeadPrompt": "head"})["21"]["inputs"]["text"], "head")


class MatchRefFramingFailSafeTests(unittest.TestCase):
    """Khớp khung là TIỆN ÍCH, không được phép làm hỏng job: mọi trục trặc đều rơi về ref nguyên
    bản. Ngoại lệ duy nhất là lệch quá xa — cái đó phải nổi thành lỗi job, vì chạy tiếp là chắc
    chắn ra kết quả hỏng sau 7 phút GPU."""

    def _run(self, p=None):
        return linux._match_ref_framing_to_driver("job-1234", "/tmp/ref.png", "/tmp/drv.mp4",
                                                  544, 960, p or {}, "/tmp")

    def test_tat_bang_param_thi_khong_dung_toi_comfy(self):
        with patch.object(linux, "_comfy_has_node") as hn:
            self.assertIsNone(self._run({"refFrameMatch": "0"}))
        hn.assert_not_called()

    def test_thieu_node_sam3_thi_bo_qua(self):
        with patch.object(linux, "_comfy_has_node", return_value=False), \
             patch.object(linux, "comfy_submit") as sub:
            self.assertIsNone(self._run())
        sub.assert_not_called()

    def test_sam3_khong_thay_dau_thi_bo_qua(self):
        with patch.object(linux, "_comfy_has_node", return_value=True), \
             patch.object(linux, "comfy_upload", return_value="x.png"), \
             patch.object(linux, "comfy_submit", return_value="pid"), \
             patch.object(linux, "comfy_poll", return_value={}), \
             patch.object(linux, "_video_nframes", return_value=450), \
             patch.object(linux, "_comfy_node_images", return_value=[]), \
             patch("PIL.Image.open"):
            self.assertIsNone(self._run())

    def test_probe_no_thi_bo_qua_chu_khong_lam_hong_job(self):
        with patch.object(linux, "_comfy_has_node", return_value=True), \
             patch.object(linux, "comfy_upload", side_effect=RuntimeError("comfy sập")), \
             patch("PIL.Image.open"):
            self.assertIsNone(self._run())

    def test_lech_qua_xa_thi_noi_thanh_loi_job(self):
        with patch.object(linux, "_comfy_has_node", return_value=True), \
             patch.object(linux, "comfy_upload", return_value="x.png"), \
             patch.object(linux, "comfy_submit", return_value="pid"), \
             patch.object(linux, "comfy_poll", return_value={}), \
             patch.object(linux, "_video_nframes", return_value=450), \
             patch.object(linux, "_comfy_node_images", return_value=["/tmp/m.png"]), \
             patch.object(linux, "_mask_png_bbox", side_effect=[(0, 0, 100, 100),      # ref: đầu 100px
                                                                (0, 0, 300, 700)]), \
             patch("PIL.Image.open") as im:
            im.return_value.__enter__.return_value.size = (1156, 2047)
            with self.assertRaises(RefFramingTooFar):
                self._run()


class EnhanceRefCropTests(unittest.TestCase):
    """Làm nét ảnh ref sau khi cắt. Ba mức: off · restore (ESRGAN+CodeFormer cục bộ, PHỤC HỒI chứ
    không vẽ lại) · gen (Gemini → Qwen cục bộ, nét nhất nhưng rủi ro đổi danh tính cao nhất).

    Ảnh ref LÀ nguồn danh tính mà Wan sao chép, nên tầng làm nét tuyệt đối không được làm chết job:
    hỏng hết thì trả lại crop trần. Test dùng ảnh PNG THẬT, không giả lập PIL — kích thước ảnh là
    điều kiện rẽ nhánh nên giả lập nó là tự bịt mắt mình.
    """

    W, H = 544, 960

    def setUp(self):
        from PIL import Image
        self.tmp = tempfile.mkdtemp(prefix="reftest-")
        self.crop = os.path.join(self.tmp, "crop.png")
        Image.new("RGB", (160, 282), (128, 128, 128)).save(self.crop)   # nhỏ hơn khung → cần phóng
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _run(self, p):
        return linux._enhance_ref_crop("job-1234", self.crop, self.W, self.H, p, self.tmp)

    def _big(self):
        from PIL import Image
        p = os.path.join(self.tmp, "big.png")
        Image.new("RGB", (self.W + 40, self.H + 40), (10, 10, 10)).save(p)
        return p

    def test_doc_mode(self):
        self.assertEqual(linux._ref_enh_mode({}), "restore")            # mặc định
        self.assertEqual(linux._ref_enh_mode({"refEnhance": "0"}), "off")
        self.assertEqual(linux._ref_enh_mode({"refEnhance": "gemini"}), "gen")

    def test_off_thi_khong_dung_toi_tang_nao(self):
        with patch.object(linux, "_ref_enh_gemini") as g, patch.object(linux, "_ref_enh_restore") as r:
            self.assertEqual(self._run({"refEnhance": "off"}), self.crop)
        g.assert_not_called()
        r.assert_not_called()

    def test_crop_da_du_lon_thi_bo_qua(self):
        # s < 1: cửa sổ cắt rộng hơn khung render nên không phóng lên — không có gì để phục hồi.
        big = self._big()
        with patch.object(linux, "_ref_enh_restore") as r:
            self.assertEqual(linux._enhance_ref_crop("j", big, self.W, self.H, {}, self.tmp), big)
        r.assert_not_called()

    def test_restore_khong_goi_gemini(self):
        # Mức mặc định KHÔNG được gửi ảnh người mẫu ra ngoài internet.
        with patch.object(linux, "_ref_enh_gemini") as g, \
             patch.object(linux, "_ref_enh_restore", return_value=self._big()) as r:
            self._run({})
        g.assert_not_called()
        r.assert_called_once()

    def test_gen_gemini_loi_thi_tut_xuong_qwen(self):
        out = self._big()
        with patch.object(linux, "_ref_enh_gemini", side_effect=RuntimeError("Gemini API 503")), \
             patch.object(linux, "_ref_enh_qwen", return_value=out) as q, \
             patch.object(linux, "_ref_enh_restore") as r:
            self.assertIsNotNone(self._run({"refEnhance": "gen"}))
        q.assert_called_once()
        r.assert_not_called()

    def test_gen_hai_tang_gen_loi_thi_tut_xuong_phuc_hoi(self):
        out = self._big()
        with patch.object(linux, "_ref_enh_gemini", return_value=None), \
             patch.object(linux, "_ref_enh_qwen", side_effect=RuntimeError("comfy sập")), \
             patch.object(linux, "_ref_enh_restore", return_value=out) as r:
            self.assertIsNotNone(self._run({"refEnhance": "gen"}))
        r.assert_called_once()

    def test_hong_het_thi_tra_lai_crop_tran_chu_khong_chet_job(self):
        with patch.object(linux, "_ref_enh_gemini", side_effect=RuntimeError("x")), \
             patch.object(linux, "_ref_enh_qwen", side_effect=RuntimeError("y")), \
             patch.object(linux, "_ref_enh_restore", side_effect=RuntimeError("z")):
            self.assertEqual(self._run({"refEnhance": "gen"}), self.crop)

    def test_flashvsr_anh_tinh_thay_dung_dau_vao_dau_ra(self):
        # build_flashvsr_upscale_workflow kết thúc bằng VHS_VideoCombine và lấy audio ["10",2] —
        # không dùng thẳng cho ảnh tĩnh được. Builder mỏng chỉ thay đầu vào/đầu ra, GIỮ node 20/30
        # để mode/precision/attention/tile vẫn một nguồn sự thật.
        wf = linux.build_flashvsr_image_workflow("crop.png", 4, 0.3, prefix="rf")
        self.assertEqual(wf["10"]["class_type"], "LoadImage")
        self.assertEqual(wf["10"]["inputs"]["image"], "crop.png")
        self.assertEqual(wf["30"]["inputs"]["frames"], ["10", 0])       # IMAGE batch một ảnh
        self.assertEqual(wf["30"]["class_type"], "FlashVSRNodeAdv")
        self.assertNotIn("110", wf)                                      # hết VHS_VideoCombine
        self.assertEqual(wf["120"]["class_type"], "SaveImage")
        self.assertEqual(wf["120"]["inputs"]["images"], ["30", 0])

    def test_restore_uu_tien_flashvsr_co_san_hon_esrgan(self):
        with patch.object(linux, "_comfy_has_node", side_effect=lambda n: n == "FlashVSRNodeAdv"), \
             patch.object(linux, "comfy_upload", return_value="c.png"), \
             patch.object(linux, "comfy_submit", return_value="pid"), \
             patch.object(linux, "comfy_poll", return_value={}), \
             patch.object(linux, "comfy_fetch_output", return_value="/tmp/o.png"), \
             patch.object(linux, "build_flashvsr_image_workflow", return_value={}) as fv, \
             patch.object(linux, "build_image_upscale_workflow") as es:
            linux._ref_enh_restore("j", self.crop, self.W, {}, self.tmp)
        fv.assert_called_once()
        es.assert_not_called()

    def test_thieu_flashvsr_thi_tut_ve_esrgan(self):
        with patch.object(linux, "_comfy_has_node", return_value=False), \
             patch.object(linux, "comfy_upload", return_value="c.png"), \
             patch.object(linux, "comfy_submit", return_value="pid"), \
             patch.object(linux, "comfy_poll", return_value={}), \
             patch.object(linux, "comfy_fetch_output", return_value="/tmp/o.png"), \
             patch.object(linux, "build_flashvsr_image_workflow") as fv, \
             patch.object(linux, "build_image_upscale_workflow", return_value={}) as es:
            linux._ref_enh_restore("j", self.crop, self.W, {}, self.tmp)
        fv.assert_not_called()
        es.assert_called_once()

    def test_ket_qua_duoc_thu_ve_dung_khung_render(self):
        # ESRGAN nhân ×4 vô điều kiện → 2900px là phí băng thông upload; thu về đúng W×H.
        from PIL import Image
        with patch.object(linux, "_ref_enh_restore", return_value=self._big()):
            got = self._run({})
        with Image.open(got) as im:
            self.assertEqual(im.size, (self.W, self.H))


class RunCharacterSwapTests(unittest.TestCase):
    def test_dang_ky_pipeline(self):
        self.assertIn("character-swap", linux.PIPELINES)
        self.assertIs(linux.PIPELINES["character-swap"], linux.run_character_swap)

    def test_map_video_sang_motion_va_default_wananimate(self):
        job = {"id": "j1", "inputs": {"ref": "a/ref.png", "video": "a/drv.mp4"}, "params": {}}
        with patch.object(linux, "run_motion") as rm:
            linux.run_character_swap(job)
        rm.assert_called_once_with(job)
        self.assertEqual(job["inputs"]["motion"], "a/drv.mp4")
        p = job["params"]
        self.assertEqual(p["_swapEngine"], "wananimate")
        self.assertEqual(p["lora_relight"], 1.0)      # relight LoRA sinh ra cho Mix mode
        self.assertEqual(p["pose_strength"], 1.0)     # bộ số theo example kijai replacement
        self.assertEqual(p["face_strength"], 1.0)
        self.assertEqual(p["preset"], "drv-5s")

    def test_engine_scail2_va_engine_la(self):
        job = {"id": "j2", "inputs": {"ref": "r.png", "video": "d.mp4"},
               "params": {"engine": "scail2"}}
        with patch.object(linux, "run_motion"):
            linux.run_character_swap(job)
        self.assertEqual(job["params"]["_swapEngine"], "scail2")
        bad = {"id": "j3", "inputs": {"ref": "r.png", "video": "d.mp4"},
               "params": {"engine": "xyz"}}
        with self.assertRaises(RuntimeError):
            linux.run_character_swap(bad)

    def test_thieu_input_bao_ro(self):
        with self.assertRaises(RuntimeError):
            linux.run_character_swap({"id": "j4", "inputs": {"ref": "r.png"}, "params": {}})


class NormalizePathIntegrationTests(unittest.TestCase):
    """Test đi ĐÚNG đường thật (không chạy run_motion): run_character_swap set default →
    _normalize_motion_params mutate → build_*_workflow đọc. Đây là chỗ thiếu khiến review trước
    lọt 2 bug: scail2 bị ép về 4 bước (preset drv-5s + fast-profile branch cùng ghi đè steps),
    và bodyProportionLock chỉ setdefault ở nhánh wananimate nên scail2 bị khóa 'vóc dáng theo ref'
    kéo pose_strength xuống 0.7. Không patch _normalize_motion_params/build_* — chỉ patch run_motion
    để callable dừng lại đúng lúc, phần còn lại chạy y hệt runtime thật."""

    def _normalized_params(self, engine):
        job = {"id": "jn", "inputs": {"ref": "r.png", "video": "d.mp4"},
               "params": {"engine": engine}}
        with patch.object(linux, "run_motion"):
            linux.run_character_swap(job)
        return linux._normalize_motion_params(dict(job["params"]))

    def test_scail2_giu_6_buoc_va_pose_strength_1(self):
        p = self._normalized_params("scail2")
        wf = build_scail2_swap_workflow("ref.png", "drv.mp4", p)
        self.assertEqual(wf["81"]["inputs"]["steps"], 6)               # BasicScheduler
        self.assertEqual(wf["70"]["inputs"]["pose_strength"], 1.0)     # WanSCAILToVideo

    def test_swap_giu_fit_driver_du_dung_preset_drv(self):
        """Đo thật 22/08 trên pod: driver 3:4 (576×768) ra 544×960 — bị KÉO DÃN. Vì preset drv-*
        tắt thẳng fitDriver rồi lấy khung theo aspectRatio mặc định 9:16. Đúng cho Motion (user tự
        chọn tỉ lệ trên UI), SAI cho swap: cả tính năng là giữ nguyên video nguồn."""
        for engine in ("wananimate", "scail2"):
            p = self._normalized_params(engine)
            self.assertNotEqual(p.get("fitDriver"), False, engine)
            self.assertNotEqual(p.get("fit_driver"), False, engine)

    def test_motion_thuong_van_tat_fit_driver(self):
        # Chính sách quality-v1 của Motion KHÔNG đổi — guard chỉ mở cho swap.
        p = linux._normalize_motion_params({"preset": "drv-5s"})
        self.assertIs(p["fitDriver"], False)
        self.assertIs(p["fit_driver"], False)

    def test_swap_van_tat_fit_driver_duoc_bang_tay(self):
        # Ai muốn ép khung 9:16 từ driver 3:4 vẫn làm được — guard không cướp quyền đó.
        p = linux._normalize_motion_params({"preset": "drv-5s", "_swapEngine": "wananimate",
                                            "fitDriver": False})
        self.assertIs(p["fitDriver"], False)

    def test_wananimate_giu_relight_pose_face_1(self):
        p = self._normalized_params("wananimate")
        wf = _apply_swap_to_wan_workflow(build_wan_workflow("ref.png", "drv.mp4", p), p)
        self.assertEqual(wf["40"]["inputs"]["strength_0"], 1.0)        # relight LoRA
        self.assertEqual(wf["81"]["inputs"]["pose_strength"], 1.0)     # WanVideoAnimateEmbeds
        self.assertEqual(wf["81"]["inputs"]["face_strength"], 1.0)


if __name__ == "__main__":
    unittest.main()
