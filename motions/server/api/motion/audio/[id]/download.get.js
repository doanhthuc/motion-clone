// #region ALD 31/05/2026 - Stream audio preview qua server (kèm key) → motion-backend /audio/:id/download.
export default defineEventHandler(async (event) => {
  const { motionApiUrl, motionApiKey } = useRuntimeConfig(event)
  const id = getRouterParam(event, 'id')
  const upstream = await fetch(`${motionApiUrl.replace(/\/$/, '')}/audio/${encodeURIComponent(id)}/download`, {
    headers: { 'X-API-Key': motionApiKey }
  })
  if (!upstream.ok || !upstream.body) {
    throw createError({ statusCode: upstream.status || 404, statusMessage: 'Không tải được audio' })
  }
  setResponseHeader(event, 'content-type', upstream.headers.get('content-type') || 'audio/mpeg')
  const len = upstream.headers.get('content-length')
  if (len) setResponseHeader(event, 'content-length', len)
  return upstream.body
})
// #endregion
