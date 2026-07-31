// #region ALD 31/05/2026 - Proxy xóa nhiều audio → motion-backend POST /audio/bulk-delete.
export default defineEventHandler(async (event) => {
  const { motionApiUrl, motionApiKey } = useRuntimeConfig(event)
  const body = await readBody(event)
  try {
    return await $fetch(`${motionApiUrl.replace(/\/$/, '')}/audio/bulk-delete`, {
      method: 'POST', body: { ids: body?.ids || [] }, headers: { 'X-API-Key': motionApiKey }
    })
  } catch (err) {
    throw createError({ statusCode: err?.response?.status || 502, statusMessage: err?.data?.error || 'Không xóa được' })
  }
})
// #endregion
