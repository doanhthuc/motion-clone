// #region ALD 31/05/2026 - Proxy hủy motion job → motion-backend DELETE /jobs/:id.
export default defineEventHandler(async (event) => {
  const { motionApiUrl, motionApiKey } = useRuntimeConfig(event)
  const id = getRouterParam(event, 'id')
  try {
    return await $fetch(`${motionApiUrl.replace(/\/$/, '')}/jobs/${encodeURIComponent(id)}`, {
      method: 'DELETE',
      headers: { 'X-API-Key': motionApiKey }
    })
  } catch (err) {
    throw createError({
      statusCode: err?.response?.status || 502,
      statusMessage: err?.data?.error || err?.message || 'Không hủy được job'
    })
  }
})
// #endregion
