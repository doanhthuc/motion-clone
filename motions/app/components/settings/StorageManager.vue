<template>
  <!-- #region ALD 06/07/2026 - Storage: Liquid Glass light redesign (bỏ dark + amber, dùng lq-* global). -->
  <div class="lq-panel flex h-full min-h-0 flex-col overflow-hidden">
    <!-- Header -->
    <div class="shrink-0 border-b border-white/[0.06] bg-white/[0.03]">
      <div class="px-4 py-3 sm:px-6">
        <div class="flex items-center justify-between gap-4">
          <div class="flex min-w-0 items-center gap-3">
            <span class="flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px] bg-primary-50 text-primary">
              <i class="bi bi-cloud-arrow-up text-lg" />
            </span>
            <div class="min-w-0">
              <h1 class="truncate text-base font-semibold text-gray-900 sm:text-lg">{{ scopeLabel }}</h1>
              <p class="hidden truncate text-xs text-gray-500 sm:block">File workflow, input và output motion</p>
            </div>
          </div>

          <div class="flex items-center gap-2">
            <div class="hidden items-center rounded-[10px] bg-white/[0.05] p-1 sm:flex">
              <button
                type="button"
                :class="['px-3 py-1.5 rounded-lg text-sm transition-all', viewMode === 'grid' ? 'bg-gray-50 text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700']"
                title="Grid"
                @click="viewMode = 'grid'"
              >
                <i class="bi bi-grid-fill" />
              </button>
              <button
                type="button"
                :class="['px-3 py-1.5 rounded-lg text-sm transition-all', viewMode === 'list' ? 'bg-gray-50 text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700']"
                title="List"
                @click="viewMode = 'list'"
              >
                <i class="bi bi-list-ul" />
              </button>
            </div>
            <button
              type="button"
              :disabled="cleaning"
              class="lq-btn hover:!border-rose-200 hover:!bg-rose-50 hover:!text-rose-600"
              @click="onBulkDeleteOld"
            >
              <i :class="['bi', cleaning ? 'bi-arrow-clockwise animate-spin' : 'bi-trash3']" />
              <span class="hidden sm:inline">Xoá file cũ</span>
            </button>
          </div>
        </div>

        <div class="mt-3 grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto]">
          <div class="flex flex-wrap items-center gap-2 text-xs text-gray-500">
            <span class="lq-chip !rounded-lg !py-1.5">
              <i class="bi bi-hdd-network" />
              {{ formatBytes(stats.totalSize || 0) }}<span v-if="stats.quota"> / {{ formatBytes(stats.quota) }}</span>
            </span>
            <span class="lq-chip !rounded-lg !py-1.5">
              <i class="bi bi-files" />
              {{ stats.fileCount || 0 }} file
            </span>
            <span class="lq-chip !rounded-lg !py-1.5">
              <i class="bi bi-funnel" />
              {{ total }} đang hiển thị
            </span>
          </div>

          <div v-if="isAdmin" class="flex flex-wrap items-center gap-2 lg:justify-end">
            <div class="flex rounded-[10px] bg-white/[0.05] p-1">
              <button
                v-for="m in scopeModes"
                :key="m.id"
                type="button"
                :class="[
                  'inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-semibold uppercase tracking-wide transition-all',
                  scopeMode === m.id ? 'bg-gray-50 text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700'
                ]"
                @click="onScopeChange(m.id)"
              >
                <i :class="['bi', m.icon]" />
                {{ m.label }}
              </button>
            </div>
            <button
              v-if="scopeMode === 'all' && selectedUserId"
              type="button"
              class="lq-btn lq-btn--tint-blue min-w-0"
              @click="selectedUserId = ''"
            >
              <i class="bi bi-arrow-left" />
              <span class="truncate">{{ selectedUserEmail || selectedUserId }}</span>
            </button>
          </div>
        </div>

        <div v-if="!(isAdmin && scopeMode === 'all' && !selectedUserId)" class="mt-3 flex flex-col gap-2 sm:flex-row">
          <div class="lq-search flex-1">
            <i class="bi bi-search" />
            <input
              v-model="searchDraft"
              type="text"
              placeholder="Tìm theo tên file..."
              @keydown.enter="applySearch"
              @input="onSearchInput"
            />
          </div>
          <div class="grid grid-cols-2 gap-2 sm:flex sm:shrink-0">
            <SocialDropdown v-model="filters.bucket" :options="bucketOptions" placeholder="Tất cả nguồn" class="min-w-0 sm:w-44" @update:model-value="reload(1)" />
            <SocialDropdown v-model="filters.ext" :options="extOptions" placeholder="Tất cả loại" class="min-w-0 sm:w-36" @update:model-value="reload(1)" />
            <SocialDropdown v-model="filters.sort" :options="sortOptions" class="col-span-2 min-w-0 sm:w-44" @update:model-value="onSortChange" />
            <SocialDropdown
              :model-value="String(limit)"
              :options="limitOptions.map((n) => ({ value: String(n), label: `${n} file` }))"
              class="col-span-2 sm:w-28"
              @update:model-value="(v) => { limit = Number(v); reload(1) }"
            />
          </div>
          <div class="flex rounded-[10px] bg-white/[0.05] p-1 sm:hidden">
            <button type="button" :class="['flex-1 rounded-lg py-2 text-sm transition-all', viewMode === 'grid' ? 'bg-gray-50 text-gray-900 shadow-sm' : 'text-gray-500']" @click="viewMode = 'grid'">
              <i class="bi bi-grid-fill" />
            </button>
            <button type="button" :class="['flex-1 rounded-lg py-2 text-sm transition-all', viewMode === 'list' ? 'bg-gray-50 text-gray-900 shadow-sm' : 'text-gray-500']" @click="viewMode = 'list'">
              <i class="bi bi-list-ul" />
            </button>
          </div>
        </div>

        <div v-if="selectedPaths.size > 0" class="mt-3 flex items-center justify-between gap-3 rounded-[10px] border border-blue-200 bg-blue-50 px-3 py-1.5">
          <span class="flex items-center gap-2 text-[13px] font-semibold text-blue-700 min-w-0 truncate">
            <i class="bi bi-check-circle-fill" />
            Đã chọn {{ selectedPaths.size }} file
          </span>
          <div class="flex items-center gap-1.5 flex-shrink-0">
            <button
              type="button"
              class="lq-btn lq-btn--tint-rose !h-8 !text-xs"
              @click="onDeleteSelected"
            >
              <i class="bi bi-trash" />
              Xoá
            </button>
            <button
              type="button"
              class="lq-btn lq-btn--ghost !h-8 !text-xs"
              @click="clearSelection"
            >
              Huỷ
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Content -->
    <div class="flex min-h-0 flex-1 flex-col overflow-hidden px-4 py-4 sm:px-6">
      <div v-if="isAdmin && scopeMode === 'all' && !selectedUserId" class="min-h-0 flex-1 overflow-y-auto">
        <div v-if="loadingUsers" class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          <div v-for="n in 8" :key="n" class="h-24 animate-pulse rounded-xl bg-white/[0.05]" />
        </div>
        <div v-else-if="!userFolders.length" class="flex min-h-[420px] flex-col items-center justify-center text-center">
          <div class="mb-5 flex h-20 w-20 items-center justify-center rounded-full bg-blue-50 text-4xl text-blue-500">
            <i class="bi bi-folder2-open" />
          </div>
          <h3 class="text-xl font-semibold text-gray-900">Chưa user nào upload file</h3>
          <p class="mt-2 max-w-sm text-sm text-gray-500">Khi user có file trong Storage, thư mục sẽ hiện ở đây.</p>
        </div>
        <div v-else class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          <button
            v-for="u in userFolders"
            :key="u.userId"
            type="button"
            class="group lq-card p-4 text-left transition-all hover:border-white/[0.14] hover:shadow-card-hover"
            @click="onSelectUser(u)"
          >
            <div class="flex items-center gap-3">
              <span class="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-primary-50 text-primary">
                <i class="bi bi-folder-fill text-xl" />
              </span>
              <div class="min-w-0 flex-1">
                <div class="truncate text-sm font-semibold text-gray-900">{{ u.email }}</div>
                <div class="mt-1 font-mono text-xs text-gray-500">{{ u.fileCount }} file · {{ formatBytes(u.totalSize) }}</div>
              </div>
              <i class="bi bi-chevron-right text-gray-300 transition-transform group-hover:translate-x-0.5 group-hover:text-primary" />
            </div>
          </button>
        </div>
      </div>

      <template v-else>
        <div class="min-h-0 flex-1 overflow-y-auto pb-3">
          <div v-if="loading" :class="viewMode === 'grid' ? 'grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6' : 'space-y-2'">
            <div v-for="n in 12" :key="n" :class="viewMode === 'grid' ? 'aspect-square rounded-xl bg-white/[0.05]' : 'h-16 rounded-xl bg-white/[0.05]'" class="animate-pulse" />
          </div>

          <div v-else-if="!items.length" class="flex min-h-[360px] flex-col items-center justify-center text-center">
            <div class="mb-4 flex h-14 w-14 items-center justify-center rounded-xl bg-white/[0.04] text-2xl text-gray-400">
              <i class="bi bi-cloud" />
            </div>
            <h3 class="text-base font-semibold text-gray-900">{{ searchDraft || filters.ext ? 'Không tìm thấy file phù hợp' : 'Chưa có file nào' }}</h3>
            <p class="mt-2 max-w-sm text-sm text-gray-500">File upload từ workflow, input và output motion sẽ xuất hiện tại đây.</p>
          </div>

          <div v-else-if="viewMode === 'grid'" class="grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6">
            <div
              v-for="f in items"
              :key="f.id"
              :class="[
                'group relative flex aspect-square cursor-pointer flex-col overflow-hidden rounded-xl border bg-gray-50 p-3 transition-all hover:border-white/[0.14] hover:shadow-card-hover',
                selectedPaths.has(f.name) ? 'border-primary ring-2 ring-[rgba(94,106,210,0.25)]' : 'border-white/[0.07]'
              ]"
              @click="openPreview(f)"
            >
            <button
              type="button"
              :class="[
                'absolute left-3 top-3 z-10 flex h-6 w-6 items-center justify-center rounded-full border shadow-sm transition-opacity',
                selectedPaths.has(f.name)
                  ? 'border-primary bg-primary text-white opacity-100'
                  : 'border-white/[0.12] bg-gray-100/95 opacity-0 group-hover:opacity-100'
              ]"
              @click.stop="toggleOne(f.name)"
            >
              <i v-if="selectedPaths.has(f.name)" class="bi bi-check" />
            </button>
            <div class="relative flex flex-1 items-center justify-center overflow-hidden rounded-lg bg-gray-100">
              <video
                v-if="isVideoFile(f) && thumbnailUrls[f.name]"
                :src="thumbnailUrls[f.name]"
                muted
                playsinline
                preload="metadata"
                class="h-full w-full object-cover"
              />
              <img
                v-else-if="isImageFile(f) && thumbnailUrls[f.name]"
                :src="thumbnailUrls[f.name]"
                alt=""
                loading="lazy"
                class="h-full w-full object-cover"
              />
              <span v-else :class="['afp-icon-tile is-large', `afp-tile-${f.kind || 'file'}`]">
                <i :class="['bi', fileIcon(f)]" />
              </span>
              <span v-if="isVideoFile(f)" class="absolute bottom-2 left-2 inline-flex items-center gap-1 rounded-md bg-black/60 px-1.5 py-1 text-[10px] font-semibold text-white">
                <i class="bi bi-play-fill" />
                Video
              </span>
              <!-- Hover actions — chỉ phủ vùng thumbnail, không che tên file -->
              <div class="absolute inset-0 flex items-center justify-center gap-2 bg-black/30 opacity-0 transition-opacity group-hover:opacity-100">
                <button type="button" class="grid h-8 w-8 place-items-center rounded-full bg-gray-100/95 text-gray-700 shadow-sm hover:text-primary" title="Copy URL" @click.stop="onCopyUrl(f)">
                  <i :class="['bi', copiedPath === f.name ? 'bi-check2 text-emerald-500' : (copyingPath === f.name ? 'bi-hourglass-split animate-pulse' : 'bi-link-45deg')]" />
                </button>
                <button type="button" class="grid h-8 w-8 place-items-center rounded-full bg-gray-100/95 text-gray-700 shadow-sm hover:text-rose-600" title="Xoá file" @click.stop="onDeleteOne(f)">
                  <i class="bi bi-trash" />
                </button>
              </div>
            </div>
            <div class="mt-3 min-w-0">
              <p class="truncate text-xs font-semibold text-gray-900" :title="displayName(f)">{{ displayName(f) }}</p>
              <div class="mt-1 flex items-center justify-between gap-2 text-[10px] text-gray-500">
                <span>{{ formatBytes(f.size) }}</span>
                <span>{{ formatDate(f.createdAt) }}</span>
              </div>
            </div>
          </div>
        </div>

          <div v-else class="lq-card overflow-hidden">
            <div class="hidden grid-cols-[44px_minmax(0,1fr)_140px_120px_120px_92px] border-b border-white/[0.06] bg-white/[0.02] px-4 py-3 lq-th lg:grid">
            <div>
              <input type="checkbox" :checked="allSelected" :indeterminate.prop="someSelected" class="lq-check" @change="toggleAll" />
            </div>
            <div>Tên file</div>
            <div>Loại</div>
            <div class="text-right">Dung lượng</div>
            <div class="text-right">Ngày tải</div>
            <div />
          </div>
          <div class="divide-y divide-white/[0.05]">
            <div
              v-for="f in items"
              :key="f.id"
              class="group grid grid-cols-[32px_minmax(0,1fr)_auto] items-center gap-3 px-4 py-3 transition-colors hover:bg-white/[0.025] lg:grid-cols-[44px_minmax(0,1fr)_140px_120px_120px_92px]"
            >
              <input type="checkbox" :checked="selectedPaths.has(f.name)" class="lq-check" @change="toggleOne(f.name)" />
              <div class="flex min-w-0 items-center gap-3">
                <span v-if="isVideoFile(f) && thumbnailUrls[f.name]" class="relative h-10 w-10 shrink-0 overflow-hidden rounded-lg bg-gray-100">
                  <video :src="thumbnailUrls[f.name]" muted playsinline preload="metadata" class="h-full w-full object-cover" />
                  <span class="absolute inset-0 grid place-items-center bg-black/10 text-white">
                    <i class="bi bi-play-fill" />
                  </span>
                </span>
                <img
                  v-else-if="isImageFile(f) && thumbnailUrls[f.name]"
                  :src="thumbnailUrls[f.name]"
                  alt=""
                  loading="lazy"
                  class="h-10 w-10 shrink-0 rounded-lg object-cover"
                />
                <span v-else :class="['afp-icon-tile', `afp-tile-${f.kind || 'file'}`]">
                  <i :class="['bi', fileIcon(f)]" />
                </span>
                <button type="button" class="min-w-0 truncate text-left text-sm font-semibold text-gray-900 transition-colors hover:text-primary" :title="displayName(f)" @click="openPreview(f)">
                  {{ displayName(f) }}
                </button>
              </div>
              <div class="hidden lg:block">
                <span :class="cn('afp-kind-badge', kindBadge(f.kind))">
                  <i :class="['bi', kindIcon(f.kind)]" />
                  {{ kindLabel(f.kind) }}
                </span>
              </div>
              <div class="hidden text-right font-mono text-xs text-gray-500 lg:block">{{ formatBytes(f.size) }}</div>
              <div class="hidden text-right text-xs text-gray-500 lg:block">{{ formatDate(f.createdAt) }}</div>
              <div class="flex items-center justify-end gap-1">
                <button type="button" :disabled="copyingPath === f.name" class="afp-action" :title="copiedPath === f.name ? 'Đã copy!' : 'Copy URL (signed 1h)'" @click="onCopyUrl(f)">
                  <i :class="['bi', copiedPath === f.name ? 'bi-check2 text-emerald-500' : (copyingPath === f.name ? 'bi-hourglass-split animate-pulse' : 'bi-link-45deg')]" />
                </button>
                <button type="button" class="afp-action afp-action-danger" title="Xoá file" @click="onDeleteOne(f)">
                  <i class="bi bi-trash" />
                </button>
              </div>
              <div class="col-span-3 flex items-center gap-2 pl-11 text-xs text-gray-500 lg:hidden">
                <span :class="cn('afp-kind-badge', kindBadge(f.kind))">{{ kindLabel(f.kind) }}</span>
                <span>{{ formatBytes(f.size) }}</span>
                <span>{{ formatDate(f.createdAt) }}</span>
              </div>
            </div>
          </div>
        </div>

        </div>

        <div v-if="total > 0" class="flex shrink-0 items-center justify-between gap-4 border-t border-white/[0.06] pt-3">
          <div class="text-xs text-gray-500 sm:text-sm">
            <span class="sm:hidden">{{ total }} file</span>
            <span class="hidden sm:inline">Hiển thị {{ (page - 1) * limit + 1 }}-{{ Math.min(page * limit, total) }} trong số {{ total }} file</span>
          </div>
          <div class="flex items-center gap-2">
            <button
              type="button"
              :disabled="page <= 1 || loading"
              class="lq-btn !h-9 sm:!px-3 max-sm:!w-9 max-sm:!px-0"
              @click="reload(page - 1)"
            >
              <span class="hidden sm:inline">Trước</span>
              <i class="bi bi-chevron-left sm:hidden" />
            </button>
            <div class="rounded-[10px] border border-[rgba(94,106,210,0.25)] bg-primary-50 px-3 py-2 text-sm font-semibold text-primary">
              {{ page }} <span class="sm:hidden">/ {{ totalPages }}</span>
            </div>
            <button
              type="button"
              :disabled="page >= totalPages || loading"
              class="lq-btn !h-9 sm:!px-3 max-sm:!w-9 max-sm:!px-0"
              @click="reload(page + 1)"
            >
              <span class="hidden sm:inline">Sau</span>
              <i class="bi bi-chevron-right sm:hidden" />
            </button>
          </div>
        </div>
      </template>
    </div>

    <!-- #region ALD 21/05/2026 - Modal lightbox preview (giống index.vue) -->
    <Teleport to="body">
      <Transition
        enter-active-class="transition-opacity duration-200"
        leave-active-class="transition-opacity duration-150"
        enter-from-class="opacity-0"
        leave-to-class="opacity-0"
      >
        <div
          v-if="previewItem"
          class="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4"
          @click.self="previewItem = null"
        >
          <button
            type="button"
            class="absolute top-4 right-4 h-10 w-10 flex items-center justify-center rounded-full bg-white/10 text-white hover:bg-white/20"
            @click="previewItem = null"
          >
            <i class="bi bi-x-lg text-lg" />
          </button>
          <div class="w-full max-w-5xl h-full max-h-[90vh] bg-[#111] rounded-2xl overflow-hidden shadow-2xl">
            <object
              v-if="previewItem.mime?.startsWith('application/pdf') && previewSignedUrl"
              :data="previewSignedUrl"
              type="application/pdf"
              class="w-full h-full"
            >
              <iframe :src="previewSignedUrl" class="w-full h-full border-0" />
            </object>
            <div v-else-if="(previewItem.mime?.startsWith('image') || /\.(png|jpe?g|webp|gif|bmp|tiff)$/i.test(previewItem.name)) && previewSignedUrl" class="w-full h-full flex items-center justify-center p-4 bg-white/[0.03]">
              <img :src="previewSignedUrl" class="max-w-full max-h-full object-contain rounded-xl" />
            </div>
            <div v-else-if="(previewItem.mime?.startsWith('video') || /\.(mp4|mov|webm|mkv|m4v)$/i.test(previewItem.name)) && previewSignedUrl" class="w-full h-full flex items-center justify-center p-4 bg-black">
              <video :src="previewSignedUrl" controls autoplay class="max-w-full max-h-full rounded-xl" />
            </div>
            <div v-else class="w-full h-full flex items-center justify-center text-white/30 text-sm">
              <i class="bi bi-arrow-clockwise animate-spin text-2xl mr-2" /> Đang tải…
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>
    <!-- #endregion -->
  </div>
</template>

<script setup>
// #region ALD 21/05/2026 - Storage Manager: list/preview/delete attachments
// Props/api inherit từ useStorageFiles composable.
// Admin: switch self ↔ all (folder view per-user)
// User: tự động scoped vào userId của mình.

const props = defineProps({
  isAdmin: { type: Boolean, default: false }
})

const storage = useStorageFiles()
const toast = useToast()
const confirm = useConfirm()

// ── State ──────────────────────────────────────────────────────────
const items = ref([])
const total = ref(0)
const page = ref(1)
const limit = ref(20)
const limitOptions = [20, 50, 100, 200]
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / limit.value)))
const loading = ref(false)
const stats = ref({ totalSize: 0, fileCount: 0, quota: null })
const cleaning = ref(false)
const thumbnailUrls = ref({})
let thumbnailBatch = 0

// Admin scope state
const scopeMode = ref('self')  // 'self' | 'all'
const scopeModes = [
  { id: 'self', label: 'File của tôi', icon: 'bi-person' },
  { id: 'all', label: 'Tất cả users', icon: 'bi-folder2-open' }
]
const userFolders = ref([])
const loadingUsers = ref(false)
const selectedUserId = ref('')
const selectedUserEmail = ref('')

// Search + filter
const searchDraft = ref('')
const filters = reactive({
  bucket: '',                   // '' = cả 2 bucket; 'chat-attachments' | 'motion-jobs'
  ext: '',
  sort: 'createdAt-desc'        // dropdown value packs sortBy+sortDir
})
const viewMode = ref('list')
const bucketOptions = [
  { value: '', label: 'Tất cả nguồn' },
  { value: 'chat-attachments', label: 'Chat / OCR' },
  { value: 'motion-jobs', label: 'Motion Transfer' }
]
const extOptions = [
  { value: '', label: 'Tất cả loại' },
  { value: 'pdf', label: 'PDF' },
  { value: 'image', label: 'Ảnh' },
  { value: 'video', label: 'Video' }
]
const sortOptions = [
  { value: 'createdAt-desc', label: 'Mới nhất' },
  { value: 'createdAt-asc', label: 'Cũ nhất' },
  { value: 'name-asc', label: 'Tên A→Z' },
  { value: 'name-desc', label: 'Tên Z→A' },
  { value: 'size-desc', label: 'Lớn nhất' },
  { value: 'size-asc', label: 'Nhỏ nhất' }
]

// Bulk selection
const selectedPaths = ref(new Set())
const allSelected = computed(() => items.value.length > 0 && items.value.every((f) => selectedPaths.value.has(f.name)))
const someSelected = computed(() => items.value.some((f) => selectedPaths.value.has(f.name)) && !allSelected.value)

// Preview
const previewItem = ref(null)
const previewSignedUrl = ref('')

// Copy URL state — track per-file (path) cho icon feedback
const copyingPath = ref('')
const copiedPath = ref('')

// ── Computed ───────────────────────────────────────────────────────
const scopeLabel = computed(() => {
  if (!props.isAdmin) return 'Storage của bạn'
  if (scopeMode.value === 'all' && !selectedUserId.value) return 'Toàn bộ Storage'
  if (scopeMode.value === 'all' && selectedUserId.value) return selectedUserEmail.value
  return 'Storage của bạn'
})
const quotaPercent = computed(() => {
  if (!stats.value.quota) return 0
  return (stats.value.totalSize / stats.value.quota) * 100
})

// ── Helpers ────────────────────────────────────────────────────────
function formatBytes(bytes) {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  let v = bytes
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`
}
function formatDate(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  const now = new Date()
  const sameDay = d.toDateString() === now.toDateString()
  if (sameDay) return d.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' })
  return d.toLocaleDateString('vi-VN', { day: '2-digit', month: '2-digit', year: '2-digit' })
}
function displayName(f) {
  // ALD 31/05/2026 - Ưu tiên tên gốc (filename) nếu BE trả về (output đều có basename 'output.ext').
  if (f.filename) return f.filename
  // Path: <userId>/<jobId>-<safeName> → trim prefix nếu có
  const parts = (f.name || '').split('/')
  const last = parts[parts.length - 1] || f.name
  // Strip jobId-uuid prefix nếu detect được (36-char uuid + dash)
  const stripped = last.replace(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}-/, '')
  return stripped || last
}

// ALD 23/05/2026 - phân loại file theo bucket+kind cho UI badge/icon
function fileIcon(f) {
  if (f.mime?.startsWith('image')) return 'bi-image text-amber-600'
  if (f.mime?.startsWith('video') || f.kind === 'motion-video' || f.kind === 'motion-output') {
    return f.kind === 'motion-output' ? 'bi-film text-emerald-600' : 'bi-film text-rose-500'
  }
  if (f.mime?.startsWith('application/pdf') || f.name.endsWith('.pdf')) return 'bi-file-earmark-pdf text-rose-500'
  return 'bi-file-earmark text-gray-400'
}
function isImageFile(f) {
  return Boolean(f?.mime?.startsWith('image') || /\.(png|jpe?g|webp|gif|bmp|tiff|avif)$/i.test(f?.name || ''))
}
function isVideoFile(f) {
  return Boolean(f?.mime?.startsWith('video') || f?.kind === 'motion-video' || f?.kind === 'motion-output' || /\.(mp4|mov|webm|mkv|m4v)$/i.test(f?.name || ''))
}
function canPreviewInline(f) {
  return isImageFile(f) || isVideoFile(f)
}
function kindLabel(k) {
  return {
    'chat': 'Chat',
    'motion-ref': 'Ref',
    'motion-video': 'Motion',
    'motion-output': 'Output',
    'motion-other': 'Motion'
  }[k] || k || '—'
}
function kindIcon(k) {
  return {
    'chat': 'bi-chat-square-text',
    'motion-ref': 'bi-person',
    'motion-video': 'bi-film',
    'motion-output': 'bi-stars',
    'motion-other': 'bi-folder'
  }[k] || 'bi-tag'
}
function kindBadge(k) {
  return {
    'chat': 'bg-blue-50 text-blue-700',
    'motion-ref': 'bg-amber-50 text-amber-700',
    'motion-video': 'bg-rose-50 text-rose-600',
    'motion-output': 'bg-emerald-50 text-emerald-700',
    'motion-other': 'bg-gray-100 text-gray-600'
  }[k] || 'bg-gray-100 text-gray-600'
}

// ── API loaders ────────────────────────────────────────────────────
async function loadStats() {
  try {
    const all = props.isAdmin && scopeMode.value === 'all' && !selectedUserId.value
    const data = await storage.getStats({ all, bucket: filters.bucket })
    stats.value = data
  } catch (err) {
    console.warn('[storage] stats failed:', err)
  }
}

async function loadUsers() {
  loadingUsers.value = true
  try {
    // BE /storage-files/users trả { users:[{userId,email,count,size}] } → map đúng shape FE (fileCount/totalSize).
    const data = await storage.listUsers()
    userFolders.value = (data.users || data.items || []).map((u) => ({
      userId: u.userId,
      email: u.email || '(chưa gán user)',
      fileCount: u.count ?? u.fileCount ?? 0,
      totalSize: u.size ?? u.totalSize ?? 0
    }))
  } catch (err) {
    toast.error('Không tải được danh sách users: ' + (err.data?.error || err.message))
  } finally {
    loadingUsers.value = false
  }
}

async function reload(newPage = page.value) {
  loading.value = true
  page.value = newPage
  thumbnailUrls.value = {}
  try {
    const [sortBy, sortDir] = filters.sort.split('-')
    const userIdParam = (props.isAdmin && scopeMode.value === 'all' && selectedUserId.value) ? selectedUserId.value : ''
    const data = await storage.listFiles({
      page: newPage,
      limit: limit.value,
      q: searchDraft.value.trim(),
      ext: filters.ext,
      sortBy,
      sortDir,
      userId: userIdParam,
      bucket: filters.bucket
    })
    items.value = data.items || []
    total.value = data.total || 0
    selectedPaths.value = new Set()  // reset selection on page change
    loadInlinePreviews(items.value)
  } catch (err) {
    toast.error('Không tải được file: ' + (err.data?.error || err.message))
  } finally {
    loading.value = false
  }
}

async function loadInlinePreviews(files) {
  const batch = ++thumbnailBatch
  const previewItems = (files || []).filter(canPreviewInline)
  if (!previewItems.length) return
  const entries = await Promise.all(previewItems.map(async (f) => {
    try {
      const data = await storage.getSignedUrl(f.name, { bucket: f.bucket })
      return [f.name, data?.signedUrl || data?.signed_url || '']
    } catch (err) {
      console.warn('[storage] preview url failed:', f.name, err)
      return [f.name, '']
    }
  }))
  if (batch !== thumbnailBatch) return
  const next = {}
  for (const [name, url] of entries) {
    if (url) next[name] = url
  }
  thumbnailUrls.value = next
}

// ── Handlers ───────────────────────────────────────────────────────
function onScopeChange(mode) {
  scopeMode.value = mode
  selectedUserId.value = ''
  selectedUserEmail.value = ''
  if (mode === 'all') {
    loadUsers()
    loadStats()
  } else {
    reload(1)
    loadStats()
  }
}
function onSelectUser(u) {
  selectedUserId.value = u.userId
  selectedUserEmail.value = u.email
  reload(1)
}

let searchTimer = null
function onSearchInput() {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => reload(1), 300)
}
function applySearch() { reload(1) }
function onSortChange() { reload(1) }

function toggleOne(path) {
  const next = new Set(selectedPaths.value)
  if (next.has(path)) next.delete(path)
  else next.add(path)
  selectedPaths.value = next
}
function toggleAll(e) {
  const next = new Set(selectedPaths.value)
  if (e.target.checked) {
    for (const f of items.value) next.add(f.name)
  } else {
    for (const f of items.value) next.delete(f.name)
  }
  selectedPaths.value = next
}
function clearSelection() {
  selectedPaths.value = new Set()
}

async function onDeleteOne(f) {
  const ok = await confirm.ask({
    title: 'Xoá file?',
    message: `"${displayName(f)}" sẽ bị xoá vĩnh viễn khỏi Storage.`,
    confirmText: 'Xoá',
    loadingText: 'Đang xoá...',
    variant: 'danger',
    async onConfirm() {
      await storage.deleteFiles([{ path: f.name, bucket: f.bucket }])
    }
  })
  if (!ok) return
  toast.success('Đã xoá file')
  reload(items.value.length === 1 && page.value > 1 ? page.value - 1 : page.value)
  loadStats()
}

async function onDeleteSelected() {
  const paths = Array.from(selectedPaths.value)
  if (!paths.length) return
  const bucketByPath = new Map(items.value.map((it) => [it.name, it.bucket]))
  const itemsPayload = paths.map((p) => ({ path: p, bucket: bucketByPath.get(p) || 'chat-attachments' }))
  const ok = await confirm.ask({
    title: `Xoá ${paths.length} file?`,
    message: 'File sẽ bị xoá vĩnh viễn khỏi Storage.',
    confirmText: 'Xoá tất cả',
    loadingText: 'Đang xoá...',
    variant: 'danger',
    async onConfirm() {
      await storage.deleteFiles(itemsPayload)
    }
  })
  if (!ok) return
  toast.success(`Đã xoá ${paths.length} file`)
  reload(1)
  loadStats()
}

async function onBulkDeleteOld() {
  const result = await confirm.ask({
    title: 'Xoá file cũ hơn 30 ngày?',
    message: 'Tất cả file trong scope hiện tại upload trước 30 ngày sẽ bị xoá vĩnh viễn.',
    confirmText: 'Xoá file cũ',
    loadingText: 'Đang xoá...',
    variant: 'danger',
    async onConfirm() {
      const userIdParam = (props.isAdmin && scopeMode.value === 'all' && selectedUserId.value) ? selectedUserId.value : undefined
      const result = await storage.bulkDelete({ olderThanDays: 30, userId: userIdParam })
      return result
    }
  })
  if (!result) return
  toast.success(`Đã xoá ${result.removed || 0} file cũ`)
  reload(1)
  loadStats()
  if (scopeMode.value === 'all' && !selectedUserId.value) loadUsers()
}

async function openPreview(f) {
  previewItem.value = f
  previewSignedUrl.value = ''
  try {
    const data = await storage.getSignedUrl(f.name, { bucket: f.bucket })
    previewSignedUrl.value = data.signedUrl
  } catch (err) {
    toast.error('Không tạo được URL preview')
    previewItem.value = null
  }
}

// Copy signed URL vào clipboard. URL expire ~1h (do BE issuing); user share / curl được trong khoảng đó.
async function onCopyUrl(f) {
  copyingPath.value = f.name
  try {
    const data = await storage.getSignedUrl(f.name, { bucket: f.bucket })
    await copyText(data.signedUrl)
    copiedPath.value = f.name
    setTimeout(() => { if (copiedPath.value === f.name) copiedPath.value = '' }, 1800)
    toast.success('Đã copy URL (hiệu lực ~1 giờ)')
  } catch (err) {
    toast.error('Copy thất bại: ' + (err.data?.error || err.message))
  } finally {
    copyingPath.value = ''
  }
}

// ── Lifecycle ──────────────────────────────────────────────────────
onMounted(() => {
  reload(1)
  loadStats()
})

watch(selectedUserId, (id) => {
  if (id) reload(1)
})
// #endregion
</script>

<style scoped>
/* ALD 24/05/2026 - Apple Files table style */
.apple-files-table { border-collapse: collapse; }
.apple-files-table thead {
  position: sticky; top: 0; z-index: 10;
  background: var(--glass-bg-solid);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
}
.afp-th {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: rgba(235,236,240,0.55);
  padding: 12px 14px;
  border-bottom: 0.5px solid rgba(235,236,240,0.1);
}
.afp-cell-checkbox { width: 44px; padding: 10px 0 10px 16px; vertical-align: middle; }
.afp-cell-actions  { width: 84px; padding: 10px 12px; vertical-align: middle; text-align: right; }

.afp-row {
  transition: background 0.16s cubic-bezier(0.32, 0.72, 0, 1);
}
.afp-row:hover { background: rgba(235,236,240,0.04); }
.afp-row td { padding: 10px 14px; vertical-align: middle; border-top: 0.5px solid rgba(235,236,240,0.06); }

.afp-icon-tile {
  flex-shrink: 0;
  width: 36px; height: 36px;
  display: inline-flex; align-items: center; justify-content: center;
  border-radius: 8px;
  font-size: 17px;
}
.afp-icon-tile.is-large {
  width: 56px;
  height: 56px;
  border-radius: 8px;
  font-size: 24px;
}
.afp-tile-image { background: var(--color-warning-light); color: #9a6700; }
.afp-tile-video { background: var(--color-danger-light); color: #d70015; }
.afp-tile-audio { background: var(--color-success-light); color: #1b6b30; }
.afp-tile-file  { background: var(--color-gray-100); color: var(--ink-3); }
.afp-tile-doc   { background: var(--color-info-light); color: #9AA2F2; }

.afp-name-btn {
  font-size: 14px;
  font-weight: 600;
  letter-spacing: -0.01em;
  color: var(--ink);
  background: none; border: none; padding: 0;
  cursor: pointer;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  text-align: left;
  transition: color 0.16s;
}
.afp-name-btn:hover { color: var(--primary); }

.afp-kind-badge {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 3px 9px;
  font-size: 10.5px; font-weight: 700;
  border-radius: 999px;
  letter-spacing: 0.01em;
}

.afp-cell-num {
  text-align: right;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12.5px;
  color: rgba(235,236,240,0.7);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.afp-cell-date {
  text-align: right;
  font-size: 12px;
  color: rgba(235,236,240,0.55);
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

.afp-action {
  width: 30px; height: 30px;
  display: inline-flex; align-items: center; justify-content: center;
  background: transparent; border: none;
  border-radius: 999px;
  color: var(--ink-3);
  cursor: pointer;
  font-size: 14px;
  transition: all 0.16s;
}
.afp-action:hover { background: rgba(94,106,210,0.08); color: var(--primary); }
.afp-action-danger:hover { background: var(--color-danger-light); color: #d70015; }
.afp-action:disabled { opacity: 0.5; cursor: default; }
</style>
