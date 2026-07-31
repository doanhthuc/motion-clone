-- #region ALD 15/06/2026 - Seed workflow MẪU "Dịch phụ đề video" (node subtitle).
-- Manual only: KHÔNG đặt trong db/init. Chỉ chạy khi muốn import workflow mẫu.
--   • INSERT ... SELECT từ users theo email admin → user chưa tồn tại thì KHÔNG chèn (không lỗi).
--   • is_public=true → mọi user thấy & chạy (đọc-chỉ, tự upload video của mình khi chạy).
--   • ON CONFLICT (user_id, slug) DO NOTHING → seed 1 LẦN, không đè khi đã sửa.
-- Graph: Input Video (upload trong Inspector) → subtitle (dịch sang Việt, cháy phụ đề) → Output.
-- ĐỔI email bên dưới cho khớp 1 admin có thật trên hệ thống nếu cần.
INSERT INTO workflows (user_id, slug, name, description, definition, is_active, is_public)
SELECT u.id,
       'dich-phu-de-video',
       'Dịch phụ đề video',
       'Input 1 video → tự nhận lời thoại (OmniVoice ASR) + dịch → cháy phụ đề đã dịch vào video (giữ tiếng gốc). Dịch từng câu hiện realtime.',
       $json$
{
  "nodes": [
    {
      "id": "in-video",
      "type": "input",
      "position": { "x": 80, "y": 200 },
      "data": { "config": { "contentType": "video", "source": "static", "field": "video", "staticData": "", "staticMime": "", "staticName": "" } }
    },
    {
      "id": "phu-de",
      "type": "subtitle",
      "position": { "x": 440, "y": 200 },
      "data": { "config": { "targetLang": "vi", "bilingual": false, "asrModel": "medium", "fontSize": 18, "position": "bottom" } }
    },
    {
      "id": "ket-qua",
      "type": "output",
      "position": { "x": 800, "y": 200 },
      "data": { "config": { "format": "markdown" } }
    }
  ],
  "edges": [
    { "id": "e-video-sub", "source": "in-video", "target": "phu-de", "sourceHandle": null, "targetHandle": null },
    { "id": "e-sub-out",   "source": "phu-de",   "target": "ket-qua", "sourceHandle": null, "targetHandle": null }
  ]
}
$json$::jsonb,
       true,
       true
FROM users u
WHERE lower(u.email) = lower('ald@pebsteel.com.vn')
ON CONFLICT (user_id, slug) DO NOTHING;
-- #endregion
