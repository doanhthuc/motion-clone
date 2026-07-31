<template>
  <!-- #region ALD 06/07/2026 - Audio library: Liquid Glass light — hero Now Playing bên trái +
       danh sách track cuộn bên phải; bỏ dark theme cục bộ, dùng lq-* design system. -->
  <div class="flex-1 min-h-0 flex flex-col px-3 sm:px-6 pt-1 pb-3 gap-3">
    <div class="lq-panel p-4 sm:p-6 flex-1 min-h-0 flex flex-col gap-4">

      <!-- Header -->
      <div class="flex items-center justify-between gap-3 flex-shrink-0 flex-wrap">
        <div class="flex items-center gap-3 min-w-0">
          <span class="inline-flex h-10 w-10 items-center justify-center rounded-xl bg-primary-50 text-primary flex-shrink-0">
            <i class="bi bi-music-note-beamed text-lg" />
          </span>
          <div class="min-w-0">
            <h1 class="text-lg font-semibold tracking-tight text-gray-900">Audio Library</h1>
            <p class="text-xs text-gray-500 truncate">{{ audio.total.value }} file · để gắn vào motion-job hoặc giữ riêng</p>
          </div>
        </div>
        <div class="flex items-center gap-2">
          <div class="lq-search w-48">
            <i class="bi bi-search" />
            <input v-model="searchQuery" type="text" placeholder="Tìm theo tên..." class="!h-9" >
          </div>
          <input ref="fileInputRef" type="file" :accept="acceptAttr" multiple class="hidden" @change="onFilePick" >
          <button type="button" class="lq-btn lq-btn--primary !rounded-full !h-9" :disabled="audio.uploading.value" @click="fileInputRef?.click()">
            <i v-if="!audio.uploading.value" class="bi bi-cloud-arrow-up" />
            <i v-else class="bi bi-arrow-clockwise animate-spin" />
            {{ audio.uploading.value ? 'Đang tải…' : 'Upload' }}
          </button>
        </div>
      </div>

      <!-- Upload errors -->
      <div v-if="uploadErrors.length" class="rounded-xl px-4 py-2 border border-rose-200 bg-rose-50 text-xs text-rose-700 space-y-1 flex-shrink-0">
        <div v-for="(e, i) in uploadErrors" :key="i" class="flex items-center gap-2">
          <i class="bi bi-exclamation-triangle-fill" />
          <span class="font-semibold">{{ e.name }}</span>
          <span>·</span>
          <span>{{ e.message }}</span>
        </div>
      </div>

      <!-- Main: hero Now Playing (trái) + track list (phải) — chiều cao cố định, chừa chỗ cho lưới bên dưới -->
      <div
        ref="dropZoneRef"
        class="flex-shrink-0 h-[300px] sm:h-[340px] grid grid-cols-1 lg:grid-cols-3 gap-4 relative"
        @dragenter.prevent="onDragEnter"
        @dragover.prevent="isDragging = true"
        @dragleave.prevent="onDragLeave"
        @drop.prevent="onDrop"
      >
        <!-- Drop overlay -->
        <div v-if="isDragging" class="absolute inset-0 z-10 flex items-center justify-center pointer-events-none bg-gray-50/90 backdrop-blur-sm rounded-2xl border-2 border-dashed border-[rgba(94,106,210,0.5)]">
          <div class="text-center">
            <i class="bi bi-cloud-arrow-down-fill text-4xl text-primary" />
            <p class="text-sm font-semibold text-primary mt-2">Thả để upload</p>
          </div>
        </div>

        <!-- Hero Now Playing -->
        <section class="lg:col-span-2 flex flex-col min-h-0 lq-card !rounded-2xl p-5 sm:p-6 relative overflow-hidden">
          <div class="hero-glow" :style="current ? { background: hashGradient(current.id) } : {}" />
          <template v-if="current">
            <div class="flex items-start gap-5 relative">
              <div class="hero-artwork flex-shrink-0" :style="{ background: hashGradient(current.id) }">
                <i class="bi bi-music-note-beamed" />
              </div>
              <div class="min-w-0 flex-1 pt-1">
                <span v-if="playingId === current.id" class="inline-flex items-center gap-1.5 text-[10px] font-semibold text-primary uppercase tracking-wide">
                  <i class="bi bi-soundwave" /> Đang phát
                </span>
                <span v-else class="text-[10px] font-semibold text-gray-400 uppercase tracking-wide">Đã chọn</span>
                <h2 class="text-2xl font-semibold text-gray-900 truncate mt-0.5">{{ current.name }}</h2>
                <div class="flex items-center gap-2 mt-2 flex-wrap">
                  <span v-if="current.mime" class="lq-chip uppercase !text-[10px]">{{ current.mime.replace('audio/', '') }}</span>
                  <span class="lq-chip !text-[10px]">{{ audio.formatSize(current.size_bytes) }}</span>
                  <span v-if="current.created_at" class="text-[11px] text-gray-400">{{ formatRelative(current.created_at) }}</span>
                </div>
              </div>
            </div>

            <!-- Progress -->
            <div class="mt-6 relative">
              <div class="hero-progress-bar" @click="onSeekClick">
                <div class="hero-progress-fill" :style="{ width: `${duration ? (currentTime / duration) * 100 : 0}%` }" />
              </div>
              <div class="flex items-center justify-between mt-1.5 text-[11px] text-gray-400 font-mono">
                <span>{{ audio.formatDuration(currentTime) }}</span>
                <span>{{ audio.formatDuration(duration) }}</span>
              </div>
            </div>

            <!-- Transport -->
            <div class="flex items-center justify-center gap-5 mt-5 relative">
              <button type="button" class="transport-btn" title="Bài trước" @click="playRelative(-1)"><i class="bi bi-skip-start-fill" /></button>
              <button type="button" class="transport-btn-main" :title="playingId === current.id ? 'Tạm dừng' : 'Phát'" @click="togglePlay(current)">
                <i :class="['bi', playingId === current.id ? 'bi-pause-fill' : 'bi-play-fill']" />
              </button>
              <button type="button" class="transport-btn" title="Bài sau" @click="playRelative(1)"><i class="bi bi-skip-end-fill" /></button>
            </div>

            <!-- Volume + actions -->
            <div class="flex items-center gap-3 mt-auto pt-4 border-t border-white/[0.06] relative">
              <i class="bi bi-volume-down text-gray-400 text-sm" />
              <input v-model.number="volume" type="range" min="0" max="1" step="0.01" class="hero-volume flex-1" >
              <i class="bi bi-volume-up text-gray-400 text-sm" />
              <button type="button" class="text-gray-400 hover:text-rose-600 flex-shrink-0 ml-2" title="Xoá" @click="onDelete(current)">
                <i class="bi bi-trash" />
              </button>
            </div>
          </template>

          <div v-else class="flex-1 flex flex-col items-center justify-center text-center relative">
            <i class="bi bi-music-note-list text-5xl text-gray-200" />
            <p class="text-sm font-medium text-gray-500 mt-3">Chọn 1 bài ở danh sách bên phải để phát</p>
          </div>
        </section>

        <!-- Track list -->
        <aside class="flex flex-col min-h-0 lq-card !rounded-2xl overflow-hidden">
          <div class="flex items-center justify-between px-4 py-3 border-b border-white/[0.06] flex-shrink-0">
            <h3 class="lq-sub">Danh sách</h3>
            <span class="text-[11px] text-gray-400">{{ filtered.length }}/{{ audio.total.value }}</span>
          </div>

          <div v-if="audio.loading.value" class="px-6 py-12 text-center text-sm text-gray-400 flex-1">
            <i class="bi bi-arrow-clockwise animate-spin text-2xl" />
            <p class="mt-2">Đang tải...</p>
          </div>
          <div v-else-if="!audio.items.value.length" class="px-6 py-12 text-center flex-1">
            <i class="bi bi-music-note text-3xl text-gray-200" />
            <p class="text-xs font-medium text-gray-500 mt-3">Library trống</p>
          </div>
          <div v-else-if="!filtered.length" class="px-6 py-12 text-center flex-1">
            <i class="bi bi-search text-2xl text-gray-200" />
            <p class="text-xs font-medium text-gray-500 mt-3">Không có file khớp</p>
          </div>

          <ul v-else class="flex-1 min-h-0 overflow-y-auto p-2 space-y-0.5">
            <li
              v-for="item in filtered" :key="item.id" role="button" tabindex="0"
              class="track-row group"
              :class="{ 'is-active': current?.id === item.id }"
              @click="selectTrack(item)"
            >
              <span class="track-thumb" :style="current?.id === item.id ? {} : { background: hashGradient(item.id) }">
                <i v-if="playingId !== item.id" class="bi bi-music-note-beamed text-xs" />
                <i v-else class="bi bi-pause-fill text-sm" />
              </span>
              <div class="min-w-0 flex-1">
                <p class="text-xs font-medium truncate" :class="current?.id === item.id ? 'text-primary' : 'text-gray-700'">{{ item.name }}</p>
                <p class="text-[10px] text-gray-400 truncate">{{ audio.formatDuration(item.duration_sec) }}</p>
              </div>
              <button type="button" class="track-delete opacity-0 group-hover:opacity-100" title="Xoá" @click.stop="onDelete(item)">
                <i class="bi bi-trash text-[11px]" />
              </button>
            </li>
          </ul>

          <!-- Pagination -->
          <div v-if="totalPages > 1" class="flex items-center justify-between px-4 py-2.5 border-t border-white/[0.06] flex-shrink-0">
            <button type="button" class="lq-page-btn" :disabled="page <= 1" @click="goPage(page - 1)"><i class="bi bi-chevron-left" /></button>
            <span class="text-[11px] text-gray-500">Trang {{ page }}/{{ totalPages }}</span>
            <button type="button" class="lq-page-btn" :disabled="page >= totalPages" @click="goPage(page + 1)"><i class="bi bi-chevron-right" /></button>
          </div>
        </aside>
      </div>

      <!-- Tất cả file — lưới bìa album, lấp khoảng trống bên dưới player + tăng độ phong phú trực quan -->
      <section v-if="filtered.length" class="flex-1 min-h-0 flex flex-col">
        <h3 class="lq-sub mb-2 flex-shrink-0">Tất cả file</h3>
        <div class="flex-1 min-h-0 overflow-y-auto pr-1">
          <div class="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8 gap-3 p-2">
            <button
              v-for="item in filtered" :key="item.id" type="button"
              class="grid-tile group text-left"
              :class="{ 'is-active': current?.id === item.id }"
              @click="selectTrack(item)"
            >
              <div class="grid-tile-art" :style="{ background: hashGradient(item.id) }">
                <i v-if="playingId !== item.id" class="bi bi-music-note-beamed" />
                <i v-else class="bi bi-soundwave" />
                <span class="grid-tile-hover"><i class="bi bi-play-fill" /></span>
              </div>
              <p class="text-[11px] font-medium truncate mt-1.5" :class="current?.id === item.id ? 'text-primary' : 'text-gray-700'">{{ item.name }}</p>
              <p class="text-[10px] text-gray-400 truncate">{{ audio.formatDuration(item.duration_sec) }}</p>
            </button>
          </div>
        </div>
      </section>
    </div>

    <!-- Singleton audio element -->
    <audio
      ref="playerRef"
      @timeupdate="onTimeUpdate"
      @loadedmetadata="onLoadedMetadata"
      @ended="onPlayEnded"
      @error="onPlayError"
    />
  </div>
  <!-- #endregion -->
</template>

<script setup>
definePageMeta({ middleware: 'auth' })

useHead({ title: 'Audio Library — Motions' })

const audio = useAudioFiles()
const toast = useToast()
const confirm = useConfirm()

const fileInputRef = ref(null)
const dropZoneRef = ref(null)
const playerRef = ref(null)
const isDragging = ref(false)
const dragCounter = ref(0)
const searchQuery = ref('')

const playingId = ref(null)
const currentId = ref(null) // bài đang được chọn hiển thị ở hero (khác playingId khi đã pause)
const currentTime = ref(0)
const duration = ref(0)
const volume = ref(1)

const page = ref(1)
const PAGE_SIZE = 50

const uploadErrors = ref([])

const acceptAttr = computed(() => audio.ALLOWED_EXTS.map((e) => `.${e}`).join(','))

// ALD 05/07/2026 - Search lọc trên trang hiện tại (phân trang thật ở BE qua audio.load({page,q})).
const filtered = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  if (!q) return audio.items.value
  return audio.items.value.filter((x) => (x.name || '').toLowerCase().includes(q))
})

const current = computed(() => audio.items.value.find((x) => x.id === currentId.value) || null)

// ALD 05/07/2026 - Gradient theo hash(id) để mỗi file có "bìa album" màu khác nhau (thay vì 1 màu cam lặp lại
// cho tất cả) — tăng độ phong phú trực quan cho hero + lưới, giống ảnh tham khảo (mỗi track 1 màu riêng).
const GRADIENTS = [
  'linear-gradient(135deg,#ffae00,#ff7a00)',
  'linear-gradient(135deg,#8b5cf6,#5856d6)',
  'linear-gradient(135deg,#06b6d4,#0ea5e9)',
  'linear-gradient(135deg,#f43f5e,#ec4899)',
  'linear-gradient(135deg,#22c55e,#0d9488)',
  'linear-gradient(135deg,#eab308,#f97316)',
  'linear-gradient(135deg,#6366f1,#3b82f6)',
  'linear-gradient(135deg,#f97316,#dc2626)'
]
function hashGradient(id) {
  const s = String(id || '')
  let h = 0
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0
  return GRADIENTS[h % GRADIENTS.length]
}

const totalPages = computed(() => Math.max(1, Math.ceil((audio.total.value || 0) / PAGE_SIZE)))

async function goPage(p) {
  if (p < 1 || p > totalPages.value) return
  page.value = p
  await audio.load({ page: p, limit: PAGE_SIZE, q: searchQuery.value.trim() })
}

let searchDebounce = null
watch(searchQuery, (q) => {
  clearTimeout(searchDebounce)
  searchDebounce = setTimeout(async () => {
    page.value = 1
    await audio.load({ page: 1, limit: PAGE_SIZE, q: q.trim() })
  }, 300)
})

watch(volume, (v) => {
  if (playerRef.value) playerRef.value.volume = v
})

// #region ALD 23/05/2026 - Drag-drop counter logic (handle nested elements)
function onDragEnter() {
  dragCounter.value++
  isDragging.value = true
}
function onDragLeave() {
  dragCounter.value--
  if (dragCounter.value <= 0) {
    isDragging.value = false
    dragCounter.value = 0
  }
}
function onDrop(e) {
  isDragging.value = false
  dragCounter.value = 0
  const files = Array.from(e.dataTransfer?.files || [])
  handleFiles(files)
}
function onFilePick(e) {
  const files = Array.from(e.target.files || [])
  handleFiles(files)
  if (fileInputRef.value) fileInputRef.value.value = ''
}
// #endregion

async function handleFiles(files) {
  if (!files.length) return
  uploadErrors.value = []
  let okCount = 0
  for (const f of files) {
    if (!audio.isValidAudio(f)) {
      uploadErrors.value.push({ name: f.name, message: `Định dạng/size không hợp lệ (${(f.size / 1024 / 1024).toFixed(1)}MB)` })
      continue
    }
    try {
      await audio.upload(f)
      okCount++
    } catch (err) {
      uploadErrors.value.push({ name: f.name, message: err?.message || 'Upload lỗi' })
    }
  }
  if (okCount > 0) {
    toast.success(`Đã upload ${okCount} file`)
  }
  if (uploadErrors.value.length === 0) {
    setTimeout(() => { uploadErrors.value = [] }, 5000)
  }
}

// ── Chọn 1 track ở list → hiện to ở hero (chưa tự phát, bấm nút Play mới phát) ──────────────────────
function selectTrack(item) {
  if (currentId.value === item.id) {
    togglePlay(item)
    return
  }
  currentId.value = item.id
  togglePlay(item)
}

// #region ALD 23/05/2026 - Audio player singleton (1 file phát tại 1 thời điểm)
async function togglePlay(item) {
  currentId.value = item.id
  if (playingId.value === item.id) {
    playerRef.value?.pause()
    playingId.value = null
    return
  }
  try {
    const url = await audio.getSignedUrl(item.id)
    if (!url) {
      toast.error('Không lấy được URL preview')
      return
    }
    playerRef.value.src = url
    playerRef.value.volume = volume.value
    await playerRef.value.play()
    playingId.value = item.id
  } catch (err) {
    toast.error('Không phát được audio')
    console.warn('[audio] play error:', err)
  }
}

function playRelative(delta) {
  const list = filtered.value
  if (!list.length) return
  const idx = list.findIndex((x) => x.id === currentId.value)
  const nextIdx = idx < 0 ? 0 : (idx + delta + list.length) % list.length
  togglePlay(list[nextIdx])
}

function onSeekClick(e) {
  if (!duration.value || playingId.value !== current.value?.id) return
  const rect = e.currentTarget.getBoundingClientRect()
  const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width))
  playerRef.value.currentTime = ratio * duration.value
}

function onTimeUpdate() {
  currentTime.value = playerRef.value?.currentTime || 0
}
function onLoadedMetadata() {
  duration.value = playerRef.value?.duration || 0
}
function onPlayEnded() {
  playRelative(1)
}
function onPlayError() {
  playingId.value = null
  toast.error('Lỗi phát audio')
}
// #endregion

async function onDelete(item) {
  const ok = await confirm.ask({
    title: 'Xoá audio?',
    message: `Hành động này sẽ xoá vĩnh viễn "${item.name}".`,
    confirmText: 'Xoá',
    variant: 'danger'
  })
  if (!ok) return
  try {
    if (playingId.value === item.id) {
      playerRef.value?.pause()
      playingId.value = null
    }
    if (currentId.value === item.id) currentId.value = null
    await audio.remove(item.id)
    toast.success('Đã xoá')
  } catch (err) {
    toast.error('Không xoá được file')
  }
}

function formatRelative(ts) {
  if (!ts) return ''
  const dt = typeof ts === 'string' ? new Date(ts).getTime() : ts
  const diff = Date.now() - dt
  const min = Math.floor(diff / 60000)
  if (min < 1) return 'vừa xong'
  if (min < 60) return `${min} phút`
  const h = Math.floor(min / 60)
  if (h < 24) return `${h} giờ`
  const d = Math.floor(h / 24)
  if (d < 7) return `${d} ngày`
  return new Date(dt).toLocaleDateString('vi-VN')
}

onMounted(async () => {
  try { await audio.load({ page: 1, limit: PAGE_SIZE }) } catch { /* BE chưa có endpoint thì silent */ }
})

onBeforeUnmount(() => {
  if (playerRef.value) {
    playerRef.value.pause()
    playerRef.value.src = ''
  }
})
</script>

<style scoped>
/* ALD 06/07/2026 - Liquid Glass light (dùng tokens global, bỏ dark utility) */

/* Hero */
.hero-artwork {
  width: 96px; height: 96px; border-radius: 22px;
  color: #fff; display: flex; align-items: center; justify-content: center;
  font-size: 34px; box-shadow: 0 8px 24px rgba(0,0,0,0.16), inset 0 1px 0 rgba(255,255,255,0.35);
}
.hero-progress-bar {
  height: 5px; border-radius: 999px; background: rgba(255,255,255,0.1);
  cursor: pointer; overflow: hidden;
}
.hero-progress-fill {
  height: 100%; border-radius: 999px; background: var(--primary); transition: width 0.1s linear;
}
.transport-btn {
  width: 40px; height: 40px; border-radius: 999px; border: 1px solid var(--line);
  background: var(--surface); color: var(--ink); font-size: 16px;
  display: flex; align-items: center; justify-content: center; cursor: pointer;
  box-shadow: 0 1px 2px rgba(0,0,0,0.05);
  transition: background-color 0.15s;
}
.transport-btn:hover { background: rgba(255,255,255,0.08); }
.transport-btn-main {
  width: 56px; height: 56px; border-radius: 999px; border: none;
  background: var(--primary); color: #fff; font-size: 24px;
  display: flex; align-items: center; justify-content: center; cursor: pointer;
  box-shadow: var(--shadow-pill);
  transition: background-color 0.15s, transform 0.1s;
}
.transport-btn-main:hover { background: #6B76E5; }
.transport-btn-main:active { transform: scale(0.95); }
.hero-volume { accent-color: var(--primary); height: 4px; }
.hero-glow {
  position: absolute; inset: -40%; opacity: .10; filter: blur(60px); pointer-events: none; z-index: 0;
}

/* Grid tiles ("Tất cả file") */
.grid-tile { display: flex; flex-direction: column; border: none; background: transparent; padding: 0; cursor: pointer; }
.grid-tile-art {
  position: relative; width: 100%; aspect-ratio: 1; border-radius: 12px; overflow: hidden;
  display: flex; align-items: center; justify-content: center; color: #fff; font-size: 20px;
  box-shadow: 0 2px 10px rgba(0,0,0,.12), inset 0 1px 0 rgba(255,255,255,.3);
  transition: transform .15s;
}
.grid-tile:hover .grid-tile-art { transform: scale(1.04); }
.grid-tile.is-active .grid-tile-art { outline: 2px solid var(--primary); outline-offset: 2px; }
.grid-tile-hover {
  position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
  background: rgba(0,0,0,.35); color: #fff; font-size: 22px; opacity: 0; transition: opacity .15s;
}
.grid-tile:hover .grid-tile-hover { opacity: 1; }

/* Track list */
.track-row {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 10px; border-radius: 10px; cursor: pointer;
  transition: background-color 0.15s;
}
.track-row:hover { background: rgba(255,255,255,0.05500000000000001); }
.track-row.is-active { background: rgba(94,106,210,0.08); }
.track-thumb {
  width: 32px; height: 32px; border-radius: 8px; flex-shrink: 0;
  background: rgba(255,255,255,0.07); color: #fff;
  display: flex; align-items: center; justify-content: center;
}
.track-row.is-active .track-thumb { background: var(--primary) !important; color: #fff; }
.track-delete {
  flex-shrink: 0; width: 24px; height: 24px; border: none; background: transparent;
  color: var(--ink-3); border-radius: 999px; cursor: pointer;
  display: flex; align-items: center; justify-content: center; transition: all 0.15s;
}
.track-delete:hover { color: #d70015; background: var(--color-danger-light); }
</style>
