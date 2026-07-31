// #region ALD 23/05/2026 - Composable motion-jobs (Wan 2.2 Animate qua ComfyUI worker).
// ALD 31/05/2026 - Chuyển transport: KHÔNG còn gọi Supabase edge function nữa, mà gọi proxy
// same-origin /api/motion/* (server/api/motion/) — proxy chèn X-API-Key server-side rồi forward
// sang motion-backend. Interface (create/get/cancel/list/poll/PRESETS) giữ NGUYÊN nên không caller nào phải sửa.
//   POST   /api/motion/jobs            → { id, status: 'queued' }
//   GET    /api/motion/jobs/:id        → { id, status, progress, output_url, ... }
//   DELETE /api/motion/jobs/:id        → cancel
//   GET    /api/motion/jobs?...        → { items }
//
// FE pattern: upload ảnh ref + video motion → proxy → motion-backend → FE poll status tới done/error.
import { MOTION_PRESETS } from '~~/shared/utils/motionPresets.js'

export function useMotionJobs() {
  const base = '/api/motion/jobs'

  /**
   * Tạo job mới. File upload qua multipart.
   * @param {object} opts
   * @param {File}   opts.refImage    - ảnh nhân vật reference (jpg/png)
   * @param {File}   opts.motionVideo - video chứa motion (mp4/mov, audio sẽ passthrough)
   * @param {string} opts.preset      - 'drv-5s' | 'drv-10s' | 'drv-15s' | 'drv-20s' (theo driver; fps/frame 1:1 driver)
   * @param {object} opts.params      - override: { mode, resolution, frames, steps, cfg, shift, scheduler, contextWindow, blockSwap,
   *                                     audio_replacement_id (ref vào audio-files để FFmpeg merge thay audio gốc) }
   * @returns {Promise<{ id, status: 'queued' }>}
   */
  async function create({ refImage, motionVideo, preset = 'drv-15s', params = {} } = {}) {
    if (!refImage)    throw new Error('refImage là bắt buộc')
    if (!motionVideo) throw new Error('motionVideo là bắt buộc')

    const fd = new FormData()
    fd.append('ref_image', refImage)
    fd.append('motion_video', motionVideo)
    fd.append('preset', preset)
    if (params && Object.keys(params).length) {
      fd.append('params', JSON.stringify(params))
    }
    return await $fetch(base, { method: 'POST', body: fd })
  }

  async function get(id) {
    return await $fetch(`${base}/${encodeURIComponent(id)}`)
  }

  async function cancel(id) {
    return await $fetch(`${base}/${encodeURIComponent(id)}`, { method: 'DELETE' })
  }

  /**
   * Liệt kê runs của user (admin có thể truyền userId='all').
   */
  async function list({ page = 1, limit = 20, status = '', userId = '' } = {}) {
    const p = new URLSearchParams()
    p.set('page', String(page))
    p.set('limit', String(limit))
    if (status) p.set('status', status)
    if (userId) p.set('userId', userId)
    return await $fetch(`${base}?${p.toString()}`)
  }

  /**
   * Poll trạng thái job tới khi xong. Trả controller { stop }.
   * @param {string} id
   * @param {object} cb
   * @param {(snap) => void} cb.onUpdate
   * @param {(snap) => void} cb.onDone
   * @param {number} intervalMs - mặc định 2000ms
   */
  function poll(id, { onUpdate, onDone, intervalMs = 2000 } = {}) {
    let stopped = false
    let timer = null

    async function tick() {
      if (stopped) return
      try {
        const snap = await get(id)
        if (stopped) return
        onUpdate?.(snap)
        if (snap?.status && snap.status !== 'queued' && snap.status !== 'running') {
          stopped = true
          onDone?.(snap)
          return
        }
      } catch (err) {
        if (stopped) return
        console.warn('[motion-jobs] poll error:', err)
      }
      timer = setTimeout(tick, intervalMs)
    }

    tick()
    return {
      stop: () => {
        stopped = true
        if (timer) { clearTimeout(timer); timer = null }
      }
    }
  }

  // ALD 31/05/2026 - PRESETS chuyển sang shared/utils/motionPresets.js để FE (dropdown) và
  // server proxy (đổi preset → params) dùng chung 1 nguồn, không lệch nhau.
  const PRESETS = MOTION_PRESETS

  return { create, get, cancel, list, poll, PRESETS }
}
// #endregion
