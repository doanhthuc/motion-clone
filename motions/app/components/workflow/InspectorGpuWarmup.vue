<template>
  <div class="space-y-3">
    <div class="apl-info-card">
      <p class="font-semibold mb-1"><i class="bi bi-lightning-charge-fill text-emerald-500 mr-1" /> GPU Warmup</p>
      <p>Gọi <code>POST /chandra/warmup</code> để chuẩn bị vRAM (Chandra vLLM ~19GB) trước khi OCR. Đặt node này TRƯỚC OCR.</p>
    </div>
    <label class="apl-check-row">
      <input v-model="local.wait_for_healthy" type="checkbox" class="apl-checkbox" />
      <span>
        <span class="apl-check-label">Block đến khi vLLM healthy</span>
        <span class="apl-check-hint"><i class="bi bi-exclamation-triangle-fill text-amber-600 me-1" /><strong>KHÔNG nên bật</strong> — race với Marker idle_stop_sec=30s gây kill vLLM. Default tắt: kick + OCR step submit ngay để Marker giữ vLLM alive.</span>
      </span>
    </label>
    <div v-if="local.wait_for_healthy" class="space-y-2">
      <div>
        <label class="apl-label">Timeout (s)</label>
        <input v-model.number="local.timeout_sec" type="number" min="30" max="900" step="30" class="apl-input mt-1 font-mono text-[12px]" />
        <p class="apl-card-hint">Cold-start vLLM ~4 phút (eta_sec=240). Default 300s.</p>
      </div>
      <label class="apl-check-row">
        <input v-model="local.use_eta" type="checkbox" class="apl-checkbox" />
        <span>
          <span class="apl-check-label">Dùng ETA động</span>
          <span class="apl-check-hint">Lấy <code>eta_sec</code> từ Marker warmup response → tự extend timeout nếu cần.</span>
        </span>
      </label>
    </div>
  </div>
</template>
<script setup>
const props = defineProps({ config: { type: Object, required: true } })
const emit = defineEmits(['update:config'])
const local = reactive({
  wait_for_healthy: props.config.wait_for_healthy === true,
  timeout_sec: Number(props.config.timeout_sec) > 0 ? Number(props.config.timeout_sec) : 300,
  use_eta: props.config.use_eta !== false
})
watch(local, (v) => emit('update:config', { ...v }), { deep: true })
</script>
<style scoped>
.apl-info-card { font-size: 11px; color: var(--apl-label); background: rgba(52,199,89,0.08); padding: 10px 12px; border-radius: 10px; border: 0.5px solid rgba(52,199,89,0.18); line-height: 1.45; }
.apl-info-card code { background: var(--apl-fill-2); padding: 1px 5px; border-radius: 3px; font-family: ui-monospace, SFMono-Regular, monospace; font-size: 10px; }
.apl-check-row { display: flex; align-items: flex-start; gap: 10px; padding: 10px; background: rgba(118,118,128,0.06); border-radius: 9px; border: 0.5px solid rgba(235,236,240,0.1); cursor: pointer; }
.apl-checkbox { width: 16px; height: 16px; margin-top: 2px; accent-color: #34C759; cursor: pointer; flex-shrink: 0; }
.apl-check-label { display: block; font-size: 12.5px; font-weight: 600; color: var(--apl-label); }
.apl-check-hint { display: block; margin-top: 2px; font-size: 11px; color: var(--apl-label); line-height: 1.4; }
.apl-label { display: block; font-size: 10px; font-weight: 700; color: var(--apl-label); text-transform: uppercase; letter-spacing: 0.04em; }
.apl-input { display: block; width: 100%; padding: 6px 9px; background: var(--apl-bg-secondary); border: 0.5px solid rgba(235,236,240,0.18); border-radius: 7px; font-size: 12px; color: var(--apl-label); outline: none; transition: all 0.15s; font-family: inherit; }
.apl-input:focus { border-color: #34C759; box-shadow: 0 0 0 3px rgba(52,199,89,0.2); }
.apl-card-hint { margin-top: 5px; font-size: 10.5px; color: var(--apl-label); line-height: 1.4; }
</style>
