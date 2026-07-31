<template>
  <!-- #region ALD 22/05/2026 - Run history: list 50 run gần nhất, click 1 → modal events + output -->
  <div class="flex flex-1 flex-col min-h-0 px-3 sm:px-6 md:pr-6 md:pl-0 overflow-hidden">
    <div class="max-w-5xl w-full mx-auto pt-3 pb-3 flex flex-col flex-1 min-h-0 space-y-3">
      <div class="flex items-center justify-between gap-3 px-1 flex-shrink-0">
        <div class="flex items-baseline gap-3 min-w-0">
          <NuxtLink :to="`/workflows/${route.params.id}`" class="text-xs text-primary hover:underline">← Editor</NuxtLink>
          <h1 class="text-2xl font-black tracking-tighter title-gradient">Run history</h1>
          <p v-if="workflow" class="hidden sm:block text-[12px] text-gray-500 font-bold border-l border-gray-200 pl-3">
            /{{ workflow.slug }}
          </p>
        </div>
        <button type="button" class="press inline-flex items-center gap-1.5 h-9 px-3 rounded-full glass shadow-card text-xs font-bold text-gray-700 hover:bg-gray-100" @click="reload">
          <i :class="['bi', loading ? 'bi-hourglass-split animate-pulse' : 'bi-arrow-clockwise']" />
          Refresh
        </button>
      </div>

      <section class="flex-1 min-w-0 overflow-y-auto pr-1 pb-4">
        <div v-if="loading && !runs.length" class="text-center text-xs text-gray-400 py-8">Đang tải...</div>
        <div v-else-if="!runs.length" class="text-center py-12">
          <i class="bi bi-clock-history text-5xl text-gray-300" />
          <p class="text-sm text-gray-500 mt-3">Workflow chưa được run lần nào.</p>
        </div>
        <table v-else class="w-full text-sm bg-gray-50 rounded-2xl border border-gray-200 shadow-card overflow-hidden">
          <thead class="bg-gray-50 text-[0.7rem] uppercase tracking-wider text-gray-500">
            <tr>
              <th class="px-3 py-2.5 text-left font-bold">Trạng thái</th>
              <th class="px-3 py-2.5 text-left font-bold">Auth</th>
              <th class="px-3 py-2.5 text-left font-bold">Bắt đầu</th>
              <th class="px-3 py-2.5 text-right font-bold">Duration</th>
              <th class="px-3 py-2.5 text-left font-bold">Lỗi</th>
              <th class="w-10 px-3 py-2.5" />
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in runs" :key="r.id" class="border-t border-gray-100 hover:bg-gray-50 transition-colors cursor-pointer" @click="openRun(r.id)">
              <td class="px-3 py-2.5">
                <span :class="['inline-flex items-center gap-1 text-xs font-bold', statusColor(r.status)]">
                  <i :class="['bi', statusIcon(r.status)]" />
                  {{ r.status }}
                </span>
              </td>
              <td class="px-3 py-2.5 text-xs text-gray-500 font-mono">{{ r.auth_method }}</td>
              <td class="px-3 py-2.5 text-xs text-gray-600">{{ fmtDate(r.started_at) }}</td>
              <td class="px-3 py-2.5 text-right text-xs text-gray-600 font-mono">{{ fmtDuration(r.started_at, r.finished_at) }}</td>
              <td class="px-3 py-2.5 text-xs text-rose-600 truncate max-w-xs" :title="r.error_msg">{{ r.error_msg || '—' }}</td>
              <td class="px-3 py-2.5 text-right">
                <i class="bi bi-chevron-right text-gray-300" />
              </td>
            </tr>
          </tbody>
        </table>

        <!-- ALD 11/07/2026 - Pager: 10 run/trang -->
        <div v-if="total > 0" class="flex items-center justify-between gap-3 mt-3 px-1 text-xs text-gray-500">
          <span class="font-medium">{{ rangeFrom }}–{{ rangeTo }} / {{ total }} run</span>
          <div class="flex items-center gap-1.5">
            <button type="button" class="press h-8 px-2.5 rounded-full glass shadow-card font-bold text-gray-700 disabled:opacity-40 disabled:pointer-events-none hover:bg-gray-100"
              :disabled="page <= 0 || loading" @click="goPage(page - 1)">
              <i class="bi bi-chevron-left" />
            </button>
            <span class="px-1 font-bold text-gray-600">Trang {{ page + 1 }}/{{ pageCount }}</span>
            <button type="button" class="press h-8 px-2.5 rounded-full glass shadow-card font-bold text-gray-700 disabled:opacity-40 disabled:pointer-events-none hover:bg-gray-100"
              :disabled="page >= pageCount - 1 || loading" @click="goPage(page + 1)">
              <i class="bi bi-chevron-right" />
            </button>
          </div>
        </div>
      </section>
    </div>

    <!-- Run detail modal -->
    <Transition enter-active-class="transition duration-200" leave-active-class="transition duration-150" enter-from-class="opacity-0" leave-to-class="opacity-0">
      <div v-if="selectedRun" class="fixed inset-0 z-50 bg-black/40 backdrop-blur-sm flex items-center justify-center p-4" @click.self="selectedRun = null">
        <div class="bg-gray-50 rounded-3xl shadow-island-lg max-w-3xl w-full max-h-[90vh] overflow-hidden flex flex-col">
          <div class="flex items-center justify-between p-4 border-b border-gray-200">
            <div>
              <h2 class="text-base font-bold">Run {{ selectedRun.id.slice(0, 8) }}</h2>
              <p class="text-xs text-gray-500 font-mono">{{ fmtDate(selectedRun.started_at) }} · {{ fmtDuration(selectedRun.started_at, selectedRun.finished_at) }}</p>
            </div>
            <button type="button" class="text-gray-400 hover:text-gray-600 p-1" @click="selectedRun = null"><i class="bi bi-x-lg text-lg" /></button>
          </div>
          <div class="flex-1 overflow-y-auto p-4 space-y-3 text-xs">
            <div v-if="selectedRun.error_msg" class="bg-rose-50 border border-rose-200 rounded-2xl p-3 text-rose-700">
              <i class="bi bi-exclamation-triangle mr-1" />
              {{ selectedRun.error_msg }}
            </div>
            <div>
              <p class="font-bold text-gray-600 uppercase text-[10px] mb-1">Input</p>
              <pre class="bg-gray-50 p-2.5 rounded-xl overflow-x-auto text-[11px]">{{ JSON.stringify(selectedRun.input, null, 2) }}</pre>
            </div>
            <div v-if="selectedRun.output">
              <p class="font-bold text-gray-600 uppercase text-[10px] mb-1">Output</p>
              <pre class="bg-gray-50 p-2.5 rounded-xl overflow-x-auto text-[11px] max-h-60">{{ JSON.stringify(selectedRun.output, null, 2) }}</pre>
            </div>
            <div>
              <p class="font-bold text-gray-600 uppercase text-[10px] mb-1">Events ({{ selectedRun.events?.length || 0 }})</p>
              <ul class="space-y-0.5 font-mono">
                <li v-for="(ev, idx) in selectedRun.events || []" :key="idx" class="text-[11px] flex items-start gap-1.5">
                  <span class="text-gray-400 flex-shrink-0">{{ fmtTime(ev.ts) }}</span>
                  <span :class="[levelColor(ev.level), 'flex-shrink-0 font-bold']">[{{ ev.level }}]</span>
                  <span class="text-gray-700 break-words">{{ ev.msg }}</span>
                </li>
              </ul>
            </div>
          </div>
        </div>
      </div>
    </Transition>
  </div>
  <!-- #endregion -->
</template>

<script setup>
definePageMeta({ middleware: 'auth' })

const route = useRoute()
const wf = useWorkflows()
const toast = useToast()
const workflow = ref(null)
const runs = ref([])
const selectedRun = ref(null)
const loading = ref(false)

// ALD 11/07/2026 - Phân trang 10 run/trang (list cũ đổ 50 dòng → chậm). page 0-based.
const PAGE_SIZE = 10
const page = ref(0)
const total = ref(0)
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))
const rangeFrom = computed(() => (total.value === 0 ? 0 : page.value * PAGE_SIZE + 1))
const rangeTo = computed(() => Math.min(total.value, (page.value + 1) * PAGE_SIZE))

async function reload() {
  loading.value = true
  try {
    if (!workflow.value) workflow.value = await wf.get(route.params.id)
    const res = await wf.listRuns(route.params.id, { limit: PAGE_SIZE, offset: page.value * PAGE_SIZE })
    runs.value = res.items
    total.value = res.total
    // Nếu xoá bớt khiến trang hiện tại vượt quá số trang → lùi về trang cuối hợp lệ.
    if (page.value > 0 && page.value >= pageCount.value) {
      page.value = pageCount.value - 1
      return reload()
    }
  } catch (err) {
    toast.error(err.data?.error || err.message)
  } finally {
    loading.value = false
  }
}

function goPage(p) {
  const next = Math.min(pageCount.value - 1, Math.max(0, p))
  if (next === page.value) return
  page.value = next
  reload()
}

async function openRun(runId) {
  try {
    selectedRun.value = await wf.getRun(runId)
  } catch (err) {
    toast.error(err.data?.error || err.message)
  }
}

function fmtDate(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleString('vi-VN', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' })
}
function fmtDuration(start, end) {
  if (!start) return '—'
  const ms = (end ? new Date(end).getTime() : Date.now()) - new Date(start).getTime()
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  const m = Math.floor(ms / 60000)
  const s = Math.floor((ms % 60000) / 1000)
  return `${m}m ${s}s`
}
function fmtTime(ts) {
  const d = new Date(ts)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`
}
function statusColor(s) {
  return { success: 'text-emerald-700', error: 'text-rose-700', running: 'text-blue-700', cancelled: 'text-gray-500' }[s] || 'text-gray-600'
}
function statusIcon(s) {
  return { success: 'bi-check-circle-fill', error: 'bi-x-circle-fill', running: 'bi-hourglass-split animate-pulse', cancelled: 'bi-dash-circle' }[s] || 'bi-circle'
}
function levelColor(l) {
  return { info: 'text-blue-600', success: 'text-emerald-600', warn: 'text-amber-600', error: 'text-rose-600' }[l] || 'text-gray-600'
}

onMounted(reload)
</script>
