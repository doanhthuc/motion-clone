// #region ALD 31/05/2026 - Proxy log job (poll, thay SSE Supabase) → motion-backend GET /jobs/:id/logs.
export default defineEventHandler(async (event) => {
  const { motionApiUrl, motionApiKey } = useRuntimeConfig(event)
  const id = getRouterParam(event, 'id')
  const since = getQuery(event).since
  const qs = since ? `?since=${encodeURIComponent(String(since))}` : ''
  try {
    return await $fetch(`${motionApiUrl.replace(/\/$/, '')}/jobs/${encodeURIComponent(id)}/logs${qs}`, {
      headers: { 'X-API-Key': motionApiKey }
    })
  } catch {
    return { logs: [] }
  }
})
// #endregion
