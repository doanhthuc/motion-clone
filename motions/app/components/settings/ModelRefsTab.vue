<!-- ALD 11/06/2026 - Model refs management: đổi gallery rời rạc thành table giống UsersManager/HRMHub.
     Upload nằm trong side panel, table là bề mặt quản lý chính: search/filter/edit/active/delete. -->
<template>
  <div class="flex flex-col flex-1 min-h-0 gap-3">
    <!-- Header -->
    <div class="flex items-center gap-3 flex-wrap flex-shrink-0">
      <span class="inline-flex h-11 w-11 items-center justify-center rounded-2xl bg-gradient-to-br from-primary to-primary-dark text-white shadow-pill flex-shrink-0">
        <i class="bi bi-person-bounding-box text-lg" />
      </span>
      <div class="min-w-0">
        <h3 class="text-sm font-bold text-gray-900 uppercase tracking-wide">Thư viện model mẫu</h3>
        <div class="flex items-center gap-2 mt-1 text-[0.7rem] font-mono text-gray-500 flex-wrap">
          <span><b class="text-gray-800">{{ stats.total }}</b> mẫu</span>
          <span class="text-gray-300">·</span>
          <span><b class="text-emerald-600">{{ stats.active }}</b> đang dùng</span>
          <span class="text-gray-300">·</span>
          <span><b class="text-primary">{{ stats.female }}</b> nữ</span>
          <span class="text-gray-300">·</span>
          <span><b class="text-sky-600">{{ stats.male }}</b> nam</span>
        </div>
      </div>
      <div class="flex items-center gap-2 ml-auto">
        <button
          type="button"
          class="press inline-flex items-center gap-2 h-10 px-4 rounded-full glass shadow-card text-sm font-semibold text-gray-700 hover:bg-gray-100 transition-colors"
          @click="load"
        >
          <i :class="['bi', loading ? 'bi-arrow-clockwise animate-spin' : 'bi-arrow-clockwise']" />
          Làm mới
        </button>
        <button
          type="button"
          class="press inline-flex items-center gap-1.5 h-10 px-4 rounded-full bg-primary text-white text-sm font-semibold shadow-pill hover:bg-primary-dark transition-colors"
          @click="openCreate"
        >
          <i class="bi bi-cloud-arrow-up-fill" />
          Thêm model
        </button>
      </div>
    </div>

    <!-- Toolbar -->
    <div class="flex items-center gap-2 flex-wrap flex-shrink-0">
      <div class="flex-1 min-w-[220px] relative">
        <i class="bi bi-search absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 text-sm" />
        <input
          v-model="searchDraft"
          type="text"
          placeholder="Tìm theo ghi chú, ID, giới tính, độ tuổi..."
          class="w-full h-11 pl-9 pr-3 rounded-2xl bg-gray-50 border border-gray-200 text-sm focus:outline-none focus:border-primary"
          @input="page = 1"
        />
      </div>
      <UiDropdown v-model="filters.gender" :options="genderFilterOptions" placeholder="Mọi giới tính" class="w-44" @update:model-value="page = 1" />
      <UiDropdown v-model="filters.age" :options="ageFilterOptions" placeholder="Mọi độ tuổi" class="w-44" @update:model-value="page = 1" />
      <UiDropdown v-model="filters.active" :options="statusFilterOptions" placeholder="Mọi trạng thái" class="w-44" @update:model-value="page = 1" />
    </div>

    <!-- Table — màn rộng full-width, màn hẹp cuộn ngang (min-width) thay vì bó cột -->
    <div class="flex-1 min-h-0 overflow-auto bg-gray-50 rounded-3xl border border-gray-200/60 shadow-card">
      <div v-if="loading" class="text-center py-16 text-xs text-gray-400 italic">Đang tải thư viện model mẫu...</div>
      <div v-else-if="!filtered.length" class="text-center py-16 text-sm text-gray-400">
        <i class="bi bi-person-bounding-box text-4xl text-gray-300 mb-2 block" />
        {{ hasFilter ? 'Không có model mẫu phù hợp bộ lọc' : 'Chưa có model mẫu nào. Bấm "Thêm model" để upload.' }}
      </div>
      <table v-else class="model-table w-full min-w-[820px]">
        <thead>
          <tr>
            <th class="mt-th text-left">Model</th>
            <th class="mt-th text-left whitespace-nowrap">Giới tính</th>
            <th class="mt-th text-left whitespace-nowrap">Độ tuổi</th>
            <th class="mt-th text-left whitespace-nowrap">Kích thước</th>
            <th class="mt-th text-center whitespace-nowrap">Trạng thái</th>
            <th class="mt-th text-right whitespace-nowrap">Ngày tạo</th>
            <th class="mt-th-actions" />
          </tr>
        </thead>
        <tbody>
          <tr v-for="it in pagedItems" :key="it.id" class="mt-row">
            <td class="mt-cell">
              <div class="flex items-center gap-3 min-w-0">
                <button type="button" class="mt-thumb" title="Xem ảnh" @click="previewItem = it">
                  <img :src="it.url" :alt="it.label || 'model ref'" loading="lazy" referrerpolicy="no-referrer" />
                </button>
                <div class="min-w-0">
                  <div class="text-sm font-semibold text-gray-900 truncate">{{ it.label || 'Chưa có ghi chú' }}</div>
                  <div class="text-[0.72rem] text-gray-500 truncate font-mono">{{ shortId(it.id) }}</div>
                </div>
              </div>
            </td>
            <td class="mt-cell">
              <UiBadge :variant="it.gender === 'female' ? 'primary' : 'neutral'" size="sm" dot>
                {{ GENDER_LABEL[it.gender] || it.gender || '—' }}
              </UiBadge>
            </td>
            <td class="mt-cell">
              <span class="text-sm font-semibold text-gray-700">{{ AGE_LABEL[it.ageGroup] || it.ageGroup || '—' }}</span>
            </td>
            <td class="mt-cell">
              <div class="text-xs font-mono text-gray-500 whitespace-nowrap">{{ formatSize(it) }}</div>
              <div v-if="it.sizeBytes" class="text-[0.7rem] text-gray-400 whitespace-nowrap">{{ formatBytes(it.sizeBytes) }}</div>
            </td>
            <td class="mt-cell text-center">
              <button
                type="button"
                :disabled="togglingId === it.id"
                :class="['mt-status press', it.isActive ? 'mt-status-on' : 'mt-status-off']"
                :title="it.isActive ? 'Bấm để tắt model này' : 'Bấm để bật model này'"
                @click="toggle(it)"
              >
                <span :class="['mt-status-dot', it.isActive ? 'bg-emerald-500' : 'bg-gray-400']" />
                {{ it.isActive ? 'Đang dùng' : 'Đã tắt' }}
              </button>
            </td>
            <td class="mt-cell mt-date">{{ formatDate(it.createdAt) }}</td>
            <td class="mt-cell-actions">
              <div class="inline-flex items-center gap-0.5">
                <button type="button" class="mt-action" title="Sửa thông tin" @click="openEdit(it)">
                  <i class="bi bi-pencil" />
                </button>
                <button type="button" class="mt-action" title="Xem ảnh" @click="previewItem = it">
                  <i class="bi bi-arrows-fullscreen" />
                </button>
                <button type="button" class="mt-action mt-action-danger" title="Xoá model" @click="del(it)">
                  <i class="bi bi-trash" />
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Pagination -->
    <div v-if="filtered.length > 0" class="flex items-center justify-between gap-3 flex-wrap text-xs flex-shrink-0">
      <span class="text-gray-500">
        {{ startRow }}-{{ endRow }} / {{ filtered.length }} model mẫu
      </span>
      <div v-if="totalPages > 1" class="flex items-center gap-1">
        <button
          type="button"
          :disabled="page <= 1"
          class="press h-8 w-8 flex items-center justify-center rounded-lg hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed"
          @click="page -= 1"
        >
          <i class="bi bi-chevron-left" />
        </button>
        <span class="px-3 font-mono text-gray-700">{{ page }} / {{ totalPages }}</span>
        <button
          type="button"
          :disabled="page >= totalPages"
          class="press h-8 w-8 flex items-center justify-center rounded-lg hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed"
          @click="page += 1"
        >
          <i class="bi bi-chevron-right" />
        </button>
      </div>
    </div>

    <!-- Create / Edit panel -->
    <UiSidePanel
      v-model="panelOpen"
      :title="editing ? 'Sửa model mẫu' : 'Thêm model mẫu'"
      :subtitle="editing ? shortId(form.id) : 'Upload ảnh người mẫu, backend tự xóa nền và lưu vào thư viện'"
    >
      <div class="space-y-4">
        <UiUploadDrop
          v-if="!editing"
          v-model="form.file"
          label="Ảnh người mẫu"
          accept="image/*"
        />

        <div v-else-if="editingItem" class="rounded-2xl border border-gray-200/70 bg-gray-50 p-3">
          <img
            :src="editingItem.url"
            :alt="editingItem.label || 'model ref'"
            class="w-full max-h-[320px] object-contain rounded-xl bg-gray-50"
            referrerpolicy="no-referrer"
          />
          <p class="mt-2 text-[0.7rem] text-gray-500 font-mono truncate">{{ editingItem.id }}</p>
        </div>

        <div>
          <label class="text-xs font-semibold text-gray-700">Ghi chú</label>
          <UiInput v-model="form.label" maxlength="120" placeholder="VD: mẫu Việt tóc dài, studio trắng" class="mt-1.5" />
        </div>

        <div class="grid grid-cols-2 gap-3">
          <div>
            <label class="text-xs font-semibold text-gray-700">Giới tính</label>
            <UiDropdown v-model="form.gender" :options="GENDER_OPTS" full-width no-clear class="mt-1.5" />
          </div>
          <div>
            <label class="text-xs font-semibold text-gray-700">Độ tuổi</label>
            <UiDropdown v-model="form.age" :options="AGE_OPTS" full-width no-clear class="mt-1.5" />
          </div>
        </div>

        <div v-if="editing" class="flex items-center justify-between gap-3 bg-gray-50 border border-gray-200/70 rounded-2xl px-4 py-3">
          <div class="min-w-0">
            <div class="text-sm font-semibold text-gray-800">Cho workflow sử dụng</div>
            <p class="text-[0.7rem] text-gray-500 mt-0.5">Tắt thì worker sẽ không bốc model này làm mẫu.</p>
          </div>
          <button
            type="button"
            :class="[
              'relative inline-flex h-6 w-11 flex-shrink-0 rounded-full transition-colors',
              form.isActive ? 'bg-emerald-500' : 'bg-gray-300'
            ]"
            @click="form.isActive = !form.isActive"
          >
            <span :class="['inline-block h-5 w-5 mt-0.5 rounded-full bg-gray-50 shadow transition-transform', form.isActive ? 'translate-x-5' : 'translate-x-0.5']" />
          </button>
        </div>
      </div>

      <template #footer>
        <UiButton variant="secondary" size="md" @click="panelOpen = false">Huỷ</UiButton>
        <UiButton variant="primary" size="md" :loading="saving" :disabled="!canSubmit" @click="submitForm">
          <i :class="editing ? 'bi bi-check2' : 'bi bi-cloud-arrow-up'" />
          {{ editing ? 'Lưu thay đổi' : 'Upload + xóa nền' }}
        </UiButton>
      </template>
    </UiSidePanel>

    <!-- Preview -->
    <div v-if="previewItem" class="fixed inset-0 z-[80] bg-black/55 backdrop-blur-sm flex items-center justify-center p-4" @click.self="previewItem = null">
      <div class="bg-gray-50 rounded-3xl shadow-island max-w-3xl w-full max-h-[90vh] overflow-hidden flex flex-col">
        <div class="flex items-center justify-between gap-3 px-4 py-3 border-b border-gray-100">
          <div class="min-w-0">
            <div class="text-sm font-bold text-gray-900 truncate">{{ previewItem.label || 'Model mẫu' }}</div>
            <div class="text-[0.72rem] text-gray-500 font-mono truncate">{{ previewItem.id }}</div>
          </div>
          <button type="button" class="mt-action" title="Đóng" @click="previewItem = null">
            <i class="bi bi-x-lg" />
          </button>
        </div>
        <div class="min-h-0 overflow-auto bg-gray-50 p-4">
          <img :src="previewItem.url" alt="" class="mx-auto max-h-[72vh] object-contain rounded-2xl bg-gray-50" referrerpolicy="no-referrer" />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
const auth = useAuth()
const toast = useToast()
const confirm = useConfirm()

const GENDER_OPTS = [{ value: 'female', label: 'Nữ' }, { value: 'male', label: 'Nam' }]
const AGE_OPTS = [{ value: 'young', label: 'Trẻ' }, { value: 'middle', label: 'Trung niên' }, { value: 'old', label: 'Già' }]
const GENDER_LABEL = { female: 'Nữ', male: 'Nam' }
const AGE_LABEL = { young: 'Trẻ', middle: 'Trung niên', old: 'Già' }

const genderFilterOptions = [{ value: '', label: 'Mọi giới tính' }, ...GENDER_OPTS]
const ageFilterOptions = [{ value: '', label: 'Mọi độ tuổi' }, ...AGE_OPTS]
const statusFilterOptions = [
  { value: '', label: 'Mọi trạng thái' },
  { value: 'true', label: 'Đang dùng' },
  { value: 'false', label: 'Đã tắt' }
]

const items = ref([])
const loading = ref(false)
const saving = ref(false)
const togglingId = ref('')
const previewItem = ref(null)

const searchDraft = ref('')
const filters = reactive({ gender: '', age: '', active: '' })
const page = ref(1)
const limit = 20

const panelOpen = ref(false)
const form = reactive({ id: '', file: null, label: '', gender: 'female', age: 'young', isActive: true })
const editing = computed(() => !!form.id)
const editingItem = computed(() => items.value.find((it) => it.id === form.id) || null)
const canSubmit = computed(() => editing.value || !!form.file)

const stats = computed(() => {
  const total = items.value.length
  return {
    total,
    active: items.value.filter((it) => it.isActive).length,
    female: items.value.filter((it) => it.gender === 'female').length,
    male: items.value.filter((it) => it.gender === 'male').length
  }
})

const hasFilter = computed(() => !!(searchDraft.value.trim() || filters.gender || filters.age || filters.active))
const filtered = computed(() => {
  const q = normalizeText(searchDraft.value)
  return items.value.filter((it) => {
    if (filters.gender && it.gender !== filters.gender) return false
    if (filters.age && it.ageGroup !== filters.age) return false
    if (filters.active === 'true' && !it.isActive) return false
    if (filters.active === 'false' && it.isActive) return false
    if (!q) return true
    const haystack = normalizeText([
      it.id,
      it.label,
      GENDER_LABEL[it.gender],
      AGE_LABEL[it.ageGroup],
      it.gender,
      it.ageGroup
    ].filter(Boolean).join(' '))
    return haystack.includes(q)
  })
})
const totalPages = computed(() => Math.max(1, Math.ceil(filtered.value.length / limit)))
const pagedItems = computed(() => filtered.value.slice((page.value - 1) * limit, page.value * limit))
const startRow = computed(() => filtered.value.length ? (page.value - 1) * limit + 1 : 0)
const endRow = computed(() => Math.min(page.value * limit, filtered.value.length))

watch([filtered, totalPages], () => {
  if (page.value > totalPages.value) page.value = totalPages.value
})

function normalizeText(v) {
  return String(v || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').trim()
}
function shortId(id) {
  if (!id) return '—'
  return String(id).slice(0, 8)
}
function formatDate(value) {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString('vi-VN', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' })
}
function formatSize(it) {
  if (!it?.width || !it?.height) return '—'
  return `${it.width} x ${it.height}`
}
function formatBytes(bytes) {
  const n = Number(bytes)
  if (!Number.isFinite(n) || n <= 0) return ''
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

function resetForm() {
  Object.assign(form, { id: '', file: null, label: '', gender: 'female', age: 'young', isActive: true })
}
function openCreate() {
  resetForm()
  panelOpen.value = true
}
function openEdit(it) {
  Object.assign(form, {
    id: it.id,
    file: null,
    label: it.label || '',
    gender: it.gender || 'female',
    age: it.ageGroup || 'young',
    isActive: it.isActive !== false
  })
  panelOpen.value = true
}

async function load() {
  loading.value = true
  try {
    items.value = (await auth.beFetch('/model-refs')).items || []
  } catch (e) {
    toast.error(e?.data?.error || e?.message || 'Lỗi tải danh sách model mẫu')
  } finally {
    loading.value = false
  }
}

async function submitForm() {
  if (saving.value || !canSubmit.value) return
  saving.value = true
  try {
    if (editing.value) {
      const res = await auth.beFetch(`/model-refs/${form.id}`, {
        method: 'PATCH',
        body: {
          label: form.label.trim(),
          gender: form.gender,
          age_group: form.age,
          is_active: form.isActive
        }
      })
      const idx = items.value.findIndex((it) => it.id === form.id)
      if (idx >= 0) items.value.splice(idx, 1, res.item)
      toast.success('Đã cập nhật model mẫu')
    } else {
      const fd = new FormData()
      fd.append('image', form.file)
      fd.append('gender', form.gender)
      fd.append('age_group', form.age)
      if (form.label.trim()) fd.append('label', form.label.trim())
      const res = await auth.beFetch('/model-refs', { method: 'POST', body: fd })
      items.value.unshift(res.item)
      toast.success('Đã thêm model mẫu')
    }
    panelOpen.value = false
    resetForm()
  } catch (e) {
    toast.error(e?.data?.error || e?.message || (editing.value ? 'Lưu thất bại' : 'Upload lỗi'))
  } finally {
    saving.value = false
  }
}

async function toggle(it) {
  if (togglingId.value) return
  togglingId.value = it.id
  try {
    const res = await auth.beFetch(`/model-refs/${it.id}`, { method: 'PATCH', body: { is_active: !it.isActive } })
    Object.assign(it, res.item)
    toast.success(it.isActive ? 'Đã bật model mẫu' : 'Đã tắt model mẫu')
  } catch (e) {
    toast.error(e?.data?.error || e?.message || 'Đổi trạng thái thất bại')
  } finally {
    togglingId.value = ''
  }
}

async function del(it) {
  const ok = await confirm.ask({
    title: 'Xoá model mẫu?',
    message: `"${it.label || shortId(it.id)}" sẽ bị xoá khỏi thư viện và không còn được worker sử dụng.`,
    confirmText: 'Xoá',
    variant: 'danger'
  })
  if (!ok) return
  try {
    await auth.beFetch(`/model-refs/${it.id}`, { method: 'DELETE' })
    items.value = items.value.filter((x) => x.id !== it.id)
    if (previewItem.value?.id === it.id) previewItem.value = null
    toast.success('Đã xoá model mẫu')
  } catch (e) {
    toast.error(e?.data?.error || e?.message || 'Xoá thất bại')
  }
}

onMounted(load)
</script>

<style scoped>
.model-table { border-collapse: collapse; }
.model-table thead {
  position: sticky; top: 0; z-index: 10;
  background: var(--glass-bg-solid);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
}
.mt-th {
  font-size: 11px; font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase;
  color: rgba(235,236,240,0.55); padding: 12px 14px; border-bottom: 0.5px solid rgba(235,236,240,0.1);
}
.mt-th-actions { width: 112px; padding: 12px; border-bottom: 0.5px solid rgba(235,236,240,0.1); }

.mt-row { transition: background 0.16s cubic-bezier(0.32, 0.72, 0, 1); }
.mt-row:hover { background: rgba(235,236,240,0.04); }
.mt-cell { padding: 10px 14px; vertical-align: middle; border-top: 0.5px solid rgba(235,236,240,0.06); }
.mt-cell-actions { padding: 10px 12px; vertical-align: middle; text-align: right; border-top: 0.5px solid rgba(235,236,240,0.06); white-space: nowrap; }

.mt-thumb {
  flex-shrink: 0; width: 48px; height: 58px; display: inline-flex; align-items: center; justify-content: center;
  overflow: hidden; border-radius: 13px; background: #f3f4f6; border: 1px solid rgba(229,231,235,0.9);
  box-shadow: 0 1px 3px rgba(0,0,0,0.08); transition: transform 0.16s, box-shadow 0.16s;
}
.mt-thumb:hover { transform: translateY(-1px); box-shadow: 0 6px 18px rgba(0,0,0,0.12); }
.mt-thumb img { width: 100%; height: 100%; object-fit: cover; }

.mt-date {
  text-align: right; font-size: 12px; color: rgba(235,236,240,0.55);
  white-space: nowrap; font-variant-numeric: tabular-nums;
}

.mt-status {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 4px 11px; font-size: 11px; font-weight: 700; border-radius: 999px;
  cursor: pointer; transition: all 0.16s; border: 1px solid transparent;
}
.mt-status-on  { background: rgba(16,185,129,0.10); color: #047857; border-color: rgba(16,185,129,0.2); }
.mt-status-off { background: rgba(235,236,240,0.07);  color: rgba(235,236,240,0.6); border-color: rgba(235,236,240,0.12); }
.mt-status:not(:disabled):hover { filter: brightness(0.96); }
.mt-status:disabled { opacity: 0.55; cursor: wait; }
.mt-status-dot { width: 7px; height: 7px; border-radius: 999px; flex-shrink: 0; }

.mt-action {
  width: 30px; height: 30px;
  display: inline-flex; align-items: center; justify-content: center;
  background: transparent; border: none; border-radius: 999px;
  color: rgba(235,236,240,0.45); cursor: pointer; font-size: 14px; transition: all 0.16s;
}
.mt-action:hover { background: rgba(37,99,235,0.08); color: var(--color-primary, #2563eb); }
.mt-action-danger:hover { background: rgba(255,59,48,0.12); color: #FF3B30; }
</style>
