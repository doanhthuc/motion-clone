// #region ALD 31/05/2026 - Proxy tạo motion job → motion-backend (KHÔNG còn qua Supabase).
// Nhận multipart từ FE (ref_image, motion_video, preset, params) → dịch sang hợp đồng motion-backend:
//   POST {motionApiUrl}/jobs  (X-API-Key)  type=motion + file 'ref' + file 'motion' + params(JSON).
// API key nằm server-side (runtimeConfig private) → KHÔNG lộ ra browser; cũng tránh CORS (same-origin).
import { presetToParams } from '~~/shared/utils/motionPresets.js'

function normalizeMotionDriverSegment(params) {
  const out = { ...(params || {}) }
  const start = Number(out.driverStartSec ?? out.driver_start_sec ?? 0)
  const dur = Number(out.driverDurSec ?? out.driver_dur_sec ?? 0)
  if (!Number.isFinite(start) || !Number.isFinite(dur) || dur <= 0) return out

  // ALD 28/06/2026 - Legacy multi-outfit stored driverDurSec as endSec:
  // node2 5/10 meant 5..10s, but worker expects duration and rendered 5..15s.
  if (start > 0 && Math.abs(dur - (start + 5)) < 0.001) {
    out.driverDurSec = 5
    out.driver_dur_sec = 5
  }
  const fixedDur = Number(out.driverDurSec ?? out.driver_dur_sec ?? dur)
  if (Math.abs(fixedDur - 5) < 0.001 && start % 5 === 0) {
    out.preset = '5s-720p'
    out.frames = 81
    out.steps = Number(out.steps || 4)
    out.skipFirstFrames = 0
    out.skip_first_frames = 0
    out.faceSource = 'ref'
    out.faceStrength = 0.6
    out.poseStrength = 0.8
    out.motionHandFix = false
    out.matchRef = false
    out.matchRefStrength = 0
    out.brightCap = 1.0
    out.warmth = 0
    delete out.cfg
    delete out.shift
    delete out.scheduler
  }
  return out
}

export default defineEventHandler(async (event) => {
  const { motionApiUrl, motionApiKey } = useRuntimeConfig(event)
  if (!motionApiUrl || !motionApiKey) {
    throw createError({ statusCode: 500, statusMessage: 'Chưa cấu hình NUXT_MOTION_API_URL / NUXT_MOTION_API_KEY' })
  }

  const parts = await readMultipartFormData(event)
  if (!parts?.length) throw createError({ statusCode: 400, statusMessage: 'Thiếu multipart body' })

  const fd = new FormData()
  fd.append('type', 'motion')
  let preset = 'drv-15s'  // ALD 11/07/2026 - default driver-native (fps/frame theo driver); FE gửi preset thật vẫn override
  let params = {}
  let hasRef = false
  let hasMotion = false

  for (const part of parts) {
    if (part.name === 'ref_image') {
      fd.append('ref', new Blob([part.data], { type: part.type || 'image/png' }), part.filename || 'ref.png')
      hasRef = true
    } else if (part.name === 'motion_video') {
      fd.append('motion', new Blob([part.data], { type: part.type || 'video/mp4' }), part.filename || 'motion.mp4')
      hasMotion = true
    } else if (part.name === 'preset') {
      preset = part.data.toString('utf8')
    } else if (part.name === 'params') {
      try { params = JSON.parse(part.data.toString('utf8')) } catch { /* bỏ qua params hỏng */ }
    }
  }
  if (!hasRef || !hasMotion) throw createError({ statusCode: 400, statusMessage: 'Cần ref_image + motion_video' })

  // preset → params; FE params override preset
  const normalizedParams = normalizeMotionDriverSegment({ ...params, preset: params?.preset || preset })
  const mergedPreset = normalizedParams.preset || preset
  // ALD 13/07/2026 - Chỉ phát hành profile Fast: LightX2V 4 bước. Ghi đè sau params để
  // workflow cũ từng lưu Natural/HQ/20 steps cũng tự lành, không âm thầm render chậm.
  const merged = {
    ...presetToParams(mergedPreset),
    ...normalizedParams,
    preset: mergedPreset,
    renderProfile: 'fast',
    render_profile: 'fast',
    hq: false,
    steps: 4,
    cfg: 1,
    scheduler: 'dpm++_sde',
    loraLightx2v: 1,
    lora_lightx2v: 1,
  }
  fd.append('params', JSON.stringify(merged))

  try {
    const res = await $fetch(`${motionApiUrl.replace(/\/$/, '')}/jobs`, {
      method: 'POST',
      body: fd,
      headers: { 'X-API-Key': motionApiKey }
    })
    return res // { id, status, ... }
  } catch (err) {
    throw createError({
      statusCode: err?.response?.status || 502,
      statusMessage: err?.data?.error || err?.message || 'motion-backend lỗi'
    })
  }
})
// #endregion
