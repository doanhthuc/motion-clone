<template>
  <!-- ALD 01/07/2026 - Node "Sửa ảnh" (edit-image): SỬA ảnh CÓ SẴN theo mô tả. Nhận LIST ảnh (1..6 cổng),
       mỗi ảnh sinh 1-5 version. GIỮ NGUYÊN bố cục/nhân dạng ảnh gốc, chỉ đổi phần được yêu cầu.
       2 provider: Qwen-Edit (self-host, mặc định) · Gemini Nano Banana (admin, cần key). KHÔNG Flux (Flux
       không sửa được ảnh). Xong version nào hiện version đó ngay trên node. -->
  <div class="space-y-3">
    <div class="apl-info-card">
      <p class="font-semibold flex items-center gap-1.5">
        <i class="bi bi-info-circle-fill text-sky-500" />
        Sửa ảnh
      </p>
      <p class="text-[11px] opacity-70 mt-1">
        Nối 1..6 <b>ảnh có sẵn</b> vào các cổng bên trái + mô tả cách sửa. Mỗi ảnh giữ nguyên bố cục/nhân dạng,
        chỉ đổi theo mô tả. Có thể sinh <b>nhiều version</b> cho mỗi ảnh — xong version nào hiện version đó.
      </p>
    </div>

    <!-- Mô tả sửa -->
    <div>
      <label class="apl-fm-label flex items-center gap-1.5">
        Mô tả cách sửa <span class="text-rose-600">*</span>
      </label>
      <textarea
        v-model="local.prompt"
        rows="4"
        spellcheck="false"
        placeholder="VD: Đổi nền thành nhà máy thép, giữ nguyên người và trang phục. / Chuyển sang phong cách hoạt hình. / Xoá logo góc phải."
        class="apl-fm-input mt-1 text-xs leading-relaxed"
        style="height:auto;min-height:92px;padding:8px 10px;line-height:1.5;resize:vertical"
      />
      <p class="apl-fm-hint">
        Nói rõ <b>sửa gì</b> và <b>giữ gì</b>. Càng cụ thể, ảnh càng bám ý (giữ mặt/dáng/nền nếu không muốn đổi).
      </p>
    </div>

    <!-- Negative prompt -->
    <div>
      <label class="apl-fm-label">Negative prompt <span class="font-normal text-gray-400">(thứ KHÔNG muốn xuất hiện — tuỳ chọn)</span></label>
      <textarea
        v-model="local.negativePrompt"
        rows="2"
        spellcheck="false"
        placeholder="VD: blurry, deformed, extra fingers, watermark…"
        class="apl-fm-input mt-1 text-xs leading-relaxed"
        style="height:auto;min-height:56px;padding:8px 10px;line-height:1.5;resize:vertical"
      />
      <p class="apl-fm-hint"><i class="bi bi-info-circle me-1" />Áp dụng cho provider Qwen.</p>
    </div>

    <!-- Số ảnh đầu vào (số cổng) -->
    <div>
      <label class="apl-fm-label">Số ảnh cần sửa</label>
      <div class="flex items-center gap-3 mt-1.5">
        <div class="inline-flex items-center rounded-lg border border-gray-200 bg-gray-50 shadow-sm overflow-hidden select-none">
          <button
            type="button"
            class="w-9 h-9 flex items-center justify-center text-gray-600 hover:bg-gray-100 active:bg-gray-200 disabled:opacity-30 disabled:cursor-not-allowed transition"
            :disabled="inputCount <= 1"
            @click="local.inputCount = Math.max(1, inputCount - 1)"
          >
            <i class="bi bi-dash-lg" />
          </button>
          <span class="w-10 text-center text-sm font-bold tabular-nums border-x border-gray-200 self-stretch flex items-center justify-center">
            {{ inputCount }}
          </span>
          <button
            type="button"
            class="w-9 h-9 flex items-center justify-center text-gray-600 hover:bg-gray-100 active:bg-gray-200 disabled:opacity-30 disabled:cursor-not-allowed transition"
            :disabled="inputCount >= 6"
            @click="local.inputCount = Math.min(6, inputCount + 1)"
          >
            <i class="bi bi-plus-lg" />
          </button>
        </div>
        <span class="apl-fm-hint flex-1 !mt-0">
          Mỗi cổng <code class="text-[10px] bg-gray-100 px-1 rounded">{{ inputCount === 1 ? 'image1' : 'image1…image' + inputCount }}</code> nhận 1 ảnh. Cùng 1 mô tả áp cho từng ảnh.
        </span>
      </div>
    </div>

    <!-- ALD 03/07/2026 - GHÉP ảnh: ≥2 ảnh + bật → 1 lần Qwen multi-ref ghép TẤT CẢ ảnh thành 1 ảnh mới
         (vd: ảnh 1 người mẫu + ảnh 2 sản phẩm → "người mẫu cầm sản phẩm"). Thay node "Đặt sản phẩm" cũ. -->
    <div v-if="inputCount >= 2">
      <label class="flex items-center justify-between gap-2 cursor-pointer p-2.5 rounded-lg border border-gray-200 bg-gray-50">
        <span class="min-w-0">
          <span class="apl-fm-label !mb-0 flex items-center gap-1.5"><i class="bi bi-intersect text-sky-500" /> Ghép các ảnh thành 1</span>
          <span class="apl-fm-hint block !mt-0.5">Ảnh 1 = người/bối cảnh chính (giữ nhân dạng); ảnh 2+ = đối tượng chèn vào (giữ đúng bao bì/nhãn). Tắt = sửa từng ảnh riêng.</span>
        </span>
        <input v-model="local.combine" type="checkbox" class="apl-ei-check" />
      </label>
    </div>

    <!-- Số version / ảnh -->
    <div>
      <label class="apl-fm-label">Số version mỗi ảnh</label>
      <div class="flex items-center gap-3 mt-1.5">
        <div class="inline-flex items-center rounded-lg border border-gray-200 bg-gray-50 shadow-sm overflow-hidden select-none">
          <button
            type="button"
            class="w-9 h-9 flex items-center justify-center text-gray-600 hover:bg-gray-100 active:bg-gray-200 disabled:opacity-30 disabled:cursor-not-allowed transition"
            :disabled="outputCount <= 1"
            @click="local.outputCount = Math.max(1, outputCount - 1)"
          >
            <i class="bi bi-dash-lg" />
          </button>
          <span class="w-10 text-center text-sm font-bold tabular-nums border-x border-gray-200 self-stretch flex items-center justify-center">
            {{ outputCount }}
          </span>
          <button
            type="button"
            class="w-9 h-9 flex items-center justify-center text-gray-600 hover:bg-gray-100 active:bg-gray-200 disabled:opacity-30 disabled:cursor-not-allowed transition"
            :disabled="outputCount >= 5"
            @click="local.outputCount = Math.min(5, outputCount + 1)"
          >
            <i class="bi bi-plus-lg" />
          </button>
        </div>
        <span class="apl-fm-hint flex-1 !mt-0">
          Mỗi ảnh render tối đa <b>5</b> biến thể (seed khác nhau). Tổng kết quả = <b>{{ inputCount * outputCount }}</b> ảnh, nằm trong <code class="text-[10px] bg-gray-100 px-1 rounded">images[]</code>.
        </span>
      </div>
    </div>

    <!-- Chất lượng (native) -->
    <div>
      <label class="apl-fm-label">Chất lượng <span class="font-normal text-gray-400">(render native)</span></label>
      <div class="grid grid-cols-2 gap-1.5 mt-1.5">
        <button
          v-for="q in QUALITIES"
          :key="q.id"
          type="button"
          :class="['apl-q-tile', local.quality === q.id && 'is-active']"
          @click="local.quality = q.id"
        >
          <b>{{ q.label }}</b>
          <small>{{ q.hint }}</small>
        </button>
      </div>
    </div>

    <!-- Provider -->
    <div>
      <label class="apl-fm-label">Provider</label>
      <div class="grid grid-cols-2 gap-1.5 mt-1.5">
        <button
          type="button"
          :class="['apl-fm-tile', local.provider === 'qwen' && 'is-active']"
          @click="local.provider = 'qwen'"
        >
          <i class="bi bi-hdd-stack text-base" />
          <span class="apl-fm-tile-label">Self-host</span>
        </button>
        <button
          v-if="isAdmin"
          type="button"
          :class="['apl-fm-tile', local.provider === 'gemini' && 'is-active']"
          @click="local.provider = 'gemini'"
        >
          <i class="bi bi-google text-base" />
          <span class="apl-fm-tile-label">Gemini</span>
        </button>
      </div>
      <p class="apl-fm-hint">
        <span v-if="local.provider === 'gemini'">
          <i class="bi bi-info-circle me-1" />Gemini Nano Banana (Google) — sửa vật nhỏ/chi tiết chính xác. Cần API key + billing.
        </span>
        <span v-else>
          <i class="bi bi-info-circle me-1" />Qwen-Image-Edit self-host (GPU local). Sửa/giữ nhân dạng tốt, miễn phí.
        </span>
      </p>
    </div>

    <!-- Model Gemini (self-host chỉ có Qwen-Edit nên không cần dropdown) -->
    <div v-if="local.provider === 'gemini' && isAdmin" class="relative">
      <label class="apl-fm-label">Model Gemini</label>
      <button type="button" class="apl-fm-input mt-1.5 w-full flex items-center justify-between gap-2 text-xs" @click="openModelDrop = !openModelDrop">
        <span class="flex items-center gap-2"><i :class="['bi', currentGeminiModel.icon, 'text-amber-500']" />{{ currentGeminiModel.label }}</span>
        <i :class="['bi bi-chevron-down text-gray-400 transition-transform', openModelDrop && 'rotate-180']" />
      </button>
      <template v-if="openModelDrop">
        <div class="fixed inset-0 z-10" @click="openModelDrop = false" />
        <div class="absolute z-20 mt-1 w-full bg-gray-50 border border-gray-200 rounded-lg shadow-lg overflow-hidden py-1">
          <button
            v-for="m in GEMINI_MODELS" :key="m.id" type="button"
            :class="['w-full flex items-center gap-2 px-3 py-2 text-xs text-left hover:bg-gray-50 transition', local.geminiModel === m.id && 'bg-amber-50 text-amber-700 font-semibold']"
            @click="local.geminiModel = m.id; openModelDrop = false"
          ><i :class="['bi', m.icon]" />{{ m.label }}</button>
        </div>
      </template>
    </div>

    <!-- Gemini API key -->
    <div v-if="local.provider === 'gemini' && !isAdmin" class="apl-info-card !bg-rose-50 !border-rose-200 !text-rose-700">
      <p class="flex items-center gap-1.5"><i class="bi bi-lock-fill" /> Gemini chỉ dành cho admin. Chọn <b>Self-host</b> (Qwen-Edit) để chạy.</p>
    </div>
    <div v-else-if="local.provider === 'gemini'">
      <div v-if="apiKeyAlreadySaved && !editingApiKey" class="flex items-center justify-between gap-2 p-2.5 rounded-lg border border-emerald-200 bg-emerald-50">
        <div class="flex items-center gap-2 min-w-0">
          <i class="bi bi-shield-check-fill text-emerald-600 text-base" />
          <div class="min-w-0">
            <div class="text-xs font-semibold text-emerald-900">Gemini API Key đã lưu</div>
            <div class="text-[10px] text-emerald-700 truncate">Key bảo mật ở server, sẵn sàng dùng cho mọi run</div>
          </div>
        </div>
        <div class="flex items-center gap-2 shrink-0">
          <button type="button" class="text-[11px] font-semibold text-primary hover:underline whitespace-nowrap inline-flex items-center gap-1" @click="editingApiKey = true">
            <i class="bi bi-pencil-square" /> Đổi
          </button>
          <button type="button" class="text-[11px] font-semibold text-rose-600 hover:underline whitespace-nowrap inline-flex items-center gap-1" @click="clearApiKey">
            <i class="bi bi-trash" /> Xoá
          </button>
        </div>
      </div>
      <div v-else>
        <label class="apl-fm-label flex items-center gap-1.5">
          Gemini API Key
          <span v-if="!apiKeyAlreadySaved" class="text-[10px] font-normal text-gray-400">(tuỳ chọn)</span>
          <button v-if="apiKeyAlreadySaved" type="button" class="ms-auto text-[10px] text-gray-500 hover:underline" @click="cancelChangeApiKey">
            Huỷ — giữ key cũ
          </button>
        </label>
        <input
          v-model="local.apiKey"
          type="password"
          autocomplete="off"
          spellcheck="false"
          placeholder="AIzaSy..."
          class="apl-fm-input mt-1 font-mono text-xs"
        />
        <p class="apl-fm-hint">
          <i class="bi bi-shield-lock me-1" />Để trống = dùng <b>key hệ thống</b>. Hoặc nhập key riêng (lưu server-side) — lấy free tại
          <a href="https://aistudio.google.com/apikey" target="_blank" class="text-primary hover:underline">aistudio.google.com/apikey</a>.
        </p>
      </div>
    </div>
  </div>
</template>

<script setup>
const props = defineProps({
  config: { type: Object, required: true },
  nodeType: { type: String, default: 'edit-image' }
})
const emit = defineEmits(['update:config'])

const normalizeProvider = (p) => (String(p || '').toLowerCase() === 'gemini' ? 'gemini' : 'qwen')

// ALD 01/07/2026 - Gemini chỉ admin (khớp InspectorCreateImage): role nằm trong JWT claim.
const auth = useAuth()
const isAdmin = computed(() => decodeJwtPayload(auth.token.value)?.role === 'admin')

const clearedApiKey = ref(false)
const apiKeyAlreadySaved = computed(() => !clearedApiKey.value && Boolean(
  props.config?.__apiKey_isSet || props.config?.__geminiApiKey_isSet,
))
const editingApiKey = ref(false)
function cancelChangeApiKey() { editingApiKey.value = false; local.value.apiKey = '' }
function clearApiKey() {
  clearedApiKey.value = true
  editingApiKey.value = false
  local.value = { ...local.value, apiKey: '' }
}

const QUALITIES = [
  { id: '1080', label: '1080', hint: '~1MP · nhanh' },
  { id: '2k', label: '2K', hint: '~1.6MP · nét' },
]
const GEMINI_MODELS = [
  { id: 'nano-banana-pro', label: 'Nano Banana Pro', icon: 'bi-stars' },
  { id: 'nano-banana', label: 'Nano Banana', icon: 'bi-lightning-charge-fill' },
]
const openModelDrop = ref(false)
const currentGeminiModel = computed(() => GEMINI_MODELS.find((m) => m.id === local.value.geminiModel) || GEMINI_MODELS[0])

const local = ref({
  provider: 'qwen',
  model: 'qwen-edit',
  geminiModel: 'nano-banana-pro',
  prompt: '',
  negativePrompt: '',
  inputCount: 1,   // số cổng ảnh vào (list ảnh cần sửa), 1-6
  combine: false,  // ALD 03/07/2026 - true + ≥2 ảnh → GHÉP tất cả thành 1 ảnh (Qwen multi-ref) thay vì sửa từng ảnh
  outputCount: 1,  // số version mỗi ảnh, 1-5
  quality: '1080', // '1080' | '2k' (native)
  apiKey: '',
  ...props.config,
  provider: normalizeProvider(props.config?.provider),
  apiKey: String(props.config?.apiKey || '').trim() || '',
})
const inputCount = computed(() => Math.max(1, Math.min(6, Number(local.value.inputCount) || 1)))
const outputCount = computed(() => Math.max(1, Math.min(5, Number(local.value.outputCount) || 1)))

watch(local, (v) => {
  const out = { ...v }
  if (clearedApiKey.value && !String(v.apiKey || '').trim()) out.__apiKey_clear = true
  emit('update:config', out)
}, { deep: true })
watch(() => props.config, (v) => {
  if (v && JSON.stringify(v) !== JSON.stringify(local.value)) {
    local.value = { ...local.value, ...v, provider: normalizeProvider(v.provider) }
  }
})
</script>

<style scoped>
/* Chất lượng / provider tile (đồng bộ InspectorCreateImage) */
.apl-q-tile { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 1px; height: 44px; border-radius: 12px; border: 0.5px solid rgba(235,236,240,0.18); background: var(--apl-bg-secondary); color: var(--apl-label); transition: all 0.15s; }
.apl-q-tile b { font-size: 12px; font-weight: 700; }
.apl-q-tile small { font-size: 9.5px; opacity: 0.6; }
.apl-q-tile:hover { border-color: rgba(88,86,214,0.4); }
.apl-q-tile.is-active { border-color: #5856D6; background: rgba(88,86,214,0.06); color: #3E3CA8; box-shadow: 0 0 0 1px #5856D6 inset; }
.apl-ei-check { width: 18px; height: 18px; accent-color: #5AC8FA; flex-shrink: 0; }
</style>
