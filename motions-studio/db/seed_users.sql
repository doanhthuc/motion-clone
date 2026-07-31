-- #region ALD 31/05/2026 - Seed users (chạy tay, idempotent). role='user' = tài khoản thường (không phải admin).
-- Áp dụng trên DB đang chạy:
--   docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" < db/seed_users.sql
-- Email để chữ thường (route auth normalize lowercase). Chạy lại nhiều lần vẫn an toàn.

INSERT INTO users (email, role, is_active) VALUES
  ('user-mau-1@example.com',  'user', true),
  ('user-mau-1@example.com',  'user', true)
ON CONFLICT (email) DO UPDATE
  SET role = EXCLUDED.role,
      is_active = true;
-- #endregion
