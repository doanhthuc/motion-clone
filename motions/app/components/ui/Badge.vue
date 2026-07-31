<template>
  <span :class="classes">
    <slot name="dot">
      <span v-if="dot" :class="['rounded-full', dotClass]" />
    </slot>
    <slot />
  </span>
</template>

<script setup>
const props = defineProps({
  variant: { type: String, default: 'neutral' },
  size: { type: String, default: 'md' },
  dot: { type: Boolean, default: false },
  class: { type: [String, Array, Object], default: '' }
})

// #region ALD 20/05/2026 - Variant pill (đồng bộ với hrm-hub user table)
const VARIANTS = {
  neutral: 'bg-gray-100 text-gray-600 border border-gray-200',
  primary: 'bg-blue-50 text-primary border border-blue-100',
  success: 'bg-emerald-50 text-emerald-700 border border-emerald-100',
  warning: 'bg-amber-50 text-amber-700 border border-amber-100',
  danger: 'bg-rose-50 text-rose-700 border border-rose-100',
  info: 'bg-cyan-50 text-cyan-700 border border-cyan-100'
}
const DOT_COLORS = {
  neutral: 'bg-gray-400',
  primary: 'bg-primary',
  success: 'bg-emerald-500',
  warning: 'bg-amber-500',
  danger: 'bg-rose-500',
  info: 'bg-cyan-500'
}
const SIZES = {
  sm: 'text-[0.65rem] px-1.5 py-0.5 gap-1',
  md: 'text-xs px-2 py-0.5 gap-1.5',
  lg: 'text-sm px-2 py-1 gap-1.5'
}
// #endregion

const classes = computed(() =>
  cn(
    'inline-flex items-center font-bold uppercase tracking-wider rounded',
    VARIANTS[props.variant] ?? VARIANTS.neutral,
    SIZES[props.size] ?? SIZES.md,
    props.class
  )
)

const dotClass = computed(() => {
  const sizeMap = { sm: 'w-1 h-1', md: 'w-1.5 h-1.5', lg: 'w-2 h-2' }
  return cn(sizeMap[props.size], DOT_COLORS[props.variant] ?? DOT_COLORS.neutral)
})
</script>
