// #region ALD 23/05/2026 - GPU worker status poller cho app motions.
// ALD 31/05/2026 - Chuyển sang motion-backend qua proxy same-origin /api/motion/status
// (không còn /functions/v1/motion-worker-status của Supabase). Badge giờ phản ánh worker motion-backend.
//
// Response shape (giữ nguyên):
//   { healthy, workers: [{ worker_id, last_seen_at, active_job_id, mode, gpu_name, gpu_vram_total_mb }],
//     queued: int, running: int, total_workers: int }
//
// Polling: 30s khi healthy, 10s khi unhealthy (fail-fast detect recovery).
export function useMotionWorkerStatus() {
  const url = '/api/motion/status'

  const status = useState('motionWorker.status', () => ({
    healthy: false,
    workers: [],
    queued: 0,
    running: 0,
    total_workers: 0,
  }))
  const polling = useState('motionWorker.polling', () => false)
  let timer = null

  async function fetchOnce() {
    try {
      const res = await fetch(url)
      if (!res.ok) {
        status.value = { ...status.value, healthy: false }
        return
      }
      const data = await res.json()
      status.value = { ...status.value, ...data }
    } catch (err) {
      console.warn('[motion-worker-status] poll failed:', err)
      status.value = { ...status.value, healthy: false }
    }
  }

  function scheduleNext() {
    if (!polling.value) return
    const delay = status.value.healthy ? 30000 : 10000
    timer = setTimeout(async () => {
      await fetchOnce()
      scheduleNext()
    }, delay)
  }

  function start() {
    if (polling.value) return
    polling.value = true
    fetchOnce().then(scheduleNext)
  }

  function stop() {
    polling.value = false
    if (timer) { clearTimeout(timer); timer = null }
  }

  function refresh() { return fetchOnce() }

  // Convenience computeds
  const primaryWorker = computed(() => status.value.workers?.[0] || null)
  const gpuName = computed(() => primaryWorker.value?.gpu_name || '—')
  const vramTotalGb = computed(() => {
    const mb = primaryWorker.value?.gpu_vram_total_mb
    return mb ? (mb / 1024).toFixed(0) : '—'
  })

  return { status, polling, start, stop, refresh, primaryWorker, gpuName, vramTotalGb }
}
// #endregion
