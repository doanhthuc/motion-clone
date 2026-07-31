<template>
  <!-- ALD 03/06/2026 - Node "Nói (lip-sync)": ảnh nhân vật (cổng trái) + câu thoại + giọng → MultiTalk
       sinh video nhân vật NÓI, miệng khớp khẩu hình, giọng mux sẵn. Mỗi nhân vật 1 giọng riêng. -->
  <div class="space-y-4">
    <div class="apl-info-card">
      <p class="font-semibold flex items-center gap-1.5"><i class="bi bi-mic-fill" /> Nói (lip-sync · MultiTalk)</p>
      <p class="text-[11px] opacity-70 mt-1">
        Cổng trái nhận <b>ẢNH nhân vật</b> (từ create-image/compose/tryon). Nhập <b>câu thoại</b> + chọn <b>giọng</b> →
        video nhân vật nói, <b>nhép miệng đúng khẩu hình</b> + giọng đó. Thời lượng tự theo độ dài thoại.
      </p>
    </div>

    <!-- Giọng -->
    <div class="apl-fm-group">
      <p class="apl-fm-heading">Giọng nhân vật</p>
      <div class="grid grid-cols-3 gap-1.5">
        <button type="button" :class="['apl-fm-tile', isVoice('gemini:Aoede') && 'is-active']" @click="local.voice = 'gemini:Aoede'">
          <i class="bi bi-gender-female text-base" /><span class="apl-fm-tile-label">Nữ</span>
        </button>
        <button type="button" :class="['apl-fm-tile', isVoice('gemini:Puck') && 'is-active']" @click="local.voice = 'gemini:Puck'">
          <i class="bi bi-gender-male text-base" /><span class="apl-fm-tile-label">Nam</span>
        </button>
        <button type="button" :class="['apl-fm-tile', String(local.voice||'').startsWith('vixtts') && 'is-active']" @click="local.voice = 'vixtts'">
          <i class="bi bi-soundwave text-base" /><span class="apl-fm-tile-label">Clone</span>
        </button>
      </div>
      <!-- ALD 13/06/2026 - Thư viện giọng (viXTTS clone từ file admin upload). Chọn → set 'voicelib:<id>'. -->
      <div v-if="voiceLibOptions.length" class="mt-2">
        <span class="apl-fm-sublabel">Thư viện giọng (đã clone)</span>
        <UiDropdown
          :model-value="libValue"
          :options="voiceLibOptions"
          icon="bi-soundwave"
          full-width
          placeholder="Chọn giọng từ thư viện…"
          class="mt-1"
          @update:model-value="(v) => v && (local.voice = v)"
        />
      </div>
      <input v-model="local.voice" type="text" class="apl-fm-input mt-2" placeholder="gemini:Aoede | gemini:Puck | vixtts:/đường-dẫn/ref.wav | voicelib:<id>" />
      <p class="apl-fm-hint">
        Nữ <code>gemini:Aoede</code>/<code>gemini:Kore</code> · Nam <code>gemini:Puck</code>/<code>gemini:Charon</code> ·
        Clone giọng riêng: <code>vixtts:&lt;file.wav&gt;</code> hoặc chọn từ <b>Thư viện giọng</b> ở trên.
      </p>
    </div>

    <!-- Câu thoại -->
    <div class="apl-fm-group">
      <p class="apl-fm-heading">Câu thoại (lời nhân vật nói)</p>
      <textarea v-model="local.line" rows="4" class="apl-fm-input" style="height:auto;padding:8px 10px;font-family:inherit;line-height:1.5;resize:vertical"
        placeholder="VD: Chào mừng bạn đến với cửa hàng của chúng tôi…" />
      <p class="apl-fm-hint">Đây là lời sẽ được đọc + nhép miệng. Độ dài video ≈ độ dài câu thoại.</p>
    </div>

    <!-- Cử động/biểu cảm phụ (tuỳ chọn) -->
    <div class="apl-fm-group">
      <p class="apl-fm-heading">Cử động / biểu cảm <span class="opacity-50 normal-case font-medium">(tuỳ chọn)</span></p>
      <input v-model="local.prompt" type="text" class="apl-fm-input" placeholder="VD: mỉm cười thân thiện, gật đầu nhẹ, nhìn thẳng ống kính…" />
      <p class="apl-fm-hint">Gợi ý cử động/biểu cảm khi nói (tiếng Anh/Việt). Để trống → mặc định nói tự nhiên.</p>
    </div>
  </div>
</template>

<script setup>
const props = defineProps({
  config: { type: Object, required: true },
  nodeType: { type: String, default: 'talk' }
})
const emit = defineEmits(['update:config'])

const local = ref({ line: '', voice: 'gemini:Aoede', prompt: '', fps: 25, ...props.config })
const isVoice = (v) => String(local.value.voice || '') === v

// ALD 13/06/2026 - Thư viện giọng: nạp list giọng đã clone → thêm vào picker (value 'voicelib:<id>').
const voicesLib = useVoices()
onMounted(() => { voicesLib.load() })
const voiceLibOptions = computed(() => voicesLib.options.value)
// Giữ dropdown hiển thị đúng khi voice hiện tại là 1 mục trong thư viện.
const libValue = computed(() => (String(local.value.voice || '').startsWith('voicelib:') ? local.value.voice : ''))

watch(local, (v) => emit('update:config', { ...v }), { deep: true })
watch(() => props.config, (v) => {
  if (v && JSON.stringify(v) !== JSON.stringify(local.value)) local.value = { ...local.value, ...v }
})
</script>

<style scoped>
.apl-info-card { background: rgba(52,199,89,0.07); border: 0.5px solid rgba(52,199,89,0.25); border-radius: 12px; padding: 11px 12px; }
.apl-fm-group { background: var(--apl-fill); border: 0.5px solid rgba(235,236,240,0.12); border-radius: 14px; padding: 12px; }
.apl-fm-heading { font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; color: var(--apl-label); margin-bottom: 8px; }
.apl-fm-hint { margin-top: 6px; font-size: 10.5px; color: var(--apl-label); line-height: 1.4; }
.apl-fm-input { width: 100%; height: 32px; padding: 0 10px; background: var(--apl-bg-secondary); border: 0.5px solid rgba(235,236,240,0.18); border-radius: 9px; font-size: 12px; transition: border-color 0.18s; }
.apl-fm-input:focus { outline: none; border-color: var(--color-primary, #9AA2F2); }
.apl-fm-tile { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 3px; height: 52px; border-radius: 12px; border: 0.5px solid rgba(235,236,240,0.18); background: var(--apl-bg-secondary); color: var(--apl-label); transition: all 0.15s; }
.apl-fm-tile:hover { border-color: rgba(52,199,89,0.4); }
.apl-fm-tile.is-active { border-color: #34C759; background: rgba(52,199,89,0.08); color: #1B7A38; box-shadow: 0 0 0 1px #34C759 inset; }
.apl-fm-tile-label { font-size: 11px; font-weight: 700; }
.apl-fm-sublabel { display: block; font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.03em; color: var(--apl-label); }
</style>
