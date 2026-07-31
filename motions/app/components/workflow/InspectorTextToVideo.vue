<template>
  <!-- ALD 14/06/2026 - Node "Text → Video": CHỈ nhập PROMPT (KHÔNG cần ảnh) → video ngắn.
       Dropdown chọn model text-to-video (Wan2.2 T2V / Wan2.1 T2V). General-purpose: quảng cáo, clip sản phẩm, cảnh đẹp. -->
  <div class="space-y-4">
    <div class="apl-info-card">
      <p class="font-semibold flex items-center gap-1.5"><i class="bi bi-camera-reels" /> Text → Video</p>
      <p class="text-[11px] opacity-70 mt-1">
        Chỉ nhập <b>mô tả cảnh quay</b> (prompt) → AI sinh video ngắn. <b>Không cần ảnh đầu vào.</b>
        Hợp cho clip quảng cáo, sản phẩm, cảnh đẹp. Sweet-spot ~5s; dài hơn render lâu hơn + dễ trôi cảnh.
      </p>
    </div>

    <!-- Model (dropdown icon + tên, không giải thích dài) -->
    <div class="apl-fm-group relative">
      <p class="apl-fm-heading">Model</p>
      <button type="button" class="apl-fm-input w-full flex items-center justify-between gap-2 text-xs" @click="openDrop = !openDrop">
        <span class="flex items-center gap-2"><i :class="['bi', currentModel.icon, 'text-rose-500']" />{{ currentModel.label }}</span>
        <i :class="['bi bi-chevron-down text-gray-400 transition-transform', openDrop && 'rotate-180']" />
      </button>
      <template v-if="openDrop">
        <div class="fixed inset-0 z-10" @click="openDrop = false" />
        <div class="absolute left-3 right-3 z-20 mt-1 bg-gray-50 border border-gray-200 rounded-lg shadow-lg overflow-hidden py-1">
          <button
            v-for="m in T2V_MODELS" :key="m.id" type="button"
            :class="['w-full flex items-center gap-2 px-3 py-2 text-xs text-left hover:bg-gray-50 transition', local.model === m.id && 'bg-rose-50 text-rose-700 font-semibold']"
            @click="local.model = m.id; openDrop = false"
          ><i :class="['bi', m.icon]" />{{ m.label }}</button>
        </div>
      </template>
    </div>

    <!-- Prompt -->
    <div class="apl-fm-group">
      <p class="apl-fm-heading">Mô tả cảnh quay (prompt)</p>
      <textarea v-model="local.prompt" rows="4" class="apl-fm-input" style="height:auto;padding:8px 10px;font-family:inherit;line-height:1.5;resize:vertical"
        placeholder="VD: Flycam bay qua nhà máy thép hiện đại lúc hoàng hôn, ánh sáng ấm, chuyển động mượt, điện ảnh, chân thực." />
      <p class="apl-fm-hint">Mô tả càng cụ thể (chủ thể, chuyển động máy, ánh sáng) → cảnh càng đúng ý.</p>
    </div>

    <!-- Negative (tuỳ chọn) -->
    <div class="apl-fm-group">
      <p class="apl-fm-heading">Tránh xuất hiện <span class="opacity-50 normal-case font-medium">(tuỳ chọn)</span></p>
      <input v-model="local.negativePrompt" type="text" class="apl-fm-input" placeholder="VD: chữ, watermark, mờ, méo…" />
    </div>

    <!-- Thời lượng -->
    <div class="apl-fm-group">
      <p class="apl-fm-heading">Thời lượng</p>
      <div class="grid grid-cols-3 gap-1.5">
        <button v-for="d in DURATIONS" :key="d.v" type="button"
          :class="['apl-fm-tile', Number(local.duration) === d.v && 'is-active']" @click="local.duration = d.v">
          <i class="bi bi-clock text-base" /><span class="apl-fm-tile-label">{{ d.label }}</span>
        </button>
      </div>
      <p class="apl-fm-hint">~5s là tối ưu. 10/15s tự bật RIFLEx chống slow-motion (render lâu hơn chút).</p>
    </div>

    <!-- Tỉ lệ khung -->
    <div class="apl-fm-group">
      <p class="apl-fm-heading">Tỉ lệ khung</p>
      <div class="grid grid-cols-3 gap-1.5">
        <button v-for="r in ASPECTS" :key="r.id" type="button"
          :class="['apl-fm-tile', local.aspectRatio === r.id && 'is-active']" @click="local.aspectRatio = r.id">
          <span class="apl-ar-icon" :style="{ width: r.w + 'px', height: r.h + 'px' }" />
          <span class="apl-fm-tile-label">{{ r.label }}</span>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
const props = defineProps({
  config: { type: Object, required: true },
  nodeType: { type: String, default: 'text-to-video' }
})
const emit = defineEmits(['update:config'])

// ALD 14/06/2026 - id KHỚP T2V_MODELS worker (linux.py). Chỉ icon + tên ngắn (không giải thích model).
// ALD 03/07/2026 - wan2.2 = MẶC ĐỊNH: dual-model + LoRA distill 4 bước (lightx2v) — nhanh, cfg 1.0, đỡ cháy sáng.
const T2V_MODELS = [
  { id: 'wan2.2', label: 'Wan2.2 T2V (distill 4 bước)', icon: 'bi-stars' },
  { id: 'wan2.1', label: 'Wan2.1 T2V (cũ, chậm)', icon: 'bi-camera-reels' },
]
const DURATIONS = [
  { v: 5, label: '5 giây' },
  { v: 10, label: '10 giây' },
  { v: 15, label: '15 giây' },
]
const ASPECTS = [
  { id: '16:9', label: '16:9', w: 24, h: 14 },
  { id: '9:16', label: '9:16', w: 13, h: 22 },
  { id: '1:1',  label: '1:1',  w: 20, h: 20 },
]

const local = ref({
  model: 'wan2.2',         // wan2.2 (MẶC ĐỊNH 03/07/2026: MoE HIGH+LOW + LoRA distill 4 bước) | wan2.1 (cũ)
  prompt: '',
  negativePrompt: '',
  duration: 5,             // giây — worker quy ra frame ở WAN_T2V_FPS
  aspectRatio: '16:9',
  ...props.config,
})
const openDrop = ref(false)
const currentModel = computed(() => T2V_MODELS.find((m) => m.id === local.value.model) || T2V_MODELS[0])

watch(local, (v) => emit('update:config', { ...v }), { deep: true })
watch(() => props.config, (v) => {
  if (v && JSON.stringify(v) !== JSON.stringify(local.value)) local.value = { ...local.value, ...v }
})
</script>

<style scoped>
.apl-info-card { background: rgba(255,45,85,0.07); border: 0.5px solid rgba(255,45,85,0.25); border-radius: 12px; padding: 11px 12px; }
.apl-fm-group { background: var(--apl-fill); border: 0.5px solid rgba(235,236,240,0.12); border-radius: 14px; padding: 12px; }
.apl-fm-heading { font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; color: var(--apl-label); margin-bottom: 8px; }
.apl-fm-hint { margin-top: 6px; font-size: 10.5px; color: var(--apl-label); line-height: 1.4; }
.apl-fm-input { width: 100%; height: 34px; padding: 0 10px; background: var(--apl-bg-secondary); border: 0.5px solid rgba(235,236,240,0.18); border-radius: 9px; font-size: 12px; transition: border-color 0.18s; }
.apl-fm-input:focus { outline: none; border-color: #FF2D55; }
.apl-fm-tile { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 4px; height: 52px; border-radius: 12px; border: 0.5px solid rgba(235,236,240,0.18); background: var(--apl-bg-secondary); color: var(--apl-label); transition: all 0.15s; }
.apl-fm-tile:hover { border-color: rgba(255,45,85,0.4); }
.apl-fm-tile.is-active { border-color: #FF2D55; background: rgba(255,45,85,0.08); color: #C81E45; box-shadow: 0 0 0 1px #FF2D55 inset; }
.apl-fm-tile-label { font-size: 11px; font-weight: 700; }
.apl-ar-icon { display: block; border: 1.5px solid currentColor; border-radius: 3px; opacity: 0.7; }
</style>
