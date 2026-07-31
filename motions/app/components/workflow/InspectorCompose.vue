<template>
  <!-- ALD 31/05/2026 - "Ghép vào mẫu": Qwen-Edit đa ảnh. Ảnh mẫu (image1, base latent giữ bố cục/
       bối cảnh/pose) + Người 1..N (image2..N) chèn vào, giữ mặt. Prompt auto-build, sửa được. -->
  <div class="space-y-4">
    <div class="apl-info-card">
      <p class="font-semibold flex items-center gap-1.5">
        <i class="bi bi-person-bounding-box" /> Ghép vào mẫu
      </p>
      <p class="text-[11px] opacity-70 mt-1">
        <b>Ảnh mẫu</b> (cổng 1) = bối cảnh/tư thế đẹp giữ làm khung. <b>Người 1–2</b> (cổng sau) = ảnh người thật ghép vào, <b>giữ nguyên gương mặt</b>. Qwen-Edit tối đa <b>3 ảnh</b> = mẫu + 2 người.
      </p>
    </div>

    <!-- ALD 11/06/2026 - provider HuggingFace ĐÃ GỠ theo yêu cầu user (compose mặc định Self-host/Qwen). -->
    <!-- Số người ghép -->
    <div class="apl-fm-group">
      <p class="apl-fm-heading">Số đối tượng ghép vào mẫu</p>
      <div class="flex items-center gap-3">
        <div class="apl-stepper" style="max-width:130px">
          <button type="button" class="apl-step-btn" :disabled="personCount <= 1" @click="local.personCount = Math.max(1, personCount - 1)"><i class="bi bi-dash-lg" /></button>
          <span class="apl-step-val">{{ personCount }}</span>
          <button type="button" class="apl-step-btn" :disabled="personCount >= 2" @click="local.personCount = Math.min(2, personCount + 1)"><i class="bi bi-plus-lg" /></button>
        </div>
        <span class="apl-fm-hint flex-1 !mt-0">Cổng <code class="text-[10px] bg-gray-100 px-1 rounded">Ảnh mẫu</code> + <code class="text-[10px] bg-gray-100 px-1 rounded">{{ subjectLabel }} 1…{{ personCount }}</code> bên trái node.</span>
      </div>
    </div>

    <!-- Loại đối tượng + giữ mặt -->
    <div class="apl-fm-group">
      <p class="apl-fm-heading">Đối tượng ghép</p>
      <div class="grid grid-cols-2 gap-1.5">
        <button type="button" :class="['apl-fm-tile', local.subjectKind === 'person' && 'is-active']" @click="local.subjectKind = 'person'">
          <i class="bi bi-person text-base" /><span class="apl-fm-tile-label">Người</span>
        </button>
        <button type="button" :class="['apl-fm-tile', local.subjectKind === 'product' && 'is-active']" @click="local.subjectKind = 'product'">
          <i class="bi bi-bag text-base" /><span class="apl-fm-tile-label">Sản phẩm</span>
        </button>
      </div>
      <label v-if="local.subjectKind === 'person'" class="flex items-start gap-2 mt-3 cursor-pointer">
        <input v-model="local.keepFace" type="checkbox" class="rounded text-primary mt-0.5" />
        <span class="text-[11px] leading-snug">Giữ nguyên <b>khuôn mặt &amp; đặc điểm nhận dạng</b> (khuyến nghị — tránh Qwen vẽ lại mặt).</span>
      </label>
    </div>

    <!-- Ghi chú bối cảnh thêm -->
    <div class="apl-fm-group">
      <p class="apl-fm-heading">Yêu cầu thêm <span class="opacity-50 normal-case font-medium">(tuỳ chọn)</span></p>
      <input v-model="local.sceneNote" type="text" class="apl-fm-input" placeholder="VD: giữ trang phục gốc, ánh sáng hoàng hôn, tông ấm…" />
      <p class="apl-fm-hint">Thêm vào prompt tự động (phong cách, ánh sáng, giữ/đổi trang phục…).</p>
    </div>

    <!-- Prompt (auto-build, sửa được) -->
    <div class="apl-fm-group">
      <div class="flex items-center justify-between mb-2">
        <p class="apl-fm-heading !mb-0">Prompt gửi Qwen</p>
        <button type="button" class="apl-regen" :class="local.autoPrompt && 'is-on'" @click="enableAuto">
          <i class="bi bi-stars" /> {{ local.autoPrompt ? 'Tự động' : 'Tạo lại tự động' }}
        </button>
      </div>
      <textarea
        v-model="local.prompt"
        rows="5"
        class="apl-fm-input"
        style="height:auto;padding:8px 10px;font-family:inherit;line-height:1.5;resize:vertical"
        @input="local.autoPrompt = false"
      />
      <p class="apl-fm-hint">
        <template v-if="local.autoPrompt"><i class="bi bi-stars me-1 text-primary" />Tự sinh theo số người + tuỳ chọn. Sửa tay → tắt tự động.</template>
        <template v-else>Đang dùng prompt sửa tay. Bấm <b>Tạo lại tự động</b> để khôi phục.</template>
      </p>
    </div>
  </div>
</template>

<script setup>
const props = defineProps({
  config: { type: Object, required: true },
  nodeType: { type: String, default: 'compose' }
})
const emit = defineEmits(['update:config'])

const local = ref({
  provider: 'qwen',
  personCount: 1,
  subjectKind: 'person',
  keepFace: true,
  sceneNote: '',
  prompt: '',
  autoPrompt: true,
  ...props.config
})

const personCount = computed(() => Math.max(1, Math.min(2, Number(local.value.personCount) || 1)))
const subjectLabel = computed(() => (local.value.subjectKind === 'product' ? 'Sản phẩm' : 'Người'))

// Auto-build prompt — tham chiếu ảnh theo thứ tự (Ảnh 1 = mẫu, Ảnh 2/3 = đối tượng) cho Qwen-Edit.
function buildPrompt() {
  const n = personCount.value
  const isPerson = local.value.subjectKind !== 'product'
  const noun = isPerson ? 'người' : 'sản phẩm'
  const refs = n === 1 ? 'Ảnh 2' : 'Ảnh 2 và Ảnh 3'
  let s = `Đặt ${noun} ở ${refs} vào bối cảnh, bố cục và tư thế của Ảnh 1 (ảnh mẫu), thay cho đối tượng trong ảnh mẫu. `
  if (isPerson && local.value.keepFace) s += 'Giữ NGUYÊN khuôn mặt, đặc điểm nhận dạng và dáng người của mỗi người. '
  else if (!isPerson) s += 'Giữ nguyên kiểu dáng, màu sắc và chi tiết của sản phẩm. '
  s += 'Hoà hợp ánh sáng, màu sắc, bóng đổ và tỷ lệ tự nhiên với bối cảnh. Ảnh chân thực, độ phân giải cao, sắc nét.'
  const note = (local.value.sceneNote || '').trim()
  if (note) s += ' ' + note
  return s
}
function enableAuto() { local.value.autoPrompt = true; local.value.prompt = buildPrompt() }

// Khởi tạo prompt nếu trống
if (local.value.autoPrompt && !String(local.value.prompt || '').trim()) local.value.prompt = buildPrompt()
// Re-build khi đổi tham số (chỉ khi đang ở chế độ tự động)
watch(() => [personCount.value, local.value.subjectKind, local.value.keepFace, local.value.sceneNote], () => {
  if (local.value.autoPrompt) local.value.prompt = buildPrompt()
})

// ALD 11/06/2026 - workflow cũ lỡ lưu provider 'huggingface' (đã gỡ) → tự lành về qwen trước khi emit.
watch(local, (v) => {
  const out = { ...v }
  if (String(out.provider || '').toLowerCase() === 'huggingface') out.provider = 'qwen'
  emit('update:config', out)
}, { deep: true })
watch(() => props.config, (v) => {
  if (v && JSON.stringify(v) !== JSON.stringify(local.value)) local.value = { ...local.value, ...v }
})
</script>

<style scoped>
.apl-info-card { background: rgba(88,86,214,0.06); border: 0.5px solid rgba(88,86,214,0.2); border-radius: 12px; padding: 11px 12px; }
.apl-fm-group { background: var(--apl-fill); border: 0.5px solid rgba(235,236,240,0.12); border-radius: 14px; padding: 12px; }
.apl-fm-heading { font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; color: var(--apl-label); margin-bottom: 8px; }
.apl-fm-hint { margin-top: 6px; font-size: 10.5px; color: var(--apl-label); line-height: 1.4; }
.apl-fm-input { width: 100%; height: 32px; padding: 0 10px; background: var(--apl-bg-secondary); border: 0.5px solid rgba(235,236,240,0.18); border-radius: 9px; font-size: 12px; transition: border-color 0.18s; }
.apl-fm-input:focus { outline: none; border-color: var(--color-primary, #9AA2F2); }
.apl-fm-tile { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 3px; height: 52px; border-radius: 12px; border: 0.5px solid rgba(235,236,240,0.18); background: var(--apl-bg-secondary); color: var(--apl-label); transition: all 0.15s; }
.apl-fm-tile:hover { border-color: rgba(88,86,214,0.4); }
.apl-fm-tile.is-active { border-color: #5856D6; background: rgba(88,86,214,0.06); color: #3E3CA8; box-shadow: 0 0 0 1px #5856D6 inset; }
.apl-fm-tile-label { font-size: 11px; font-weight: 700; }
.apl-stepper { display: inline-flex; align-items: stretch; width: 100%; border: 0.5px solid rgba(235,236,240,0.18); border-radius: 10px; background: var(--apl-bg-secondary); overflow: hidden; }
.apl-step-btn { flex: 0 0 auto; width: 34px; display: flex; align-items: center; justify-content: center; color: var(--apl-label); transition: background 0.15s; }
.apl-step-btn:hover:not(:disabled) { background: rgba(235,236,240,0.06); color: #5856D6; }
.apl-step-btn:disabled { opacity: 0.3; cursor: not-allowed; }
.apl-step-val { flex: 1; text-align: center; font-size: 14px; font-weight: 700; font-variant-numeric: tabular-nums; line-height: 34px; border-left: 0.5px solid rgba(235,236,240,0.12); border-right: 0.5px solid rgba(235,236,240,0.12); }
.apl-regen { display: inline-flex; align-items: center; gap: 4px; cursor: pointer; font-size: 10.5px; font-weight: 700; padding: 3px 9px; border-radius: 999px; border: 0.5px solid rgba(235,236,240,0.18); background: var(--apl-bg-secondary); color: var(--apl-label); transition: all 0.15s; }
.apl-regen.is-on { background: #5856D6; border-color: #5856D6; color: white; }
.apl-regen i { font-size: 11px; }
</style>
