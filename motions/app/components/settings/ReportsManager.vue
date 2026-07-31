<template>
  <div class="flex flex-col flex-1 min-h-0 space-y-3">
    <!-- #region ALD 21/05/2026 - Stats overview cards -->
    <div class="grid grid-cols-2 lg:grid-cols-4 gap-2">
      <div class="glass shadow-card rounded-2xl px-4 py-3">
        <div class="text-[0.65rem] uppercase tracking-wider text-gray-500 font-bold">Tổng job</div>
        <div class="text-xl font-black text-gray-900 mt-0.5">{{ stats.total ?? '—' }}</div>
        <div class="text-[0.65rem] text-gray-400 mt-0.5 font-mono">
          done {{ stats.byStatus?.done ?? 0 }} · err {{ stats.byStatus?.error ?? 0 }}
        </div>
      </div>
      <div class="glass shadow-card rounded-2xl px-4 py-3">
        <div class="text-[0.65rem] uppercase tracking-wider text-gray-500 font-bold">Avg duration</div>
        <div class="text-xl font-black text-gray-900 mt-0.5">{{ formatMs(stats.avgDurationMs) }}</div>
        <div class="text-[0.65rem] text-gray-400 mt-0.5 font-mono">tổng job hoàn thành</div>
      </div>
      <div class="glass shadow-card rounded-2xl px-4 py-3">
        <div class="text-[0.65rem] uppercase tracking-wider text-gray-500 font-bold">Tokens generated</div>
        <div class="text-xl font-black text-gray-900 mt-0.5">{{ formatNumber(stats.totalCompletionTokens) }}</div>
        <div class="text-[0.65rem] text-gray-400 mt-0.5 font-mono">completion tokens</div>
      </div>
      <div class="glass shadow-card rounded-2xl px-4 py-3">
        <div class="text-[0.65rem] uppercase tracking-wider text-gray-500 font-bold">Avg t/s</div>
        <div class="text-xl font-black text-gray-900 mt-0.5">{{ stats.avgTokensPerSec ?? '—' }}</div>
        <div class="text-[0.65rem] text-gray-400 mt-0.5 font-mono">trung bình throughput</div>
      </div>
    </div>
    <!-- #endregion -->

    <!-- Toolbar: search + filter -->
    <div class="flex items-center gap-2 flex-wrap">
      <div class="flex-1 min-w-[200px] relative">
        <i class="bi bi-search absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 text-sm" />
        <input
          v-model="searchDraft"
          type="text"
          placeholder="Tìm theo job_id / file / model…"
          class="w-full h-10 pl-9 pr-3 rounded-full bg-gray-50 border border-gray-200 text-sm focus:outline-none focus:border-primary"
          @keydown.enter="reload(1)"
          @input="onSearchInput"
        />
      </div>
      <UiDropdown
        v-model="filters.mode"
        :options="modeOptions"
        class="w-32"
        @update:model-value="reload(1)"
      />
      <UiDropdown
        v-model="filters.status"
        :options="statusOptions"
        class="w-32"
        @update:model-value="reload(1)"
      />
    </div>

    <!-- Table -->
    <div class="flex-1 min-h-0 overflow-y-auto bg-gray-50 rounded-2xl border border-gray-200 shadow-card">
      <div v-if="loading" class="text-center py-16 text-xs text-gray-400 italic">Đang tải…</div>
      <div v-else-if="!items.length" class="text-center py-16 text-sm text-gray-400">
        <i class="bi bi-clipboard-data text-4xl text-gray-300 mb-2 block" />
        {{ searchDraft || filters.mode || filters.status ? 'Không có report phù hợp' : 'Chưa có report nào' }}
      </div>
      <table v-else class="w-full text-sm">
        <thead class="sticky top-0 bg-gray-50 z-10 text-[0.65rem] uppercase tracking-wider text-gray-500">
          <tr>
            <th class="px-3 py-2.5 text-left font-bold">Job · User · File</th>
            <th class="px-3 py-2.5 text-left font-bold whitespace-nowrap">Mode</th>
            <th class="px-3 py-2.5 text-left font-bold whitespace-nowrap">Status</th>
            <th class="px-3 py-2.5 text-right font-bold whitespace-nowrap">Tokens</th>
            <th class="px-3 py-2.5 text-right font-bold whitespace-nowrap">t/s</th>
            <th class="px-3 py-2.5 text-right font-bold whitespace-nowrap">Duration</th>
            <th class="px-3 py-2.5 text-right font-bold whitespace-nowrap">Tạo lúc</th>
            <th class="w-24 px-3 py-2.5" />
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="r in items"
            :key="r.id"
            class="border-t border-gray-100 hover:bg-gray-50 transition-colors cursor-pointer"
            @click="openDetail(r.job_id)"
          >
            <td class="px-3 py-2.5 min-w-0">
              <div class="font-mono text-[0.7rem] text-gray-500 truncate max-w-[18rem]">{{ r.job_id.slice(0, 12) }}…</div>
              <div class="text-xs font-semibold text-gray-800 truncate max-w-[18rem]">{{ r.user_email }}</div>
              <div v-if="r.attachment_name" class="text-[0.7rem] text-gray-500 truncate max-w-[18rem]">
                <i class="bi bi-paperclip" /> {{ r.attachment_name }}
              </div>
            </td>
            <td class="px-3 py-2.5">
              <span :class="['inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[0.65rem] font-bold uppercase', modeBadge(r.mode)]">
                <i :class="['bi text-[0.7rem]', modeIcon(r.mode)]" />
                {{ r.mode }}
              </span>
            </td>
            <td class="px-3 py-2.5">
              <span :class="['inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[0.65rem] font-bold uppercase', statusBadge(r.status)]">
                {{ r.status }}
              </span>
            </td>
            <td class="px-3 py-2.5 text-right text-gray-700 font-mono text-xs whitespace-nowrap">
              <span v-if="r.total_tokens">{{ formatNumber(r.total_tokens) }}</span>
              <span v-else class="text-gray-300">—</span>
              <span v-if="r.prompt_tokens && r.completion_tokens" class="block text-[0.6rem] text-gray-400">
                {{ r.prompt_tokens }} in · {{ r.completion_tokens }} out
              </span>
            </td>
            <td class="px-3 py-2.5 text-right text-gray-700 font-mono text-xs whitespace-nowrap">
              {{ r.tokens_per_sec ? r.tokens_per_sec.toFixed(1) : '—' }}
            </td>
            <td class="px-3 py-2.5 text-right text-gray-700 font-mono text-xs whitespace-nowrap">
              {{ formatMs(r.total_duration_ms) }}
            </td>
            <td class="px-3 py-2.5 text-right text-gray-500 text-xs whitespace-nowrap">
              {{ formatDate(r.created_at) }}
            </td>
            <td class="px-3 py-2.5 text-right">
              <button
                type="button"
                class="press h-7 px-2 rounded-md text-[0.65rem] text-gray-500 hover:text-primary hover:bg-blue-50 font-bold uppercase"
                title="Xem detail"
                @click.stop="openDetail(r.job_id)"
              >
                <i class="bi bi-eye" />
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Pagination -->
    <div v-if="total > 0" class="flex items-center justify-between gap-3 flex-wrap text-xs">
      <span class="text-gray-500">
        {{ (page - 1) * limit + 1 }}–{{ Math.min(page * limit, total) }} / {{ total }}
      </span>
      <div class="flex items-center gap-1">
        <button
          type="button"
          :disabled="page <= 1 || loading"
          class="press h-8 w-8 flex items-center justify-center rounded-lg hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed"
          @click="reload(page - 1)"
        >
          <i class="bi bi-chevron-left" />
        </button>
        <span class="px-3 font-mono text-gray-700">{{ page }} / {{ totalPages }}</span>
        <button
          type="button"
          :disabled="page >= totalPages || loading"
          class="press h-8 w-8 flex items-center justify-center rounded-lg hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed"
          @click="reload(page + 1)"
        >
          <i class="bi bi-chevron-right" />
        </button>
      </div>
    </div>

    <!-- #region ALD 21/05/2026 - Detail panel (slide từ phải, giống hrm-hub PermissionPanel) -->
    <Teleport to="body">
      <Transition
        enter-active-class="transition-opacity duration-200"
        leave-active-class="transition-opacity duration-150"
        enter-from-class="opacity-0"
        leave-to-class="opacity-0"
      >
        <div
          v-if="detail"
          class="fixed inset-0 z-50 bg-black/40 backdrop-blur-sm"
          @click.self="detail = null"
        >
          <Transition
            enter-active-class="transition-transform duration-300 ease-out"
            leave-active-class="transition-transform duration-200 ease-in"
            enter-from-class="translate-x-full"
            leave-to-class="translate-x-full"
            appear
          >
            <aside class="fixed top-2 right-2 bottom-2 w-full sm:w-[640px] max-w-[95vw] bg-gray-50 rounded-3xl shadow-island-lg overflow-hidden flex flex-col">
              <header class="flex items-center justify-between gap-2 px-5 h-14 border-b border-gray-100 flex-shrink-0">
                <div class="min-w-0">
                  <h3 class="text-sm font-black text-gray-900 truncate">Report detail</h3>
                  <p class="text-[0.65rem] font-mono text-gray-400 truncate">{{ detail.job_id }}</p>
                </div>
                <div class="flex items-center gap-1 flex-shrink-0">
                  <button
                    type="button"
                    class="press h-9 px-3 inline-flex items-center gap-1.5 rounded-full text-xs font-bold text-gray-700 hover:bg-blue-50 hover:text-primary"
                    title="Copy JSON vào clipboard"
                    @click="onCopyJson"
                  >
                    <i class="bi bi-clipboard" />
                    Copy JSON
                  </button>
                  <button
                    type="button"
                    class="press h-9 px-3 inline-flex items-center gap-1.5 rounded-full text-xs font-bold bg-primary text-white hover:bg-primary-dark"
                    title="Download JSON file"
                    @click="onDownloadJson"
                  >
                    <i class="bi bi-download" />
                    Tải JSON
                  </button>
                  <button
                    type="button"
                    class="press h-9 w-9 flex items-center justify-center rounded-full text-gray-400 hover:text-gray-700 hover:bg-gray-100"
                    @click="detail = null"
                  >
                    <i class="bi bi-x-lg" />
                  </button>
                </div>
              </header>

              <div class="flex-1 min-h-0 overflow-y-auto p-5 space-y-4">
                <!-- Meta block -->
                <section>
                  <h4 class="text-[0.7rem] font-black uppercase tracking-wider text-gray-500 mb-2">Job</h4>
                  <dl class="grid grid-cols-[140px_1fr] gap-y-1.5 gap-x-3 text-xs">
                    <dt class="text-gray-500">User</dt><dd class="font-semibold text-gray-800 break-words">{{ detail.user_email }}</dd>
                    <dt class="text-gray-500">Session</dt><dd class="font-mono text-gray-700">{{ detail.session_id ? detail.session_id.slice(0, 12) + '…' : '—' }}</dd>
                    <dt class="text-gray-500">Mode</dt><dd class="text-gray-800">{{ detail.mode }}<span v-if="detail.ocr_mode" class="ml-1 text-[0.65rem] px-1.5 py-0.5 rounded bg-amber-50 text-amber-700">ocr</span></dd>
                    <dt class="text-gray-500">Status</dt>
                    <dd>
                      <span :class="['inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[0.65rem] font-bold uppercase', statusBadge(detail.status)]">
                        {{ detail.status }}
                      </span>
                      <span v-if="detail.error_msg" class="block mt-1 text-rose-600 text-[0.7rem]">{{ detail.error_msg }}</span>
                    </dd>
                    <dt class="text-gray-500">File</dt><dd class="text-gray-800">{{ detail.attachment_name || '—' }}<span v-if="detail.file_size_bytes" class="text-gray-500 font-mono"> · {{ formatBytes(detail.file_size_bytes) }}</span></dd>
                    <dt class="text-gray-500">Pages</dt><dd class="font-mono text-gray-700">{{ detail.page_count ?? '—' }}</dd>
                  </dl>
                </section>

                <!-- LLM block -->
                <section>
                  <h4 class="text-[0.7rem] font-black uppercase tracking-wider text-gray-500 mb-2">LLM stats</h4>
                  <dl class="grid grid-cols-[140px_1fr] gap-y-1.5 gap-x-3 text-xs">
                    <dt class="text-gray-500">Model</dt><dd class="font-mono text-gray-800">{{ detail.model || '—' }}</dd>
                    <dt class="text-gray-500">Prompt tokens</dt><dd class="font-mono text-gray-800">{{ formatNumber(detail.prompt_tokens) }}</dd>
                    <dt class="text-gray-500">Completion tokens</dt><dd class="font-mono text-gray-800">{{ formatNumber(detail.completion_tokens) }}</dd>
                    <dt class="text-gray-500">Total tokens</dt><dd class="font-mono text-gray-800">{{ formatNumber(detail.total_tokens) }}</dd>
                    <dt class="text-gray-500">Tokens/sec</dt><dd class="font-mono text-gray-800">{{ detail.tokens_per_sec ? detail.tokens_per_sec.toFixed(2) : '—' }}</dd>
                  </dl>
                </section>

                <!-- Timeline -->
                <section>
                  <h4 class="text-[0.7rem] font-black uppercase tracking-wider text-gray-500 mb-2">Timeline (ms)</h4>
                  <dl class="grid grid-cols-[140px_1fr] gap-y-1.5 gap-x-3 text-xs">
                    <dt class="text-gray-500">Total</dt><dd class="font-mono text-gray-800">{{ formatMs(detail.total_duration_ms) }}</dd>
                    <dt class="text-gray-500">OCR</dt><dd class="font-mono text-gray-800">{{ formatMs(detail.ocr_duration_ms) }}</dd>
                    <dt class="text-gray-500">Extraction</dt><dd class="font-mono text-gray-800">{{ formatMs(detail.extraction_duration_ms) }}</dd>
                    <dt class="text-gray-500">RAG retrieve</dt><dd class="font-mono text-gray-800">{{ formatMs(detail.rag_retrieve_ms) }}<span v-if="detail.rag_chunks_used" class="text-gray-500"> · {{ detail.rag_chunks_used }} chunks</span></dd>
                    <dt class="text-gray-500">LLM eval</dt><dd class="font-mono text-gray-800">{{ formatMs(detail.llm_eval_ms) }}</dd>
                    <dt class="text-gray-500">Started</dt><dd class="font-mono text-gray-700">{{ formatDateTime(detail.created_at) }}</dd>
                    <dt class="text-gray-500">Finished</dt><dd class="font-mono text-gray-700">{{ formatDateTime(detail.finished_at) }}</dd>
                  </dl>
                </section>

                <!-- Prompt -->
                <section v-if="detail.prompt">
                  <h4 class="text-[0.7rem] font-black uppercase tracking-wider text-gray-500 mb-2">Prompt</h4>
                  <pre class="bg-gray-50 border border-gray-200 rounded-xl p-3 text-xs whitespace-pre-wrap break-words max-h-40 overflow-y-auto font-mono">{{ detail.prompt }}</pre>
                </section>

                <!-- Output -->
                <section v-if="detail.output_content">
                  <h4 class="text-[0.7rem] font-black uppercase tracking-wider text-gray-500 mb-2">
                    Output <span class="text-gray-400 font-normal">({{ formatNumber(detail.output_chars) }} chars · {{ formatNumber(detail.output_lines) }} lines)</span>
                  </h4>
                  <pre class="bg-gray-50 border border-gray-200 rounded-xl p-3 text-xs whitespace-pre-wrap break-words max-h-96 overflow-y-auto font-mono">{{ detail.output_content }}</pre>
                </section>
              </div>
            </aside>
          </Transition>
        </div>
      </Transition>
    </Teleport>
    <!-- #endregion -->
  </div>
</template>

<script setup>
// #region ALD 21/05/2026 - Reports Manager: admin xem báo cáo job + export JSON cho Claude review.
const reports = useJobReports()
const toast = useToast()

// State
const items = ref([])
const total = ref(0)
const page = ref(1)
const limit = 20
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / limit)))
const loading = ref(false)
const stats = ref({})
const detail = ref(null)

const searchDraft = ref('')
const filters = reactive({ mode: '', status: '' })
const modeOptions = [
  { value: '', label: 'Tất cả mode' },
  { value: 'chat', label: 'Chat' },
  { value: 'image', label: 'Image' },
  { value: 'pdf', label: 'PDF' }
]
const statusOptions = [
  { value: '', label: 'Tất cả status' },
  { value: 'done', label: 'Done' },
  { value: 'error', label: 'Error' },
  { value: 'cancelled', label: 'Cancelled' }
]

// Helpers
function formatBytes(b) {
  if (!b) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let i = 0, v = b
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`
}
function formatNumber(n) {
  if (n === null || n === undefined) return '—'
  return Number(n).toLocaleString('vi-VN')
}
function formatMs(ms) {
  if (!ms && ms !== 0) return '—'
  if (ms < 1000) return `${ms}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  const m = Math.floor(ms / 60_000)
  const s = Math.round((ms % 60_000) / 1000)
  return `${m}m ${s}s`
}
function formatDate(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  const today = new Date()
  if (d.toDateString() === today.toDateString()) {
    return d.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' })
  }
  return d.toLocaleDateString('vi-VN', { day: '2-digit', month: '2-digit', year: '2-digit' })
}
function formatDateTime(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('vi-VN', { hour12: false })
}
function modeBadge(mode) {
  return {
    chat:  'bg-indigo-50 text-indigo-700',
    image: 'bg-blue-50 text-blue-700',
    pdf:   'bg-rose-50 text-rose-700'
  }[mode] || 'bg-gray-50 text-gray-700'
}
function modeIcon(mode) {
  return { chat: 'bi-chat-dots', image: 'bi-image', pdf: 'bi-file-earmark-pdf' }[mode] || 'bi-question'
}
function statusBadge(status) {
  return {
    done:      'bg-emerald-50 text-emerald-700',
    error:     'bg-rose-50 text-rose-700',
    cancelled: 'bg-gray-100 text-gray-600'
  }[status] || 'bg-gray-50 text-gray-700'
}

// API
async function loadStats() {
  try {
    const data = await reports.getStats()
    stats.value = data
  } catch (err) {
    console.warn('[reports] stats failed:', err)
  }
}

async function reload(newPage = page.value) {
  loading.value = true
  page.value = newPage
  try {
    const data = await reports.listReports({
      page: newPage, limit,
      q: searchDraft.value.trim(),
      mode: filters.mode,
      status: filters.status
    })
    items.value = data.items || []
    total.value = data.total || 0
  } catch (err) {
    toast.error('Không tải được reports: ' + (err.data?.error || err.message))
  } finally {
    loading.value = false
  }
}

let searchTimer = null
function onSearchInput() {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => reload(1), 300)
}

async function openDetail(jobId) {
  try {
    const data = await reports.getReport(jobId)
    detail.value = data.item
  } catch (err) {
    toast.error('Không tải được detail: ' + (err.data?.error || err.message))
  }
}

async function onCopyJson() {
  if (!detail.value) return
  try {
    const chars = await reports.copyJson(detail.value.job_id)
    toast.success(`Đã copy JSON (${chars} ký tự) vào clipboard`)
  } catch (err) {
    toast.error('Copy thất bại: ' + (err.message || err))
  }
}

async function onDownloadJson() {
  if (!detail.value) return
  try {
    await reports.downloadJson(detail.value.job_id)
    toast.success('Đã tải report.json')
  } catch (err) {
    toast.error('Download thất bại: ' + (err.message || err))
  }
}

// Lifecycle
onMounted(() => {
  reload(1)
  loadStats()
})
// #endregion
</script>
