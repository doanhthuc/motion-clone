<template>
  <div class="space-y-3">
    <div>
      <label class="apl-label">Expression (JS)</label>
      <textarea v-model="local.expression" rows="2" class="apl-input mt-1.5 font-mono" placeholder="text.length > 100" />
      <p class="apl-hint">Có sẵn <code>text</code> (string) + <code>metadata</code> (object) từ node trước.</p>
    </div>

    <div class="apl-info-card">
      <p class="font-semibold mb-1">Ví dụ:</p>
      <ul class="space-y-1 list-disc list-inside">
        <li><code>text.length > 100</code></li>
        <li><code>text.includes("hóa đơn")</code></li>
        <li><code>metadata.page_count >= 5</code></li>
        <li><code>/\d{4}/.test(text)</code></li>
      </ul>
    </div>

    <div class="apl-warn-card">
      <p class="font-semibold"><i class="bi bi-info-circle mr-1" />Wire 2 nhánh</p>
      <p class="mt-0.5">Từ node này có 2 output handle: <b class="text-emerald-700">TRUE</b> (trên) và <b class="text-rose-700">FALSE</b> (dưới). Nối mỗi handle sang 1 node để engine pick branch tương ứng.</p>
    </div>
  </div>
</template>
<script setup>
const props = defineProps({ config: { type: Object, required: true } })
const emit = defineEmits(['update:config'])
const local = reactive({ expression: props.config.expression || 'text.length > 0' })
watch(local, (v) => emit('update:config', { ...v }), { deep: true })
</script>
<style scoped>
.apl-label { display: block; font-size: 11px; font-weight: 700; color: var(--apl-label); text-transform: uppercase; letter-spacing: 0.04em; }
.apl-hint  { font-size: 11px; color: var(--apl-label); margin-top: 4px; line-height: 1.4; }
.apl-hint code { background: rgba(118,118,128,0.12); padding: 1px 4px; border-radius: 4px; font-size: 10.5px; }
.apl-input { display: block; width: 100%; padding: 8px 10px; background: var(--apl-bg-secondary); border: 0.5px solid rgba(235,236,240,0.18); border-radius: 8px; font-size: 13px; color: var(--apl-label); outline: none; transition: all 0.15s; font-family: inherit; }
.apl-input:focus { border-color: #007AFF; box-shadow: 0 0 0 3px rgba(0,122,255,0.2); }
.apl-info-card { font-size: 11px; color: #8FBAF0; background: rgba(10,132,255,0.15); padding: 10px 12px; border-radius: 10px; border: 0.5px solid rgba(0,122,255,0.15); }
.apl-info-card code { background: rgba(0,122,255,0.12); padding: 1px 4px; border-radius: 4px; font-size: 10.5px; }
.apl-warn-card { font-size: 11px; color: #A86200; background: rgba(255,149,0,0.15); padding: 10px 12px; border-radius: 10px; border: 0.5px solid rgba(255,149,0,0.2); }
</style>
