// #region ALD 22/05/2026 - Composable workflow CRUD + invoke
// Endpoints: /functions/v1/workflows (list/get/create/update/delete + invoke by slug)
//
// Surface:
//   - items: ref([]) — list workflows visible cho user
//   - load(), get(id), create(payload), update(id, patch), remove(id)
//   - invoke(slug, input) — gọi POST /workflows/:slug/invoke, trả output + events
//   - listRuns(id), getRun(runId)
export function useWorkflows() {
  // ALD 31/05/2026 - chuyển sang motion-backend (beFetch). base bỏ /functions/v1.
  const auth = useAuth()
  const items = useState('workflows.items', () => [])
  const loading = useState('workflows.loading', () => false)
  const base = '/workflows'

  async function load() {
    loading.value = true
    try {
      // ALD 29/06/2026 - Admin xem & quản lý TẤT CẢ workflow của mọi user (kể cả đã tắt):
      // xin include_inactive + KHÔNG lọc bỏ workflow disabled ở client. User thường giữ nguyên.
      const isAdmin = decodeJwtPayload(auth.token.value)?.role === 'admin'
      const res = await auth.beFetch(isAdmin ? `${base}?include_inactive=true` : base)
      const list = res?.items ?? []
      items.value = isAdmin ? list : list.filter((w) => w?.is_active !== false)
    } finally {
      loading.value = false
    }
  }

  async function get(id) {
    const res = await auth.beFetch(`${base}/${id}`)
    return res?.item
  }

  /**
   * @param {object} payload - { slug, name, description?, definition?, is_public? }
   */
  async function create(payload) {
    const res = await auth.beFetch(base, {
      method: 'POST',
      body: payload,
      headers: { 'Content-Type': 'application/json' }
    })
    await load()
    return res?.item
  }

  async function update(id, patch) {
    const res = await auth.beFetch(`${base}/${id}`, {
      method: 'PUT',
      body: patch,
      headers: { 'Content-Type': 'application/json' }
    })
    await load()
    return res?.item
  }

  async function remove(id) {
    await auth.beFetch(`${base}/${id}`, { method: 'DELETE' })
    await load()
  }

  /**
   * Invoke workflow theo slug. Input là object tự do, được map vào input nodes
   * trong workflow (field name của input node ↔ key trong input object).
   */
  async function invoke(slug, input = {}) {
    return await auth.beFetch(`${base}/${slug}/invoke`, {
      method: 'POST',
      body: input,
      headers: { 'Content-Type': 'application/json' }
    })
  }

  /**
   * Test ASYNC: kick worker với in-memory definition, trả run_id ngay.
   * FE poll getRun(run_id) để xem events + output live (engine flush events
   * vào DB mỗi step).
   *
   * Trả: { run_id, status: 'queued', poll_url }
   * Chỉ chờ tới khi server nhận request (kick xong), ~vài trăm ms.
   */
  async function test(id, definition, input = {}, opts = {}) {
    return await auth.beFetch(`${base}/${id}/test`, {
      method: 'POST',
      // ALD 17/06/2026 - resume=true ("Tiếp tục từ chỗ lỗi") → BE đọc cache output, node đã xong dùng lại, chỉ render node lỗi
      body: { definition, input, resume: opts?.resume === true },
      headers: { 'Content-Type': 'application/json' },
      signal: AbortSignal.timeout(30000)   // kick fetch chỉ vài trăm ms, cap 30s an toàn
    })
  }

  // ALD 11/07/2026 - Phân trang: nhận { limit, offset }, trả { items, total } để runs.vue dựng pager.
  async function listRuns(id, { limit = 10, offset = 0 } = {}) {
    const qs = new URLSearchParams({ limit: String(limit), offset: String(offset) })
    const res = await auth.beFetch(`${base}/${id}/runs?${qs}`)
    return { items: res?.items ?? [], total: res?.total ?? (res?.items?.length ?? 0) }
  }

  async function getRun(runId) {
    const res = await auth.beFetch(`${base}/runs/${runId}`)
    return res?.run
  }

  /**
   * Workflow-scoped asset resolver — fetch signed URL của library item được reference
   * trong workflow def (audio /audio hoặc storage /storage). Cho phép public viewer
   * preview asset của owner mà KHÔNG cần grant full library access.
   *
   * ALD 27/05/2026 - kind: 'audio' | 'storage'. Trả { signedUrl, name, mime, ... } | null.
   */
  async function getAsset(wfId, libId, kind = 'audio') {
    if (!wfId || !libId) return null
    try {
      const res = await auth.beFetch(`${base}/${wfId}/asset/${libId}?kind=${kind}`)
      return res?.item || null
    } catch (e) {
      console.warn('[useWorkflows.getAsset] fail:', e?.message || e)
      return null
    }
  }

  return { items, loading, load, get, create, update, remove, invoke, test, listRuns, getRun, getAsset }
}
// #endregion
