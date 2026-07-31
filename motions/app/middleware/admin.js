export default defineNuxtRouteMiddleware((to) => {
  // #region ALD 20/05/2026 - Defense-in-depth: chặn non-admin truy cập trực tiếp URL /settings
  const auth = useAuth()
  if (!auth.isAuthenticated.value) {
    return navigateTo(`/login?callbackUrl=${encodeURIComponent(to.fullPath || '/settings')}`)
  }
  // Role không có trong cookie → cần fetch nhanh app-settings (server return role) hoặc decode JWT.
  // Đơn giản: decode payload của JWT (không verify lại, chỉ đọc claim role).
  const payload = decodeJwtPayload(auth.token.value)
  if (payload?.role !== 'admin') {
    return navigateTo('/')
  }
  // #endregion
})
