<template>
  <!-- #region ALD 05/07/2026 - UX bắt buộc cho đăng TikTok (Content Sharing Guidelines mục 4) — dùng chung cho
       cả 3 nơi tạo bài TikTok: composer "Đề xuất", "Upload file test", và slot editor "Kế hoạch". Component tự
       gọi creator_info khi có accountId (mục 1: hiển thị nickname, giới hạn thời lượng, tương tác bị tài khoản
       tắt sẵn) và bắt user tự chọn Privacy/Duet/Comment/Stitch/Commercial — KHÔNG có giá trị mặc định "ẩn". -->
  <div class="space-y-3 rounded-xl border border-white/[0.07] bg-white/[0.02] p-3 text-gray-800">
    <p v-if="!accountId" class="text-[11px] text-gray-400"><i class="bi bi-info-circle me-1" />Chọn 1 tài khoản TikTok ở trên để hiện đầy đủ tuỳ chọn đăng bài.</p>

    <template v-else>
      <p v-if="loading" class="text-[11px] text-gray-400">Đang tải thông tin tài khoản TikTok…</p>
      <p v-else-if="loadError" class="text-[11px] text-rose-600"><i class="bi bi-exclamation-triangle me-1" />{{ loadError }}</p>

      <template v-else-if="info">
        <div class="flex items-center gap-2">
          <img v-if="info.avatarUrl" :src="info.avatarUrl" class="w-6 h-6 rounded-full object-cover flex-shrink-0 bg-gray-100" >
          <p class="text-xs text-gray-600">Đang đăng vào tài khoản: <span class="font-semibold text-gray-900">@{{ info.nickname || '(không rõ)' }}</span></p>
        </div>

        <p v-if="durationWarning" class="text-[11px] text-rose-600"><i class="bi bi-exclamation-triangle me-1" />{{ durationWarning }}</p>

        <!-- Privacy — BẮT BUỘC chọn, không mặc định. Dropdown tự vẽ (không dùng <select> gốc — <select> native
             render theo OS/browser nền trắng, vỡ theme tối, đây là lỗi "value option" user báo). -->
        <div>
          <label class="text-xs font-semibold text-gray-500 uppercase tracking-wide">Chế độ hiển thị</label>
          <SocialDropdown
            v-model="local.privacyLevel"
            :options="privacyOptions"
            placeholder="-- Chọn chế độ hiển thị --"
            class="mt-1"
          />
          <p v-if="local.brandContentToggle" class="text-[10px] text-primary mt-1">Nội dung "Đối tác được trả tiền" không được để chế độ riêng tư.</p>
        </div>

        <!-- Tương tác — mặc định TẮT, user tự bật; xám nếu tài khoản đã tắt sẵn -->
        <div>
          <label class="text-xs font-semibold text-gray-500 uppercase tracking-wide">Cho phép tương tác</label>
          <div class="mt-1 flex flex-wrap gap-3">
            <label class="flex items-center gap-1.5 text-xs text-gray-700" :class="{ 'opacity-40': info.commentDisabled }">
              <input type="checkbox" v-model="allowComment" :disabled="info.commentDisabled" > Bình luận
            </label>
            <label class="flex items-center gap-1.5 text-xs text-gray-700" :class="{ 'opacity-40': info.duetDisabled }">
              <input type="checkbox" v-model="allowDuet" :disabled="info.duetDisabled" > Duet
            </label>
            <label class="flex items-center gap-1.5 text-xs text-gray-700" :class="{ 'opacity-40': info.stitchDisabled }">
              <input type="checkbox" v-model="allowStitch" :disabled="info.stitchDisabled" > Stitch
            </label>
          </div>
          <p v-if="info.commentDisabled || info.duetDisabled || info.stitchDisabled" class="text-[10px] text-gray-400 mt-1">Ô xám = tài khoản này đã tắt sẵn trong cài đặt TikTok, không bật lại được ở đây.</p>
        </div>

        <!-- Commercial Content Disclosure — mặc định TẮT -->
        <div>
          <label class="flex items-center gap-1.5 text-xs font-semibold text-gray-800">
            <input type="checkbox" v-model="commercialOn" > Nội dung thương mại / quảng cáo
          </label>
          <div v-if="commercialOn" class="mt-1.5 ml-1 space-y-1">
            <label class="flex items-center gap-1.5 text-xs text-gray-600">
              <input type="checkbox" v-model="local.brandOrganicToggle" > Thương hiệu của bạn — quảng bá cho chính mình/doanh nghiệp mình
            </label>
            <label class="flex items-center gap-1.5 text-xs text-gray-600">
              <input type="checkbox" v-model="local.brandContentToggle" > Nội dung được tài trợ — quảng bá cho bên thứ ba
            </label>
            <p v-if="commercialOn && !local.brandOrganicToggle && !local.brandContentToggle" class="text-[10px] text-rose-600">Chọn ít nhất 1 trong 2 mục trên.</p>
          </div>
        </div>

        <p class="text-[11px] text-gray-400 border-t border-white/[0.07] pt-2.5">
          Bằng cách đăng bài, bạn đồng ý với
          <a v-if="local.brandContentToggle" href="https://www.tiktok.com/legal/page/global/bc-policy/en" target="_blank" rel="noopener" class="text-primary hover:underline">Chính sách nội dung thương hiệu</a>
          <span v-if="local.brandContentToggle"> và </span>
          <a href="https://www.tiktok.com/legal/page/global/music-usage-confirmation/en" target="_blank" rel="noopener" class="text-primary hover:underline">Xác nhận sử dụng âm nhạc</a>
          của TikTok.
        </p>
      </template>
    </template>
  </div>
  <!-- #endregion -->
</template>

<script setup>
const props = defineProps({
  modelValue: {
    type: Object,
    default: () => ({ privacyLevel: '', disableDuet: true, disableComment: true, disableStitch: true, brandContentToggle: false, brandOrganicToggle: false })
  },
  accountId: { type: String, default: null },
  videoDurationSec: { type: Number, default: null }
})
const emit = defineEmits(['update:modelValue'])

const social = useSocialAccounts()
const loading = ref(false)
const loadError = ref('')
const info = ref(null)

const local = reactive({
  privacyLevel: props.modelValue.privacyLevel || '',
  disableDuet: props.modelValue.disableDuet !== false,
  disableComment: props.modelValue.disableComment !== false,
  disableStitch: props.modelValue.disableStitch !== false,
  brandContentToggle: !!props.modelValue.brandContentToggle,
  brandOrganicToggle: !!props.modelValue.brandOrganicToggle
})
watch(local, (v) => emit('update:modelValue', { ...v }), { deep: true })

const allowComment = computed({ get: () => !local.disableComment, set: (v) => { local.disableComment = !v } })
const allowDuet = computed({ get: () => !local.disableDuet, set: (v) => { local.disableDuet = !v } })
const allowStitch = computed({ get: () => !local.disableStitch, set: (v) => { local.disableStitch = !v } })
const commercialOn = computed({
  get: () => local.brandContentToggle || local.brandOrganicToggle,
  set: (v) => { if (!v) { local.brandContentToggle = false; local.brandOrganicToggle = false } }
})

// ALD 05/07/2026 - Fix lỗi hiện raw enum (vd "FOLLOWER_OF_CREATOR") thay vì nhãn tiếng Việt — thiếu mapping cho
// giá trị này (1 trong 4 privacy_level_options mà creator_info trả về).
function privacyLabel(opt) {
  return {
    SELF_ONLY: 'Chỉ mình tôi',
    PUBLIC_TO_EVERYONE: 'Công khai',
    MUTUAL_FOLLOW_FRIENDS: 'Bạn bè',
    FOLLOWER_OF_CREATOR: 'Người theo dõi'
  }[opt] || opt
}

const privacyOptions = computed(() => (info.value?.privacyLevelOptions || []).map((opt) => ({
  value: opt, label: privacyLabel(opt), disabled: local.brandContentToggle && opt === 'SELF_ONLY'
})))

const durationWarning = computed(() => {
  const max = info.value?.maxVideoPostDurationSec
  if (!max || !props.videoDurationSec) return ''
  return props.videoDurationSec > max ? `Video dài ${Math.round(props.videoDurationSec)}s, vượt giới hạn ${max}s cho phép của tài khoản này.` : ''
})

// Nếu bật Branded Content mà đang chọn SELF_ONLY → tự bỏ chọn, bắt user chọn lại chế độ công khai/bạn bè.
watch(() => local.brandContentToggle, (on) => {
  if (on && local.privacyLevel === 'SELF_ONLY') local.privacyLevel = ''
})

async function loadInfo() {
  if (!props.accountId) { info.value = null; return }
  loading.value = true
  loadError.value = ''
  try {
    info.value = await social.fetchTikTokCreatorInfo(props.accountId)
    // Chỉ 1 option (thường SELF_ONLY khi app chưa audit) → chọn sẵn cho đỡ phải bấm (vẫn là lựa chọn hợp lệ duy nhất).
    if (info.value?.privacyLevelOptions?.length === 1 && !local.privacyLevel) local.privacyLevel = info.value.privacyLevelOptions[0]
  } catch (e) {
    loadError.value = e?.data?.error || e?.message || 'Không lấy được thông tin tài khoản TikTok'
  } finally {
    loading.value = false
  }
}
watch(() => props.accountId, loadInfo, { immediate: true })
</script>
