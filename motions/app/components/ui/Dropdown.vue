<template>
  <!-- #region ALD 20/05/2026 - Wrapper button; popover Teleport to body để escape mọi stacking context -->
  <div ref="wrapRef" :class="cn('relative', fullWidth && 'w-full')">
    <button
      ref="buttonRef"
      type="button"
      :disabled="disabled"
      :class="buttonClasses"
      @click="toggle"
    >
      <i v-if="icon" :class="['bi text-sm flex-shrink-0', icon]" />
      <span :class="cn('truncate', fullWidth ? 'flex-1 text-left' : 'max-w-32')">
        {{ active ? displayLabel : placeholder }}
      </span>
      <i
        v-if="active && clearable && !noClear"
        class="bi bi-x text-base flex-shrink-0 -mr-1 hover:text-rose-500"
        role="button"
        @click.stop="clear"
      />
      <i
        v-else
        :class="['bi text-xs flex-shrink-0 text-gray-400', open ? 'bi-chevron-up' : 'bi-chevron-down']"
      />
    </button>

    <Teleport to="body">
      <Transition
        enter-active-class="transition duration-150"
        leave-active-class="transition duration-100"
        enter-from-class="opacity-0 -translate-y-1"
        leave-to-class="opacity-0"
      >
        <div
          v-if="open && coords"
          ref="popoverRef"
          :style="popoverStyle"
          class="fixed z-[1000] glass-pop rounded-2xl shadow-dropdown overflow-hidden"
        >
        <!-- #region ALD 20/05/2026 - Search box, hiện khi options.length >= 5 hoặc forceSearch -->
        <div
          v-if="showSearch"
          class="border-b border-gray-100 bg-gray-50/60 px-2 py-1.5"
        >
          <div class="flex items-center gap-1.5 px-2 py-1 bg-gray-50 border border-gray-200 rounded-md focus-within:border-primary">
            <i class="bi bi-search text-xs text-gray-400" />
            <input
              ref="searchRef"
              v-model="query"
              type="text"
              placeholder="Tìm kiếm…"
              class="flex-1 min-w-0 bg-transparent outline-none text-xs text-gray-700 placeholder:text-gray-400"
            />
            <button
              v-if="query"
              type="button"
              class="text-gray-400 hover:text-gray-600"
              @click="query = ''"
            >
              <i class="bi bi-x text-sm" />
            </button>
          </div>
        </div>
        <!-- #endregion -->

        <div class="max-h-72 overflow-y-auto py-1">
          <!-- #region ALD 20/05/2026 - "Tất cả" sentinel ở đầu (single mode, không có query) -->
          <button
            v-if="!multiple && !query && !noClear"
            type="button"
            :class="cn(
              'w-full text-left px-3 py-1.5 text-xs hover:bg-gray-50 flex items-center gap-2 transition-colors',
              !modelValue ? 'text-primary font-semibold' : 'text-gray-500'
            )"
            @click="selectAll"
          >
            <i v-if="!modelValue" class="bi bi-check2 text-primary text-xs flex-shrink-0" />
            <span v-else class="w-4 flex-shrink-0" />
            <span class="truncate">Tất cả</span>
          </button>
          <!-- #endregion -->

          <div v-if="!filtered.length" class="px-3 py-3 text-xs text-gray-400 italic text-center">
            Không có kết quả
          </div>

          <template v-for="(opt, i) in filtered" :key="opt.header ? `hdr-${i}` : opt.value">
          <!-- ALD 02/07/2026 - dòng nhóm {header:'…'}: label section không click được (vd Base 16fps / Native 30fps) -->
          <div
            v-if="opt.header"
            class="px-3 pt-2 pb-1 text-[0.65rem] font-bold uppercase tracking-wider text-gray-400 select-none"
          >
            {{ opt.header }}
          </div>
          <button
            v-else
            type="button"
            :title="opt.label"
            :class="cn(
              'w-full text-left px-3 py-1.5 text-xs hover:bg-gray-50 flex items-center gap-2 transition-colors',
              isSelected(opt.value)
                ? 'text-primary font-semibold bg-blue-50/60'
                : 'text-gray-700'
            )"
            @click="onPick(opt.value)"
          >
            <!-- #region ALD 20/05/2026 - Checkbox cho multi mode / check icon cho single mode -->
            <span
              v-if="multiple"
              :class="cn(
                'w-3.5 h-3.5 rounded border flex items-center justify-center flex-shrink-0',
                isSelected(opt.value)
                  ? 'border-primary bg-primary text-white'
                  : 'border-gray-300 bg-gray-50'
              )"
            >
              <i v-if="isSelected(opt.value)" class="bi bi-check text-[0.55rem] leading-none" />
            </span>
            <i
              v-else-if="isSelected(opt.value)"
              class="bi bi-check2 text-primary text-xs flex-shrink-0"
            />
            <span v-else class="w-4 flex-shrink-0" />
            <!-- #endregion -->
            <!-- ALD 02/07/2026 - hiện thêm opt.hint mờ cạnh label (phân biệt option trùng label, vd 2 preset "10s") -->
            <span class="truncate flex-1">{{ opt.label }}<span v-if="opt.hint" class="ml-1.5 text-[0.65rem] text-gray-400 font-normal">{{ opt.hint }}</span></span>
          </button>
          </template>
        </div>

        <!-- #region ALD 20/05/2026 - Footer multi-mode: select all / clear -->
        <div
          v-if="multiple && filtered.length"
          class="flex items-center justify-between gap-2 px-3 py-1.5 border-t border-gray-100 bg-gray-50/60"
        >
          <button
            type="button"
            class="text-xs font-bold text-gray-600 hover:text-primary transition-colors"
            @click="selectAllVisible"
          >
            <i class="bi bi-check2-all mr-1" />
            {{ query.trim() ? 'Chọn các kết quả' : 'Chọn tất cả' }}
          </button>
          <button
            type="button"
            :disabled="!valueArr.length"
            :class="cn(
              'text-xs font-bold transition-colors',
              valueArr.length === 0
                ? 'text-gray-300 cursor-not-allowed'
                : 'text-rose-600 hover:text-rose-700'
            )"
            @click="clear"
          >
            <i class="bi bi-x-circle mr-1" />
            Xoá ({{ valueArr.length }})
          </button>
        </div>
        <!-- #endregion -->
        </div>
      </Transition>
    </Teleport>
  </div>
  <!-- #endregion -->
</template>

<script setup>
const props = defineProps({
  modelValue: { type: [String, Number, Array], default: '' },
  options: { type: Array, required: true },
  placeholder: { type: String, default: 'Chọn…' },
  multiple: { type: Boolean, default: false },
  clearable: { type: Boolean, default: true },
  disabled: { type: Boolean, default: false },
  icon: { type: String, default: '' },
  align: { type: String, default: 'left' },
  fullWidth: { type: Boolean, default: false },
  forceSearch: { type: Boolean, default: false },
  // noClear=true: ẩn nút "Tất cả" sentinel + ẩn X clear (cho field bắt buộc)
  noClear: { type: Boolean, default: false }
})

const emit = defineEmits(['update:modelValue', 'change'])

const open = ref(false)
const query = ref('')
const wrapRef = ref(null)
const buttonRef = ref(null)
const popoverRef = ref(null)
const searchRef = ref(null)
const coords = ref(null)

// #region ALD 20/05/2026 - Tính position fixed của popover dựa trên bounding rect của button
function updateCoords() {
  if (!buttonRef.value) return
  const rect = buttonRef.value.getBoundingClientRect()
  const vh = window.innerHeight
  const popoverMaxH = 360
  const showAbove = rect.bottom + popoverMaxH > vh - 8 && rect.top > popoverMaxH
  coords.value = {
    left: rect.left,
    width: rect.width,
    top: showAbove ? null : rect.bottom + 8,
    bottom: showAbove ? vh - rect.top + 8 : null
  }
}

const popoverStyle = computed(() => {
  if (!coords.value) return {}
  const s = {
    left: `${coords.value.left}px`,
    width: `${coords.value.width}px`
  }
  if (coords.value.top !== null) s.top = `${coords.value.top}px`
  if (coords.value.bottom !== null) s.bottom = `${coords.value.bottom}px`
  return s
})
// #endregion

// #region ALD 20/05/2026 - Auto-detect khi nào hiện search box (>=5 options hoặc forced)
// ALD 02/07/2026 - chỉ đếm option THẬT (bỏ dòng {header} nhóm) để header không đẩy list ngắn qua ngưỡng search.
const showSearch = computed(() => props.forceSearch || props.options.filter((o) => !o.header).length >= 5)
// #endregion

const valueArr = computed(() =>
  props.multiple ? (Array.isArray(props.modelValue) ? props.modelValue : []) : []
)

const selectedSet = computed(() => new Set(valueArr.value))

const active = computed(() =>
  props.multiple ? valueArr.value.length > 0 : !!props.modelValue
)

// #region ALD 20/05/2026 - Label hiển thị trên button
const displayLabel = computed(() => {
  if (props.multiple) {
    if (valueArr.value.length === 1) {
      const found = props.options.find((o) => o.value === valueArr.value[0])
      return found?.label ?? valueArr.value[0]
    }
    return `${valueArr.value.length} đã chọn`
  }
  const found = props.options.find((o) => o.value === props.modelValue)
  return found?.label ?? props.modelValue ?? props.placeholder
})
// #endregion

const filtered = computed(() => {
  if (!query.value.trim()) return props.options
  const q = query.value.toLowerCase()
  // ALD 02/07/2026 - đang search thì ẨN dòng {header} nhóm (kết quả lọc không còn đúng section nữa).
  return props.options.filter(
    (o) =>
      !o.header &&
      (String(o.label ?? '').toLowerCase().includes(q) ||
        String(o.value ?? '').toLowerCase().includes(q))
  )
})

const buttonClasses = computed(() =>
  cn(
    'press inline-flex items-center gap-2 h-11 px-4 text-sm rounded-2xl transition-spring',
    props.fullWidth && 'w-full justify-between',
    active.value
      ? 'bg-primary text-white font-semibold shadow-pill'
      : 'glass text-gray-700 hover:bg-gray-100 shadow-card font-medium',
    props.disabled && 'opacity-50 cursor-not-allowed'
  )
)

function isSelected(value) {
  return props.multiple ? selectedSet.value.has(value) : props.modelValue === value
}

function toggle() {
  if (props.disabled) return
  open.value = !open.value
  if (open.value) {
    nextTick(() => {
      updateCoords()
      searchRef.value?.focus()
    })
  }
}

function onPick(value) {
  if (props.multiple) {
    const next = new Set(valueArr.value)
    if (next.has(value)) next.delete(value)
    else next.add(value)
    emit('update:modelValue', Array.from(next))
    emit('change', Array.from(next))
  } else {
    emit('update:modelValue', value)
    emit('change', value)
    open.value = false
  }
}

function selectAll() {
  emit('update:modelValue', '')
  emit('change', '')
  open.value = false
}

function selectAllVisible() {
  const merged = new Set(valueArr.value)
  filtered.value.forEach((o) => { if (!o.header) merged.add(o.value) })  // ALD 02/07/2026 - bỏ dòng {header} nhóm
  emit('update:modelValue', Array.from(merged))
  emit('change', Array.from(merged))
}

function clear() {
  const empty = props.multiple ? [] : ''
  emit('update:modelValue', empty)
  emit('change', empty)
  open.value = false
}

// #region ALD 20/05/2026 - Click ngoài đóng popover; ESC để hủy; reposition khi scroll/resize
function onClickOutside(event) {
  if (!open.value) return
  // Click trong button wrapper hoặc trong popover (đã teleport ra body) → bỏ qua
  if (wrapRef.value?.contains(event.target)) return
  if (popoverRef.value?.contains(event.target)) return
  open.value = false
}
function onKeydown(event) {
  if (event.key === 'Escape') open.value = false
}
function onReposition() {
  if (open.value) updateCoords()
}
onMounted(() => {
  document.addEventListener('mousedown', onClickOutside)
  document.addEventListener('keydown', onKeydown)
  window.addEventListener('resize', onReposition)
  window.addEventListener('scroll', onReposition, true)
})
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onClickOutside)
  document.removeEventListener('keydown', onKeydown)
  window.removeEventListener('resize', onReposition)
  window.removeEventListener('scroll', onReposition, true)
})
// #endregion

watch(open, (isOpen) => {
  if (!isOpen) query.value = ''
})
</script>
