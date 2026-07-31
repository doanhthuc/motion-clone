<template>
  <!-- #region ALD 28/05/2026 - Tryon node inspector. 2 provider:
       - Qwen Edit (Qwen-Image-Edit-2509, default, GPU local, Apache 2.0, ~30s)
       - Gemini (Google API, cần billing + apiKey user, ~15s)
       Leffa đã remove (quality kém + identity loss). Workflow cũ provider='local'
       coerce → 'qwen' automatic. -->
  <div class="space-y-3">
    <div class="apl-info-card">
      <p class="font-semibold flex items-center gap-1.5">
        <i class="bi bi-info-circle-fill text-amber-500" />
        Try-on
      </p>
    </div>

    <!-- Provider selector -->
    <!-- ALD 11/06/2026 - provider HuggingFace ĐÃ GỠ theo yêu cầu user; backend còn nhánh HF ngủ đông. -->
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
          <i class="bi bi-info-circle me-1" />Gọi Gemini Nano Banana (Google). Cần API key + billing. Quality cao nhất.
        </span>
      </p>
    </div>

    <!-- ALD 27/05/2026 - Gemini API key. Mỗi user nạp key của mình (quota riêng).
         BE strip apiKey khi GET → __apiKey_isSet=true báo FE đã có key.
         ALD 28/05/2026 - UX: nếu đã có key → ẨN input, show "Đổi key" button. -->
    <div v-if="local.provider === 'gemini' && !isAdmin" class="apl-info-card !bg-rose-50 !border-rose-200 !text-rose-700">
      <p class="flex items-center gap-1.5"><i class="bi bi-lock-fill" /> Gemini chỉ dành cho admin. Chọn <b>Qwen Edit</b> để chạy.</p>
    </div>
    <div v-else-if="local.provider === 'gemini'">
      <!-- Trạng thái 1: đã có key, chưa click "Đổi key" → show summary + nút -->
      <div v-if="apiKeyAlreadySaved && !editingApiKey" class="flex items-center justify-between gap-2 p-2.5 rounded-lg border border-emerald-200 bg-emerald-50">
        <div class="flex items-center gap-2 min-w-0">
          <i class="bi bi-shield-check-fill text-emerald-600 text-base" />
          <div class="min-w-0">
            <div class="text-xs font-semibold text-emerald-900">Gemini API Key đã lưu</div>
            <div class="text-[10px] text-emerald-700 truncate">Key bảo mật ở server, sẵn sàng dùng cho mọi run</div>
          </div>
        </div>
        <div class="flex items-center gap-2 shrink-0">
          <button
            type="button"
            class="text-[11px] font-semibold text-primary hover:underline whitespace-nowrap inline-flex items-center gap-1"
            @click="editingApiKey = true"
          >
            <i class="bi bi-pencil-square" /> Đổi
          </button>
          <button
            type="button"
            class="text-[11px] font-semibold text-rose-600 hover:underline whitespace-nowrap inline-flex items-center gap-1"
            @click="clearApiKey"
          >
            <i class="bi bi-trash" /> Xoá
          </button>
        </div>
      </div>

      <!-- Trạng thái 2: chưa có key HOẶC user click "Đổi key" → show input -->
      <div v-else>
        <label class="apl-fm-label flex items-center gap-1.5">
          Gemini API Key
          <span v-if="!apiKeyAlreadySaved" class="text-[10px] font-normal text-gray-400">(tuỳ chọn)</span>
          <button
            v-if="apiKeyAlreadySaved"
            type="button"
            class="ms-auto text-[10px] text-gray-500 hover:underline"
            @click="cancelChangeApiKey"
          >
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
          <i class="bi bi-shield-lock me-1" />Để trống = dùng <b>key hệ thống</b> (admin đã cấu hình sẵn). Hoặc nhập key riêng (lưu server-side, user khác không thấy) — lấy free tại
          <a href="https://aistudio.google.com/apikey" target="_blank" class="text-primary hover:underline">aistudio.google.com/apikey</a>.
        </p>
        <p v-if="apiKeyLooksInvalid" class="apl-fm-hint !text-rose-600">
          <i class="bi bi-exclamation-triangle me-1" />Key chưa đúng định dạng — phải bắt đầu <b>AIza</b>, ~39 ký tự, KHÔNG khoảng trắng. (Có vẻ dán nhầm text khác.)
        </p>
      </div>
    </div>

    <!-- ALD 21/06/2026 - Toggle CHỈ LÀM SẠCH ảnh (không thay đồ): tái sinh ảnh model cho sạch/nét/full-body → ref đẹp cho Motion Transfer. KHÔNG cần ảnh sản phẩm. -->
    <div>
      <span class="apl-fm-label">Chế độ</span>
      <div class="grid grid-cols-2 gap-1.5 mt-1.5">
        <button type="button" :class="['apl-fm-tile', !local.cleanOnly && 'is-active']" @click="local.cleanOnly = false">
          <i class="bi bi-person-bounding-box text-base" /><span class="apl-fm-tile-label">Thay đồ</span>
        </button>
        <button type="button" :class="['apl-fm-tile', local.cleanOnly && 'is-active']" @click="local.cleanOnly = true">
          <i class="bi bi-stars text-base" /><span class="apl-fm-tile-label">Chỉ làm sạch ảnh</span>
        </button>
      </div>
      <p class="apl-fm-hint">
        <i class="bi bi-info-circle me-1" /><b>Chỉ làm sạch</b>: KHÔNG thay đồ — chỉ tái sinh ảnh cho sạch/nét/đủ người (full-body, sáng đều), làm ref đẹp cho Motion Transfer. Chỉ cần ảnh người, không cần ảnh sản phẩm.
      </p>
    </div>

    <!-- Loại sản phẩm. 'Auto ✨' = vision (Ollama) tự nhận loại đồ ở worker; còn lại = ép thủ công. -->
    <div v-if="!local.cleanOnly">
      <label class="apl-fm-label">Loại sản phẩm</label>
      <div class="grid grid-cols-2 gap-1.5 mt-1.5">
        <button
          v-for="g in GARMENT_TYPES"
          :key="g.id"
          type="button"
          :class="['apl-fm-tile', local.garmentType === g.id && 'is-active']"
          @click="local.garmentType = g.id"
        >
          <i :class="['bi text-base', g.icon]" />
          <span class="apl-fm-tile-label">{{ g.label }}</span>
        </button>
      </div>
      <p class="apl-fm-hint">
        <!-- ALD 10/07/2026 - Auto mở rộng: bê TOÀN BỘ đồ thời trang (quần áo + tất/vớ đùi + giày + phụ kiện). -->
        <template v-if="local.garmentType === 'auto'"><b>Tự động</b>: mặc <b>toàn bộ</b> đồ thời trang trong <b>ảnh sản phẩm</b> lên người mẫu — quần áo, váy, tất/vớ đùi, giày, phụ kiện (trang sức, mũ, thắt lưng, túi, kính...). Ảnh SP không có món nào thì không bịa món đó. Chọn loại cụ thể bên dưới nếu muốn ép chính xác 1 món.</template>
        <template v-else>Đang thay loại <b>{{ GARMENT_TYPES.find(g => g.id === local.garmentType)?.label }}</b>. Chọn đúng loại đồ để thay chính xác (váy, áo, quần, set...).</template>
      </p>
    </div>

    <!-- ALD 01/07/2026 - Ghi chú thêm (tuỳ chọn): user dặn thêm điều muốn GIỮ/ĐỔI ngoài thay quần áo,
         vd "giữ nguyên nón và trang sức", "thêm kính râm". Nhập tiếng Việt được — worker tự dịch EN cho Qwen. -->
    <div v-if="!local.cleanOnly">
      <label class="apl-fm-label">Ghi chú thêm <span class="font-normal text-gray-400">(tuỳ chọn)</span></label>
      <textarea
        v-model="local.extraPrompt"
        rows="2"
        spellcheck="false"
        placeholder="VD: Giữ nguyên nón và trang sức người mẫu đang đeo. / Thêm kính râm. / Đừng đổi giày."
        class="apl-fm-input mt-1 text-xs leading-relaxed"
        style="height:auto;min-height:52px;padding:8px 10px;line-height:1.5;resize:vertical"
      />
      <p class="apl-fm-hint">
        <i class="bi bi-info-circle me-1" />Dặn thêm điều muốn <b>giữ</b> hoặc <b>đổi</b> ngoài quần áo (nón, trang sức, phụ kiện…). Nhập tiếng Việt cũng được — tự dịch sang tiếng Anh cho Qwen.
      </p>
    </div>

    <!-- #region ALD 10/06/2026 - Số ảnh sản phẩm (góc độ): 2 ảnh → thêm cổng "Ảnh SP 2" trên node, AI thấy mặt sau/bên hông → render đúng sản phẩm từ mọi góc. -->
    <div v-if="!local.cleanOnly">
      <label class="apl-fm-label">Số góc độ sản phẩm</label>
      <div class="grid grid-cols-2 gap-1.5 mt-1.5">
        <button
          v-for="n in [1, 2]"
          :key="n"
          type="button"
          :class="['apl-fm-tile', (Number(local.productCount) || 1) === n && 'is-active']"
          @click="local.productCount = n"
        >
          <i :class="['bi text-base', n === 1 ? 'bi-image' : 'bi-images']" />
          <span class="apl-fm-tile-label">{{ n === 1 ? '1 ảnh' : '2 ảnh (đa góc)' }}</span>
        </button>
      </div>
      <p class="apl-fm-hint">
        <i class="bi bi-info-circle me-1" />Chọn <b>2 ảnh</b> → node có thêm cổng <b>Ảnh SP 2</b>: nối ảnh
        <b>mặt sau / bên hông</b> của CÙNG sản phẩm để AI render đúng khi người mẫu quay lưng hoặc xoay người.
      </p>
    </div>

    <!-- ALD 21/06/2026 - Toggle GHÉP NỀN: bật → node hiện cổng "Nền" để nối 1 ảnh phòng/địa điểm; worker ghép
         người (đã thay đồ) vào bối cảnh đó (Qwen pass 2). Tắt = giữ nền gốc của ảnh model. -->
    <div>
      <label class="apl-fm-label">Ghép nền (bối cảnh)</label>
      <div class="grid grid-cols-2 gap-1.5 mt-1.5">
        <button
          type="button"
          :class="['apl-fm-tile', !local.useBackground && 'is-active']"
          @click="local.useBackground = false"
        >
          <i class="bi bi-image text-base" />
          <span class="apl-fm-tile-label">Tắt</span>
        </button>
        <button
          type="button"
          :class="['apl-fm-tile', local.useBackground && 'is-active']"
          @click="local.useBackground = true"
        >
          <i class="bi bi-houses text-base" />
          <span class="apl-fm-tile-label">Bật · có cổng Nền</span>
        </button>
      </div>
      <p class="apl-fm-hint">
        <i class="bi bi-info-circle me-1" />Bật → node hiện cổng <b>Nền</b>: nối 1 ảnh <b>phòng/địa điểm</b> →
        AI ghép người (đã thay đồ) vào bối cảnh đó. Tắt = giữ nền gốc của ảnh model.
      </p>
    </div>

    <!-- ALD 15/06/2026 - Độ sáng output: đồ TRẮNG/sáng hay bị CHÁY SÁNG → kéo xuống ÂM. 0 = gốc. -->
    <div>
      <label class="apl-fm-label">Độ sáng output</label>
      <input v-model.number="local.brightness" type="range" min="-0.3" max="0.2" step="0.02" class="w-full mt-1.5" >
      <p class="apl-fm-hint">Hệ số: <b>{{ Number(local.brightness || 0).toFixed(2) }}</b>. Đồ <b>trắng/sáng</b> bị cháy → kéo về ÂM (vd −0.1). 0 = giữ gốc.</p>
    </div>

    <!-- Độ phân giải output -->
    <div>
      <label class="apl-fm-label">Độ phân giải output</label>
      <div class="grid grid-cols-4 gap-1.5 mt-1.5">
        <button
          v-for="r in RES_OPTS"
          :key="r.id"
          type="button"
          :class="['apl-fm-tile', (local.outputRes || '') === r.id && 'is-active']"
          @click="local.outputRes = r.id"
        >
          <span class="apl-fm-tile-label">{{ r.label }}</span>
        </button>
      </div>
      <p class="apl-fm-hint">Gốc = nhanh nhất (~1–2MP). FullHD/2K/4K = phóng to nét (lanczos), file lớn + lâu hơn chút.</p>
    </div>
    <!-- #endregion -->
  </div>
  <!-- #endregion -->
</template>

<script setup>
const props = defineProps({
  config: { type: Object, required: true },
  nodeType: { type: String, default: 'tryon' }
})
const emit = defineEmits(['update:config'])

// ALD 21/06/2026 - ĐÃ BỎ 'Auto ✨' (vision Ollama tự nhận): tốn VRAM (nạp model) + chậm. User TỰ CHỌN loại đồ.
// Trùng GARMENT_TYPES_VALID của worker.py.
const GARMENT_TYPES = [
  { id: 'auto',      label: 'Tự động ✨', icon: 'bi-magic' },
  { id: 'upper',     label: 'Áo',        icon: 'bi-person-fill' },
  { id: 'lower',     label: 'Quần',      icon: 'bi-person-walking' },
  { id: 'skirt',     label: 'Chân váy',  icon: 'bi-triangle' },
  { id: 'dress',     label: 'Váy/Đầm',   icon: 'bi-person-arms-up' },
  { id: 'set',       label: 'Set/Bộ',    icon: 'bi-bag-check' },
  { id: 'bikini',    label: 'Bikini',    icon: 'bi-water' },
  { id: 'bra',       label: 'Bra',       icon: 'bi-suit-heart' },
  { id: 'lingerie',  label: 'Đồ lót bộ', icon: 'bi-suit-heart-fill' },
  { id: 'shoes',     label: 'Giày/Dép',  icon: 'bi-disc' },
  { id: 'accessory', label: 'Phụ kiện',  icon: 'bi-bag-heart-fill' }
]
// ALD 15/06/2026 - độ phân giải output (upscale lanczos ở worker). '' = gốc (nhanh).
const RES_OPTS = [
  { id: '', label: 'Gốc' },
  { id: 'fullhd', label: 'FullHD' },
  { id: '2k', label: '2K' },
  { id: '4k', label: '4K' }
]

// ALD 28/05/2026 - Bỏ Leffa khỏi options. Coerce legacy provider:
//   'fashn' → 'gemini' (đã rename trước đây)
//   'local' → 'qwen' (Leffa removed, Qwen là default local GPU model)
// Allowed: 'qwen' | 'gemini'. Mọi giá trị khác → 'qwen' (safe default; 'huggingface' đã gỡ 11/06 → tự lành).
const incomingProvider = String(props.config?.provider || '').toLowerCase()
const normalizedProvider =
    incomingProvider === 'gemini' ? 'gemini'
  : incomingProvider === 'qwen'   ? 'qwen'
  : /* 'fashn' | 'local' | 'leffa' | 'huggingface' | '' | bất kỳ */ 'qwen'

// ALD 05/06/2026 - Gemini chỉ dành cho admin (BE chặn run; FE ẩn tile + key field cho non-admin).
// Role nằm trong JWT claim (khớp settings.vue/layouts/default.vue) — KHÔNG phải auth.user.role.
const auth = useAuth()
const isAdmin = computed(() => {
  try { return JSON.parse(atob((auth.token.value || '').split('.')[1] ?? ''))?.role === 'admin' }
  catch { return false }
})

const clearedApiKey = ref(false)
const apiKeyAlreadySaved = computed(() => !clearedApiKey.value && Boolean(
  props.config?.__apiKey_isSet || props.config?.__geminiApiKey_isSet,
))

const editingApiKey = ref(false)
function cancelChangeApiKey() {
  editingApiKey.value = false
  local.value.apiKey = ''
}
// ALD 05/06/2026 - Xoá hẳn key đã lưu → emit cờ __apiKey_clear (BE để rỗng, không khôi phục key cũ).
function clearApiKey() {
  clearedApiKey.value = true
  editingApiKey.value = false
  local.value = { ...local.value, apiKey: '' }
}

const local = ref({
  garmentType: 'auto',    // ALD 28/06/2026 - mặc định TỰ ĐỘNG (generic): thay nguyên bộ từ ảnh sản phẩm, không cần chọn loại. Chọn loại cụ thể nếu muốn ép 1 món.
  extraPrompt: '',    // ALD 01/07/2026 - ghi chú thêm: giữ/đổi ngoài quần áo (nón, trang sức…). Worker dịch VN→EN.
  cleanOnly: false,   // ALD 21/06/2026 - true = CHỈ làm sạch ảnh (Qwen tái sinh sạch/nét/full-body), KHÔNG thay đồ; không cần ảnh sản phẩm.
  productCount: 1,   // ALD 10/06/2026 - 2 = thêm cổng Ảnh SP 2 (góc mặt sau/bên hông)
  useBackground: false,   // ALD 21/06/2026 - true = hiện cổng "Nền" → ghép người vào ảnh phòng/địa điểm (Qwen pass 2)
  brightness: 0,     // ALD 15/06/2026 - chỉnh sáng output (đồ trắng cháy → ÂM)
  outputRes: '',     // '' gốc | fullhd | 2k | 4k
  apiKey: '',
  ...props.config,
  provider: normalizedProvider,  // 'qwen' | 'gemini'
  // Apply apiKey từ props sau spread để strip flag không leak vào local value
  apiKey: String(props.config?.apiKey || '').trim() || '',
})

// ALD 02/06/2026 - Cảnh báo key Gemini sai định dạng (chặn dán nhầm text lỗi → Google 400 khó hiểu).
const apiKeyLooksInvalid = computed(() => {
  const k = String(local.value.apiKey || '').trim()
  return k.length > 0 && !(k.startsWith('AIza') && k.length >= 30 && k.length <= 60 && !/\s/.test(k))
})

watch(local, (v) => {
  const out = { ...v }
  if (clearedApiKey.value && !String(v.apiKey || '').trim()) out.__apiKey_clear = true  // đã bấm Xoá & chưa nhập key mới
  emit('update:config', out)
}, { deep: true })
watch(() => props.config, (v) => {
  if (v && JSON.stringify(v) !== JSON.stringify(local.value)) {
    const incoming = String(v.provider || '').toLowerCase()
    const norm = incoming === 'gemini' ? 'gemini' : incoming === 'qwen' ? 'qwen' : 'qwen'
    local.value = { ...local.value, ...v, provider: norm }
  }
})
</script>
