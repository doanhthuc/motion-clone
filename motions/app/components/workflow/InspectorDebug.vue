<template>
  <!-- #region ALD 24/05/2026 - Debug node inspector v2: tập trung vào kết quả + log + performance -->
  <div class="space-y-3">
    <!-- Label compact -->
    <div>
      <input
        v-model="local.label"
        type="text"
        class="apl-dbg-input"
        placeholder="Tên debug step"
      />
    </div>

    <!-- Capture toggles — icon-only segmented -->
    <div class="apl-dbg-toggles">
      <button
        v-for="t in TOGGLES"
        :key="t.key"
        type="button"
        :class="['apl-dbg-toggle', local[t.key] && 'is-on']"
        :title="t.label"
        @click="local[t.key] = !local[t.key]"
      >
        <i :class="['bi', t.icon]" />
      </button>
    </div>

    <!-- RESULT — latest capture preview -->
    <div class="apl-dbg-section">
      <div class="apl-dbg-section-header">
        <span><i class="bi bi-eye me-1" /> Kết quả</span>
        <span v-if="captureSize" class="apl-dbg-meta">{{ captureSize }}</span>
      </div>
      <div class="apl-dbg-preview">
        <!-- ALD 25/05/2026 - Multi-image grid cho debug-tryon: hiện đủ Model / Product /
             Mask / Tryon side-by-side để user validate mask coverage trước Stage 2. -->
        <div v-if="previewGrid.length > 1" class="apl-dbg-grid">
          <figure v-for="item in previewGrid" :key="item.key" class="apl-dbg-grid-item">
            <img :src="item.url" :alt="item.label" loading="lazy" />
            <figcaption>{{ item.label }}</figcaption>
          </figure>
        </div>
        <video v-else-if="resultVideo" :src="resultVideo" controls preload="metadata" />
        <img   v-else-if="resultImage" :src="resultImage" alt="" />
        <audio v-else-if="resultAudio" :src="resultAudio" controls />
        <pre v-else-if="resultText" class="apl-dbg-pre">{{ resultText }}</pre>
        <div v-else class="apl-dbg-empty">
          <i class="bi bi-hourglass-split" />
          <span>Chưa có kết quả — chạy workflow để xem</span>
        </div>
      </div>
      <div v-if="resultMeta.length" class="apl-dbg-kvs">
        <div v-for="kv in resultMeta" :key="kv.k" class="apl-dbg-kv">
          <span class="apl-dbg-kv-k">{{ kv.k }}</span>
          <span class="apl-dbg-kv-v">{{ kv.v }}</span>
        </div>
      </div>
    </div>

    <!-- LOG — events từ node này -->
    <div class="apl-dbg-section">
      <div class="apl-dbg-section-header">
        <span><i class="bi bi-journal-text me-1" /> Log</span>
        <span class="apl-dbg-meta">{{ events.length }} event{{ events.length !== 1 ? 's' : '' }}</span>
      </div>
      <div class="apl-dbg-log">
        <div v-if="!events.length" class="apl-dbg-empty py-3">
          <i class="bi bi-circle" />
          <span>Empty — log sẽ điền sau khi run</span>
        </div>
        <div v-for="(ev, i) in events.slice(-8)" :key="i" :class="['apl-dbg-log-row', `lvl-${ev.level}`]">
          <span class="apl-dbg-log-time">{{ fmtTime(ev.ts) }}</span>
          <span class="apl-dbg-log-lvl">{{ ev.level }}</span>
          <span class="apl-dbg-log-msg" :title="ev.msg">{{ ev.msg }}</span>
        </div>
      </div>
    </div>

    <!-- PERFORMANCE -->
    <div v-if="perfRows.length" class="apl-dbg-section">
      <div class="apl-dbg-section-header">
        <span><i class="bi bi-speedometer2 me-1" /> Performance</span>
      </div>
      <div class="apl-dbg-perf">
        <div v-for="p in perfRows" :key="p.k" class="apl-dbg-perf-row">
          <span class="apl-dbg-perf-k">{{ p.k }}</span>
          <span class="apl-dbg-perf-v">{{ p.v }}</span>
        </div>
      </div>
    </div>
  </div>
  <!-- #endregion -->
</template>

<script setup>
const props = defineProps({
  config: { type: Object, required: true },
  nodeType: { type: String, default: 'debug' },
  // Engine-provided runtime data — node data._runOutput / _runEvents nếu workflow editor pass.
  runtime: { type: Object, default: () => ({}) }
})
const emit = defineEmits(['update:config'])

const local = reactive({
  label: props.config.label || 'Debug step',
  captureImage: props.config.captureImage !== false,
  captureVideo: props.config.captureVideo !== false,
  captureAudio: props.config.captureAudio || false,
  captureText: props.config.captureText !== false
})
watch(local, (v) => emit('update:config', { ...v }), { deep: true })

const TOGGLES = [
  { key: 'captureImage', icon: 'bi-image',          label: 'Capture image' },
  { key: 'captureVideo', icon: 'bi-film',           label: 'Capture video' },
  { key: 'captureAudio', icon: 'bi-music-note-beamed', label: 'Capture audio' },
  { key: 'captureText',  icon: 'bi-chat-left-text', label: 'Capture text' }
]

// ─── RESULT: đọc từ runtime ─────────────────────────────────────────────
// runtime shape mong đợi: { output: NodeOutput, events: [{ts, level, msg, extra}], durationMs }
const out = computed(() => props.runtime?.output || {})
const meta = computed(() => out.value?.metadata || {})
const resultVideo = computed(() => meta.value.video || (out.value?.file?.mimeType?.startsWith('video/') ? buildDataUrl(out.value.file) : ''))
const resultImage = computed(() => meta.value.image || (out.value?.file?.mimeType?.startsWith('image/') ? buildDataUrl(out.value.file) : ''))
const resultAudio = computed(() => out.value?.file?.mimeType?.startsWith('audio/') ? buildDataUrl(out.value.file) : '')
const resultText  = computed(() => out.value?.text && !meta.value.video && !meta.value.image ? out.value.text.slice(0, 800) : '')
function buildDataUrl(f) { return f?.data ? `data:${f.mimeType};base64,${f.data}` : '' }

// ALD 25/05/2026 - Grid preview cho debug-tryon: detect Model / Product / Mask / Tryon
// URLs trong metadata, render side-by-side. Bỏ qua entry rỗng để layout không vỡ.
const previewGrid = computed(() => {
  const m = meta.value || {}
  const items = [
    { key: 'model',   label: 'Model',   url: m.model_url },
    { key: 'product', label: 'Product', url: m.product_url },
    { key: 'mask',    label: 'Mask',    url: m.mask_url },
    { key: 'tryon',   label: 'Try-on',  url: m.tryon_url || m.image },
  ].filter((x) => x.url && typeof x.url === 'string' && x.url.startsWith('http'))
  return items
})

const captureSize = computed(() => {
  const f = out.value?.file
  if (f?.data) {
    const kb = Math.round((f.data.length || 0) * 0.75 / 1024)
    return kb >= 1024 ? `${(kb / 1024).toFixed(1)} MB` : `${kb} KB`
  }
  if (out.value?.text) return `${out.value.text.length} chars`
  return ''
})

const resultMeta = computed(() => {
  const m = meta.value
  if (!m || !Object.keys(m).length) return []
  const out = []
  if (m.job_id) out.push({ k: 'job_id', v: String(m.job_id).slice(0, 8) })
  if (m.aspect_ratio) out.push({ k: 'aspect', v: m.aspect_ratio })
  if (m.quality) out.push({ k: 'quality', v: m.quality })
  if (m.pending) out.push({ k: 'state', v: 'pending' })
  if (m.kind) out.push({ k: 'kind', v: m.kind })
  return out
})

// ─── LOG ───────────────────────────────────────────────────────────────
const events = computed(() => props.runtime?.events || [])
function fmtTime(ts) {
  if (!ts) return '--:--'
  return new Date(ts).toLocaleTimeString('vi-VN', { hour12: false })
}

// ─── PERFORMANCE ───────────────────────────────────────────────────────
const perfRows = computed(() => {
  const rows = []
  if (props.runtime?.durationMs != null) {
    rows.push({ k: 'Duration', v: fmtMs(props.runtime.durationMs) })
  }
  if (captureSize.value) rows.push({ k: 'Payload', v: captureSize.value })
  if (events.value.length) {
    const errs = events.value.filter((e) => e.level === 'error').length
    const warns = events.value.filter((e) => e.level === 'warn').length
    if (errs || warns) rows.push({ k: 'Issues', v: `${errs} error${errs !== 1 ? 's' : ''} · ${warns} warn${warns !== 1 ? 's' : ''}` })
  }
  return rows
})
function fmtMs(ms) {
  if (ms == null) return '--'
  if (ms < 1000) return `${ms} ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)} s`
  return `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`
}
</script>

<style scoped>
.apl-dbg-input {
  width: 100%;
  height: 36px;
  padding: 0 12px;
  border-radius: 10px;
  border: 0.5px solid rgba(235,236,240,0.15);
  background: var(--apl-bg-secondary);
  font-size: 13px;
  font-weight: 600;
  letter-spacing: -0.01em;
  color: var(--apl-label);
  transition: border-color 0.15s;
}
.apl-dbg-input:focus { outline: none; border-color: var(--color-primary, #9AA2F2); }

.apl-dbg-toggles {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 2px;
  background: rgba(118,118,128,0.10);
  border-radius: 10px;
  padding: 2px;
}
.apl-dbg-toggle {
  height: 32px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: none;
  border-radius: 8px;
  color: var(--apl-label);
  cursor: pointer;
  font-size: 14px;
  transition: all 0.15s;
}
.apl-dbg-toggle.is-on {
  background: var(--apl-bg-secondary);
  color: var(--apl-label);
  box-shadow: 0 0.5px 1px rgba(0,0,0,0.06), 0 1px 4px rgba(0,0,0,0.08);
}

.apl-dbg-section {
  background: var(--apl-fill);
  border: 0.5px solid rgba(235,236,240,0.10);
  border-radius: 12px;
  padding: 10px 12px;
}
.apl-dbg-section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 10.5px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--apl-label);
  margin-bottom: 8px;
}
.apl-dbg-meta {
  font-size: 10px;
  font-weight: 600;
  color: var(--apl-label);
  font-variant-numeric: tabular-nums;
  text-transform: none;
  letter-spacing: 0;
}

.apl-dbg-preview {
  background: rgba(235,236,240,0.05);
  border-radius: 8px;
  overflow: hidden;
  min-height: 64px;
}
/* ALD 27/05/2026 - Adaptive: width 100% + height auto theo natural ratio content.
   Cap max-height tránh portrait video stretch quá tall. */
.apl-dbg-preview img,
.apl-dbg-preview video {
  width: 100%;
  height: auto;
  max-height: 320px;
  object-fit: cover;
  object-position: center top;
  background: #0a0a0a;
  display: block;
}
.apl-dbg-preview audio { width: 100%; }
.apl-dbg-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 6px;
  padding: 6px;
}
.apl-dbg-grid-item {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  background: var(--apl-bg-secondary);
  border: 0.5px solid rgba(235,236,240,0.10);
  border-radius: 8px;
  overflow: hidden;
}
/* Grid 2x2 cho debug-tryon preview — mỗi cell adaptive, cap height đều nhau */
.apl-dbg-grid-item img {
  width: 100%;
  height: auto;
  max-height: 220px;
  object-fit: cover;
  object-position: center top;
  background: #0a0a0a;
  display: block;
}
.apl-dbg-grid-item figcaption {
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--apl-label);
  text-align: center;
  padding: 4px 6px 6px;
}
.apl-dbg-pre {
  margin: 0;
  padding: 8px 10px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
  color: var(--apl-label);
  white-space: pre-wrap;
  max-height: 160px;
  overflow-y: auto;
}
.apl-dbg-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 16px 8px;
  color: var(--apl-label);
  font-size: 10.5px;
  font-weight: 500;
}
.apl-dbg-empty i { font-size: 18px; }

.apl-dbg-kvs {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}
.apl-dbg-kv {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  background: rgba(118,118,128,0.10);
  border-radius: 999px;
  font-size: 10.5px;
}
.apl-dbg-kv-k {
  font-weight: 700;
  color: var(--apl-label);
}
.apl-dbg-kv-v {
  color: var(--apl-label);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}

.apl-dbg-log { font-size: 10.5px; }
.apl-dbg-log-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 2px;
  border-top: 0.5px solid rgba(235,236,240,0.06);
}
.apl-dbg-log-row:first-child { border-top: none; }
.apl-dbg-log-time {
  flex-shrink: 0;
  color: var(--apl-label);
  font-variant-numeric: tabular-nums;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.apl-dbg-log-lvl {
  flex-shrink: 0;
  width: 44px;
  padding: 1px 4px;
  border-radius: 4px;
  text-align: center;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.02em;
  font-size: 9px;
}
.apl-dbg-log-row.lvl-info    .apl-dbg-log-lvl { background: rgba(0,122,255,0.10); color: #0040A0; }
.apl-dbg-log-row.lvl-success .apl-dbg-log-lvl { background: rgba(52,199,89,0.14); color: #146C2E; }
.apl-dbg-log-row.lvl-warn    .apl-dbg-log-lvl { background: rgba(255,149,0,0.16); color: #8A4B00; }
.apl-dbg-log-row.lvl-error   .apl-dbg-log-lvl { background: rgba(255,59,48,0.14); color: #9F1D14; }
.apl-dbg-log-msg {
  flex: 1;
  min-width: 0;
  color: var(--apl-label);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.apl-dbg-perf-row {
  display: flex;
  justify-content: space-between;
  padding: 3px 0;
  font-size: 11px;
  border-top: 0.5px solid rgba(235,236,240,0.06);
}
.apl-dbg-perf-row:first-child { border-top: none; }
.apl-dbg-perf-k { color: var(--apl-label); }
.apl-dbg-perf-v { font-weight: 600; color: var(--apl-label); font-variant-numeric: tabular-nums; }
</style>
