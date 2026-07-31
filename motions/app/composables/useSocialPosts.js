// #region ALD 05/07/2026 - Composable "Social Management" — Recommend (output workflow chưa đăng) + hàng
// đợi/lịch sử đăng bài. Endpoints: /social-posts/recommend, /social-posts (CRUD).
export function useSocialPosts() {
  const auth = useAuth()
  const recommendations = useState('socialPosts.recommendations', () => [])
  const history = useState('socialPosts.history', () => [])
  const historyTotal = useState('socialPosts.historyTotal', () => 0)
  const historyPage = useState('socialPosts.historyPage', () => 1)
  const HISTORY_PAGE_SIZE = 20
  const loading = useState('socialPosts.loading', () => false)

  async function loadRecommendations() {
    loading.value = true
    try {
      const res = await auth.beFetch('/social-posts/recommend')
      recommendations.value = res?.items ?? []
    } finally {
      loading.value = false
    }
  }

  // ALD 05/07/2026 - Phân trang thật (mục "Hoạt động gần đây" gói vào drawer, có thể nhiều dữ liệu theo thời gian).
  async function loadHistory(page = historyPage.value || 1) {
    const res = await auth.beFetch(`/social-posts?page=${page}&limit=${HISTORY_PAGE_SIZE}`)
    history.value = res?.items ?? []
    historyTotal.value = res?.total ?? history.value.length
    historyPage.value = page
  }

  /**
   * Tạo bài đăng từ 1 output (workflow_run_id) — đăng ngay (mặc định) hoặc hẹn giờ (scheduledFor ISO string).
   * @param {{workflow_run_id:string, caption?:string, facebook?:{enabled:boolean,accountIds:string[]}, tiktok?:{enabled:boolean,accountIds:string[]}, scheduled_for?:string}} payload
   */
  async function createPost(payload) {
    const res = await auth.beFetch('/social-posts', { method: 'POST', body: payload })
    await Promise.all([loadRecommendations(), loadHistory()])
    return res
  }

  async function cancelPost(id) {
    await auth.beFetch(`/social-posts/${id}`, { method: 'DELETE' })
    await loadHistory(historyPage.value)
  }

  /**
   * Test đăng 1 file bất kỳ (KHÔNG cần chạy workflow trước) — dùng khi muốn tự thử nhanh luồng đăng Facebook/
   * TikTok (vd sau khi đổi cấu hình App/scope) mà chưa có output workflow phù hợp trong tay.
   * @param {File} file
   * @param {{caption?:string, facebook?:{enabled:boolean,accountIds:string[]}, tiktok?:{enabled:boolean,accountIds:string[]}}} opts
   */
  async function uploadTestPost(file, opts = {}) {
    const form = new FormData()
    form.append('file', file)
    form.append('caption', opts.caption || '')
    form.append('facebook', JSON.stringify(opts.facebook || { enabled: false, accountIds: [] }))
    form.append('tiktok', JSON.stringify(opts.tiktok || { enabled: false, accountIds: [] }))
    const res = await auth.beFetch('/social-posts/upload-test', { method: 'POST', body: form })
    await loadHistory()
    return res
  }

  return { recommendations, history, historyTotal, historyPage, loading, loadRecommendations, loadHistory, createPost, cancelPost, uploadTestPost }
}
// #endregion
