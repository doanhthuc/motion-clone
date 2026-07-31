// #region ALD 05/07/2026 - Cấu hình App Facebook/TikTok (Client ID/Secret) + Public Base URL — nhập trực tiếp
// qua Help drawer, dùng chung cho cả team (không phải per-user). Endpoint: /social-app-config (GET ai cũng
// xem được trạng thái + redirect URI cần khai; PUT chỉ admin). publicBaseUrl cũng nhập ở đây (KHÔNG còn cần
// sửa .env server) — domain HTTPS để Facebook/TikTok redirect về sau khi user cấp quyền.
export function useSocialAppConfig() {
  const auth = useAuth()
  const config = useState('socialAppConfig.value', () => null)
  const loading = useState('socialAppConfig.loading', () => false)

  async function load() {
    loading.value = true
    try {
      config.value = await auth.beFetch('/social-app-config')
    } finally {
      loading.value = false
    }
  }

  /** payload: { facebook?: {appId, appSecret}, tiktok?: {clientKey, clientSecret}, publicBaseUrl? } — secret rỗng = giữ nguyên. */
  async function save(payload) {
    const res = await auth.beFetch('/social-app-config', { method: 'PUT', body: payload })
    await load()
    return res
  }

  return { config, loading, load, save }
}
// #endregion
