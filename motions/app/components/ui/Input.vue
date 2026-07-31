<template>
  <div :class="wrapperClasses">
    <i
      v-if="icon"
      :class="['bi absolute left-3 text-gray-400 text-sm pointer-events-none', icon]"
    />
    <input
      :type="type"
      :value="modelValue"
      :placeholder="placeholder"
      :disabled="disabled"
      :class="inputClasses"
      @input="$emit('update:modelValue', $event.target.value)"
    />
  </div>
</template>

<script setup>
const props = defineProps({
  modelValue: { type: [String, Number], default: '' },
  type: { type: String, default: 'text' },
  placeholder: { type: String, default: '' },
  disabled: { type: Boolean, default: false },
  icon: { type: String, default: '' },
  class: { type: [String, Array, Object], default: '' }
})

defineEmits(['update:modelValue'])

const wrapperClasses = computed(() => cn('relative flex items-center', props.class))

const inputClasses = computed(() =>
  cn(
    'w-full h-11 rounded-2xl glass text-sm text-gray-800 placeholder:text-gray-400',
    'transition-spring focus:outline-none focus:ring-4 focus:ring-primary/15 focus:bg-gray-50',
    'disabled:opacity-60 disabled:cursor-not-allowed',
    props.icon ? 'pl-10 pr-3' : 'px-4'
  )
)
</script>
