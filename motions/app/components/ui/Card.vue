<template>
  <div :class="classes">
    <slot />
  </div>
</template>

<script setup>
const props = defineProps({
  class: { type: [String, Array, Object], default: '' },
  padded: { type: Boolean, default: true },
  hoverable: { type: Boolean, default: false }
})

const classes = computed(() =>
  cn(
    'glass rounded-3xl shadow-island',
    props.padded && 'p-5',
    props.hoverable && 'transition-spring hover:shadow-card-hover hover:bg-gray-100',
    props.class
  )
)
</script>
