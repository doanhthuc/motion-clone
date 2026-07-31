<template>
  <div class="space-y-3">
    <div class="apl-info-card">
      <p class="font-semibold mb-1"><i class="bi bi-arrow-down-circle-fill text-gray-500 mr-1" /> GPU Free</p>
      <p>Đặt TRƯỚC các node render nặng để nhả VRAM khỏi Ollama/Chandra. Nếu vừa dùng AI dàn cảnh bằng Ollama, node này sẽ unload model chat trước khi ComfyUI render.</p>
    </div>
    <div class="grid grid-cols-3 gap-1.5">
      <label :class="['apl-check-tile', local.free_ollama && 'is-on']">
        <input v-model="local.free_ollama" type="checkbox" class="sr-only" />
        <i class="bi bi-cpu" />
        <span>Ollama</span>
      </label>
      <label :class="['apl-check-tile', local.free_chandra && 'is-on']">
        <input v-model="local.free_chandra" type="checkbox" class="sr-only" />
        <i class="bi bi-file-earmark-text" />
        <span>Chandra</span>
      </label>
      <label :class="['apl-check-tile', local.free_comfy && 'is-on']">
        <input v-model="local.free_comfy" type="checkbox" class="sr-only" />
        <i class="bi bi-gpu-card" />
        <span>Comfy</span>
      </label>
    </div>
    <div>
      <label class="apl-label">Ollama models <span class="normal-case font-medium opacity-50">(trống = unload tất cả model đang loaded)</span></label>
      <input v-model="local.ollama_models" type="text" class="apl-input mt-1 font-mono text-[12px]" placeholder="vd: qwen3.6:35b, qwen2.5:7b" />
    </div>
    <div class="grid grid-cols-2 gap-2">
      <div>
        <label class="apl-label">Max wait (s)</label>
        <input v-model.number="local.max_wait_sec" type="number" min="10" max="180" step="5" class="apl-input mt-1 font-mono text-[12px]" />
        <p class="apl-card-hint">Default 45s = 30s idle + buffer.</p>
      </div>
      <div>
        <label class="apl-label">Poll interval (s)</label>
        <input v-model.number="local.poll_interval_sec" type="number" min="1" max="30" step="1" class="apl-input mt-1 font-mono text-[12px]" />
        <p class="apl-card-hint">Tần suất check status.</p>
      </div>
    </div>
  </div>
</template>
<script setup>
const props = defineProps({ config: { type: Object, required: true } })
const emit = defineEmits(['update:config'])
const local = reactive({
  max_wait_sec: Number(props.config.max_wait_sec) > 0 ? Number(props.config.max_wait_sec) : 45,
  poll_interval_sec: Number(props.config.poll_interval_sec) > 0 ? Number(props.config.poll_interval_sec) : 3,
  free_ollama: props.config.free_ollama !== false,
  free_chandra: props.config.free_chandra !== false,
  free_comfy: props.config.free_comfy === true,
  ollama_models: props.config.ollama_models || ''
})
watch(local, (v) => emit('update:config', { ...v }), { deep: true })
</script>
<style scoped>
.apl-info-card { font-size: 11px; color: var(--apl-label); background: rgba(118,118,128,0.08); padding: 10px 12px; border-radius: 10px; border: 0.5px solid rgba(235,236,240,0.18); line-height: 1.45; }
.apl-info-card code { background: var(--apl-fill-2); padding: 1px 5px; border-radius: 3px; font-family: ui-monospace, SFMono-Regular, monospace; font-size: 10px; }
.apl-label { display: block; font-size: 10px; font-weight: 700; color: var(--apl-label); text-transform: uppercase; letter-spacing: 0.04em; }
.apl-input { display: block; width: 100%; padding: 6px 9px; background: var(--apl-bg-secondary); border: 0.5px solid rgba(235,236,240,0.18); border-radius: 7px; font-size: 12px; color: var(--apl-label); outline: none; transition: all 0.15s; font-family: inherit; }
.apl-input:focus { border-color: #007AFF; box-shadow: 0 0 0 3px rgba(0,122,255,0.2); }
.apl-card-hint { margin-top: 5px; font-size: 10.5px; color: var(--apl-label); line-height: 1.4; }
.apl-check-tile { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 4px; min-height: 48px; border-radius: 10px; border: 0.5px solid rgba(235,236,240,0.18); background: var(--apl-bg-secondary); color: var(--apl-label); font-size: 10.5px; font-weight: 800; cursor: pointer; transition: all 0.15s; }
.apl-check-tile i { font-size: 15px; }
.apl-check-tile.is-on { border-color: #007AFF; color: #007AFF; background: rgba(0,122,255,0.07); box-shadow: 0 0 0 1px #007AFF inset; }
</style>
