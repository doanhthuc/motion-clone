-- ============================================================
-- Motion Backend — schema (tự chạy lần đầu Postgres init, idempotent).
-- 1 bảng jobs generic: type = motion | tryon | teaser | ... (mở rộng dễ).
--   inputs : storage keys (MinIO) — { ref, motion, models[], products[], ... }
--   params : cấu hình pipeline — { preset, garment_type, script_text, target_duration_sec, ... }
--   output_key : storage key của video/ảnh kết quả.
-- ============================================================

CREATE TABLE IF NOT EXISTS jobs (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  type          text        NOT NULL,
  status        text        NOT NULL DEFAULT 'queued',   -- queued|running|done|error|cancelled
  progress      real        NOT NULL DEFAULT 0,          -- 0..1
  current_step  text,
  inputs        jsonb       NOT NULL DEFAULT '{}'::jsonb, -- { handle: storage_key | [keys] }
  params        jsonb       NOT NULL DEFAULT '{}'::jsonb,
  output_key    text,
  error         text,
  worker_id     text,
  client_id     text,                                    -- ai tạo (optional, từ API key/app)
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  started_at    timestamptz,
  finished_at   timestamptz
);

CREATE INDEX IF NOT EXISTS jobs_pick_idx   ON jobs (status, type, created_at);
CREATE INDEX IF NOT EXISTS jobs_recent_idx ON jobs (created_at DESC);

-- Tự cập nhật updated_at mỗi lần UPDATE
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS jobs_updated_at ON jobs;
CREATE TRIGGER jobs_updated_at BEFORE UPDATE ON jobs
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();
