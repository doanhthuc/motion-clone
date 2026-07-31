<template>
  <div class="space-y-4">
    <div class="apl-info-card">
      <p class="font-semibold flex items-center gap-1.5">
        <i class="bi bi-bag-check-fill" /> Đặt sản phẩm vào cảnh
      </p>
      <p class="text-[11px] opacity-70 mt-1">
        Mặc định Qwen-Edit tự chọn cách đặt sản phẩm: cầm tay, trên bàn/kệ, đứng cạnh mẫu hoặc xe/đồ lớn. Nếu nhãn/bao bì cần đúng tuyệt đối, chuyển về <b>Safe packshot</b>.
      </p>
    </div>

    <div class="apl-fm-group">
      <p class="apl-fm-heading">Cổng input</p>
      <div class="grid gap-2 text-[11px] text-gray-600">
        <div class="flex items-center justify-between rounded-xl bg-gray-50 px-3 py-2">
          <span><b>Ảnh mẫu</b></span>
          <code>image1</code>
        </div>
        <div class="flex items-center justify-between rounded-xl bg-gray-50 px-3 py-2">
          <span><b>SP thật</b></span>
          <code>image2</code>
        </div>
      </div>
      <p class="apl-fm-hint">Workflow quảng cáo sản phẩm nên nối <code>session.model_image</code> vào image1 và <code>session.product_image</code> vào image2.</p>
    </div>

    <div class="apl-fm-group">
      <p class="apl-fm-heading">Chế độ dựng frame</p>
      <div class="grid grid-cols-2 gap-1.5">
        <button type="button" :class="['apl-fm-tile', local.mode !== 'safe-packshot' && 'is-active']" @click="local.mode = 'natural-hold'">
          <i class="bi bi-stars text-base" /><span class="apl-fm-tile-label">Đặt tự nhiên</span>
        </button>
        <button type="button" :class="['apl-fm-tile', local.mode === 'safe-packshot' && 'is-active']" @click="local.mode = 'safe-packshot'">
          <i class="bi bi-shield-check text-base" /><span class="apl-fm-tile-label">Safe packshot</span>
        </button>
      </div>
      <p class="apl-fm-hint">
        <b>Đặt tự nhiên</b> đẹp hơn nhưng có thể lệch nhãn nhẹ. <b>Safe packshot</b> đúng sản phẩm nhất nhưng nhìn giống card/overlay.
      </p>
    </div>

    <template v-if="local.mode !== 'safe-packshot'">
      <div class="apl-fm-group">
        <p class="apl-fm-heading">Cách đặt sản phẩm</p>
        <select v-model="local.productPlacement" class="apl-fm-input">
          <option value="auto">Auto theo kịch bản</option>
          <option value="handheld">Cầm tay: serum / điện thoại / chai nhỏ</option>
          <option value="tabletop">Đặt bàn/kệ: laptop / giày / túi / máy nhỏ</option>
          <option value="large-display">Đứng cạnh mẫu: TV / tủ lạnh / sofa</option>
          <option value="vehicle">Xe / đồ rất lớn</option>
        </select>
        <p class="apl-fm-hint">Auto do AI Director set khi dựng từ kịch bản. Chọn tay nếu sản phẩm đặc biệt hoặc Qwen hiểu sai kích thước.</p>
      </div>

      <div class="apl-fm-group">
        <p class="apl-fm-heading">Prompt đặt sản phẩm</p>
        <textarea
          v-model="local.prompt"
          rows="5"
          class="apl-fm-input"
          style="height:auto;padding:8px 10px;line-height:1.45;resize:vertical"
          placeholder="Để trống để dùng prompt mặc định theo cách đặt sản phẩm đã chọn..."
        />
        <p class="apl-fm-hint">Prompt này gửi Qwen-Edit với 2 ảnh tham chiếu: image1 là mẫu, image2 là sản phẩm.</p>
      </div>

      <div class="apl-fm-group">
        <p class="apl-fm-heading">Negative prompt</p>
        <textarea
          v-model="local.negativePrompt"
          rows="3"
          class="apl-fm-input"
          style="height:auto;padding:8px 10px;line-height:1.45;resize:vertical"
        />
      </div>
    </template>

    <template v-else>
      <div class="apl-fm-group">
        <p class="apl-fm-heading">Vị trí sản phẩm</p>
        <select v-model="local.position" class="apl-fm-input">
          <option value="bottom-right">Dưới phải</option>
          <option value="bottom-left">Dưới trái</option>
          <option value="top-right">Trên phải</option>
          <option value="top-left">Trên trái</option>
          <option value="center">Giữa ảnh</option>
        </select>
        <p class="apl-fm-hint">Dùng packshot nhỏ, rõ nhãn. Chế độ này không vẽ lại sản phẩm nên giữ nhãn chính xác nhất.</p>
      </div>

      <div class="apl-fm-group">
        <div class="flex items-center justify-between gap-3">
          <p class="apl-fm-heading !mb-0">Kích thước</p>
          <span class="text-[11px] font-semibold text-blue-700">{{ Math.round(scalePct) }}%</span>
        </div>
        <input v-model.number="local.scale" type="range" min="0.12" max="0.58" step="0.01" class="w-full accent-blue-600" />
        <p class="apl-fm-hint">Tính theo cạnh ngắn của ảnh mẫu. Mặc định 34% để sản phẩm đủ nổi nhưng không che mặt.</p>
      </div>

      <div class="apl-fm-group">
        <div class="flex items-center justify-between gap-3">
          <p class="apl-fm-heading !mb-0">Lề</p>
          <span class="text-[11px] font-semibold text-blue-700">{{ Math.round(paddingPct) }}%</span>
        </div>
        <input v-model.number="local.padding" type="range" min="0" max="0.12" step="0.005" class="w-full accent-blue-600" />
      </div>

      <label class="apl-check-row">
        <input v-model="local.card" type="checkbox" class="rounded text-blue-600 mt-0.5" />
        <span>
          <b>Đặt sản phẩm trên card trắng</b>
          <small>Giúp packshot nổi hơn và tránh lẫn với nền.</small>
        </span>
      </label>

      <div class="apl-fm-group">
        <p class="apl-fm-heading">Nhãn nhỏ <span class="opacity-50 normal-case font-medium">(tuỳ chọn)</span></p>
        <input v-model="local.label" type="text" class="apl-fm-input" placeholder="VD: 40ml · Chính hãng" />
      </div>
    </template>
  </div>
</template>

<script setup>
const props = defineProps({
  config: { type: Object, required: true },
  nodeType: { type: String, default: 'product-overlay' }
})
const emit = defineEmits(['update:config'])

const DEFAULT_NEGATIVE = 'different person, changed face, wrong product, changed product label, fake product packaging, distorted logo, unreadable label, floating sticker, pasted card, extra fingers, missing fingers, fused fingers, mutated fingers, deformed hand, hand wrapped around product, blurry, low quality'

const local = ref({
  mode: 'natural-hold',
  productPlacement: 'auto',
  position: 'bottom-right',
  scale: 0.28,
  padding: 0.035,
  card: false,
  label: '',
  prompt: '',
  negativePrompt: DEFAULT_NEGATIVE,
  ...props.config
})

const scalePct = computed(() => Number(local.value.scale || 0.34) * 100)
const paddingPct = computed(() => Number(local.value.padding || 0.035) * 100)

watch(local, (v) => {
  const mode = v.mode === 'safe-packshot' ? 'safe-packshot' : 'natural-hold'
  emit('update:config', {
    ...v,
    mode,
    productPlacement: v.productPlacement || 'auto',
    scale: Number(v.scale) || (mode === 'safe-packshot' ? 0.34 : 0.28),
    padding: Number(v.padding) || 0.035,
    card: mode === 'safe-packshot' ? v.card !== false : false,
    negativePrompt: v.negativePrompt || DEFAULT_NEGATIVE
  })
}, { deep: true })

watch(() => props.config, (v) => {
  if (v && JSON.stringify(v) !== JSON.stringify(local.value)) local.value = { ...local.value, ...v }
})
</script>

<style scoped>
.apl-info-card { background: rgba(10,132,255,0.07); border: 0.5px solid rgba(10,132,255,0.22); border-radius: 12px; padding: 11px 12px; }
.apl-fm-group { background: var(--apl-fill); border: 0.5px solid rgba(235,236,240,0.12); border-radius: 14px; padding: 12px; }
.apl-fm-heading { font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; color: var(--apl-label); margin-bottom: 8px; }
.apl-fm-hint { margin-top: 6px; font-size: 10.5px; color: var(--apl-label); line-height: 1.4; }
.apl-fm-input { width: 100%; height: 34px; padding: 0 10px; background: var(--apl-bg-secondary); border: 0.5px solid rgba(235,236,240,0.18); border-radius: 10px; font-size: 12px; transition: border-color 0.18s; }
.apl-fm-input:focus { outline: none; border-color: #0A84FF; }
.apl-fm-tile { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 3px; min-height: 56px; border-radius: 12px; border: 0.5px solid rgba(235,236,240,0.18); background: var(--apl-bg-secondary); color: var(--apl-label); transition: all 0.15s; }
.apl-fm-tile:hover { border-color: rgba(10,132,255,0.4); }
.apl-fm-tile.is-active { border-color: #0A84FF; background: rgba(10,132,255,0.07); color: #0757A8; box-shadow: 0 0 0 1px #0A84FF inset; }
.apl-fm-tile-label { font-size: 11px; font-weight: 700; }
.apl-check-row { display: flex; align-items: flex-start; gap: 10px; cursor: pointer; border: 0.5px solid rgba(10,132,255,0.2); background: rgba(10,132,255,0.06); border-radius: 14px; padding: 12px; font-size: 12px; color: rgba(0,0,0,0.78); }
.apl-check-row small { display: block; margin-top: 2px; font-size: 10.5px; color: rgba(71,85,105,0.68); }
</style>
