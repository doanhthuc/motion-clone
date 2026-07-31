// ALD 02/06/2026 - Sau khi deploy bản build MỚI, tab đang mở vẫn giữ app CŨ → đi tải chunk JS cũ (đã đổi
// hash / bị xoá) → 404 "Failed to fetch dynamically imported module". Thay vì để user kẹt màn hình lỗi,
// tự reload sang bản mới (1 lần). Có guard chống loop nếu chunk vẫn 404 trên bản mới.
export default defineNuxtPlugin((nuxtApp) => {
  const KEY = "__chunkReloadAt"
  function safeReload(reason) {
    try {
      const now = Date.now()
      const last = Number(sessionStorage.getItem(KEY) || 0)
      if (now - last < 10000) return   // vừa reload <10s trước → đừng lặp vô hạn
      sessionStorage.setItem(KEY, String(now))
    } catch { /* sessionStorage có thể bị chặn — vẫn reload */ }
    console.warn("[chunk-reload] reload bản mới:", reason)
    reloadNuxtApp({ persistState: false })
  }

  // Hook chính thức của Nuxt khi 1 dynamic chunk fail (thường lúc điều hướng / mở component lazy).
  nuxtApp.hook("app:chunkError", ({ error }) => safeReload(error?.message || "app:chunkError"))

  // Phòng hờ: lỗi import động lọt ra ngoài hook trên (vd Vite preload error).
  const RE = /dynamically imported module|Importing a module script failed|Failed to fetch dynamically/i
  nuxtApp.hook("vue:error", (err) => { if (RE.test(String(err?.message || ""))) safeReload("vue:error") })
  if (import.meta.client) {
    // ALD 11/07/2026 - QUAN TRỌNG NHẤT: event chuẩn của Vite khi 1 module động fail preload (vd click node →
    // tải chunk Inspector lazy 404 sau deploy). Các listener error/rejection bên dưới hay bị Vue Suspense/
    // async-component NUỐT nên không bắt được ca này → phải nghe thẳng vite:preloadError. preventDefault để
    // Vite khỏi ném tiếp, rồi tự reload sang bản mới.
    window.addEventListener("vite:preloadError", (e) => { try { e.preventDefault() } catch {} safeReload("vite:preloadError") })
    window.addEventListener("error", (e) => { if (RE.test(String(e?.message || ""))) safeReload("window.error") })
    window.addEventListener("unhandledrejection", (e) => { if (RE.test(String(e?.reason?.message || e?.reason || ""))) safeReload("unhandledrejection") })
  }
})
