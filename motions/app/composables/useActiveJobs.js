// #region ALD 24/05/2026 - Global active jobs tracker.
// Poll Motion Transfer jobs có status queued/running.
// Cache state qua useState để sidebar widget hiện realtime ở mọi page.
export function useActiveJobs() {
  const items = useState('activeJobs.items', () => [])
  const loading = useState('activeJobs.loading', () => false)
  const lastFetch = useState('activeJobs.lastFetch', () => 0)
  let timer = null

  async function refresh() {
    loading.value = true
    try {
      const [motionRunning, motionQueued] = await Promise.all([
        $fetch('/api/motion/jobs?limit=20&status=running').catch(() => ({ items: [] })),
        $fetch('/api/motion/jobs?limit=20&status=queued').catch(() => ({ items: [] })),
      ])

      const all = [
        ...(motionRunning.items || []).map((j) => ({ ...j, kind: 'motion' })),
        ...(motionQueued.items || []).map((j) => ({ ...j, kind: 'motion' })),
      ]
      // Dedup by id (running + queued sets có thể overlap nếu race)
      const seen = new Set()
      items.value = all.filter((j) => {
        if (seen.has(j.id)) return false
        seen.add(j.id)
        return true
      }).sort((a, b) => new Date(b.created_at) - new Date(a.created_at))
      lastFetch.value = Date.now()
    } catch (e) {
      console.warn('[useActiveJobs] refresh fail:', e)
    } finally {
      loading.value = false
    }
  }

  function start(intervalMs = 10000) {
    if (timer) return
    refresh()
    timer = setInterval(refresh, intervalMs)
  }
  function stop() {
    if (timer) { clearInterval(timer); timer = null }
  }

  // Helper render
  function kindIcon(kind) {
    if (kind === 'motion') return 'bi-film'
    return 'bi-activity'
  }
  function kindLabel(kind) {
    if (kind === 'motion') return 'Motion Transfer'
    return kind
  }

  return { items, loading, lastFetch, refresh, start, stop, kindIcon, kindLabel }
}
// #endregion
