<template>
  <!-- #region ALD 20/05/2026 - Trang lỗi global Apple-style: fixed inset-0, glass card center -->
  <div class="fixed inset-0 flex items-center justify-center p-6 overflow-hidden">
    <!-- Soft floating blob behind card -->
    <div class="absolute -top-1/4 -right-1/4 w-1/2 aspect-square rounded-full bg-rose-200/40 blur-3xl pointer-events-none" />
    <div class="absolute -bottom-1/4 -left-1/4 w-1/2 aspect-square rounded-full bg-blue-200/40 blur-3xl pointer-events-none" />

    <div class="relative glass shadow-island-lg rounded-3xl p-8 sm:p-10 max-w-md w-full text-center">
      <div class="flex justify-center mb-5">
        <span
          :class="cn(
            'inline-flex h-16 w-16 items-center justify-center rounded-3xl text-white shadow-pill',
            iconBg
          )"
        >
          <i :class="['bi text-3xl', iconName]" />
        </span>
      </div>

      <!-- Status code (gradient title) -->
      <h1 class="text-5xl sm:text-6xl font-black tracking-tighter title-gradient leading-none">
        {{ statusCode }}
      </h1>

      <h2 class="text-base sm:text-lg font-bold text-gray-900 mt-3">
        {{ statusTitle }}
      </h2>
      <p class="text-sm text-gray-500 mt-2 leading-relaxed">
        {{ statusMessage }}
      </p>

      <!-- Actions -->
      <div class="flex flex-col sm:flex-row items-center justify-center gap-2 mt-6">
        <button
          type="button"
          class="press w-full sm:w-auto inline-flex items-center justify-center gap-2 h-11 px-5 rounded-full bg-primary text-white font-semibold text-sm shadow-pill hover:bg-primary-dark transition-colors"
          @click="onHome"
        >
          <i class="bi bi-house-door" />
          Về trang chủ
        </button>
        <button
          type="button"
          class="press w-full sm:w-auto inline-flex items-center justify-center gap-2 h-11 px-5 rounded-full glass shadow-card text-sm font-semibold text-gray-700 hover:bg-white transition-colors"
          @click="onReload"
        >
          <i class="bi bi-arrow-clockwise" />
          Thử lại
        </button>
      </div>

      <!-- Diagnostic detail (dev mode only) -->
      <details v-if="showDetail && props.error?.message" class="mt-5 text-left">
        <summary class="text-xs font-semibold text-gray-500 cursor-pointer hover:text-gray-700">
          Chi tiết kỹ thuật
        </summary>
        <pre class="mt-2 text-[0.7rem] text-gray-600 bg-white/60 border border-gray-200/60 rounded-xl p-3 overflow-x-auto whitespace-pre-wrap break-words">{{ props.error?.message }}</pre>
      </details>
    </div>
  </div>
  <!-- #endregion -->
</template>

<script setup>
// #region ALD 20/05/2026 - Nuxt error layout — không cần định nghĩa layout vì error.vue là root render
const props = defineProps({ error: Object })

useHead({
  title: () => `${props.error?.statusCode ?? 'Lỗi'} — Local AI`
})

// #region ALD 20/05/2026 - Map status code → tiêu đề + icon + màu
const statusCode = computed(() => props.error?.statusCode ?? 500)

const statusTitle = computed(() => {
  const c = statusCode.value
  if (c === 404) return 'Không tìm thấy trang'
  if (c === 401) return 'Bạn cần đăng nhập'
  if (c === 403) return 'Không có quyền truy cập'
  if (c === 503) return 'Dịch vụ tạm thời ngừng'
  return 'Có lỗi xảy ra'
})

const statusMessage = computed(() => {
  const c = statusCode.value
  if (c === 404) return 'Trang bạn cần không tồn tại hoặc đã được di chuyển.'
  if (c === 401) return 'Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.'
  if (c === 403) return 'Liên hệ admin nếu bạn cho rằng đây là nhầm lẫn.'
  if (c === 503) return 'Hệ thống đang bảo trì. Vui lòng quay lại sau ít phút.'
  return props.error?.statusMessage || props.error?.message || 'Đã có lỗi không xác định.'
})

const iconName = computed(() => {
  const c = statusCode.value
  if (c === 404) return 'bi-compass'
  if (c === 401 || c === 403) return 'bi-shield-lock'
  if (c === 503) return 'bi-cone-striped'
  return 'bi-exclamation-triangle'
})

const iconBg = computed(() => {
  const c = statusCode.value
  if (c === 404) return 'bg-gradient-to-br from-blue-500 to-primary-dark'
  if (c === 401 || c === 403) return 'bg-gradient-to-br from-amber-500 to-orange-600'
  if (c === 503) return 'bg-gradient-to-br from-amber-400 to-rose-500'
  return 'bg-gradient-to-br from-rose-500 to-rose-700'
})
// #endregion

const showDetail = computed(() => import.meta.dev)

function onHome() {
  clearError({ redirect: '/' })
}
function onReload() {
  // Re-fetch route hiện tại (clear error sẽ unmount error layout)
  if (typeof window !== 'undefined') window.location.reload()
}
// #endregion
</script>
