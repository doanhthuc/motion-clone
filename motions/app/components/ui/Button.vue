<template>
  <button :type="type" :disabled="disabled || loading" :class="classes">
    <svg
      v-if="loading"
      class="animate-spin h-3.5 w-3.5 flex-shrink-0"
      viewBox="0 0 24 24"
      fill="none"
    >
      <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
      <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
    </svg>
    <slot />
  </button>
</template>

<script setup>
const props = defineProps({
  variant: { type: String, default: 'primary' },
  size: { type: String, default: 'md' },
  loading: { type: Boolean, default: false },
  icon: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  type: { type: String, default: 'button' },
  class: { type: [String, Array, Object], default: '' }
})

// #region ALD 20/05/2026 - Apple Dynamic Island style: rounded-full, press feedback, shadow-pill
const VARIANTS = {
  primary: 'bg-primary hover:bg-primary-dark text-white shadow-pill',
  secondary: 'glass text-gray-700 hover:bg-gray-100 shadow-card',
  danger: 'bg-rose-500 hover:bg-rose-600 text-white shadow-pill',
  ghost: 'bg-transparent hover:bg-white/70 text-gray-600',
  outline: 'bg-transparent hover:bg-primary-light text-primary border border-primary/40'
}

const SIZES = {
  sm: 'h-8 px-3 text-xs gap-1.5 rounded-full',
  md: 'h-9 px-4 text-sm gap-1.5 rounded-full',
  lg: 'h-11 px-5 text-sm gap-2 rounded-full'
}
// #endregion

const classes = computed(() =>
  cn(
    'press inline-flex items-center justify-center font-semibold transition-spring',
    'focus-visible:ring-2 focus-visible:ring-primary/40 focus-visible:outline-none',
    'disabled:opacity-50 disabled:pointer-events-none whitespace-nowrap flex-shrink-0',
    VARIANTS[props.variant] ?? VARIANTS.primary,
    SIZES[props.size] ?? SIZES.md,
    props.icon && 'aspect-square px-0',
    props.class
  )
)
</script>
