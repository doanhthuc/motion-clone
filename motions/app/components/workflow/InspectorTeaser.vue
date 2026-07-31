<template>
  <!-- ALD 31/05/2026 - Teaser inspector (Phase 2). Cổng động: Sản phẩm (productCount 1-6)
       + Người mẫu (modelCount 0-3, opt). Bỏ cổng Motion + Nhạc → Nhạc upload tại đây. -->
  <div class="space-y-4">
    <!-- Kịch bản / Storyboard do người dùng nhập -->
    <div class="apl-fm-group">
      <div class="flex items-center justify-between mb-2">
        <p class="apl-fm-heading !mb-0">Kịch bản / Storyboard</p>
      </div>
      <textarea
        v-model="local.scriptText"
        rows="6"
        class="apl-fm-input"
        style="height:auto;padding:8px 10px;font-family:inherit;line-height:1.5;resize:vertical"
        placeholder="Dán storyboard/kịch bản/cảnh quay. Hệ thống dùng đúng nội dung bạn nhập để chia cảnh + làm prompt, không gọi LLM dàn cảnh."
      />
      <p class="apl-fm-hint">
        Dùng nội dung người dùng truyền vào làm cảnh/prompt. Nếu có mốc giây (0-3s, 3-6s...) hệ thống chia theo mốc; nếu không có thì chia theo câu.
      </p>
    </div>

    <!-- Ảnh sản phẩm (product-hero, KHÔNG người mẫu — dùng node Lookbook nếu cần mẫu mặc đồ) -->
    <div class="apl-fm-group">
      <p class="apl-fm-heading">Ảnh sản phẩm · <span class="normal-case font-medium text-primary">1 sản phẩm / teaser</span></p>
      <span class="apl-fm-label">Số ảnh (góc) <span class="text-rose-600">*</span></span>
      <div class="apl-stepper mt-1">
        <button type="button" class="apl-step-btn" :disabled="productCount <= 1" @click="local.productCount = Math.max(1, productCount - 1)"><i class="bi bi-dash-lg" /></button>
        <span class="apl-step-val">{{ productCount }}</span>
        <button type="button" class="apl-step-btn" :disabled="productCount >= 6" @click="local.productCount = Math.min(6, productCount + 1)"><i class="bi bi-plus-lg" /></button>
      </div>
      <p class="apl-fm-hint">
        <i class="bi bi-images me-1 text-primary" /><b>{{ productCount }} ảnh = {{ productCount }} góc/chi tiết của CÙNG 1 sản phẩm</b> (không phải nhiều SP khác nhau). AI dựng bối cảnh điện ảnh từ các ảnh này.
        <br><i class="bi bi-info-circle me-1" />Teaser <b>product-hero</b> — KHÔNG cần người mẫu. Cần mẫu mặc sản phẩm? Dùng node <b>Lookbook</b>.
      </p>
    </div>

    <!-- Kiểu chuyển động -->
    <div class="apl-fm-group">
      <div class="flex items-center justify-between mb-2">
        <p class="apl-fm-heading !mb-0">Kiểu chuyển động</p>
        <label class="apl-toggle" :class="local.sceneMode !== 'off' && 'is-on'">
          <input :checked="local.sceneMode !== 'off'" type="checkbox" class="sr-only" @change="local.sceneMode = $event.target.checked ? 'auto' : 'off'" />
          <i class="bi bi-easel" /> AI dựng bối cảnh
        </label>
      </div>
      <div class="grid grid-cols-1 gap-1.5">
        <button
          type="button"
          :class="['apl-motion-tile', 'is-active']"
        >
          <i class="bi bi-film" />
          <span class="flex-1 text-left">
            <b>AI chuyển động thật</b> <span class="opacity-60">(Wan I2V)</span>
            <small class="block opacity-70">Mỗi cảnh tự cử động (~1-2'/shot GPU). Cắt nhanh + transition TikTok ở khâu ghép.</small>
          </span>
        </button>
      </div>
      <p class="apl-fm-hint">
        <i class="bi bi-easel me-1 text-primary" /><b>AI dựng bối cảnh</b>: mỗi shot AI tạo cảnh điện ảnh từ ảnh sản phẩm theo storyboard (hợp teaser showcase). Tắt → giữ nguyên ảnh sản phẩm/try-on.
      </p>
    </div>

    <!-- Giọng đọc voiceover — Ngôn ngữ → Giọng (dropdown list) -->
    <div class="apl-fm-group">
      <p class="apl-fm-heading">Giọng đọc voiceover</p>
      <div class="grid grid-cols-2 gap-2">
        <div>
          <span class="apl-fm-label">Loại giọng</span>
          <UiDropdown v-model="selectedLang" :options="LANG_OPTIONS" icon="bi-mic" full-width no-clear :clearable="false" class="mt-1" />
        </div>
        <div>
          <span class="apl-fm-label">Giọng</span>
          <UiDropdown v-model="local.voice" :options="voiceOptions" icon="bi-soundwave" full-width no-clear :clearable="false" class="mt-1" />
        </div>
      </div>
      <p class="apl-fm-hint"><b>Clone từ file mẫu</b> = giọng giống file bạn cung cấp (viXTTS). Hoặc chọn <b>Gemini</b> (giọng tự nhiên có sẵn).</p>
    </div>

    <!-- Thời lượng -->
    <div class="apl-fm-group">
      <p class="apl-fm-heading">Thời lượng teaser</p>
      <div class="grid grid-cols-3 gap-1.5">
        <button
          v-for="d in DURATIONS"
          :key="d.sec"
          type="button"
          :class="[
            'press flex flex-col items-center justify-center gap-0.5 h-12 rounded-2xl border text-[11px] font-bold transition-colors',
            local.targetDurationSec === d.sec
              ? 'bg-primary text-white border-primary shadow-pill'
              : 'bg-gray-50 text-gray-600 border-gray-200 hover:border-primary/40'
          ]"
          @click="local.targetDurationSec = d.sec"
        >
          <span>{{ d.sec }}s</span>
          <span class="text-[9px] font-medium opacity-80">{{ d.hint }}</span>
        </button>
      </div>
      <p class="apl-fm-hint">Tổng độ dài ≈ {{ local.targetDurationSec }}s, chia đều cho số shot.</p>
    </div>

    <!-- Số shot -->
    <div class="apl-fm-group">
      <p class="apl-fm-heading">Số shot (cảnh)</p>
      <UiDropdown v-model="local.numShots" :options="SHOT_OPTIONS" icon="bi-collection-play" full-width no-clear :clearable="false" />
      <p class="apl-fm-hint">Nhiều shot = cắt nhanh (hợp TikTok); ít = chậm/sang. <b>Auto</b> = AI tự chia (3–6).</p>
    </div>

    <!-- Nhạc nền (upload tuỳ chọn) -->
    <div class="apl-fm-group">
      <p class="apl-fm-heading">Nhạc nền <span class="opacity-50 normal-case font-medium">(tuỳ chọn)</span></p>
      <input ref="musicInput" type="file" accept="audio/*" class="hidden" @change="onMusicSelected" />
      <button v-if="!local.musicKey" type="button" class="apl-upload-btn" :disabled="musicUploading" @click="musicInput?.click()">
        <i :class="['bi', musicUploading ? 'bi-arrow-repeat animate-spin' : 'bi-music-note-beamed']" />
        <span>{{ musicUploading ? 'Đang upload…' : 'Chọn file nhạc (MP3/WAV)' }}</span>
      </button>
      <div v-else class="apl-music-card">
        <i class="bi bi-file-music-fill text-primary text-lg" />
        <div class="min-w-0 flex-1">
          <div class="text-xs font-semibold truncate">{{ local.musicName || 'audio' }}</div>
          <audio v-if="local.musicUrl" :src="local.musicUrl" controls class="w-full mt-1 h-8" />
        </div>
        <button type="button" class="apl-icon-btn-mini" title="Xoá nhạc" :disabled="musicUploading" @click="clearMusic"><i class="bi bi-trash" /></button>
      </div>
      <p class="apl-fm-hint">Nhạc nền trộn dưới voiceover. Bỏ trống → chỉ có giọng đọc.</p>
    </div>

  </div>
</template>

<script setup>
const props = defineProps({
  config: { type: Object, required: true },
  nodeType: { type: String, default: 'teaser' }
})
const emit = defineEmits(['update:config'])

const DURATIONS = [
  { sec: 15, hint: 'ngắn' },
  { sec: 30, hint: 'test' },
  { sec: 60, hint: 'production' }
]
// ALD 03/06/2026 - Số shot: 0 = Auto (AI tự chia 3-6); 3-8 = ép đúng số (numShots → handlers.js).
const SHOT_OPTIONS = [
  { value: 0, label: 'Auto (AI tự chia)' },
  { value: 3, label: '3 shot' }, { value: 4, label: '4 shot' }, { value: 5, label: '5 shot' },
  { value: 6, label: '6 shot' }, { value: 7, label: '7 shot' }, { value: 8, label: '8 shot' }
]
// ALD 03/06/2026 - Giọng: viXTTS clone (từ file mẫu) + Gemini (tự nhiên). Bỏ Piper (robotic) + edge (bị chặn).
// id khớp worker _tts: 'vixtts' → service clone; 'gemini:<Name>' → Gemini TTS.
const VOICE_GROUPS = [
  { label: 'Giọng clone (file mẫu)', voices: [
    { id: 'vixtts', label: 'Clone từ file mẫu của bạn ⭐' }
  ] },
  { label: 'Gemini (tự nhiên)', voices: [
    { id: 'gemini:Kore',    label: 'Nữ — chắc (Kore)' },
    { id: 'gemini:Aoede',   label: 'Nữ — nhẹ (Aoede)' },
    { id: 'gemini:Leda',    label: 'Nữ — trẻ (Leda)' },
    { id: 'gemini:Charon',  label: 'Nam — trầm (Charon)' },
    { id: 'gemini:Puck',    label: 'Nam — sôi nổi (Puck)' }
  ] }
]

const local = ref({
  scriptText: '',
  targetDurationSec: 30,
  productCount: 1,      // ALD 31/05/2026 - số ảnh (góc) của 1 sản phẩm (1-6)
  numShots: 0,          // ALD 03/06/2026 - 0 = Auto; 3-8 = ép số shot
  voice: 'vixtts',      // ALD 03/06/2026 - mặc định GIỌNG CLONE (từ file mẫu); đổi sang gemini:* nếu muốn
  motionMode: 'i2v',    // ALD 04/06/2026 - Ken Burns đã GỠ (vô dụng/cảnh tĩnh). Mặc định chuyển động AI thật (Wan I2V).
  sceneMode: 'auto',    // ALD 02/06/2026 - 'auto' AI dựng bối cảnh điện ảnh từng shot (product-hero) | 'off'
  aiDirector: false,    // Teaser dùng đúng kịch bản/cảnh người dùng nhập, không gọi LLM dàn cảnh
  scriptModel: '',
  mode: 'single',
  musicKey: '', musicBucket: '', musicName: '', musicUrl: '',
  ...props.config
})

// Clamp hiển thị (không cho ngoài range dù config lỗi)
const productCount = computed(() => Math.max(1, Math.min(6, Number(local.value.productCount) || 1)))

// ALD 13/06/2026 - Thư viện giọng (viXTTS clone từ file mẫu admin upload). Thêm 1 NHÓM "Thư viện giọng"
// vào picker, value 'voicelib:<id>' (worker _tts route qua API → tải ref → viXTTS). Built-in giữ nguyên.
const voicesLib = useVoices()
onMounted(() => { voicesLib.load() })
const VOICE_LIB_GROUP_LABEL = 'Thư viện giọng (đã clone)'
const allVoiceGroups = computed(() => {
  const groups = [...VOICE_GROUPS]
  if (voicesLib.options.value.length) {
    groups.unshift({ label: VOICE_LIB_GROUP_LABEL, voices: voicesLib.options.value.map((o) => ({ id: o.value, label: o.label })) })
  }
  return groups
})

// ALD 02/06/2026 - Picker Ngôn ngữ → Giọng (UiDropdown, không dùng <select>). selectedLang là computed
// writable: đổi ngôn ngữ → đặt voice = giọng ĐẦU của ngôn ngữ đó. local.voice (id) là giá trị lưu thật.
const LANG_OPTIONS = computed(() => allVoiceGroups.value.map((g) => ({ value: g.label, label: g.label })))
const selectedLang = computed({
  get: () => allVoiceGroups.value.find((g) => g.voices.some((v) => v.id === local.value.voice))?.label || allVoiceGroups.value[0].label,
  set: (label) => { const g = allVoiceGroups.value.find((x) => x.label === label); if (g) local.value.voice = g.voices[0].id }
})
const voiceOptions = computed(() => {
  const g = allVoiceGroups.value.find((x) => x.label === selectedLang.value) || allVoiceGroups.value[0]
  return g.voices.map((v) => ({ value: v.id, label: v.label }))
})

// #region ALD 31/05/2026 - Upload nhạc nền → storage (bucket motion-audio) → musicKey/musicBucket.
// Engine teaser nạp musicKey từ storage vào inputs.music (run_teaser đọc inputs.music).
const storageFiles = useStorageFiles()
const toast = useToast()
const musicInput = ref(null)
const musicUploading = ref(false)
const MAX_MUSIC_BYTES = 50 * 1024 * 1024

async function onMusicSelected(ev) {
  const file = ev.target.files?.[0]
  if (!file) return
  if (file.size > MAX_MUSIC_BYTES) { toast.error('File nhạc > 50MB.'); ev.target.value = ''; return }
  musicUploading.value = true
  const oldKey = local.value.musicKey, oldBucket = local.value.musicBucket || 'motion-audio'
  try {
    const res = await storageFiles.uploadFile(file, { bucket: 'motion-audio', prefix: 'wf-teaser-music' })
    if (res?.path) {
      local.value.musicKey = res.path
      local.value.musicBucket = res.bucket || 'motion-audio'
      local.value.musicName = file.name
      local.value.musicUrl = res.signedUrl || ''
      toast.success(`Đã thêm nhạc: ${file.name}`, { duration: 2500 })
      if (oldKey && oldKey !== res.path) storageFiles.deleteFiles([oldKey], { bucket: oldBucket }).catch(() => {})
    } else { throw new Error('Server không trả path') }
  } catch (err) {
    toast.error(`Upload nhạc lỗi: ${err?.data?.message || err?.message || err}`)
  } finally {
    musicUploading.value = false
    ev.target.value = ''
  }
}
function clearMusic() {
  const k = local.value.musicKey, b = local.value.musicBucket || 'motion-audio'
  local.value.musicKey = ''; local.value.musicBucket = ''; local.value.musicName = ''; local.value.musicUrl = ''
  if (k) storageFiles.deleteFiles([k], { bucket: b }).catch(() => {})
}
// #endregion

watch(local, (v) => emit('update:config', { ...v }), { deep: true })
watch(() => props.config, (v) => {
  if (v && JSON.stringify(v) !== JSON.stringify(local.value)) {
    local.value = { ...local.value, ...v }
  }
})
</script>

<style scoped>
.apl-fm-group { background: var(--apl-fill); border: 0.5px solid rgba(235,236,240,0.12); border-radius: 14px; padding: 12px; }
.apl-fm-heading { font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; color: var(--apl-label); margin-bottom: 8px; }
.apl-fm-label { display: block; font-size: 11px; font-weight: 600; color: var(--apl-label); margin-bottom: 4px; }
.apl-fm-hint { margin-top: 6px; font-size: 10.5px; color: var(--apl-label); line-height: 1.4; }
.apl-fm-input { width: 100%; height: 32px; padding: 0 10px; background: var(--apl-bg-secondary); border: 0.5px solid rgba(235,236,240,0.18); border-radius: 9px; font-size: 12px; transition: border-color 0.18s; }
.apl-fm-input:focus { outline: none; border-color: var(--color-primary, #9AA2F2); }
.apl-fm-summary { cursor: pointer; user-select: none; font-size: 11.5px; font-weight: 700; color: var(--apl-label); text-transform: uppercase; letter-spacing: 0.04em; }
.apl-fm-summary:hover { color: var(--color-primary, #9AA2F2); }
/* Toggle dựng bối cảnh */
.apl-toggle { display: inline-flex; align-items: center; gap: 4px; cursor: pointer; user-select: none; font-size: 10.5px; font-weight: 700; padding: 3px 9px; border-radius: 999px; border: 0.5px solid rgba(235,236,240,0.18); background: var(--apl-bg-secondary); color: var(--apl-label); transition: all 0.15s; }
.apl-toggle.is-on { background: var(--color-primary, #9AA2F2); border-color: var(--color-primary, #9AA2F2); color: white; box-shadow: 0 1px 3px rgba(0,49,167,0.25); }
.apl-toggle i { font-size: 11px; }
/* Motion mode tiles */
.apl-motion-tile { display: flex; align-items: flex-start; gap: 9px; padding: 9px 11px; border-radius: 12px; border: 0.5px solid rgba(235,236,240,0.18); background: var(--apl-bg-secondary); font-size: 12px; color: var(--apl-label); transition: all 0.15s; }
.apl-motion-tile i { font-size: 16px; margin-top: 1px; color: var(--apl-label); }
.apl-motion-tile small { font-size: 10px; line-height: 1.35; margin-top: 1px; }
.apl-motion-tile:hover { border-color: rgba(0,49,167,0.4); }
.apl-motion-tile.is-active { border-color: var(--color-primary, #9AA2F2); background: rgba(0,49,167,0.05); box-shadow: 0 0 0 1px var(--color-primary, #9AA2F2) inset; }
.apl-motion-tile.is-active i { color: var(--color-primary, #9AA2F2); }
/* Stepper số cổng */
.apl-stepper { display: inline-flex; align-items: stretch; width: 100%; border: 0.5px solid rgba(235,236,240,0.18); border-radius: 10px; background: var(--apl-bg-secondary); overflow: hidden; }
.apl-step-btn { flex: 0 0 auto; width: 34px; display: flex; align-items: center; justify-content: center; color: var(--apl-label); transition: background 0.15s; }
.apl-step-btn:hover:not(:disabled) { background: rgba(235,236,240,0.06); color: var(--color-primary, #9AA2F2); }
.apl-step-btn:disabled { opacity: 0.3; cursor: not-allowed; }
.apl-step-val { flex: 1; text-align: center; font-size: 14px; font-weight: 700; font-variant-numeric: tabular-nums; line-height: 34px; border-left: 0.5px solid rgba(235,236,240,0.12); border-right: 0.5px solid rgba(235,236,240,0.12); }
/* Upload nhạc */
.apl-upload-btn { display: flex; align-items: center; justify-content: center; gap: 8px; width: 100%; height: 38px; border: 1px dashed rgba(235,236,240,0.3); border-radius: 11px; background: var(--apl-bg-secondary); font-size: 12px; font-weight: 600; color: var(--apl-label); transition: border-color 0.18s, color 0.18s; }
.apl-upload-btn:hover:not(:disabled) { border-color: var(--color-primary, #9AA2F2); color: var(--color-primary, #9AA2F2); }
.apl-upload-btn:disabled { opacity: 0.6; cursor: wait; }
.apl-music-card { display: flex; align-items: center; gap: 10px; padding: 8px 10px; background: var(--apl-bg-secondary); border: 0.5px solid rgba(235,236,240,0.18); border-radius: 11px; }
.apl-icon-btn-mini { flex: 0 0 auto; width: 28px; height: 28px; display: flex; align-items: center; justify-content: center; border-radius: 8px; color: rgba(220,38,38,0.8); transition: background 0.15s; }
.apl-icon-btn-mini:hover:not(:disabled) { background: rgba(220,38,38,0.1); }
</style>
