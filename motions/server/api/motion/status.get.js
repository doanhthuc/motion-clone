// #region ALD 31/05/2026 - Proxy worker-status (badge GPU) → motion-backend GET /worker-status.
// Lỗi upstream → trả unhealthy thay vì 500 để badge FE không vỡ.
export default defineEventHandler(async (event) => {
  const { motionApiUrl, motionApiKey } = useRuntimeConfig(event)
  try {
    return await $fetch(`${motionApiUrl.replace(/\/$/, '')}/worker-status`, {
      headers: { 'X-API-Key': motionApiKey }
    })
  } catch {
    return { healthy: false, workers: [], queued: 0, running: 0, total_workers: 0 }
  }
})
// #endregion
