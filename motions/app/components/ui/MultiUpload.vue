<template>
  <!-- #region ALD 23/05/2026 - Reusable multi-file dropzone (1-N files).
       v-model: File[] array. Add via click + drag-drop, remove per-item.
       Main slot: dropzone CTA. Below: thumbnail row of selected files. -->
  <div class="flex flex-col gap-1 flex-1 min-h-0">
    <label class="text-[11px] font-bold text-gray-700 flex items-center justify-between gap-1 flex-shrink-0">
      <span class="flex items-center gap-1">
        <i v-if="icon" :class="['bi', icon, accentTextClass]" />
        {{ label }}
      </span>
      <span class="text-[9px] font-semibold text-gray-400 normal-case tracking-normal">
        {{ modelValue.length }}/{{ maxFiles }}
      </span>
    </label>

    <div
      :class="cn(
        'relative border-2 border-dashed rounded-2xl overflow-hidden transition-spring group flex-1 min-h-[100px]',
        dragging
          ? `border-${accentColor}-500 ${ringBgClass} ring-4 ring-${accentColor}-500/15 scale-[1.005]`
          : modelValue.length > 0 ? 'border-primary/40' : 'border-gray-200 hover:border-primary/40 bg-white/30 hover:bg-white/60'
      )"
      @click="modelValue.length < maxFiles && inputRef?.click()"
      @dragenter.prevent="dragCount++; dragging = true"
      @dragover.prevent
      @dragleave.prevent="dragCount--; dragCount <= 0 && (dragging = false, dragCount = 0)"
      @drop.prevent="onDrop"
    >
      <input
        ref="inputRef"
        type="file"
        :accept="accept"
        multiple
        class="hidden"
        @change="onPick"
      />

      <!-- Empty: full-size CTA -->
      <div v-if="modelValue.length === 0" class="absolute inset-0 flex flex-col items-center justify-center text-center px-3 cursor-pointer pointer-events-none">
        <i :class="[
          'bi text-2xl transition-spring',
          dragging ? 'bi-cloud-arrow-down-fill ' + accentTextClass : (icon || 'bi-images') + ' text-gray-300 group-hover:scale-110'
        ]" />
        <p :class="['text-[11px] font-semibold mt-1', dragging ? accentTextClass : 'text-gray-600']">
          {{ dragging ? 'Thả để upload' : `Click hoặc kéo 1-${maxFiles} file vào` }}
        </p>
        <p v-if="!dragging && hint" class="text-[10px] text-gray-400 mt-0.5 line-clamp-1">{{ hint }}</p>
      </div>

      <!-- Has files: grid thumbnails -->
      <div v-else class="absolute inset-0 p-2 grid gap-1.5 overflow-y-auto"
           :class="modelValue.length >= 3 ? 'grid-cols-3 grid-rows-[1fr_1fr]' : modelValue.length === 2 ? 'grid-cols-2' : 'grid-cols-1'">
        <div v-for="(f, idx) in modelValue" :key="idx"
             class="relative rounded-xl overflow-hidden bg-black/5 group/thumb min-h-[60px]">
          <img v-if="f.type.startsWith('image/')" :src="previewUrls[idx]" class="w-full h-full object-cover" :alt="`item-${idx}`" />
          <video v-else-if="f.type.startsWith('video/')" :src="previewUrls[idx]" class="w-full h-full object-cover" muted />
          <div v-else class="w-full h-full flex items-center justify-center">
            <i class="bi bi-file-earmark text-2xl text-gray-400" />
          </div>
          <div class="absolute inset-0 bg-gradient-to-t from-black/60 to-transparent opacity-0 group-hover/thumb:opacity-100 transition-opacity" />
          <button
            type="button"
            class="absolute top-1 right-1 h-5 w-5 flex items-center justify-center rounded-full bg-black/40 hover:bg-rose-500 text-white backdrop-blur transition-colors press"
            title="Xoá"
            @click.stop.prevent="removeAt(idx)"
          >
            <i class="bi bi-x text-[10px]" />
          </button>
          <span class="absolute bottom-0.5 left-1 text-[8px] text-white font-bold px-1 bg-black/50 rounded">{{ idx + 1 }}</span>
        </div>
        <!-- Add more slot nếu chưa max -->
        <button
          v-if="modelValue.length < maxFiles"
          type="button"
          :class="cn(
            'relative rounded-xl border-2 border-dashed flex flex-col items-center justify-center gap-0.5 text-gray-400 hover:text-primary hover:border-primary/40 transition-colors press min-h-[60px]',
            'border-gray-200 bg-white/[0.03]'
          )"
          @click.stop.prevent="inputRef?.click()"
        >
          <i class="bi bi-plus-lg text-base" />
          <span class="text-[9px] font-bold">Thêm</span>
        </button>
      </div>
    </div>
  </div>
  <!-- #endregion -->
</template>

<script setup>
const props = defineProps({
  modelValue: { type: Array, default: () => [] }, // File[]
  label:       { type: String,  default: 'Upload files' },
  icon:        { type: String,  default: '' },
  hint:        { type: String,  default: '' },
  accept:      { type: String,  default: 'image/*' },
  accentColor: { type: String,  default: 'primary' },
  maxFiles:    { type: Number,  default: 5 }
})
const emit = defineEmits(['update:modelValue'])

const inputRef = ref(null)
const dragging = ref(false)
const dragCount = ref(0)
const previewUrls = ref([])

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

watch(() => props.modelValue, (files) => {
  // Revoke old URLs
  for (const u of previewUrls.value) URL.revokeObjectURL(u)
  previewUrls.value = files.map((f) => f instanceof File ? URL.createObjectURL(f) : '')
}, { immediate: true })

onBeforeUnmount(() => {
  for (const u of previewUrls.value) URL.revokeObjectURL(u)
})

function appendFiles(newFiles) {
  if (!newFiles?.length) return
  const merged = [...props.modelValue, ...newFiles]
  // Clamp to maxFiles
  emit('update:modelValue', merged.slice(0, props.maxFiles))
}

function onDrop(e) {
  dragging.value = false
  dragCount.value = 0
  const files = Array.from(e.dataTransfer?.files || [])
  appendFiles(files)
}

function onPick(e) {
  const files = Array.from(e.target.files || [])
  appendFiles(files)
  if (inputRef.value) inputRef.value.value = ''
}

function removeAt(idx) {
  const next = [...props.modelValue]
  next.splice(idx, 1)
  emit('update:modelValue', next)
}
</script>
