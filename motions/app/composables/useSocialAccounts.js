// #region ALD 05/07/2026 - Composable quản lý Social Account (Facebook Page / TikTok) đã connect, dùng cho
// node "output" (đăng tự động khi có kết quả). Endpoints: /social-accounts (list/CRUD) + /:platform/connect-url.
//
// Surface:
//   - items: ref([]) — { id, platform, name, avatar_url, external_id, connected_at }
//   - load()
//   - connect(platform) — mở popup OAuth, tự reload() khi popup báo kết quả qua postMessage
//   - disconnect(id)
//   - byPlatform(platform) — computed helper lọc theo platform
//
// Caller pattern: const social = useSocialAccounts(); onMounted(() => social.load())
export function useSocialAccounts() {
  const auth = useAuth()
  const items = useState('socialAccounts.items', () => [])
  const loading = useState('socialAccounts.loading', () => false)
  const connecting = useState('socialAccounts.connecting', () => null) // platform đang mở popup, hoặc null

  async function load() {
    loading.value = true
    try {
      const res = await auth.beFetch('/social-accounts')
      items.value = res?.items ?? []
    } finally {
      loading.value = false
    }
  }

  function byPlatform(platform) {
    return computed(() => items.value.filter((a) => a.platform === platform))
  }

  /**
   * Mở popup OAuth cho platform ('facebook' | 'tiktok'). Backend build URL kèm state ký sẵn
   * (không cần PKCE/state phía FE). Khi popup callback xong, nó postMessage({source:'social-oauth', ok, message})
   * rồi tự đóng — ta lắng nghe để refresh list + trả kết quả cho UI hiện toast.
   */
  function connect(platform) {
    return new Promise((resolve, reject) => {
      if (!import.meta.client) return reject(new Error('connect() chỉ chạy trên client'))
      connecting.value = platform
      auth.beFetch(`/social-accounts/${platform}/connect-url`)
        .then((res) => {
          const url = res?.url
          if (!url) throw new Error('Server không trả về URL kết nối')
          const popup = window.open(url, 'social-connect', 'width=560,height=700,noopener=no')
          if (!popup) throw new Error('Trình duyệt chặn popup — cho phép popup rồi thử lại')

          let settled = false
          const onMessage = (ev) => {
            if (ev?.data?.source !== 'social-oauth') return
            settled = true
            window.removeEventListener('message', onMessage)
            clearInterval(poll)
            connecting.value = null
            load().finally(() => {
              if (ev.data.ok) resolve(ev.data)
              else reject(new Error(ev.data.message || 'Kết nối thất bại'))
            })
          }
          window.addEventListener('message', onMessage)
          // Phòng khi user tự đóng popup tay (không postMessage) — dừng trạng thái "đang kết nối".
          const poll = setInterval(() => {
            if (popup.closed) {
              clearInterval(poll)
              window.removeEventListener('message', onMessage)
              connecting.value = null
              if (!settled) load().finally(() => reject(new Error('Popup đã đóng — nếu vừa kết nối xong, danh sách sẽ tự cập nhật')))
            }
          }, 700)
        })
        .catch((e) => {
          connecting.value = null
          reject(e)
        })
    })
  }

  async function disconnect(id) {
    await auth.beFetch(`/social-accounts/${id}`, { method: 'DELETE' })
    await load()
  }

  /**
   * Content Sharing Guidelines mục 4 (Required UX) — BẮT BUỘC gọi trước khi render form đăng TikTok: lấy
   * nickname (hiển thị cho user biết đang đăng vào tài khoản nào), giới hạn thời lượng, danh sách privacy_level
   * hợp lệ, và tương tác nào tài khoản đã tắt sẵn (để làm mờ checkbox tương ứng).
   */
  async function fetchTikTokCreatorInfo(accountId) {
    return await auth.beFetch(`/social-accounts/${accountId}/tiktok-creator-info`)
  }

  return { items, loading, connecting, load, byPlatform, connect, disconnect, fetchTikTokCreatorInfo }
}
// #endregion
