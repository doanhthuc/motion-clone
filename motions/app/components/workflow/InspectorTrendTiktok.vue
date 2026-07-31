<template>
  <div class="space-y-4">
    <div class="apl-info-card">
      <p class="font-semibold flex items-center gap-1.5"><i class="bi bi-stars" /> Trend TikTok</p>
      <p class="text-[11px] opacity-70 mt-1">
        Node này nhận <b>2 ảnh</b>: look ban đầu và look sau khi chuyển cảnh. Preset đầu tiên <b>Xé giấy</b> sẽ bám clip mẫu để ra nhịp reveal tự nhiên hơn.
      </p>
    </div>

    <div class="apl-fm-group">
      <p class="apl-fm-heading">Preset</p>
      <div class="space-y-1.5">
        <button
          v-for="p in presetOptions"
          :key="p.id"
          type="button"
          :class="['apl-preset', local.preset === p.id && 'is-active']"
          @click="applyPreset(p.id)"
        >
          <span class="flex items-start justify-between gap-2">
            <span class="flex items-center gap-2 min-w-0">
              <i :class="['bi', p.icon]" />
              <span class="font-semibold">{{ p.label }}</span>
            </span>
          </span>
          <span class="apl-preset-sub">{{ p.hint }}</span>
        </button>
      </div>
    </div>

    <div class="apl-fm-group">
      <p class="apl-fm-heading">Bố cục input</p>
      <div class="apl-engine-pill">
        <i class="bi bi-images" />
        <span>
          <b>Look 1 + Look 2</b>
          <small>Cổng 1 = ảnh trước khi xé giấy, cổng 2 = ảnh sau khi reveal outfit</small>
        </span>
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
      <p class="apl-fm-hint">Dùng để Wan chọn gesture và nhịp pose phù hợp hơn cho clip trend.</p>
    </div>

    <div class="apl-fm-group">
      <p class="apl-fm-heading">Audio</p>
      <label class="apl-toggle-row">
        <span>
          <span class="apl-toggle-title">Dùng audio nối vào</span>
          <span class="apl-toggle-sub">Tắt = lấy audio gốc của preset Xé giấy</span>
        </span>
        <input
          :checked="local.audioMode === 'input'"
          type="checkbox"
          class="apl-fm-switch"
          @change="local.audioMode = $event.target.checked ? 'input' : 'preset'"
        >
      </label>
      <p v-if="local.audioMode === 'input'" class="apl-fm-hint mt-2">Đang hiện cổng <b>Audio (opt)</b> trên canvas.</p>
      <p v-else class="apl-fm-hint mt-2">Đang ẩn cổng audio và dùng luôn tiếng gốc của preset.</p>
    </div>

    <div class="apl-fm-group">
      <p class="apl-fm-heading">Seed</p>
      <select v-model="local.seedMode" class="apl-fm-input">
        <option value="random">Ngẫu nhiên mỗi lần chạy</option>
        <option value="fixed">Cố định</option>
      </select>
      <input v-if="local.seedMode === 'fixed'" v-model.number="local.seed" type="number" class="apl-fm-input mt-2" placeholder="Seed" />
      <p class="apl-fm-hint">Preset này khoá timeline theo clip mẫu; seed chỉ đổi chi tiết hình sinh ra.</p>
    </div>
  </div>
</template>

<script setup>
const props = defineProps({
  config: { type: Object, required: true },
  nodeType: { type: String, default: 'trend-tiktok' }
})
const emit = defineEmits(['update:config'])

const PRESETS = [
  {
    id: 'paper-rip',
    label: 'Xé giấy',
    hint: '2 look / 1 reveal · theo nhịp trend xé giấy TikTok',
    icon: 'bi-file-earmark-break',
    duration: 6,
    shotCount: 2,
  },
]

function normalizeTrendConfig(cfg = {}) {
  return {
    preset: 'paper-rip',
    duration: 6,
    shotCount: 2,
    aspectRatio: '9:16',
    wanModel: 'wan2.2',
    modelGender: 'auto',
    steps: 20,
    seedMode: 'random',
    audioMode: 'preset',
    ...cfg,
  }
}

const local = ref(normalizeTrendConfig(props.config))
const presetOptions = computed(() => PRESETS)

function applyPreset(presetId) {
  const preset = PRESETS.find((p) => p.id === presetId)
  if (!preset) return
  local.value.preset = preset.id
  local.value.duration = preset.duration
  local.value.shotCount = preset.shotCount
}

watch(local, (v) => emit('update:config', { ...normalizeTrendConfig(v), aspectRatio: '9:16', wanModel: 'wan2.2' }), { deep: true })
watch(() => props.config, (v) => {
  if (v && JSON.stringify(v) !== JSON.stringify(local.value)) local.value = normalizeTrendConfig({ ...local.value, ...v })
})
watch(() => local.value.preset, (id) => applyPreset(id), { immediate: true })
</script>

<style scoped>
.apl-info-card { background: rgba(255,45,85,0.07); border: 0.5px solid rgba(255,45,85,0.25); border-radius: 12px; padding: 11px 12px; }
.apl-fm-group { background: var(--apl-fill); border: 0.5px solid rgba(235,236,240,0.12); border-radius: 14px; padding: 12px; }
.apl-fm-heading { font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; color: var(--apl-label); margin-bottom: 8px; }
.apl-fm-hint { margin-top: 6px; font-size: 10.5px; color: var(--apl-label); line-height: 1.4; }
.apl-fm-input { width: 100%; height: 34px; padding: 0 10px; background: var(--apl-bg-secondary); border: 0.5px solid rgba(235,236,240,0.18); border-radius: 9px; font-size: 12px; transition: border-color 0.18s; }
.apl-fm-input:focus { outline: none; border-color: #FF2D55; }
.apl-fm-tile { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 2px; height: 50px; border-radius: 12px; border: 0.5px solid rgba(235,236,240,0.18); background: var(--apl-bg-secondary); color: var(--apl-label); transition: all 0.15s; }
.apl-fm-tile:hover { border-color: rgba(255,45,85,0.4); }
.apl-fm-tile.is-active { border-color: #FF2D55; background: rgba(255,45,85,0.07); color: #A11D38; box-shadow: 0 0 0 1px #FF2D55 inset; }
.apl-fm-tile-label { font-size: 12px; font-weight: 700; }
.apl-preset { width: 100%; text-align: left; display: flex; flex-direction: column; gap: 4px; padding: 10px 11px; border: 0.5px solid rgba(235,236,240,0.16); border-radius: 12px; background: var(--apl-bg-secondary); color: var(--apl-label); transition: all 0.15s; }
.apl-preset:hover { border-color: rgba(255,45,85,0.38); }
.apl-preset.is-active { border-color: #FF2D55; background: rgba(255,45,85,0.07); color: #A11D38; box-shadow: 0 0 0 1px #FF2D55 inset; }
.apl-preset-sub { font-size: 10.5px; line-height: 1.35; color: var(--apl-label); }
.apl-preset.is-active .apl-preset-sub { color: rgba(161,29,56,0.72); }
.apl-engine-pill { display: flex; align-items: center; gap: 10px; min-height: 46px; padding: 9px 10px; border-radius: 12px; background: rgba(255,45,85,0.07); color: #A11D38; border: 0.5px solid rgba(255,45,85,0.24); }
.apl-engine-pill i { font-size: 17px; }
.apl-engine-pill span { display: flex; flex-direction: column; gap: 1px; line-height: 1.2; }
.apl-engine-pill b { font-size: 12px; }
.apl-engine-pill small { font-size: 10px; opacity: 0.68; }
.apl-toggle-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; padding: 10px 12px; border-radius: 12px; background: rgba(15, 15, 18, 0.78); border: 0.5px solid rgba(235,236,240,0.14); }
.apl-toggle-title { display: block; font-size: 12.5px; font-weight: 700; color: rgba(28,28,30,0.9); }
.apl-toggle-sub { display: block; margin-top: 2px; font-size: 10.5px; color: var(--apl-label); line-height: 1.35; }
.apl-fm-switch { width: 38px; height: 22px; flex-shrink: 0; -webkit-appearance: none; appearance: none; background: rgba(235,236,240,0.22); border-radius: 999px; position: relative; cursor: pointer; transition: background 0.18s; }
.apl-fm-switch:checked { background: #FF2D55; }
.apl-fm-switch::after { content: ''; position: absolute; top: 2px; left: 2px; width: 18px; height: 18px; border-radius: 50%; background: var(--apl-bg-secondary); transition: transform 0.18s; }
.apl-fm-switch:checked::after { transform: translateX(16px); }
</style>
