export default defineNuxtRouteMiddleware((to) => {
  // #region ALD 20/05/2026 - Guard: chưa có session token → bật về /login (giữ callbackUrl)
  const { isAuthenticated } = useAuth()
  if (!isAuthenticated.value && to.path !== '/login') {
    return navigateTo({
      path: '/login',
      query: to.fullPath !== '/' ? { callbackUrl: to.fullPath } : undefined
    })
  }
  // #endregion
})
