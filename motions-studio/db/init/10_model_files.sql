-- ALD 14/06/2026 - "Model AI (custom)": admin upload file model tự train (LoRA/checkpoint/unet/vae/
-- text-encoder/clip-vision) qua /settings → lưu THẲNG xuống đĩa trong search-path ComfyUI
-- (~/ai/ComfyUI/models/uploads/<type>/<filename>, đăng ký qua extra_model_paths.yaml) — KHÔNG vào MinIO
-- vì ComfyUI chỉ nạp model theo tên file trên đĩa. Bảng này chỉ giữ METADATA để list/xoá ở UI (dọn dẹp).
-- Node "SS" (LTX-2.3 I2V) chọn LoRA từ list type='loras'. Gom 1 nơi (folder uploads/) → dễ dọn.
-- Idempotent: CREATE ... IF NOT EXISTS — re-run mỗi lần boot qua migrate.js.

CREATE TABLE IF NOT EXISTS model_files (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  type          text NOT NULL,                 -- loras | checkpoints | unet | vae | text_encoders | clip_vision (= subdir ComfyUI)
  filename      text NOT NULL,                 -- tên file trên đĩa (đã sanitize) — ComfyUI nạp theo tên này
  original_name text,                          -- tên file gốc lúc upload (hiển thị)
  size_bytes    bigint,
  sha256        text,                          -- checksum (tùy chọn — best-effort)
  note          text,                          -- ghi chú của user (vd "LoRA train phong cách X")
  uploaded_by   uuid,
  is_active     boolean NOT NULL DEFAULT true,
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (type, filename)                       -- 1 tên file / 1 loại — tránh đè nhầm
);

CREATE INDEX IF NOT EXISTS model_files_type_idx ON model_files (type, is_active, created_at DESC);
