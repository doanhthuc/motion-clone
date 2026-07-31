-- #region ALD 31/05/2026 - Workflows + runs (port từ supabase 009_workflows.sql). Idempotent.
CREATE TABLE IF NOT EXISTS workflows (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  slug            text NOT NULL,
  name            text NOT NULL,
  description     text,
  definition      jsonb NOT NULL DEFAULT '{"nodes":[],"edges":[]}'::jsonb,
  is_public       boolean NOT NULL DEFAULT false,
  is_active       boolean NOT NULL DEFAULT true,
  async_enabled   boolean NOT NULL DEFAULT false,
  callback_url    text,
  callback_headers jsonb,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, slug)
);
CREATE INDEX IF NOT EXISTS idx_workflows_user   ON workflows (user_id);
CREATE INDEX IF NOT EXISTS idx_workflows_slug   ON workflows (slug);
CREATE INDEX IF NOT EXISTS idx_workflows_public ON workflows (is_public) WHERE is_public = true;
DROP TRIGGER IF EXISTS trg_workflows_updated_at ON workflows;
CREATE TRIGGER trg_workflows_updated_at BEFORE UPDATE ON workflows
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS workflow_runs (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workflow_id  uuid REFERENCES workflows(id) ON DELETE CASCADE,
  user_id      uuid REFERENCES users(id) ON DELETE SET NULL,
  auth_method  text NOT NULL DEFAULT 'session',
  status       text NOT NULL DEFAULT 'queued',  -- queued | running | success | error | cancelled
  input        jsonb,
  output       jsonb,
  events       jsonb NOT NULL DEFAULT '[]'::jsonb,
  definition   jsonb,                            -- snapshot def cho test-run (in-memory def)
  error_msg    text,
  started_at   timestamptz NOT NULL DEFAULT now(),
  finished_at  timestamptz
);
CREATE INDEX IF NOT EXISTS idx_wf_runs_wf     ON workflow_runs (workflow_id);
CREATE INDEX IF NOT EXISTS idx_wf_runs_user   ON workflow_runs (user_id);
CREATE INDEX IF NOT EXISTS idx_wf_runs_recent ON workflow_runs (started_at DESC);
CREATE INDEX IF NOT EXISTS idx_wf_runs_pick   ON workflow_runs (status, started_at);
-- #endregion
