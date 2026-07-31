-- #region ALD 31/05/2026 - AI provider keys (per-user) + OCR cache (cho node chat/ocr). Idempotent.
CREATE TABLE IF NOT EXISTS ai_provider_keys (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  provider      text NOT NULL,                 -- ollama | custom
  api_key       text,
  base_url      text,
  default_model text,
  is_active     boolean NOT NULL DEFAULT true,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, provider)
);

CREATE TABLE IF NOT EXISTS ocr_cache (
  file_hash       text PRIMARY KEY,
  text            text,
  metadata        jsonb,
  file_name       text,
  file_size_bytes bigint,
  mime_type       text,
  hit_count       int NOT NULL DEFAULT 1,
  created_at      timestamptz NOT NULL DEFAULT now(),
  accessed_at     timestamptz NOT NULL DEFAULT now()
);
-- #endregion
