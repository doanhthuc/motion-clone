<template>
  <div class="space-y-3">
    <!-- #region ALD 11/06/2026 - Node API Key: khai báo key theo provider. Cách dùng:
         1) NỐI cổng ra → cổng "API Key" của node đích (ưu tiên cao nhất, chỉ định rõ node nào dùng key nào).
         2) Đặt RỜI trên canvas = tự phân bổ cho MỌI node cùng provider khi chạy.
         Key lưu server-side (mask khi load lại — pattern __apiKey_isSet/__apiKey_clear như create-image). -->
    <div class="apl-info-card">
      <p class="flex items-start gap-1.5">
        <i class="bi bi-key-fill mt-0.5" />
        <span>
          Nối cổng ra của node này vào cổng <b>API Key</b> của node cần key (ưu tiên nhất), hoặc đặt rời trên canvas —
          key sẽ <b>tự phân bổ</b> cho mọi node dùng provider cùng Type. Chỉ <b>Self-host</b> không cần key.
        </span>
      </p>
    </div>

    <!-- Type provider -->
    <div>
      <label class="apl-fm-label">Type</label>
      <!-- ALD 11/06/2026 - tile HuggingFace ĐÃ GỠ theo yêu cầu user (node cũ Type huggingface hiện thành Custom). -->
      <!-- ALD 10/07/2026 - 4 tile (thêm DashScope) → 2x2 -->
      <div class="grid grid-cols-2 gap-1.5 mt-1.5">
        <button
          type="button"
          :class="['apl-fm-tile', selectedKind === 'gemini' && 'is-active']"
          @click="pickKind('gemini')"
        >
          <i class="bi bi-google text-base" />
          <span class="apl-fm-tile-label">Gemini</span>
        </button>
        <!-- ALD 10/07/2026 - DashScope (Alibaba Model Studio): node wan-i2v provider dashscope (happyhorse i2v). -->
        <button
          type="button"
          :class="['apl-fm-tile', selectedKind === 'dashscope' && 'is-active']"
          @click="pickKind('dashscope')"
        >
          <i class="bi bi-cloud text-base" />
          <span class="apl-fm-tile-label">DashScope</span>
        </button>
        <button
          type="button"
          :class="['apl-fm-tile', selectedKind === 'veo' && 'is-active']"
          @click="pickKind('veo')"
        >
          <i class="bi bi-camera-video text-base" />
          <span class="apl-fm-tile-label">Veo 3 <span class="text-[9px] font-normal text-amber-600">(sắp có)</span></span>
        </button>
        <button
          type="button"
          :class="['apl-fm-tile', selectedKind === 'custom' && 'is-active']"
          @click="pickKind('custom')"
        >
          <i class="bi bi-sliders text-base" />
          <span class="apl-fm-tile-label">Custom</span>
        </button>
      </div>
      <input
        v-if="selectedKind === 'custom'"
        v-model="local.providerType"
        type="text"
        spellcheck="false"
        placeholder="tên provider (vd: openai, fal, runway…)"
        class="apl-fm-input mt-1.5 text-xs"
      />
      <p class="apl-fm-hint">
        <span v-if="selectedKind === 'gemini'"><i class="bi bi-info-circle me-1" />Key <b>AIza…</b> — lấy tại aistudio.google.com/apikey. Node provider Gemini vẫn chỉ admin chạy được.</span>
        <span v-else-if="selectedKind === 'veo'"><i class="bi bi-info-circle me-1" />Khung chuẩn bị sẵn cho Veo 3 (video) — chưa có node nào dùng, key được lưu chờ.</span>
        <span v-else><i class="bi bi-info-circle me-1" />Type tự đặt — node nào có <code>provider</code> trùng tên này sẽ nhận key.</span>
      </p>
    </div>

    <!-- API key (secret pattern: đã lưu / đổi / xoá) -->
    <div v-if="apiKeyAlreadySaved && !editingApiKey" class="flex items-center justify-between gap-2 p-2.5 rounded-lg border border-emerald-200 bg-emerald-50">
      <div class="flex items-center gap-2 min-w-0">
        <i class="bi bi-shield-check-fill text-emerald-600 text-base" />
        <div class="min-w-0">
          <div class="text-xs font-semibold text-emerald-900">API Key đã lưu</div>
          <div class="text-[10px] text-emerald-700 truncate">Key bảo mật ở server, sẵn sàng phân phối khi chạy</div>
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

    <div v-else>
      <label class="apl-fm-label flex items-center gap-1.5">
        API Key
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
        :placeholder="keyPlaceholder"
        class="apl-fm-input mt-1 font-mono text-xs"
      />
      <p class="apl-fm-hint">
        <i class="bi bi-shield-lock me-1" />Key lưu <b>server-side</b>, không hiển thị lại khi mở workflow. Nhớ bấm <b>Lưu</b> workflow sau khi nhập.
      </p>
    </div>
    <!-- #endregion -->
  </div>
</template>

<script setup>
const props = defineProps({
  config: { type: Object, required: true },
  nodeType: { type: String, default: 'api-key' }
})
const emit = defineEmits(['update:config'])

const KNOWN_KINDS = ['gemini', 'dashscope', 'veo']  // ALD 11/06/2026 - huggingface đã gỡ → node cũ rơi về Custom (vẫn thấy tên). ALD 10/07/2026 + dashscope (wan-i2v cloud)

const local = ref({
  providerType: 'gemini',
  apiKey: '',
  ...props.config
})

// 'custom' = providerType ngoài 3 loại chuẩn → hiện ô text tự đặt tên
const customPicked = ref(false)
const selectedKind = computed(() => {
  const t = String(local.value.providerType || '').toLowerCase().trim()
  if (customPicked.value) return 'custom'
  return KNOWN_KINDS.includes(t) ? t : (t ? 'custom' : 'gemini')
})
function pickKind(kind) {
  if (kind === 'custom') {
    customPicked.value = true
    if (KNOWN_KINDS.includes(String(local.value.providerType || '').toLowerCase())) local.value.providerType = ''
  } else {
    customPicked.value = false
    local.value.providerType = kind
  }
}

const keyPlaceholder = computed(() => ({
  gemini: 'AIzaSy…',
  veo: 'key Google Cloud / AI Studio…'
}[selectedKind.value] || 'api key…'))

// Secret pattern (đồng bộ create-image): server mask key → __apiKey_isSet; xoá hẳn → __apiKey_clear.
const clearedApiKey = ref(false)
const apiKeyAlreadySaved = computed(() => !clearedApiKey.value && Boolean(props.config?.__apiKey_isSet))

const editingApiKey = ref(false)
function cancelChangeApiKey() {
  editingApiKey.value = false
  local.value.apiKey = ''
}
function clearApiKey() {
  clearedApiKey.value = true
  editingApiKey.value = false
  local.value = { ...local.value, apiKey: '' }
}

watch(local, (v) => {
  const out = { ...v, providerType: String(v.providerType || '').toLowerCase().trim() }
  if (clearedApiKey.value && !String(v.apiKey || '').trim()) out.__apiKey_clear = true
  emit('update:config', out)
}, { deep: true })
</script>
