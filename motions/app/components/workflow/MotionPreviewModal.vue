<template>
  <!-- #region ALD 26/06/2026 - Modal xem thử Motion Transfer (redesign): render nhanh để kiểm tra
       màu/sáng/pose/biểu cảm trước khi chạy full workflow. Teleport ra body để float trên mọi z-index. -->
  <Teleport to="body">
    <Transition enter-active-class="transition-opacity duration-200" leave-active-class="transition-opacity duration-200"
      enter-from-class="opacity-0" leave-to-class="opacity-0">
      <div v-if="modelValue" class="fixed inset-0 z-[200] bg-black/50 backdrop-blur-sm" @click.self="close" />
    </Transition>
    <Transition enter-active-class="transition-transform duration-200" leave-active-class="transition-transform duration-200"
      enter-from-class="translate-y-full" leave-to-class="translate-y-full">
      <aside
        v-if="modelValue"
        class="fixed inset-x-0 bottom-0 z-[201] mx-auto flex w-full max-w-5xl flex-col overflow-hidden rounded-t-[28px] border border-white/[0.08] bg-gray-100/95 shadow-[0_-24px_64px_rgba(0,0,0,0.22)] backdrop-blur-xl pb-[env(safe-area-inset-bottom)]"
      >
        <div class="mx-auto mt-3 h-1.5 w-12 rounded-full bg-gray-300/80" />

        <!-- Header -->
        <header class="flex-shrink-0 flex items-center gap-3 px-5 h-14 border-b border-gray-100 bg-gray-50/70">
          <div class="flex items-center gap-2 flex-1 min-w-0">
            <span class="inline-flex items-center justify-center w-8 h-8 rounded-lg bg-rose-100 text-rose-600 flex-shrink-0">
              <i class="bi bi-play-circle-fill text-sm" />
            </span>
            <div class="min-w-0">
              <h3 class="text-sm font-bold text-gray-900 leading-tight">Xem thử Motion Transfer</h3>
              <p class="text-[11px] text-gray-400 leading-tight">~25 frame · 16fps · kiểm tra màu/sáng/pose</p>
            </div>
          </div>
          <button type="button" class="flex-shrink-0 h-8 w-8 flex items-center justify-center rounded-lg text-gray-400 hover:bg-gray-200 hover:text-gray-600 transition" @click="close">
            <i class="bi bi-x-lg text-sm" />
          </button>
        </header>

        <!-- Body -->
        <div class="flex-1 min-h-0 overflow-y-auto">

          <!-- === KẾT QUẢ === -->
          <div v-if="result" class="p-5 space-y-4">
            <div class="flex items-center gap-2 text-green-700 bg-green-50 rounded-xl px-3 py-2">
              <i class="bi bi-check-circle-fill text-green-500" />
              <span class="text-sm font-medium">Hoàn thành! So sánh bên dưới.</span>
            </div>
            <!-- So sánh ảnh gốc / video output -->
            <div class="grid grid-cols-2 gap-3">
              <div>
                <p class="text-[11px] font-medium text-gray-400 mb-1.5 uppercase tracking-wide">Ảnh gốc</p>
                <img v-if="refImagePreview" :src="refImagePreview" class="w-full aspect-[9/16] object-cover rounded-xl shadow" />
              </div>
              <div>
                <p class="text-[11px] font-medium text-gray-400 mb-1.5 uppercase tracking-wide">Kết quả</p>
                <video :src="result" autoplay loop controls playsinline class="w-full aspect-[9/16] object-cover rounded-xl shadow" />
              </div>
            </div>
            <!-- ALD 26/06/2026 - Thông số ĐÃ DÙNG cho lần render này (đối chiếu khi so màu/pose). -->
            <div v-if="usedParams" class="rounded-xl border border-gray-100 bg-gray-50 overflow-hidden">
              <div class="flex items-center gap-2 px-3.5 py-2.5 border-b border-gray-100">
                <i class="bi bi-sliders2 text-xs text-gray-400" />
                <span class="text-[11px] font-semibold text-gray-500 uppercase tracking-wide">Thông số đã dùng</span>
              </div>
              <div class="px-3.5 py-3 grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
                <ParamRow label="Face strength" :val="usedParams.faceStrength" />
                <ParamRow label="Pose strength" :val="usedParams.poseStrength" />
                <ParamRow label="CLIP strength" :val="usedParams.clipStrength" />
                <ParamRow label="Relight LoRA" :val="usedParams.loraRelight" :warn="(usedParams.loraRelight || 0) > 0" warnMsg="≠0 → cháy da" />
                <ParamRow label="Skip frames" :val="usedParams.skipFirstFrames" />
              </div>
            </div>
            <button type="button" class="w-full flex items-center justify-center gap-2 text-sm text-indigo-600 hover:text-indigo-800 hover:bg-indigo-50 rounded-full py-2 transition" @click="resetResult">
              <i class="bi bi-arrow-repeat" /> Thử lại
            </button>
          </div>

          <!-- === FORM NẾU CHƯA CÓ KẾT QUẢ === -->
          <template v-else>

            <!-- INPUTS: 2 upload zones cạnh nhau -->
            <div class="p-5 pb-3 grid grid-cols-2 gap-3">
              <!-- Ảnh mẫu -->
              <label class="flex flex-col gap-2 cursor-pointer group">
                <span class="text-xs font-semibold text-gray-600">Ảnh người mẫu</span>
                <div class="relative flex-1 min-h-[160px] rounded-xl border-2 transition overflow-hidden"
                  :class="refImagePreview ? 'border-indigo-300' : 'border-dashed border-gray-200 group-hover:border-indigo-300 bg-gray-50'">
                  <img v-if="refImagePreview" :src="refImagePreview" class="absolute inset-0 w-full h-full object-cover" />
                  <div v-else class="flex flex-col items-center justify-center h-full gap-1 p-3 text-center">
                    <i class="bi bi-person-bounding-box text-2xl text-gray-300" />
                    <span class="text-xs text-gray-400">Nhấn để chọn</span>
                  </div>
                  <div v-if="refImagePreview" class="absolute inset-0 bg-black/0 group-hover:bg-black/30 transition flex items-center justify-center">
                    <span class="opacity-0 group-hover:opacity-100 text-white text-xs font-medium bg-black/40 px-2 py-1 rounded transition">Đổi ảnh</span>
                  </div>
                </div>
                <input type="file" accept="image/*" class="sr-only" @change="onRefImageChange" />
              </label>

              <!-- Video motion -->
              <label class="flex flex-col gap-2 cursor-pointer group">
                <span class="text-xs font-semibold text-gray-600">Video motion</span>
                <div class="relative flex-1 min-h-[160px] rounded-xl border-2 transition overflow-hidden"
                  :class="motionFile ? 'border-indigo-300 bg-indigo-50/20' : 'border-dashed border-gray-200 group-hover:border-indigo-300 bg-gray-50'">
                  <div class="flex flex-col items-center justify-center h-full gap-1 p-3 text-center">
                    <i class="bi bi-film text-2xl" :class="motionFile ? 'text-indigo-500' : 'text-gray-300'" />
                    <span class="text-xs font-medium truncate w-full px-1" :class="motionFile ? 'text-indigo-700' : 'text-gray-400'">
                      {{ motionFile?.name || 'Nhấn để chọn' }}
                    </span>
                    <span v-if="motionFile" class="text-[11px] text-gray-400">{{ (motionFile.size / 1024 / 1024).toFixed(1) }} MB</span>
                  </div>
                </div>
                <input type="file" accept="video/*" class="sr-only" @change="onMotionChange" />
              </label>
            </div>

            <!-- PARAMS (CHỈNH ĐƯỢC) - ALD 26/06/2026: sửa tại chỗ rồi Render thử; sync ngược node luôn. -->
            <div class="mx-5 mb-3 rounded-xl border border-gray-100 bg-gray-50 overflow-hidden">
              <div class="flex items-center justify-between px-3.5 py-2.5 border-b border-gray-100">
                <div class="flex items-center gap-2">
                  <i class="bi bi-sliders2 text-xs text-gray-400" />
                  <span class="text-[11px] font-semibold text-gray-500 uppercase tracking-wide">Thông số (chỉnh trực tiếp)</span>
                </div>
                <button type="button" class="text-[10px] text-indigo-500 hover:text-indigo-700 hover:underline" @click="syncFromProps">
                  <i class="bi bi-arrow-counterclockwise mr-0.5" />Khôi phục
                </button>
              </div>
              <div class="px-3.5 py-3 grid grid-cols-2 gap-x-4 gap-y-2.5">
                <label class="flex flex-col gap-1">
                  <span class="text-[11px] text-gray-500">Face strength</span>
                  <input v-model.number="form.faceStrength" type="number" step="0.1" min="0" max="1.5" class="apl-fm-input h-8 text-xs" />
                </label>
                <label class="flex flex-col gap-1">
                  <span class="text-[11px] text-gray-500">Pose strength</span>
                  <input v-model.number="form.poseStrength" type="number" step="0.1" min="0" max="1.5" class="apl-fm-input h-8 text-xs" />
                </label>
                <label class="flex flex-col gap-1">
                  <span class="text-[11px] text-gray-500">CLIP strength</span>
                  <input v-model.number="form.clipStrength" type="number" step="0.1" min="0.5" max="2" class="apl-fm-input h-8 text-xs" />
                </label>
                <label class="flex flex-col gap-1">
                  <span class="text-[11px] text-gray-500">Relight LoRA</span>
                  <input v-model.number="form.loraRelight" type="number" step="0.05" min="0" max="1.5" class="apl-fm-input h-8 text-xs"
                    :class="(form.loraRelight || 0) > 0 && 'ring-1 ring-amber-300'" />
                </label>
                <div class="flex flex-col gap-1">
                  <span class="text-[11px] text-gray-500">ColorMatch (mkl)</span>
                  <button type="button" @click="form.matchRef = !form.matchRef"
                    class="h-8 rounded-lg text-xs font-semibold border transition"
                    :class="form.matchRef ? 'bg-green-50 border-green-200 text-green-700' : 'bg-gray-100 border-gray-200 text-gray-400'">
                    {{ form.matchRef ? 'BẬT' : 'TẮT' }}
                  </button>
                </div>
                <label class="flex flex-col gap-1" :class="!form.matchRef && 'opacity-40 pointer-events-none'">
                  <span class="text-[11px] text-gray-500">Match strength</span>
                  <input v-model.number="form.matchRefStrength" type="number" step="0.05" min="0" max="1" class="apl-fm-input h-8 text-xs" />
                </label>
                <label class="flex flex-col gap-1" :class="!form.matchRef && 'opacity-40 pointer-events-none'">
                  <span class="text-[11px] text-gray-500">ColorMatch method</span>
                  <select v-model="form.matchRefMethod" class="apl-fm-input h-8 text-xs">
                    <option v-for="m in COLORMATCH_METHODS" :key="m.value" :value="m.value">{{ m.value }}</option>
                  </select>
                </label>
                <label class="flex flex-col gap-1">
                  <span class="text-[11px] text-gray-500">Bright cap</span>
                  <input v-model.number="form.brightCap" type="number" step="0.01" min="0.7" max="1" class="apl-fm-input h-8 text-xs" />
                </label>
                <label class="flex flex-col gap-1">
                  <span class="text-[11px] text-gray-500">Skip frames</span>
                  <input v-model.number="form.skipFirstFrames" type="number" step="1" min="0" max="32" class="apl-fm-input h-8 text-xs" />
                </label>
              </div>
              <!-- ALD 26/06/2026 - "Nhiệt độ màu" = ĐỘ ẤM iPhone. GỐC ám vàng/cam = white balance output quá ấm.
                   Kéo ÂM = mát hóa → hết ám MÀ giữ độ tươi. Đây là knob CHÍNH trị ám vàng (không phải Match strength). -->
              <div class="px-3.5 pb-3">
                <div class="flex items-center justify-between mb-1">
                  <span class="font-mono text-xs font-semibold" :class="(form.warmth || 0) < 0 ? 'text-sky-600' : (form.warmth || 0) > 0 ? 'text-amber-600' : 'text-gray-400'">{{ form.warmth || 0 }}</span>
                </div>
                <input v-model.number="form.warmth" type="range" step="1" min="-100" max="100" class="w-full accent-sky-500" />
              </div>
              <p v-if="(form.loraRelight || 0) > 0" class="px-3.5 pb-1 flex items-start gap-1 text-[10px] text-amber-600">
                <i class="bi bi-exclamation-triangle flex-shrink-0 mt-0.5" /><span>Relight ≠ 0 → dễ <b>cháy da</b>. Đề xuất 0.</span>
              </p>
              <div class="px-3.5 pb-2.5 flex items-center gap-1 text-[10px] text-gray-400">
                <i class="bi bi-info-circle" />
                25 frame · 16fps · 544×960 · 4 steps distill
              </div>
            </div>

            <!-- PROGRESS -->
            <div v-if="jobId" class="mx-5 mb-3 space-y-2">
              <div class="flex items-center justify-between text-xs">
                <span class="text-gray-600 truncate pr-2">{{ stepText || 'Đang xếp hàng…' }}</span>
                <span class="font-mono text-gray-500 flex-shrink-0">{{ Math.round(progress * 100) }}%</span>
              </div>
              <div class="h-1.5 bg-gray-100 rounded-full overflow-hidden">
                <div class="h-full bg-indigo-500 rounded-full transition-all duration-700" :style="{ width: Math.round(progress * 100) + '%' }" />
              </div>
              <button type="button" class="text-xs text-red-400 hover:text-red-600 hover:underline" @click="cancelJob">
                <i class="bi bi-x-circle mr-0.5" />Hủy render
              </button>
            </div>

            <!-- ERROR -->
            <div v-if="error" class="mx-5 mb-3 flex gap-2 text-sm text-red-600 bg-red-50 border border-red-100 rounded-xl px-3 py-2.5">
              <i class="bi bi-exclamation-circle-fill flex-shrink-0 mt-0.5" />
              <span class="text-xs">{{ error }}</span>
            </div>

          </template>
        </div>

        <!-- Footer -->
        <footer class="flex-shrink-0 flex items-center gap-2 px-5 py-3 border-t border-gray-100 bg-gray-50/60">
          <button type="button" class="apl-btn-ghost rounded-full" @click="close">Đóng</button>
          <button
            v-if="!result"
            type="button"
            :disabled="!refImageFile || !motionFile || !!jobId"
            class="apl-btn-primary flex-1 rounded-full"
            @click="startPreview"
          >
            <i class="bi" :class="jobId ? 'bi-hourglass-split animate-spin' : 'bi-play-fill'" />
            {{ jobId ? 'Đang render…' : 'Render thử (~2 phút)' }}
          </button>
          <button v-else type="button" class="apl-btn-primary flex-1 rounded-full" @click="resetResult">
            <i class="bi bi-arrow-repeat" /> Thử lại
          </button>
        </footer>
      </aside>
    </Transition>
  </Teleport>
  <!-- #endregion -->
</template>

<script setup>
const props = defineProps({
  modelValue: { type: Boolean, default: false },
  params: { type: Object, default: () => ({}) }
})
// ALD 26/06/2026 - update:params: chỉnh trong modal → sync ngược node (full workflow dùng cùng thông số).
const emit = defineEmits(['update:modelValue', 'update:params'])

const { create, cancel, poll } = useMotionJobs()

const refImageFile = ref(null)
const refImagePreview = ref(null)
const motionFile = ref(null)
const jobId = ref(null)
const progress = ref(0)
const stepText = ref('')
const result = ref(null)
const error = ref(null)
let poller = null

// ALD 26/06/2026 - Params EDIT ĐƯỢC ngay trong modal: kéo Match strength/pose… rồi Render thử luôn,
// không cần quay lại Inspector. Sửa ở đây cũng đẩy ngược về node (emit update:params) nên full workflow dùng cùng.
const PARAM_DEFAULTS = {
  faceStrength: 0.6, poseStrength: 0.8, clipStrength: 1.2, loraRelight: 0,
  matchRef: false, matchRefMethod: 'reinhard', matchRefStrength: 0, warmth: 0, brightCap: 1.0, skipFirstFrames: 0,
}
// ALD 26/06/2026 - method ColorMatch: mkl khử ám mạnh nhưng strength cao→bệt; reinhard giữ độ tươi (per-channel).
const COLORMATCH_METHODS = [
  { value: 'reinhard', label: 'reinhard — giữ độ tươi (khuyên cho driver nắng ấm)' },
  { value: 'mkl', label: 'mkl — khử ám mạnh (dễ bệt màu ở strength cao)' },
  { value: 'mvgd', label: 'mvgd — cân bằng' },
  { value: 'hm-mkl-hm', label: 'hm-mkl-hm — chất lượng cao, giữ chi tiết' },
  { value: 'hm-mvgd-hm', label: 'hm-mvgd-hm — chất lượng cao (mềm)' },
]
const form = reactive({ ...PARAM_DEFAULTS })
const usedParams = ref(null)   // snapshot thông số đã dùng (hiện ở phần Kết quả)
let _syncing = false

function syncFromProps() {
  _syncing = true
  for (const k of Object.keys(PARAM_DEFAULTS)) form[k] = props.params?.[k] ?? PARAM_DEFAULTS[k]
  nextTick(() => { _syncing = false })
}
// Mở modal → nạp lại từ node. immediate để form có giá trị ngay lần đầu.
watch(() => props.modelValue, (open) => { if (open) syncFromProps() }, { immediate: true })
// Chỉnh form → đẩy ngược về node (bỏ qua lần nạp từ props để khỏi vòng lặp).
watch(form, () => { if (!_syncing) emit('update:params', { ...form }) }, { deep: true })

// Sub-component inline để hiện 1 dòng param
const ParamRow = defineComponent({
  props: { label: String, val: [String, Number, Boolean], ok: { type: Boolean, default: null }, warn: Boolean, warnMsg: String },
  setup(p) {
    return () => h('div', { class: 'contents' }, [
      h('span', { class: 'text-gray-400' }, p.label),
      h('div', { class: 'flex items-center gap-1' }, [
        h('span', {
          class: ['font-mono font-medium',
            p.ok === true ? 'text-green-700' : p.ok === false ? 'text-gray-400' :
            p.warn ? 'text-amber-600' : 'text-gray-800']
        }, String(p.val ?? '—')),
        p.warn && p.warnMsg ? h('span', { class: 'text-amber-500 text-[10px]' }, p.warnMsg) : null,
      ])
    ])
  }
})

function close() {
  if (jobId.value) return
  emit('update:modelValue', false)
}

function onRefImageChange(e) {
  const f = e.target.files?.[0]
  if (!f) return
  refImageFile.value = f
  refImagePreview.value = URL.createObjectURL(f)
}

function onMotionChange(e) {
  const f = e.target.files?.[0]
  if (f) motionFile.value = f
}

async function startPreview() {
  if (!refImageFile.value || !motionFile.value) return
  error.value = null
  progress.value = 0
  stepText.value = ''
  try {
    // Snapshot thông số dùng cho lần render này → hiện ở phần Kết quả để đối chiếu.
    usedParams.value = { ...form }
    const job = await create({
      refImage: refImageFile.value,
      motionVideo: motionFile.value,
      preset: '5s-720p',
      params: { ...props.params, ...form, previewFrames: 25, frames: 25, steps: 4 },
    })
    jobId.value = job.id
    poller = poll(job.id, {
      intervalMs: 3000,
      onUpdate(snap) { progress.value = snap.progress ?? 0; stepText.value = snap.current_step || '' },
      onDone(snap) {
        if (snap.status === 'done') { result.value = snap.output_url; progress.value = 1; stepText.value = 'Hoàn thành!'; jobId.value = null }
        else { error.value = snap.error || `Render ${snap.status}`; jobId.value = null }
      },
    })
  } catch (e) {
    error.value = e?.data?.message || e?.message || 'Lỗi tạo preview job'
  }
}

async function cancelJob() {
  if (!jobId.value) return
  try { await cancel(jobId.value) } catch {}
  poller?.stop(); jobId.value = null; progress.value = 0; stepText.value = ''
}

function resetResult() { result.value = null; progress.value = 0; stepText.value = '' }
onBeforeUnmount(() => poller?.stop())
</script>
