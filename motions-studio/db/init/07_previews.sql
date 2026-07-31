-- #region ALD 04/06/2026 - Cột previews cho jobs (THIẾU migration khi ship feature "preview từng bước").
-- Worker đẩy ảnh preview TỪNG BƯỚC (story-film: chân dung nhân vật chính/phụ + từng cảnh; create-image…)
-- → API append vào jobs.previews[] (POST /worker/jobs/:id/preview, xem routes/jobs.js); mediaViaJob poll
-- previews → emit event có thumbnail → FE hiện step-by-step trên timeline.
-- Trước đây handlers.js SELECT cột previews nhưng cột chưa tồn tại → mọi media job (create-image/teaser/
-- motion/tryon) vỡ "column previews does not exist". Bổ sung cột (idempotent, chạy lại an toàn).
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS previews jsonb NOT NULL DEFAULT '[]'::jsonb;
-- #endregion
