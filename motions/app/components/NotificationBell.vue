<template>
  <!-- #region ALD 25/05/2026 - Bell icon + dropdown panel. Apple HIG: glass blur,
       System colors, hairline border. Click outside để đóng. -->
  <div class="apl-noti-wrapper" v-click-outside="closePanel">
    <button
      type="button"
      :class="['apl-noti-bell', noti.unreadCount.value > 0 && 'has-unread']"
      :title="noti.unreadCount.value > 0 ? `${noti.unreadCount.value} thông báo mới` : 'Thông báo'"
      @click="togglePanel"
    >
      <i :class="['bi', noti.unreadCount.value > 0 ? 'bi-bell-fill' : 'bi-bell']" />
      <span v-if="noti.unreadCount.value > 0" class="apl-noti-badge">
        {{ noti.unreadCount.value > 99 ? '99+' : noti.unreadCount.value }}
      </span>
    </button>

    <Transition name="apl-noti-pop">
      <div v-if="noti.open.value" class="apl-noti-panel">
        <div class="apl-noti-header">
          <span class="apl-noti-title">
            <i class="bi bi-bell-fill text-primary" />
            Thông báo
          </span>
          <button v-if="noti.unreadCount.value > 0" type="button" class="apl-noti-action" @click="noti.markAllRead()" title="Đánh dấu đã đọc">
            <i class="bi bi-check2-all" />
          </button>
          <button v-if="noti.items.value.length" type="button" class="apl-noti-action" @click="onClear" title="Xoá tất cả">
            <i class="bi bi-trash" />
          </button>
        </div>

        <div v-if="noti.items.value.length === 0" class="apl-noti-empty">
          <i class="bi bi-bell-slash text-gray-300 text-3xl" />
          <p class="text-xs text-gray-400 mt-2">Chưa có thông báo</p>
        </div>

        <ul v-else class="apl-noti-list">
          <li
            v-for="n in noti.items.value"
            :key="n.id"
            :class="['apl-noti-item', !n.read && 'is-unread', `kind-${n.kind}`]"
            @click="onItemClick(n)"
          >
            <span class="apl-noti-dot" v-if="!n.read" />
            <i :class="['apl-noti-icon bi', iconFor(n.kind)]" />
            <div class="apl-noti-body">
              <div class="apl-noti-row-title">{{ n.title }}</div>
              <div v-if="n.body" class="apl-noti-row-body">{{ n.body }}</div>
              <div class="apl-noti-row-meta">{{ fmtAgo(n.ts) }}</div>
            </div>
            <button type="button" class="apl-noti-remove" @click.stop="noti.remove(n.id)" title="Xoá">
              <i class="bi bi-x" />
            </button>
          </li>
        </ul>
      </div>
    </Transition>
  </div>
  <!-- #endregion -->
</template>

<script setup>
const noti = useNotifications()
const router = useRouter()

function togglePanel() {
  noti.open.value = !noti.open.value
  if (noti.open.value) noti.requestBrowserPermission()
}
function closePanel() {
  noti.open.value = false
}
async function onClear() {
  const ok = await useConfirm().ask({
    title: 'Xoá tất cả thông báo?',
    message: 'Sẽ xoá vĩnh viễn toàn bộ danh sách. Không khôi phục được.',
    confirmText: 'Xoá',
    cancelText: 'Huỷ',
    variant: 'danger',
  })
  if (ok) noti.clear()
}
function onItemClick(n) {
  noti.markRead(n.id)
  if (n.workflowId) {
    router.push(`/workflows/${n.workflowId}`)
    closePanel()
  }
}
function iconFor(kind) {
  if (kind === 'done')   return 'bi-check-circle-fill text-emerald-500'
  if (kind === 'error')  return 'bi-x-circle-fill text-rose-500'
  if (kind === 'cancel') return 'bi-slash-circle text-gray-500'
  return 'bi-info-circle text-blue-500'
}
function fmtAgo(ts) {
  const diff = Math.floor((Date.now() - ts) / 1000)
  if (diff < 60) return 'vừa xong'
  if (diff < 3600) return `${Math.floor(diff / 60)} phút trước`
  if (diff < 86400) return `${Math.floor(diff / 3600)} giờ trước`
  return new Date(ts).toLocaleString('vi-VN', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
}

// Click outside directive — đơn giản inline (không cần composable riêng)
const vClickOutside = {
  mounted(el, binding) {
    el.__clickOutside__ = (e) => {
      if (!(el === e.target || el.contains(e.target))) binding.value(e)
    }
    document.addEventListener('click', el.__clickOutside__)
  },
  unmounted(el) {
    document.removeEventListener('click', el.__clickOutside__)
  }
}
</script>

<style scoped>
.apl-noti-wrapper {
  position: relative;
  display: inline-flex;
}
.apl-noti-bell {
  position: relative;
  width: 36px; height: 36px;
  display: inline-flex; align-items: center; justify-content: center;
  background: rgba(255, 255, 255, 0.5);
  backdrop-filter: blur(20px);
  border: 0.5px solid rgba(255, 255, 255, 0.6);
  border-radius: 999px;
  color: rgba(235, 236, 240, 0.85);
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.apl-noti-bell:hover {
  background: rgba(255, 255, 255, 0.85);
  color: var(--apl-primary, #9AA2F2);
}
.apl-noti-bell.has-unread {
  color: var(--apl-primary, #9AA2F2);
  animation: bell-shake 1.5s ease-in-out infinite;
}
@keyframes bell-shake {
  0%, 50%, 100% { transform: rotate(0); }
  10% { transform: rotate(-10deg); }
  20% { transform: rotate(8deg); }
  30% { transform: rotate(-6deg); }
  40% { transform: rotate(4deg); }
}
.apl-noti-badge {
  position: absolute;
  top: -2px; right: -2px;
  min-width: 16px; height: 16px; padding: 0 4px;
  display: inline-flex; align-items: center; justify-content: center;
  background: #FF3B30;
  color: white;
  font-size: 9.5px; font-weight: 800;
  border-radius: 999px;
  border: 1.5px solid white;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}

.apl-noti-panel {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  width: 360px;
  max-height: 70vh;
  display: flex; flex-direction: column;
  background: rgba(255, 255, 255, 0.92);
  backdrop-filter: blur(40px) saturate(180%);
  border: 0.5px solid rgba(255,255,255,0.1);
  border-radius: 16px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.18), 0 0 0 0.5px rgba(0, 0, 0, 0.04);
  z-index: 1000;
  overflow: hidden;
}
.apl-noti-header {
  display: flex; align-items: center; gap: 8px;
  padding: 12px 14px;
  border-bottom: 0.5px solid rgba(255,255,255,0.1);
}
.apl-noti-title {
  flex: 1;
  font-size: 13px; font-weight: 700;
  color: rgba(235, 236, 240, 0.85);
}
.apl-noti-action {
  width: 28px; height: 28px;
  display: inline-flex; align-items: center; justify-content: center;
  background: transparent; border: none; border-radius: 8px;
  color: rgba(235, 236, 240, 0.55);
  cursor: pointer;
  transition: background 0.1s, color 0.1s;
}
.apl-noti-action:hover { background: rgba(118, 118, 128, 0.1); color: rgba(235, 236, 240, 0.9); }

.apl-noti-empty {
  flex: 1;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  padding: 40px 20px;
}

.apl-noti-list {
  flex: 1;
  overflow-y: auto;
  margin: 0; padding: 4px;
  list-style: none;
}
.apl-noti-item {
  position: relative;
  display: flex; gap: 10px; align-items: flex-start;
  padding: 10px 12px;
  border-radius: 10px;
  cursor: pointer;
  transition: background 0.1s;
}
.apl-noti-item:hover { background: rgba(118, 118, 128, 0.08); }
.apl-noti-item.is-unread { background: rgba(0, 122, 255, 0.05); }
.apl-noti-item.is-unread:hover { background: rgba(0, 122, 255, 0.09); }
.apl-noti-dot {
  position: absolute;
  left: 4px; top: 16px;
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--apl-primary, #007AFF);
}
.apl-noti-icon { font-size: 18px; flex-shrink: 0; margin-top: 1px; margin-left: 4px; }
.apl-noti-body { flex: 1; min-width: 0; }
.apl-noti-row-title {
  font-size: 12.5px; font-weight: 600;
  color: rgba(235, 236, 240, 0.95);
  line-height: 1.35;
}
.apl-noti-row-body {
  font-size: 11px;
  color: rgba(235, 236, 240, 0.7);
  margin-top: 2px;
  line-height: 1.4;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.apl-noti-row-meta {
  font-size: 10px;
  color: rgba(235, 236, 240, 0.45);
  margin-top: 4px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.apl-noti-remove {
  width: 24px; height: 24px;
  display: inline-flex; align-items: center; justify-content: center;
  background: transparent; border: none; border-radius: 6px;
  color: rgba(235, 236, 240, 0.4);
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s, background 0.1s, color 0.1s;
  flex-shrink: 0;
}
.apl-noti-item:hover .apl-noti-remove { opacity: 1; }
.apl-noti-remove:hover { background: rgba(255, 59, 48, 0.12); color: #FF3B30; }

/* Pop-in animation */
.apl-noti-pop-enter-active, .apl-noti-pop-leave-active {
  transition: opacity 0.18s ease, transform 0.18s cubic-bezier(0.34, 1.56, 0.64, 1);
}
.apl-noti-pop-enter-from, .apl-noti-pop-leave-to {
  opacity: 0;
  transform: translateY(-8px) scale(0.96);
}
</style>
