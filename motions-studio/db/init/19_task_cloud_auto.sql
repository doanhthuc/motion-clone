-- #region ALD 16/07/2026 - Task Cloud Auto chạy nền, độc lập trình duyệt.
CREATE TABLE IF NOT EXISTS task_cloud_auto_settings (
  singleton_id    boolean PRIMARY KEY DEFAULT true CHECK (singleton_id = true),
  enabled         boolean NOT NULL DEFAULT false,
  state           text NOT NULL DEFAULT 'off',
  active_task_id  text,
  active_task_code text,
  last_error      text,
  heartbeat_at    timestamptz,
  updated_by      uuid REFERENCES users(id) ON DELETE SET NULL,
  updated_at      timestamptz NOT NULL DEFAULT now()
);

INSERT INTO task_cloud_auto_settings (singleton_id)
VALUES (true)
ON CONFLICT (singleton_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS task_cloud_auto_jobs (
  task_id         text PRIMARY KEY,
  task_code       text,
  task_type       text,
  task_payload    jsonb NOT NULL DEFAULT '{}'::jsonb,
  workflow_id     uuid REFERENCES workflows(id) ON DELETE SET NULL,
  run_id          uuid REFERENCES workflow_runs(id) ON DELETE SET NULL,
  status          text NOT NULL DEFAULT 'claimed',
  attempts        integer NOT NULL DEFAULT 0,
  last_error      text,
  claimed_at      timestamptz NOT NULL DEFAULT now(),
  started_at      timestamptz,
  finished_at     timestamptz,
  updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_task_cloud_auto_jobs_status
  ON task_cloud_auto_jobs (status, updated_at);
-- #endregion
