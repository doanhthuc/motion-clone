<template>
  <!-- #region ALD 01/06/2026 - Admin User Manager: fullscreen, KHÔNG scroll trang (chỉ body bảng cuộn).
       Chuỗi flex-1/min-h-0 từ settings <section> xuống → table area là chỗ duy nhất overflow-y-auto. -->
  <div class="flex flex-col flex-1 min-h-0 gap-3">
    <!-- Header: tiêu đề + stats chips + actions -->
    <div class="flex items-center gap-3 flex-wrap flex-shrink-0">
      <span class="inline-flex h-11 w-11 items-center justify-center rounded-2xl bg-gradient-to-br from-primary to-primary-dark text-white shadow-pill flex-shrink-0">
        <i class="bi bi-people-fill text-lg" />
      </span>
      <div class="min-w-0">
        <h3 class="text-sm font-bold text-gray-900 uppercase tracking-wide">Quản lý người dùng</h3>
        <div class="flex items-center gap-2 mt-1 text-[0.7rem] font-mono text-gray-500 flex-wrap">
          <span><b class="text-gray-800">{{ stats.total }}</b> người dùng</span>
          <span class="text-gray-300">·</span>
          <span><b class="text-primary">{{ stats.admins }}</b> admin</span>
          <span class="text-gray-300">·</span>
          <span><b class="text-emerald-600">{{ stats.active }}</b> hoạt động</span>
        </div>
      </div>
      <div class="flex items-center gap-2 ml-auto">
        <button
          type="button"
          class="press inline-flex items-center gap-2 h-10 px-4 rounded-full glass shadow-card text-sm font-semibold text-gray-700 hover:bg-gray-100 transition-colors"
          @click="reload(page)"
        >
          <i :class="['bi', loading ? 'bi-arrow-clockwise animate-spin' : 'bi-arrow-clockwise']" />
          Làm mới
        </button>
        <button
          type="button"
          class="press inline-flex items-center gap-1.5 h-10 px-4 rounded-full bg-primary text-white text-sm font-semibold shadow-pill hover:bg-primary-dark transition-colors"
          @click="openCreate"
        >
          <i class="bi bi-person-plus-fill" />
          Thêm người dùng
        </button>
      </div>
    </div>

    <!-- Toolbar: search + filter role/status -->
    <div class="flex items-center gap-2 flex-wrap flex-shrink-0">
      <div class="flex-1 min-w-[200px] relative">
        <i class="bi bi-search absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 text-sm" />
        <input
          v-model="searchDraft"
          type="text"
          placeholder="Tìm theo email, tên, mã NV…"
          class="w-full h-11 pl-9 pr-3 rounded-2xl bg-gray-50 border border-gray-200 text-sm focus:outline-none focus:border-primary"
          @input="onSearchInput"
          @keydown.enter="reload(1)"
        />
      </div>
      <UiDropdown
        v-model="filters.role"
        :options="roleFilterOptions"
        placeholder="Mọi vai trò"
        class="w-44"
        @update:model-value="reload(1)"
      />
      <UiDropdown
        v-model="filters.active"
        :options="statusFilterOptions"
        placeholder="Mọi trạng thái"
        class="w-44"
        @update:model-value="reload(1)"
      />
    </div>

    <!-- Table area — CHỖ DUY NHẤT cuộn -->
    <div class="flex-1 min-h-0 overflow-y-auto bg-gray-50 rounded-3xl border border-gray-200/60 shadow-card">
      <div v-if="loading" class="text-center py-16 text-xs text-gray-400 italic">Đang tải danh sách…</div>
      <div v-else-if="!items.length" class="text-center py-16 text-sm text-gray-400">
        <i class="bi bi-people text-4xl text-gray-300 mb-2 block" />
        {{ searchDraft || filters.role || filters.active ? 'Không tìm thấy người dùng phù hợp' : 'Chưa có người dùng nào' }}
      </div>
      <table v-else class="users-table w-full">
        <thead>
          <tr>
            <th class="ut-th text-left">Người dùng</th>
            <th class="ut-th text-left whitespace-nowrap">Vai trò</th>
            <th class="ut-th text-left whitespace-nowrap">Phòng ban / Chức danh</th>
            <th class="ut-th text-left whitespace-nowrap">Mã NV</th>
            <th class="ut-th text-center whitespace-nowrap">Trạng thái</th>
            <th class="ut-th text-right whitespace-nowrap">Đăng nhập cuối</th>
            <th class="ut-th-actions" />
          </tr>
        </thead>
        <tbody>
          <tr v-for="u in items" :key="u.id" class="ut-row">
            <!-- Người dùng: avatar + tên + email -->
            <td class="ut-cell">
              <div class="flex items-center gap-3 min-w-0">
                <span class="ut-avatar" :style="avatarStyle(u)">
                  <img v-if="u.avatarUrl" :src="u.avatarUrl" alt="" class="h-full w-full object-cover rounded-[10px]" referrerpolicy="no-referrer" />
                  <template v-else>{{ initials(u) }}</template>
                </span>
                <div class="min-w-0">
                  <div class="flex items-center gap-1.5">
                    <span class="text-sm font-semibold text-gray-900 truncate">{{ u.fullName || '(chưa đặt tên)' }}</span>
                    <span v-if="u.id === currentUserId" class="ut-you">Bạn</span>
                  </div>
                  <div class="text-[0.72rem] text-gray-500 truncate font-mono">{{ u.email }}</div>
                </div>
              </div>
            </td>
            <!-- Vai trò -->
            <td class="ut-cell">
              <UiBadge :variant="u.role === 'admin' ? 'primary' : 'neutral'" size="sm" dot>
                {{ u.role === 'admin' ? 'Admin' : 'Nhân viên' }}
              </UiBadge>
            </td>
            <!-- Phòng ban / chức danh -->
            <td class="ut-cell">
              <div class="text-sm text-gray-700 truncate">{{ u.department || '—' }}</div>
              <div v-if="u.title" class="text-[0.72rem] text-gray-400 truncate">{{ u.title }}</div>
            </td>
            <!-- Mã NV -->
            <td class="ut-cell">
              <span class="text-xs font-mono text-gray-500">{{ u.hrCode || '—' }}</span>
            </td>
            <!-- Trạng thái: bấm để bật/khoá nhanh -->
            <td class="ut-cell text-center">
              <button
                type="button"
                :disabled="u.id === currentUserId || togglingId === u.id"
                :class="cn(
                  'ut-status press',
                  u.isActive ? 'ut-status-on' : 'ut-status-off',
                  (u.id === currentUserId) && 'opacity-60 cursor-not-allowed'
                )"
                :title="u.id === currentUserId ? 'Không thể đổi trạng thái của chính bạn' : (u.isActive ? 'Bấm để khoá' : 'Bấm để mở khoá')"
                @click="toggleActive(u)"
              >
                <span :class="['ut-status-dot', u.isActive ? 'bg-emerald-500' : 'bg-gray-400']" />
                {{ u.isActive ? 'Hoạt động' : 'Đã khoá' }}
              </button>
            </td>
            <!-- Đăng nhập cuối -->
            <td class="ut-cell ut-date">{{ u.lastLoginAt ? formatRelative(u.lastLoginAt) : 'chưa từng' }}</td>
            <!-- Hành động -->
            <td class="ut-cell-actions">
              <div class="inline-flex items-center gap-0.5">
                <button type="button" class="ut-action" title="Sửa" @click="openEdit(u)">
                  <i class="bi bi-pencil" />
                </button>
                <button
                  type="button"
                  class="ut-action ut-action-danger"
                  :disabled="u.id === currentUserId"
                  :title="u.id === currentUserId ? 'Không thể tự xoá chính bạn' : 'Xoá người dùng'"
                  @click="removeUser(u)"
                >
                  <i class="bi bi-trash" />
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Pagination -->
    <div v-if="total > 0" class="flex items-center justify-between gap-3 flex-wrap text-xs flex-shrink-0">
      <span class="text-gray-500">
        {{ (page - 1) * limit + 1 }}–{{ Math.min(page * limit, total) }} / {{ total }} người dùng
      </span>
      <div v-if="totalPages > 1" class="flex items-center gap-1">
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

    <!-- #region ALD 01/06/2026 - SidePanel tạo / sửa user -->
    <UiSidePanel
      v-model="panelOpen"
      :title="editing ? 'Sửa người dùng' : 'Thêm người dùng'"
      :subtitle="editing ? form.email : 'Người dùng đăng nhập bằng mã OTP gửi tới email'"
    >
      <div class="space-y-4">
        <div>
          <label class="text-xs font-semibold text-gray-700">Email <span class="text-rose-500">*</span></label>
          <UiInput
            v-model="form.email"
            type="email"
            placeholder="ten@pebsteel.com"
            class="mt-1.5"
            :disabled="editing"
          />
          <p v-if="editing" class="text-[0.7rem] text-gray-400 mt-1">Email là định danh đăng nhập — không sửa được sau khi tạo.</p>
        </div>

        <div>
          <label class="text-xs font-semibold text-gray-700">Họ và tên</label>
          <UiInput v-model="form.fullName" placeholder="Nguyễn Văn A" class="mt-1.5" />
        </div>

        <div>
          <label class="text-xs font-semibold text-gray-700">Vai trò</label>
          <UiDropdown
            v-model="form.role"
            :options="roleFormOptions"
            full-width
            no-clear
            class="mt-1.5"
            :disabled="editingSelf"
          />
          <p v-if="editingSelf" class="text-[0.7rem] text-gray-400 mt-1">Không thể tự hạ quyền admin của chính mình.</p>
        </div>

        <div class="grid grid-cols-2 gap-3">
          <div>
            <label class="text-xs font-semibold text-gray-700">Phòng ban</label>
            <UiInput v-model="form.department" placeholder="vd: Marketing" class="mt-1.5" />
          </div>
          <div>
            <label class="text-xs font-semibold text-gray-700">Chức danh</label>
            <UiInput v-model="form.title" placeholder="vd: Trưởng phòng" class="mt-1.5" />
          </div>
        </div>

        <div>
          <label class="text-xs font-semibold text-gray-700">Mã nhân viên</label>
          <UiInput v-model="form.hrCode" placeholder="vd: PEB-00123" class="mt-1.5" />
        </div>

        <!-- Trạng thái -->
        <div class="flex items-center justify-between gap-3 bg-gray-50 border border-gray-200/70 rounded-2xl px-4 py-3">
          <div class="min-w-0">
            <div class="text-sm font-semibold text-gray-800">Tài khoản hoạt động</div>
            <p class="text-[0.7rem] text-gray-500 mt-0.5">Tắt → user không gửi/nhận được OTP để đăng nhập.</p>
          </div>
          <button
            type="button"
            :disabled="editingSelf"
            :class="cn(
              'relative inline-flex h-6 w-11 flex-shrink-0 rounded-full transition-colors',
              form.isActive ? 'bg-emerald-500' : 'bg-gray-300',
              editingSelf && 'opacity-50 cursor-not-allowed'
            )"
            @click="!editingSelf && (form.isActive = !form.isActive)"
          >
            <span :class="cn('inline-block h-5 w-5 mt-0.5 rounded-full bg-gray-50 shadow transition-transform', form.isActive ? 'translate-x-5' : 'translate-x-0.5')" />
          </button>
        </div>
      </div>

      <template #footer>
        <UiButton variant="secondary" size="md" @click="panelOpen = false">Huỷ</UiButton>
        <UiButton variant="primary" size="md" :loading="saving" :disabled="!canSubmit" @click="submitForm">
          <i class="bi bi-check2" />
          {{ editing ? 'Lưu thay đổi' : 'Tạo người dùng' }}
        </UiButton>
      </template>
    </UiSidePanel>
    <!-- #endregion -->
  </div>
  <!-- #endregion -->
</template>

<script setup>
// #region ALD 01/06/2026 - Admin User CRUD (gọi motion-backend /admin/users qua beFetch)
const auth = useAuth()
const toast = useToast()
const confirm = useConfirm()

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

// User hiện tại (từ JWT) — để chặn tự xoá / tự khoá / tự hạ quyền ngay trên UI.
const currentUserId = computed(() => {
  try {
    return JSON.parse(atob((auth.token.value || '').split('.')[1] ?? ''))?.sub || ''
  } catch { return '' }
})

// ── State ────────────────────────────────────────────────────────────────────
const items = ref([])
const total = ref(0)
const page = ref(1)
const limit = 50
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / limit)))
const loading = ref(false)
const stats = ref({ total: 0, admins: 0, active: 0 })
const togglingId = ref('')

const searchDraft = ref('')
const filters = reactive({ role: '', active: '' })
const roleFilterOptions = [
  { value: 'admin', label: 'Admin' },
  { value: 'staff', label: 'Nhân viên' }
]
const statusFilterOptions = [
  { value: 'true', label: 'Hoạt động' },
  { value: 'false', label: 'Đã khoá' }
]
const roleFormOptions = [
  { value: 'staff', label: 'Nhân viên' },
  { value: 'admin', label: 'Quản trị viên (Admin)' }
]

// ── Form / panel ───────────────────────────────────────────────────────────────
const panelOpen = ref(false)
const saving = ref(false)
const form = reactive({ id: '', email: '', fullName: '', role: 'staff', department: '', title: '', hrCode: '', isActive: true })
const editing = computed(() => !!form.id)
const editingSelf = computed(() => editing.value && form.id === currentUserId.value)
const canSubmit = computed(() => editing.value || EMAIL_RE.test(form.email.trim()))

// ── Helpers ──────────────────────────────────────────────────────────────────
function initials(u) {
  const base = (u.fullName || u.email || '?').trim()
  const parts = base.split(/[\s@.]+/).filter(Boolean)
  return ((parts[0]?.[0] || '') + (parts[1]?.[0] || '')).toUpperCase() || base[0]?.toUpperCase() || '?'
}
// Màu avatar ổn định theo id (hash → hue) — đẹp + phân biệt user.
function avatarStyle(u) {
  let h = 0
  const s = u.id || u.email || ''
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360
  return { background: `linear-gradient(135deg, hsl(${h} 70% 55%), hsl(${(h + 40) % 360} 70% 45%))` }
}
function formatRelative(ts) {
  if (!ts) return ''
  const t = typeof ts === 'string' ? Date.parse(ts) : ts
  const diff = Date.now() - t
  const min = Math.floor(diff / 60000)
  if (min < 1) return 'vừa xong'
  if (min < 60) return `${min} phút trước`
  const hour = Math.floor(min / 60)
  if (hour < 24) return `${hour} giờ trước`
  const day = Math.floor(hour / 24)
  if (day < 7) return `${day} ngày trước`
  return new Date(t).toLocaleDateString('vi-VN')
}

// ── Load ───────────────────────────────────────────────────────────────────────
async function reload(newPage = page.value) {
  loading.value = true
  page.value = Math.max(1, newPage)
  try {
    const params = new URLSearchParams({ page: String(page.value), limit: String(limit) })
    if (searchDraft.value.trim()) params.set('q', searchDraft.value.trim())
    if (filters.role) params.set('role', filters.role)
    if (filters.active) params.set('active', filters.active)
    const res = await auth.beFetch(`/admin/users?${params.toString()}`)
    items.value = res.items || []
    total.value = res.total || 0
    if (res.stats) stats.value = res.stats
  } catch (err) {
    toast.error(err?.data?.error || 'Không tải được danh sách người dùng')
  } finally {
    loading.value = false
  }
}

let searchTimer = null
function onSearchInput() {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => reload(1), 300)
}

// ── Create / Edit ───────────────────────────────────────────────────────────────
function openCreate() {
  Object.assign(form, { id: '', email: '', fullName: '', role: 'staff', department: '', title: '', hrCode: '', isActive: true })
  panelOpen.value = true
}
function openEdit(u) {
  Object.assign(form, {
    id: u.id, email: u.email, fullName: u.fullName || '', role: u.role || 'staff',
    department: u.department || '', title: u.title || '', hrCode: u.hrCode || '', isActive: u.isActive !== false
  })
  panelOpen.value = true
}

async function submitForm() {
  if (saving.value || !canSubmit.value) return
  saving.value = true
  try {
    const body = {
      fullName: form.fullName.trim(),
      role: form.role,
      department: form.department.trim(),
      title: form.title.trim(),
      hrCode: form.hrCode.trim(),
      isActive: form.isActive
    }
    if (editing.value) {
      await auth.beFetch(`/admin/users/${form.id}`, { method: 'PUT', body })
      toast.success('Đã cập nhật người dùng')
    } else {
      await auth.beFetch('/admin/users', { method: 'POST', body: { ...body, email: form.email.trim().toLowerCase() } })
      toast.success('Đã tạo người dùng')
    }
    panelOpen.value = false
    reload(editing.value ? page.value : 1)
  } catch (err) {
    toast.error(err?.data?.error || 'Lưu thất bại')
  } finally {
    saving.value = false
  }
}

// ── Toggle active (quick) ───────────────────────────────────────────────────────
async function toggleActive(u) {
  if (u.id === currentUserId.value || togglingId.value) return
  togglingId.value = u.id
  try {
    await auth.beFetch(`/admin/users/${u.id}`, { method: 'PUT', body: { isActive: !u.isActive } })
    u.isActive = !u.isActive
    stats.value.active += u.isActive ? 1 : -1
    toast.success(u.isActive ? 'Đã mở khoá tài khoản' : 'Đã khoá tài khoản')
  } catch (err) {
    toast.error(err?.data?.error || 'Đổi trạng thái thất bại')
  } finally {
    togglingId.value = ''
  }
}

// ── Delete ──────────────────────────────────────────────────────────────────────
async function removeUser(u) {
  if (u.id === currentUserId.value) return
  const ok = await confirm.ask({
    title: 'Xoá người dùng?',
    message: `"${u.fullName || u.email}" sẽ bị xoá vĩnh viễn (kèm session + API key). Hành động không thể hoàn tác.`,
    confirmText: 'Xoá',
    variant: 'danger'
  })
  if (!ok) return
  try {
    await auth.beFetch(`/admin/users/${u.id}`, { method: 'DELETE' })
    toast.success('Đã xoá người dùng')
    reload(items.value.length === 1 && page.value > 1 ? page.value - 1 : page.value)
  } catch (err) {
    toast.error(err?.data?.error || 'Xoá thất bại')
  }
}

onMounted(() => reload(1))
// #endregion
</script>

<style scoped>
/* ALD 01/06/2026 - User table style (đồng bộ Apple-files-table của StorageManager) */
.users-table { border-collapse: collapse; }
.users-table thead {
  position: sticky; top: 0; z-index: 10;
  background: var(--glass-bg-solid);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
}
.ut-th {
  font-size: 11px; font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase;
  color: rgba(235,236,240,0.55); padding: 12px 14px; border-bottom: 0.5px solid rgba(235,236,240,0.1);
}
.ut-th-actions { width: 92px; padding: 12px; border-bottom: 0.5px solid rgba(235,236,240,0.1); }

.ut-row { transition: background 0.16s cubic-bezier(0.32, 0.72, 0, 1); }
.ut-row:hover { background: rgba(235,236,240,0.04); }
.ut-cell { padding: 10px 14px; vertical-align: middle; border-top: 0.5px solid rgba(235,236,240,0.06); }
.ut-cell-actions { padding: 10px 12px; vertical-align: middle; text-align: right; border-top: 0.5px solid rgba(235,236,240,0.06); white-space: nowrap; }

.ut-avatar {
  flex-shrink: 0; width: 38px; height: 38px;
  display: inline-flex; align-items: center; justify-content: center;
  border-radius: 11px; color: #fff; font-size: 13px; font-weight: 800; letter-spacing: 0.02em;
  box-shadow: 0 1px 3px rgba(0,0,0,0.12);
}
.ut-you {
  flex-shrink: 0; font-size: 9px; font-weight: 800; letter-spacing: 0.04em;
  padding: 1px 6px; border-radius: 999px; background: var(--color-primary, #2563eb); color: #fff;
}

.ut-date {
  text-align: right; font-size: 12px; color: rgba(235,236,240,0.55);
  white-space: nowrap; font-variant-numeric: tabular-nums;
}

.ut-status {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 4px 11px; font-size: 11px; font-weight: 700; border-radius: 999px;
  cursor: pointer; transition: all 0.16s; border: 1px solid transparent;
}
.ut-status-on  { background: rgba(16,185,129,0.10); color: #047857; border-color: rgba(16,185,129,0.2); }
.ut-status-off { background: rgba(235,236,240,0.07);  color: rgba(235,236,240,0.6); border-color: rgba(235,236,240,0.12); }
.ut-status:not(:disabled):hover { filter: brightness(0.96); }
.ut-status-dot { width: 7px; height: 7px; border-radius: 999px; flex-shrink: 0; }

.ut-action {
  width: 30px; height: 30px;
  display: inline-flex; align-items: center; justify-content: center;
  background: transparent; border: none; border-radius: 999px;
  color: rgba(235,236,240,0.45); cursor: pointer; font-size: 14px; transition: all 0.16s;
}
.ut-action:hover { background: rgba(37,99,235,0.08); color: var(--color-primary, #2563eb); }
.ut-action-danger:hover { background: rgba(255,59,48,0.12); color: #FF3B30; }
.ut-action:disabled { opacity: 0.35; cursor: not-allowed; }
.ut-action:disabled:hover { background: transparent; color: rgba(235,236,240,0.45); }
</style>
