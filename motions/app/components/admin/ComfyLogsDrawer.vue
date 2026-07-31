<template>
  <!-- #region ALD 13/07/2026 - Nút sticky + bottom drawer stream PM2 logs ComfyUI. -->
  <div v-if="isAdmin" class="comfy-log-layer" :class="{ 'is-open': open }">
    <button
      v-if="!open"
      type="button"
      class="comfy-log-trigger"
      aria-label="Mở log ComfyUI"
      @click="open = true"
    >
      <i class="bi bi-terminal" />
      <span>PM2 / ComfyUI</span>
      <span :class="['comfy-log-trigger-state', triggerStateClass]">{{ triggerState }}</span>
    </button>

    <Transition name="comfy-log-drawer">
      <section
        v-if="open"
        ref="drawerEl"
        class="comfy-log-drawer"
        role="dialog"
        aria-label="Log realtime ComfyUI"
        tabindex="-1"
        @keydown.esc="open = false"
      >
        <header class="comfy-log-head">
          <div class="min-w-0">
            <div class="flex items-center gap-2">
              <span class="comfy-log-title"><i class="bi bi-terminal-fill" /> pm2 logs comfyui</span>
              <span :class="['comfy-log-status', statusClass]">{{ statusLabel }}</span>
            </div>
            <p class="comfy-log-meta">{{ metaMessage }}</p>
          </div>
          <div class="comfy-log-actions">
            <button type="button" :class="['comfy-log-action', follow && 'is-active']" :title="follow ? 'Đang tự cuộn theo log mới' : 'Bật tự cuộn'" @click="enableFollow">
              <i class="bi bi-arrow-down-circle" /><span class="hidden sm:inline">Theo cuối</span>
            </button>
            <button type="button" class="comfy-log-action" title="Sao chép log đang hiển thị" @click="copyLogs">
              <i :class="['bi', copied ? 'bi-check2' : 'bi-copy']" /><span class="hidden sm:inline">{{ copied ? 'Đã chép' : 'Sao chép' }}</span>
            </button>
            <button type="button" class="comfy-log-action" title="Xóa log trên màn hình" @click="entries = []">
              <i class="bi bi-trash3" /><span class="hidden sm:inline">Xóa màn hình</span>
            </button>
            <button type="button" class="comfy-log-close" aria-label="Đóng log ComfyUI" @click="open = false">
              <i class="bi bi-x-lg" />
            </button>
          </div>
        </header>

        <div ref="terminalEl" class="comfy-log-terminal" @scroll="onTerminalScroll">
          <div v-if="!entries.length" class="comfy-log-empty">
            <i :class="['bi', status === 'error' ? 'bi-exclamation-triangle' : 'bi-hourglass-split']" />
            <span>{{ status === 'error' ? metaMessage : 'Đang chờ dữ liệu từ ComfyUI...' }}</span>
          </div>
          <p v-for="entry in entries" :key="entry.id" :class="['comfy-log-line', `is-${entry.level}`]">
            <span class="comfy-log-prefix">{{ levelLabel(entry.level) }}</span>
            <span>{{ entry.line }}</span>
          </p>
        </div>
      </section>
    </Transition>
  </div>
  <!-- #endregion -->
</template>

<script setup>
const auth = useAuth()
const config = useRuntimeConfig()
const open = ref(false)
const status = ref('idle')
const metaMessage = ref('Chỉ mở kết nối khi drawer được bật')
const entries = ref([])
const follow = ref(true)
const copied = ref(false)
const drawerEl = ref(null)
const terminalEl = ref(null)
let es = null
let seq = 0
let copyTimer = null

const isAdmin = computed(() => decodeJwtPayload(auth.token.value)?.role === 'admin')
const statusLabel = computed(() => ({
  live: 'Realtime', connecting: 'Đang kết nối', waiting: 'Đang đợi', error: 'Mất kết nối', idle: 'Chưa mở',
}[status.value] || status.value))
const statusClass = computed(() => `is-${status.value}`)
const triggerState = computed(() => status.value === 'error' ? 'Lỗi' : status.value === 'live' ? 'Live' : 'Logs')
const triggerStateClass = computed(() => status.value === 'error' ? 'is-error' : status.value === 'live' ? 'is-live' : '')

function streamUrl() {
  const base = String(config.public.motionBackendUrl || '').replace(/\/$/, '')
  return `${base}/admin/server-monitor/comfy-logs/stream?token=${encodeURIComponent(auth.token.value || '')}`
}

function stop() {
  es?.close()
  es = null
  if (status.value !== 'error') status.value = 'idle'
}

function start() {
  stop()
  if (!import.meta.client || !open.value || !auth.token.value) return
  status.value = 'connecting'
  metaMessage.value = 'Đang mở luồng log bảo mật...'
  es = new EventSource(streamUrl())
  es.addEventListener('meta', (event) => {
    const data = JSON.parse(event.data || '{}')
    if (data.type === 'snapshot') {
      entries.value = []
      status.value = data.found ? 'connecting' : 'waiting'
      metaMessage.value = data.found ? `Đã tìm thấy ${data.files?.length || 0} file log PM2` : 'Chưa tìm thấy file log PM2 của ComfyUI'
    } else if (data.type === 'live') {
      status.value = 'live'
      metaMessage.value = data.message || 'Đang theo dõi log realtime'
    } else if (data.type === 'waiting') {
      status.value = 'waiting'
      metaMessage.value = data.message || 'Đang chờ file log xuất hiện'
    } else if (data.type === 'error') {
      status.value = 'error'
      metaMessage.value = data.message || 'Không đọc được log ComfyUI'
    }
  })
  es.addEventListener('log', (event) => {
    const data = JSON.parse(event.data || '{}')
    const level = ['error', 'warning', 'info', 'out'].includes(data.level)
      ? data.level
      : data.stream === 'error' ? 'info' : 'out'
    entries.value.push({ id: ++seq, stream: data.stream === 'error' ? 'error' : 'out', level, line: String(data.line || '') })
    if (entries.value.length > 2000) entries.value.splice(0, entries.value.length - 2000)
    if (follow.value) nextTick(scrollToEnd)
  })
  es.onopen = () => {
    if (status.value === 'connecting') metaMessage.value = 'Đã kết nối, đang nạp log gần nhất...'
  }
  es.onerror = () => {
    status.value = 'error'
    metaMessage.value = 'Mất kết nối log hoặc session đã hết hạn'
  }
}

function scrollToEnd() {
  if (terminalEl.value) terminalEl.value.scrollTop = terminalEl.value.scrollHeight
}
function levelLabel(level) {
  return ({ error: 'ERR', warning: 'WARN', info: 'INFO', out: 'OUT' })[level] || 'OUT'
}
function enableFollow() {
  follow.value = true
  nextTick(scrollToEnd)
}
function onTerminalScroll() {
  const el = terminalEl.value
  if (!el) return
  follow.value = el.scrollHeight - el.scrollTop - el.clientHeight < 48
}
async function copyLogs() {
  try {
    await navigator.clipboard.writeText(entries.value.map((e) => `[${levelLabel(e.level)}] ${e.line}`).join('\n'))
    copied.value = true
    clearTimeout(copyTimer)
    copyTimer = setTimeout(() => { copied.value = false }, 1600)
  } catch {
    copied.value = false
  }
}

watch(open, async (value) => {
  if (!value) return stop()
  await nextTick()
  drawerEl.value?.focus()
  start()
})
watch(() => auth.token.value, () => { if (open.value) start() })
onBeforeUnmount(() => {
  stop()
  clearTimeout(copyTimer)
})
</script>

<style scoped>
.comfy-log-layer {
  position: absolute;
  inset: 0;
  z-index: 40;
  pointer-events: none;
}
.comfy-log-trigger,
.comfy-log-drawer { pointer-events: auto; }
.comfy-log-trigger {
  position: absolute;
  left: 50%;
  bottom: 14px;
  transform: translateX(-50%);
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  min-height: 36px;
  padding: 0 0.55rem 0 0.8rem;
  border: 1px solid rgba(255,255,255,0.14);
  border-radius: 11px;
  background: rgba(19,21,27,0.94);
  box-shadow: 0 10px 30px rgba(0,0,0,0.24), inset 0 1px 0 rgba(255,255,255,0.07);
  color: #f0f2f5;
  font: 600 12px/1 ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  white-space: nowrap;
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
}
.comfy-log-trigger:hover { background: rgba(28,31,39,0.98); border-color: rgba(255,255,255,0.24); }
.comfy-log-trigger:active { transform: translateX(-50%) translateY(1px); }
.comfy-log-trigger-state {
  min-width: 38px;
  padding: 0.3rem 0.45rem;
  border-radius: 7px;
  background: rgba(255,255,255,0.08);
  color: #aeb4bf;
  font-size: 10px;
  text-align: center;
}
.comfy-log-trigger-state.is-live { background: rgba(52,199,89,0.16); color: #7cdda0; }
.comfy-log-trigger-state.is-error { background: rgba(255,59,48,0.16); color: #ff8b84; }
.comfy-log-drawer {
  position: absolute;
  right: 12px;
  bottom: 12px;
  left: 12px;
  display: flex;
  height: min(420px, calc(100% - 24px));
  min-height: 230px;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid rgba(255,255,255,0.13);
  border-radius: 14px;
  background: #111319;
  box-shadow: 0 24px 70px rgba(0,0,0,0.38), inset 0 1px 0 rgba(255,255,255,0.05);
  outline: none;
}
.comfy-log-head {
  display: flex;
  min-height: 54px;
  flex-shrink: 0;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  padding: 0.55rem 0.65rem 0.55rem 0.9rem;
  border-bottom: 1px solid rgba(255,255,255,0.08);
  background: #191c23;
}
.comfy-log-title { display: inline-flex; align-items: center; gap: 0.45rem; color: #f1f3f6; font: 650 12px/1.2 ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }
.comfy-log-status { border-radius: 6px; padding: 0.25rem 0.45rem; background: rgba(255,255,255,0.07); color: #aeb4bf; font: 650 9px/1 ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; text-transform: uppercase; letter-spacing: 0.06em; }
.comfy-log-status.is-live { background: rgba(52,199,89,0.14); color: #7cdda0; }
.comfy-log-status.is-error { background: rgba(255,59,48,0.14); color: #ff8b84; }
.comfy-log-status.is-connecting,
.comfy-log-status.is-waiting { background: rgba(94,106,210,0.2); color: #aeb5ff; }
.comfy-log-meta { margin-top: 0.25rem; max-width: 62vw; overflow: hidden; color: #747b87; font-size: 10px; line-height: 1.2; text-overflow: ellipsis; white-space: nowrap; }
.comfy-log-actions { display: flex; flex-shrink: 0; align-items: center; gap: 0.35rem; }
.comfy-log-action,
.comfy-log-close { display: inline-flex; min-height: 31px; align-items: center; justify-content: center; gap: 0.35rem; border: 1px solid rgba(255,255,255,0.09); border-radius: 8px; background: rgba(255,255,255,0.04); color: #aeb4bf; font-size: 10px; font-weight: 650; padding: 0 0.55rem; white-space: nowrap; }
.comfy-log-action:hover,
.comfy-log-close:hover,
.comfy-log-action.is-active { background: rgba(255,255,255,0.09); color: #f1f3f6; }
.comfy-log-close { width: 31px; padding: 0; color: #d6dae0; }
.comfy-log-terminal { flex: 1; min-height: 0; overflow: auto; padding: 0.65rem 0.8rem 1rem; color: #c8cdd6; overscroll-behavior: contain; scrollbar-color: #454b57 transparent; }
.comfy-log-line { display: grid; grid-template-columns: 28px minmax(0, 1fr); gap: 0.55rem; margin: 0; padding: 1px 0; font: 11px/1.55 ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; white-space: pre-wrap; overflow-wrap: anywhere; }
.comfy-log-line.is-error { color: #ffaaa5; }
.comfy-log-line.is-warning { color: #e8c878; }
.comfy-log-line.is-info { color: #c8cdd6; }
.comfy-log-prefix { color: #596170; font-size: 9px; font-weight: 700; line-height: 1.9; user-select: none; }
.comfy-log-line.is-error .comfy-log-prefix { color: #d85a54; }
.comfy-log-line.is-warning .comfy-log-prefix { color: #b99645; }
.comfy-log-line.is-info .comfy-log-prefix { color: #687da8; }
.comfy-log-empty { display: flex; min-height: 100%; align-items: center; justify-content: center; gap: 0.55rem; color: #747b87; font: 11px/1.5 ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; text-align: center; }
.comfy-log-drawer-enter-active,
.comfy-log-drawer-leave-active { transition: transform 160ms ease, opacity 140ms ease; }
.comfy-log-drawer-enter-from,
.comfy-log-drawer-leave-to { transform: translateY(14px); opacity: 0; }
@media (max-width: 639px) {
  .comfy-log-drawer { right: 6px; bottom: 6px; left: 6px; height: min(62dvh, calc(100% - 12px)); border-radius: 12px; }
  .comfy-log-head { align-items: flex-start; padding: 0.6rem; }
  .comfy-log-meta { max-width: 45vw; }
  .comfy-log-action { width: 31px; padding: 0; }
  .comfy-log-trigger { bottom: 10px; }
}
@media (prefers-reduced-motion: reduce) {
  .comfy-log-drawer-enter-active,
  .comfy-log-drawer-leave-active { transition: none; }
}
</style>
