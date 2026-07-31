# Try-On nguyên bộ — lệch tỉ lệ đầu/thân (đã test .165)

**Nhánh:** `tryon-fulloutfit-proportion`. Vấn đề: đầm/set/auto/reveal đi **full-regen** `denoise=1.0` (để outfit
bám đúng thiết kế SP) → Qwen vẽ lại cả người → tỉ lệ đầu/thân trôi nhẹ (đầu hơi nhỏ, thân/chân dài hơn ảnh gốc).

## Đã test A/B trên .165 (isolated, không đụng worker prod)
Cùng model + product (đầm hồng cold-shoulder) + seed 42:

| Nhánh | Tỉ lệ | Outfit | Kết luận |
|---|---|---|---|
| baseline (full-regen 1.0) | hơi kéo dài | ✅ đúng | hiện trạng |
| **knob B** (full-regen + PROP_LOCK mạnh) | nhỉnh hơn chút | ✅ đúng | **CHỌN** — zero-risk, cải thiện nhẹ |
| knob A (img2img 0.9) | ✅ chuẩn | ❌ giữ chân váy gốc, không thay hết đồ | **LOẠI** |

→ **knob A (img2img cho nguyên bộ) bị loại**: giữ tỉ lệ tốt nhưng neo ảnh gốc → outfit không thay hết (đã xác
nhận 2 lần). Full-regen cho nguyên bộ vẫn đúng (commit c625781).

## Thay đổi cuối (đã áp mặc định, không env)
`worker/worker_runtime/linux.py`:
- **PROP_LOCK mạnh hơn** (mốc 7–7.5 head-heights, cấm đầu-nhỏ/chân-dài) + **đưa PROP_LOCK lên vị trí 2** (ngay sau
  FACE_LOCK, trước HAIR) vì ở denoise=1.0 câu giữa prompt bị đè.
- `preserve` trả về nguyên bản (`not _full_outfit`) — không còn knob img2img cho nguyên bộ.

## Follow-up (nếu muốn trị dứt điểm)
**ControlNet OpenPose**: trích xương từ ảnh model → ép Qwen full-regen bám đúng khung xương (vừa đúng outfit vừa
khóa tỉ lệ). Box đã có sẵn `comfyui_controlnet_aux`; cần thêm ControlNet model Qwen + wiring node. Chưa làm.

## Merge
Sẵn sàng merge `tryon-fulloutfit-proportion` → `development` (chỉ còn thay đổi prompt PROP_LOCK, zero-risk).
