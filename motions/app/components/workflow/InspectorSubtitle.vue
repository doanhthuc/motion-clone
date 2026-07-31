<template>
  <!-- ALD 15/06/2026 - Node "Phụ đề + Dịch": 1 video → OmniVoice ASR (nhận lời thoại) + dịch (Ollama) →
       CHÁY phụ đề đã dịch vào video, GIỮ tiếng gốc. Dịch từng câu hiện realtime ở log. Nối 1 node Input (Video). -->
  <div class="space-y-4">
    <div class="apl-info-card">
      <p class="font-semibold flex items-center gap-1.5"><i class="bi bi-badge-cc" /> Phụ đề + Dịch</p>
      <p class="text-[11px] opacity-70 mt-1">
        Nối <b>1 node Input (Video)</b> vào. Hệ thống tự nhận lời thoại (OmniVoice ASR), <b>dịch</b> sang ngôn ngữ đích
        rồi <b>cháy phụ đề</b> vào video (giữ nguyên tiếng gốc). Từng câu dịch hiện realtime khi chạy.
      </p>
    </div>

    <!-- Chế độ xuất -->
    <div class="apl-fm-group">
      <p class="apl-fm-heading">Chế độ</p>
      <div class="grid grid-cols-3 gap-1.5">
        <button v-for="m in MODES" :key="m.id" type="button"
          :class="['apl-fm-tile', local.mode === m.id && 'is-active']" @click="local.mode = m.id">
          <i :class="['bi', m.icon]" /><span class="apl-fm-tile-sub">{{ m.label }}</span>
        </button>
      </div>
      <p class="apl-fm-hint">{{ MODES.find(m => m.id === local.mode)?.hint }}</p>
    </div>

    <!-- Ngôn ngữ đích (dịch sang) — dùng cho cả phụ đề lẫn lồng tiếng -->
    <div class="apl-fm-group">
      <p class="apl-fm-heading">Ngôn ngữ đích (dịch sang)</p>
      <select v-model="local.targetLang" class="apl-fm-input">
        <option v-for="l in LANGS" :key="l.code" :value="l.code">{{ l.label }}</option>
      </select>
      <p class="apl-fm-hint">Ngôn ngữ NGUỒN tự nhận diện. VD: video tiếng Việt → chọn <b>English</b> = phụ đề/giọng tiếng Anh.</p>
    </div>

    <!-- Giọng lồng tiếng — chỉ cho chế độ dub -->
    <div v-if="local.mode !== 'subtitle'" class="apl-fm-group">
      <p class="apl-fm-heading">Giọng lồng tiếng</p>
      <select v-model="local.voice" class="apl-fm-input">
        <option v-for="v in VOICES" :key="v.id" :value="v.id">{{ v.label }}</option>
      </select>
      <p class="apl-fm-hint"><b>Clone</b>: giữ chất giọng người trong video, đọc ngôn ngữ đích (vd Việt→Anh cùng giọng) — cần viXTTS chạy. "Tự động": giọng máy theo ngôn ngữ. Audio gốc bị THAY.</p>
    </div>

    <!-- Tốc độ giọng clone (XTTS đọc ngoại ngữ hay bị chậm) -->
    <div v-if="local.mode !== 'subtitle' && local.voice === 'clone'" class="apl-fm-group">
      <p class="apl-fm-heading">Tốc độ giọng clone</p>
      <label class="apl-fm-hint block mb-1">Hệ số: <b>{{ Number(local.voiceSpeed || 1.3).toFixed(2) }}×</b></label>
      <input v-model.number="local.voiceSpeed" type="range" min="0.8" max="1.7" step="0.05" class="w-full" >
      <p class="apl-fm-hint">Clone (XTTS) đọc ngoại ngữ thường CHẬM → ~1.3× cho tự nhiên. 1.0 = nguyên gốc; cao hơn = nhanh hơn.</p>
    </div>

    <!-- Song ngữ (chỉ khi có phụ đề) -->
    <label v-if="local.mode !== 'dub'" class="apl-fm-group flex items-center justify-between cursor-pointer">
      <span>
        <span class="apl-fm-heading" style="margin:0">Phụ đề song ngữ</span>
        <span class="apl-fm-hint block">Hiện 2 dòng: câu gốc + bản dịch. Tắt = chỉ bản dịch.</span>
      </span>
      <input v-model="local.bilingual" type="checkbox" class="apl-fm-switch" />
    </label>

    <!-- Độ chính xác ASR -->
    <div class="apl-fm-group">
      <p class="apl-fm-heading">Độ chính xác nhận diện (ASR)</p>
      <div class="grid grid-cols-3 gap-1.5">
        <button v-for="m in ASR_MODELS" :key="m.id" type="button"
          :class="['apl-fm-tile', local.asrModel === m.id && 'is-active']" @click="local.asrModel = m.id">
          <span class="apl-fm-tile-label">{{ m.label }}</span>
          <span class="apl-fm-tile-sub">{{ m.sub }}</span>
        </button>
      </div>
      <p class="apl-fm-hint">Cao hơn = chính xác hơn nhưng chậm hơn. <code>large-v3</code> tốt cho video nhiều giọng/ồn.</p>
    </div>

    <!-- Vị trí + cỡ chữ (chỉ khi có phụ đề) -->
    <div v-if="local.mode !== 'dub'" class="apl-fm-group">
      <p class="apl-fm-heading">Hiển thị phụ đề</p>
      <div class="grid grid-cols-3 gap-1.5 mb-2">
        <button v-for="p in POSITIONS" :key="p.id" type="button"
          :class="['apl-fm-tile', local.position === p.id && 'is-active']" @click="local.position = p.id">
          <i :class="['bi', p.icon]" /><span class="apl-fm-tile-sub">{{ p.label }}</span>
        </button>
      </div>
      <label class="apl-fm-hint block mb-1">Cỡ chữ: <b>{{ local.fontSize }}</b></label>
      <input v-model.number="local.fontSize" type="range" min="12" max="32" step="1" class="w-full" />
    </div>
  </div>
</template>

<script setup>
const props = defineProps({
  config: { type: Object, required: true },
  nodeType: { type: String, default: 'subtitle' }
})
const emit = defineEmits(['update:config'])

const MODES = [
  { id: 'subtitle', label: 'Phụ đề', icon: 'bi-badge-cc', hint: 'Giữ tiếng gốc, chỉ cháy phụ đề đã dịch (theo ngôn ngữ đích).' },
  { id: 'dub', label: 'Lồng tiếng', icon: 'bi-mic-fill', hint: 'Dịch → ngôn ngữ đích, đọc giọng tương ứng, THAY audio gốc. VD: video Việt → giọng Anh.' },
  { id: 'dub-sub', label: 'Cả hai', icon: 'bi-collection-play', hint: 'Lồng tiếng + cháy phụ đề (cùng ngôn ngữ đích).' }
]
const VOICES = [
  { id: 'clone', label: '★ Clone giọng người trong video (giữ chất giọng)' },
  { id: '', label: 'Tự động (theo ngôn ngữ đích)' },
  { id: 'en_US-amy-medium', label: 'English — nữ (offline, ổn định)' },
  { id: 'en_US-ryan-high', label: 'English — nam (offline, ổn định)' },
  { id: 'en-US-AriaNeural', label: 'English — nữ (tự nhiên, cần mạng — hay bị chặn)' },
  { id: 'gemini:Kore', label: 'Gemini — nữ (đa ngữ, cần key)' },
  { id: 'gemini:Puck', label: 'Gemini — nam (đa ngữ, cần key)' },
  { id: 'vixtts', label: 'viXTTS — clone (chỉ Tiếng Việt)' },
  { id: 'vi_VN-vais1000-medium', label: 'Piper — VN offline' }
]
const LANGS = [
  { code: 'vi', label: 'Tiếng Việt' }, { code: 'en', label: 'English' }, { code: 'zh', label: '中文 (Trung)' },
  { code: 'ja', label: '日本語 (Nhật)' }, { code: 'ko', label: '한국어 (Hàn)' }, { code: 'fr', label: 'Français' },
  { code: 'es', label: 'Español' }, { code: 'th', label: 'ไทย (Thái)' }, { code: 'de', label: 'Deutsch' }, { code: 'ru', label: 'Русский' }
]
const ASR_MODELS = [
  { id: 'small', label: 'Nhanh', sub: 'small' },
  { id: 'medium', label: 'Cân bằng', sub: 'medium' },
  { id: 'large-v3', label: 'Chính xác', sub: 'large-v3' }
]
const POSITIONS = [
  { id: 'bottom', label: 'Dưới', icon: 'bi-align-bottom' },
  { id: 'center', label: 'Giữa', icon: 'bi-align-center' },
  { id: 'top', label: 'Trên', icon: 'bi-align-top' }
]

const local = ref({ mode: 'subtitle', targetLang: 'vi', bilingual: false, asrModel: 'medium', fontSize: 18, position: 'bottom', voice: '', voiceSpeed: 1.3, ...props.config })

watch(local, (v) => emit('update:config', { ...v }), { deep: true })
watch(() => props.config, (v) => {
  if (v && JSON.stringify(v) !== JSON.stringify(local.value)) local.value = { ...local.value, ...v }
})
</script>

<style scoped>
.apl-info-card { background: rgba(255,149,0,0.08); border: 0.5px solid rgba(255,149,0,0.28); border-radius: 12px; padding: 11px 12px; }
.apl-fm-group { background: var(--apl-fill); border: 0.5px solid rgba(235,236,240,0.12); border-radius: 14px; padding: 12px; }
.apl-fm-heading { font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; color: var(--apl-label); margin-bottom: 8px; }
.apl-fm-hint { margin-top: 6px; font-size: 10.5px; color: var(--apl-label); line-height: 1.4; }
.apl-fm-input { width: 100%; height: 32px; padding: 0 10px; background: var(--apl-bg-secondary); border: 0.5px solid rgba(235,236,240,0.18); border-radius: 9px; font-size: 12px; transition: border-color 0.18s; }
.apl-fm-input:focus { outline: none; border-color: var(--color-primary, #9AA2F2); }
.apl-fm-switch { width: 38px; height: 22px; flex-shrink: 0; -webkit-appearance: none; appearance: none; background: rgba(235,236,240,0.22); border-radius: 999px; position: relative; cursor: pointer; transition: background 0.18s; }
.apl-fm-switch:checked { background: #FF9500; }
.apl-fm-switch::after { content: ''; position: absolute; top: 2px; left: 2px; width: 18px; height: 18px; border-radius: 50%; background: var(--apl-bg-secondary); transition: transform 0.18s; }
.apl-fm-switch:checked::after { transform: translateX(16px); }
.apl-fm-tile { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 3px; height: 44px; border-radius: 12px; border: 0.5px solid rgba(235,236,240,0.18); background: var(--apl-bg-secondary); color: var(--apl-label); transition: all 0.15s; }
.apl-fm-tile:hover { border-color: rgba(255,149,0,0.45); }
.apl-fm-tile.is-active { border-color: #FF9500; background: rgba(255,149,0,0.08); color: #B36800; box-shadow: 0 0 0 1px #FF9500 inset; }
.apl-fm-tile-label { font-size: 12.5px; font-weight: 700; }
.apl-fm-tile-sub { font-size: 9.5px; opacity: 0.7; }
</style>
