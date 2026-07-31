<template>
  <!-- ALD 17/06/2026 - Node "Ảnh → Video (Wan)": cổng trái nhận 1 ẢNH (từ Create Image / Input) + prompt TIẾNG ANH →
       video chuyển động bằng Wan 2.1/2.2 I2V (engine đã cài trên box; KHÔNG cần LTX). Dùng cho time-lapse BĐS. -->
  <div class="space-y-4">
    <div class="apl-info-card">
      <p class="font-semibold flex items-center gap-1.5"><i class="bi bi-camera-reels" /> Ảnh → Video</p>
      <p class="text-[11px] opacity-70 mt-1">
        Cổng trái nhận <b>1 ẢNH</b>. Mô tả chuyển động bằng <b>tiếng Anh</b> (text-encoder của Wan hiểu tiếng Anh tốt nhất) → ra clip ngắn.
      </p>
    </div>

    <!-- ALD 10/07/2026 - Provider: Self-host (Wan trên box, miễn phí) vs DashScope (Alibaba cloud happyhorse i2v,
         cần API key — nối node API Key Type "dashscope" hoặc đặt rời trong workflow, engine tự phân bổ). -->
    <div class="apl-fm-group">
      <p class="apl-fm-heading">Provider</p>
      <div class="grid grid-cols-2 gap-1.5">
        <button type="button" :class="['apl-fm-tile', local.provider !== 'dashscope' && 'is-active']" @click="local.provider = 'self-host'">
          <span class="apl-fm-tile-label">Self-host</span><span class="apl-fm-tile-sub">Wan trên box · miễn phí</span>
        </button>
        <button type="button" :class="['apl-fm-tile', local.provider === 'dashscope' && 'is-active']" @click="local.provider = 'dashscope'">
          <span class="apl-fm-tile-label">DashScope</span><span class="apl-fm-tile-sub">Alibaba cloud · cần key</span>
        </button>
      </div>
      <p v-if="local.provider === 'dashscope'" class="apl-fm-hint">
        <i class="bi bi-key me-1" />Cần node <b>API Key</b> (Type: <code>dashscope</code>) trong workflow — nối vào node này hoặc đặt rời (tự phân bổ). Video trả về ~1-5 phút/task.
      </p>
    </div>

    <!-- Options DashScope (cloud) -->
    <!-- ALD 10/07/2026 - + wan2.7-i2v: hỗ trợ AUDIO (driving_audio — video diễn/nhép theo audio, cổng Audio
         tự hiện trên node) + ảnh cuối (last_frame) + prompt_extend. happyhorse chỉ nhận ảnh đầu. -->
    <div v-if="local.provider === 'dashscope'" class="grid grid-cols-2 gap-2">
      <div class="apl-fm-group">
        <p class="apl-fm-heading">Model</p>
        <select v-model="local.dashscopeModel" class="apl-fm-input">
          <option value="happyhorse-1.0-i2v">happyhorse-1.0-i2v</option>
          <option value="happyhorse-1.1-i2v">happyhorse-1.1-i2v (mới)</option>
          <option value="wan2.7-i2v">wan2.7-i2v (audio + ảnh cuối)</option>
        </select>
      </div>
      <div class="apl-fm-group">
        <p class="apl-fm-heading">Độ phân giải</p>
        <select v-model="local.dashscopeResolution" class="apl-fm-input">
          <option value="720P">720P</option>
          <option value="1080P">1080P (đắt hơn)</option>
        </select>
      </div>
    </div>
    <div v-if="local.provider === 'dashscope' && isDsWan2x" class="apl-fm-group">
      <label class="flex items-center justify-between gap-2 cursor-pointer">
        <span>
          <p class="apl-fm-heading" style="margin-bottom:2px">Prompt extend</p>
          <p class="apl-fm-hint" style="margin-top:0">DashScope tự làm giàu prompt (khuyên bật). Cổng <b>Audio</b> trên node: nối wav/mp3 2-30s → video diễn/nhép theo audio.</p>
        </span>
        <input v-model="local.dashscopePromptExtend" type="checkbox" class="apl-fm-check" />
      </label>
    </div>

    <!-- Engine Wan (chỉ self-host) -->
    <!-- ALD 03/07/2026 - Wan 2.2 = MẶC ĐỊNH MỚI: dual-model + LoRA distill 4 bước (lightx2v) — nhanh + nét,
         không còn "lâu hơn" như trước. Cần tải model/LoRA trong Settings → Models AI (nhóm Wan 2.2). -->
    <div v-if="local.provider !== 'dashscope'" class="apl-fm-group">
      <p class="apl-fm-heading">Engine</p>
      <div class="grid grid-cols-2 gap-1.5">
        <button type="button" :class="['apl-fm-tile', local.wanModel === 'wan2.2' && 'is-active']" @click="local.wanModel = 'wan2.2'">
          <span class="apl-fm-tile-label">Wan 2.2</span><span class="apl-fm-tile-sub">distill 4 bước · khuyên dùng</span>
        </button>
        <button type="button" :class="['apl-fm-tile', local.wanModel === 'wan2.1' && 'is-active']" @click="local.wanModel = 'wan2.1'">
          <span class="apl-fm-tile-label">Wan 2.1</span><span class="apl-fm-tile-sub">cũ · 480p</span>
        </button>
      </div>
    </div>

    <!-- Prompt (English) -->
    <div class="apl-fm-group">
      <p class="apl-fm-heading">Prompt chuyển động (English)</p>
      <textarea v-model="local.prompt" rows="3" class="apl-fm-input" style="height:auto;padding:8px 10px;font-family:inherit;line-height:1.5;resize:vertical"
        placeholder="e.g. Aerial drone slowly descends over an empty construction plot, workers placing survey stakes, morning light, gentle camera motion" />
      <p class="apl-fm-hint">PHẢI viết <b>tiếng Anh</b> (Wan không hiểu tiếng Việt → ra sai/đứng hình).</p>
    </div>

    <!-- Tỉ lệ + thời lượng -->
    <div class="grid grid-cols-2 gap-2">
      <div v-if="local.provider !== 'dashscope'" class="apl-fm-group">
        <p class="apl-fm-heading">Tỉ lệ</p>
        <select v-model="local.aspectRatio" class="apl-fm-input">
          <option value="9:16">Dọc 9:16</option>
          <option value="16:9">Ngang 16:9</option>
          <option value="1:1">Vuông 1:1</option>
          <option value="auto">Auto (theo ảnh)</option>
        </select>
      </div>
      <div class="apl-fm-group">
        <p class="apl-fm-heading">Thời lượng (giây)</p>
        <!-- ALD 10/07/2026 - DashScope: happyhorse 3-15s, wan2.x 2-15s; self-host Wan: 2-10s. -->
        <input v-model.number="local.duration" type="number" :min="local.provider === 'dashscope' ? (isDsWan2x ? 2 : 3) : 2" :max="local.provider === 'dashscope' ? 15 : 10" step="1" class="apl-fm-input" />
        <p v-if="local.provider === 'dashscope'" class="apl-fm-hint">{{ isDsWan2x ? '2' : '3' }}–15s (cloud tự chọn tỉ lệ theo ảnh đầu).</p>
        <p v-else class="apl-fm-hint">2–10s (Wan 16fps). Trên 5s tự bật RIFLEx chống slow-motion.</p>
      </div>
    </div>

    <!-- ALD 03/07/2026 - Toggle "Ảnh cuối (FLF)": bật → hiện cổng 'Ảnh cuối' trên canvas (morph ảnh đầu → ảnh
         cuối); tắt → ẨN cổng + tự gỡ dây đã nối + worker bỏ qua input end. Mặc định TẮT cho node mới.
         ALD 10/07/2026 - DashScope: ẨN với happyhorse (chỉ first_frame), HIỆN với wan2.x (last_frame OK). -->
    <div v-if="local.provider !== 'dashscope' || isDsWan2x" class="apl-fm-group">
      <label class="flex items-center justify-between gap-2 cursor-pointer">
        <span>
          <p class="apl-fm-heading" style="margin-bottom:2px">Ảnh cuối (tuỳ chọn)</p>
          <p class="apl-fm-hint" style="margin-top:0">Bật để hiện cổng <b>Ảnh cuối</b> trên node: video morph từ ảnh đầu → ảnh cuối (FLF). Lưu ý: bật ảnh cuối sẽ tắt "giữ màu ảnh gốc".</p>
        </span>
        <input v-model="local.endEnabled" type="checkbox" class="apl-fm-check" />
      </label>
    </div>

    <!-- ALD 03/07/2026 - Giữ màu ảnh gốc: ColorMatch (mkl) kéo màu + độ sáng video về đúng ảnh đầu vào —
         trị CHÁY SÁNG của Wan 2.2 distill (học từ Motion node). Mặc định BẬT.
         ALD 10/07/2026 - ẨN khi DashScope (ColorMatch chạy trong graph ComfyUI, không áp cho video cloud). -->
    <div v-if="local.provider !== 'dashscope'" class="apl-fm-group">
      <label class="flex items-center justify-between gap-2 cursor-pointer">
        <span>
          <p class="apl-fm-heading" style="margin-bottom:2px">Giữ màu ảnh gốc</p>
          <p class="apl-fm-hint" style="margin-top:0">Chống cháy sáng: khớp màu/độ sáng video theo chính ảnh đầu vào.</p>
        </span>
        <input v-model="local.matchRef" type="checkbox" class="apl-fm-check" />
      </label>
    </div>

    <!-- Negative (optional, English) -->
    <div class="apl-fm-group">
      <p class="apl-fm-heading">Tránh (negative, English — tuỳ chọn)</p>
      <input v-model="local.negativePrompt" type="text" class="apl-fm-input"
        placeholder="e.g. blurry, distorted, melting, jump cut" />
    </div>
  </div>
</template>

<script setup>
const props = defineProps({
  config: { type: Object, required: true },
  nodeType: { type: String, default: 'wan-i2v' }
})
const emit = defineEmits(['update:config'])

// ALD 03/07/2026 - default wan2.2 (distill 4 bước) + matchRef bật (chống cháy sáng). Node cũ đã lưu config giữ nguyên.
// endEnabled default true ở local (node CŨ thiếu key → cổng end đang hiện, giữ nguyên); node MỚI defaultConfig cho false.
// ALD 10/07/2026 - provider: 'self-host' (Wan box) | 'dashscope' (Alibaba cloud happyhorse/wan2.x i2v, key qua node API Key).
const local = ref({ prompt: '', negativePrompt: '', duration: 5, aspectRatio: '9:16', wanModel: 'wan2.2', matchRef: true, endEnabled: true, provider: 'self-host', dashscopeModel: 'happyhorse-1.0-i2v', dashscopeResolution: '720P', dashscopePromptExtend: true, ...props.config })
// wan2.x (wan2.7-i2v...) = họ model DashScope hỗ trợ audio + last_frame + prompt_extend.
const isDsWan2x = computed(() => String(local.value.dashscopeModel || '').startsWith('wan2.'))

watch(local, (v) => emit('update:config', { ...v }), { deep: true })
watch(() => props.config, (v) => {
  if (v && JSON.stringify(v) !== JSON.stringify(local.value)) local.value = { ...local.value, ...v }
})
</script>

<style scoped>
.apl-info-card { background: rgba(255,45,85,0.06); border: 0.5px solid rgba(255,45,85,0.22); border-radius: 12px; padding: 11px 12px; }
.apl-fm-group { background: var(--apl-fill); border: 0.5px solid rgba(235,236,240,0.12); border-radius: 14px; padding: 12px; }
.apl-fm-heading { font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; color: var(--apl-label); margin-bottom: 8px; }
.apl-fm-hint { margin-top: 6px; font-size: 10.5px; color: var(--apl-label); line-height: 1.4; }
.apl-fm-input { width: 100%; min-height: 32px; padding: 0 10px; background: var(--apl-bg-secondary); border: 0.5px solid rgba(235,236,240,0.18); border-radius: 9px; font-size: 12px; transition: border-color 0.18s; }
.apl-fm-input:focus { outline: none; border-color: #FF2D55; }
.apl-fm-check { width: 18px; height: 18px; accent-color: #FF2D55; flex-shrink: 0; }
.apl-fm-tile { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 2px; height: 50px; border-radius: 12px; border: 0.5px solid rgba(235,236,240,0.18); background: var(--apl-bg-secondary); color: var(--apl-label); transition: all 0.15s; }
.apl-fm-tile:hover { border-color: rgba(255,45,85,0.4); }
.apl-fm-tile.is-active { border-color: #FF2D55; background: rgba(255,45,85,0.07); color: #A11D38; box-shadow: 0 0 0 1px #FF2D55 inset; }
.apl-fm-tile-label { font-size: 12px; font-weight: 700; }
.apl-fm-tile-sub { font-size: 9.5px; opacity: 0.6; }
</style>
