-- #region ALD 19/07/2026 - Worker chủ động kết nối Motion Task Cloud bằng URL + API key mã hoá.
ALTER TABLE task_cloud_auto_settings
  ADD COLUMN IF NOT EXISTS task_cloud_url text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS task_cloud_token_cipher text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS task_cloud_worker_id text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS connection_checked_at timestamptz,
  ADD COLUMN IF NOT EXISTS connection_error text;
-- #endregion
