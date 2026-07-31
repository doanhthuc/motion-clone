<template>
  <!-- #region ALD 20/05/2026 - Slide-in panel bên phải; mobile full screen, desktop max-w -->
  <Transition
    enter-active-class="transition-opacity duration-200"
    leave-active-class="transition-opacity duration-200"
    enter-from-class="opacity-0"
    leave-to-class="opacity-0"
  >
    <div
      v-if="modelValue"
      class="absolute inset-0 z-40 bg-black/30 backdrop-blur-sm"
      @click="closeIfBackdrop"
    />
  </Transition>
  <Transition
    enter-active-class="transition-transform duration-200"
    leave-active-class="transition-transform duration-200"
    enter-from-class="translate-x-full"
    leave-to-class="translate-x-full"
  >
    <aside
      v-if="modelValue"
      class="absolute inset-y-0 right-0 z-50 flex flex-col bg-gray-50 border-l border-gray-200 shadow-modal w-full md:w-3/5 lg:w-2/5 xl:max-w-2xl"
    >
      <header class="flex-shrink-0 flex items-center justify-between gap-2 px-4 sm:px-6 h-14 border-b border-gray-100">
        <div class="min-w-0">
          <h3 class="text-sm sm:text-base font-bold text-gray-900 truncate">
            {{ title }}
          </h3>
          <p v-if="subtitle" class="text-xs text-gray-500 truncate">
            {{ subtitle }}
          </p>
        </div>
        <button
          type="button"
          class="h-9 w-9 flex items-center justify-center rounded-lg text-gray-500 hover:bg-gray-100"
          @click="$emit('update:modelValue', false)"
        >
          <i class="bi bi-x-lg" />
        </button>
      </header>

      <div class="flex-1 min-h-0 overflow-y-auto p-4 sm:p-6">
        <slot />
      </div>

      <footer
        v-if="$slots.footer"
        class="flex-shrink-0 flex items-center justify-end gap-2 px-4 sm:px-6 py-3 border-t border-gray-100 bg-gray-50/40"
      >
        <slot name="footer" />
      </footer>
    </aside>
  </Transition>
  <!-- #endregion -->
</template>

<script setup>
const props = defineProps({
  modelValue: { type: Boolean, default: false },
  title: { type: String, default: '' },
  subtitle: { type: String, default: '' },
  dismissOnBackdrop: { type: Boolean, default: true }
})

const emit = defineEmits(['update:modelValue'])

function closeIfBackdrop() {
  if (props.dismissOnBackdrop) emit('update:modelValue', false)
}

// #region ALD 20/05/2026 - ESC để đóng panel
function onKeydown(event) {
  if (event.key === 'Escape' && props.modelValue) emit('update:modelValue', false)
}
onMounted(() => document.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => document.removeEventListener('keydown', onKeydown))
// #endregion
</script>
