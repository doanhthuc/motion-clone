<template>
  <!-- #region ALD 22/05/2026 - Apple-style workflow node
       Soft white card, tinted-square icon, refined typography, subtle shadow.
       Selected state: System Blue ring (#5E6AD2). -->
  <div
    :class="[
      'apl-node',
      selected ? 'is-selected' : '',
      data._runState ? `state-${data._runState}` : ''
    ]"
    :style="{ '--accent': accent, '--accent-soft': accentSoft, '--accent-text': accentText }"
    ref="rootEl"
  >
    <!-- Status pill bottom (refined Apple capsule) -->
    <span class="apl-status" :data-state="data._runState || 'idle'">
      <i v-if="data._runState === 'success'" class="bi bi-check" />
      <i v-else-if="data._runState === 'error'" class="bi bi-exclamation" />
      <i v-else-if="data._runState === 'warn'" class="bi bi-exclamation" />
      <i v-else-if="data._runState === 'running'" class="bi bi-dot" />
    </span>

    <!-- Input handle (LEFT) — không có cho input* nodes (entry points).
         Motion Transfer có image / motion / audio. -->
    <template v-if="multiTargets.length">
      <Handle
        v-for="(slot, idx) in multiTargets"
        :key="slot.id"
        :id="slot.id"
        type="target"
        :position="Position.Left"
        :style="{ top: `${20 + idx * 20}%` }"
        :class="['apl-handle handle-target', `handle-${slot.id}`]"
      />
      <span
        v-for="(slot, idx) in multiTargets"
        :key="`lbl-${slot.id}`"
        :class="['handle-pill', `pill-${slot.id}`]"
        :style="{ top: `calc(${20 + idx * 20}% - 8px)` }"
      >{{ slot.label }}</span>
    </template>
    <Handle
      v-else-if="!isEntryNode"
      type="target"
      :position="Position.Left"
      class="apl-handle handle-target"
    />

    <!-- ALD 24/05/2026 - Preview thumbnail (chỉ hiện khi có data thực):
         · Input image/video: staticData (base64) hoặc URL config
         · Input audio: data URL / URL / signed URL từ library → mini audio player
         · Output: video placeholder khi đang chạy, video player khi done. -->
    <!-- ALD 24/05/2026 - Output node: preview giữ aspectRatio nhận từ workflow run.
         Nút Download overlay góc dưới phải khi có video kết quả. -->
    <div v-if="outputImageGrid.length > 1" class="apl-preview apl-preview-grid" :style="aspectStyle">
      <a
        v-for="(img, idx) in outputImageGrid"
        :key="img.url || idx"
        :href="img.url"
        target="_blank"
        class="apl-preview-grid-item"
        :title="img.label || `Ảnh ${idx + 1}`"
        @click.stop
      >
        <img :src="img.url" alt="" @load="_syncHandles" />
        <span>{{ img.label || `Ảnh ${idx + 1}` }}</span>
      </a>
      <button
        v-if="isOutputResult"
        type="button"
        class="apl-download-btn"
        title="Tải ảnh đầu tiên"
        @click.stop="onDownload"
      >
        <i class="bi bi-download" />
      </button>
    </div>
    <div v-else-if="previewSrc && !previewIsAudio" class="apl-preview" :style="aspectStyle">
      <video v-if="previewIsVideo" :src="previewSrc" muted playsinline preload="metadata" controls @loadedmetadata="onVideoLoaded" />
      <img   v-else                :src="previewSrc" alt="" @load="_syncHandles" />
      <!-- ALD 06/07/2026 - badge độ phân giải + fps video (đọc lúc loadedmetadata + đo fps) -->
      <div v-if="previewIsVideo && vidMeta.w" class="apl-vidmeta" title="Độ phân giải · FPS của video">
        {{ vidMeta.w }}×{{ vidMeta.h }}<template v-if="vidMeta.fps"> · {{ vidMeta.fps }}fps</template>
      </div>
      <button
        v-if="isOutputResult"
        type="button"
        class="apl-download-btn"
        title="Tải về"
        @click.stop="onDownload"
      >
        <i class="bi bi-download" />
      </button>
    </div>
    <div v-else-if="previewIsAudio && previewSrc" class="apl-preview apl-preview-audio">
      <audio :src="previewSrc" controls preload="metadata" @click.stop />
    </div>
    <div v-else-if="showOutputPlaceholder" class="apl-preview apl-preview-placeholder" :style="aspectStyle">
      <i :class="['bi', isOutputRunning ? 'bi-camera-reels animate-pulse' : 'bi-camera-reels']" />
      <span>{{ isOutputRunning ? 'Đang xử lý…' : 'Kết quả sẽ hiển thị ở đây' }}</span>
    </div>

    <div class="apl-body">
      <span class="apl-icon">
        <i :class="['bi', icon]" />
      </span>
      <div class="apl-text">
        <div class="apl-label">{{ label }}</div>
        <div :class="['apl-subtitle', !isConfigured && 'empty']" :title="subtitleTitle">
          <!-- ALD 11/06/2026 - Đang chạy/chờ/xong → chỉ hiện trạng thái (Processing/Waiting/Done), KHÔNG hiện model AI. -->
          <template v-if="runStateLabel">{{ runStateLabel }}</template>
          <template v-else>{{ subtitle || 'Chưa cấu hình' }}</template>
        </div>
      </div>
    </div>

    <!-- Output handle (RIGHT) — variants:
         (1) condition: 2 handles true/false
         (2) onError='route': 2 handles success/error
         (3) output node: no handle
         (4) default: 1 handle (success) -->
    <template v-if="data.type === 'condition'">
      <Handle
        id="true"
        type="source"
        :position="Position.Right"
        :style="{ top: '40%' }"
        class="apl-handle handle-source handle-true"
      />
      <span class="branch-pill pill-true">True</span>
      <Handle
        id="false"
        type="source"
        :position="Position.Right"
        :style="{ top: '72%' }"
        class="apl-handle handle-source handle-false"
      />
      <span class="branch-pill pill-false">False</span>
    </template>
    <template v-else-if="errorRouted && data.type !== 'output'">
      <Handle
        id="success"
        type="source"
        :position="Position.Right"
        :style="{ top: '40%' }"
        class="apl-handle handle-source"
      />
      <span class="branch-pill pill-success">OK</span>
      <Handle
        id="error"
        type="source"
        :position="Position.Right"
        :style="{ top: '72%' }"
        class="apl-handle handle-source handle-error"
      />
      <span class="branch-pill pill-error">Err</span>
    </template>
    <Handle
      v-else-if="data.type !== 'output'"
      type="source"
      :position="Position.Right"
      class="apl-handle handle-source"
    />
  </div>
  <!-- #endregion -->
</template>

<script setup>
import { Handle, Position, useVueFlow } from '@vue-flow/core'

const props = defineProps({
  id: { type: String, default: '' },
  data: { type: Object, required: true },
  selected: { type: Boolean, default: false }
})

// ALD 02/06/2026 - FIX dây nối lệch khỏi chấm cổng: node đổi chiều cao (ảnh preview load xong, đổi
// subtitle…) → handle (đặt theo %) dịch, nhưng Vue Flow vẫn neo dây ở vị trí CŨ → dây 1 nơi, cổng 1 nơi.
// Phải gọi updateNodeInternals để Vue Flow tính lại điểm neo. ResizeObserver bắt mọi thay đổi size node.
const { updateNodeInternals } = useVueFlow()
const rootEl = ref(null)
let _ro = null
function _syncHandles() { if (props.id) updateNodeInternals([props.id]) }
onMounted(() => {
  _syncHandles()
  if (rootEl.value && typeof ResizeObserver !== 'undefined') {
    _ro = new ResizeObserver(() => requestAnimationFrame(_syncHandles))
    _ro.observe(rootEl.value)
  }
})
onBeforeUnmount(() => { if (_ro) { _ro.disconnect(); _ro = null } _clearFpsProbe() })

// #region ALD 06/07/2026 - Input Video: phân tích ĐỘ PHÂN GIẢI (loadedmetadata) + FPS (requestVideoFrameCallback
// trên 1 video ẨN, không đụng preview) → badge "1080×1920 · 30fps" trên node. Trình duyệt cũ ko có rVFC → chỉ res.
const vidMeta = ref({ w: 0, h: 0, fps: 0 })
let _fpsVid = null
function _clearFpsProbe() {
  if (_fpsVid) {
    try { _fpsVid.pause() } catch { /* ignore */ }
    _fpsVid.removeAttribute('src'); try { _fpsVid.load() } catch { /* ignore */ }
    _fpsVid.remove()
    _fpsVid = null
  }
}
function _measureFps(src) {
  _clearFpsProbe()
  if (!src || typeof document === 'undefined') return
  // Trình duyệt cũ không có requestVideoFrameCallback → bỏ qua fps (chỉ hiện độ phân giải), khỏi tải video thừa.
  if (typeof HTMLVideoElement === 'undefined' || !('requestVideoFrameCallback' in HTMLVideoElement.prototype)) return
  const v = document.createElement('video')
  _fpsVid = v
  v.muted = true; v.playsInline = true; v.preload = 'auto'; v.src = src   // KHÔNG set crossOrigin (rVFC ko cần CORS; set vào dễ chặn phát)
  // gắn ẩn vào DOM để trình duyệt render frame (rVFC cần element được vẽ)
  v.style.cssText = 'position:fixed;width:1px;height:1px;opacity:0;pointer-events:none;left:-9999px;top:-9999px'
  document.body.appendChild(v)
  let first = null
  const cb = (now, meta) => {
    if (v !== _fpsVid) return                                     // đã bị thay bằng video khác → dừng
    // ALD 09/07/2026 - FIX badge fps nhảy số (20 → 30): cửa sổ đo cũ 0.35s bắt đầu NGAY frame đầu — lúc mới play
    // trình duyệt buffer/drop vài frame → đếm thiếu (video 30fps hiện 20fps), đo lại mới đúng. Nay: BỎ 5 frame
    // khởi động rồi đo ≥1s. Lưu ý: đây là ƯỚC LƯỢNG hiển thị; worker render đọc fps bằng ffprobe (chuẩn).
    if (first === null) {
      if ((meta.presentedFrames || 0) < 5) { v.requestVideoFrameCallback(cb); return }
      first = meta
    } else {
      const dt = meta.mediaTime - first.mediaTime
      const df = meta.presentedFrames - first.presentedFrames
      if (dt >= 1.0 && df > 0) { vidMeta.value = { ...vidMeta.value, fps: Math.max(1, Math.round(df / dt)) }; _clearFpsProbe(); return }
    }
    v.requestVideoFrameCallback(cb)
  }
  v.requestVideoFrameCallback(cb)
  v.play().catch(() => { /* autoplay muted bị chặn → chỉ hiện res */ })
}
function onVideoLoaded(e) {
  _syncHandles()
  const v = e && e.target
  if (v && v.videoWidth) {
    vidMeta.value = { w: v.videoWidth, h: v.videoHeight, fps: 0 }
    _measureFps(previewSrc.value)
  }
}
// #endregion

// Apple system colors — tinted icons giống iOS Symbols
// Common base color cho input variants — tất cả màu xanh emerald
const INPUT_BASE = { accent: '#34C759', accentSoft: 'rgba(52,199,89,0.15)', accentText: '#7CDDAA' }
// Variant per contentType — icon + label đổi theo config.contentType
const INPUT_VARIANTS = {
  text:    { icon: 'bi-chat-left-text',  label: 'Input Text' },
  image:   { icon: 'bi-image',           label: 'Input Image' },
  video:   { icon: 'bi-film',            label: 'Input Video' },
  audio:   { icon: 'bi-music-note-beamed', label: 'Input Audio' },
  file:    { icon: 'bi-file-earmark',    label: 'Input File' },
  history: { icon: 'bi-clock-history',   label: 'Input History' }
}

const NODE_META = {
  // Legacy types — kept for backward compat (now all route to type='input' with config.contentType)
  inputText:    { ...INPUT_BASE, ...INPUT_VARIANTS.text },
  inputImage:   { ...INPUT_BASE, ...INPUT_VARIANTS.image },
  inputFile:    { ...INPUT_BASE, ...INPUT_VARIANTS.file },
  inputHistory: { ...INPUT_BASE, ...INPUT_VARIANTS.history },
  // Processing
  'gpu-warmup':  { icon: 'bi-lightning-charge-fill',  accent: '#34C759', accentSoft: 'rgba(52,199,89,0.15)', accentText: '#7CDDAA', label: 'GPU Warmup' },
  'gpu-free':    { icon: 'bi-arrow-down-circle-fill', accent: '#8E8E93', accentSoft: 'rgba(255,255,255,0.08)', accentText: '#3C3C43', label: 'GPU Free' },
  validate:      { icon: 'bi-check2-square',          accent: '#7CDDAA', accentSoft: 'rgba(52,199,89,0.18)', accentText: '#0F4F1F', label: 'Validate' },
  motion:    { icon: 'bi-film',                  accent: '#FF2D55', accentSoft: 'rgba(255,45,85,0.15)', accentText: '#A11D38', label: 'Motion Transfer' },
  tryon:     { icon: 'bi-person-vcard',          accent: '#FF9500', accentSoft: 'rgba(255,149,0,0.15)', accentText: '#A86200', label: 'Try-on' },
  'create-image': { icon: 'bi-images',           accent: '#AF52DE', accentSoft: 'rgba(175,82,222,0.16)', accentText: '#702A98', label: 'Create Image' },
  'edit-image':   { icon: 'bi-pencil-square',    accent: '#5AC8FA', accentSoft: 'rgba(90,200,250,0.15)', accentText: '#0B6E99', label: 'Sửa ảnh' },
  compose:   { icon: 'bi-person-bounding-box',   accent: '#5856D6', accentSoft: 'rgba(88,86,214,0.16)', accentText: '#3E3CA8', label: 'Ghép vào mẫu' },
  'product-overlay': { icon: 'bi-bag-check-fill', accent: '#0A84FF', accentSoft: 'rgba(10,132,255,0.15)', accentText: '#0757A8', label: 'Overlay sản phẩm' },
  'cast-model': { icon: 'bi-people-fill',        accent: '#0A84FF', accentSoft: 'rgba(10,132,255,0.15)', accentText: '#0757A8', label: 'Tuyển mẫu (kho)' },
  'teen-flycam': { icon: 'bi-camera-video',      accent: '#FF2D55', accentSoft: 'rgba(255,45,85,0.15)', accentText: '#A11D38', label: 'Teen Flycam' },
  'trend-tiktok': { icon: 'bi-stars',            accent: '#FF2D55', accentSoft: 'rgba(255,45,85,0.15)', accentText: '#A11D38', label: 'Trend TikTok' },
  'text-to-video': { icon: 'bi-camera-reels',    accent: '#FF2D55', accentSoft: 'rgba(255,45,85,0.15)', accentText: '#A11D38', label: 'Text → Video' },
  // ALD 03/07/2026 - đổi tên 'Video AI' → 'LoRA' để phân biệt với node Wan mới (Ảnh → Video / Text → Video).
  ss:        { icon: 'bi-film',                  accent: '#5856D6', accentSoft: 'rgba(88,86,214,0.16)', accentText: '#3E3CA8', label: 'LoRA' },
  // ALD 10/07/2026 - bỏ "(Wan)" khỏi label: node giờ đa provider (self-host Wan + DashScope happyhorse/wan2.7).
  'wan-i2v': { icon: 'bi-camera-reels',          accent: '#FF2D55', accentSoft: 'rgba(255,45,85,0.15)', accentText: '#A11D38', label: 'Ảnh → Video' },
  talk:      { icon: 'bi-mic-fill',              accent: '#34C759', accentSoft: 'rgba(52,199,89,0.15)', accentText: '#1B7A38', label: 'Nói (lip-sync)' },
  voiceover: { icon: 'bi-soundwave',             accent: '#34C759', accentSoft: 'rgba(52,199,89,0.15)', accentText: '#1B7A38', label: 'Lồng tiếng' },
  concat:    { icon: 'bi-collection-play-fill',  accent: '#5856D6', accentSoft: 'rgba(88,86,214,0.16)', accentText: '#3A38A6', label: 'Ghép cảnh' },
  subtitle:  { icon: 'bi-badge-cc',              accent: '#FF9500', accentSoft: 'rgba(255,149,0,0.15)', accentText: '#B36800', label: 'Language' },
  debug:     { icon: 'bi-bug-fill',              accent: '#FF9500', accentSoft: 'rgba(255,149,0,0.15)', accentText: '#A86200', label: 'Debug' },
  http:      { icon: 'bi-cloud-arrow-up-fill',   accent: '#5856D6', accentSoft: '#ECEBFB', accentText: '#3E3CA8', label: 'HTTP' },
  // Flow control
  workflow:  { icon: 'bi-diagram-3-fill',        accent: '#FF2D55', accentSoft: 'rgba(255,45,85,0.15)', accentText: '#A11D38', label: 'Workflow' },
  condition: { icon: 'bi-shuffle',               accent: '#FF9500', accentSoft: 'rgba(255,149,0,0.15)', accentText: '#A86200', label: 'Condition' },
  output:    { icon: 'bi-box-arrow-right',       accent: '#8E8E93', accentSoft: 'rgba(255,255,255,0.08)', accentText: '#3C3C43', label: 'Output' },
  // ALD 11/06/2026 - Node khai báo API key (HuggingFace/Gemini/Veo/custom): nối cổng ra → cổng "API Key" của
  // node đích (ưu tiên nhất), hoặc đặt rời = tự phân bổ theo Type. Chỉ self-host không cần key.
  'api-key': { icon: 'bi-key-fill',              accent: '#FFCC00', accentSoft: 'rgba(255,204,0,0.15)', accentText: '#8A6D00', label: 'API Key' }
}

const meta = computed(() => {
  // input node: variant theo config.contentType (text/image/file/history)
  if (props.data.type === 'input') {
    const ct = props.data.config?.contentType || 'text'
    return { ...INPUT_BASE, ...(INPUT_VARIANTS[ct] || INPUT_VARIANTS.text) }
  }
  return NODE_META[props.data.type] || { icon: 'bi-circle', accent: '#8E8E93', accentSoft: 'rgba(255,255,255,0.08)', accentText: '#3C3C43', label: props.data.type }
})
const icon = computed(() => meta.value.icon)
// ALD 24/05/2026 - Ưu tiên config.label do user/seed đặt (vd "Ảnh người mẫu", "Video motion")
// để phân biệt nhiều input cùng loại trong 1 workflow. Fallback về meta.label.
const label = computed(() => {
  const custom = props.data.config?.label
  if (custom && String(custom).trim()) return String(custom).trim()
  return meta.value.label
})
const accent = computed(() => meta.value.accent)
const accentSoft = computed(() => meta.value.accentSoft)
const accentText = computed(() => meta.value.accentText)

// ALD 11/06/2026 - Khi node đang chạy/chờ/xong → subtitle chỉ hiện TRẠNG THÁI (không hiện model AI/config).
const RUN_STATE_LABEL = { queued: 'Waiting', running: 'Processing', success: 'Done', error: 'Error', warn: 'Warning' }
const runStateLabel = computed(() => RUN_STATE_LABEL[props.data._runState] || '')

const subtitle = computed(() => {
  const c = props.data.config || {}
  if (props.data.type === 'input' || props.data.type === 'inputText' || props.data.type === 'inputImage' || props.data.type === 'inputFile' || props.data.type === 'inputHistory') {
    return inputSubtitle(c, props.data.type)
  }
  switch (props.data.type) {
    // ALD 11/06/2026 - KHÔNG hiện tên model/engine AI trên node (theo yêu cầu). Chỉ giữ thông tin chức năng.
    case 'motion': {
      const parts = [c.preset || 'drv-15s']
      if (c.mode && c.mode !== 'transfer') parts.push(c.mode)
      if (c.refImageSource === 'url' && c.refImageUrl) parts.push('ref:URL')
      if (c.motionVideoSource === 'url' && c.motionVideoUrl) parts.push('vid:URL')
      return parts.join(' · ')
    }
    case 'teen-flycam': {
      const customPresets = Array.isArray(c.customPresets) ? c.customPresets : []
      const selectedPreset = customPresets.find((p) => p?.id === c.preset)
      if (!c.preset && !String(c.driverUrls || '').trim()) return 'Chưa có preset'
      const preset = selectedPreset?.label || (String(c.driverUrls || '').trim() ? 'Preset clone' : (c.preset || 'Preset'))
      const duration = Number(selectedPreset?.duration ?? c.duration) || 10
      const shotCount = Number(selectedPreset?.shotCount ?? c.shotCount) || 5
      const gender = c.modelGender === 'female' ? 'Nữ' : c.modelGender === 'male' ? 'Nam' : 'Auto'
      const source = c.driverMode === 'custom' ? 'URL mẫu' : 'Demo'
      const audio = String(c.audioMode || 'preset').toLowerCase() === 'input' ? 'audio input' : 'audio preset'
      return `${preset} · ${source} · ${gender} · ${duration}s/${shotCount} shot · ${audio}`
    }
    case 'trend-tiktok': {
      const preset = c.preset === 'paper-rip' ? 'Xé giấy' : (c.preset || 'Preset')
      const gender = c.modelGender === 'female' ? 'Nữ' : c.modelGender === 'male' ? 'Nam' : 'Auto'
      const audio = String(c.audioMode || 'preset').toLowerCase() === 'input' ? 'audio input' : 'audio preset'
      return `${preset} · 2 look · ${gender} · ${audio}`
    }
    case 'tryon': {
      // ALD 11/06/2026 - KHÔNG hiện engine (Qwen-Edit/Gemini). Chỉ loại đồ.
      const garmentLabel = { upper: 'Áo', lower: 'Quần', dress: 'Váy', set: 'Set', bikini: 'Bikini', accessory: 'Phụ kiện' }[c.garmentType]
      return garmentLabel || 'Thử đồ'
    }
    case 'create-image':
    case 'edit-image': {
      // ALD 11/06/2026 - KHÔNG hiện engine. Chỉ mô tả.
      if (c.prompt && c.prompt.trim()) {
        const p = c.prompt.trim()
        return p.length > 36 ? p.slice(0, 36) + '…' : p
      }
      return props.data.type === 'edit-image' ? '— chưa có mô tả sửa' : '— chưa có mô tả'
    }
    // ALD 11/06/2026 - api-key: hiện provider + trạng thái key (không bao giờ hiện giá trị key).
    case 'api-key': {
      const p = { huggingface: 'HuggingFace', gemini: 'Gemini', veo: 'Veo 3' }[String(c.providerType || '').toLowerCase()] || c.providerType || '—'
      return (c.__apiKey_isSet || (c.apiKey && String(c.apiKey).trim())) ? `${p} · key đã lưu` : `${p} · chưa có key`
    }
    case 'workflow':  return c.slug ? `/${c.slug}` : '— no slug'
    case 'condition': return c.expression || '— no expression'
    case 'http':      return c.url ? `${(c.method || 'POST').toUpperCase()} ${c.url.length > 40 ? c.url.slice(0, 40) + '…' : c.url}` : '— no URL'
    case 'gpu-warmup': return c.wait_for_healthy === false ? `kick (non-block)` : `wait healthy · timeout ${c.timeout_sec || 60}s`
    case 'gpu-free': {
      const targets = []
      if (c.free_ollama !== false) targets.push('Ollama')
      if (c.free_chandra !== false) targets.push('Chandra')
      if (c.free_comfy === true) targets.push('Comfy')
      return `${targets.join('+') || 'none'} · max ${c.max_wait_sec || 45}s`
    }
    case 'validate': {
      const f = (c.required_fields || []).length
      const m = (c.math_checks || []).length
      const mode = c.strict ? 'strict' : 'warn-only'
      return `${f} fields · ${m} math · ${mode}`
    }
    case 'output':    return `format: ${c.format || 'markdown'}`
    case 'debug': {
      const labels = []
      if (c.captureImage !== false) labels.push('image')
      if (c.captureVideo !== false) labels.push('video')
      if (c.captureAudio)            labels.push('audio')
      if (c.captureText !== false)  labels.push('text')
      return `${c.label || 'Debug step'} · ${labels.join('/')}`
    }
    case 'compose': {
      const np = Math.max(1, Math.min(2, Number(c.personCount) || 1))
      return `mẫu + ${np} ${c.subjectKind === 'product' ? 'SP' : 'người'}`
    }
    case 'product-overlay': {
      if (c.mode === 'safe-packshot') return `packshot thật · ${c.position || 'bottom-right'}`
      const map = {
        handheld: 'cầm tay',
        tabletop: 'bàn/kệ',
        'large-display': 'đứng cạnh',
        vehicle: 'xe/đồ lớn',
        auto: 'auto'
      }
      return `đặt SP · ${map[c.productPlacement || 'auto'] || c.productPlacement || 'auto'}`
    }
    case 'talk': {
      const v = (c.voice || '').includes('Puck') || (c.voice || '').includes('Charon') ? 'giọng nam'
        : (c.voice || '').includes('Aoede') || (c.voice || '').includes('Kore') ? 'giọng nữ'
        : (c.voice || '').startsWith('vixtts') ? 'clone' : (c.voice || 'mặc định')
      const l = (c.line || '').trim()
      return `${v}${l ? ' · ' + (l.length > 22 ? l.slice(0, 22) + '…' : l) : ''}`
    }
    case 'text-to-video': {
      const p = String(c.prompt || '').trim()
      return p ? (p.length > 34 ? p.slice(0, 34) + '…' : p) : ''
    }
    case 'voiceover': {
      const l = String(c.script || '').trim()
      const v = String(c.voice || 'vixtts').trim()
      return l ? `${v} · ${l.length > 20 ? l.slice(0, 20) + '…' : l}` : v
    }
    case 'subtitle': {
      const mode = c.mode === 'dub' ? 'lồng tiếng' : c.mode === 'both' ? 'sub + dub' : 'phụ đề'
      return `${mode} · ${c.targetLang || 'vi'}`
    }
    case 'concat':    return 'ghép các cảnh (giữ tiếng)'
    case 'reveal': {
      const mode = c.revealMode || 'slider'
      const rm = mode === 'vortex' ? '🌀xoáy' : mode === 'scan' ? 'cửa sổ' : mode === 'wipe' ? 'wipe' : 'slider'
      const dir = mode === 'vortex' ? '' : ` ${({ down: '↓', up: '↑', left: '←', right: '→', diagtl: '↘', diagtr: '↙' })[c.direction || 'down'] || '↓'}`
      const detail = mode === 'slider'
        ? `@${Math.round((c.sweepAt ?? 0.5) * 100)}% · ${c.sweepDuration ?? 1}s`
        : `dải ${Math.round((c.bandPct ?? 0.25) * 100)}%${c.loop ? '·lặp' : ''}`
      return `${rm}${dir} · ${detail}`
    }
    default:          return ''
  }
})

// "Đã cấu hình" check — TRUE nếu user đã set bất kỳ field nào ngoài defaults
const isConfigured = computed(() => {
  const c = props.data.config || {}
  switch (props.data.type) {
    case 'motion': return !!c.preset
    case 'tryon': return !!c.garmentType
    case 'create-image': return !!(c.prompt && c.prompt.trim())
    case 'edit-image': return !!(c.prompt && c.prompt.trim())
    case 'text-to-video': return !!(c.prompt && c.prompt.trim())
    case 'voiceover': return !!(c.script && c.script.trim())
    case 'subtitle': return true
    case 'compose': return true
    case 'teen-flycam': return !!c.preset || !!String(c.driverUrls || '').trim()
    case 'trend-tiktok': return !!c.preset
    case 'concat': return true
    case 'debug': return true
    case 'workflow': return !!c.slug
    case 'condition': return !!c.expression
    case 'http': return !!c.url
    case 'gpu-warmup': case 'gpu-free': case 'output': return true
    case 'input': case 'inputText': case 'inputImage': case 'inputFile': case 'inputHistory': return true
    default: return false
  }
})

// Input subtitle: show source + relevant info per source
function inputSubtitle(c, type) {
  // Legacy infer
  const ct = c.contentType || ({ inputText: 'text', inputImage: 'image', inputFile: 'file', inputHistory: 'history' }[type] || 'text')
  const source = c.source || 'session'
  if (source === 'session') return `session.${c.field || ct}`
  if (source === 'url') {
    if (!c.url) return 'URL — chưa set'
    try { return `URL · ${new URL(c.url).hostname}` } catch { return `URL · ${c.url.slice(0, 24)}` }
  }
  if (source === 'static') {
    if (ct === 'text') return c.staticText ? `Static · "${c.staticText.slice(0, 18)}…"` : 'Static text — empty'
    return c.staticName ? `Upload · ${c.staticName.slice(0, 22)}` : 'Upload — chưa chọn'
  }
  // ALD 24/05/2026 - Library source: hiện tên file đã pick (nếu config.label đã track),
  // fallback gọn "Library /audio" / "Library /storage" để node không hiện "Chưa cấu hình".
  if (source === 'library') {
    if (!c.libraryId) return ct === 'audio' ? 'Library /audio — chưa pick' : 'Library /storage — chưa pick'
    return ct === 'audio' ? `Library /audio · ${String(c.libraryId).slice(0, 8)}` : `Library /storage · ${String(c.libraryId).slice(0, 8)}`
  }
  return ''
}

// Render error handle khi user bật "On Failure: route" trong inspector
const errorRouted = computed(() => (props.data.config?.onError) === 'route')

const subtitleTitle = computed(() => subtitle.value || 'Chưa cấu hình')

// ALD 24/05/2026 - Library signed URL resolver (audio /audio, image-video-file /storage).
// Cache theo libraryId trên local ref để không spam fetch khi Vue Flow re-render.
// ALD 27/05/2026 - Ưu tiên workflow-scoped endpoint /workflows/:wfId/asset/:libId nếu đang
// trong context workflow editor (route.params.id) → public viewer truy cập được asset owner
// reference trong def. Fallback direct /audio-files /storage-files cho trường hợp ngoài
// editor (vd shared component nếu có).
const libraryResolvedUrl = ref('')
const libraryResolvingId = ref('')
const route = useRoute()
const wf = useWorkflows()
async function resolveLibraryUrl(libId, ct) {
  if (!libId) { libraryResolvedUrl.value = ''; return }
  if (libraryResolvingId.value === libId && libraryResolvedUrl.value) return
  libraryResolvingId.value = libId
  const wfId = route?.params?.id
  const kind = ct === 'audio' ? 'audio' : 'storage'
  try {
    if (wfId) {
      const item = await wf.getAsset(String(wfId), libId, kind)
      if (item?.signedUrl) {
        if (libraryResolvingId.value === libId) libraryResolvedUrl.value = item.signedUrl
        return
      }
      // Fallthrough — endpoint mới trả null (e.g., libId không reference trong def) thì
      // thử direct (owner trường hợp picker chưa save vào def).
    }
    if (ct === 'audio') {
      const audio = useAudioFiles()
      const url = await audio.getSignedUrl(libId)
      if (libraryResolvingId.value === libId) libraryResolvedUrl.value = url || ''
    } else {
      const storage = useStorageFiles()
      const data = await storage.getSignedUrl(libId).catch(() => null)
      const url = data?.signedUrl || data?.signed_url || ''
      if (libraryResolvingId.value === libId) libraryResolvedUrl.value = url
    }
  } catch {
    libraryResolvedUrl.value = ''
  }
}
watch(() => [props.data.config?.libraryId, props.data.config?.contentType, props.data.config?.source], ([id, ct, src]) => {
  if (src === 'library' && id) resolveLibraryUrl(id, ct)
  else libraryResolvedUrl.value = ''
}, { immediate: true })

// Preview src cho node:
// · Input: staticData (base64) → wrap data:URL; OR url field (skip nếu template `{{...}}`)
// · Library: signed URL từ /audio (audio) / /storage (image/video/file)
// · Output: data._runOutput.video / .image sau khi Test xong
const previewSrc = computed(() => {
  const c = props.data.config || {}
  const t = props.data.type
  if (t === 'input' || t === 'inputText' || t === 'inputImage' || t === 'inputFile' || t === 'inputHistory') {
    const ct = c.contentType || ''
    if (!['image', 'video', 'audio'].includes(ct)) return null
    // ALD 27/05/2026 - staticUrl (FE upload → storage) ưu tiên hơn staticData (base64 legacy).
    // Refactor static base64 → URL: workflow lưu URL persistent → canvas FlowNode preview
    // hiện ngay không cần chờ run. Engine fetch URL khi chạy + re-sign nếu expire.
    if (c.source === 'static' && c.staticUrl) return c.staticUrl
    if (c.source === 'static' && c.staticData) {
      if (/^data:/.test(c.staticData)) return c.staticData
      const mime = c.staticMime || (ct === 'video' ? 'video/mp4' : ct === 'audio' ? 'audio/mpeg' : 'image/jpeg')
      return `data:${mime};base64,${c.staticData}`
    }
    if (c.source === 'url' && c.url && !/^\{\{/.test(c.url)) return c.url
    if (c.source === 'library' && libraryResolvedUrl.value) return libraryResolvedUrl.value
    // ALD 27/05/2026 - Fallback: khi user click run cũ, workflow def staticData đã bị
    // strip trên BE save. Watcher selectedRunId extract URL upload từ events đặt vào
    // _runOutput. Hiện ở preview để user thấy "input nào đã dùng cho run đó".
    const out = props.data._runOutput
    if (out && (out.image || out.video || out.audio)) {
      return out.video || out.image || out.audio
    }
    return null
  }
  if (t === 'output') {
    const out = props.data._runOutput || {}
    return out.video || out.image || out.images?.[0]?.url || null
  }
  // ALD 27/05/2026 - Intermediate nodes (tryon/motion) cũng có
  // _runOutput sau khi run xong (engine emit previewUrl trong success event).
  // Tryon/product-overlay → image PNG, Motion Transfer → video MP4.
  if (t === 'tryon' || t === 'create-image' || t === 'edit-image' || t === 'compose' || t === 'product-overlay' || t === 'motion' || t === 'teen-flycam' || t === 'trend-tiktok' || t === 'subtitle' || t === 'ss' || t === 'reveal') {
    const out = props.data._runOutput || {}
    return out.video || out.image || out.images?.[0]?.url || null
  }
  return null
})
const previewIsVideo = computed(() => {
  const c = props.data.config || {}
  const t = props.data.type
  if (t === 'output' || t === 'tryon' || t === 'create-image' || t === 'compose' || t === 'product-overlay' || t === 'motion' || t === 'teen-flycam' || t === 'trend-tiktok' || t === 'subtitle' || t === 'ss' || t === 'reveal') {
    return !!(props.data._runOutput?.video)
  }
  return c.contentType === 'video'
})
const previewIsAudio = computed(() => {
  const c = props.data.config || {}
  return c.contentType === 'audio'
})
const outputImageGrid = computed(() => {
  // ALD 01/07/2026 - edit-image cũng render lưới nhiều ảnh (progressive: mỗi version xong đẩy 1 ảnh vào images[]).
  if (props.data.type !== 'output' && props.data.type !== 'edit-image') return []
  const out = props.data._runOutput || {}
  const list = Array.isArray(out.images) ? out.images : []
  return list
    .map((it, idx) => typeof it === 'string' ? { url: it, label: `Ảnh ${idx + 1}` } : it)
    .filter((it) => it?.url && !String(it.url).toLowerCase().endsWith('.mp4'))
})
// Output node placeholder: hiện skeleton khi đang chạy / queued, hoặc khi format=video
// nhưng chưa có kết quả — để user thấy chỗ video sẽ render.
const isOutputRunning = computed(() => {
  return props.data.type === 'output' && (props.data._runState === 'running' || props.data._runState === 'queued')
})
const showOutputPlaceholder = computed(() => {
  if (props.data.type !== 'output') return false
  const fmt = props.data.config?.format
  if (fmt !== 'video' && fmt !== 'image') return false
  return isOutputRunning.value || !props.data._runOutput
})
const isOutputResult = computed(() => props.data.type === 'output' && !!props.data._runOutput)

// ALD 27/05/2026 - aspectStyle giờ chỉ override khi metadata explicit aspect_ratio
// (vd output có metadata.aspect_ratio='9:16'). Mặc định để CSS class .apl-preview
// dùng 16:9. Bỏ giá trị fallback `{ height: '160px' }` để tỉ lệ luôn áp dụng.
const aspectStyle = computed(() => {
  if (props.data.type !== 'output') return {}
  const ratio = props.data._runOutput?.metadata?.aspect_ratio
              || props.data.config?.aspectRatio
              || ''
  const m = String(ratio).match(/^(\d+):(\d+)$/)
  if (!m) return {}  // fallback 16:9 từ CSS
  const w = Number(m[1]); const h = Number(m[2])
  return { aspectRatio: `${w} / ${h}` }
})
async function onDownload() {
  const src = previewSrc.value
  if (!src) return
  try {
    const res = await fetch(src)
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    const ext = previewIsVideo.value ? 'mp4' : 'png'
    a.download = `output-${Date.now()}.${ext}`
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  } catch (e) {
    console.warn('[FlowNode] download fail, fallback open:', e)
    window.open(src, '_blank')
  }
}

// Entry nodes (input*) không có input handle bên trái — chúng là source của data.
const isEntryNode = computed(() => {
  const t = props.data.type
  if (['input', 'inputText', 'inputImage', 'inputFile', 'inputHistory'].includes(t)) return true
  // ALD 11/06/2026 - api-key: node nguồn (chỉ có cổng ra nối vào cổng API Key của node khác).
  if (t === 'api-key') return true
  // ALD 14/06/2026 - text-to-video: CHỈ prompt (không ảnh) → node NGUỒN, KHÔNG có cổng input bên trái.
  if (t === 'text-to-video') return true
  // ALD 30/06/2026 - cast-model: tuyển mẫu từ kho theo gender/age → node NGUỒN (chỉ cổng ra), không nhận input.
  if (t === 'cast-model') return true
  // ALD 31/05/2026 - Create Image với 0 cổng ảnh = node NGUỒN (sinh ảnh từ prompt) →
  // không có điểm nối input. Có ≥1 ảnh thì dùng multi-handle (image1..N) nên không tới đây.
  // (11/06: nếu provider cần key thì multiTargets đã có cổng apikey → template đi nhánh multi, không tới đây.)
  if (t === 'create-image') {
    const n = Number(props.data.config?.inputCount)
    return !(Number.isFinite(n) && n > 0)
  }
  return false
})

// Edge.targetHandle = id giúp engine map đúng input → slot.
// #region ALD 10/06/2026 - Tryon: cổng SP động (config.productCount 1-2). Ảnh góc 2 (mặt sau/
// bên hông) → Qwen-Edit image3 → render đúng sản phẩm khi người mẫu quay lưng/xoay người. Qwen tối đa 3 slot
// ảnh (model chiếm 1) nên cap 2 ảnh SP. Handle id khớp worker: product + product2.
const PRODUCT_PORT_COUNT = computed(() => {
  const n = Number(props.data.config?.productCount)
  return Math.max(1, Math.min(2, Number.isFinite(n) && n > 0 ? n : 1))
})
const productPorts = (count) => Array.from({ length: count }, (_, i) => ({
  id: i === 0 ? 'product' : `product${i + 1}`,
  label: count >= 2 ? `Ảnh SP ${i + 1}` : 'Sản phẩm'
}))
// Motion Transfer: 3 handles (image + motion + audio)
const MOTION_TARGETS = [
  { id: 'image',   label: 'Người mẫu' },
  { id: 'motion',  label: 'Motion' },
  { id: 'audio',   label: 'Audio (opt)' }
]
const TEEN_FLYCAM_TARGETS = [{ id: 'image', label: 'Người mẫu' }, { id: 'audio', label: 'Audio (opt)' }]
const TREND_TIKTOK_TARGETS = [{ id: 'before', label: 'Look đầu' }, { id: 'after', label: 'Look sau' }, { id: 'audio', label: 'Audio (opt)' }]
// Tryon: model + 1-2 cổng sản phẩm + cổng NỀN (chỉ khi bật toggle). Output image (đã thay đồ, có thể ghép bối cảnh).
// ALD 21/06/2026 - cổng 'background' CHỈ HIỆN khi config.useBackground=true (toggle ở InspectorTryon). Nối 1 ảnh
// phòng/địa điểm → worker ghép người vào bối cảnh (Qwen pass 2). Handle id 'background' khớp inputs.background.
const TRYON_TARGETS = computed(() => {
  // ALD 21/06/2026 - cleanOnly (toggle "chỉ làm sạch ảnh"): KHÔNG thay đồ → bỏ cổng Sản phẩm + Nền, chỉ còn
  // 1 cổng ảnh người để làm sạch. Khớp BE run_tryon (clean_only chỉ cần inputs.model).
  if (props.data.config?.cleanOnly) return [{ id: 'model', label: 'Ảnh cần làm sạch' }]
  return [
    { id: 'model',   label: 'Người mẫu' },
    ...productPorts(PRODUCT_PORT_COUNT.value),
    ...(props.data.config?.useBackground ? [{ id: 'background', label: 'Nền' }] : [])
  ]
})
// #endregion
// Create Image: số cổng ảnh động theo config.inputCount (MẶC ĐỊNH 0 = text→image thuần).
// ALD 31/05/2026 - 0 cổng = sinh ảnh CHỈ từ prompt (không cần input ảnh). Thêm cổng (1-6) =
// chỉnh/sinh theo ảnh tham chiếu. Chỉnh ở Inspector (props.config.inputCount).
const CREATE_IMAGE_COUNT = computed(() => {
  const n = Number(props.data.config?.inputCount)
  return Math.max(0, Math.min(6, Number.isFinite(n) && n >= 0 ? n : 0))
})
// ≥2 ảnh: Ảnh 1 = "Ảnh mẫu" (base/tham chiếu phong cách-trang phục), còn lại "Ảnh 2..N".
// 1 ảnh = "Ảnh gốc" (ảnh chỉnh sửa). Handle id giữ image1..N (worker gom theo thứ tự, image1=base).
const CREATE_IMAGE_TARGETS = computed(() => {
  const n = CREATE_IMAGE_COUNT.value
  return Array.from({ length: n }, (_, i) => ({
    id: `image${i + 1}`,
    label: n >= 2 ? (i === 0 ? 'Ảnh mẫu' : `Ảnh ${i + 1}`) : 'Ảnh gốc',
  }))
})
// ALD 01/07/2026 - Edit Image "Sửa ảnh": LIST 1-6 cổng ảnh cần sửa (config.inputCount, tối thiểu 1).
// Handle id image1..N khớp worker run_edit_image (gom theo thứ tự, mỗi cổng 1 ảnh).
const EDIT_IMAGE_TARGETS = computed(() => {
  const raw = Number(props.data.config?.inputCount)
  const n = Math.max(1, Math.min(6, Number.isFinite(raw) && raw >= 1 ? raw : 1))
  return Array.from({ length: n }, (_, i) => ({ id: `image${i + 1}`, label: n >= 2 ? `Ảnh ${i + 1}` : 'Ảnh gốc' }))
})
// ALD 31/05/2026 - Compose "Ghép vào mẫu": cổng image1 = Ảnh mẫu (base latent) +
// image2..N = Người (config.personCount 1-2). Qwen-Edit tối đa 3 ảnh = mẫu + 2 người.
const COMPOSE_PERSON_COUNT = computed(() => {
  const n = Number(props.data.config?.personCount)
  return Math.max(1, Math.min(2, Number.isFinite(n) && n > 0 ? n : 1))
})
const COMPOSE_TARGETS = computed(() => {
  const kind = props.data.config?.subjectKind === 'product' ? 'SP' : 'Người'
  const out = [{ id: 'image1', label: 'Ảnh mẫu' }]
  for (let i = 0; i < COMPOSE_PERSON_COUNT.value; i++)
    out.push({ id: `image${i + 2}`, label: `${kind} ${i + 1}` })
  return out
})
const PRODUCT_OVERLAY_TARGETS = [{ id: 'image1', label: 'Ảnh mẫu' }, { id: 'image2', label: 'SP thật' }]
// ALD 08/07/2026 - Đè lộ: 2 video cùng người khác đồ. base=nền (A), reveal=đồ mới lộ dần (B). Khớp worker inputs.base/reveal.
const REVEAL_TARGETS = [{ id: 'base', label: 'Nền' }, { id: 'reveal', label: 'Đồ mới' }]
// ALD 03/06/2026 - Concat "Ghép cảnh": cổng clip1..N — mỗi cổng 1 phân cảnh.
// ALD 17/06/2026 - max 8 (trước 6): generator BĐS có thể dựng tới 6 công đoạn + cảnh đêm = 7 clip. run_concat (BE) gom clip bất kỳ.
const CONCAT_CLIP_COUNT = computed(() => {
  const n = Number(props.data.config?.clipCount)
  return Math.max(2, Math.min(8, Number.isFinite(n) && n >= 2 ? n : 2))
})
const CONCAT_TARGETS = computed(() => {
  const out = []
  for (let i = 0; i < CONCAT_CLIP_COUNT.value; i++) out.push({ id: `clip${i + 1}`, label: `Cảnh ${i + 1}` })
  if (props.data.config?.audioMode === 'source') out.push({ id: 'audio', label: 'Audio gốc' })
  return out
})
// ALD 15/06/2026 - SS cổng vào ĐỘNG: I2V = 1–3 ảnh; T2V = không cổng; V2V = 1 video.
// ALD 26/06/2026 - wan-v2v model: cổng 'video' (video nguồn → Wan restyle).
const SS_TARGETS = computed(() => {
  if (props.data.config?.model === 'wan-v2v') return [{ id: 'video', label: 'Video nguồn' }]
  if (props.data.config?.model === 'wan2.2' || props.data.config?.model === 'wan2.1') return []
  const n = Math.max(1, Math.min(3, Number(props.data.config?.inputCount) || 1))
  return ['input', 'image2', 'image3'].slice(0, n).map((id, i) => ({ id, label: `Ảnh ${i + 1}` }))
})
// ALD 18/06/2026 - wan-i2v: cổng 'start' (ảnh đầu, BẮT BUỘC) + 'end' (ảnh cuối, TUỲ CHỌN) → FLF morph start→end (liền mạch).
// ALD 03/07/2026 - cổng 'end' chỉ hiện khi bật toggle "Ảnh cuối" trong Inspector (config.endEnabled).
// Node cũ thiếu key (undefined) = hiện cổng như trước; chỉ endEnabled === false mới ẩn.
// ALD 10/07/2026 - provider DashScope: happyhorse chỉ nhận ảnh đầu → ẨN cổng 'end'; wan2.x (wan2.7-i2v...)
// nhận last_frame (giữ 'end' theo toggle) + driving_audio → thêm cổng 'audio' (video diễn/nhép theo audio).
const WAN_I2V_TARGETS = computed(() => {
  const c = props.data.config || {}
  const isDs = String(c.provider || '').toLowerCase().trim() === 'dashscope'
  const dsWan2x = isDs && String(c.dashscopeModel || '').startsWith('wan2.')
  const ports = [{ id: 'start', label: 'Ảnh đầu' }]
  const endHidden = c.endEnabled === false || (isDs && !dsWan2x)
  if (!endHidden) ports.push({ id: 'end', label: 'Ảnh cuối (opt)' })
  if (dsWan2x) ports.push({ id: 'audio', label: 'Audio (opt)' })
  return ports
})
const VOICEOVER_TARGETS = [{ id: 'input', label: 'Video' }]
// #region ALD 11/06/2026 - Cổng "API Key": node dùng provider CẦN KEY hiện thêm cổng nối từ node API Key
// (engine coi cạnh này là config-edge, không phải data input). Self-host không cần key → không cổng.
// (HF đã gỡ cùng ngày theo yêu cầu user — chỉ còn gemini.)
// ALD 10/07/2026 - + dashscope (wan-i2v provider cloud Alibaba happyhorse i2v).
const NEEDS_KEY_PROVIDERS = ['gemini', 'dashscope']
const API_KEY_PORT = { id: 'apikey', label: 'API Key' }
const needsKeyPort = computed(() => NEEDS_KEY_PROVIDERS.includes(String(props.data.config?.provider || '').toLowerCase().trim()))
const withKeyPort = (arr) => (needsKeyPort.value ? [...arr, API_KEY_PORT] : arr)
// #endregion
// Unified multi-target lookup theo node type
const multiTargets = computed(() => {
  // motion: chỉ hiện cổng 'audio' khi chọn "Âm thay thế". "Âm gốc" và "Im lặng" đều ẩn cổng này.
  if (props.data.type === 'motion') {
    const mode = String(props.data.config?.audioMode || '').toLowerCase()
    const replacement = mode ? mode === 'replacement' : props.data.config?.audioPassthrough === false
    // ALD 10/07/2026 - provider dashscope (wan2.2-animate cloud) → thêm cổng API Key (withKeyPort tự gate).
    return withKeyPort(replacement ? MOTION_TARGETS : MOTION_TARGETS.filter((s) => s.id !== 'audio'))
  }
  if (props.data.type === 'teen-flycam') {
    const useInputAudio = String(props.data.config?.audioMode || 'preset').toLowerCase() === 'input'
    return useInputAudio ? TEEN_FLYCAM_TARGETS : TEEN_FLYCAM_TARGETS.filter((s) => s.id !== 'audio')
  }
  if (props.data.type === 'trend-tiktok') {
    const useInputAudio = String(props.data.config?.audioMode || 'preset').toLowerCase() === 'input'
    return useInputAudio ? TREND_TIKTOK_TARGETS : TREND_TIKTOK_TARGETS.filter((s) => s.id !== 'audio')
  }
  if (props.data.type === 'tryon') return withKeyPort(TRYON_TARGETS.value)
  if (props.data.type === 'create-image') return withKeyPort(CREATE_IMAGE_TARGETS.value)
  if (props.data.type === 'edit-image') return withKeyPort(EDIT_IMAGE_TARGETS.value)
  if (props.data.type === 'compose') return withKeyPort(COMPOSE_TARGETS.value)
  if (props.data.type === 'product-overlay') return PRODUCT_OVERLAY_TARGETS
  if (props.data.type === 'ss') return SS_TARGETS.value
  // ALD 10/07/2026 - wan-i2v: provider dashscope → thêm cổng API Key (self-host không hiện cổng).
  if (props.data.type === 'wan-i2v') return withKeyPort(WAN_I2V_TARGETS.value)
  if (props.data.type === 'voiceover') return VOICEOVER_TARGETS
  if (props.data.type === 'concat') return CONCAT_TARGETS.value
  if (props.data.type === 'reveal') return REVEAL_TARGETS
  return []
})
// Đổi SỐ cổng input (vd ẩn/hiện cổng audio motion theo audioPassthrough) → Vue Flow phải tính lại điểm neo dây.
watch(() => multiTargets.value.length, () => nextTick(() => _syncHandles()))
</script>

<style scoped>
/* #region ALD 22/05/2026 - Apple HIG-inspired node */
.apl-node {
  position: relative;
  /* ALD 24/05/2026 - Width cố định 240px. KHÔNG overflow:hidden — sẽ clip Vue Flow
     handles (chấm tròn nối) thò ra cạnh trái/phải. */
  width: 240px;
  min-width: 240px;
  max-width: 240px;
  background: var(--apl-node-bg);
  backdrop-filter: blur(24px) saturate(200%);
  -webkit-backdrop-filter: blur(24px) saturate(200%);
  border-radius: 20px;
  border: 0.5px solid var(--apl-node-border);
  box-shadow: var(--apl-node-shadow);
  transition: box-shadow 0.25s cubic-bezier(0.32, 0.72, 0, 1),
              border-color 0.25s cubic-bezier(0.32, 0.72, 0, 1);
  cursor: grab;
}
.apl-node:active { cursor: grabbing; }
/* Hover: KHÔNG transform — chỉ shadow tăng + border đậm hơn. Transform làm vỡ
   layout edges (handle position dịch theo) → đường nối bể. */
.apl-node:hover {
  border-color: var(--apl-node-border-hover);
  box-shadow: var(--apl-node-shadow-hover);
}
.apl-node.is-selected {
  border-color: rgba(94, 106, 210, 0.7);
  box-shadow:
    0 0 0 2px rgba(94, 106, 210, 0.2),
    0 6px 18px rgba(94, 106, 210, 0.08);
}

/* Run state */
.apl-node.state-running {
  border-color: rgba(255, 149, 0, 0.5);
  animation: apl-pulse 1.4s ease-in-out infinite;
}
.apl-node.state-success { border-color: rgba(52, 199, 89, 0.55); }
.apl-node.state-warn    { border-color: rgba(255, 149, 0, 0.55); box-shadow: 0 0 0 3px rgba(255, 149, 0, 0.12); }
.apl-node.state-error   { border-color: rgba(255, 59, 48, 0.55); box-shadow: 0 0 0 3px rgba(255, 59, 48, 0.12); }
@keyframes apl-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(255, 149, 0, 0.18), 0 8px 24px rgba(255, 149, 0, 0.1); }
  50%      { box-shadow: 0 0 0 8px rgba(255, 149, 0, 0), 0 8px 24px rgba(255, 149, 0, 0.15); }
}

.apl-preview {
  position: relative;
  width: 100%;
  /* ALD 27/05/2026 - Adaptive aspect ratio: box auto-fit theo natural ratio của content
     (image/video). User feedback: 16:9 cứng làm portrait Wan output 9:16 bị letterbox
     2 bên đen — xấu. Bỏ aspect-ratio cứng, để <img>/<video> sizing tự nhiên fill width
     và đặt box height theo content. Cap max-height để không vỡ layout node. */
  max-height: 360px;
  overflow: hidden;
  background: #0a0a0a;
  /* Round top corners theo node radius (20) — không phụ thuộc parent overflow */
  border-top-left-radius: 20px;
  border-top-right-radius: 20px;
  /* Layout flex để image/video center khi có max-height crop */
  display: flex;
  align-items: center;
  justify-content: center;
}
/* ALD 06/07/2026 - object-fit: cover — media lấp ĐẦY khung node, không còn letterbox
   dải đen 2 bên khi height bị cap (contain cũ nhìn "kì"). height cứng theo tỉ lệ đẹp:
   portrait vẫn hiển thị phần trên (mặt người) nhờ object-position: center top. */
.apl-preview img,
.apl-preview video {
  width: 100%;
  height: auto;
  max-height: 360px;
  object-fit: cover;
  object-position: center top;
  display: block;
}
/* ALD 06/07/2026 - badge độ phân giải + fps của video (góc trên-trái preview) */
.apl-vidmeta {
  position: absolute;
  top: 6px;
  left: 6px;
  padding: 2px 7px;
  border-radius: 7px;
  background: rgba(0, 0, 0, 0.6);
  color: #fff;
  font-size: 10.5px;
  font-weight: 600;
  letter-spacing: 0.02em;
  line-height: 1.5;
  font-variant-numeric: tabular-nums;
  backdrop-filter: blur(4px);
  pointer-events: none;
  z-index: 2;
}
.apl-preview-grid {
  max-height: 360px;
  padding: 8px;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  align-items: start;
  justify-content: stretch;
  gap: 8px;
  overflow-y: auto;
  background: var(--apl-fill);
}
.apl-preview-grid-item {
  min-width: 0;
  overflow: hidden;
  border-radius: 10px;
  background: var(--apl-bg-secondary);
  border: 0.5px solid var(--apl-separator);
  text-decoration: none;
  color: var(--apl-label-2);
}
.apl-preview-grid-item img {
  width: 100%;
  aspect-ratio: 1 / 1;
  height: auto;
  max-height: none;
  object-fit: cover;
  display: block;
}
.apl-preview-grid-item span {
  display: block;
  padding: 4px 6px 5px;
  font-size: 9.5px;
  line-height: 1.2;
  font-weight: 700;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
/* ALD 27/05/2026 - Audio override .apl-preview adaptive aspect → audio compact height. */
.apl-preview-audio {
  height: 64px;
  max-height: 64px;
  display: flex;
  align-items: center;
  padding: 0 14px;
  background: linear-gradient(135deg, rgba(52,199,89,0.14), rgba(48,176,199,0.14));
}
.apl-preview-audio audio {
  width: 100%;
  min-width: 0;       /* override native min-width */
  height: 36px;
  display: block;
}
/* ALD 24/05/2026 - Output placeholder: skeleton khi workflow đang chạy hoặc chưa có kết quả */
/* ALD 27/05/2026 - Placeholder không có content → set min-height đảm bảo thấy được */
.apl-preview-placeholder {
  min-height: 140px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  background: repeating-linear-gradient(
    45deg,
    var(--apl-fill),
    var(--apl-fill) 8px,
    var(--apl-fill-2) 8px,
    var(--apl-fill-2) 16px
  );
  color: var(--apl-label-2);
  font-size: 10.5px;
  font-weight: 600;
  letter-spacing: -0.01em;
}
.apl-preview-placeholder i { font-size: 22px; }
.apl-download-btn {
  position: absolute;
  bottom: 8px;
  right: 8px;
  width: 32px; height: 32px;
  display: inline-flex; align-items: center; justify-content: center;
  background: rgba(0,0,0,0.55);
  color: white;
  border: none;
  border-radius: 999px;
  cursor: pointer;
  font-size: 14px;
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  transition: all 0.16s cubic-bezier(0.32, 0.72, 0, 1);
  z-index: 2;
}
.apl-download-btn:hover { background: rgba(0,0,0,0.78); transform: scale(1.06); }

.apl-body {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px 16px;
}
.apl-icon {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 38px;
  border-radius: 11px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 18px;
  /* iOS app icon-like with subtle inner glow */
  box-shadow:
    inset 0 0.5px 0 rgba(255,255,255,0.05),
    0 0.5px 2px rgba(255,255,255,0.06);
}
.apl-text { min-width: 0; flex: 1; }
.apl-label {
  font-size: 14.5px;
  font-weight: 600;
  color: var(--apl-label);
  letter-spacing: -0.022em;
  line-height: 1.18;
}
.apl-subtitle {
  font-size: 11.5px;
  font-weight: 500;
  color: var(--apl-label-2);
  margin-top: 3px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  letter-spacing: -0.005em;
  display: flex;
  align-items: center;
  gap: 4px;
}
.apl-subtitle i.bi { font-size: 12px; opacity: 0.85; }
.apl-subtitle.empty {
  color: var(--apl-label-3);
  font-style: italic;
}

/* Status pill bottom-right — small capsule */
.apl-status {
  position: absolute;
  bottom: -6px;
  right: -6px;
  min-width: 16px;
  height: 16px;
  border-radius: 8px;
  background: #3A3A42;             /* iOS gray idle */
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: white;
  font-size: 11px;
  font-weight: 800;
  box-shadow: 0 0 0 3px var(--apl-bg), 0 1px 2px rgba(0,0,0,0.1);
  padding: 0 2px;
}
.apl-status[data-state="running"] { background: #FF9500; animation: apl-blink 1s infinite; }
.apl-status[data-state="success"] { background: #34C759; }
.apl-status[data-state="warn"]    { background: #FF9500; }
.apl-status[data-state="error"]   { background: #FF3B30; }
@keyframes apl-blink {
  0%, 100% { transform: scale(1); }
  50%      { transform: scale(1.15); }
}

/* Handles — bigger để dễ click, hover chỉ glow ring (không scale → edge không bể) */
:deep(.apl-handle) {
  width: 14px !important;
  height: 14px !important;
  background: var(--apl-bg-secondary) !important;
  border: 2.5px solid var(--accent) !important;
  box-shadow: 0 1px 2px rgba(0,0,0,0.08);
  transition: box-shadow 0.18s ease, background 0.18s ease;
  z-index: 10;
}
:deep(.apl-handle:hover) {
  background: var(--accent) !important;
  box-shadow: 0 0 0 6px var(--accent-soft), 0 2px 4px rgba(0,0,0,0.12);
  cursor: crosshair;
}
:deep(.apl-handle.connectingfrom),
:deep(.apl-handle.connectingto) {
  background: #5E6AD2 !important;
  box-shadow: 0 0 0 8px rgba(94, 106, 210, 0.18) !important;
}
:deep(.handle-target) {
  border-color: #94a3b8 !important;
  left: -8px !important;
}
:deep(.handle-source) {
  right: -8px !important;
}
:deep(.handle-true)  { border-color: #34C759 !important; }
:deep(.handle-false) { border-color: #FF3B30 !important; }
:deep(.handle-error) { border-color: #FF9500 !important; }

/* Branch pills (condition) — iOS-style capsule */
.branch-pill {
  position: absolute;
  right: -8px;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: -0.01em;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--apl-bg-secondary);
  pointer-events: none;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08), 0 0 0 0.5px rgba(0, 0, 0, 0.08);
  transform: translateX(100%);
  margin-left: 14px;
}
.pill-true    { top: calc(40% - 10px); color: #7CDDAA; }
.pill-false   { top: calc(72% - 10px); color: #F29B9B; }
.pill-success { top: calc(40% - 10px); color: #7CDDAA; }
.pill-error   { top: calc(72% - 10px); color: #A86200; }

/* Nhãn cho các target handle bên trái. */
.handle-pill {
  position: absolute;
  left: -8px;
  font-size: 9.5px;
  font-weight: 700;
  letter-spacing: -0.01em;
  padding: 1.5px 7px;
  border-radius: 999px;
  background: var(--apl-bg-secondary);
  pointer-events: none;
  white-space: nowrap;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08), 0 0 0 0.5px rgba(0, 0, 0, 0.08);
  transform: translateX(-100%);
  margin-right: 14px;
  color: #AF52DE;
}
.pill-audio { color: #FF9500; }
/* Node có nhiều handle cần thêm chiều cao. */
.apl-node:has(.handle-pill) {
  min-height: 132px;
}

/* ── ALD 06/07/2026 - LIGHT SKIN cho node: surface đi theo var --apl-node-* (editor định nghĩa
   theo theme). Pills đổi màu chữ đủ tương phản trên nền sáng. */
html.theme-light .branch-pill { background: #FFFFFF; }
html.theme-light .pill-true,
html.theme-light .pill-success { color: #1F7D38; }
html.theme-light .pill-false { color: #C0261F; }
html.theme-light .pill-error { color: #A86200; }
/* #endregion */
</style>
