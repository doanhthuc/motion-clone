-- #region ALD 05/07/2026 - social_accounts: Facebook Page / TikTok account đã connect (OAuth) theo user, dùng
-- để node "output" tự đăng bài khi workflow ra kết quả (video/ảnh). access_token/refresh_token lưu PLAINTEXT
-- giống pattern ai_provider_keys hiện có (mask khi trả về FE qua route, không encrypt riêng — đồng bộ convention
-- code hiện tại). Idempotent.
CREATE TABLE IF NOT EXISTS social_accounts (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id           uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  platform          text NOT NULL,              -- 'facebook' | 'tiktok'
  external_id       text NOT NULL,               -- FB page id | TikTok open_id
  name              text,                        -- Page name | TikTok display_name
  avatar_url        text,
  access_token      text NOT NULL,               -- FB: page access token · TikTok: user access token (24h)
  refresh_token     text,                         -- TikTok only (~365 ngày)
  token_expires_at  timestamptz,                  -- TikTok only; FB page token coi như không hết hạn
  meta              jsonb NOT NULL DEFAULT '{}'::jsonb, -- FB: {user_access_token}. TikTok: {privacy_level}
  is_active         boolean NOT NULL DEFAULT true,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, platform, external_id)
);
CREATE INDEX IF NOT EXISTS social_accounts_user_idx ON social_accounts (user_id, platform) WHERE is_active = true;
-- #endregion
