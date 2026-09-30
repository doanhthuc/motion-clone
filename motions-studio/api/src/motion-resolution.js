// #region ALD 19/07/2026 - Nguồn sự thật resolution Motion theo lựa chọn chất lượng.
// Chuẩn hóa ngay ở API trước khi ghi jobs.params để worker đang chạy bản cũ cũng nhận width/height rõ ràng.
import { enforceMotionFitPolicy } from "./motion-camera-policy.js"

// Any N: the worker reads the seconds off the name (linux.py ^drv-(\d+)s$), so a fixed list would
// silently drop `quality` for a preset like drv-12s.
const DRIVER_PRESET = /^drv-\d+s$/

function round16(value) {
  return Math.max(16, Math.round(Number(value) / 16) * 16)
}

function aspectPair(value) {
  const match = String(value || "9:16").trim().match(/^(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)$/)
  if (!match) return [9, 16]
  const width = Number(match[1])
  const height = Number(match[2])
  return width > 0 && height > 0 ? [width, height] : [9, 16]
}

export function enforceMotionResolution(type, params) {
  if (type !== "motion" || !params || typeof params !== "object") return params
  const out = { ...params }
  if (!DRIVER_PRESET.test(String(out.preset || ""))) return out

  const [rw, rh] = aspectPair(out.aspectRatio || out.aspect_ratio)
  const quality = String(out.quality || "").trim().toLowerCase() === "720p" ? "720p" : "540p"
  const shortEdge = quality === "720p" ? 720 : 544
  const maxEdge = quality === "720p" ? 1280 : 968
  let width = rw <= rh ? shortEdge : shortEdge * rw / rh
  let height = rw <= rh ? shortEdge * rh / rw : shortEdge
  const scale = Math.min(1, maxEdge / Math.max(width, height))

  out.width = round16(width * scale)
  out.height = round16(height * scale)
  out.maxRenderEdge = maxEdge
  out.max_render_edge = maxEdge
  out.quality = quality
  out.resolutionPolicy = "quality-v1"
  out.resolution_policy = "quality-v1"
  return enforceMotionFitPolicy(out)
}
// #endregion
