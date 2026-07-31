-- #region ALD 31/05/2026 - Storage files index (thay Supabase Storage buckets).
-- File blob ở MinIO (key = "<bucket>/<path>"); bảng này index để list/stats/delete nhanh.
CREATE TABLE IF NOT EXISTS storage_files (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  bucket      text NOT NULL,                 -- chat-attachments | motion-jobs | ...
  path        text NOT NULL,                 -- đường dẫn trong bucket (vd wf-upload/uuid.png)
  storage_key text NOT NULL,                 -- key thật trong MinIO = bucket/path
  name        text,
  size_bytes  bigint,
  mime        text,
  user_id     uuid,
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (bucket, path)
);
CREATE INDEX IF NOT EXISTS storage_bucket_idx ON storage_files (bucket, created_at DESC);
CREATE INDEX IF NOT EXISTS storage_user_idx   ON storage_files (user_id);
-- #endregion
