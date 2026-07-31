-- ALD 13/06/2026 - Thư viện "Giọng nói": admin upload file mẫu MP3/WAV qua /settings → lưu MinIO
-- (voices/<id>.<ext>) + 1 dòng DB. viXTTS clone giọng TỪ file mẫu này lúc TTS (không cần clone trước
-- trên GPU) → mọi node có giọng (talk/teaser/story-film) chọn từ list này. Worker tải object về tmp
-- rồi truyền local path làm 'ref' cho service viXTTS (service đọc path local cùng host).
-- Idempotent: chạy lại an toàn (CREATE ... IF NOT EXISTS) — re-run mỗi lần boot qua migrate.js.

CREATE TABLE IF NOT EXISTS voices (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      uuid,
  name         text NOT NULL,                 -- tên giọng (hiển thị ở picker)
  storage_key  text NOT NULL,                 -- key file ref audio trên MinIO (voices/<id>.<ext>)
  mime         text,
  size_bytes   bigint,
  duration_sec numeric,                        -- thời lượng (giây) — best-effort qua ffprobe
  gender       text,                           -- male | female | unknown (tùy chọn)
  is_active    boolean NOT NULL DEFAULT true,
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS voices_active_idx ON voices (is_active, created_at DESC);
