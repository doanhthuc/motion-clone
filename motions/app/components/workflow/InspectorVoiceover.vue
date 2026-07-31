<template>
  <!-- ALD 17/06/2026 - Node "Lồng tiếng (đọc mô tả)": cổng trái nhận 1 CLIP video (từ SS/Ảnh→Video) + lời thuyết minh
       + giọng → TTS tiếng Việt (viXTTS/OmniVoice/Gemini) rồi mux/duck audio lên clip, GIỮ nguyên hình + độ dài clip.
       Dùng cho từng công đoạn pipeline BĐS. KHÔNG cần khuôn mặt (khác node Nói/Người mẫu đọc kịch bản). -->
  <div class="space-y-4">
    <div class="apl-info-card">
      <p class="font-semibold flex items-center gap-1.5"><i class="bi bi-soundwave" /> Lồng tiếng (đọc mô tả)</p>
      <p class="text-[11px] opacity-70 mt-1">
        Cổng trái nhận <b>1 CLIP video</b>. Nhập <b>lời thuyết minh</b> + chọn <b>giọng</b> → giọng đọc tiếng Việt được
        ghép lên clip, <b>giữ nguyên hình và độ dài</b>. Lời ngắn hơn clip → đệm im; dài hơn → cắt theo clip.
      </p>
    </div>

    <!-- Giọng -->
    <div class="apl-fm-group">
      <p class="apl-fm-heading">Giọng đọc</p>
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
      <!-- ALD 17/06/2026 - Thư viện giọng (viXTTS clone từ file admin upload). Chọn → set 'voicelib:<id>'. -->
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

    <!-- Lời thuyết minh -->
    <div class="apl-fm-group">
      <p class="apl-fm-heading">Lời thuyết minh (đọc lên clip)</p>
      <textarea v-model="local.script" rows="4" class="apl-fm-input" style="height:auto;padding:8px 10px;font-family:inherit;line-height:1.5;resize:vertical"
        placeholder="VD: Khởi đầu từ mặt bằng đất trống, đội ngũ tiến hành định vị và đo đạc…" />
      <p class="apl-fm-hint">PHẢI gõ tiếng Việt <b>có dấu</b> (không dấu sẽ đọc như người nước ngoài).</p>
    </div>

    <!-- Cách ghép tiếng -->
    <div class="apl-fm-group">
      <p class="apl-fm-heading">Cách ghép tiếng</p>
      <div class="grid grid-cols-2 gap-1.5">
        <button type="button" :class="['apl-fm-tile', local.mix === 'replace' && 'is-active']" @click="local.mix = 'replace'">
          <i class="bi bi-volume-mute text-base" /><span class="apl-fm-tile-label">Thay tiếng nền</span>
        </button>
        <button type="button" :class="['apl-fm-tile', local.mix === 'under' && 'is-active']" @click="local.mix = 'under'">
          <i class="bi bi-volume-down text-base" /><span class="apl-fm-tile-label">Trộn dưới nền</span>
        </button>
      </div>
      <p class="apl-fm-hint">
        <b>Thay tiếng nền</b>: bỏ âm gốc của clip, chỉ còn giọng đọc. <b>Trộn dưới nền</b>: giữ âm gốc nhỏ lại, giọng đọc nổi lên trên.
      </p>
    </div>
  </div>
</template>

<script setup>
const props = defineProps({
  config: { type: Object, required: true },
  nodeType: { type: String, default: 'voiceover' }
})
const emit = defineEmits(['update:config'])

const local = ref({ script: '', voice: 'vixtts', mix: 'replace', ...props.config })
const isVoice = (v) => String(local.value.voice || '') === v

// ALD 17/06/2026 - Thư viện giọng: nạp list giọng đã clone → thêm vào picker (value 'voicelib:<id>').
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
