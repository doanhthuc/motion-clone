<template>
  <!-- #region ALD 20/05/2026 - 6 ô input riêng biệt, auto-focus next khi gõ -->
  <div class="flex items-center gap-2 justify-center">
    <input
      v-for="(_, i) in length"
      :key="i"
      :ref="(el) => (inputsRef[i] = el)"
      type="text"
      inputmode="numeric"
      maxlength="1"
      :disabled="disabled"
      :value="modelValue[i] || ''"
      :class="cn(
        'w-11 h-13 text-center text-lg font-semibold font-mono rounded-xl border-2 outline-none transition-all',
        'focus:border-primary focus:ring-4 focus:ring-primary/10',
        disabled
          ? 'bg-gray-100 border-gray-200 text-gray-400 cursor-not-allowed'
          : modelValue[i]
          ? 'border-primary/40 bg-blue-50/60 text-gray-900'
          : 'border-gray-200 bg-gray-50 text-gray-900 hover:border-gray-300'
      )"
      :autofocus="i === 0"
      @input="handleInput(i, $event)"
      @keydown="handleKeydown(i, $event)"
      @paste="i === 0 ? handlePaste($event) : null"
      @focus="$event.target.select()"
    />
  </div>
  <!-- #endregion -->
</template>

<script setup>
const props = defineProps({
  modelValue: { type: String, default: '' },
  length: { type: Number, default: 6 },
  disabled: { type: Boolean, default: false }
})
const emit = defineEmits(['update:modelValue', 'complete'])

const inputsRef = ref([])

function focusInput(idx) {
  inputsRef.value[idx]?.focus()
}

// #region ALD 20/05/2026 - Logic gõ ký tự: chỉ số 0-9, tự nhảy ô kế
function handleInput(idx, e) {
  const char = e.target.value.replace(/\D/g, '').slice(-1)
  const next = props.modelValue.split('')
  next[idx] = char
  const newVal = next.join('').slice(0, props.length)
  emit('update:modelValue', newVal)
  if (char && idx < props.length - 1) focusInput(idx + 1)
  if (newVal.length === props.length) emit('complete', newVal)
}
// #endregion

function handleKeydown(idx, e) {
  if (e.key === 'Backspace' && !props.modelValue[idx] && idx > 0) focusInput(idx - 1)
  if (e.key === 'ArrowLeft' && idx > 0) focusInput(idx - 1)
  if (e.key === 'ArrowRight' && idx < props.length - 1) focusInput(idx + 1)
}

// #region ALD 20/05/2026 - Paste cả 6 chữ số 1 lần (vd copy từ email)
function handlePaste(e) {
  e.preventDefault()
  const pasted = e.clipboardData.getData('text').replace(/\D/g, '').slice(0, props.length)
  emit('update:modelValue', pasted)
  focusInput(Math.min(pasted.length, props.length - 1))
  if (pasted.length === props.length) emit('complete', pasted)
}
// #endregion
</script>
