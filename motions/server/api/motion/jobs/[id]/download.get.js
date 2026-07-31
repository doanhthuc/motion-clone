// #region ALD 31/05/2026 - Stream video output từ motion-backend qua server (kèm API key).
// FE chỉ thấy URL same-origin /api/motion/jobs/:id/download → <video> tải được, không lộ key, không CORS.
export default defineEventHandler(async (event) => {
  const { motionApiUrl, motionApiKey } = useRuntimeConfig(event)
  const id = getRouterParam(event, 'id')
  const upstream = await fetch(`${motionApiUrl.replace(/\/$/, '')}/jobs/${encodeURIComponent(id)}/download`, {
    headers: { 'X-API-Key': motionApiKey }
  })
  if (!upstream.ok || !upstream.body) {
    throw createError({ statusCode: upstream.status || 404, statusMessage: 'Output chưa sẵn sàng' })
  }
  setResponseHeader(event, 'content-type', upstream.headers.get('content-type') || 'video/mp4')
  const len = upstream.headers.get('content-length')
  if (len) setResponseHeader(event, 'content-length', len)
  setResponseHeader(event, 'content-disposition', `inline; filename="${id}.mp4"`)
  return upstream.body
})
// #endregion
