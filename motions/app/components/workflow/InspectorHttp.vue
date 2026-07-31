<template>
  <!-- #region ALD 22/05/2026 - HTTP node inspector — method/URL/headers/body
       Template support: {{text}} → prev.text, {{metadata.key}} → prev.metadata.key -->
  <div class="space-y-3">
    <!-- Method + URL -->
    <div>
      <label class="apl-field-label">Endpoint</label>
      <div class="flex gap-1.5 mt-1.5">
        <select v-model="local.method" class="apl-method-select">
          <option v-for="m in METHODS" :key="m" :value="m">{{ m }}</option>
        </select>
        <input v-model="local.url" type="text" placeholder="https://api.example.com/path" class="apl-input flex-1 font-mono text-[12px]" />
      </div>
      <p class="apl-field-hint">Hỗ trợ <code v-pre>{{text}}</code> + <code v-pre>{{metadata.key}}</code></p>
    </div>

    <!-- Headers -->
    <div>
      <div class="flex items-center justify-between">
        <label class="apl-field-label">Headers</label>
        <button type="button" class="apl-mini-btn" @click="addHeader">
          <i class="bi bi-plus" /> Thêm
        </button>
      </div>
      <div v-if="headerEntries.length === 0" class="apl-empty-row">Chưa có header.</div>
      <div v-else class="space-y-1.5 mt-1.5">
        <div v-for="(h, idx) in headerEntries" :key="idx" class="flex items-center gap-1">
          <input v-model="h.key" type="text" placeholder="Key" class="apl-input flex-1 font-mono text-[11px]" @input="syncHeaders" />
          <input v-model="h.value" type="text" placeholder="Value" class="apl-input flex-1 font-mono text-[11px]" @input="syncHeaders" />
          <button type="button" class="apl-icon-btn-mini" @click="removeHeader(idx)"><i class="bi bi-x-lg" /></button>
        </div>
      </div>
      <p class="apl-field-hint mt-1">Vd: <code v-pre>Authorization: Bearer {{metadata.token}}</code></p>
    </div>

    <!-- Body (chỉ cho non-GET) -->
    <div v-if="local.method !== 'GET' && local.method !== 'HEAD'">
      <label class="apl-field-label">Body</label>
      <textarea
        v-model="local.body"
        rows="6"
        spellcheck="false"
        placeholder='{"text": "{{text}}"}'
        class="apl-input font-mono text-[11px] mt-1.5"
      />
      <p class="apl-field-hint">JSON hoặc plain text. Template <code v-pre>{{text}}</code> sẽ replace với output node trước.</p>
    </div>

    <!-- Timeout -->
    <div>
      <label class="apl-field-label">Timeout (ms)</label>
      <input v-model.number="local.timeout" type="number" min="1000" max="120000" step="1000" class="apl-input mt-1.5" />
    </div>

    <!-- Tip -->
    <div class="apl-info-card">
      <p class="font-semibold">Output node này</p>
      <ul class="mt-1 space-y-0.5 list-disc list-inside">
        <li><code>output.text</code> = response body (string)</li>
        <li><code>output.metadata.status</code> = HTTP status</li>
        <li><code>output.metadata.json</code> = parsed JSON (nếu response là JSON)</li>
      </ul>
    </div>
  </div>
  <!-- #endregion -->
</template>

<script setup>
const props = defineProps({ config: { type: Object, required: true } })
const emit = defineEmits(['update:config'])

const METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD']

const local = reactive({
  method: props.config.method || 'POST',
  url: props.config.url || '',
  headers: { ...(props.config.headers || {}) },
  body: props.config.body || '',
  timeout: props.config.timeout ?? 30000
})

// Headers as array {key, value} cho UI editing — sync về object khi emit
const headerEntries = ref(
  Object.entries(local.headers).map(([key, value]) => ({ key, value }))
)
function addHeader() { headerEntries.value.push({ key: '', value: '' }); syncHeaders() }
function removeHeader(idx) { headerEntries.value.splice(idx, 1); syncHeaders() }
function syncHeaders() {
  const obj = {}
  for (const h of headerEntries.value) {
    if (h.key && h.key.trim()) obj[h.key.trim()] = h.value
  }
  local.headers = obj
}

watch(local, (v) => emit('update:config', { ...v, headers: { ...v.headers } }), { deep: true })
</script>

<style scoped>
.apl-field-label {
  display: block;
  font-size: 11px;
  font-weight: 700;
  color: var(--apl-label);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.apl-field-hint {
  font-size: 11px;
  color: var(--apl-label);
  margin-top: 4px;
  line-height: 1.4;
}
.apl-field-hint code {
  background: rgba(118, 118, 128, 0.12);
  padding: 1px 4px;
  border-radius: 4px;
  font-size: 10.5px;
}
.apl-input {
  display: block;
  width: 100%;
  padding: 7px 10px;
  background: var(--apl-bg-secondary);
  border: 0.5px solid rgba(235, 236, 240, 0.18);
  border-radius: 8px;
  font-size: 13px;
  color: var(--apl-label);
  outline: none;
  transition: all 0.15s;
  font-family: inherit;
}
.apl-input:focus {
  border-color: #007AFF;
  box-shadow: 0 0 0 3px rgba(0, 122, 255, 0.2);
}
.apl-method-select {
  padding: 7px 8px;
  background: var(--apl-bg-secondary);
  border: 0.5px solid rgba(235, 236, 240, 0.18);
  border-radius: 8px;
  font-size: 12px;
  font-weight: 700;
  font-family: ui-monospace, SFMono-Regular, monospace;
  color: var(--apl-label);
  cursor: pointer;
  outline: none;
}
.apl-method-select:focus { border-color: #007AFF; box-shadow: 0 0 0 3px rgba(0, 122, 255, 0.2); }

.apl-mini-btn {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 11px;
  font-weight: 600;
  color: #007AFF;
  background: transparent;
  border: none;
  padding: 2px 6px;
  border-radius: 6px;
  cursor: pointer;
}
.apl-mini-btn:hover { background: rgba(0, 122, 255, 0.08); }

.apl-icon-btn-mini {
  flex-shrink: 0;
  width: 26px;
  height: 26px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: none;
  border-radius: 6px;
  color: var(--apl-label);
  cursor: pointer;
  font-size: 11px;
}
.apl-icon-btn-mini:hover { background: rgba(255,59,48,0.15); color: #FF3B30; }

.apl-empty-row {
  margin-top: 6px;
  padding: 8px;
  font-size: 11px;
  color: var(--apl-label);
  font-style: italic;
  background: rgba(118, 118, 128, 0.06);
  border-radius: 6px;
  text-align: center;
}

.apl-info-card {
  font-size: 11px;
  color: #8FBAF0;
  background: rgba(10,132,255,0.15);
  padding: 10px 12px;
  border-radius: 10px;
  border: 0.5px solid rgba(0, 122, 255, 0.15);
  line-height: 1.5;
}
.apl-info-card code {
  background: rgba(0, 122, 255, 0.12);
  padding: 1px 4px;
  border-radius: 4px;
  font-size: 10.5px;
}
</style>
