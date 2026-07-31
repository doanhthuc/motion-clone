-- #region ALD 13/07/2026 - Chỉ giữ một node video pose-driven: Motion Transfer.
-- Ẩn workflow legacy có node gộp Try-on + Wan; người dùng vẫn dựng cùng luồng rõ ràng
-- bằng các node Try-on → Motion Transfer. Giữ row/history để không xoá dữ liệu đã chạy.
UPDATE workflows
SET is_active = false,
    is_public = false,
    updated_at = now()
WHERE slug IN ('fashion-motion', 'linux-fashion-motion', 'macos-fashion-motion')
   OR EXISTS (
     SELECT 1
     FROM jsonb_array_elements(COALESCE(definition->'nodes', '[]'::jsonb)) AS node
     WHERE COALESCE(node->>'type', node#>>'{data,type}') = 'fashion-motion'
   );

UPDATE jobs
SET status = 'cancelled',
    error = COALESCE(error, 'Node legacy đã được thay bằng Try-on → Motion Transfer'),
    finished_at = COALESCE(finished_at, now()),
    updated_at = now()
WHERE type = 'fashion-motion'
  AND status IN ('queued', 'running');
-- #endregion
