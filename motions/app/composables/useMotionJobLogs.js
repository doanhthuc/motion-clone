// #region ALD 23/05/2026 - Stream log realtime cho 1 motion job.
// ALD 31/05/2026 - motion-backend không có SSE log → POLL /api/motion/jobs/:id/logs?since= mỗi 2s
// (proxy same-origin, không Bearer). Interface giữ nguyên { logs, connected, error, start, stop, reset }.
export function useMotionJobLogs() {
  const logs = ref([])             // [{ id, ts, level, message, progress }]
  const connected = ref(false)
  const error = ref('')
  const lastTs = ref('')

  let pollTimer = null
  let currentJobId = null

  function reset() {
    logs.value = []
    lastTs.value = ''
    error.value = ''
    connected.value = false
  }

  function _push(entry) {
    if (!entry?.ts) return
    if (entry.id && logs.value.some((l) => l.id === entry.id)) return
    logs.value.push(entry)
    if (entry.ts > lastTs.value) lastTs.value = entry.ts
    if (logs.value.length > 500) logs.value.splice(0, logs.value.length - 500)
  }

  async function _pollOnce(jobId) {
    try {
      const qs = lastTs.value ? `?since=${encodeURIComponent(lastTs.value)}` : ''
      const data = await $fetch(`/api/motion/jobs/${encodeURIComponent(jobId)}/logs${qs}`)
      connected.value = true
      for (const r of data.logs || []) _push(r)
    } catch (err) {
      error.value = err?.message || 'log poll error'
      console.warn('[motion-logs] poll error:', err)
    }
  }

  function start(jobId) {
    if (currentJobId === jobId && pollTimer) return
    stop()
    currentJobId = jobId
    reset()
    _pollOnce(jobId)
    pollTimer = setInterval(() => _pollOnce(jobId), 2000)
  }

  function stop() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
    connected.value = false
    currentJobId = null
  }

  return { logs, connected, error, start, stop, reset }
}
// #endregion
