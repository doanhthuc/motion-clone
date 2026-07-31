// #region ALD 31/05/2026 - Proxy chi tiết audio → motion-backend GET /audio/:id.
// Rewrite signedUrl về same-origin /api/motion/audio/:id/download (preview không lộ key/CORS).
export default defineEventHandler(async (event) => {
  const { motionApiUrl, motionApiKey } = useRuntimeConfig(event)
  const id = getRouterParam(event, 'id')
  try {
    const res = await $fetch(`${motionApiUrl.replace(/\/$/, '')}/audio/${encodeURIComponent(id)}`, {
      headers: { 'X-API-Key': motionApiKey }
    })
    if (res?.item) res.item.signedUrl = `/api/motion/audio/${encodeURIComponent(id)}/download`
    return res
  } catch (err) {
    throw createError({ statusCode: err?.response?.status || 502, statusMessage: err?.data?.error || 'Không lấy được audio' })
  }
})
// #endregion
