// #region ALD 31/05/2026 - Proxy list motion runs → motion-backend GET /jobs?type=motion.
// motion-backend lọc theo type/status/limit (không phân trang theo page) → map các tham số tương ứng.
export default defineEventHandler(async (event) => {
  const { motionApiUrl, motionApiKey } = useRuntimeConfig(event)
  const q = getQuery(event)
  const sp = new URLSearchParams()
  sp.set('type', 'motion')
  if (q.status) sp.set('status', String(q.status))
  if (q.limit) sp.set('limit', String(q.limit))
  try {
    return await $fetch(`${motionApiUrl.replace(/\/$/, '')}/jobs?${sp.toString()}`, {
      headers: { 'X-API-Key': motionApiKey }
    })
  } catch (err) {
    throw createError({
      statusCode: err?.response?.status || 502,
      statusMessage: err?.data?.error || err?.message || 'Không lấy được danh sách'
    })
  }
})
// #endregion
