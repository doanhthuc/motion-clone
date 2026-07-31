<template>
  <!-- #region ALD 06/07/2026 - Shell v2 "Liquid Glass": sidebar CỐ ĐỊNH bên trái (macOS style),
       thu gọn được về icon-rail (persist localStorage). Bỏ drawer nổi mặc định ẩn — lý do chính
       khiến điều hướng "khó thao tác". Mobile (<lg) fallback về drawer overlay. -->
  <div class="fixed inset-0 flex overflow-hidden">

    <!-- Backdrop mobile khi sidebar mở -->
    <Transition
      enter-active-class="transition-opacity duration-200"
      leave-active-class="transition-opacity duration-200"
      enter-from-class="opacity-0"
      leave-to-class="opacity-0"
    >
      <div
        v-if="mobileOpen"
        class="absolute inset-0 z-30 lg:hidden bg-black/25 backdrop-blur-[2px]"
        @click="mobileOpen = false"
      />
    </Transition>

    <!-- ── Sidebar ─────────────────────────────────────────────────────── -->
    <aside
      :class="cn(
        'flex flex-col glass z-40 flex-shrink-0',
        'transition-[width,transform] duration-300 ease-[cubic-bezier(0.32,0.72,0,1)]',
        // Desktop: cố định trong flow, đổi width khi collapse
        'lg:relative lg:translate-x-0 lg:rounded-none lg:border-y-0 lg:border-l-0',
        collapsed ? 'lg:w-[68px]' : 'lg:w-64',
        // Mobile: drawer overlay
        'fixed inset-y-0 left-0 w-72 max-lg:border-y-0 max-lg:border-l-0 max-lg:shadow-island-lg',
        mobileOpen ? 'translate-x-0' : 'max-lg:-translate-x-full'
      )"
    >
      <!-- Brand -->
      <div :class="cn('flex items-center gap-2 h-14 px-3 flex-shrink-0', collapsed && 'lg:justify-center ')">
        <NuxtLink to="/" class="flex items-center gap-2 min-w-0 press" @click="mobileOpen = false">
          <span class="inline-flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-b from-[#6B76E5] to-[#5E6AD2] text-white flex-shrink-0 text-[15px] shadow-pill">
            <i class="bi bi-film" />
          </span>
          <span v-if="!collapsed || mobileOpen" class="text-[15px] font-semibold tracking-tight text-gray-900 truncate lg:block" :class="collapsed && 'lg:hidden'">
            {{ appConfig.app.name }}
          </span>
        </NuxtLink>
      </div>

      <!-- CTA: workflow mới -->
      <div :class="cn('px-3 pb-2 flex-shrink-0', collapsed && 'lg:px-3.5')">
        <button
          type="button"
          :class="cn(
            'lq-btn lq-btn--primary w-full !h-9',
            collapsed && 'lg:!px-0'
          )"
          :title="collapsed ? 'Workflow mới' : undefined"
          @click="onNewWorkflow"
        >
          <i class="bi bi-plus-lg" />
          <span :class="collapsed && 'lg:hidden'">Workflow mới</span>
        </button>
      </div>

      <!-- Nav -->
      <nav class="flex-1 min-h-0 overflow-y-auto overflow-x-hidden px-2 py-1 space-y-0.5">
        <NuxtLink
          v-for="item in visibleNavItems"
          :key="item.to"
          :to="item.to"
          :title="collapsed ? item.label : undefined"
          :class="cn(
            'flex items-center gap-2 h-9 px-2 rounded-[10px] text-[13px] font-medium',
            // ALD 06/07/2026 - border LUÔN ở base (giữ chỗ 1px cố định) → active/inactive KHÔNG lệch trái khi đổi tab.
            'border border-transparent transition-colors duration-150',
            collapsed && 'lg:justify-center ',
            // active = tint primary (rõ ở CẢ dark & light; bg-gray-50 dark=#17171A bị chìm nên đổi).
            isActive(item.to)
              ? 'bg-primary/10 text-gray-900 shadow-card !border-primary/30'
              : 'text-gray-600 hover:bg-white/[0.04] hover:text-gray-900'
          )"
          @click="mobileOpen = false"
        >
          <i :class="['bi text-[15px] flex-shrink-0 leading-none', item.icon, isActive(item.to) ? 'text-primary' : '']" />
          <span class="flex-1 truncate" :class="collapsed && 'lg:hidden'">{{ item.label }}</span>
        </NuxtLink>
      </nav>

      <!-- Footer: user -->
      <div ref="userMenuWrapRef" class="p-2 border-t border-white/[0.06] relative flex-shrink-0">
        <button
          type="button"
          :class="cn(
            'w-full flex items-center gap-2 px-2 py-1.5 rounded-[10px] hover:bg-white/[0.04] press transition-colors',
            collapsed && 'lg:justify-center '
          )"
          @click="userMenuOpen = !userMenuOpen"
        >
          <span class="inline-flex h-8 w-8 items-center justify-center rounded-full bg-primary-light text-primary-dark font-semibold text-xs flex-shrink-0">
            {{ userInitial }}
          </span>
          <div class="flex-1 min-w-0 text-left" :class="collapsed && 'lg:hidden'">
            <div class="text-[12.5px] font-medium text-gray-900 truncate leading-tight">
              {{ userEmail }}
            </div>
            <div class="text-[10.5px] text-gray-500 truncate">
              {{ isAdmin ? 'Admin' : 'Thành viên' }}
            </div>
          </div>
          <i :class="cn('bi text-gray-400 text-[10px]', userMenuOpen ? 'bi-chevron-down' : 'bi-chevron-up', collapsed && 'lg:hidden')" />
        </button>

        <Transition
          enter-active-class="transition duration-200"
          leave-active-class="transition duration-150"
          enter-from-class="opacity-0 translate-y-1"
          leave-to-class="opacity-0 translate-y-1"
        >
          <div
            v-if="userMenuOpen"
            class="absolute bottom-full left-2 right-2 mb-2 glass-pop rounded-xl overflow-hidden z-50 min-w-[180px]"
          >
            <NuxtLink
              to="/settings"
              class="flex items-center gap-2 px-3 py-2.5 text-[13px] font-medium text-gray-700 hover:bg-white/[0.04] transition-colors"
              @click="userMenuOpen = false; mobileOpen = false"
            >
              <i class="bi bi-gear text-sm" />
              Cài đặt
            </NuxtLink>
            <button
              type="button"
              class="w-full flex items-center gap-2 px-3 py-2.5 text-[13px] font-medium text-rose-600 hover:bg-rose-50 transition-colors"
              @click="onLogout"
            >
              <i class="bi bi-box-arrow-right text-sm" />
              Đăng xuất
            </button>
          </div>
        </Transition>
      </div>
    </aside>

    <!-- ── Main ────────────────────────────────────────────────────────── -->
    <div class="flex flex-1 min-w-0 flex-col">
      <header class="flex items-center gap-3 h-14 px-3 sm:px-4 flex-shrink-0">
        <!-- Toggle: desktop collapse / mobile drawer -->
        <button
          type="button"
          class="h-9 w-9 hidden lg:flex items-center justify-center rounded-[10px] text-gray-500 hover:bg-white/[0.05] hover:text-gray-900 press transition-colors flex-shrink-0"
          :title="collapsed ? 'Mở rộng sidebar' : 'Thu gọn sidebar'"
          @click="collapsed = !collapsed"
        >
          <i class="bi bi-layout-sidebar-inset text-[17px]" />
        </button>
        <button
          type="button"
          class="h-9 w-9 flex lg:hidden items-center justify-center rounded-[10px] text-gray-500 hover:bg-white/[0.05] hover:text-gray-900 press transition-colors flex-shrink-0"
          title="Mở menu"
          @click="mobileOpen = true"
        >
          <i class="bi bi-list text-[19px]" />
        </button>

        <div class="min-w-0 flex-1 flex items-baseline gap-3">
          <h1 class="text-[17px] font-semibold tracking-tight text-gray-900 truncate leading-none">
            {{ pageTitle }}
          </h1>
          <p class="hidden md:block text-[12px] text-gray-500 font-normal truncate whitespace-nowrap">
            {{ pageSubtitle }}
          </p>
        </div>

        <!-- ALD 06/07/2026 - Toggle theme sáng/tối -->
        <button
          type="button"
          class="h-9 w-9 flex items-center justify-center rounded-[10px] text-gray-500 hover:bg-white/[0.05] hover:text-gray-900 press transition-colors flex-shrink-0"
          :title="isLight ? 'Chuyển theme tối' : 'Chuyển theme sáng'"
          @click="toggleTheme"
        >
          <i :class="['bi text-[16px]', isLight ? 'bi-moon-stars' : 'bi-sun']" />
        </button>

        <NotificationBell />
      </header>

      <div class="flex flex-1 min-h-0 flex-col">
        <slot />
      </div>
    </div>
  </div>
  <!-- #endregion -->
</template>

<script setup>
import { useStorage } from '@vueuse/core'

const route = useRoute()
const appConfig = useAppConfig()
const auth = useAuth()
// ALD 06/07/2026 - Theme sáng/tối (persist localStorage, class trên <html>)
const { isLight, toggle: toggleTheme } = useTheme()
const motionWorker = useMotionWorkerStatus()
const activeJobs = useActiveJobs()
onMounted(() => activeJobs.start(10000))
onBeforeUnmount(() => activeJobs.stop())

// #region ALD 06/07/2026 - Sidebar v2: desktop luôn hiển thị + collapse icon-rail (persist);
// mobile dùng drawer overlay riêng. Key localStorage mới, không đụng key drawer cũ.
const collapsed = useStorage('motions_sidebar_collapsed', false)
const mobileOpen = ref(false)
// #endregion

// Nav items: Workflows / Audio / Storage / Social / VPS / Settings
const navItems = [
  { to: '/',          icon: 'bi-house-door',         label: 'Tổng quan' },
  { to: '/workflows', icon: 'bi-diagram-3',          label: 'Workflows' },
  { to: '/audio',     icon: 'bi-music-note-beamed',  label: 'Audio library' },
  { to: '/storage',   icon: 'bi-hdd',                label: 'Storage' },
  { to: '/social',    icon: 'bi-share',              label: 'Social Management' },
  { to: '/admin/vps', icon: 'bi-activity',           label: 'VPS Monitor', adminOnly: true },
  // ALD 06/07/2026 - Trang hướng dẫn node + thông số
  { to: '/guide',     icon: 'bi-book',               label: 'Hướng dẫn' },
  { to: '/settings',  icon: 'bi-sliders',            label: 'Cài đặt' }
]

const visibleNavItems = computed(() => navItems.filter((n) => !n.adminOnly || isAdmin.value))

function isActive(to) {
  if (to === '/') return route.path === '/'
  return route.path === to || route.path.startsWith(`${to}/`)
}

// Page title từ route, fallback theo nav item
const pageTitle = computed(() => {
  const meta = route.meta?.title
  if (meta) return meta
  const item = visibleNavItems.value.find((n) => isActive(n.to))
  if (item && item.to !== '/') return item.label
  if (route.path.startsWith('/workflows/')) return 'Workflow editor'
  return 'Tổng quan'
})

const pageSubtitle = computed(() => {
  const sub = route.meta?.subtitle
  if (sub) return sub
  if (route.path === '/')           return 'Quản lý workflows motion-video'
  if (route.path === '/marketing')  return 'Sản phẩm + người mẫu + kịch bản → video TTS lip-sync'
  if (route.path === '/workflows')  return 'Tất cả workflows'
  if (route.path === '/audio')      return 'Thư viện audio (mp3/wav/m4a/ogg/flac)'
  if (route.path === '/runs')       return 'Lịch sử lần chạy'
  if (route.path === '/storage')    return 'File ảnh / video / output'
  if (route.path === '/social')     return 'Kết nối, đăng và lên lịch nội dung MXH'
  if (route.path === '/reports')    return 'Báo cáo chất lượng AI — token / timing / output'
  if (route.path === '/admin/vps')  return 'Theo dõi tải VPS realtime và giải phóng tài nguyên'
  if (route.path === '/settings')   return 'Tài khoản & cấu hình'
  if (route.path.startsWith('/workflows/')) return 'Thiết kế graph'
  return ''
})

const userMenuOpen = ref(false)
const userMenuWrapRef = ref(null)

// Click outside đóng user menu
function onUserMenuOutside(e) {
  if (userMenuOpen.value && userMenuWrapRef.value && !userMenuWrapRef.value.contains(e.target)) {
    userMenuOpen.value = false
  }
}
onMounted(() => document.addEventListener('mousedown', onUserMenuOutside))
onBeforeUnmount(() => document.removeEventListener('mousedown', onUserMenuOutside))

// GPU + motion-worker status poller
onMounted(() => {
  if (auth.isAuthenticated.value) {
    motionWorker.start()
  }
})
onBeforeUnmount(() => { motionWorker.stop() })
watch(() => auth.isAuthenticated.value, (val) => {
  if (val) {
    motionWorker.start()
  } else {
    motionWorker.stop()
  }
})

// Decode JWT cho role + email
const sessionClaim = computed(() => {
  return decodeJwtPayload(auth.token.value)
})
const userEmail = computed(() => sessionClaim.value?.email || 'Khách')
const isAdmin = computed(() => sessionClaim.value?.role === 'admin')
const userInitial = computed(() => (userEmail.value || '?').slice(0, 1).toUpperCase())

async function onNewWorkflow() {
  mobileOpen.value = false
  await navigateTo('/workflows?new=1')
}

async function onLogout() {
  userMenuOpen.value = false
  auth.logout()
  await navigateTo('/login')
}

// Đóng menu khi route đổi
watch(() => route.path, () => {
  userMenuOpen.value = false
  mobileOpen.value = false
})
</script>
