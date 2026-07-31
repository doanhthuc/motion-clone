<template>
  <div class="space-y-3">
    <!-- ALD 24/05/2026 - Label hiển thị + Vai trò semantic. Label cho UI,
         Purpose để consumer node (motion-transfer, tryon, ...) map
         đúng input vào slot tương ứng. -->
    <div class="grid grid-cols-2 gap-2">
      <div>
        <label class="apl-label">Tên hiển thị</label>
        <input
          v-model="local.label"
          type="text"
          class="apl-input mt-1"
          placeholder="VD: Ảnh người mẫu"
        />
      </div>
      <div v-if="purposeOptions.length > 1">
        <label class="apl-label">Vai trò</label>
        <UiDropdown
          v-model="local.purpose"
          :options="purposeOptions"
          full-width
          no-clear
          class="mt-1"
        />
      </div>
    </div>

    <!-- Row: Content + Source — 2 cột compact -->
    <div class="grid grid-cols-2 gap-2">
      <div>
        <label class="apl-label">Content</label>
        <UiDropdown
          v-model="local.contentType"
          :options="contentTypeOptions"
          full-width
          class="mt-1"
        />
      </div>
      <div>
        <label class="apl-label">Source</label>
        <UiDropdown
          v-model="local.source"
          :options="availableSourceOptions"
          full-width
          class="mt-1"
        />
      </div>
    </div>

    <!-- LIBRARY: audio → /audio, image/video/file → /storage. Picker từ thư viện đã upload. -->
    <!-- ALD 26/06/2026 - Refactor: load 10 items/lần (thay 200), "Xem thêm" append dần, search server-side debounced. -->
    <div v-if="local.source === 'library'" class="apl-card">
      <label class="apl-sub-label">{{ local.contentType === 'audio' ? 'Audio từ thư viện /audio' : `${local.contentType[0].toUpperCase() + local.contentType.slice(1)} từ thư viện /storage` }}</label>
      <!-- Thanh tìm kiếm dùng chung cho cả thumbnail lẫn dropdown -->
      <input
        v-model="librarySearch"
        type="text"
        placeholder="Tìm theo tên…"
        class="apl-thumb-search mt-1"
      >
      <!-- ảnh/video: LƯỚI THUMBNAIL -->
      <template v-if="['image', 'video'].includes(local.contentType)">
        <div v-if="libraryLoading" class="apl-card-hint py-4 text-center"><i class="bi bi-arrow-clockwise animate-spin" /> Đang tải…</div>
        <div v-else-if="!libraryItems.length" class="apl-card-hint py-4 text-center text-rose-600">— Thư viện trống / không khớp</div>
        <div v-else class="apl-thumb-grid mt-1.5">
          <button
            v-for="it in libraryItems"
            :key="it.id || it.name"
            type="button"
            :class="['apl-thumb', (it.id || it.name) === local.libraryId && 'is-active']"
            :title="`${it.name} · ${fmtSize(it.size_bytes || it.size || 0)}`"
            @click="local.libraryId = (it.id || it.name)"
          >
            <video v-if="local.contentType === 'video'" :src="it.signedUrl || it.url" muted preload="metadata" />
            <img v-else :src="it.signedUrl || it.url" alt="" loading="lazy" >
            <span class="apl-thumb-name">{{ it.name }}</span>
            <i v-if="(it.id || it.name) === local.libraryId" class="bi bi-check-circle-fill apl-thumb-check" />
          </button>
        </div>
      </template>
      <!-- audio/file: DROPDOWN -->
      <UiDropdown
        v-else
        v-model="local.libraryId"
        :options="libraryOptions"
        :placeholder="libraryLoading ? 'Đang tải…' : 'Chọn file…'"
        :disabled="libraryLoading"
        full-width
        class="mt-1"
      />
      <!-- "Xem thêm" — dùng chung cho cả 2 chế độ -->
      <div v-if="libraryHasMore || libraryLoadingMore" class="mt-2 flex items-center gap-2">
        <button
          type="button"
          :disabled="libraryLoadingMore"
          class="apl-load-more-btn"
          @click="loadMoreLibrary"
        >
          <i :class="['bi', libraryLoadingMore ? 'bi-arrow-clockwise animate-spin' : 'bi-chevron-down']" />
          {{ libraryLoadingMore ? 'Đang tải…' : `Xem thêm (còn ${libraryTotal - libraryItems.length})` }}
        </button>
      </div>
      <p v-if="selectedLibraryItem" class="apl-card-hint mt-1.5">
        <i :class="['bi me-1', local.contentType === 'audio' ? 'bi-music-note-beamed' : 'bi-file-earmark']" />
        {{ selectedLibraryItem.name }} · {{ fmtSize(selectedLibraryItem.size_bytes || 0) }}
      </p>
      <!-- ALD 27/05/2026 - libraryId set nhưng không có trong library của user
           (public workflow của owner khác) → fetch metadata qua workflow-scoped
           endpoint để hiển thị tên + cảnh báo user pick lại nếu muốn override. -->
      <p v-else-if="local.libraryId && externalLibraryItem" class="apl-card-hint mt-1.5">
        <i :class="['bi me-1', local.contentType === 'audio' ? 'bi-music-note-beamed' : 'bi-file-earmark']" />
        <span class="text-amber-700">{{ externalLibraryItem.name }}</span>
        <span class="text-gray-500"> · {{ fmtSize(externalLibraryItem.size_bytes || 0) }} · (của owner)</span>
      </p>
      <p class="apl-card-hint">
        <NuxtLink :to="local.contentType === 'audio' ? '/audio' : '/storage'" class="text-primary hover:underline">
          <i class="bi bi-box-arrow-up-right" /> Mở {{ local.contentType === 'audio' ? '/audio' : '/storage' }} để upload thêm
        </NuxtLink>
        <span v-if="libraryLoading" class="ms-2 text-gray-400"><i class="bi bi-arrow-clockwise animate-spin" /> Đang tải…</span>
        <span v-else-if="libraryItems.length === 0" class="ms-2 text-rose-600">— Thư viện trống</span>
      </p>
    </div>

    <!-- SESSION → field input -->
    <div v-else-if="local.source === 'session'" class="apl-card">
      <label class="apl-sub-label">Field từ session</label>
      <input v-model="local.field" type="text" class="apl-input mt-1 font-mono text-[12px]" :placeholder="local.contentType" />
      <p class="apl-card-hint">Pull <code>session.{{ local.field || local.contentType }}</code></p>
    </div>

    <!-- URL → URL input + template hint. Social links auto-import through yt-dlp when user clicks import. -->
    <div v-else-if="local.source === 'url'" class="apl-card">
      <label class="apl-sub-label">URL</label>
      <div class="flex gap-2 mt-1">
        <input
          v-model="local.url"
          type="text"
          class="apl-input font-mono text-[12px]"
          placeholder="https://facebook.com/... hoặc https://tiktok.com/... hoặc https://youtube.com/..."
          @keydown.enter.prevent="importSocialUrl"
        />
        <button
          v-if="['video', 'audio'].includes(local.contentType)"
          type="button"
          class="apl-import-btn"
          :disabled="socialImporting || !local.url"
          @click="importSocialUrl"
        >
          <i :class="['bi', socialImporting ? 'bi-arrow-clockwise animate-spin' : 'bi-cloud-download']" />
          {{ socialImporting ? 'Đang lấy…' : (local.contentType === 'audio' ? 'Lấy MP3' : 'Lấy video') }}
        </button>
      </div>
      <p class="apl-card-hint">
        URL thường: runtime fetch. Facebook/TikTok/YouTube: bấm nút để tự tải media vào storage/library.<br>
        Template <code v-pre>{{session.X}}</code> inject session field.<br>
        VD <code v-pre>{{session.file_url}}</code> → user upload file qua chat sẽ tự thay bằng signed URL (engine upload base64 → storage trước khi chạy).
      </p>
      <div v-if="socialImportError" class="apl-warn mt-2">
        <i class="bi bi-exclamation-triangle" />
        {{ socialImportError }}
      </div>
      <div v-if="socialImported" class="apl-info mt-2">
        <i class="bi bi-check-circle" />
        {{ socialImported }}
      </div>
      <!-- #region ALD 22/05/2026 - Cảnh báo signed URL: nội bộ → engine auto re-sign; ngoài → có thể expire. -->
      <div v-if="signedUrlHint?.kind === 'internal'" class="apl-info mt-2">
        <i class="bi bi-shield-check" />
        Signed URL của Supabase này — engine sẽ tự re-sign khi chạy, không lo hết hạn.
      </div>
      <div v-else-if="signedUrlHint?.kind === 'external'" class="apl-warn mt-2">
        <i class="bi bi-exclamation-triangle" />
        URL có token (signed) — có thể hết hạn theo TTL của provider. Cân nhắc <b>Source = Session</b> để pass file mỗi lần invoke.
      </div>
      <!-- #endregion -->
    </div>

    <!-- STATIC TEXT -->
    <div v-else-if="local.source === 'static' && local.contentType === 'text'" class="apl-card">
      <label class="apl-sub-label">Static text</label>
      <textarea v-model="local.staticText" rows="4" class="apl-input mt-1 text-[12px]" placeholder="Constant text..." />
    </div>

    <!-- STATIC media upload (image / video / audio / file) -->
    <div v-else-if="local.source === 'static' && ['image', 'video', 'audio', 'file'].includes(local.contentType)" class="apl-card">
      <label class="apl-sub-label">Upload từ máy</label>
      <input
        ref="fileInput"
        type="file"
        :accept="acceptByType"
        class="hidden"
        @change="onFileSelected"
      />
      <!-- ALD 27/05/2026 - BE strip staticData (base64) khi save → reload thấy staticData=''
           nhưng staticName + staticMime giữ. Hiển thị card "đã upload" với fallback preview
           từ _runOutput URL nếu node đã chạy thành công. Cho phép Re-upload nếu cần. -->
      <!-- ALD 27/05/2026 - Upload button cho cả case "chưa pick lần nào" lẫn "chỉ còn
           metadata staticName nhưng mất data" (workflow legacy save trước refactor).
           User không quan tâm phân biệt — chỉ cần thấy nút Upload là pick lại được. -->
      <!-- ALD 28/05/2026 - Show upload button CHỈ khi không có data nào (kể cả fallback từ
           _runOutput của lần chạy gần nhất). Trước: chỉ check staticUrl + staticData → khi
           workflow def lưu empty nhưng node ĐÃ chạy success → fallback có URL nhưng UI hiện
           "Chọn file" gây user nhầm chưa upload. Thêm fallbackPreviewUrl vào điều kiện. -->
      <button
        v-if="!local.staticUrl && !local.staticData && !fallbackPreviewUrl"
        type="button"
        class="apl-upload-btn mt-1"
        :disabled="staticUploading"
        @click="fileInput?.click()"
      >
        <i :class="['bi text-lg', staticUploading ? 'bi-arrow-clockwise animate-spin' : 'bi-cloud-arrow-up']" />
        <span>{{ staticUploading ? 'Đang upload…' : `Chọn ${uploadKindLabel}` }}</span>
      </button>
      <div v-else class="apl-file-preview mt-1">
        <div class="flex items-center gap-2">
          <i :class="['bi text-base flex-shrink-0', iconByType]" />
          <div class="flex-1 min-w-0">
            <p class="text-[11.5px] font-semibold truncate">{{ local.staticName || 'File từ run gần nhất' }}</p>
            <p class="text-[10px] text-gray-500 font-mono">
              <span v-if="local.staticUrl">{{ fmtSize(local.staticSize || 0) }} · đã lưu trên storage</span>
              <span v-else-if="local.staticData">{{ fmtSize(estimatedSize) }} · in-memory (chưa save)</span>
              <span v-else-if="fallbackPreviewUrl" class="text-amber-600">⚠ chỉ có từ run trước · re-upload để persist</span>
            </p>
          </div>
          <button type="button" class="apl-icon-btn-mini" title="Re-upload" :disabled="staticUploading" @click="fileInput?.click()">
            <i :class="['bi', staticUploading ? 'bi-arrow-clockwise animate-spin' : 'bi-arrow-clockwise']" />
          </button>
          <button type="button" class="apl-icon-btn-mini" title="Xoá file" :disabled="staticUploading" @click="clearStatic">
            <i class="bi bi-x-lg" />
          </button>
        </div>
        <!-- Preview ưu tiên: staticUrl (storage) → staticData (base64 inline) → _runOutput fallback -->
        <img   v-if="local.contentType === 'image' && local.staticUrl" :src="local.staticUrl" class="apl-img-preview mt-2" />
        <video v-else-if="local.contentType === 'video' && local.staticUrl" :src="local.staticUrl" controls muted playsinline class="apl-img-preview mt-2" />
        <audio v-else-if="local.contentType === 'audio' && local.staticUrl" :src="local.staticUrl" controls class="w-full mt-2" />
        <img   v-else-if="local.contentType === 'image' && local.staticData" :src="`data:${local.staticMime};base64,${local.staticData}`" class="apl-img-preview mt-2" />
        <video v-else-if="local.contentType === 'video' && local.staticData" :src="`data:${local.staticMime};base64,${local.staticData}`" controls muted playsinline class="apl-img-preview mt-2" />
        <audio v-else-if="local.contentType === 'audio' && local.staticData" :src="`data:${local.staticMime};base64,${local.staticData}`" controls class="w-full mt-2" />
        <!-- Fallback preview từ _runOutput của lần chạy gần nhất (engine upload base64 → storage trả URL) -->
        <img   v-else-if="local.contentType === 'image' && fallbackPreviewUrl" :src="fallbackPreviewUrl" class="apl-img-preview mt-2" />
        <video v-else-if="local.contentType === 'video' && fallbackPreviewUrl" :src="fallbackPreviewUrl" controls muted playsinline class="apl-img-preview mt-2" />
        <audio v-else-if="local.contentType === 'audio' && fallbackPreviewUrl" :src="fallbackPreviewUrl" controls class="w-full mt-2" />
      </div>
      <p class="apl-card-hint">Max 150MB. File &gt;20MB nên dùng URL source (nhẹ JSON payload).</p>
    </div>

    <!-- Warn nếu History + source != session -->
    <div v-if="local.contentType === 'history' && local.source !== 'session'" class="apl-warn">
      History chỉ lấy được từ Session.
    </div>
  </div>
</template>

<script setup>
const props = defineProps({
  config: { type: Object, required: true },
  nodeType: { type: String, default: 'input' },
  // ALD 07/07/2026 - id node để ghi kết quả async (import social) thẳng vào store theo id,
  // sống sót khi user đóng sidebar (unmount) giữa chừng — xem patchNodeConfigById ở page.
  nodeId: { type: String, default: '' },
  // ALD 27/05/2026 - _runOutput của node truyền xuống để Inspector dùng làm fallback
  // preview khi staticData đã bị strip (sau save). Engine upload base64 → storage,
  // emit URL trong success event, FE set vào node._runOutput.image/video/audio.
  runOutput: { type: Object, default: () => ({}) }
})
const emit = defineEmits(['update:config'])
// ALD 07/07/2026 - ghi thẳng vào node theo id (bypass watcher emit bị huỷ khi unmount).
const patchNodeConfigById = inject('patchNodeConfigById', null)
const fallbackPreviewUrl = computed(() => {
  if (!props.runOutput) return ''
  const ct = props.config?.contentType
  if (ct === 'image') return props.runOutput.image || ''
  if (ct === 'video') return props.runOutput.video || ''
  if (ct === 'audio') return props.runOutput.audio || ''
  return ''
})

const contentTypeOptions = [
  { value: 'text',    label: 'Text' },
  { value: 'image',   label: 'Image' },
  { value: 'video',   label: 'Video' },
  { value: 'audio',   label: 'Audio' },
  { value: 'file',    label: 'File' },
  { value: 'history', label: 'History' }
]
// ALD 24/05/2026 - File accept + label + icon theo contentType.
const acceptByType = computed(() => ({
  image: 'image/*', video: 'video/*', audio: 'audio/*', file: '*/*'
}[local?.contentType] || '*/*'))
const uploadKindLabel = computed(() => ({
  image: 'ảnh', video: 'video', audio: 'audio', file: 'file'
}[local?.contentType] || 'file'))
const iconByType = computed(() => ({
  image: 'bi-image-fill text-violet-500',
  video: 'bi-film text-rose-500',
  audio: 'bi-music-note-beamed text-amber-500',
  file:  'bi-file-earmark-fill text-blue-500'
}[local?.contentType] || 'bi-file-earmark-fill text-blue-500'))
const allSourceOptions = [
  { value: 'session', label: 'Session' },
  { value: 'url',     label: 'URL' },
  { value: 'static',  label: 'Upload' }
]

const legacyContentType = {
  inputText: 'text', inputImage: 'image', inputFile: 'file', inputHistory: 'history'
}[props.nodeType] || 'text'

const local = reactive({
  label: props.config.label || '',
  purpose: props.config.purpose || 'generic',
  contentType: props.config.contentType || legacyContentType,
  source: props.config.source === 'social' ? 'url' : (props.config.source || 'session'),
  field: props.config.field || '',
  url: props.config.url || props.config.socialUrl || '',
  staticText: props.config.staticText || '',
  staticData: props.config.staticData || '',
  staticName: props.config.staticName || '',
  staticMime: props.config.staticMime || '',
  // ALD 27/05/2026 - Static source persistent: thay base64 inline → upload bucket lưu URL.
  // staticUrl + staticPath ưu tiên khi resolve. staticData chỉ giữ cho backward compat
  // (workflow cũ + fallback khi upload fail).
  staticUrl: props.config.staticUrl || '',
  staticPath: props.config.staticPath || '',
  staticBucket: props.config.staticBucket || '',
  staticSize: props.config.staticSize || 0,
  // ALD 24/05/2026 - Library source: id của row trong motion_audio_files (audio) / storage_files (file)
  libraryId: props.config.libraryId || '',
  // ALD 28/06/2026 - Legacy socialUrl kept only for backward-compatible save/load.
  socialUrl: props.config.socialUrl || props.config.url || ''
})

// ALD 24/05/2026 - Library source available cho image / video / audio / file.
// Audio → /audio (motion_audio_files); image / video / file → /storage (storage-files).
const availableSourceOptions = computed(() => {
  if (local.contentType === 'history') return [{ value: 'session', label: 'Session' }]
  const libraryLabel = local.contentType === 'audio' ? 'Library /audio'
                     : ['image', 'video', 'file'].includes(local.contentType) ? 'Library /storage'
                     : null
  if (!libraryLabel) return allSourceOptions
  return [{ value: 'library', label: libraryLabel }, ...allSourceOptions]
})

// ALD 26/06/2026 - Refactor library load: 10 items/trang, "Xem thêm" append dần, search server-side debounced.
// audioFiles.load() mutate shared useState → copy ra libraryItems cục bộ (tránh ảnh hưởng màn hình khác).
// storageFiles.listFiles() return { items, total } trực tiếp. Client-side MIME filter sau khi nhận.
const LIB_PAGE_SIZE = 10
const audioFiles = useAudioFiles()
const storageFiles = useStorageFiles()
const libraryItems = ref([])
const libraryLoading = ref(false)
const libraryLoadingMore = ref(false)
const libraryTotal = ref(0)
const libraryPage = ref(1)
const librarySearch = ref('')
const libraryHasMore = computed(() => libraryTotal.value > 0 && libraryItems.value.length < libraryTotal.value)

function _mimeMatch(it) {
  if (local.contentType === 'file') return true
  const mime = String(it.mime || '').toLowerCase()
  return mime.startsWith(`${local.contentType}/`)
}

async function _fetchPage(page, q) {
  if (local.contentType === 'audio') {
    await audioFiles.load({ page, limit: LIB_PAGE_SIZE, q })
    return { items: audioFiles.items.value || [], total: audioFiles.total.value ?? 0 }
  } else {
    // ALD 26/06/2026 - Pass mime prefix → server filter đúng loại → total chính xác → hasMore đúng
    const mime = local.contentType === 'file' ? '' : local.contentType
    const res = await storageFiles.listFiles({ page, limit: LIB_PAGE_SIZE, q, mime })
    return { items: res?.items || [], total: res?.total ?? 0 }
  }
}

async function loadLibrary() {
  if (!['audio', 'image', 'video', 'file'].includes(local.contentType)) {
    libraryItems.value = []; libraryTotal.value = 0; return
  }
  libraryLoading.value = true
  libraryPage.value = 1
  libraryItems.value = []
  libraryTotal.value = 0
  try {
    const { items, total } = await _fetchPage(1, librarySearch.value.trim())
    libraryItems.value = items
    libraryTotal.value = total
  } catch (e) {
    console.warn('[InspectorInput] library load fail:', e)
  } finally {
    libraryLoading.value = false
  }
}

async function loadMoreLibrary() {
  if (libraryLoadingMore.value || !libraryHasMore.value) return
  libraryLoadingMore.value = true
  const nextPage = libraryPage.value + 1
  try {
    const { items, total } = await _fetchPage(nextPage, librarySearch.value.trim())
    libraryItems.value = [...libraryItems.value, ...items]
    libraryTotal.value = total
    libraryPage.value = nextPage
  } catch (e) {
    console.warn('[InspectorInput] library load-more fail:', e)
  } finally {
    libraryLoadingMore.value = false
  }
}

// Debounce search 350ms → server-side search (không chỉ filter client)
let _searchTimer = null
watch(librarySearch, () => {
  clearTimeout(_searchTimer)
  _searchTimer = setTimeout(() => { if (local.source === 'library') loadLibrary() }, 350)
})

watch(() => [local.source, local.contentType], ([src]) => {
  librarySearch.value = ''
  if (src === 'library') loadLibrary()
})
onMounted(() => { if (local.source === 'library') loadLibrary() })

const libraryOptions = computed(() =>
  libraryItems.value.map((it) => ({
    value: it.id || it.name,
    label: `${it.name || it.id}${it.size_bytes || it.size ? ' · ' + fmtSize(it.size_bytes || it.size) : ''}`
  }))
)
const selectedLibraryItem = computed(() =>
  libraryItems.value.find((it) => (it.id || it.name) === local.libraryId)
)

// ALD 27/05/2026 - Khi non-owner mở public workflow: libraryId trỏ tới asset của owner
// → không có trong libraryItems của user → fetch metadata qua workflow-scoped endpoint
// để Inspector hiện tên + size thay vì UUID trống. Cache theo libraryId tránh re-fetch.
const externalLibraryItem = ref(null)
const externalResolvingId = ref('')
const route = useRoute()
const wf = useWorkflows()
async function resolveExternalLibraryItem() {
  const libId = local.libraryId
  const wfId = route?.params?.id
  if (!libId || !wfId || selectedLibraryItem.value || local.source !== 'library') {
    externalLibraryItem.value = null
    return
  }
  if (externalResolvingId.value === libId && externalLibraryItem.value) return
  externalResolvingId.value = libId
  const kind = local.contentType === 'audio' ? 'audio' : 'storage'
  const item = await wf.getAsset(String(wfId), libId, kind)
  if (externalResolvingId.value === libId) externalLibraryItem.value = item
}
watch(() => [local.libraryId, local.source, local.contentType, libraryLoading.value], () => {
  resolveExternalLibraryItem()
}, { immediate: true })

// ALD 24/05/2026 - Purpose options theo contentType — semantic role để consumer node
// (motion-transfer, tryon, ...) map input vào đúng slot.
const purposeOptions = computed(() => {
  const ct = local.contentType
  if (ct === 'image') return [
    { value: 'generic',       label: 'Ảnh chung' },
    { value: 'model_image',   label: 'Ảnh người mẫu' },
    { value: 'product_image', label: 'Ảnh sản phẩm' },
    { value: 'ref_image',     label: 'Ảnh tham chiếu (motion)' }
  ]
  if (ct === 'video') return [
    { value: 'generic',      label: 'Video chung' },
    { value: 'motion_video', label: 'Video motion (chứa chuyển động)' }
  ]
  if (ct === 'audio') return [
    { value: 'generic',           label: 'Audio chung' },
    { value: 'replacement_audio', label: 'Audio thay thế (output cuối)' }
  ]
  return []
})
// Khi đổi contentType, reset purpose về 'generic' nếu purpose hiện tại không hợp lệ
watch(() => local.contentType, () => {
  const valid = purposeOptions.value.map((o) => o.value)
  if (valid.length && !valid.includes(local.purpose)) local.purpose = 'generic'
})

watch(() => local.contentType, (ct) => {
  if (ct === 'history' && local.source !== 'session') local.source = 'session'
})
watch(local, (v) => emit('update:config', { ...v }), { deep: true })

const fileInput = ref(null)
const toast = useToast()
const auth = useAuth()
const estimatedSize = computed(() => Math.round((local.staticData?.length || 0) * 0.75))

// #region ALD 22/05/2026 - Detect signed URL để cảnh báo expire / báo OK nếu nội bộ.
const config = useRuntimeConfig()
const signedUrlHint = computed(() => {
  if (local.source !== 'url') return null
  const raw = String(local.url || '').trim()
  if (!raw) return null
  let u
  try { u = new URL(raw) } catch { return null }
  const isSupabaseSign = /^\/storage\/v1\/object\/sign\//.test(u.pathname)
  const hasToken = u.searchParams.has('token') || u.searchParams.has('X-Amz-Signature') || u.searchParams.has('sig') || u.searchParams.has('signature')
  if (!isSupabaseSign && !hasToken) return null
  let ourHost = ''
  try { ourHost = new URL(config.public.motionBackendUrl || '').host } catch { /* ignore */ }
  if (isSupabaseSign && ourHost && u.host === ourHost) return { kind: 'internal' }
  return { kind: 'external' }
})
// #endregion

const MAX_UPLOAD_BYTES = 150 * 1024 * 1024  // 150MB

// ALD 27/05/2026 - Upload file → storage bucket (chat-attachments) thay vì base64 inline.
// DB chỉ lưu staticUrl + staticPath (string nhẹ), không bloat, persist qua reload.
// Engine source='static' + staticUrl set → fetch URL → base64 → pass downstream.
// Fallback: nếu upload fail (offline/server) → quay về base64 inline (giữ workflow chạy được).
// Reuse storageFiles composable đã instantiate ở library loader phía trên.
const staticUploading = ref(false)
const socialImporting = ref(false)
const socialImportError = ref('')
const socialImported = ref('')

// ALD 27/05/2026 - Re-sign on mount/path-change. Signed URL TTL 7 ngày — workflow lưu
// lâu hơn → reload sau 1 tuần thì URL 401, preview vỡ. Fetch fresh từ staticPath để
// đảm bảo Inspector luôn hiển thị được. Engine có maybeResignStorageUrl riêng cho runtime.
async function refreshStaticSignedUrl() {
  if (local.source !== 'static' || !local.staticPath) return
  try {
    const res = await storageFiles.getSignedUrl(local.staticPath, { bucket: local.staticBucket || 'chat-attachments' })
    const fresh = res?.signedUrl || res?.signed_url
    if (fresh && fresh !== local.staticUrl) local.staticUrl = fresh
  } catch (e) {
    console.warn('[InspectorInput] refresh staticUrl fail:', e?.message || e)
  }
}
watch(() => [local.staticPath, local.source], () => refreshStaticSignedUrl(), { immediate: true })
async function onFileSelected(ev) {
  const file = ev.target.files?.[0]
  if (!file) return
  if (file.size > MAX_UPLOAD_BYTES) {
    toast.error(`File > 150MB (${fmtSize(file.size)}).`)
    ev.target.value = ''
    return
  }
  staticUploading.value = true
  // ALD 27/05/2026 - Capture path cũ để delete sau khi upload mới thành công.
  // Tránh orphan file trong bucket khi user re-pick (bloat storage qua thời gian).
  const oldPath = local.staticPath
  const oldBucket = local.staticBucket || 'chat-attachments'
  // ALD 27/05/2026 - Route bucket theo contentType vì mỗi bucket có MIME allowlist riêng:
  //   image / file (PDF) → chat-attachments (200MB, image+pdf)
  //   video             → motion-jobs (500MB, image+video)
  //   audio             → motion-audio (50MB, audio MIMEs)
  // Nếu nhầm bucket → Supabase storage 415 mime rejected → 500 STORAGE_UPLOAD_FAIL.
  const bucketByContent = {
    audio: 'motion-audio',
    video: 'motion-jobs',
    image: 'chat-attachments',
    file:  'chat-attachments',
  }[local.contentType] || 'chat-attachments'
  try {
    const res = await storageFiles.uploadFile(file, { bucket: bucketByContent, prefix: 'wf-static' })
    if (res?.signedUrl && res?.path) {
      local.staticUrl = res.signedUrl
      local.staticPath = res.path
      local.staticBucket = res.bucket || 'chat-attachments'
      local.staticName = file.name
      local.staticMime = file.type || 'application/octet-stream'
      local.staticSize = file.size
      local.staticData = ''   // clear base64 — không cần nữa
      toast.success(`Đã upload ${file.name}`, { duration: 2500 })
      // Best-effort delete old file (fire-and-forget) — không block UX nếu fail.
      if (oldPath && oldPath !== res.path) {
        storageFiles.deleteFiles([oldPath], { bucket: oldBucket }).catch((e) => {
          console.warn('[InspectorInput] delete old staticPath fail:', e?.message || e)
        })
      }
    } else {
      throw new Error('Server không trả signedUrl')
    }
  } catch (err) {
    // ALD 27/05/2026 - $fetch throw {data, statusCode, statusMessage}. Extract BE error
    // message (sau khi BE đã wrap try/catch trả JSON body). Phân biệt:
    //   - 413 PAYLOAD_TOO_LARGE → suggest signed upload URL path (chưa implement).
    //   - 500 STORAGE_UPLOAD_FAIL → log chi tiết để debug bucket/RLS.
    //   - Khác → generic + fallback base64.
    const beErr = err?.data?.error || err?.statusMessage || err?.message || String(err)
    const code = err?.data?.code || err?.statusCode
    console.warn('[InspectorInput] upload fail:', { code, beErr, raw: err })
    if (code === 'PAYLOAD_TOO_LARGE' || err?.statusCode === 413) {
      toast.error(`File ${fmtSize(file.size)} quá lớn để upload qua edge function. Cần signed upload URL trực tiếp (chưa support).`, { duration: 7000 })
      ev.target.value = ''
      staticUploading.value = false
      return  // không fallback base64 cho file quá to
    }
    toast.warning(`Upload fail: ${beErr} — dùng base64 inline tạm`, { duration: 5000 })
    if (file.size > 20 * 1024 * 1024) {
      toast.warning(`File ${fmtSize(file.size)} — payload to. Khuyến nghị retry upload sau.`)
    }
    await new Promise((resolve) => {
      const reader = new FileReader()
      reader.onload = () => {
        local.staticData = String(reader.result).split(',')[1] || ''
        local.staticName = file.name
        local.staticMime = file.type || 'application/octet-stream'
        local.staticUrl = ''
        local.staticPath = ''
        resolve()
      }
      reader.readAsDataURL(file)
    })
  } finally {
    staticUploading.value = false
    ev.target.value = ''  // cho phép pick lại cùng file
  }
}

async function importSocialUrl() {
  socialImportError.value = ''
  socialImported.value = ''
  const url = String(local.url || local.socialUrl || '').trim()
  if (!url) return
  if (!['video', 'audio'].includes(local.contentType)) {
    socialImportError.value = 'Social URL chỉ hỗ trợ Video hoặc Audio.'
    return
  }
  socialImporting.value = true
  try {
    const res = await auth.beFetch('/media-imports/social', {
      method: 'POST',
      body: { url, contentType: local.contentType },
      headers: { 'Content-Type': 'application/json' },
    })
    if (local.contentType === 'audio') {
      const item = res?.item
      if (!item?.id) throw new Error('Backend không trả audio id')
      local.libraryId = item.id
      local.source = 'library'
      // ALD 07/07/2026 - persist thẳng theo id: nếu user đã đóng sidebar (unmount) thì watcher
      // emit đã chết, chỉ đường này cứu được kết quả import khỏi rơi mất.
      patchNodeConfigById?.(props.nodeId, { libraryId: item.id, source: 'library' })
      socialImported.value = `Đã tạo MP3: ${item.name || item.id}`
      toast.success('Đã lấy audio MP3 vào Library /audio', { duration: 3500 })
      await loadLibrary()
    } else {
      if (!res?.signedUrl || !res?.path) throw new Error('Backend không trả video URL')
      local.staticUrl = res.signedUrl
      local.staticPath = res.path
      local.staticBucket = res.bucket || 'motion-jobs'
      local.staticName = res?.item?.name || res.path.split('/').pop() || 'social-video.mp4'
      local.staticMime = res?.item?.mime || 'video/mp4'
      local.staticSize = Number(res?.item?.size_bytes || 0)
      local.staticData = ''
      local.source = 'static'
      // ALD 07/07/2026 - persist thẳng theo id (xem trên): sống sót khi sidebar đóng giữa chừng.
      patchNodeConfigById?.(props.nodeId, {
        staticUrl: local.staticUrl, staticPath: local.staticPath, staticBucket: local.staticBucket,
        staticName: local.staticName, staticMime: local.staticMime, staticSize: local.staticSize,
        staticData: '', source: 'static',
      })
      socialImported.value = `Đã lấy video: ${local.staticName}`
      toast.success('Đã lấy video vào workflow input', { duration: 3500 })
    }
  } catch (e) {
    const msg = e?.data?.error || e?.message || String(e)
    socialImportError.value = msg
    toast.error(`Không lấy được media: ${msg}`, { duration: 6500 })
  } finally {
    socialImporting.value = false
  }
}

function clearStatic() {
  // ALD 27/05/2026 - Best-effort delete file trên bucket khi user clear (tránh orphan).
  if (local.staticPath) {
    const path = local.staticPath
    const bucket = local.staticBucket || 'chat-attachments'
    storageFiles.deleteFiles([path], { bucket }).catch((e) => {
      console.warn('[InspectorInput] clearStatic delete fail:', e?.message || e)
    })
  }
  local.staticData = ''
  local.staticName = ''
  local.staticMime = ''
  local.staticUrl = ''
  local.staticPath = ''
  local.staticBucket = ''
  local.staticSize = 0
}
function fmtSize(bytes) {
  if (!bytes) return ''
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(0)}KB`
  return `${(bytes / 1024 ** 2).toFixed(1)}MB`
}
</script>

<style scoped>
.apl-label { display: block; font-size: 10px; font-weight: 700; color: var(--apl-label); text-transform: uppercase; letter-spacing: 0.04em; }
.apl-sub-label { display: block; font-size: 10px; font-weight: 600; color: var(--apl-label); text-transform: uppercase; letter-spacing: 0.04em; }
.apl-input {
  display: block; width: 100%;
  padding: 6px 9px;
  background: var(--apl-bg-secondary);
  border: 0.5px solid rgba(235,236,240,0.18); border-radius: 7px;
  font-size: 12px; color: var(--apl-label);
  outline: none; transition: all 0.15s;
  font-family: inherit;
}
.apl-input:focus { border-color: #007AFF; box-shadow: 0 0 0 3px rgba(0,122,255,0.2); }

.apl-card {
  padding: 10px;
  background: rgba(118,118,128,0.06);
  border-radius: 9px;
  border: 0.5px solid rgba(235,236,240,0.1);
}
.apl-card-hint {
  margin-top: 5px;
  font-size: 10.5px;
  color: var(--apl-label);
  line-height: 1.4;
}
.apl-card-hint code {
  background: var(--apl-fill-2); padding: 1px 4px; border-radius: 3px;
  font-family: ui-monospace,SFMono-Regular,monospace; font-size: 10px;
}

.apl-warn {
  font-size: 11px; color: #A86200;
  background: rgba(255,149,0,0.15);
  padding: 8px 10px; border-radius: 8px;
  border: 0.5px solid rgba(255,149,0,0.2);
}
.apl-warn .bi { margin-right: 4px; }

.apl-info {
  font-size: 11px; color: #9AA2F2;
  background: rgba(0,49,167,0.06);
  padding: 8px 10px; border-radius: 8px;
  border: 0.5px solid rgba(0,49,167,0.18);
  line-height: 1.4;
}
.apl-info .bi { margin-right: 4px; }

.apl-upload-btn {
  display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 3px;
  width: 100%; min-height: 60px;
  padding: 10px;
  background: var(--apl-bg-secondary);
  border: 1.5px dashed rgba(235,236,240,0.25); border-radius: 8px;
  font-size: 11.5px; font-weight: 600; color: var(--apl-label);
  cursor: pointer; font-family: inherit; transition: all 0.15s;
}
.apl-upload-btn:hover {
  border-color: #007AFF; background: rgba(0,122,255,0.04); color: #007AFF;
}

.apl-import-btn {
  flex-shrink: 0;
  height: 32px;
  min-width: 70px;
  padding: 0 10px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  border: none;
  border-radius: 7px;
  background: #007AFF;
  color: white;
  font-size: 11.5px;
  font-weight: 700;
  cursor: pointer;
  font-family: inherit;
}
.apl-import-btn:disabled { opacity: 0.55; cursor: not-allowed; }

.apl-file-preview {
  background: var(--apl-bg-secondary); padding: 8px; border-radius: 7px;
  border: 0.5px solid rgba(235,236,240,0.1);
}
/* ALD 27/05/2026 - Adaptive: width 100% + height auto theo natural aspect ratio
   của image/video. Cap max-height tránh portrait quá tall vỡ layout inspector. */
.apl-img-preview {
  display: block; width: 100%; height: auto;
  max-height: 360px;
  border-radius: 5px; object-fit: cover; object-position: center top; background: #0a0a0a;
}
.apl-icon-btn-mini {
  flex-shrink: 0; width: 22px; height: 22px;
  display: inline-flex; align-items: center; justify-content: center;
  background: transparent; border: none; border-radius: 5px;
  color: var(--apl-label); cursor: pointer; font-size: 10px;
}
.apl-icon-btn-mini:hover { background: rgba(255,59,48,0.15); color: #FF3B30; }

/* ALD 15/06/2026 - lưới thumbnail chọn ảnh/video từ thư viện (thay dropdown text) */
.apl-thumb-search { width: 100%; height: 30px; padding: 0 10px; background: var(--apl-bg-secondary); border: 0.5px solid rgba(235,236,240,0.18); border-radius: 8px; font-size: 12px; }
.apl-thumb-search:focus { outline: none; border-color: var(--color-primary, #9AA2F2); }
.apl-thumb-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; max-height: 280px; overflow-y: auto; padding: 2px; }
.apl-thumb { position: relative; display: flex; flex-direction: column; padding: 0; border: 1.5px solid rgba(235,236,240,0.12); border-radius: 10px; overflow: hidden; background: #101013; cursor: pointer; transition: border-color .15s, box-shadow .15s; }
.apl-thumb:hover { border-color: rgba(0,49,167,0.4); }
.apl-thumb.is-active { border-color: var(--color-primary, #9AA2F2); box-shadow: 0 0 0 2px rgba(0,49,167,0.25); }
.apl-thumb img, .apl-thumb video { width: 100%; aspect-ratio: 1/1; object-fit: cover; display: block; background: #e5e5ea; }
.apl-thumb-name { font-size: 9.5px; line-height: 1.2; padding: 3px 4px; color: var(--apl-label); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.apl-thumb-check { position: absolute; top: 3px; right: 3px; color: var(--color-primary, #9AA2F2); background: var(--apl-bg-secondary); border-radius: 50%; font-size: 14px; line-height: 1; }
.apl-load-more-btn { display: inline-flex; align-items: center; gap: 5px; font-size: 12px; font-weight: 500; color: var(--color-primary, #9AA2F2); background: rgba(0,49,167,0.06); border: 0.5px solid rgba(0,49,167,0.2); border-radius: 8px; padding: 5px 12px; cursor: pointer; transition: background .15s; }
.apl-load-more-btn:hover:not(:disabled) { background: rgba(0,49,167,0.12); }
.apl-load-more-btn:disabled { opacity: 0.6; cursor: default; }
</style>
