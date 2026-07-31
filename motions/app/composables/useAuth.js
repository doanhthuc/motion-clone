// #region ALD 20/05/2026 - Composable auth: lưu session JWT vào cookie + helper apiFetch
// ALD 30/05/2026 - Thêm refresh token (chuẩn OAuth): access JWT ngắn hạn + refresh
// token dài hạn (dùng chung backend supabase.pebsteel.com với local-ai). apiFetch tự
// đổi refresh→access mới khi gặp 401 rồi RETRY 1 lần → user không bị đá ra sau khi
// access hết hạn (chỉ logout khi refresh cũng hết/bị thu hồi).

// ALD 29/06/2026 - SỬA "vừa login xong bị đá ra + refresh hoài":
//   1. doRefresh() TRI-STATE ('ok'|'expired'|'error'): chỉ 'expired' (refresh token
//      thật sự sai/hết/bị thu hồi, hoặc không có) mới được phép logout. Lỗi mạng/5xx
//      = 'error' → GIỮ session, không đá user ra vì một cú trượt tạm thời.
//   2. Sau khi refresh 'ok' mà retry VẪN 401 → KHÔNG logout: token vừa cấp còn hợp lệ
//      nên 401 đó là do RIÊNG endpoint (quyền/timing), không phải phiên hết.
//   3. Cooldown refresh: không gọi /auth/refresh dồn dập (chống "refresh hoài" khi
//      nhiều poll 401 liên tiếp hoặc khi lệch giờ làm token luôn trông như hết hạn).
//   4. Cờ { background: true }: request nền (poller) KHÔNG bao giờ tự điều hướng user
//      về /login — chỉ thao tác foreground mới được đá ra.
//   5. Proactive refresh: nếu access token giải mã được là đã hết hạn thì refresh
//      TRƯỚC, đỡ một vòng 401 vô ích (vẫn qua cooldown nên không thành vòng lặp).

// Single-flight refresh: nhiều request 401 cùng lúc chỉ gọi /refresh ĐÚNG 1 lần.
// Dedupe redirect: tránh nhiều navigate khi nhiều request cùng 401 (page fan-out endpoint).
// Cả 2 chỉ chạy client nên module-scope = per-tab, không rò rỉ giữa request SSR khác user.
let _refreshPromise = null
let _expiredRedirecting = false
// ALD 29/06/2026 - Cache kết quả refresh gần nhất + thời điểm, để cooldown.
let _lastRefreshResult = null // 'ok' | 'expired' | 'error'
let _lastRefreshAt = 0
// Không gọi /auth/refresh quá 1 lần / khoảng này. Nhiều poll 401 liên tiếp (hoặc lệch
// giờ) sẽ tái dùng kết quả gần nhất thay vì nã /refresh → hết "refresh hoài".
const REFRESH_COOLDOWN_MS = 15000

export function useAuth() {
  const config = useRuntimeConfig()

  // maxAge cookie = tuổi refresh token (30 ngày) để cookie access còn sống trong khi
  // JWT bên trong được xoay liên tục qua /refresh. Nếu maxAge access < refresh thì
  // cookie biến mất sớm → middleware đá về login trước khi kịp refresh.
  const COOKIE_MAX_AGE = 60 * 60 * 24 * 30 // 30 ngày
  const tokenCookie = useCookie('pebsteel_session', {
    sameSite: 'lax',
    secure: false,
    maxAge: COOKIE_MAX_AGE
  })
  const refreshCookie = useCookie('pebsteel_refresh', {
    sameSite: 'lax',
    secure: false,
    maxAge: COOKIE_MAX_AGE
  })
  const userState = useState('auth.user', () => null)

  async function requestOtp(email) {
    return await $fetch('/auth/send-otp', {
      baseURL: config.public.motionBackendUrl,
      method: 'POST',
      body: { email }
    })
  }

  async function verifyOtp(email, code) {
    const res = await $fetch('/auth/verify-otp', {
      baseURL: config.public.motionBackendUrl,
      method: 'POST',
      body: { email, code }
    })
    if (res?.success && res?.token) {
      tokenCookie.value = res.token
      if (res.refreshToken) refreshCookie.value = res.refreshToken
      userState.value = res.user
      // ALD 29/06/2026 - Phiên mới: xoá cache refresh cũ (tránh 'expired' tồn dư trong
      // cooldown đá nhầm user vừa đăng nhập lại).
      resetRefreshState()
    }
    return res
  }

  function logout() {
    // ALD 30/05/2026 - Đọc token TRƯỚC khi xoá để còn gửi kèm yêu cầu thu hồi.
    const rt = refreshCookie.value
    const headers = authHeader()
    // Xoá cookie NGAY (UI phản hồi tức thì), không chờ network.
    tokenCookie.value = null
    refreshCookie.value = null
    userState.value = null
    resetRefreshState()
    // Thu hồi phiên server-side (best-effort, chạy nền) → refresh token hết hiệu lực
    // ngay thay vì sống tới 30 ngày sau khi đăng xuất.
    if (import.meta.client && rt) {
      $fetch('/auth/logout', {
        baseURL: config.public.motionBackendUrl,
        method: 'POST',
        headers,
        body: { refreshToken: rt }
      }).catch(() => {})
    }
  }

  function authHeader() {
    return tokenCookie.value ? { Authorization: `Bearer ${tokenCookie.value}` } : {}
  }

  // #region ALD 30/05/2026 - Refresh flow (ALD 29/06/2026 - tri-state + cooldown)
  function resetRefreshState() {
    _lastRefreshResult = null
    _lastRefreshAt = 0
  }

  /**
   * Đổi refresh→access mới (single-flight + cooldown).
   * Trả 'ok' | 'expired' | 'error'. Chỉ 'expired' mới được phép dẫn tới logout.
   */
  function ensureRefreshed() {
    if (_refreshPromise) return _refreshPromise
    // Cooldown: vừa refresh xong gần đây → tái dùng kết quả, KHÔNG nã /refresh lần nữa.
    if (_lastRefreshResult && (Date.now() - _lastRefreshAt) < REFRESH_COOLDOWN_MS) {
      return Promise.resolve(_lastRefreshResult)
    }
    _refreshPromise = doRefresh()
      .then((r) => {
        _lastRefreshResult = r
        _lastRefreshAt = Date.now()
        return r
      })
      .finally(() => { _refreshPromise = null })
    return _refreshPromise
  }

  async function doRefresh() {
    const rt = refreshCookie.value
    if (!rt) return 'expired' // không có refresh token → phiên thật sự hết
    try {
      const res = await $fetch('/auth/refresh', {
        baseURL: config.public.motionBackendUrl,
        method: 'POST',
        body: { refreshToken: rt }
      })
      if (res?.success && res?.token) {
        tokenCookie.value = res.token
        return 'ok'
      }
      // Server trả lời nhưng không cấp token mới → refresh token bị từ chối.
      return 'expired'
    } catch (err) {
      const status = err?.response?.status ?? err?.statusCode
      // 400/401/403 từ /refresh = refresh token sai/hết/bị thu hồi → phiên hết thật.
      if (status === 400 || status === 401 || status === 403) return 'expired'
      // Mạng lỗi / 5xx / timeout → KHÔNG kết luận phiên hết; giữ nguyên session.
      return 'error'
    }
  }

  /** True nếu access token giải mã được là đã (sắp) hết hạn — dùng cho proactive refresh. */
  function isAccessExpired() {
    const tok = tokenCookie.value
    if (!tok) return false // không có token → để request đi tự nhiên / middleware lo
    const payload = decodeJwtPayload(tok)
    const exp = payload?.exp
    if (!exp) return false // không đọc được exp → bỏ qua proactive, dựa vào 401 phản ứng
    // Đệm 30s. Date.now() là giờ client; nếu lệch giờ thì cooldown ở ensureRefreshed()
    // chặn gọi /refresh dồn dập nên cũng không thành vòng lặp.
    return (exp * 1000) <= (Date.now() + 30000)
  }

  /** Clear session + đẩy về /login?expired=1 (giữ callbackUrl). */
  function handleExpired() {
    if (!import.meta.client) return
    if (_expiredRedirecting) return
    _expiredRedirecting = true

    tokenCookie.value = null
    refreshCookie.value = null
    userState.value = null
    resetRefreshState()

    const router = useRouter()
    const route = useRoute()
    const isOnLogin = route.path === '/login'
    const query = { expired: '1' }
    if (!isOnLogin && route.fullPath && route.fullPath !== '/') {
      query.callbackUrl = route.fullPath
    }
    Promise.resolve(router.push({ path: '/login', query })).finally(() => {
      setTimeout(() => { _expiredRedirecting = false }, 1500)
    })
  }
  // #endregion

  function rawFetch(path, options = {}) {
    return $fetch(path, {
      baseURL: config.public.motionBackendUrl,
      ...options,
      headers: {
        ...authHeader(),
        ...(options.headers ?? {})
      }
    })
  }

  // #region ALD 29/06/2026 - Core 401 handling dùng chung cho apiFetch + beFetch.
  /**
   * Bọc rawFetch với refresh→retry NON-DESTRUCTIVE:
   *   - Proactive: token đã hết hạn → refresh trước (qua cooldown).
   *   - 401 → ensureRefreshed():
   *       'ok'      → retry 1 lần; còn 401 thì NÉM lỗi nhưng KHÔNG logout (lỗi endpoint).
   *       'expired' → refresh token thật sự hết → logout (trừ request background).
   *       'error'   → mạng trục trặc → giữ session, ném lỗi cho caller tự xử/retry.
   * `options.background = true`: poller nền, không bao giờ tự điều hướng về /login.
   */
  async function withAuthRetry(path, options = {}) {
    const { background = false, ...fetchOptions } = options
    if (!import.meta.client) return await rawFetch(path, fetchOptions)

    // Proactive: tránh một vòng 401 chắc-chắn-xảy-ra nếu token đã hết hạn.
    if (isAccessExpired()) await ensureRefreshed()

    try {
      return await rawFetch(path, fetchOptions)
    } catch (err) {
      const status = err?.response?.status ?? err?.statusCode
      if (status !== 401) throw err

      const result = await ensureRefreshed()
      if (result === 'ok') {
        // Token mới còn hợp lệ → thử lại 1 lần. Nếu VẪN 401: KHÔNG đá user ra,
        // đây là vấn đề của riêng endpoint, không phải phiên hết.
        return await rawFetch(path, fetchOptions)
      }
      // 'expired' → phiên hết thật; chỉ foreground mới được điều hướng.
      // 'error'   → giữ session, để caller retry sau.
      if (result === 'expired' && !background) handleExpired()
      throw err
    }
  }
  // #endregion

  /**
   * Wrapper $fetch với baseURL = motionBackendUrl + auto attach Bearer token.
   * Dùng cho mọi endpoint yêu cầu session. SSR giữ hành vi cũ (chỉ $fetch, ném lỗi).
   * Truyền { background: true } cho request nền để không bị đá về /login.
   */
  async function apiFetch(path, options = {}) {
    return await withAuthRetry(path, options)
  }

  // #region ALD 31/05/2026 - beFetch: gọi motion-backend trực tiếp (baseURL=motionBackendUrl)
  // + Bearer session (motion-backend chấp nhận token cùng secret qua bridge). Dùng cho
  // workflows/storage/ai-providers (đã port sang motion-backend).
  // ALD 29/06/2026 - Gộp chung core withAuthRetry với apiFetch (logic 401 y hệt nhau).
  async function beFetch(path, options = {}) {
    return await withAuthRetry(path, options)
  }
  // #endregion

  return {
    user: userState,
    token: tokenCookie,
    isAuthenticated: computed(() => !!tokenCookie.value),
    requestOtp,
    verifyOtp,
    logout,
    authHeader,
    apiFetch,
    beFetch
  }
}
// #endregion
