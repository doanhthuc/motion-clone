-- #region ALD 06/06/2026 - Seed workflow MẪU "Người mẫu đọc kịch bản" (node face-motion) cho 1 user.
-- Manual only: KHÔNG đặt file này trong db/init. Chỉ chạy khi thật sự muốn import workflow mẫu.
--   • INSERT ... SELECT từ users theo email → user CHƯA tồn tại thì KHÔNG chèn gì (không lỗi), deploy sau
--     khi user đã đăng ký sẽ tự tạo.
--   • ON CONFLICT (user_id, slug) DO NOTHING → seed 1 LẦN; KHÔNG đè khi user đã sửa script/upload ảnh sau đó.
-- Graph: Input Image (ảnh người mẫu, upload trong Inspector) → face-motion (kịch bản + phong thái + giọng) → Output.
INSERT INTO workflows (user_id, slug, name, description, definition, is_active)
SELECT u.id,
       'nguoi-mau-doc-kich-ban',
       'Người mẫu đọc kịch bản',
       'Ảnh người mẫu + kịch bản → người mẫu đọc, nhép miệng đúng khẩu hình KÈM hành động/biểu cảm tự nhiên theo phong thái (Hồn nhiên/Trong sáng/Mạnh mẽ/Cá tính).',
       $json$
{
  "nodes": [
    {
      "id": "in-anh-mau",
      "type": "input",
      "position": { "x": 80, "y": 200 },
      "data": { "config": { "contentType": "image", "source": "static", "field": "image", "staticData": "", "staticMime": "", "staticName": "" } }
    },
    {
      "id": "nguoi-mau",
      "type": "face-motion",
      "position": { "x": 440, "y": 200 },
      "data": { "config": { "script": "Xin chào mọi người! Hôm nay mình rất vui được gặp các bạn. Hãy cùng mình khám phá những điều thú vị nhé!", "demeanor": "hon-nhien", "voice": "gemini:Aoede", "prompt": "", "fps": 25, "quality": "720p" } }
    },
    {
      "id": "ket-qua",
      "type": "output",
      "position": { "x": 800, "y": 200 },
      "data": { "config": { "format": "markdown" } }
    }
  ],
  "edges": [
    { "id": "e-anh-nguoimau", "source": "in-anh-mau", "target": "nguoi-mau", "sourceHandle": null, "targetHandle": null },
    { "id": "e-nguoimau-out", "source": "nguoi-mau", "target": "ket-qua", "sourceHandle": null, "targetHandle": null }
  ]
}
$json$::jsonb,
       true
FROM users u
WHERE lower(u.email) = lower('admin-mau@example.com')
ON CONFLICT (user_id, slug) DO NOTHING;
-- #endregion
