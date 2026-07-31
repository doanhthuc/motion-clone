-- #region ALD 05/07/2026 - Public Base URL (domain HTTPS server) cho luồng Connect Facebook/TikTok cũng
-- CHUYỂN vào DB, nhập qua UI (Social Management > Help), KHÔNG còn đọc process.env.PUBLIC_BASE_URL cho
-- việc tính redirect URI OAuth nữa — đồng bộ tuyệt đối với App ID/Secret/Client Key/Secret (không có biến
-- "connect" nào nằm trong .env). Lưu ý: biến process.env.PUBLIC_BASE_URL trong .env.example VẪN còn, nhưng
-- giờ chỉ phục vụ mục đích khác (tạo link tải media qua API, xem api/src/storage.js) — không liên quan
-- Social Management nữa. Idempotent.
ALTER TABLE social_app_config ADD COLUMN IF NOT EXISTS public_base_url text;
-- #endregion
