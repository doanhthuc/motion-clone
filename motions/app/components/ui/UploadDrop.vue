<template>
  <!-- #region ALD 23/05/2026 - Reusable dropzone: click hoặc drag-drop file đơn lẻ.
       Root = flex-1 min-h-0 → khi đặt trong flex-col container, dropzone tự fill slot.
       Caller có thể override với class prop nếu cần fixed height. -->
  <div class="flex flex-col gap-1 flex-1 min-h-0">
    <label class="text-[11px] font-bold text-gray-700 flex items-center gap-1 flex-shrink-0">
      <i v-if="icon" :class="['bi', icon, accentTextClass]" />
      {{ label }}
      <span v-if="optional" class="text-[9px] font-semibold text-gray-400 uppercase tracking-wide ml-1">
        · tùy chọn
      </span>
    </label>
    <div
      :class="cn(
        'relative border-2 border-dashed rounded-2xl overflow-hidden transition-spring group flex-1',
        compact ? 'min-h-[80px]' : 'min-h-[120px]',
        dragging
          ? `border-${accentColor}-500 bg-${accentColor}-50/80 ring-4 ring-${accentColor}-500/15 scale-[1.01] ${ringBgClass}`
          : modelValue ? 'border-primary/40' : 'border-gray-200 hover:border-primary/40 bg-white/30 hover:bg-white/60'
      )"
      @click="!modelValue && inputRef?.click()"
      @dragenter.prevent="dragCount++; dragging = true"
      @dragover.prevent
      @dragleave.prevent="dragCount--; dragCount <= 0 && (dragging = false, dragCount = 0)"
      @drop.prevent="onDrop"
    >
      <input
        ref="inputRef"
        type="file"
        :accept="accept"
        class="hidden"
        @change="onPick"
      />

      <!-- Empty state -->
      <div v-if="!modelValue" class="absolute inset-0 flex flex-col items-center justify-center text-center px-3 cursor-pointer pointer-events-none">
        <i :class="[
          'bi text-2xl transition-spring',
          dragging
            ? 'bi-cloud-arrow-down-fill ' + accentTextClass
            : (icon || 'bi-cloud-arrow-up') + ' text-gray-300 group-hover:scale-110'
        ]" />
        <p :class="[
          'text-[11px] font-semibold mt-1',
          dragging ? accentTextClass : 'text-gray-600'
        ]">
          {{ dragging ? 'Thả để upload' : 'Click hoặc kéo file vào' }}
        </p>
        <p v-if="!dragging && hint" class="text-[10px] text-gray-400 mt-0.5 line-clamp-1">{{ hint }}</p>
      </div>

      <!-- File loaded — image / video preview -->
      <div v-else class="absolute inset-0">
        <img
          v-if="isImage"
          :src="previewUrl"
          class="w-full h-full object-cover"
          :alt="label"
        />
        <video
          v-else-if="isVideo"
          :src="previewUrl"
          class="w-full h-full object-cover"
          muted
          autoplay
          loop
          playsinline
        />
        <div v-else class="w-full h-full flex items-center justify-center bg-gray-100">
          <i class="bi bi-file-earmark text-3xl text-gray-400" />
        </div>
        <div class="absolute inset-0 bg-gradient-to-t from-black/60 via-transparent to-transparent" />
        <div class="absolute top-1.5 right-1.5">
          <button
            type="button"
            class="press h-7 w-7 flex items-center justify-center rounded-full bg-black/40 hover:bg-rose-500 text-white backdrop-blur transition-colors"
            title="Xoá"
            @click.stop.prevent="clear"
          >
            <i class="bi bi-x text-sm" />
          </button>
        </div>
        <div class="absolute bottom-1.5 left-2 right-2 text-white">
          <p class="text-[11px] font-semibold truncate">{{ modelValue.name }}</p>
          <p class="text-[10px] opacity-75">{{ (modelValue.size / 1024 / 1024).toFixed(2) }} MB</p>
        </div>
      </div>
    </div>
  </div>
  <!-- #endregion -->
</template>

<script setup>
const props = defineProps({
  modelValue: { type: File, default: null },
  label:       { type: String,  default: 'Upload file' },
  icon:        { type: String,  default: '' },
  hint:        { type: String,  default: '' },
  accept:      { type: String,  default: 'image/*' },
  accentColor: { type: String,  default: 'primary' }, // primary | rose | indigo | emerald
  compact:     { type: Boolean, default: false },
  optional:    { type: Boolean, default: false }
})
const emit = defineEmits(['update:modelValue'])

const inputRef = ref(null)
const dragging = ref(false)
const dragCount = ref(0)
const previewUrl = ref('')

const isImage = computed(() => props.modelValue?.type?.startsWith('image/'))
const isVideo = computed(() => props.modelValue?.type?.startsWith('video/'))

// Map accent → text class (Tailwind không support dynamic class names hoàn toàn nên hardcode)
const accentTextClass = computed(() => ({
  primary:  'text-primary',
  rose:     'text-rose-500',
  indigo:   'text-indigo-600',
  emerald:  'text-emerald-600'
})[props.accentColor] || 'text-primary')

const ringBgClass = computed(() => ({
  primary:  'bg-blue-50/80',
  rose:     'bg-rose-50/80',
  indigo:   'bg-indigo-50/80',
  emerald:  'bg-emerald-50/80'
})[props.accentColor] || 'bg-blue-50/80')

// Object URL lifecycle
watch(() => props.modelValue, (f, oldF) => {
  if (previewUrl.value) {
    URL.revokeObjectURL(previewUrl.value)
    previewUrl.value = ''
  }
  if (f instanceof File) {
    previewUrl.value = URL.createObjectURL(f)
  }
}, { immediate: true })

onBeforeUnmount(() => {
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
})

function setFile(f) {
  if (!f) return
  // accept attribute đã filter ở browser file dialog;
  // drag-drop có thể bypass nhưng cha sẽ validate thêm nếu cần.
  emit('update:modelValue', f)
}

function onDrop(e) {
  dragging.value = false
  dragCount.value = 0
  const f = e.dataTransfer?.files?.[0]
  if (f) setFile(f)
}

function onPick(e) {
  const f = e.target.files?.[0]
  if (f) setFile(f)
  if (inputRef.value) inputRef.value.value = ''
}

function clear() {
  emit('update:modelValue', null)
  if (inputRef.value) inputRef.value.value = ''
}
</script>
