// #region ALD 31/05/2026 - Proxy list audio → motion-backend GET /audio.
export default defineEventHandler(async (event) => {
  const { motionApiUrl, motionApiKey } = useRuntimeConfig(event)
  const q = getQuery(event)
  const sp = new URLSearchParams()
  for (const k of ['page', 'limit', 'q', 'sortBy', 'sortDir']) if (q[k] != null) sp.set(k, String(q[k]))
  try {
    return await $fetch(`${motionApiUrl.replace(/\/$/, '')}/audio?${sp.toString()}`, {
      headers: { 'X-API-Key': motionApiKey }
    })
  } catch (err) {
    throw createError({ statusCode: err?.response?.status || 502, statusMessage: err?.data?.error || 'Không lấy được audio' })
  }
})
// #endregion
