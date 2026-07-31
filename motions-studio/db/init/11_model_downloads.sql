-- ALD 15/06/2026 - Tab Settings → "Models AI": cài model on-demand (thay tải ở setup).
--   model_downloads     : theo dõi tiến trình tải nền (ComfyUI aria2c / Ollama pull) để UI poll.
--   model_catalog_custom: model admin tự thêm qua UI (URL) ngoài catalog.json curated trong repo.
-- Idempotent: CREATE ... IF NOT EXISTS — chạy mỗi lần boot qua migrate.js (runMigrations).

CREATE TABLE IF NOT EXISTS model_downloads (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kind         text NOT NULL,                 -- comfy | ollama
  ref          text NOT NULL,                 -- comfy: filename trên đĩa · ollama: tên model (vd qwen2.5:7b-instruct)
  type         text,                          -- comfy: subdir đích (loras|checkpoints|unet|vae|text_encoders|clip_vision|diffusion_models)
  url          text,                          -- comfy: URL tải · ollama: null
  catalog_id   text,                          -- id entry trong catalog.json (nếu cài từ catalog)
  status       text NOT NULL DEFAULT 'queued',-- queued | running | done | error | cancelled
  total_bytes  bigint,
  done_bytes   bigint NOT NULL DEFAULT 0,
  error        text,
  started_by   uuid,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now()
);

-- 1 model đang tải/hoàn tất chỉ 1 dòng "đang sống" (queued|running) — tránh tải trùng song song.
CREATE UNIQUE INDEX IF NOT EXISTS model_downloads_active_uq
  ON model_downloads (kind, ref)
  WHERE status IN ('queued', 'running');
CREATE INDEX IF NOT EXISTS model_downloads_status_idx ON model_downloads (status, created_at DESC);

CREATE TABLE IF NOT EXISTS model_catalog_custom (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kind        text NOT NULL DEFAULT 'comfy',  -- comfy | ollama
  type        text,                           -- comfy: subdir đích
  filename    text,                           -- comfy: tên file lưu trên đĩa
  model       text,                           -- ollama: tên model
  url         text,                           -- comfy: URL
  label       text,
  note        text,
  size_bytes  bigint,
  added_by    uuid,
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (kind, filename, model)
);
