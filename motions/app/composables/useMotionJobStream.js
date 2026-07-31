// #region ALD 13/07/2026 - SSE stream duy nhất cho Motion Transfer job.
// Motion Transfer dùng motion-backend và schema event status/log/end.
export function useMotionJobStream() {
  const auth = useAuth()
  const config = useRuntimeConfig()
  const active = new Map()

  async function _connect(jobId, handlers) {
    const slot = active.get(jobId)
    if (!slot || slot._closed) return
    const ctrl = new AbortController()
    slot.abortCtrl = ctrl
    try {
      const token = auth.token.value || ''
      const url = `${config.public.motionBackendUrl}/functions/v1/motion-jobs/${jobId}/logs/stream`
      const res = await fetch(url, {
        headers: { Authorization: `Bearer ${token}` },
        signal: ctrl.signal,
      })
      if (!res.ok || !res.body) throw new Error(`SSE HTTP ${res.status}`)
      const reader = res.body.getReader()
      const decoder = new TextDecoder('utf-8')
      let buf = ''
      while (!slot._closed) {
        const { value, done } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        let idx
        while ((idx = buf.indexOf('\n\n')) >= 0) {
          const chunk = buf.slice(0, idx)
          buf = buf.slice(idx + 2)
          let evName = 'message'
          const dataLines = []
          for (const line of chunk.split('\n')) {
            if (line.startsWith(':')) continue
            if (line.startsWith('event:')) evName = line.slice(6).trim()
            else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
          }
          if (!dataLines.length) continue
          let payload
          try { payload = JSON.parse(dataLines.join('\n')) } catch { continue }
          if (evName === 'log') handlers.onLog?.(payload)
          else if (evName === 'status') handlers.onStatus?.(payload)
          else if (evName === 'end') {
            handlers.onEnd?.(payload)
            slot._closed = true
            return
          } else if (evName === 'warn') handlers.onWarn?.(payload)
        }
      }
    } catch (err) {
      if (err.name === 'AbortError') return
      console.warn('[motion-job-stream] disconnect:', err.message)
      if (!slot._closed) slot.reconnectTimer = setTimeout(() => _connect(jobId, handlers), 3000)
    }
  }

  function subscribe(jobId, handlers) {
    if (active.has(jobId)) return
    active.set(jobId, { _closed: false, abortCtrl: null, reconnectTimer: null })
    _connect(jobId, handlers)
  }

  function unsubscribe(jobId) {
    const slot = active.get(jobId)
    if (!slot) return
    slot._closed = true
    try { slot.abortCtrl?.abort() } catch { /* ignore */ }
    if (slot.reconnectTimer) clearTimeout(slot.reconnectTimer)
    active.delete(jobId)
  }

  function unsubscribeAll() {
    for (const id of [...active.keys()]) unsubscribe(id)
  }

  return { subscribe, unsubscribe, unsubscribeAll }
}
// #endregion
