// #region ALD 31/05/2026 - Proxy snapshot 1 motion job từ motion-backend.
// Rewrite output_url → route same-origin /api/motion/jobs/:id/download (stream qua server, kèm key)
// để <video :src> tải được mà không lộ API key / không vướng CORS.
export default defineEventHandler(async (event) => {
  const { motionApiUrl, motionApiKey } = useRuntimeConfig(event)
  const id = getRouterParam(event, 'id')
  try {
    const snap = await $fetch(`${motionApiUrl.replace(/\/$/, '')}/jobs/${encodeURIComponent(id)}`, {
      headers: { 'X-API-Key': motionApiKey }
    })
    if (snap?.output_key || snap?.output_url) snap.output_url = `/api/motion/jobs/${encodeURIComponent(id)}/download`
    return snap
  } catch (err) {
    throw createError({
      statusCode: err?.response?.status || 502,
      statusMessage: err?.data?.error || err?.message || 'Không lấy được job'
    })
  }
})
// #endregion
