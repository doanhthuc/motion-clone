// #region ALD 20/07/2026 - Phát hiện VRAM của GPU box (dùng chung cho /workflows/capabilities GATE UI và
// hard-block tạo job Wan-Dancer). Node Wan-Dancer (music-to-dance, Wan-Dancer-14B ~2×34.5GB bf16) CHỈ chạy
// trên VPS GPU ≥ 90GB — .165 (5090 32GB) không đủ. "Không đo được" ⇒ coi như 0 ⇒ KHÓA (block mặc định).
import { query } from "./db.js"
import { readComfyGpu } from "./admin-resource-actions.js"

// Ngưỡng unlock node Wan-Dancer. Nguồn sự thật DUY NHẤT cho cả FE (qua capabilities) lẫn BE (hard-block).
export const WAN_DANCER_MIN_VRAM_GB = 90

const COMFY_URL = (process.env.ADMIN_COMFY_URL || process.env.COMFY_URL || "").replace(/\/$/, "")

// Trả VRAM tổng của GPU box (GB, số nguyên). Lấy MAX giữa:
//   1) worker tự báo qua heartbeat (workers.gpu_vram_total_mb) — phản ánh đúng GPU sẽ chạy job.
//   2) ComfyUI /system_stats (fallback khi chưa có worker báo).
// Lỗi/không có tín hiệu → 0 (an toàn: FE khóa + BE chặn).
export async function detectGpuVramGb() {
  let mb = 0
  try {
    const { rows } = await query(
      `SELECT max(gpu_vram_total_mb) AS mb FROM workers
       WHERE gpu_vram_total_mb IS NOT NULL AND last_seen_at > now() - interval '2 minutes'`)
    mb = Number(rows?.[0]?.mb || 0)
  } catch { /* bảng/cột chưa có → bỏ qua, thử ComfyUI */ }
  let gb = mb > 0 ? mb / 1024 : 0
  if (gb <= 0 && COMFY_URL) {
    try {
      const g = await readComfyGpu(COMFY_URL)
      const bytes = Number(g?.total_bytes || 0)
      if (bytes > 0) gb = bytes / (1024 ** 3)
    } catch { /* ignore */ }
  }
  return Math.round(gb)
}
// #endregion
