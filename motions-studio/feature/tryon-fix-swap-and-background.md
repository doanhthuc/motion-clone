# Try-On: fix "toàn không thay đồ" + implement "Ghép nền" (16/08/2026)

User báo: thử đồ **không thay đồ cho mẫu** và **không ghép background**. Đào DB pod (restore từ volume) thấy
3 run thật sáng 16/08 (`5d4fff9d` upper+nền, `b7710459`/`3271d27b` auto+nền) — cả 3 job `done` nhưng output
giữ nguyên áo camisole gốc + nền studio gốc.

## Bug 1 — không thay đồ: lock-trước-lệnh giết lệnh chính

**Tái hiện có kiểm soát** trên pod (ComfyUI trực tiếp, đúng ảnh user, seed 42):

| Prompt | Kết quả |
|---|---|
| A. hiện trạng: `FACE_LOCK + PROP_LOCK + HAIR_LOCK + SRC_LOCK + lệnh thay đồ` (~2.430 ký tự) | ❌ giữ nguyên áo gốc |
| B. CÙNG nội dung, đảo lệnh thay đồ lên ĐẦU | ✅ thay đúng blouse |
| C. lệnh ĐẦU + 1 khối lock GỌN (~840 ký tự) | ✅ thay đúng, tóc/mặt giữ tốt hơn |

Nguyên nhân: các bản vá identity 15/06→20/07 dồn 4 khối "CRITICAL … keep EXACTLY as image 1" lên **trước**
lệnh thay đồ → prompt mở đầu bằng ~1.600 ký tự "giữ nguyên hệt" → Qwen tái tạo lại ảnh 1, lệnh thay đồ ở cuối
bị loãng (đúng bệnh "LOÃNG LỆNH" đã ghi trong comment 20/07 nhưng khi đó chỉ rút gọn phần thân, giữ nguyên locks).

**Fix** (`_qwen_tryon_prompts`): lệnh thay đồ theo loại đồ đứng ĐẦU → ghi chú user → `_COMPACT_LOCK` (mặt nét/
tóc/tỉ lệ/bỏ-người-mặc-trong-ảnh-SP) đứng CUỐI. **Negative giữ nguyên 100%** (vẫn mã hoá mọi failure mode cũ:
đổi tóc 15/06, lệch tỉ lệ 21/06, đổi mặt 04/07, hút tóc người mặc 06/07, mặt mờ 12/07, đầu nhỏ 20/07).

## Bug 2 — Ghép nền chưa từng có ở worker

FE (`InspectorTryon` toggle "Ghép nền" → cổng `Nền`, FlowNode hứa "worker ghép người vào bối cảnh (Qwen pass 2)")
gửi `inputs.background` nhưng `run_tryon` không đọc → ảnh nền bị bỏ qua âm thầm, job vẫn `done`.

**Fix**: pass 2 `_tryon_compose_background` — image1 = người ĐÃ thay đồ, image2 = ảnh nền, tái dùng
`build_qwen_create_workflow` (đường ghép người-vào-cảnh đã chạy thật ở teaser TẦNG 2A). Áp cho cả 3 nhánh
provider (qwen / gemini / HF ngủ đông). Pass 2 lỗi → job lỗi RÕ (không âm thầm trả nền cũ — đó chính là bug).

## Bug 3 (phát hiện kèm) — FE/worker lệch contract sau "Try-On tối giản" 20/07

- `cleanOnly` ("Chỉ làm sạch ảnh" + template Singer đa-outfit) bị worker bỏ → job lỗi "cần inputs.product".
  **Khôi phục** nhánh tối thiểu: img2img Qwen `TRYON_CLEAN_DENOISE=0.3` trên chính ảnh model.
- "2 ảnh đa góc" (productCount=2): worker chỉ nhận 1 ảnh SP → ảnh SP 2 bị bỏ qua âm thầm. **Khôi phục
  end-to-end theo yêu cầu user** (đảo quyết định "tối giản" 20/07): `inputs.product2` → Qwen `image3`
  (cả encoder pos/neg) + câu prompt nói rõ image3 = CÙNG sản phẩm góc khác, không phải món thêm; nhánh
  Gemini gửi cả 2 ảnh, nhánh HF gửi `image_urls` 3 phần tử.

## Verify (pod 5090, qua API thật)

- `auto` + background → thay nguyên bộ + người đứng trong phòng khách sạn (pass 2).
- `upper` (đúng case fail của user) → thay blouse, giữ váy/tóc/mặt/nền.
- `cleanOnly` → ảnh sạch, không lỗi product.
