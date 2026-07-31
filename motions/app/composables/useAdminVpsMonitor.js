export function useAdminVpsMonitor() {
  const auth = useAuth()
  const config = useRuntimeConfig()
  const status = useState('adminVps.status', () => null)
  const connected = useState('adminVps.connected', () => false)
  const error = useState('adminVps.error', () => '')
  let es = null

  function streamUrl() {
    const base = String(config.public.motionBackendUrl || '').replace(/\/$/, '')
    const token = encodeURIComponent(auth.token.value || '')
    return `${base}/admin/server-monitor/status/stream?token=${token}`
  }

  function start() {
    if (!import.meta.client || es || !auth.token.value) return
    error.value = ''
    es = new EventSource(streamUrl())
    es.addEventListener('status', (ev) => {
      status.value = JSON.parse(ev.data)
      connected.value = true
      error.value = ''
    })
    es.onerror = () => {
      connected.value = false
      error.value = 'Mất kết nối SSE hoặc token hết hạn'
    }
  }

  function stop() {
    if (es) es.close()
    es = null
    connected.value = false
  }

  async function refresh() {
    // ALD 29/06/2026 - poll trạng thái nền: background:true để không tự đá khỏi /admin/vps.
    status.value = await auth.beFetch('/admin/server-monitor/status', { background: true })
    return status.value
  }

  async function clearJobs(mode = 'all', jobIds = []) {
    const res = await auth.beFetch('/admin/server-monitor/clear-jobs', {
      method: 'POST',
      body: { mode, jobIds },
      headers: { 'Content-Type': 'application/json' },
    })
    await refresh().catch(() => {})
    return res
  }

  async function freeResources(mode = 'all') {
    const res = await auth.beFetch('/admin/server-monitor/free-resources', {
      method: 'POST',
      body: { mode },
      headers: { 'Content-Type': 'application/json' },
    })
    await refresh().catch(() => {})
    return res
  }

  async function restartComfy() {
    const res = await auth.beFetch('/admin/server-monitor/restart-comfy', {
      method: 'POST',
      body: {},
      headers: { 'Content-Type': 'application/json' },
    })
    await refresh().catch(() => {})
    return res
  }

  return { status, connected, error, start, stop, refresh, clearJobs, freeResources, restartComfy }
}
