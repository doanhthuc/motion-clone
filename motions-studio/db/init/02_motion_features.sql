-- #region ALD 31/05/2026 - Bổ sung: job logs, worker heartbeat, audio library.
-- Idempotent (IF NOT EXISTS) → chạy lại an toàn. Init dir chỉ chạy lúc DB trống;
-- DB có sẵn thì apply tay qua psql (xem deploy).

-- 1) Log chi tiết theo job (thay cho SSE log của Supabase) — worker append mỗi bước.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS logs jsonb NOT NULL DEFAULT '[]'::jsonb;

-- 2) Worker registry (heartbeat) → badge "GPU Ready" + queued/running.
CREATE TABLE IF NOT EXISTS workers (
  worker_id          text PRIMARY KEY,
  last_seen_at       timestamptz NOT NULL DEFAULT now(),
  active_job_id      uuid,
  mode               text,
  gpu_name           text,
  gpu_vram_total_mb  integer,
  meta               jsonb
);

-- 3) Audio library (thay bucket audio của Supabase Storage).
CREATE TABLE IF NOT EXISTS audio_files (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name          text NOT NULL,
  storage_key   text NOT NULL,
  size_bytes    bigint,
  duration_sec  real,
  mime          text,
  client_id     text,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS audio_recent_idx ON audio_files (created_at DESC);
-- #endregion
