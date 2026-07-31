// #region ALD 21/05/2026 - Composable cho mục Reports trong Settings (admin only).
// Wrapper /job-reports:
//   - listReports({page, limit, q, mode, status, userId})
//   - getReport(jobId)
//   - getStats()
//   - deleteReport(jobId)
//   - exportUrl(jobId) → URL để download JSON (kèm auth qua redirect ko khả thi → dùng fetch + blob)
export function useJobReports() {
  const auth = useAuth()
  const config = useRuntimeConfig()
  const base = '/job-reports'

  async function listReports({
    page = 1, limit = 20, q = '', mode = '', status = '', userId = ''
  } = {}) {
    const params = new URLSearchParams()
    params.set('page', String(page))
    params.set('limit', String(limit))
    if (q) params.set('q', q)
    if (mode) params.set('mode', mode)
    if (status) params.set('status', status)
    if (userId) params.set('userId', userId)
    return await auth.beFetch(`${base}?${params.toString()}`)
  }

  async function getReport(jobId) {
    return await auth.beFetch(`${base}/${encodeURIComponent(jobId)}`)
  }

  async function getStats() {
    return await auth.beFetch(`${base}/stats`)
  }

  async function deleteReport(jobId) {
    return await auth.beFetch(`${base}/${encodeURIComponent(jobId)}`, { method: 'DELETE' })
  }

  /**
   * Download report dưới dạng file JSON. Trigger browser save dialog.
   */
  async function downloadJson(jobId) {
    const url = `${config.public.motionBackendUrl}${base}/${encodeURIComponent(jobId)}/export`
    const res = await fetch(url, { headers: auth.authHeader() })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const blob = await res.blob()
    const blobUrl = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = blobUrl
    a.download = `report-${jobId}.json`
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(blobUrl), 1000)
  }

  /**
   * Copy report JSON vào clipboard (cho user paste sang Claude).
   */
  async function copyJson(jobId) {
    const r = await getReport(jobId)
    if (!r?.item) throw new Error('Report không tồn tại')
    const text = JSON.stringify(r.item, null, 2)
    await copyText(text)
    return text.length
  }

  return { listReports, getReport, getStats, deleteReport, downloadJson, copyJson }
}
// #endregion
