// #region ALD 31/05/2026 - Proxy xóa audio → motion-backend DELETE /audio/:id.
export default defineEventHandler(async (event) => {
  const { motionApiUrl, motionApiKey } = useRuntimeConfig(event)
  const id = getRouterParam(event, 'id')
  try {
    return await $fetch(`${motionApiUrl.replace(/\/$/, '')}/audio/${encodeURIComponent(id)}`, {
      method: 'DELETE', headers: { 'X-API-Key': motionApiKey }
    })
  } catch (err) {
    throw createError({ statusCode: err?.response?.status || 502, statusMessage: err?.data?.error || 'Không xóa được audio' })
  }
})
// #endregion
