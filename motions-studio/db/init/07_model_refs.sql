-- ALD 10/06/2026 - Thư viện "Model mẫu" cho create-image. Admin upload ảnh người mẫu qua /settings,
-- API tự xóa nền + lưu MinIO, phân loại gender × age_group. Worker create-image bốc NGẪU NHIÊN 1 mẫu
-- trong pool theo category cho từng output → mẫu đa dạng (hết "10 tấm na ná nhau").
-- Idempotent: chạy lại an toàn (CREATE ... IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS model_refs (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  gender      text NOT NULL,                 -- female | male
  age_group   text NOT NULL,                 -- young | middle | old
  label       text,                          -- ghi chú tên mẫu (tùy chọn)
  storage_key text NOT NULL,                 -- key ảnh ĐÃ XÓA NỀN trên MinIO (model-refs/<id>.jpg)
  orig_key    text,                          -- key ảnh gốc (tùy chọn, để xử lý lại)
  mime        text,
  size_bytes  bigint,
  width       int,
  height      int,
  is_active   boolean NOT NULL DEFAULT true,
  user_id     uuid,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS model_refs_cat_idx ON model_refs (gender, age_group, is_active);
