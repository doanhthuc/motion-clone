-- #region ALD 05/07/2026 - Cấu hình App Facebook/TikTok (Client ID/Secret) nhập trực tiếp qua UI (Social
-- Management > Help), thay vì bắt buộc sửa .env server. Bảng singleton (1 dòng, id cố định=1) — dùng CHUNG
-- cho mọi user (1 app Facebook/TikTok đại diện cả team, giống model hiện tại của Pebsteel). Idempotent.
CREATE TABLE IF NOT EXISTS social_app_config (
  id                    smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  facebook_app_id       text,
  facebook_app_secret   text,
  tiktok_client_key     text,
  tiktok_client_secret  text,
  updated_at            timestamptz NOT NULL DEFAULT now(),
  updated_by            uuid REFERENCES users(id) ON DELETE SET NULL
);
-- #endregion
