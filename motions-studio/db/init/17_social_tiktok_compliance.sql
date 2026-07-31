-- #region ALD 05/07/2026 - Tuân thủ TikTok Content Sharing Guidelines (Direct Post API): lưu lựa chọn THẬT của
-- user cho từng bài đăng TikTok (privacy, tương tác Duet/Comment/Stitch, khai báo nội dung thương mại) thay vì
-- hardcode trong code — bắt buộc theo mục "Required UX Implementation" của guidelines. Idempotent.
ALTER TABLE social_posts ADD COLUMN IF NOT EXISTS privacy_level text;             -- vd 'SELF_ONLY' | 'PUBLIC_TO_EVERYONE' | 'MUTUAL_FOLLOW_FRIENDS' — user tự chọn, KHÔNG mặc định
ALTER TABLE social_posts ADD COLUMN IF NOT EXISTS disable_duet boolean NOT NULL DEFAULT true;      -- mặc định TẮT (disable=true) — user tự bật (uncheck) nếu muốn cho phép
ALTER TABLE social_posts ADD COLUMN IF NOT EXISTS disable_comment boolean NOT NULL DEFAULT true;
ALTER TABLE social_posts ADD COLUMN IF NOT EXISTS disable_stitch boolean NOT NULL DEFAULT true;
ALTER TABLE social_posts ADD COLUMN IF NOT EXISTS brand_content_toggle boolean NOT NULL DEFAULT false;   -- "Branded Content" (quảng bá bên thứ 3) — mặc định TẮT
ALTER TABLE social_posts ADD COLUMN IF NOT EXISTS brand_organic_toggle boolean NOT NULL DEFAULT false;   -- "Your Brand" (quảng bá chính mình) — mặc định TẮT
-- #endregion
