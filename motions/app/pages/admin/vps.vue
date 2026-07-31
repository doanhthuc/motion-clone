<template>
  <!-- #region ALD 06/07/2026 - VPS Monitor: Liquid Glass light redesign (bỏ dark + amber, dùng lq-* global). -->
  <div class="flex-1 min-h-0 flex flex-col px-3 sm:px-6 pt-1 pb-3">
  <div class="lq-panel relative flex-1 min-h-0 flex flex-col gap-2 px-3 sm:px-5 py-3">
    <!-- Thanh loading indeterminate ở đỉnh khi đang xử lý / tải lần đầu -->
    <div v-if="anyBusy || initialLoading" class="pointer-events-none absolute inset-x-0 top-0 z-20 h-[3px] overflow-hidden rounded-t-[18px] bg-blue-100">
      <div class="vps-indeterminate h-full w-2/5 rounded-full bg-primary" />
    </div>

    <!-- Header -->
    <header class="shrink-0 flex flex-wrap items-center justify-between gap-2">
      <div class="flex flex-wrap items-center gap-2">
        <h1 class="text-lg font-semibold tracking-tight text-gray-900">VPS Monitor</h1>
        <span :class="['lq-chip', connected ? 'lq-chip--emerald' : initialLoading ? 'lq-chip--blue' : 'lq-chip--amber']">
          <span :class="['h-1.5 w-1.5 rounded-full', connected ? 'bg-emerald-500 animate-pulse' : initialLoading ? 'bg-blue-500 animate-ping' : 'bg-amber-500']" />
          {{ connected ? 'Trực tuyến' : initialLoading ? 'Đang kết nối…' : 'Mất kết nối' }}
        </span>
        <span class="hidden sm:inline text-[11px] font-medium text-gray-400">
          {{ status?.host?.hostname || '—' }} · up {{ fmtDuration(status?.host?.uptime_sec) }} · {{ fmtTime(status?.ts) }}
        </span>
      </div>
      <div class="flex items-center gap-2">
        <ActionBtn icon="bi-arrow-repeat" :label="restarting ? 'Đang khởi động lại…' : 'Khởi động lại ComfyUI'" variant="primary" :busy="restarting" :disabled="anyBusy" @click="restartComfy" />
        <ActionBtn icon="bi-arrow-clockwise" label="Làm mới" :disabled="anyBusy" @click="monitor.refresh()" />
      </div>
    </header>

    <p v-if="anyBusy || actionMessage" class="shrink-0 flex items-center gap-2 truncate rounded-lg bg-white/[0.03] px-3 py-1.5 text-xs font-medium text-gray-600">
      <i v-if="anyBusy" class="bi bi-arrow-repeat animate-spin text-primary" />
      <span class="truncate">{{ busyLabel || actionMessage }}</span>
    </p>
    <div v-if="monitorError" class="shrink-0 rounded-lg border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs font-medium text-amber-800">
      {{ monitorError }}
    </div>

    <!-- Metric strip -->
    <div class="shrink-0 grid grid-cols-4 lg:grid-cols-8 gap-2">
      <StatChip label="Health" :value="`${health.score}`" :sub="health.status" :level="healthLevel(health.status)" icon="bi-heart-pulse" />
      <StatChip label="CPU" :value="pct(status?.cpu?.used_pct)" :sub="`${status?.cpu?.cores || 0} cores`" :level="level(status?.cpu?.used_pct)" icon="bi-cpu" />
      <StatChip label="RAM" :value="pct(status?.memory?.used_pct)" :sub="bytesPair(status?.memory?.used_bytes, status?.memory?.total_bytes)" :level="level(status?.memory?.used_pct)" icon="bi-memory" />
      <StatChip label="Swap" :value="pct(status?.memory?.swap_used_pct)" :sub="bytesPair(status?.memory?.swap_used_bytes, status?.memory?.swap_total_bytes)" :level="level(status?.memory?.swap_used_pct)" icon="bi-hdd-stack" />
      <StatChip label="Disk" :value="pct(status?.disk?.used_pct)" :sub="bytesPair(status?.disk?.used_bytes, status?.disk?.total_bytes)" :level="level(status?.disk?.used_pct)" icon="bi-device-hdd" />
      <StatChip label="VRAM" :value="gpuValue" :sub="bytesPair(status?.gpu?.used_bytes, status?.gpu?.total_bytes)" :level="level(status?.gpu?.used_pct)" icon="bi-gpu-card" />
      <StatChip label="GPU" :value="fmtTemp(status?.gpu?.temperature_c)" :sub="`${shortGpu(status?.gpu?.name)}`" :level="healthLevel(gpuHealth?.status)" icon="bi-thermometer-half" />
      <StatChip label="Workers" :value="`${freshWorkers}/${totalWorkers}`" :sub="freshWorkers ? 'fresh' : 'không có heartbeat'" :level="freshWorkers ? 'ok' : 'danger'" icon="bi-hdd-network" />
    </div>

    <!-- Chẩn đoán / loading -->
    <div v-if="initialLoading" class="shrink-0 flex items-center gap-2 rounded-xl border border-blue-200 bg-blue-50 px-3 py-2 text-xs font-semibold text-blue-700">
      <i class="bi bi-arrow-repeat animate-spin" /> Đang tải dữ liệu giám sát…
    </div>
    <section v-else-if="issues.length" class="shrink-0 space-y-1.5">
      <div v-for="(it, i) in issues" :key="i" :class="['flex items-start gap-2 rounded-xl border px-3 py-2', issueCardCls(it.level)]">
        <i :class="['bi mt-0.5 text-base', issueIconCls(it.level)]" />
        <div class="min-w-0 flex-1">
          <p class="text-[13px] font-semibold text-gray-900">{{ it.title }}</p>
          <p class="text-[11px] font-medium leading-snug text-gray-600">{{ it.detail }}</p>
        </div>
        <ActionBtn
          v-if="it.action"
          :icon="it.action === 'restart-comfy' ? 'bi-arrow-repeat' : 'bi-stars'"
          :label="issueActionLabel(it.action)"
          :variant="it.level === 'critical' ? 'danger' : 'primary'"
          :busy="it.action === 'restart-comfy' ? restarting : freeing"
          :disabled="anyBusy"
          @click="runIssueAction(it.action)"
        />
      </div>
    </section>
    <!-- <div v-else-if="health.status === 'healthy'" class="shrink-0 flex items-center gap-2 rounded-xl border border-emerald-400/25 bg-emerald-500/15 px-3 py-1.5 text-[11px] font-bold text-emerald-400">
      <i class="bi bi-check-circle-fill" /> Hệ thống ổn định — không phát hiện vấn đề.
    </div> -->

    <!-- Main grid fills remaining height; tables scroll internally -->
    <div class="grid grid-cols-1 xl:grid-cols-12 gap-2 flex-1 min-h-0 overflow-y-auto xl:overflow-visible">
      <!-- Actions -->
      <section class="apl-panel xl:col-span-3 flex flex-col min-h-0">
        <div class="apl-panel-head">
          <h2>Điều khiển</h2>
          <span class="text-[11px] font-medium text-gray-400">{{ fmtTime(status?.ts) }}</span>
        </div>
        <div class="min-h-0 overflow-y-auto p-3 space-y-5">
          <!-- Nhóm 1: Hàng đợi job -->
          <section class="space-y-2.5">
            <h3 class="apl-sub">Hàng đợi job</h3>
            <div class="grid grid-cols-4 gap-1.5">
              <MetricMini label="Queued" :value="status?.jobs?.queued ?? 0" tone="blue" />
              <MetricMini label="Run" :value="status?.jobs?.running ?? 0" tone="amber" />
              <MetricMini label="Error" :value="status?.jobs?.error ?? 0" tone="rose" />
              <MetricMini label="Done" :value="status?.jobs?.done ?? 0" tone="emerald" />
            </div>
            <div class="grid grid-cols-2 gap-1.5">
              <ActionBtn icon="bi-trash3" label="Clear queued" :busy="isBusy('clear:queued')" :disabled="anyBusy" @click="clearJobs('queued')" />
              <ActionBtn icon="bi-stop-circle" label="Stop running" :busy="isBusy('clear:running')" :disabled="anyBusy" @click="clearJobs('running')" />
              <ActionBtn icon="bi-x-octagon" label="Clear error" :busy="isBusy('clear:error')" :disabled="anyBusy" @click="clearJobs('error')" />
              <ActionBtn icon="bi-exclamation-triangle" label="Clear all" variant="danger" :busy="isBusy('clear:all')" :disabled="anyBusy" @click="clearJobs('all')" />
            </div>
          </section>

          <!-- Nhóm 2: Giải phóng tài nguyên -->
          <section class="space-y-2.5">
            <h3 class="apl-sub">Giải phóng tài nguyên</h3>
            <div class="grid grid-cols-2 gap-1.5">
              <ActionBtn icon="bi-gpu-card" label="Free GPU" variant="emerald" :busy="isBusy('free:gpu')" :disabled="anyBusy" @click="freeResources('gpu')" />
              <ActionBtn icon="bi-memory" label="Drop cache" variant="sky" :busy="isBusy('free:ram')" :disabled="anyBusy" @click="freeResources('ram')" />
              <ActionBtn icon="bi-hdd-stack" label="Reset swap" variant="amber" :busy="isBusy('free:swap')" :disabled="anyBusy" @click="freeResources('swap')" />
              <ActionBtn icon="bi-lightning-charge" label="Free all" variant="primary" :busy="isBusy('free:all')" :disabled="anyBusy" @click="freeResources('all')" />
            </div>
          </section>

          <p class="flex items-start gap-1.5 text-[11px] leading-snug text-gray-400">
            <i class="bi bi-info-circle mt-0.5" />
            <span><b>Drop cache</b> chỉ xả page-cache, không hạ RAM của tiến trình đang chạy. RAM cao do ComfyUI rò → bấm <b>Khởi động lại ComfyUI</b> (góc trên) để giải phóng VRAM + RAM rồi dựng lại.</span>
          </p>
        </div>
      </section>

      <!-- Jobs -->
      <section class="apl-panel xl:col-span-5 flex flex-col min-h-0">
        <div class="apl-panel-head">
          <h2>Job chờ/chạy/lỗi</h2>
          <div class="flex items-center gap-2">
            <span v-if="selectedJobIds.length" class="text-[11px] font-medium text-gray-500">{{ selectedJobIds.length }} chọn</span>
            <ActionBtn icon="bi-check2-square" label="Cancel chọn" variant="amber" :busy="isBusy('clear:selected')" :disabled="anyBusy || !selectedJobIds.length" @click="cancelSelectedJobs" />
            <ActionBtn icon="bi-x-octagon" label="Cancel hết" variant="rose" :busy="isBusy('clear:all_with_error')" :disabled="anyBusy || !recentJobs.length" @click="clearJobs('all_with_error')" />
          </div>
        </div>
        <div class="min-h-0 overflow-auto">
          <table class="min-w-full text-sm">
            <thead class="sticky top-0 z-10 bg-gray-50/95 backdrop-blur lq-th shadow-sm">
              <tr>
                <th class="text-left py-2 pl-3 w-8">
                  <input type="checkbox" class="lq-check" :checked="allRecentSelected" :disabled="!recentJobs.length" @change="toggleAllRecentJobs($event.target.checked)" />
                </th>
                <th class="text-left py-2">Job</th>
                <th class="text-left py-2">Type</th>
                <th class="text-left py-2">Status</th>
                <th class="text-left py-2">%</th>
                <th class="text-left py-2 pr-3">Step / Worker</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-white/[0.05]">
              <tr v-for="j in recentJobs" :key="j.id">
                <td class="py-2 pl-3"><input type="checkbox" class="lq-check" :checked="selectedJobIds.includes(j.id)" @change="toggleJobSelection(j.id, $event.target.checked)" /></td>
                <td class="py-2 font-mono text-xs text-gray-500">{{ j.id.slice(0, 8) }}</td>
                <td class="py-2 font-medium text-gray-900">{{ j.type }}</td>
                <td class="py-2"><StatusPill :status="j.status" /></td>
                <td class="py-2 text-gray-600">{{ Math.round(Number(j.progress || 0) * 100) }}%</td>
                <td class="py-2 pr-3 max-w-xs truncate text-xs text-gray-500">{{ j.current_step || '—' }} · {{ j.worker_id || '—' }}</td>
              </tr>
              <tr v-if="!recentJobs.length"><td colspan="6" class="py-10 text-center text-sm font-medium text-gray-400">Không có job chờ/chạy/lỗi</td></tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- GPU & processes -->
      <section class="apl-panel xl:col-span-4 flex flex-col min-h-0">
        <div class="apl-panel-head">
          <h2>GPU & tiến trình</h2>
          <span class="text-[11px] font-medium text-gray-400">{{ ramProcessSub }}</span>
        </div>
        <div class="min-h-0 overflow-auto p-3 space-y-4">
          <div>
            <h3 class="apl-sub">VRAM theo process</h3>
            <table class="mt-1 min-w-full text-xs">
              <tbody class="divide-y divide-white/[0.05]">
                <tr v-for="p in gpuProcesses" :key="`${p.gpu_uuid}-${p.pid}`">
                  <td class="py-1.5 font-mono text-gray-500 w-14">{{ p.pid || '—' }}</td>
                  <td class="py-1.5 max-w-[8rem] truncate font-medium text-gray-800">{{ gpuProcessName(p) }}</td>
                  <td class="py-1.5 text-right font-semibold text-gray-900">{{ fmtBytes(p.used_bytes) }}</td>
                </tr>
                <tr v-if="!gpuProcesses.length"><td colspan="3" class="py-3 text-center font-medium text-gray-400">Chưa có process VRAM</td></tr>
              </tbody>
            </table>
          </div>
          <div>
            <h3 class="apl-sub">Worker heartbeat</h3>
            <table class="mt-1 min-w-full text-xs">
              <tbody class="divide-y divide-white/[0.05]">
                <tr v-for="w in status?.workers || []" :key="w.worker_id">
                  <td class="py-1.5 font-semibold text-gray-900">
                    <span :class="['mr-1.5 inline-block h-1.5 w-1.5 rounded-full', w.fresh ? 'bg-emerald-500' : 'bg-gray-300']" />{{ w.worker_id }}
                  </td>
                  <td class="py-1.5 text-gray-500">{{ w.mode || '—' }}</td>
                  <td class="py-1.5 font-mono text-gray-500">{{ w.active_job_id ? w.active_job_id.slice(0, 8) : '—' }}</td>
                  <td class="py-1.5 text-right text-gray-400">{{ fmtTime(w.last_seen_at) }}</td>
                </tr>
                <tr v-if="!(status?.workers || []).length"><td colspan="4" class="py-3 text-center font-medium text-gray-400">Chưa có worker</td></tr>
              </tbody>
            </table>
          </div>
          <div>
            <h3 class="apl-sub">RAM top process</h3>
            <table class="mt-1 min-w-full text-xs">
              <tbody class="divide-y divide-white/[0.05]">
                <tr v-for="p in ramProcesses" :key="p.pid">
                  <td class="py-1.5 max-w-[10rem] truncate font-medium text-gray-800">{{ p.command || '—' }}</td>
                  <td class="py-1.5 font-mono text-gray-500 w-14">{{ p.pid }}</td>
                  <td class="py-1.5 text-right font-semibold text-gray-900">{{ fmtBytes(p.rss_bytes) }}</td>
                </tr>
                <tr v-if="!ramProcesses.length"><td colspan="3" class="py-3 text-center font-medium text-gray-400">Chưa đọc được process RAM</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>
    </div>
    <ClientOnly>
      <AdminComfyLogsDrawer />
    </ClientOnly>
  </div>
  </div>
  <!-- #endregion -->
</template>

<script setup>
import { h } from 'vue'

definePageMeta({ middleware: ['auth', 'admin'] })

const monitor = useAdminVpsMonitor()
const toast = useToast()
const clearing = ref(false)
const freeing = ref(false)
const restarting = ref(false)
const busyMode = ref('') // khóa nút đang chạy để hiện spinner đúng chỗ, vd 'free:ram' | 'clear:all' | 'restart'
const restartMessage = ref('')
const resourceMessage = ref('')
const selectedJobIds = ref([])
const status = computed(() => monitor.status.value)
const recentJobs = computed(() => status.value?.recent_jobs || [])
const allRecentSelected = computed(() => recentJobs.value.length > 0 && recentJobs.value.every((j) => selectedJobIds.value.includes(j.id)))
const connected = computed(() => Boolean(monitor.connected.value))
const monitorError = computed(() => monitor.error.value || '')
const health = computed(() => status.value?.health || { score: 0, status: 'critical' })
const actionMessage = computed(() => restartMessage.value || resourceMessage.value || '')

const gpuValue = computed(() => status.value?.gpu?.used_pct == null ? '—' : pct(status.value.gpu.used_pct))
const gpuHealth = computed(() => status.value?.gpu_health || { score: 0, status: 'critical', reasons: ['Chưa có dữ liệu GPU'] })
const gpuProcesses = computed(() => status.value?.gpu_processes || [])
const ramProcesses = computed(() => status.value?.ram_processes || [])
const freshWorkers = computed(() => (status.value?.workers || []).filter((w) => w.fresh).length)
const totalWorkers = computed(() => (status.value?.workers || []).length)
const ramProcessSub = computed(() => {
  const source = status.value?.inspect_helper?.enabled ? 'host helper' : (status.value?.ram_process_source || 'local')
  return `${ramProcesses.value.length} proc · ${source}`
})

// #region ALD 15/06/2026 - Issues banner + trạng thái loading rõ ràng
const issues = computed(() => status.value?.issues || [])
const initialLoading = computed(() => !status.value && !monitorError.value)
const anyBusy = computed(() => clearing.value || freeing.value || restarting.value)
const busyLabel = computed(() =>
  restarting.value ? 'Đang khởi động lại ComfyUI…' : freeing.value ? 'Đang giải phóng tài nguyên…' : clearing.value ? 'Đang xử lý hàng đợi…' : '')
function isBusy(key) {
  return busyMode.value === key
}
function issueCardCls(lvl) {
  return lvl === 'critical' ? 'border-rose-200 bg-rose-50' : lvl === 'warning' ? 'border-amber-200 bg-amber-50' : 'border-white/[0.06] bg-gray-50'
}
function issueIconCls(lvl) {
  return lvl === 'critical' ? 'bi-exclamation-octagon-fill text-rose-500' : lvl === 'warning' ? 'bi-exclamation-triangle-fill text-amber-500' : 'bi-info-circle-fill text-gray-400'
}
function issueActionLabel(action) {
  return action === 'restart-comfy' ? 'Khởi động lại ComfyUI' : action === 'free-swap' ? 'Reset swap' : action === 'free-ram' ? 'Drop cache' : ''
}
function runIssueAction(action) {
  if (action === 'restart-comfy') return restartComfy()
  if (action === 'free-swap') return freeResources('swap')
  if (action === 'free-ram') return freeResources('ram')
}
// #endregion

onMounted(() => {
  monitor.start()
  monitor.refresh().catch(() => {})
})
onBeforeUnmount(() => monitor.stop())
watch(recentJobs, (jobs) => {
  const visible = new Set((jobs || []).map((j) => j.id))
  selectedJobIds.value = selectedJobIds.value.filter((id) => visible.has(id))
})

async function clearJobs(mode) {
  if (anyBusy.value) return
  clearing.value = true
  busyMode.value = `clear:${mode}`
  try {
    const res = await monitor.clearJobs(mode)
    toast.success(`Đã clear ${res.jobs_cancelled} jobs + ${res.workflow_runs_cancelled} workflow runs`)
    if (mode === 'all_with_error') selectedJobIds.value = []
  } catch (e) {
    toast.error(e?.data?.error || e?.message || 'Clear jobs lỗi')
  } finally {
    clearing.value = false
    busyMode.value = ''
  }
}

async function freeResources(mode) {
  if (anyBusy.value) return
  freeing.value = true
  busyMode.value = `free:${mode}`
  restartMessage.value = ''
  resourceMessage.value = 'Đang giải phóng tài nguyên...'
  try {
    const res = await monitor.freeResources(mode)
    const steps = res.steps || []
    const failed = steps.filter((s) => !s.ok && !s.skipped)
    const skipped = steps.filter((s) => s.skipped)
    const ok = steps.filter((s) => s.ok)
    const swapBefore = res.memory_before?.swap_used_bytes
    const swapAfter = res.memory_after?.swap_used_bytes
    const swapText = swapBefore != null && swapAfter != null ? ` · Swap ${fmtBytes(swapBefore)} → ${fmtBytes(swapAfter)}` : ''
    resourceMessage.value = `${ok.length} bước OK${failed.length ? `, ${failed.length} lỗi` : ''}${skipped.length ? `, ${skipped.length} bỏ qua` : ''}${swapText}`
    if (failed.length) toast.warning(resourceMessage.value, { duration: 6000 })
    else toast.success('Đã gửi lệnh giải phóng tài nguyên')
  } catch (e) {
    resourceMessage.value = ''
    toast.error(e?.data?.error || e?.message || 'Free resource lỗi')
  } finally {
    freeing.value = false
    busyMode.value = ''
  }
}

async function restartComfy() {
  if (anyBusy.value) return
  restarting.value = true
  busyMode.value = 'restart'
  resourceMessage.value = ''
  restartMessage.value = 'Đang khởi động lại ComfyUI (free VRAM → reboot)...'
  try {
    const res = await monitor.restartComfy()
    const method = res?.method === 'kill_supervisor' ? 'kill + supervisor' : res?.method === 'manager_reboot' ? 'Manager reboot' : (res?.method || 'không rõ')
    if (res?.success) {
      restartMessage.value = `ComfyUI đã khởi động lại (${method})${res?.comfy_up === false ? ' · đang chờ dựng lại, Làm mới sau ~30s' : ''}`
      toast.success(restartMessage.value)
    } else {
      const why = (res?.steps || []).filter((s) => !s.ok && !s.skipped).map((s) => s.error || s.name).join('; ')
      restartMessage.value = `Restart chưa chắc chắn: ${why || 'cần resource-helper (pid:host) hoặc ComfyUI-Manager'}`
      toast.warning(restartMessage.value, { duration: 8000 })
    }
  } catch (e) {
    restartMessage.value = ''
    toast.error(e?.data?.error || e?.message || 'Restart ComfyUI lỗi')
  } finally {
    restarting.value = false
    busyMode.value = ''
  }
}

function toggleJobSelection(id, checked) {
  const ids = new Set(selectedJobIds.value)
  if (checked) ids.add(id)
  else ids.delete(id)
  selectedJobIds.value = Array.from(ids)
}
function toggleAllRecentJobs(checked) {
  selectedJobIds.value = checked ? recentJobs.value.map((j) => j.id) : []
}
async function cancelSelectedJobs() {
  if (!selectedJobIds.value.length || anyBusy.value) return
  clearing.value = true
  busyMode.value = 'clear:selected'
  try {
    const ids = [...selectedJobIds.value]
    const res = await monitor.clearJobs('selected', ids)
    selectedJobIds.value = []
    toast.success(`Đã cancel ${res.jobs_cancelled} selected jobs`)
  } catch (e) {
    toast.error(e?.data?.error || e?.message || 'Cancel selected lỗi')
  } finally {
    clearing.value = false
    busyMode.value = ''
  }
}

function pct(v) {
  return v == null ? '—' : `${Math.round(Number(v) * 100)}%`
}
function level(v) {
  if (v == null) return 'neutral'
  return v >= 0.9 ? 'danger' : v >= 0.75 ? 'warn' : 'ok'
}
function healthLevel(s) {
  return s === 'healthy' ? 'ok' : s === 'warning' ? 'warn' : s === 'critical' ? 'danger' : 'neutral'
}
function fmtBytes(n) {
  if (n == null || Number.isNaN(Number(n))) return '—'
  const gb = Number(n) / 1024 / 1024 / 1024
  return `${gb.toFixed(gb >= 10 ? 0 : 1)}GB`
}
function fmtTemp(n) {
  return n == null || Number.isNaN(Number(n)) ? '—' : `${Math.round(Number(n))}°C`
}
function bytesPair(used, total) {
  if (!total) return '—'
  return `${fmtBytes(used)} / ${fmtBytes(total)}`
}
function fmtDuration(sec) {
  if (!sec) return '—'
  const hh = Math.floor(sec / 3600)
  const mm = Math.floor((sec % 3600) / 60)
  return `${hh}h ${mm}m`
}
function fmtTime(v) {
  if (!v) return '—'
  return new Date(v).toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}
function shortGpu(name) {
  return (name || '—').replace('NVIDIA GeForce ', '').replace('NVIDIA ', '')
}
function gpuProcessName(p) {
  return (p?.process_name || '—').split('/').pop()
}

// Nút hành động gọn, đồng bộ — tự đổi sang spinner khi busy, disable khi đang có tác vụ khác chạy.
const ActionBtn = defineComponent({
  props: {
    icon: String,
    label: String,
    variant: { type: String, default: '' },
    busy: Boolean,
    disabled: Boolean,
  },
  emits: ['click'],
  setup(props, { emit }) {
    return () => h('button', {
      type: 'button',
      class: ['vps-btn', props.variant && `vps-btn--${props.variant}`],
      disabled: props.disabled || props.busy,
      onClick: () => { if (!props.disabled && !props.busy) emit('click') },
    }, [
      h('i', { class: ['bi', props.busy ? 'bi-arrow-repeat animate-spin' : props.icon] }),
      props.label ? h('span', { class: 'truncate' }, props.label) : null,
    ])
  },
})

const StatChip = defineComponent({
  props: { label: String, value: String, sub: String, level: String, icon: String },
  setup(props) {
    const textCls = computed(() => props.level === 'danger' ? 'text-rose-600' : props.level === 'warn' ? 'text-amber-600' : props.level === 'ok' ? 'text-emerald-600' : 'text-gray-900')
    const iconCls = computed(() => props.level === 'danger' ? 'bg-rose-50 text-rose-500' : props.level === 'warn' ? 'bg-amber-50 text-amber-600' : props.level === 'ok' ? 'bg-emerald-50 text-emerald-600' : 'bg-gray-100 text-gray-400')
    return () => h('div', { class: 'min-w-0 rounded-xl border border-white/[0.07] bg-gray-50 px-3 py-2 shadow-sm' }, [
      h('div', { class: 'flex items-center justify-between gap-1' }, [
        h('span', { class: 'text-[10px] font-semibold uppercase tracking-wide text-gray-400' }, props.label),
        h('span', { class: `inline-flex h-5 w-5 items-center justify-center rounded-md text-[11px] ${iconCls.value}` }, [h('i', { class: `bi ${props.icon}` })]),
      ]),
      h('div', { class: `mt-1 text-xl font-semibold leading-none tabular-nums ${textCls.value}` }, props.value ?? '—'),
      h('div', { class: 'mt-1 truncate text-[10px] font-medium text-gray-400' }, props.sub || '—'),
    ])
  },
})

const MetricMini = defineComponent({
  props: { label: String, value: [String, Number], tone: String },
  setup(props) {
    const tone = computed(() => ({
      blue: 'bg-blue-50 text-blue-700',
      amber: 'bg-amber-50 text-amber-700',
      rose: 'bg-rose-50 text-rose-600',
      emerald: 'bg-emerald-50 text-emerald-700',
    }[props.tone] || 'bg-gray-50 text-gray-700'))
    return () => h('div', { class: `rounded-xl px-3 py-2 ${tone.value}` }, [
      h('div', { class: 'text-[10px] font-semibold uppercase tracking-wide opacity-70' }, props.label),
      h('div', { class: 'mt-0.5 text-xl font-semibold tabular-nums' }, String(props.value ?? 0)),
    ])
  },
})

const StatusPill = defineComponent({
  props: { status: String },
  setup(props) {
    const cls = computed(() => props.status === 'running' ? 'bg-amber-50 text-amber-700' : props.status === 'queued' ? 'bg-blue-50 text-blue-700' : props.status === 'error' ? 'bg-rose-50 text-rose-600' : 'bg-gray-100 text-gray-600')
    return () => h('span', { class: `inline-flex rounded-full px-2 py-0.5 text-[11px] font-semibold ${cls.value}` }, props.status || '—')
  },
})
</script>

<style scoped>
/* ALD 06/07/2026 - Liquid Glass light (dùng tokens global) */
.apl-panel {
  border-radius: 0.875rem;
  border: 1px solid var(--line);
  background: var(--surface);
  box-shadow: 0 1px 2px rgba(0,0,0,0.03);
  overflow: hidden;
}
.apl-panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  padding: 0.6rem 0.85rem;
  border-bottom: 1px solid var(--line);
  flex-shrink: 0;
}
.apl-panel-head h2 {
  font-size: 0.78rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--ink-2);
}
.apl-sub {
  font-size: 0.68rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--ink-3);
}
/* Nút hành động gọn & đồng bộ — biến thể tô màu theo ngữ cảnh */
.vps-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.4rem;
  height: 2rem;
  min-height: 2rem;
  border-radius: 0.6rem;
  border: 1px solid var(--line-2);
  background: var(--surface);
  color: var(--ink);
  font-size: 0.72rem;
  font-weight: 600;
  padding: 0 0.7rem;
  white-space: nowrap;
  transition: background 0.12s, border-color 0.12s, opacity 0.12s;
}
.vps-btn > i {
  font-size: 0.85rem;
  line-height: 1;
}
.vps-btn:hover:not(:disabled) {
  background: rgba(255,255,255,0.08);
  border-color: rgba(0,0,0,0.22);
}
.vps-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.vps-btn--primary { background: var(--primary); border-color: var(--primary); color: #fff; }
.vps-btn--primary:hover:not(:disabled) { background: #6B76E5; border-color: #6B76E5; }
.vps-btn--danger { background: var(--color-danger); border-color: var(--color-danger); color: white; }
.vps-btn--danger:hover:not(:disabled) { filter: brightness(1.1); }
.vps-btn--rose { background: var(--color-danger-light); border-color: transparent; color: #b3000f; }
.vps-btn--rose:hover:not(:disabled) { background: #fbdadd; }
.vps-btn--amber { background: var(--color-warning-light); border-color: transparent; color: #9a6700; }
.vps-btn--amber:hover:not(:disabled) { background: #fae9c8; }
.vps-btn--emerald { background: var(--color-success-light); border-color: transparent; color: #1b6b30; }
.vps-btn--emerald:hover:not(:disabled) { background: #d4f0dd; }
.vps-btn--sky { background: var(--color-info-light); border-color: transparent; color: #9AA2F2; }
.vps-btn--sky:hover:not(:disabled) { background: #d3e8fc; }
/* Thanh loading indeterminate ở đỉnh trang */
@keyframes vps-indeterminate {
  0% { transform: translateX(-120%); }
  60% { transform: translateX(260%); }
  100% { transform: translateX(260%); }
}
.vps-indeterminate {
  animation: vps-indeterminate 1.1s ease-in-out infinite;
}
code {
  font-family: ui-monospace, monospace;
  background: rgba(255,255,255,0.08);
  color: var(--ink);
  padding: 0 0.2rem;
  border-radius: 0.2rem;
}
</style>
