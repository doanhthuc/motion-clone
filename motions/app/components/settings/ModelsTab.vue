<!-- ALD 15/06/2026 - Tab "Models AI": 2 khu
     • CATALOG (cài on-demand): list model curated (ComfyUI + Ollama) theo nhóm tính năng — bấm Download để tải
       thẳng vào ComfyUI/Ollama trên server (aria2 song song / ollama pull), có thanh tiến trình. Thay việc tải ở setup.
     • UPLOAD custom: upload file model tự train (LoRA/checkpoint…) — giữ nguyên như cũ.
     Admin-only (tab model-files đã requireAdmin ở settings.vue). -->
<template>
  <div class="flex flex-col flex-1 min-h-0 gap-3">
    <!-- Header -->
    <div class="flex items-center gap-3 flex-wrap flex-shrink-0">
      <span class="inline-flex h-11 w-11 items-center justify-center rounded-2xl bg-gradient-to-br from-primary to-primary-dark text-white shadow-pill flex-shrink-0">
        <i class="bi bi-boxes text-lg" />
      </span>
      <div class="min-w-0">
        <h3 class="text-sm font-bold text-gray-900 uppercase tracking-wide">Models AI</h3>
        <div class="flex items-center gap-2 mt-1 text-[0.7rem] font-mono text-gray-500 flex-wrap">
          <template v-if="view === 'catalog'">
            <span><b class="text-emerald-600">{{ installedCount }}</b> đã cài</span>
            <span class="text-gray-300">·</span>
            <span><b class="text-gray-800">{{ totalCount }}</b> model</span>
            <span v-if="activeDownloads.length" class="text-gray-300">·</span>
            <span v-if="activeDownloads.length" class="text-primary"><b>{{ activeDownloads.length }}</b> đang tải</span>
          </template>
          <template v-else>
            <span><b class="text-gray-800">{{ items.length }}</b> file upload</span>
            <span class="text-gray-300">·</span>
            <span><b class="text-primary">{{ formatSize(totalBytes) }}</b></span>
          </template>
        </div>
      </div>

      <!-- Segmented switch -->
      <div class="ml-auto flex items-center gap-2 flex-wrap">
        <div class="inline-flex p-0.5 rounded-full bg-gray-100 text-xs font-semibold">
          <button type="button" :class="segCls('catalog')" @click="view = 'catalog'"><i class="bi bi-cloud-arrow-down me-1" />Cài model</button>
          <button type="button" :class="segCls('uploads')" @click="view = 'uploads'"><i class="bi bi-cloud-arrow-up me-1" />Upload custom</button>
        </div>
        <button type="button" class="press inline-flex items-center gap-2 h-10 px-4 rounded-full glass shadow-card text-sm font-semibold text-gray-700 hover:bg-gray-100 transition-colors" @click="refresh">
          <i :class="['bi bi-arrow-clockwise', (loading || catalogLoading) && 'animate-spin']" /> Làm mới
        </button>
        <button v-if="view === 'catalog'" type="button" class="press inline-flex items-center gap-1.5 h-10 px-4 rounded-full bg-primary text-white text-sm font-semibold shadow-pill hover:bg-primary-dark transition-colors" @click="openCustom">
          <i class="bi bi-plus-lg" /> Thêm model (URL)
        </button>
        <button v-else type="button" class="press inline-flex items-center gap-1.5 h-10 px-4 rounded-full bg-primary text-white text-sm font-semibold shadow-pill hover:bg-primary-dark transition-colors" @click="openCreate">
          <i class="bi bi-cloud-arrow-up-fill" /> Upload model
        </button>
      </div>
    </div>

    <!-- ════════ CATALOG ════════ -->
    <template v-if="view === 'catalog'">
      <!-- Filters -->
      <div class="flex items-center gap-2 flex-wrap flex-shrink-0">
        <div class="inline-flex p-0.5 rounded-full bg-gray-100 text-[0.72rem] font-semibold">
          <button v-for="k in KIND_FILTERS" :key="k.v" type="button" :class="chipCls(kindFilter === k.v)" @click="kindFilter = k.v">{{ k.label }}</button>
        </div>
        <div class="inline-flex p-0.5 rounded-full bg-gray-100 text-[0.72rem] font-semibold">
          <button v-for="t in TIER_FILTERS" :key="t.v" type="button" :class="chipCls(tierFilter === t.v)" @click="tierFilter = t.v">{{ t.label }}</button>
        </div>
        <div class="relative ml-auto">
          <i class="bi bi-search absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 text-xs" />
          <input v-model="q" type="text" placeholder="Tìm model…" class="h-9 w-48 pl-8 pr-3 rounded-full bg-gray-100 text-sm focus:bg-gray-50 focus:ring-2 focus:ring-primary/30 outline-none transition" >
        </div>
      </div>

      <div class="flex-1 min-h-0 overflow-auto -mx-1 px-1">
        <div v-if="catalogLoading && !groups.length" class="text-center py-16 text-xs text-gray-400 italic">Đang tải catalog…</div>
        <div v-else-if="!groups.length" class="text-center py-16 text-sm text-gray-400">
          <i class="bi bi-search text-4xl text-gray-300 mb-2 block" /> Không có model khớp bộ lọc.
        </div>

        <div v-for="grp in groups" :key="grp.group" class="mb-4">
          <div class="flex items-center gap-2 mb-1.5 px-1">
            <h4 class="text-[0.72rem] font-bold uppercase tracking-wide text-gray-500">{{ grp.group }}</h4>
            <span class="text-[0.66rem] font-mono text-gray-400">{{ grp.items.length }}</span>
            <!-- ALD 16/06/2026 - nút cài CẢ NHÓM: tải mọi model chưa cài trong group 1 phát -->
            <button
              v-if="grp.items.some((it) => !it.installed && !liveDl(it))"
              type="button"
              class="press ml-auto inline-flex items-center gap-1 h-7 px-2 rounded-full bg-primary/10 text-primary text-[0.68rem] font-semibold hover:bg-primary hover:text-white transition-colors"
              title="Tải tất cả model chưa cài trong nhóm này"
              @click="installGroup(grp)"
            >
              <i class="bi bi-cloud-arrow-down" /> Cài cả nhóm ({{ grp.items.filter((it) => !it.installed && !liveDl(it)).length }})
            </button>
          </div>
          <div class="grid grid-cols-1 lg:grid-cols-2 gap-2">
            <div v-for="it in grp.items" :key="it.id" class="flex items-center gap-3 bg-gray-50 rounded-2xl border border-gray-200/70 shadow-card px-3.5 py-2.5">
              <span :class="['inline-flex h-9 w-9 items-center justify-center rounded-xl flex-shrink-0', it.installed ? 'bg-emerald-50 text-emerald-600' : 'bg-gray-100 text-gray-400']">
                <i :class="['bi', it.kind === 'ollama' ? 'bi-cpu-fill' : (TYPE_ICON[it.type] || 'bi-file-earmark-binary')]" />
              </span>
              <div class="min-w-0 flex-1">
                <div class="flex items-center gap-1.5 min-w-0">
                  <span class="text-sm font-semibold text-gray-900 truncate">{{ it.label }}</span>
                  <UiBadge v-if="it.tier === 'core'" variant="primary" size="sm">core</UiBadge>
                  <UiBadge v-if="it.custom" variant="neutral" size="sm">custom</UiBadge>
                </div>
                <div class="flex items-center gap-2 text-[0.7rem] text-gray-500 font-mono mt-0.5">
                  <span>{{ formatSize(it.sizeBytes) }}</span>
                  <span v-if="it.vram" class="text-gray-300">·</span>
                  <span v-if="it.vram">VRAM {{ it.vram }}</span>
                  <span v-if="it.note" class="text-gray-300 hidden sm:inline">·</span>
                  <span v-if="it.note" class="truncate hidden sm:inline text-gray-400">{{ it.note }}</span>
                </div>
                <!-- progress -->
                <div v-if="liveDl(it) && ['queued','running'].includes(liveDl(it).status)" class="mt-1.5">
                  <div class="flex items-center justify-between text-[0.66rem] font-mono text-gray-500 mb-0.5">
                    <span>{{ liveDl(it).status === 'queued' ? 'Đang chờ…' : 'Đang tải…' }}</span>
                    <span>{{ dlText(liveDl(it)) }}</span>
                  </div>
                  <div class="h-1.5 rounded-full bg-gray-100 overflow-hidden">
                    <div class="h-full bg-primary transition-all" :class="liveDl(it).pct == null && 'animate-pulse w-1/3'" :style="liveDl(it).pct != null ? { width: liveDl(it).pct + '%' } : {}" />
                  </div>
                </div>
                <p v-else-if="liveDl(it) && liveDl(it).status === 'error'" class="mt-1 text-[0.68rem] text-rose-500 truncate">⚠ {{ liveDl(it).error || 'Tải lỗi' }}</p>
              </div>

              <!-- action -->
              <div class="flex-shrink-0">
                <template v-if="liveDl(it) && ['queued','running'].includes(liveDl(it).status)">
                  <button type="button" class="mf-action mf-action-danger" title="Huỷ tải" @click="cancel(liveDl(it))"><i class="bi bi-x-lg" /></button>
                </template>
                <template v-else-if="it.installed">
                  <div class="flex items-center gap-1">
                    <span class="inline-flex items-center gap-1 text-[0.72rem] font-semibold text-emerald-600"><i class="bi bi-check-circle-fill" /> Đã cài</span>
                    <button type="button" class="mf-action mf-action-danger" title="Xoá khỏi đĩa" @click="removeInstalled(it)"><i class="bi bi-trash" /></button>
                  </div>
                </template>
                <template v-else-if="!it.url && it.kind === 'comfy'">
                  <span class="text-[0.7rem] text-gray-400 italic" :title="it.note">prebuilt</span>
                </template>
                <template v-else>
                  <button type="button" class="press inline-flex items-center gap-1.5 h-8 px-3 rounded-full bg-primary/10 text-primary text-[0.74rem] font-semibold hover:bg-primary hover:text-white transition-colors" @click="install(it)">
                    <i class="bi bi-download" /> Tải
                  </button>
                </template>
              </div>
            </div>
          </div>
        </div>
      </div>
    </template>

    <!-- ════════ UPLOAD CUSTOM (giữ nguyên) ════════ -->
    <template v-else>
      <div class="flex-1 min-h-0 overflow-auto bg-gray-50 rounded-3xl border border-gray-200/60 shadow-card">
        <div v-if="loading" class="text-center py-16 text-xs text-gray-400 italic">Đang tải danh sách model...</div>
        <div v-else-if="!items.length" class="text-center py-16 text-sm text-gray-400">
          <i class="bi bi-boxes text-4xl text-gray-300 mb-2 block" />
          Chưa có model upload nào. Bấm "Upload model" để đưa file model tự train (LoRA/checkpoint…) lên ComfyUI.
        </div>
        <table v-else class="mf-table w-full min-w-[720px]">
          <thead>
            <tr>
              <th class="mf-th text-left">Tên file</th>
              <th class="mf-th text-left whitespace-nowrap">Loại</th>
              <th class="mf-th text-left whitespace-nowrap">Dung lượng</th>
              <th class="mf-th text-right whitespace-nowrap">Ngày tải</th>
              <th class="mf-th-actions" />
            </tr>
          </thead>
          <tbody>
            <tr v-for="it in items" :key="it.id" class="mf-row">
              <td class="mf-cell">
                <div class="flex items-center gap-3 min-w-0">
                  <span class="inline-flex h-9 w-9 items-center justify-center rounded-xl bg-primary-50 text-primary flex-shrink-0">
                    <i :class="['bi', TYPE_ICON[it.type] || 'bi-file-earmark-binary']" />
                  </span>
                  <div class="min-w-0">
                    <div class="text-sm font-semibold text-gray-900 truncate font-mono">{{ it.filename }}</div>
                    <div v-if="it.note" class="text-[0.72rem] text-gray-500 truncate">{{ it.note }}</div>
                  </div>
                </div>
              </td>
              <td class="mf-cell"><UiBadge variant="neutral" size="sm" dot>{{ TYPE_LABEL[it.type] || it.type }}</UiBadge></td>
              <td class="mf-cell"><span class="text-sm font-mono text-gray-600">{{ formatSize(it.sizeBytes) }}</span></td>
              <td class="mf-cell mf-date">{{ formatDate(it.createdAt) }}</td>
              <td class="mf-cell-actions">
                <button type="button" class="mf-action mf-action-danger" title="Xoá model" @click="del(it)"><i class="bi bi-trash" /></button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <!-- Upload panel (custom) -->
    <UiSidePanel v-model="panelOpen" title="Upload model AI" subtitle="File model tự train (LoRA / checkpoint / VAE / text-encoder) — lưu thẳng vào ComfyUI">
      <div class="space-y-4">
        <div>
          <label class="text-xs font-semibold text-gray-700">Loại model</label>
          <UiDropdown v-model="form.type" :options="TYPE_OPTS" full-width no-clear class="mt-1.5" />
          <p class="text-[0.68rem] text-gray-500 mt-1">{{ TYPE_HINT[form.type] }}</p>
        </div>
        <UiUploadDrop v-model="form.file" label="File model (.safetensors / .gguf …)" icon="bi-file-earmark-binary" accept=".safetensors,.gguf,.pt,.pth,.ckpt,.bin" hint="File lớn (vài trăm MB tới vài GB) upload có thể lâu, đừng đóng tab" />
        <div>
          <label class="text-xs font-semibold text-gray-700">Ghi chú (tuỳ chọn)</label>
          <UiInput v-model="form.note" maxlength="300" placeholder="VD: LoRA LTX-2.3 train phong cách sản phẩm Pebsteel" class="mt-1.5" />
        </div>
        <div v-if="saving" class="space-y-1.5">
          <div class="flex items-center justify-between text-[0.72rem] font-mono text-gray-600"><span>Đang upload…</span><span>{{ uploadPct }}%</span></div>
          <div class="h-2 rounded-full bg-gray-100 overflow-hidden"><div class="h-full bg-primary transition-all" :style="{ width: uploadPct + '%' }" /></div>
        </div>
        <p class="text-[0.7rem] text-gray-500 leading-relaxed bg-gray-50 border border-gray-200/70 rounded-2xl px-3 py-2.5">
          <i class="bi bi-info-circle me-1" />
          Model gom vào thư mục <code>models/uploads/</code> riêng (không lẫn model hệ thống), dễ dọn. Loại file phải khớp đúng.
        </p>
      </div>
      <template #footer>
        <UiButton variant="secondary" size="md" :disabled="saving" @click="panelOpen = false">Huỷ</UiButton>
        <UiButton variant="primary" size="md" :loading="saving" :disabled="!canSubmit" @click="submitForm"><i class="bi bi-cloud-arrow-up" /> Upload</UiButton>
      </template>
    </UiSidePanel>

    <!-- Add-by-URL panel (catalog custom) -->
    <UiSidePanel v-model="customOpen" title="Thêm model (URL)" subtitle="Tải model bất kỳ vào ComfyUI/Ollama bằng link trực tiếp">
      <div class="space-y-4">
        <div>
          <label class="text-xs font-semibold text-gray-700">Nguồn</label>
          <UiDropdown v-model="cform.kind" :options="[{value:'comfy',label:'ComfyUI (file)'},{value:'ollama',label:'Ollama (model)'}]" full-width no-clear class="mt-1.5" />
        </div>
        <template v-if="cform.kind === 'comfy'">
          <div>
            <label class="text-xs font-semibold text-gray-700">Loại (thư mục đích)</label>
            <UiDropdown v-model="cform.type" :options="CATALOG_TYPE_OPTS" full-width no-clear class="mt-1.5" />
          </div>
          <div>
            <label class="text-xs font-semibold text-gray-700">URL tải trực tiếp (.safetensors/.gguf…)</label>
            <UiInput v-model="cform.url" placeholder="https://huggingface.co/.../model.safetensors" class="mt-1.5" />
          </div>
          <div>
            <label class="text-xs font-semibold text-gray-700">Tên file lưu (để trống = lấy từ URL)</label>
            <UiInput v-model="cform.filename" placeholder="model.safetensors" class="mt-1.5" />
          </div>
        </template>
        <template v-else>
          <div>
            <label class="text-xs font-semibold text-gray-700">Tên model Ollama</label>
            <UiInput v-model="cform.model" placeholder="vd: qwen2.5:14b" class="mt-1.5" />
          </div>
        </template>
        <div>
          <label class="text-xs font-semibold text-gray-700">Nhãn (tuỳ chọn)</label>
          <UiInput v-model="cform.label" maxlength="200" class="mt-1.5" />
        </div>
        <p class="text-[0.7rem] text-gray-500 leading-relaxed bg-gray-50 border border-gray-200/70 rounded-2xl px-3 py-2.5">
          <i class="bi bi-info-circle me-1" /> Tải chạy nền trên server (aria2 song song / ollama pull). Bấm Thêm rồi xem tiến trình ở danh sách.
        </p>
      </div>
      <template #footer>
        <UiButton variant="secondary" size="md" :disabled="customSaving" @click="customOpen = false">Huỷ</UiButton>
        <UiButton variant="primary" size="md" :loading="customSaving" :disabled="!canCustom" @click="submitCustom"><i class="bi bi-download" /> Thêm & tải</UiButton>
      </template>
    </UiSidePanel>
  </div>
</template>

<script setup>
const auth = useAuth()
const toast = useToast()
const confirm = useConfirm()
const config = useRuntimeConfig()

const TYPE_OPTS = [
  { value: 'loras', label: 'LoRA' },
  { value: 'checkpoints', label: 'Checkpoint' },
  { value: 'unet', label: 'UNet / Diffusion' },
  { value: 'vae', label: 'VAE' },
  { value: 'text_encoders', label: 'Text encoder' },
  { value: 'clip_vision', label: 'CLIP vision' }
]
// Catalog cho phép thêm diffusion_models (ngoài 6 loại upload).
const CATALOG_TYPE_OPTS = [...TYPE_OPTS, { value: 'diffusion_models', label: 'Diffusion models' }]
const TYPE_LABEL = Object.fromEntries(CATALOG_TYPE_OPTS.map((o) => [o.value, o.label]))
const TYPE_ICON = {
  loras: 'bi-stars', checkpoints: 'bi-box-seam', unet: 'bi-cpu', diffusion_models: 'bi-cpu',
  vae: 'bi-images', text_encoders: 'bi-translate', clip_vision: 'bi-eye'
}
const TYPE_HINT = {
  loras: 'Adapter fine-tune gắn lên base model.',
  checkpoints: 'Model all-in-one (CheckpointLoaderSimple).',
  unet: 'File diffusion/UNet riêng (UnetLoader / GGUF).',
  vae: 'Bộ giải mã latent → ảnh/video.',
  text_encoders: 'Bộ mã hoá prompt (Gemma/T5/umt5).',
  clip_vision: 'Bộ mã hoá ảnh CLIP-Vision.'
}
const KIND_FILTERS = [{ v: 'all', label: 'Tất cả' }, { v: 'comfy', label: 'ComfyUI' }, { v: 'ollama', label: 'Ollama' }]
const TIER_FILTERS = [{ v: 'all', label: 'Mọi mức' }, { v: 'core', label: 'Core' }, { v: 'optional', label: 'Optional' }, { v: 'missing', label: 'Chưa cài' }]

const view = ref('catalog')

// ── catalog state ──
const catalog = ref({ comfy: [], ollama: [] })
const catalogLoading = ref(false)
const dlMap = ref({})           // key `${kind}:${ref}` -> download row
const q = ref('')
const kindFilter = ref('all')
const tierFilter = ref('all')
let pollTimer = null

const allItems = computed(() => [...catalog.value.comfy, ...catalog.value.ollama])
const totalCount = computed(() => allItems.value.length)
const installedCount = computed(() => allItems.value.filter((it) => it.installed).length)
const refOf = (it) => (it.kind === 'ollama' ? it.model : it.filename)
const keyOf = (it) => `${it.kind}:${refOf(it)}`
const liveDl = (it) => dlMap.value[keyOf(it)] || it.download || null
const activeDownloads = computed(() => Object.values(dlMap.value).filter((d) => d && ['queued', 'running'].includes(d.status)))

const groups = computed(() => {
  const out = []
  const byGroup = new Map()
  for (const it of allItems.value) {
    if (kindFilter.value !== 'all' && it.kind !== kindFilter.value) continue
    if (tierFilter.value === 'core' && it.tier !== 'core') continue
    if (tierFilter.value === 'optional' && it.tier !== 'optional') continue
    if (tierFilter.value === 'missing' && it.installed) continue
    if (q.value.trim()) {
      const s = q.value.trim().toLowerCase()
      const hay = `${it.label} ${it.filename || ''} ${it.model || ''} ${it.note || ''} ${it.group}`.toLowerCase()
      if (!hay.includes(s)) continue
    }
    if (!byGroup.has(it.group)) { byGroup.set(it.group, []); out.push({ group: it.group, items: byGroup.get(it.group) }) }
    byGroup.get(it.group).push(it)
  }
  return out
})

function segCls(v) {
  return ['px-3 h-8 inline-flex items-center rounded-full transition-colors', view.value === v ? 'bg-gray-50 text-primary shadow-card' : 'text-gray-500 hover:text-gray-700']
}
function chipCls(active) {
  return ['px-2 h-7 inline-flex items-center rounded-full transition-colors', active ? 'bg-gray-50 text-primary shadow-card' : 'text-gray-500 hover:text-gray-700']
}

function dlText(d) {
  if (!d) return ''
  if (d.pct != null) return `${d.pct}%`
  if (d.doneBytes) return formatSize(d.doneBytes)
  return '…'
}

async function loadCatalog() {
  catalogLoading.value = true
  try {
    const res = await auth.beFetch('/models/catalog')
    catalog.value = { comfy: res.comfy || [], ollama: res.ollama || [] }
    // seed dlMap từ download kèm trong catalog
    const m = {}
    for (const it of [...catalog.value.comfy, ...catalog.value.ollama]) if (it.download) m[keyOf(it)] = it.download
    dlMap.value = { ...m, ...dlMap.value }
    ensurePolling()
  } catch (e) {
    toast.error(e?.data?.error || e?.message || 'Lỗi tải catalog')
  } finally {
    catalogLoading.value = false
  }
}

async function pollDownloads() {
  try {
    const { items: rows } = await auth.beFetch('/models/downloads')
    const m = {}
    let anyDone = false
    for (const d of rows) {
      const k = `${d.kind}:${d.ref}`
      // chỉ giữ bản mới nhất / đang sống
      if (!m[k]) m[k] = d
      if (d.status === 'done') anyDone = true
    }
    // phát hiện vừa hoàn tất → refresh catalog (cập nhật trạng thái đã cài)
    const wasActive = activeDownloads.value.length
    dlMap.value = m
    if (anyDone || (wasActive && !activeDownloads.value.length)) await loadCatalog()
  } catch { /* noop */ }
  ensurePolling()
}
function ensurePolling() {
  const need = activeDownloads.value.length > 0
  if (need && !pollTimer) pollTimer = setInterval(pollDownloads, 2000)
  if (!need && pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

async function install(it) {
  try {
    const res = await auth.beFetch('/models/catalog/install', { method: 'POST', body: { id: it.id } })
    if (res?.download) { dlMap.value = { ...dlMap.value, [keyOf(it)]: res.download }; ensurePolling() }
    toast.success(`Bắt đầu tải ${it.label}`)
  } catch (e) {
    toast.error(e?.data?.error || e?.message || 'Không bắt đầu được tải')
  }
}
// ALD 16/06/2026 - Cài CẢ NHÓM: enqueue tải mọi model chưa cài trong group (BE scheduler tự xếp hàng tải).
async function installGroup(grp) {
  const pending = grp.items.filter((it) => !it.installed && !liveDl(it))
  if (!pending.length) return
  toast.success(`Bắt đầu tải ${pending.length} model nhóm "${grp.group}"…`)
  for (const it of pending) {
    try {
      const res = await auth.beFetch('/models/catalog/install', { method: 'POST', body: { id: it.id } })
      if (res?.download) dlMap.value = { ...dlMap.value, [keyOf(it)]: res.download }
    } catch (e) { toast.error(`${it.label}: ${e?.data?.error || e?.message || 'lỗi'}`) }
  }
  ensurePolling()
}
async function cancel(d) {
  try {
    await auth.beFetch(`/models/downloads/${d.id}`, { method: 'DELETE' })
    const m = { ...dlMap.value }; for (const k of Object.keys(m)) if (m[k]?.id === d.id) delete m[k]
    dlMap.value = m
    toast.success('Đã huỷ tải')
    setTimeout(loadCatalog, 600)
  } catch (e) { toast.error(e?.data?.error || e?.message || 'Huỷ thất bại') }
}
async function removeInstalled(it) {
  const ok = await confirm.ask({
    title: 'Xoá model khỏi đĩa?',
    message: `"${it.label}" sẽ bị xoá khỏi ${it.kind === 'ollama' ? 'Ollama' : 'ComfyUI'}. Tính năng dùng model này sẽ lỗi khi chạy.`,
    confirmText: 'Xoá', variant: 'danger'
  })
  if (!ok) return
  try {
    const body = it.kind === 'ollama' ? { kind: 'ollama', model: it.model } : { kind: 'comfy', filename: it.filename }
    await auth.beFetch('/models/installed', { method: 'DELETE', body })
    toast.success('Đã xoá model')
    await loadCatalog()
  } catch (e) { toast.error(e?.data?.error || e?.message || 'Xoá thất bại') }
}

// add-by-URL
const customOpen = ref(false)
const customSaving = ref(false)
const cform = reactive({ kind: 'comfy', type: 'loras', url: '', filename: '', model: '', label: '' })
const canCustom = computed(() => cform.kind === 'ollama' ? !!cform.model.trim() : (!!cform.url.trim() && !!cform.type))
function openCustom() { Object.assign(cform, { kind: 'comfy', type: 'loras', url: '', filename: '', model: '', label: '' }); customOpen.value = true }
async function submitCustom() {
  if (customSaving.value || !canCustom.value) return
  customSaving.value = true
  try {
    const body = cform.kind === 'ollama'
      ? { kind: 'ollama', model: cform.model.trim(), label: cform.label.trim() || undefined }
      : { kind: 'comfy', type: cform.type, url: cform.url.trim(), filename: cform.filename.trim() || undefined, label: cform.label.trim() || undefined }
    const res = await auth.beFetch('/models/catalog/custom', { method: 'POST', body })
    if (res?.download) { dlMap.value = { ...dlMap.value, [`${res.download.kind}:${res.download.ref}`]: res.download }; ensurePolling() }
    toast.success('Đã thêm & bắt đầu tải')
    customOpen.value = false
    setTimeout(loadCatalog, 600)
  } catch (e) {
    toast.error(e?.data?.error || e?.message || 'Thêm model thất bại')
  } finally { customSaving.value = false }
}

// ── upload custom (giữ nguyên) ──
const items = ref([])
const loading = ref(false)
const saving = ref(false)
const uploadPct = ref(0)
const panelOpen = ref(false)
const form = reactive({ file: null, type: 'loras', note: '' })
const canSubmit = computed(() => !!form.file && !!form.type)
const totalBytes = computed(() => items.value.reduce((s, it) => s + (Number(it.sizeBytes) || 0), 0))

function formatDate(value) {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString('vi-VN', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' })
}
function formatSize(bytes) {
  const b = Number(bytes)
  if (!b || !isFinite(b)) return '—'
  if (b >= 1024 ** 3) return (b / 1024 ** 3).toFixed(2) + ' GB'
  if (b >= 1024 ** 2) return (b / 1024 ** 2).toFixed(1) + ' MB'
  if (b >= 1024) return (b / 1024).toFixed(0) + ' KB'
  return b + ' B'
}
function resetForm() { Object.assign(form, { file: null, type: 'loras', note: '' }); uploadPct.value = 0 }
function openCreate() { resetForm(); panelOpen.value = true }

async function load() {
  loading.value = true
  try { items.value = (await auth.beFetch('/models')).items || [] }
  catch (e) { toast.error(e?.data?.error || e?.message || 'Lỗi tải danh sách model') }
  finally { loading.value = false }
}
function refresh() { loadCatalog(); load() }

function uploadModel(fd, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${config.public.motionBackendUrl}/models`)
    const headers = auth.authHeader?.() || {}
    for (const [k, v] of Object.entries(headers)) { if (v) xhr.setRequestHeader(k, v) }
    xhr.upload.onprogress = (e) => { if (e.lengthComputable && onProgress) onProgress(Math.round((e.loaded / e.total) * 100)) }
    xhr.onload = () => {
      let body = null
      try { body = JSON.parse(xhr.responseText) } catch { /* noop */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body)
      else reject(new Error(body?.error || `Upload lỗi (HTTP ${xhr.status})`))
    }
    xhr.onerror = () => reject(new Error('Lỗi mạng khi upload (file quá lớn hoặc mất kết nối)'))
    xhr.send(fd)
  })
}
async function submitForm() {
  if (saving.value || !canSubmit.value) return
  saving.value = true
  uploadPct.value = 0
  try {
    const fd = new FormData()
    fd.append('type', form.type)
    if (form.note.trim()) fd.append('note', form.note.trim())
    fd.append('file', form.file)
    const res = await uploadModel(fd, (p) => { uploadPct.value = p })
    if (res?.item) items.value.unshift(res.item)
    toast.success('Đã upload model')
    panelOpen.value = false
    resetForm()
  } catch (e) { toast.error(e?.message || 'Upload lỗi') }
  finally { saving.value = false }
}
async function del(it) {
  const ok = await confirm.ask({
    title: 'Xoá model này?',
    message: `"${it.filename}" sẽ bị xoá khỏi ComfyUI. Node/workflow nào đang dùng model này sẽ lỗi khi chạy.`,
    confirmText: 'Xoá', variant: 'danger'
  })
  if (!ok) return
  try {
    await auth.beFetch(`/models/${it.id}`, { method: 'DELETE' })
    items.value = items.value.filter((x) => x.id !== it.id)
    toast.success('Đã xoá model')
  } catch (e) { toast.error(e?.data?.error || e?.message || 'Xoá thất bại') }
}

onMounted(() => {
  loadCatalog()
  load()
})
onBeforeUnmount(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<style scoped>
.mf-table { border-collapse: collapse; }
.mf-table thead {
  position: sticky; top: 0; z-index: 10;
  background: var(--glass-bg-solid);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
}
.mf-th {
  font-size: 11px; font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase;
  color: rgba(235,236,240,0.55); padding: 12px 14px; border-bottom: 0.5px solid rgba(235,236,240,0.1);
}
.mf-th-actions { width: 64px; padding: 12px; border-bottom: 0.5px solid rgba(235,236,240,0.1); }
.mf-row { transition: background 0.16s cubic-bezier(0.32, 0.72, 0, 1); }
.mf-row:hover { background: rgba(235,236,240,0.04); }
.mf-cell { padding: 10px 14px; vertical-align: middle; border-top: 0.5px solid rgba(235,236,240,0.06); }
.mf-cell-actions { padding: 10px 12px; vertical-align: middle; text-align: right; border-top: 0.5px solid rgba(235,236,240,0.06); white-space: nowrap; }
.mf-date {
  text-align: right; font-size: 12px; color: rgba(235,236,240,0.55);
  white-space: nowrap; font-variant-numeric: tabular-nums;
}
.mf-action {
  width: 30px; height: 30px;
  display: inline-flex; align-items: center; justify-content: center;
  background: transparent; border: none; border-radius: 999px;
  color: rgba(235,236,240,0.45); cursor: pointer; font-size: 14px; transition: all 0.16s;
}
.mf-action:hover { background: rgba(37,99,235,0.08); color: var(--color-primary, #2563eb); }
.mf-action-danger:hover { background: rgba(255,59,48,0.12); color: #FF3B30; }
</style>
