<template>
  <div class="space-y-3">
    <div class="apl-info-card">
      <p class="font-semibold mb-1"><i class="bi bi-check2-square text-emerald-600 mr-1" /> Validate</p>
      <p>Parse JSON từ <code>prev.text</code> + check required fields + math rules. Pass-through metadata <code>validation</code>.</p>
    </div>

    <!-- Strict mode -->
    <label class="apl-check-row">
      <input v-model="local.strict" type="checkbox" class="apl-checkbox" />
      <span>
        <span class="apl-check-label">Strict mode</span>
        <span class="apl-check-hint">Fail → throw error, workflow dừng. Tắt → warn-only, vẫn pass output.</span>
      </span>
    </label>

    <!-- Required fields -->
    <div>
      <div class="flex items-center justify-between mb-1">
        <label class="apl-label">Required fields <span class="apl-mute">(dot-path)</span></label>
        <button type="button" class="apl-mini-btn" @click="addField"><i class="bi bi-plus-lg" /> Thêm</button>
      </div>
      <div class="space-y-1.5">
        <div v-for="(_, idx) in local.required_fields" :key="`f-${idx}`" class="apl-row">
          <input v-model="local.required_fields[idx]" type="text" class="apl-input font-mono text-[12px]" placeholder="invoice.no" />
          <button type="button" class="apl-icon-btn-mini" @click="local.required_fields.splice(idx, 1)"><i class="bi bi-x-lg" /></button>
        </div>
        <p v-if="local.required_fields.length === 0" class="apl-empty-list">Chưa có field — workflow chỉ check math.</p>
      </div>
    </div>

    <!-- Math checks -->
    <div>
      <div class="flex items-center justify-between mb-1">
        <label class="apl-label">Math checks</label>
        <button type="button" class="apl-mini-btn" @click="addMath"><i class="bi bi-plus-lg" /> Thêm</button>
      </div>
      <div class="space-y-2">
        <div v-for="(m, idx) in local.math_checks" :key="`m-${idx}`" class="apl-math-card">
          <div class="flex items-center gap-1">
            <input v-model="m.name" type="text" class="apl-input apl-name-input text-[11.5px]" placeholder="Tên check..." />
            <button type="button" class="apl-icon-btn-mini" @click="local.math_checks.splice(idx, 1)"><i class="bi bi-x-lg" /></button>
          </div>
          <label class="apl-sub-label mt-1">Formula (JS, access via <code>data.field</code>)</label>
          <input v-model="m.formula" type="text" class="apl-input mt-0.5 font-mono text-[11px]" placeholder="data.totals.base * 0.08" />
          <div class="flex items-center gap-2 mt-1">
            <div class="flex-1">
              <label class="apl-sub-label">Expected path</label>
              <input v-model="m.expected_path" type="text" class="apl-input mt-0.5 font-mono text-[11px]" placeholder="totals.vat" />
            </div>
            <div class="w-20">
              <label class="apl-sub-label">Tolerance ±</label>
              <input v-model.number="m.tolerance" type="number" min="0" step="0.5" class="apl-input mt-0.5 font-mono text-[11px]" />
            </div>
          </div>
        </div>
        <p v-if="local.math_checks.length === 0" class="apl-empty-list">Chưa có math check.</p>
      </div>
    </div>

    <div class="apl-tip-card">
      <p class="font-semibold mb-1"><i class="bi bi-lightbulb mr-1" /> Ví dụ hóa đơn VAT</p>
      <p>Required: <code>invoice_no</code>, <code>seller.tax_id</code>, <code>totals.total_amount</code></p>
      <p>Math: <code>data.totals.total_before_vat * data.totals.vat_rate / 100</code> = <code>totals.vat_amount</code></p>
    </div>
  </div>
</template>
<script setup>
const props = defineProps({ config: { type: Object, required: true } })
const emit = defineEmits(['update:config'])
const local = reactive({
  required_fields: Array.isArray(props.config.required_fields) ? [...props.config.required_fields] : [],
  math_checks: Array.isArray(props.config.math_checks)
    ? props.config.math_checks.map((m) => ({ ...m }))
    : [],
  strict: props.config.strict === true
})
function addField() { local.required_fields.push('') }
function addMath() { local.math_checks.push({ name: '', formula: '', expected_path: '', tolerance: 1 }) }
watch(local, (v) => emit('update:config', JSON.parse(JSON.stringify(v))), { deep: true })
</script>
<style scoped>
.apl-info-card { font-size: 11px; color: var(--apl-label); background: rgba(31,125,56,0.08); padding: 10px 12px; border-radius: 10px; border: 0.5px solid rgba(31,125,56,0.18); line-height: 1.45; }
.apl-info-card code { background: var(--apl-fill-2); padding: 1px 4px; border-radius: 3px; font-family: ui-monospace, SFMono-Regular, monospace; font-size: 10px; }
.apl-check-row { display: flex; align-items: flex-start; gap: 10px; padding: 10px; background: rgba(118,118,128,0.06); border-radius: 9px; border: 0.5px solid rgba(235,236,240,0.1); cursor: pointer; }
.apl-checkbox { width: 16px; height: 16px; margin-top: 2px; accent-color: #7CDDAA; cursor: pointer; flex-shrink: 0; }
.apl-check-label { display: block; font-size: 12.5px; font-weight: 600; color: var(--apl-label); }
.apl-check-hint { display: block; margin-top: 2px; font-size: 11px; color: var(--apl-label); line-height: 1.4; }
.apl-label { display: block; font-size: 10px; font-weight: 700; color: var(--apl-label); text-transform: uppercase; letter-spacing: 0.04em; }
.apl-sub-label { display: block; font-size: 9.5px; font-weight: 600; color: var(--apl-label); text-transform: uppercase; letter-spacing: 0.04em; }
.apl-mute { font-weight: 400; opacity: 0.6; text-transform: none; }
.apl-input { display: block; width: 100%; padding: 5px 8px; background: var(--apl-bg-secondary); border: 0.5px solid rgba(235,236,240,0.18); border-radius: 6px; font-size: 12px; color: var(--apl-label); outline: none; transition: all 0.15s; font-family: inherit; }
.apl-input:focus { border-color: #7CDDAA; box-shadow: 0 0 0 3px rgba(31,125,56,0.2); }
.apl-row { display: flex; align-items: center; gap: 4px; }
.apl-math-card { padding: 8px; background: rgba(31,125,56,0.04); border: 0.5px solid rgba(31,125,56,0.15); border-radius: 8px; }
.apl-name-input { flex: 1; font-weight: 600; }
.apl-mini-btn { display: inline-flex; align-items: center; gap: 3px; padding: 2px 8px; background: rgba(31,125,56,0.1); border: none; border-radius: 6px; font-size: 10.5px; font-weight: 600; color: #7CDDAA; cursor: pointer; font-family: inherit; }
.apl-mini-btn:hover { background: rgba(31,125,56,0.18); }
.apl-icon-btn-mini { width: 22px; height: 22px; display: inline-flex; align-items: center; justify-content: center; background: transparent; border: none; border-radius: 5px; color: var(--apl-label); cursor: pointer; font-size: 10px; flex-shrink: 0; }
.apl-icon-btn-mini:hover { background: rgba(255,59,48,0.1); color: #FF3B30; }
.apl-empty-list { font-size: 10.5px; color: var(--apl-label); font-style: italic; padding: 4px 0; }
.apl-tip-card { font-size: 11px; color: rgba(168,98,0,0.85); background: rgba(255,149,0,0.15); padding: 8px 10px; border-radius: 8px; border: 0.5px solid rgba(255,149,0,0.25); line-height: 1.5; }
.apl-tip-card code { background: var(--apl-fill-2); padding: 1px 4px; border-radius: 3px; font-family: ui-monospace, SFMono-Regular, monospace; font-size: 10px; }
</style>
