<template>
  <!-- #region ALD 23/05/2026 - Marketing video 1-screen no-scroll (match /motion layout)
       Grid 3 cột fill viewport. LEFT: 2 dropzone. MIDDLE: config + script. RIGHT: submit/tracker. -->
  <div class="flex-1 min-h-0 px-3 sm:px-4 pb-3 pt-1 overflow-hidden">
    <form
      class="h-full grid grid-cols-1 md:grid-cols-3 gap-3"
      @submit.prevent="onSubmit"
    >

      <!-- LEFT: 2 image inputs (sản phẩm + người mẫu) -->
      <UiCard class="!p-3 flex flex-col gap-2 min-h-0 overflow-hidden">
        <div class="flex items-center justify-between flex-shrink-0">
          <h3 class="text-xs font-black tracking-tight uppercase text-gray-700 flex items-center gap-1.5">
            <i class="bi bi-upload" /> Inputs
          </h3>
          <span
            :class="cn(
              'inline-flex items-center gap-1 text-[10px] font-bold rounded-full px-2 py-0.5',
              gpu.status.value.healthy ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-700'
            )"
          >
            <span :class="cn('h-1.5 w-1.5 rounded-full', gpu.status.value.healthy ? 'bg-emerald-500' : 'bg-amber-500')" />
            {{ gpu.status.value.healthy ? 'Worker Online' : 'Worker Offline' }}
          </span>
        </div>

        <UiMultiUpload
          v-model="productImages"
          label="Ảnh sản phẩm"
          icon="bi-box-seam"
          accent-color="primary"
          accept="image/jpeg,image/png,image/webp"
          :max-files="5"
          hint="1-5 ảnh · carousel ở góc dưới phải video"
        />
        <UiMultiUpload
          v-model="modelImages"
          label="Ảnh người mẫu"
          icon="bi-person-bounding-box"
          accent-color="rose"
          accept="image/jpeg,image/png,image/webp"
          :max-files="3"
          hint="1-3 ảnh · ảnh đầu dùng cho lip-sync talking head"
        />
      </UiCard>

      <!-- MIDDLE: config + script -->
      <UiCard class="!p-3 flex flex-col gap-2 min-h-0 overflow-hidden">
        <h3 class="text-xs font-black tracking-tight uppercase text-gray-700 flex items-center gap-1.5 flex-shrink-0">
          <i class="bi bi-sliders" /> Cấu hình
        </h3>
        <!-- Compact configs phía trên (flex-shrink-0) -->
        <div class="flex-shrink-0 space-y-2">
          <div>
            <label class="text-[10px] font-bold text-gray-700 mb-0.5 block">Lĩnh vực</label>
            <UiDropdown
              v-model="category"
              :options="categoryOptions"
              placeholder="Chọn lĩnh vực…"
              icon="bi-tags"
              full-width
              no-clear
            />
          </div>

          <!-- Giọng (3 button) + Tốc độ trên 1 row grid 5 cột (3+2) -->
          <div class="grid grid-cols-5 gap-2">
            <div class="col-span-3">
              <label class="text-[10px] font-bold text-gray-700 mb-0.5 block flex items-center gap-1">
                <i class="bi bi-mic" /> Giọng
              </label>
              <div class="grid grid-cols-3 gap-1">
                <button
                  v-for="v in motion.VOICES"
                  :key="v.id"
                  type="button"
                  :class="cn(
                    'press inline-flex items-center justify-center gap-1 h-9 rounded-xl text-[10px] font-bold border transition-colors',
                    voicePreference === v.id
                      ? 'bg-primary text-white border-primary shadow-pill'
                      : 'bg-gray-50 text-gray-600 border-gray-200 hover:border-primary/40 hover:text-primary'
                  )"
                  :title="v.hint"
                  @click="voicePreference = v.id"
                >
                  <i :class="['bi', v.icon]" />
                  <span>{{ v.id === 'auto' ? 'Auto' : v.id === 'male' ? 'Nam' : 'Nữ' }}</span>
                </button>
              </div>
            </div>
            <div class="col-span-2">
              <label class="text-[10px] font-bold text-gray-700 mb-0.5 block flex items-center gap-1">
                <i class="bi bi-speedometer2" /> Tốc độ
              </label>
              <UiDropdown v-model="voiceRate" :options="rateOptions" full-width no-clear />
            </div>
          </div>

          <!-- Thời lượng + Tỷ lệ grid 2 -->
          <div class="grid grid-cols-2 gap-2">
            <div>
              <label class="text-[10px] font-bold text-gray-700 mb-0.5 block flex items-center gap-1">
                <i class="bi bi-clock" /> Thời lượng
              </label>
              <UiDropdown v-model="durationSec" :options="durationOptions" full-width no-clear />
            </div>
            <div>
              <label class="text-[10px] font-bold text-gray-700 mb-0.5 block flex items-center gap-1">
                <i class="bi bi-aspect-ratio" /> Tỷ lệ
              </label>
              <UiDropdown v-model="resolution" :options="resolutionOptions" full-width no-clear />
            </div>
          </div>
        </div>

        <!-- Kịch bản textarea FILL phần còn lại -->
        <div class="flex-1 min-h-0 flex flex-col">
          <label class="text-[11px] font-bold text-gray-700 mb-1 flex items-center justify-between flex-shrink-0">
            <span class="flex items-center gap-1">
              <i class="bi bi-chat-quote" />
              Kịch bản
            </span>
            <span class="text-[9px] font-semibold text-gray-400 normal-case tracking-normal">
              {{ scriptText.length }}/2000 · {{ wordCount }} từ
            </span>
          </label>
          <textarea
            v-model="scriptText"
            maxlength="2000"
            placeholder="Nhập nội dung kịch bản đọc. Ví dụ: 'Pebsteel — đối tác tin cậy về nhà thép tiền chế. 30 năm kinh nghiệm, hơn 8000 dự án trên toàn quốc.'"
            class="flex-1 min-h-0 w-full rounded-2xl glass text-xs px-3 py-2 text-gray-800 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-inset focus:ring-primary/40 focus:bg-gray-50 resize-none"
          />
          <p class="text-[10px] text-gray-500 mt-1 italic flex-shrink-0 flex items-center gap-1">
            <i class="bi bi-info-circle" />
            <span>~150 từ/phút · 15s ≈ 35–40 từ · 30s ≈ 70–80 từ</span>
          </p>
        </div>
      </UiCard>

      <!-- RIGHT: submit / job tracker -->
      <UiCard class="!p-3 flex flex-col min-h-0 overflow-hidden">
          <h3 class="text-xs font-black tracking-tight uppercase text-gray-700 mb-2 flex items-center gap-1.5">
            <i :class="['bi', activeJob ? 'bi-activity' : 'bi-play-circle']" />
            {{ activeJob ? 'Job đang chạy' : 'Submit' }}
          </h3>

          <template v-if="!activeJob">
            <div class="flex-1 min-h-0 flex flex-col items-center justify-center gap-3 px-2 text-center">
              <span class="inline-flex h-16 w-16 items-center justify-center rounded-3xl bg-gradient-to-br from-blue-100 to-indigo-100 text-primary">
                <i class="bi bi-broadcast text-3xl" />
              </span>
              <div>
                <h4 class="text-base font-black tracking-tight title-gradient leading-tight">Marketing Video AI</h4>
                <p class="text-[11px] text-gray-500 mt-1">
                  TTS XTTS-v2 + Lip-sync · Output {{ resolution }} · {{ durationSec }}s
                </p>
              </div>
              <p v-if="errorMsg" class="text-[11px] font-semibold text-rose-700 bg-rose-50 border border-rose-100 px-2 py-1.5 rounded-xl flex items-center gap-1.5">
                <i class="bi bi-exclamation-triangle-fill" />
                {{ errorMsg }}
              </p>
            </div>

            <UiButton
              type="submit"
              variant="primary"
              size="lg"
              :loading="submitting"
              :disabled="!canSubmit || submitting"
              class="w-full flex-shrink-0"
            >
              <i v-if="!submitting" class="bi bi-play-fill text-lg" />
              {{ submitting ? 'Đang gửi…' : 'Tạo video' }}
            </UiButton>
            <p v-if="!canSubmit && !submitting" class="text-[10px] text-gray-400 text-center mt-1 flex items-center justify-center gap-1">
              <i class="bi bi-info-circle" />
              Cần đủ ảnh sản phẩm + ảnh người mẫu + kịch bản text
            </p>
          </template>

          <template v-else>
            <div class="flex-1 min-h-0 flex flex-col gap-2 overflow-hidden">
              <div class="flex items-center gap-2 px-1">
                <span :class="cn(
                  'inline-flex h-9 w-9 items-center justify-center rounded-2xl flex-shrink-0',
                  activeJob.status === 'done' ? 'bg-emerald-100 text-emerald-700'
                    : activeJob.status === 'error' ? 'bg-rose-100 text-rose-700'
                    : activeJob.status === 'cancelled' ? 'bg-gray-100 text-gray-500'
                    : 'bg-blue-100 text-primary'
                )">
                  <i :class="[
                    'bi text-base',
                    activeJob.status === 'done' ? 'bi-check2-circle'
                      : activeJob.status === 'error' ? 'bi-x-circle'
                      : activeJob.status === 'cancelled' ? 'bi-slash-circle'
                      : 'bi-arrow-clockwise animate-spin'
                  ]" />
                </span>
                <div class="flex-1 min-w-0">
                  <div class="text-xs font-bold text-gray-900 capitalize">{{ activeJob.status }}</div>
                  <div class="text-[11px] font-semibold text-gray-700 truncate" :title="activeJob.current_step">
                    {{ activeJob.current_step || activeJob.error || ('Job ' + (activeJob.id || '').slice(0, 8)) }}
                  </div>
                </div>
              </div>

              <div v-if="activeJob.status === 'running' || activeJob.status === 'queued'">
                <div class="flex items-center justify-between text-[10px] mb-1">
                  <span class="text-gray-500">Tiến độ</span>
                  <span class="font-bold text-primary">{{ Math.round((activeJob.progress || 0) * 100) }}%</span>
                </div>
                <div class="h-1.5 rounded-full bg-gray-100 overflow-hidden">
                  <div class="h-full bg-primary transition-all duration-300"
                       :style="{ width: `${Math.round((activeJob.progress || 0) * 100)}%` }" />
                </div>
              </div>

              <div v-if="activeJob.status === 'done' && activeJob.output_url"
                   class="flex-1 min-h-0 rounded-2xl overflow-hidden bg-black">
                <video :src="activeJob.output_url" controls autoplay class="w-full h-full object-contain" />
              </div>
              <div v-else-if="activeJob.status === 'error'"
                   class="flex-1 min-h-0 rounded-2xl bg-rose-50 border border-rose-100 p-3 overflow-y-auto">
                <div class="text-[11px] text-rose-700 font-mono break-all">{{ activeJob.error }}</div>
              </div>
              <div v-else
                   class="flex-1 min-h-0 rounded-2xl bg-gradient-to-br from-blue-50 to-indigo-50 flex flex-col items-center justify-center text-center px-3">
                <i class="bi bi-cpu text-3xl text-primary/40" />
                <p class="text-[11px] font-semibold text-gray-600 mt-1">
                  {{ activeJob.status === 'queued' ? 'Đợi GPU worker pick…' : 'Đang generate marketing video…' }}
                </p>
              </div>
            </div>

            <div class="flex gap-2 flex-shrink-0 mt-2">
              <button v-if="activeJob.status === 'queued' || activeJob.status === 'running'" type="button"
                      class="press flex-1 h-9 rounded-full bg-rose-500 text-white text-xs font-bold shadow-pill hover:bg-rose-600 transition-colors inline-flex items-center justify-center gap-1"
                      @click="onCancel">
                <i class="bi bi-x-octagon" /> Cancel
              </button>
              <button v-else type="button"
                      class="press flex-1 h-9 rounded-full bg-primary text-white text-xs font-bold shadow-pill hover:bg-primary-dark transition-colors inline-flex items-center justify-center gap-1"
                      @click="resetJob">
                <i class="bi bi-plus-circle" /> Job mới
              </button>
            </div>
          </template>
      </UiCard>
    </form>
  </div>
  <!-- #endregion -->
</template>

<script setup>
definePageMeta({ middleware: 'auth' })
useHead({ title: 'Marketing Video — Motions' })

const toast = useToast()
const gpu = useMotionWorkerStatus()
const motion = useMarketingJobs()

const productImages = ref([])
const modelImages = ref([])
const scriptText = ref('')
const category = ref('steel_building')
const voicePreference = ref('female')
const voiceRate = ref('+0%')
const durationSec = ref(15)
const resolution = ref('720x1280')

const submitting = ref(false)
const errorMsg = ref('')
const activeJob = ref(null)
let pollController = null

const canSubmit = computed(() =>
  productImages.value.length > 0 && modelImages.value.length > 0 && scriptText.value.trim().length > 0
)
const wordCount = computed(() => scriptText.value.trim().split(/\s+/).filter(Boolean).length)

const rateOptions = computed(() =>
  motion.VOICE_RATES.map((r) => ({ value: r.id, label: `${r.label} (${r.id})` }))
)

const categoryOptions = computed(() =>
  motion.CATEGORIES.map((c) => ({ value: c.id, label: c.label }))
)
const durationOptions = computed(() =>
  motion.DURATIONS.map((d) => ({ value: d.id, label: `${d.label} (${d.hint})` }))
)
const resolutionOptions = computed(() =>
  motion.RESOLUTIONS.map((r) => ({ value: r.id, label: r.label }))
)

async function onSubmit() {
  if (!canSubmit.value || submitting.value) return
  errorMsg.value = ''
  submitting.value = true
  try {
    const res = await motion.create({
      productImages: productImages.value,
      modelImages: modelImages.value,
      scriptText: scriptText.value.trim(),
      category: category.value,
      voicePreference: voicePreference.value,
      durationSec: durationSec.value,
      resolution: resolution.value,
      params: { voice_rate: voiceRate.value }
    })
    activeJob.value = { id: res.id, status: res.status || 'queued', progress: 0 }
    toast.success('Đã tạo marketing job', { duration: 3000 })
    startPolling(res.id)
  } catch (err) {
    const data = err?.data ?? err?.response?._data
    errorMsg.value = data?.error || err?.message || 'Không tạo được job. Thử lại.'
  } finally {
    submitting.value = false
  }
}

function startPolling(id) {
  if (pollController) pollController.stop()
  pollController = motion.poll(id, {
    onUpdate: (snap) => { activeJob.value = { ...activeJob.value, ...snap } },
    onDone: (snap) => {
      activeJob.value = { ...activeJob.value, ...snap }
      if (snap.status === 'done') toast.success('Marketing video hoàn tất!')
      else if (snap.status === 'error') toast.error(`Job lỗi: ${snap.error || 'unknown'}`)
      else if (snap.status === 'cancelled') toast.info('Đã hủy job')
    }
  })
}

async function onCancel() {
  if (!activeJob.value?.id) return
  try {
    await motion.cancel(activeJob.value.id)
    toast.info('Đã gửi yêu cầu hủy')
  } catch {
    toast.error('Không hủy được job')
  }
}

function resetJob() {
  if (pollController) pollController.stop()
  activeJob.value = null
  productImages.value = []
  modelImages.value = []
  scriptText.value = ''
  errorMsg.value = ''
}

onBeforeUnmount(() => {
  if (pollController) pollController.stop()
})
</script>
