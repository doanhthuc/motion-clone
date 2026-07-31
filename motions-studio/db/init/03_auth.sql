-- #region ALD 31/05/2026 - Auth standalone (thay Supabase): users + sessions + otp.
-- OTP lưu Postgres (khỏi cần Redis). Idempotent.

CREATE TABLE IF NOT EXISTS users (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email         text UNIQUE NOT NULL,
  full_name     text,
  role          text NOT NULL DEFAULT 'staff',   -- admin | staff
  is_active     boolean NOT NULL DEFAULT true,
  department    text,
  title         text,
  hr_code       text,
  avatar_url    text,
  last_login_at timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS user_sessions (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id             uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash          text NOT NULL,              -- sha256(access JWT)
  refresh_token_hash  text,                       -- sha256(refresh opaque)
  user_agent          text,
  ip                  text,
  expires_at          timestamptz NOT NULL,
  refresh_expires_at  timestamptz,
  revoked_at          timestamptz,
  last_seen_at        timestamptz,
  created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sessions_token_idx   ON user_sessions (token_hash);
CREATE INDEX IF NOT EXISTS sessions_refresh_idx ON user_sessions (refresh_token_hash);

-- OTP (1 dòng/email) + đếm rate-limit trong cửa sổ 1h.
CREATE TABLE IF NOT EXISTS otp_codes (
  email        text PRIMARY KEY,
  code         text NOT NULL,
  expires_at   timestamptz NOT NULL,
  sent_count   int NOT NULL DEFAULT 1,            -- số OTP fresh trong window
  window_start timestamptz NOT NULL DEFAULT now(),
  created_at   timestamptz NOT NULL DEFAULT now()
);

-- API keys (external integration) — giữ tương thích extractSession.
CREATE TABLE IF NOT EXISTS api_keys (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  key          text UNIQUE NOT NULL,
  user_id      uuid REFERENCES users(id) ON DELETE CASCADE,
  is_active    boolean NOT NULL DEFAULT true,
  expires_at   timestamptz,
  revoked_at   timestamptz,
  last_used_at timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now()
);
-- #endregion
