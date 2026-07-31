<template>
  <div class="space-y-4">
    <div class="apl-info-card">
      <p class="font-semibold flex items-center gap-1.5"><i class="bi bi-camera-video" /> Teen Flycam</p>
      <p class="text-[11px] opacity-70 mt-1">
        Cổng trái nhận <b>1 ảnh người mẫu</b> và <b>audio tuỳ chọn</b>. Preset clone dùng motion-driver mẫu để bám nhịp pose, cut camera và đoạn đi bộ cuối.
      </p>
    </div>

    <div class="apl-fm-group">
      <p class="apl-fm-heading">Preset</p>
      <div v-if="presetOptions.length" class="space-y-1.5">
        <button
          v-for="p in presetOptions"
          :key="p.id"
          type="button"
          :class="['apl-preset', local.preset === p.id && 'is-active']"
          @click="local.preset = p.id"
        >
          <span class="flex items-start justify-between gap-2">
            <span class="flex items-center gap-2 min-w-0">
            <i :class="['bi', p.icon]" />
            <span class="font-semibold">{{ p.label }}</span>
            </span>
            <button
              v-if="isCustomPreset(p.id)"
              type="button"
              class="apl-icon-btn-mini"
              title="Xoá preset"
              @click.stop="removePreset(p.id)"
            >
              <i class="bi bi-trash" />
            </button>
          </span>
          <span class="apl-preset-sub">{{ p.hint }}</span>
        </button>
      </div>
      <div v-else class="apl-empty-inline">
        Chưa có preset nào. Bấm <b>Clone preset</b> ở dưới để thêm preset mới.
      </div>
    </div>

    <div class="apl-fm-group">
      <p class="apl-fm-heading">Giới tính mẫu</p>
      <div class="grid grid-cols-3 gap-2">
        <button type="button" :class="['apl-fm-tile', local.modelGender === 'auto' && 'is-active']" @click="local.modelGender = 'auto'">
          <i class="bi bi-stars text-base" /><span class="apl-fm-tile-label">Auto</span>
        </button>
        <button type="button" :class="['apl-fm-tile', local.modelGender === 'female' && 'is-active']" @click="local.modelGender = 'female'">
          <i class="bi bi-gender-female text-base" /><span class="apl-fm-tile-label">Nữ</span>
        </button>
        <button type="button" :class="['apl-fm-tile', local.modelGender === 'male' && 'is-active']" @click="local.modelGender = 'male'">
          <i class="bi bi-gender-male text-base" /><span class="apl-fm-tile-label">Nam</span>
        </button>
      </div>
      <p class="apl-fm-hint">Dùng để Wan chọn dáng đi/pose phù hợp hơn; Auto giữ trung tính.</p>
    </div>

    <div class="apl-fm-group">
      <p class="apl-fm-heading">Engine</p>
      <div class="apl-engine-pill">
        <i class="bi bi-camera-reels" />
        <span>
          <b>Demo motion driver + Wan Animate</b>
          <small>bám choreography sample · ảnh vào giữ nhân vật/outfit</small>
        </span>
      </div>
    </div>

    <div class="apl-fm-group">
      <p class="apl-fm-heading">Clone preset mẫu</p>
      <button type="button" class="apl-clone-btn" @click="openCloneModal">
        <i class="bi bi-copy" />
        <span>Clone preset</span>
      </button>
      <p class="apl-fm-hint">Tạo preset mới từ URL mẫu. Clone xong preset sẽ xuất hiện ngay trong danh sách bên trên.</p>
    </div>

    <div v-if="showCloneModal" class="apl-modal-backdrop" @click.self="closeCloneModal">
      <div class="apl-modal">
        <div class="apl-modal-head">
          <div>
            <p class="apl-modal-title">Clone preset Teen Flycam</p>
            <p class="apl-modal-sub">Preset mới sẽ lưu ngay trong node hiện tại.</p>
          </div>
          <button type="button" class="apl-modal-x" @click="closeCloneModal">
            <i class="bi bi-x-lg" />
          </button>
        </div>

        <div class="space-y-3">
          <div>
            <p class="apl-fm-heading">Tên preset</p>
            <input v-model.trim="cloneDraft.name" type="text" class="apl-fm-input" placeholder="Ví dụ: Flycam phố đi bộ" />
          </div>

          <div>
            <p class="apl-fm-heading">URL motion-driver</p>
            <textarea
              v-model.trim="cloneDraft.driverUrls"
              class="apl-fm-textarea"
              rows="4"
              placeholder="Dán URL video mẫu. Có thể dán nhiều URL, mỗi dòng một clip."
            />
            <div class="mt-2 flex items-center gap-2">
              <button type="button" class="apl-import-btn" :disabled="socialImporting || !cloneDraft.driverUrls.trim()" @click="importCloneSocialUrl">
                <i :class="['bi', socialImporting ? 'bi-arrow-clockwise animate-spin' : 'bi-cloud-download']" />
                <span>{{ socialImporting ? 'Đang lấy video…' : 'Lấy video từ link social' }}</span>
              </button>
            </div>
            <p class="apl-fm-hint">Hỗ trợ link YouTube, TikTok, Facebook giống node Input Video.</p>
            <div v-if="socialImportError" class="apl-inline-warn mt-2">{{ socialImportError }}</div>
            <div v-if="socialImported" class="apl-inline-info mt-2">{{ socialImported }}</div>
            <p v-if="cloneDraft.duration || cloneDraft.shotCount" class="apl-fm-hint">
              Phát hiện từ clip: <b>{{ cloneDraft.duration || 10 }}s</b> · <b>{{ cloneDraft.shotCount || 1 }} shot</b> · tối đa 30s
            </p>
            <p class="apl-fm-hint">Wan sẽ dùng clip này làm motion-driver ẩn để clone nhịp pose, cut camera và đoạn đi bộ.</p>
          </div>
        </div>

        <div class="apl-modal-actions">
          <button type="button" class="apl-modal-btn" @click="closeCloneModal">Huỷ</button>
          <button type="button" class="apl-modal-btn is-primary" :disabled="!canCreateClone" @click="createClonePreset">Tạo preset</button>
        </div>
      </div>
    </div>

    <div class="apl-fm-group">
      <p class="apl-fm-heading">Seed</p>
      <select v-model="local.seedMode" class="apl-fm-input">
        <option value="random">Ngẫu nhiên mỗi lần chạy</option>
        <option value="fixed">Cố định</option>
      </select>
      <input v-if="local.seedMode === 'fixed'" v-model.number="local.seed" type="number" class="apl-fm-input mt-2" placeholder="Seed" />
      <p class="apl-fm-hint">Preset bám driver demo 10s; seed chỉ ảnh hưởng chi tiết sinh hình, không đổi nhịp choreography.</p>
    </div>

    <div class="apl-fm-group">
      <p class="apl-fm-heading">Audio</p>
      <label class="apl-toggle-row">
        <span>
          <span class="apl-toggle-title">Dùng audio nối vào</span>
          <span class="apl-toggle-sub">Tắt = dùng audio gốc của clip preset</span>
        </span>
        <input
          :checked="local.audioMode === 'input'"
          type="checkbox"
          class="apl-fm-switch"
          @change="local.audioMode = $event.target.checked ? 'input' : 'preset'"
        >
      </label>
      <p v-if="local.audioMode === 'input'" class="apl-fm-hint mt-2">Đang ưu tiên audio từ cổng <b>Audio (opt)</b>. Nếu không nối audio, clip sẽ xuất im lặng.</p>
      <p v-else class="apl-fm-hint mt-2">Đang bỏ qua audio nối vào và dùng audio gốc của preset.</p>
    </div>
  </div>
</template>

<script setup>
const auth = useAuth()
const toast = useToast()
const confirm = useConfirm()

const props = defineProps({
  config: { type: Object, required: true },
  nodeType: { type: String, default: 'teen-flycam' }
})
const emit = defineEmits(['update:config'])

const BASE_PRESETS = []

function normalizePresetEntry(p = {}) {
  return {
    id: String(p.id || '').trim(),
    label: String(p.label || '').trim(),
    hint: String(p.hint || '').trim(),
    icon: String(p.icon || 'bi-link-45deg').trim(),
    driverMode: String(p.driverMode || 'custom').trim(),
    driverUrls: String(p.driverUrls || '').trim(),
    duration: Number(p.duration) || 10,
    shotCount: Number(p.shotCount) || 5,
  }
}

function dedupeCustomPresets(list = []) {
  const out = []
  const seen = new Set()
  for (const raw of Array.isArray(list) ? list : []) {
    const p = normalizePresetEntry(raw)
    if (!p.id && !p.driverUrls) continue
    const key = p.id || `${p.label.toLowerCase()}__${p.driverUrls}`
    if (seen.has(key)) continue
    seen.add(key)
    out.push(p)
  }
  return out
}

function normalizeTeenFlycamConfig(cfg = {}) {
  const next = {
    preset: '',
    duration: 10,
    shotCount: 5,
    aspectRatio: '9:16',
    wanModel: 'wan2.2',
    modelGender: 'auto',
    customPresets: [],
    driverMode: 'custom',
    driverUrls: '',
    steps: 20,
    seedMode: 'random',
    audioMode: 'preset',
    ...cfg,
  }
  next.customPresets = dedupeCustomPresets(next.customPresets)
  if (next.preset === 'street-fashion-5shot') next.preset = ''
  if (!next.preset && !String(next.driverUrls || '').trim()) next.driverMode = 'custom'
  return next
}

const local = ref(normalizeTeenFlycamConfig(props.config))

const showCloneModal = ref(false)
const socialImporting = ref(false)
const socialImportError = ref('')
const socialImported = ref('')
const cloneDraft = reactive({
  name: '',
  driverUrls: '',
  duration: 10,
  shotCount: 5,
})

const customPresets = computed(() => Array.isArray(local.value.customPresets) ? local.value.customPresets : [])
const presetOptions = computed(() => [...BASE_PRESETS, ...customPresets.value])
const canCreateClone = computed(() => {
  if (!cloneDraft.name) return false
  return !!cloneDraft.driverUrls.trim()
})

function slugifyPresetName(v = '') {
  return String(v || '')
    .toLowerCase()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 48) || 'teen-flycam'
}

function resetCloneDraft() {
  cloneDraft.name = ''
  cloneDraft.driverUrls = ''
  cloneDraft.duration = 10
  cloneDraft.shotCount = 5
  socialImportError.value = ''
  socialImported.value = ''
}

function openCloneModal() {
  resetCloneDraft()
  cloneDraft.name = `${(presetOptions.value.find((p) => p.id === local.value.preset)?.label || 'Teen Flycam')} copy`
  cloneDraft.driverUrls = String(local.value.driverUrls || '')
  cloneDraft.duration = Number(local.value.duration) || 10
  cloneDraft.shotCount = Number(local.value.shotCount) || 5
  showCloneModal.value = true
}

function closeCloneModal() {
  showCloneModal.value = false
}

function applyPresetToConfig(presetId) {
  const preset = presetOptions.value.find((p) => p.id === presetId)
  if (!preset) return
  local.value.driverMode = preset.driverMode || 'demo'
  local.value.driverUrls = String(preset.driverUrls || '')
  local.value.duration = Number(preset.duration) || 10
  local.value.shotCount = Number(preset.shotCount) || 5
}

function clearPresetConfig() {
  local.value.preset = ''
  local.value.driverMode = 'custom'
  local.value.driverUrls = ''
  local.value.duration = 10
  local.value.shotCount = 5
}

function isCustomPreset(presetId) {
  return customPresets.value.some((p) => p?.id === presetId)
}

function createClonePreset() {
  if (!canCreateClone.value) return
  const normalizedUrl = cloneDraft.driverUrls.trim()
  const normalizedLabel = cloneDraft.name.trim().toLowerCase()
  const existing = customPresets.value.find((p) =>
    String(p?.driverUrls || '').trim() === normalizedUrl &&
    String(p?.label || '').trim().toLowerCase() === normalizedLabel
  )
  if (existing) {
    local.value.preset = existing.id
    applyPresetToConfig(existing.id)
    closeCloneModal()
    toast.success('Preset này đã tồn tại, mình chọn lại giúp bạn rồi', { duration: 2500 })
    return
  }
  const id = `teen-flycam-${slugifyPresetName(cloneDraft.name)}-${Date.now().toString(36)}`
  const next = normalizePresetEntry({
    id,
    label: cloneDraft.name.trim(),
    hint: `${Number(cloneDraft.duration) || 10}s / ${Number(cloneDraft.shotCount) || 5} shot · clone tu URL mẫu`,
    icon: 'bi-link-45deg',
    driverMode: 'custom',
    driverUrls: cloneDraft.driverUrls.trim(),
    duration: Number(cloneDraft.duration) || 10,
    shotCount: Number(cloneDraft.shotCount) || 5,
  })
  local.value.customPresets = dedupeCustomPresets([...customPresets.value, next])
  local.value.preset = id
  applyPresetToConfig(id)
  closeCloneModal()
}

async function removePreset(presetId) {
  const preset = customPresets.value.find((p) => p?.id === presetId)
  if (!preset) return
  const ok = await confirm.ask({
    title: 'Xoá preset clone?',
    message: `Preset "${preset.label || presetId}" sẽ bị xoá khỏi node này.`,
    confirmText: 'Xoá',
    cancelText: 'Huỷ',
    destructive: true,
  })
  if (!ok) return
  local.value.customPresets = customPresets.value.filter((p) => p?.id !== presetId)
  if (local.value.preset === presetId) {
    clearPresetConfig()
  }
  toast.success('Đã xoá preset clone', { duration: 2500 })
}

async function importCloneSocialUrl() {
  socialImportError.value = ''
  socialImported.value = ''
  const url = String(cloneDraft.driverUrls || '').trim()
  if (!url) return
  socialImporting.value = true
  try {
    const res = await auth.beFetch('/media-imports/social', {
      method: 'POST',
      body: { url, contentType: 'video' },
      headers: { 'Content-Type': 'application/json' },
    })
    if (!res?.signedUrl) throw new Error('Backend không trả video URL')
    cloneDraft.driverUrls = String(res.signedUrl || '').trim()
    cloneDraft.duration = Math.max(2, Math.min(30, Math.round(Number(res?.meta?.durationSec) || 10)))
    cloneDraft.shotCount = Math.max(1, Math.min(12, Number(res?.meta?.shotCount) || Math.max(1, Math.round(cloneDraft.duration / 2))))
    socialImported.value = `Đã lấy video: ${res?.item?.name || res?.path?.split('/').pop() || 'social-video.mp4'}`
    toast.success('Đã lấy video social vào preset clone', { duration: 3500 })
  } catch (e) {
    const msg = e?.data?.error || e?.message || String(e)
    socialImportError.value = msg
    toast.error(`Không lấy được video: ${msg}`, { duration: 6500 })
  } finally {
    socialImporting.value = false
  }
}

watch(local, (v) => emit('update:config', { ...normalizeTeenFlycamConfig(v), aspectRatio: '9:16', wanModel: 'wan2.2' }), { deep: true })
watch(() => props.config, (v) => {
  if (v && JSON.stringify(v) !== JSON.stringify(local.value)) local.value = normalizeTeenFlycamConfig({ ...local.value, ...v })
})
watch(() => local.value.preset, (id) => applyPresetToConfig(id), { immediate: true })
</script>

<style scoped>
.apl-info-card { background: rgba(255,45,85,0.07); border: 0.5px solid rgba(255,45,85,0.25); border-radius: 12px; padding: 11px 12px; }
.apl-fm-group { background: var(--apl-fill); border: 0.5px solid rgba(235,236,240,0.12); border-radius: 14px; padding: 12px; }
.apl-fm-heading { font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; color: var(--apl-label); margin-bottom: 8px; }
.apl-fm-hint { margin-top: 6px; font-size: 10.5px; color: var(--apl-label); line-height: 1.4; }
.apl-fm-input { width: 100%; height: 34px; padding: 0 10px; background: var(--apl-bg-secondary); border: 0.5px solid rgba(235,236,240,0.18); border-radius: 9px; font-size: 12px; transition: border-color 0.18s; }
.apl-fm-input:focus { outline: none; border-color: #FF2D55; }
.apl-fm-textarea { width: 100%; min-height: 74px; padding: 9px 10px; background: var(--apl-bg-secondary); border: 0.5px solid rgba(235,236,240,0.18); border-radius: 9px; font-size: 12px; line-height: 1.35; resize: vertical; transition: border-color 0.18s; }
.apl-fm-textarea:focus { outline: none; border-color: #FF2D55; }
.apl-import-btn { display: inline-flex; align-items: center; justify-content: center; gap: 8px; min-width: 210px; height: 34px; padding: 0 12px; border-radius: 10px; border: 0.5px solid rgba(255,45,85,0.24); background: var(--apl-bg-secondary); color: #A11D38; font-size: 12px; font-weight: 700; transition: all 0.15s; }
.apl-import-btn:hover:not(:disabled) { background: rgba(255,45,85,0.07); border-color: rgba(255,45,85,0.35); }
.apl-import-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.apl-icon-btn-mini { flex: 0 0 auto; width: 24px; height: 24px; display: inline-flex; align-items: center; justify-content: center; border-radius: 7px; color: rgba(220,38,38,0.8); transition: background 0.15s; }
.apl-icon-btn-mini:hover { background: rgba(220,38,38,0.1); }
.apl-clone-btn { display: inline-flex; align-items: center; justify-content: center; gap: 8px; width: 100%; height: 38px; border-radius: 11px; border: 0.5px solid rgba(255,45,85,0.24); background: rgba(255,45,85,0.07); color: #A11D38; font-size: 12px; font-weight: 700; transition: all 0.15s; }
.apl-clone-btn:hover { background: rgba(255,45,85,0.12); border-color: rgba(255,45,85,0.35); }
.apl-engine-pill { display: flex; align-items: center; gap: 10px; min-height: 46px; padding: 9px 10px; border-radius: 12px; background: rgba(255,45,85,0.07); color: #A11D38; border: 0.5px solid rgba(255,45,85,0.24); }
.apl-engine-pill i { font-size: 17px; }
.apl-engine-pill span { display: flex; flex-direction: column; gap: 1px; line-height: 1.2; }
.apl-engine-pill b { font-size: 12px; }
.apl-engine-pill small { font-size: 10px; opacity: 0.68; }
.apl-fm-tile { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 2px; height: 50px; border-radius: 12px; border: 0.5px solid rgba(235,236,240,0.18); background: var(--apl-bg-secondary); color: var(--apl-label); transition: all 0.15s; }
.apl-fm-tile:hover { border-color: rgba(255,45,85,0.4); }
.apl-fm-tile.is-active { border-color: #FF2D55; background: rgba(255,45,85,0.07); color: #A11D38; box-shadow: 0 0 0 1px #FF2D55 inset; }
.apl-fm-tile-label { font-size: 12px; font-weight: 700; }
.apl-fm-tile-sub { font-size: 9.5px; opacity: 0.6; }
.apl-preset { width: 100%; text-align: left; display: flex; flex-direction: column; gap: 4px; padding: 10px 11px; border: 0.5px solid rgba(235,236,240,0.16); border-radius: 12px; background: var(--apl-bg-secondary); color: var(--apl-label); transition: all 0.15s; }
.apl-preset:hover { border-color: rgba(255,45,85,0.38); }
.apl-preset.is-active { border-color: #FF2D55; background: rgba(255,45,85,0.07); color: #A11D38; box-shadow: 0 0 0 1px #FF2D55 inset; }
.apl-preset-sub { font-size: 10.5px; line-height: 1.35; color: var(--apl-label); }
.apl-preset.is-active .apl-preset-sub { color: rgba(161,29,56,0.72); }
.apl-modal-backdrop { position: fixed; inset: 0; z-index: 80; background: rgba(0,0,0,0.42); backdrop-filter: blur(6px); display: flex; align-items: center; justify-content: center; padding: 20px; }
.apl-modal { width: min(100%, 420px); border-radius: 18px; background: var(--apl-bg-secondary); border: 0.5px solid rgba(235,236,240,0.12); box-shadow: 0 20px 60px rgba(0,0,0,0.24); padding: 16px; }
.apl-modal-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
.apl-modal-title { font-size: 14px; font-weight: 800; color: var(--apl-label); }
.apl-modal-sub { margin-top: 2px; font-size: 11px; color: var(--apl-label); line-height: 1.35; }
.apl-modal-x { width: 30px; height: 30px; border-radius: 999px; display: inline-flex; align-items: center; justify-content: center; color: var(--apl-label); transition: background 0.15s, color 0.15s; }
.apl-modal-x:hover { background: rgba(235,236,240,0.08); color: var(--apl-label); }
.apl-modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }
.apl-modal-btn { min-width: 92px; height: 36px; padding: 0 14px; border-radius: 10px; border: 0.5px solid rgba(235,236,240,0.16); background: var(--apl-bg-secondary); color: var(--apl-label); font-size: 12px; font-weight: 700; transition: all 0.15s; }
.apl-modal-btn:hover { background: rgba(235,236,240,0.06); }
.apl-modal-btn.is-primary { border-color: #FF2D55; background: #FF2D55; color: white; }
.apl-modal-btn.is-primary:hover { background: #ea2750; }
.apl-modal-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.apl-inline-warn { border-radius: 10px; padding: 8px 10px; background: #FFF3E6; color: #A86200; font-size: 11px; line-height: 1.35; border: 0.5px solid rgba(255,149,0,0.22); }
.apl-inline-info { border-radius: 10px; padding: 8px 10px; background: #EAF4FF; color: #8FBAF0; font-size: 11px; line-height: 1.35; border: 0.5px solid rgba(0,122,255,0.18); }
.apl-empty-inline { border-radius: 12px; padding: 12px; background: rgba(235,236,240,0.04); border: 0.5px dashed rgba(235,236,240,0.18); color: var(--apl-label); font-size: 11px; line-height: 1.45; }
.apl-toggle-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; padding: 10px 12px; border-radius: 12px; background: rgba(15, 15, 18, 0.78); border: 0.5px solid rgba(235,236,240,0.14); }
.apl-toggle-title { display: block; font-size: 12.5px; font-weight: 700; color: rgba(28,28,30,0.9); }
.apl-toggle-sub { display: block; margin-top: 2px; font-size: 10.5px; color: var(--apl-label); line-height: 1.35; }
.apl-fm-switch { width: 38px; height: 22px; flex-shrink: 0; -webkit-appearance: none; appearance: none; background: rgba(235,236,240,0.22); border-radius: 999px; position: relative; cursor: pointer; transition: background 0.18s; }
.apl-fm-switch:checked { background: #FF2D55; }
.apl-fm-switch::after { content: ''; position: absolute; top: 2px; left: 2px; width: 18px; height: 18px; border-radius: 50%; background: var(--apl-bg-secondary); transition: transform 0.18s; }
.apl-fm-switch:checked::after { transform: translateX(16px); }
</style>
