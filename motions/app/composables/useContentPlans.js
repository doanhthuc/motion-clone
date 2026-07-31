// #region ALD 05/07/2026 - Composable "Content Plan" (Social Management) — N bài/ngày, mỗi slot = 1 workflow
// chạy ở 1 giờ cố định rồi tự đăng. Endpoints: /content-plans (+ /slots, /runs).
export function useContentPlans() {
  const auth = useAuth()
  const items = useState('contentPlans.items', () => [])
  const loading = useState('contentPlans.loading', () => false)

  async function load() {
    loading.value = true
    try {
      const res = await auth.beFetch('/content-plans')
      items.value = res?.items ?? []
    } finally {
      loading.value = false
    }
  }

  async function createPlan(name) {
    const res = await auth.beFetch('/content-plans', { method: 'POST', body: { name } })
    await load()
    return res?.item
  }

  async function updatePlan(id, payload) {
    const res = await auth.beFetch(`/content-plans/${id}`, { method: 'PUT', body: payload })
    await load()
    return res?.item
  }

  async function deletePlan(id) {
    await auth.beFetch(`/content-plans/${id}`, { method: 'DELETE' })
    await load()
  }

  /** payload: { label, workflow_id, input, time_of_day, weekdays, caption, facebook, tiktok, is_active } */
  async function addSlot(planId, payload) {
    const res = await auth.beFetch(`/content-plans/${planId}/slots`, { method: 'POST', body: payload })
    await load()
    return res?.item
  }

  async function updateSlot(slotId, payload) {
    const res = await auth.beFetch(`/content-plans/slots/${slotId}`, { method: 'PUT', body: payload })
    await load()
    return res?.item
  }

  async function deleteSlot(slotId) {
    await auth.beFetch(`/content-plans/slots/${slotId}`, { method: 'DELETE' })
    await load()
  }

  async function loadRuns(planId) {
    const res = await auth.beFetch(`/content-plans/${planId}/runs`)
    return res?.items ?? []
  }

  return { items, loading, load, createPlan, updatePlan, deletePlan, addSlot, updateSlot, deleteSlot, loadRuns }
}
// #endregion
