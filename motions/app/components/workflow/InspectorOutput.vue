<template>
  <div class="space-y-3">
    <div>
      <label class="apl-label">Format trả về end user</label>
      <div class="apl-segmented mt-1.5">
        <button
          v-for="f in formats"
          :key="f.id"
          type="button"
          :class="['apl-seg-btn', local.format === f.id ? 'is-active' : '']"
          @click="local.format = f.id"
        >
          <i :class="['bi mr-1', f.icon]" />
          {{ f.label }}
        </button>
      </div>
    </div>

    <div class="apl-card">
      <div class="apl-card-header">
        <i :class="['bi mr-1', activeFormat.icon]" />
        {{ activeFormat.label }}
      </div>
      <p class="apl-card-hint">{{ activeFormat.desc }}</p>
    </div>

    <!-- ALD 15/06/2026 - Xả RAM/VRAM khi workflow ra kết quả (Chandra stop + Ollama unload + ComfyUI free). -->
    <label class="apl-toggle">
      <span class="min-w-0">
        <span class="apl-toggle-title">Xả RAM/VRAM khi xong</span>
        <span class="apl-card-hint">Ra kết quả → tự dọn VRAM/RAM (Chandra + Ollama + ComfyUI). Máy nhẹ sau mỗi lần chạy; lần sau ComfyUI nạp lại model (cold-start chậm hơn chút).</span>
      </span>
      <input v-model="local.cleanup" type="checkbox" class="apl-toggle-input" >
    </label>

    <!-- #region ALD 05/07/2026 - Đăng tự động lên Facebook Page / TikTok khi node này có kết quả. -->
    <label class="apl-toggle">
      <span class="min-w-0">
        <span class="apl-toggle-title">Tự động đăng lên MXH</span>
        <span class="apl-card-hint">Có kết quả (ảnh/video) → tự đăng lên Page Facebook / TikTok đã kết nối bên dưới.</span>
      </span>
      <input v-model="local.social.enabled" type="checkbox" class="apl-toggle-input" >
    </label>

    <div v-if="local.social.enabled" class="social-panel">
      <div>
        <label class="apl-fm-label">Caption</label>
        <textarea
          v-model="local.social.caption"
          rows="2"
          placeholder="Để trống → dùng text output của workflow làm caption"
          class="apl-fm-input social-textarea"
        />
        <p class="apl-fm-hint">Áp dụng cho cả Facebook và TikTok. Để trống thì dùng text workflow trả về.</p>
      </div>

      <!-- Facebook -->
      <div class="social-platform">
        <label class="social-platform-head">
          <span class="social-platform-name"><i class="bi bi-facebook" /> Facebook Page</span>
          <input v-model="local.social.facebook.enabled" type="checkbox" class="apl-toggle-input" >
        </label>
        <div v-if="local.social.facebook.enabled" class="social-accounts">
          <p v-if="social.loading.value" class="apl-fm-hint">Đang tải danh sách Page…</p>
          <p v-else-if="!fbAccounts.length" class="apl-fm-hint">Chưa kết nối Page nào.</p>
          <label v-for="a in fbAccounts" :key="a.id" class="social-account-row">
            <input type="checkbox" :checked="isChecked('facebook', a.id)" @change="toggleAccount('facebook', a.id)" >
            <img v-if="a.avatar_url" :src="a.avatar_url" class="social-avatar" >
            <span class="truncate">{{ a.name || a.external_id }}</span>
          </label>
          <button type="button" class="social-connect-btn" :disabled="social.connecting.value === 'facebook'" @click="doConnect('facebook')">
            <i class="bi bi-plus-circle" /> {{ social.connecting.value === 'facebook' ? 'Đang mở…' : 'Kết nối thêm Page' }}
          </button>
        </div>
      </div>

      <!-- TikTok -->
      <div class="social-platform">
        <label class="social-platform-head">
          <span class="social-platform-name"><i class="bi bi-tiktok" /> TikTok</span>
          <input v-model="local.social.tiktok.enabled" type="checkbox" class="apl-toggle-input" >
        </label>
        <div v-if="local.social.tiktok.enabled" class="social-accounts">
          <p v-if="social.loading.value" class="apl-fm-hint">Đang tải danh sách tài khoản…</p>
          <p v-else-if="!ttAccounts.length" class="apl-fm-hint">Chưa kết nối tài khoản nào.</p>
          <label v-for="a in ttAccounts" :key="a.id" class="social-account-row">
            <input type="checkbox" :checked="isChecked('tiktok', a.id)" @change="toggleAccount('tiktok', a.id)" >
            <img v-if="a.avatar_url" :src="a.avatar_url" class="social-avatar" >
            <span class="truncate">{{ a.name || a.external_id }}</span>
          </label>
          <button type="button" class="social-connect-btn" :disabled="social.connecting.value === 'tiktok'" @click="doConnect('tiktok')">
            <i class="bi bi-plus-circle" /> {{ social.connecting.value === 'tiktok' ? 'Đang mở…' : 'Kết nối tài khoản' }}
          </button>
          <p class="apl-fm-hint">
            <i class="bi bi-info-circle me-1" />Chỉ đăng được <b>video</b>. App chưa được TikTok duyệt Content Posting API
            công khai → bài đăng ở chế độ <b>riêng tư (chỉ mình xem)</b> cho tới khi được duyệt.
          </p>
        </div>
      </div>
    </div>
    <!-- #endregion -->
  </div>
</template>

<script setup>
const props = defineProps({ config: { type: Object, required: true } })
const emit = defineEmits(['update:config'])

const formats = [
  { id: 'markdown', label: 'Markdown', icon: 'bi-markdown', desc: 'Chat render markdown (bảng, code, list). Default — phù hợp hầu hết case.' },
  { id: 'text',     label: 'Text',     icon: 'bi-text-paragraph', desc: 'Plain text monospace, không format. Cho output raw từ LLM/HTTP.' },
  { id: 'json',     label: 'JSON',     icon: 'bi-braces',  desc: 'Parse text → JSON object trong metadata.parsed. Chat render syntax highlight + UI để inspect.' },
  { id: 'video',    label: 'Video',    icon: 'bi-film',    desc: 'Render player MP4 inline (output.video URL). Cho Motion Transfer.' },
  { id: 'image',    label: 'Image',    icon: 'bi-image',   desc: 'Render ảnh preview (output.image URL). Cho Image generation nodes.' },
  { id: 'file',     label: 'File',     icon: 'bi-file-earmark-arrow-down', desc: 'Trả file download cho user (nếu prev node có .file).' }
]

// #region ALD 05/07/2026 - config.social: { enabled, caption, facebook:{enabled,accountIds[]}, tiktok:{enabled,accountIds[]} }
// Toggle + settings kết nối MXH (Facebook Page / TikTok) ngay trong props node Output — bật thì node "output"
// (handlers.js handleOutput) tự đăng khi workflow ra kết quả. Danh sách account lấy qua useSocialAccounts()
// (BE /social-accounts), Connect mở popup OAuth (xem composable).
const social = useSocialAccounts()
const toast = useToast()
onMounted(() => social.load())

const fbAccounts = computed(() => social.items.value.filter((a) => a.platform === 'facebook'))
const ttAccounts = computed(() => social.items.value.filter((a) => a.platform === 'tiktok'))

function defaultSocial() {
  return {
    enabled: false,
    caption: '',
    facebook: { enabled: false, accountIds: [] },
    tiktok: { enabled: false, accountIds: [] }
  }
}

const local = reactive({
  format: props.config.format || 'markdown',
  cleanup: props.config.cleanup ?? false,
  social: {
    ...defaultSocial(),
    ...(props.config.social || {}),
    facebook: { ...defaultSocial().facebook, ...(props.config.social?.facebook || {}) },
    tiktok: { ...defaultSocial().tiktok, ...(props.config.social?.tiktok || {}) }
  }
})
watch(local, (v) => emit('update:config', { ...v }), { deep: true })

const activeFormat = computed(() => formats.find((f) => f.id === local.format) || formats[0])

function isChecked(platform, id) {
  return (local.social[platform].accountIds || []).includes(id)
}
function toggleAccount(platform, id) {
  const list = local.social[platform].accountIds || []
  local.social[platform].accountIds = list.includes(id) ? list.filter((x) => x !== id) : [...list, id]
}
async function doConnect(platform) {
  try {
    await social.connect(platform)
    toast.success(platform === 'facebook' ? 'Đã kết nối Facebook Page' : 'Đã kết nối TikTok')
  } catch (e) {
    toast.error(e?.message || 'Kết nối thất bại')
  }
}
</script>

<style scoped>
.apl-label { display: block; font-size: 11px; font-weight: 700; color: var(--apl-label); text-transform: uppercase; letter-spacing: 0.04em; }
.apl-segmented {
  display: inline-flex; width: 100%;
  background: rgba(118,118,128,0.12); border-radius: 9px; padding: 2px;
}
.apl-seg-btn {
  flex: 1; padding: 6px 8px;
  background: transparent; border: none; border-radius: 7px;
  font-size: 11px; font-weight: 600; color: var(--apl-label);
  cursor: pointer; font-family: inherit;
  transition: all 0.18s cubic-bezier(0.32, 0.72, 0, 1);
}
.apl-seg-btn:hover { color: var(--apl-label); }
.apl-seg-btn.is-active {
  background: var(--apl-bg-secondary); color: var(--apl-label);
  box-shadow: 0 0.5px 1px rgba(0,0,0,0.08), 0 2px 4px rgba(0,0,0,0.08);
}
.apl-card {
  padding: 12px;
  background: rgba(118,118,128,0.06);
  border-radius: 10px;
  border: 0.5px solid rgba(235,236,240,0.12);
}
.apl-card-header {
  font-size: 11px; font-weight: 700; color: var(--apl-label);
  text-transform: uppercase; letter-spacing: 0.04em;
}
.apl-card-hint { margin-top: 4px; font-size: 11px; color: var(--apl-label); line-height: 1.45; }
/* ALD 15/06/2026 - toggle xả RAM/VRAM */
.apl-toggle { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; margin-top: 12px; padding: 10px 12px; background: var(--apl-fill); border: 0.5px solid rgba(235,236,240,0.14); border-radius: 12px; cursor: pointer; }
.apl-toggle-title { display: block; font-size: 12.5px; font-weight: 700; color: var(--apl-label); }
.apl-toggle-input { flex-shrink: 0; width: 38px; height: 22px; margin-top: 2px; -webkit-appearance: none; appearance: none; background: rgba(235,236,240,0.22); border-radius: 999px; position: relative; cursor: pointer; transition: background 0.18s; }
.apl-toggle-input:checked { background: #34C759; }
.apl-toggle-input::after { content: ''; position: absolute; top: 2px; left: 2px; width: 18px; height: 18px; border-radius: 50%; background: var(--apl-bg-secondary); transition: transform 0.18s; }
.apl-toggle-input:checked::after { transform: translateX(16px); }

/* ALD 05/07/2026 - panel Đăng MXH */
.social-panel { display: flex; flex-direction: column; gap: 10px; padding: 12px; background: rgba(10,132,255,0.05); border: 0.5px solid rgba(10,132,255,0.18); border-radius: 12px; }
.social-textarea { height: auto; padding: 8px 10px; font-family: inherit; resize: vertical; }
.social-platform { border-top: 0.5px solid rgba(235,236,240,0.12); padding-top: 10px; }
.social-platform:first-of-type { border-top: none; padding-top: 0; }
.social-platform-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; cursor: pointer; }
.social-platform-name { display: flex; align-items: center; gap: 6px; font-size: 12.5px; font-weight: 700; color: var(--apl-label); }
.social-accounts { margin-top: 8px; display: flex; flex-direction: column; gap: 6px; }
.social-account-row { display: flex; align-items: center; gap: 8px; padding: 6px 8px; background: var(--apl-bg-secondary); border: 0.5px solid rgba(235,236,240,0.14); border-radius: 8px; font-size: 12px; cursor: pointer; }
.social-avatar { width: 18px; height: 18px; border-radius: 50%; object-fit: cover; flex-shrink: 0; }
.social-connect-btn {
  display: inline-flex; align-items: center; gap: 5px; align-self: flex-start;
  margin-top: 2px; padding: 6px 10px;
  background: rgba(10,132,255,0.1); border: 0.5px solid rgba(10,132,255,0.3); border-radius: 8px;
  font-size: 11.5px; font-weight: 600; color: #0a84ff; cursor: pointer; font-family: inherit;
  transition: background 0.15s;
}
.social-connect-btn:hover:not(:disabled) { background: rgba(10,132,255,0.18); }
.social-connect-btn:disabled { opacity: 0.6; cursor: default; }
</style>
