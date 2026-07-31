<!-- ALD 13/06/2026 - Thư viện "Giọng nói": admin upload file mẫu MP3/WAV + tên + giới tính → lưu thư viện.
     viXTTS clone giọng TỪ file mẫu lúc TTS (không clone trước), nên mọi node có giọng (talk/teaser/story-film)
     chọn từ list này (value 'voicelib:<id>').
     ALD 06/07/2026 - Redesign theo trang Audio library: hero player bên trái (chọn giọng → phát to) +
     danh sách bên phải, bìa gradient theo hash(id). Bỏ table + <audio controls> per-row. -->
<template>
  <div class="flex flex-col flex-1 min-h-0 gap-3">
    <!-- Header -->
    <div class="flex items-center justify-between gap-3 flex-wrap flex-shrink-0">
      <div class="flex items-center gap-3 min-w-0">
        <span class="inline-flex h-10 w-10 items-center justify-center rounded-xl bg-primary-50 text-primary flex-shrink-0">
          <i class="bi bi-mic-fill text-lg" />
        </span>
        <div class="min-w-0">
          <h3 class="text-base font-semibold tracking-tight text-gray-900">Thư viện giọng nói</h3>
          <p class="text-xs text-gray-500">{{ stats.total }} giọng · {{ stats.female }} nữ · {{ stats.male }} nam</p>
        </div>
      </div>
      <div class="flex items-center gap-2">
        <!-- Lọc giới tính -->
        <div class="flex rounded-[10px] bg-white/[0.05] p-1">
          <button
            v-for="g in FILTERS" :key="g.value" type="button"
            :class="['px-3 py-1.5 rounded-lg text-xs font-semibold transition-all', filterGender === g.value ? 'bg-gray-50 text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700']"
            @click="filterGender = g.value"
          >{{ g.label }}</button>
        </div>
        <button type="button" class="lq-btn lq-btn--icon" title="Làm mới" @click="load">
          <i :class="['bi bi-arrow-clockwise', loading && 'animate-spin']" />
        </button>
        <button type="button" class="lq-btn lq-btn--primary !rounded-full" @click="openCreate">
          <i class="bi bi-cloud-arrow-up-fill" />
          Thêm giọng
        </button>
      </div>
    </div>

    <!-- Main: hero player (trái) + danh sách (phải) -->
    <div class="flex-1 min-h-0 grid grid-cols-1 lg:grid-cols-3 gap-4">
      <!-- Hero -->
      <section class="lg:col-span-2 flex flex-col min-h-0 lq-card !rounded-2xl p-5 sm:p-6 relative overflow-hidden">
        <div class="vl-glow" :style="current ? { background: hashGradient(current.id) } : {}" />
        <template v-if="current">
          <div class="flex items-start gap-5 relative">
            <div class="vl-artwork flex-shrink-0" :style="{ background: hashGradient(current.id) }">
              <i class="bi bi-mic-fill" />
            </div>
            <div class="min-w-0 flex-1 pt-1">
              <span v-if="playingId === current.id" class="inline-flex items-center gap-1.5 text-[10px] font-semibold text-primary uppercase tracking-wide">
                <i class="bi bi-soundwave" /> Đang phát
              </span>
              <span v-else class="text-[10px] font-semibold text-gray-400 uppercase tracking-wide">Đã chọn</span>
              <h2 class="text-2xl font-semibold text-gray-900 truncate mt-0.5">{{ current.name }}</h2>
              <div class="flex items-center gap-2 mt-2 flex-wrap">
                <span class="lq-chip" :class="current.gender === 'female' ? 'lq-chip--rose' : current.gender === 'male' ? 'lq-chip--blue' : ''">
                  {{ GENDER_LABEL[current.gender] || 'Không rõ' }}
                </span>
                <span class="lq-chip !text-[10px]">{{ formatDuration(current.durationSec) }}</span>
                <span class="text-[11px] text-gray-400">{{ formatDate(current.createdAt) }}</span>
              </div>
            </div>
          </div>

          <!-- Progress -->
          <div class="mt-6 relative">
            <div class="vl-progress-bar" @click="onSeekClick">
              <div class="vl-progress-fill" :style="{ width: `${duration ? (currentTime / duration) * 100 : 0}%` }" />
            </div>
            <div class="flex items-center justify-between mt-1.5 text-[11px] text-gray-400 font-mono">
              <span>{{ formatDuration(currentTime) }}</span>
              <span>{{ formatDuration(duration) }}</span>
            </div>
          </div>

          <!-- Transport -->
          <div class="flex items-center justify-center gap-5 mt-5 relative">
            <button type="button" class="vl-btn" title="Giọng trước" @click="playRelative(-1)"><i class="bi bi-skip-start-fill" /></button>
            <button type="button" class="vl-btn-main" :title="playingId === current.id ? 'Tạm dừng' : 'Nghe thử'" @click="togglePlay(current)">
              <i :class="['bi', playingId === current.id ? 'bi-pause-fill' : 'bi-play-fill']" />
            </button>
            <button type="button" class="vl-btn" title="Giọng sau" @click="playRelative(1)"><i class="bi bi-skip-end-fill" /></button>
          </div>

          <!-- Volume + xoá -->
          <div class="flex items-center gap-3 mt-auto pt-4 border-t border-white/[0.06] relative">
            <i class="bi bi-volume-down text-gray-400 text-sm" />
            <input v-model.number="volume" type="range" min="0" max="1" step="0.01" class="vl-volume flex-1" >
            <i class="bi bi-volume-up text-gray-400 text-sm" />
            <button type="button" class="text-gray-400 hover:text-rose-600 flex-shrink-0 ml-2" title="Xoá giọng" @click="del(current)">
              <i class="bi bi-trash" />
            </button>
          </div>
        </template>

        <div v-else class="flex-1 flex flex-col items-center justify-center text-center relative py-10">
          <i class="bi bi-mic text-5xl text-gray-200" />
          <p class="text-sm font-medium text-gray-500 mt-3">Chọn 1 giọng ở danh sách bên phải để nghe thử</p>
          <button type="button" class="lq-btn lq-btn--primary !rounded-full mt-4" @click="openCreate">
            <i class="bi bi-cloud-arrow-up-fill" /> Thêm giọng đầu tiên
          </button>
        </div>
      </section>

      <!-- Danh sách -->
      <aside class="flex flex-col min-h-0 lq-card !rounded-2xl overflow-hidden">
        <div class="flex items-center justify-between px-4 py-3 border-b border-white/[0.06] flex-shrink-0">
          <h4 class="lq-sub">Danh sách</h4>
          <span class="text-[11px] text-gray-400">{{ filtered.length }}/{{ items.length }}</span>
        </div>

        <div v-if="loading" class="px-6 py-12 text-center text-sm text-gray-400 flex-1">
          <i class="bi bi-arrow-clockwise animate-spin text-2xl" />
          <p class="mt-2">Đang tải...</p>
        </div>
        <div v-else-if="!items.length" class="px-6 py-12 text-center flex-1">
          <i class="bi bi-mic text-3xl text-gray-200" />
          <p class="text-xs font-medium text-gray-500 mt-3">Chưa có giọng nào</p>
        </div>
        <div v-else-if="!filtered.length" class="px-6 py-12 text-center flex-1">
          <i class="bi bi-funnel text-2xl text-gray-200" />
          <p class="text-xs font-medium text-gray-500 mt-3">Không có giọng khớp bộ lọc</p>
        </div>

        <ul v-else class="flex-1 min-h-0 overflow-y-auto p-2 space-y-0.5">
          <li
            v-for="it in filtered" :key="it.id" role="button" tabindex="0"
            class="vl-row group"
            :class="{ 'is-active': current?.id === it.id }"
            @click="selectVoice(it)"
          >
            <span class="vl-thumb" :style="current?.id === it.id ? {} : { background: hashGradient(it.id) }">
              <i v-if="playingId !== it.id" class="bi bi-mic-fill text-xs" />
              <i v-else class="bi bi-pause-fill text-sm" />
            </span>
            <div class="min-w-0 flex-1">
              <p class="text-xs font-medium truncate" :class="current?.id === it.id ? 'text-primary' : 'text-gray-700'">{{ it.name }}</p>
              <p class="text-[10px] text-gray-400 truncate">{{ GENDER_LABEL[it.gender] || 'Không rõ' }} · {{ formatDuration(it.durationSec) }}</p>
            </div>
            <button type="button" class="vl-delete opacity-0 group-hover:opacity-100" title="Xoá" @click.stop="del(it)">
              <i class="bi bi-trash text-[11px]" />
            </button>
          </li>
        </ul>
      </aside>
    </div>

    <!-- Singleton audio element -->
    <audio
      ref="playerRef"
      @timeupdate="onTimeUpdate"
      @loadedmetadata="onLoadedMetadata"
      @ended="onPlayEnded"
      @error="onPlayError"
    />

    <!-- Create panel -->
    <UiSidePanel
      v-model="panelOpen"
      title="Thêm giọng nói"
      subtitle="Upload file mẫu MP3/WAV — viXTTS sẽ clone giọng này khi đọc lời thoại"
    >
      <div class="space-y-4">
        <UiUploadDrop
          v-model="form.file"
          label="File giọng mẫu (MP3/WAV)"
          icon="bi-file-music"
          accept="audio/*"
          hint="MP3 / WAV ≤ 50MB · nên là đoạn 5–20s nói rõ, KHÔ (ít vang)"
        />

        <div>
          <label class="lq-label">Tên giọng</label>
          <UiInput v-model="form.name" maxlength="120" placeholder="VD: Giọng nam miền Bắc, MC nữ trẻ…" class="mt-1.5" />
        </div>

        <div>
          <label class="lq-label">Giới tính</label>
          <div class="mt-1.5 grid grid-cols-3 gap-2">
            <button
              v-for="g in GENDER_OPTS" :key="g.value" type="button"
              :class="['lq-select-card !py-2 justify-center text-[13px] font-medium', form.gender === g.value && 'is-on']"
              @click="form.gender = g.value"
            >{{ g.label }}</button>
          </div>
        </div>

        <p class="lq-hint leading-relaxed bg-white/[0.02] border border-white/[0.06] rounded-xl px-3 py-2.5">
          <i class="bi bi-info-circle me-1" />
          Giọng này sẽ hiện trong picker của node Nói / Teaser / Story-film dưới mục "Thư viện giọng".
        </p>
      </div>

      <template #footer>
        <UiButton variant="secondary" size="md" @click="panelOpen = false">Huỷ</UiButton>
        <UiButton variant="primary" size="md" :loading="saving" :disabled="!canSubmit" @click="submitForm">
          <i class="bi bi-cloud-arrow-up" />
          Upload
        </UiButton>
      </template>
    </UiSidePanel>
  </div>
</template>

<script setup>
const auth = useAuth()
const toast = useToast()
const confirm = useConfirm()

const GENDER_OPTS = [
  { value: 'female', label: 'Nữ' },
  { value: 'male', label: 'Nam' },
  { value: 'unknown', label: 'Không rõ' }
]
const GENDER_LABEL = { female: 'Nữ', male: 'Nam', unknown: 'Không rõ' }
const FILTERS = [
  { value: '', label: 'Tất cả' },
  { value: 'female', label: 'Nữ' },
  { value: 'male', label: 'Nam' }
]

const items = ref([])
const loading = ref(false)
const saving = ref(false)
const filterGender = ref('')

const panelOpen = ref(false)
const form = reactive({ file: null, name: '', gender: 'female' })
const canSubmit = computed(() => !!form.file && !!form.name.trim())

const stats = computed(() => ({
  total: items.value.length,
  female: items.value.filter((it) => it.gender === 'female').length,
  male: items.value.filter((it) => it.gender === 'male').length
}))

const filtered = computed(() =>
  filterGender.value ? items.value.filter((it) => it.gender === filterGender.value) : items.value
)

// ── Player (mirror trang Audio: singleton <audio>, chọn ở list → phát ở hero) ──
const playerRef = ref(null)
const playingId = ref(null)
const currentId = ref(null)
const currentTime = ref(0)
const duration = ref(0)
const volume = ref(1)

const current = computed(() => items.value.find((x) => x.id === currentId.value) || null)

watch(volume, (v) => { if (playerRef.value) playerRef.value.volume = v })

const GRADIENTS = [
  'linear-gradient(135deg,#5E6AD2,#5856d6)',
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

function selectVoice(it) {
  if (currentId.value === it.id) { togglePlay(it); return }
  currentId.value = it.id
  togglePlay(it)
}

async function togglePlay(it) {
  currentId.value = it.id
  if (playingId.value === it.id) {
    playerRef.value?.pause()
    playingId.value = null
    return
  }
  try {
    playerRef.value.src = it.audioUrl
    playerRef.value.volume = volume.value
    await playerRef.value.play()
    playingId.value = it.id
  } catch (err) {
    toast.error('Không phát được giọng mẫu')
    console.warn('[voices] play error:', err)
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
function onTimeUpdate() { currentTime.value = playerRef.value?.currentTime || 0 }
function onLoadedMetadata() { duration.value = playerRef.value?.duration || 0 }
function onPlayEnded() { playingId.value = null }
function onPlayError() { playingId.value = null }

function formatDate(value) {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString('vi-VN', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' })
}
function formatDuration(sec) {
  if (!sec || !isFinite(sec)) return '—'
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

function resetForm() {
  Object.assign(form, { file: null, name: '', gender: 'female' })
}
function openCreate() {
  resetForm()
  panelOpen.value = true
}

async function load() {
  loading.value = true
  try {
    items.value = (await auth.beFetch('/voices')).items || []
  } catch (e) {
    toast.error(e?.data?.error || e?.message || 'Lỗi tải danh sách giọng nói')
  } finally {
    loading.value = false
  }
}

async function submitForm() {
  if (saving.value || !canSubmit.value) return
  saving.value = true
  try {
    const fd = new FormData()
    fd.append('file', form.file)
    fd.append('name', form.name.trim())
    fd.append('gender', form.gender)
    const res = await auth.beFetch('/voices', { method: 'POST', body: fd })
    items.value.unshift(res.item)
    toast.success('Đã thêm giọng nói')
    panelOpen.value = false
    resetForm()
  } catch (e) {
    toast.error(e?.data?.error || e?.message || 'Upload lỗi')
  } finally {
    saving.value = false
  }
}

async function del(it) {
  const ok = await confirm.ask({
    title: 'Xoá giọng này?',
    message: `"${it.name}" sẽ bị xoá khỏi thư viện. Node nào đang chọn giọng này sẽ tự dùng giọng mặc định.`,
    confirmText: 'Xoá',
    variant: 'danger'
  })
  if (!ok) return
  try {
    await auth.beFetch(`/voices/${it.id}`, { method: 'DELETE' })
    if (playingId.value === it.id) { playerRef.value?.pause(); playingId.value = null }
    if (currentId.value === it.id) currentId.value = null
    items.value = items.value.filter((x) => x.id !== it.id)
    toast.success('Đã xoá giọng nói')
  } catch (e) {
    toast.error(e?.data?.error || e?.message || 'Xoá thất bại')
  }
}

onMounted(load)
onBeforeUnmount(() => {
  if (playerRef.value) {
    playerRef.value.pause()
    playerRef.value.src = ''
  }
})
</script>

<style scoped>
/* Hero player — mirror trang Audio (light liquid glass) */
.vl-artwork {
  width: 96px; height: 96px; border-radius: 22px;
  color: #fff; display: flex; align-items: center; justify-content: center;
  font-size: 34px; box-shadow: 0 8px 24px rgba(0,0,0,0.16), inset 0 1px 0 rgba(255,255,255,0.35);
}
.vl-glow {
  position: absolute; inset: -40%; opacity: .10; filter: blur(60px); pointer-events: none; z-index: 0;
}
.vl-progress-bar {
  height: 5px; border-radius: 999px; background: rgba(255,255,255,0.1);
  cursor: pointer; overflow: hidden;
}
.vl-progress-fill {
  height: 100%; border-radius: 999px; background: var(--primary); transition: width 0.1s linear;
}
.vl-btn {
  width: 40px; height: 40px; border-radius: 999px; border: 1px solid var(--line);
  background: var(--surface); color: var(--ink); font-size: 16px;
  display: flex; align-items: center; justify-content: center; cursor: pointer;
  box-shadow: 0 1px 2px rgba(0,0,0,0.05);
  transition: background-color 0.15s;
}
.vl-btn:hover { background: rgba(255,255,255,0.08); }
.vl-btn-main {
  width: 56px; height: 56px; border-radius: 999px; border: none;
  background: var(--primary); color: #fff; font-size: 24px;
  display: flex; align-items: center; justify-content: center; cursor: pointer;
  box-shadow: var(--shadow-pill);
  transition: background-color 0.15s, transform 0.1s;
}
.vl-btn-main:hover { background: #6B76E5; }
.vl-btn-main:active { transform: scale(0.95); }
.vl-volume { accent-color: var(--primary); height: 4px; }

/* Danh sách */
.vl-row {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 10px; border-radius: 10px; cursor: pointer;
  transition: background-color 0.15s;
}
.vl-row:hover { background: rgba(255,255,255,0.05500000000000001); }
.vl-row.is-active { background: rgba(94,106,210,0.08); }
.vl-thumb {
  width: 32px; height: 32px; border-radius: 8px; flex-shrink: 0;
  background: rgba(255,255,255,0.07); color: #fff;
  display: flex; align-items: center; justify-content: center;
}
.vl-row.is-active .vl-thumb { background: var(--primary) !important; color: #fff; }
.vl-delete {
  flex-shrink: 0; width: 24px; height: 24px; border: none; background: transparent;
  color: var(--ink-3); border-radius: 999px; cursor: pointer;
  display: flex; align-items: center; justify-content: center; transition: all 0.15s;
}
.vl-delete:hover { color: #d70015; background: var(--color-danger-light); }
</style>
