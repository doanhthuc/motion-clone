-- #region ALD 12/06/2026 - Cột outputs cho jobs (THIẾU migration khi ship feature "nhiều output/biến thể").
-- API append vào jobs.outputs[] khi worker POST /jobs/:id/output (mỗi style/biến thể 1 entry {key,label,mime}),
-- xem routes/jobs.js (~dòng 188: outputs = COALESCE(outputs,'[]'::jsonb) || $1::jsonb). Schema gốc chỉ có
-- output_key (1 file) → trên DB MỚI mọi job sinh file (create-image/tryon/motion/teaser…) vỡ ở bước cuối
-- "column outputs does not exist": render + upload MinIO XONG nhưng job kẹt 0.95 không finalize được.
-- Bổ sung cột (idempotent, chạy lại an toàn — giống 07_previews.sql).
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS outputs jsonb NOT NULL DEFAULT '[]'::jsonb;
-- #endregion
