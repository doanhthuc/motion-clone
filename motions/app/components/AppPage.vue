<template>
  <div :class="rootClass">
    <!-- #region ALD 20/05/2026 - Header: title gradient + actions slot + toolbar slot -->
    <header
      class="flex-shrink-0 bg-gray-50 border-b border-gray-200 z-30 shadow-sm overflow-hidden"
    >
      <div class="px-3 py-2 space-y-2 sm:px-6 sm:py-3 sm:space-y-4">
        <div class="flex flex-col md:flex-row md:items-center md:justify-between gap-2 md:gap-4">
          <div class="min-w-0">
            <h1 class="text-2xl font-black tracking-tighter title-gradient">
              {{ title }}
            </h1>
            <div
              v-if="subtitle"
              class="text-[12px] text-gray-500 font-semibold tracking-tight mt-1 max-w-3xl"
            >
              {{ subtitle }}
            </div>
          </div>
          <div
            v-if="$slots.actions"
            class="flex-shrink-0 flex flex-wrap gap-2 justify-start md:justify-end"
          >
            <slot name="actions" />
          </div>
        </div>
        <div
          v-if="$slots.toolbar"
          class="pt-2 sm:pt-4 sm:border-t sm:border-gray-100 min-w-0"
        >
          <slot name="toolbar" />
        </div>
      </div>
    </header>
    <!-- #endregion -->

    <!-- #region ALD 20/05/2026 - Body: chế độ noScroll cho table full-height -->
    <div v-if="noScroll" class="flex flex-1 flex-col min-h-0">
      <slot />
    </div>
    <section v-else :class="sectionClass">
      <slot />
    </section>
    <!-- #endregion -->

    <!-- #region ALD 20/05/2026 - Footer cố định, ẩn được bằng noFooter -->
    <footer
      v-if="!noFooter"
      class="flex-shrink-0 bg-gray-100/95 border-t border-gray-200 px-8 py-2 flex items-center justify-end gap-4"
    >
      <span class="text-[12px] font-semibold tracking-tight text-gray-500">
        © {{ new Date().getFullYear() }} Pebsteel
      </span>
      <div
        class="w-8 h-8 rounded-full bg-ivory border border-gray-100 flex items-center justify-center text-gray-500"
      >
        <i class="bi bi-question-circle text-sm" />
      </div>
    </footer>
    <!-- #endregion -->
  </div>
</template>

<script setup>
const props = defineProps({
  title: { type: String, default: '' },
  subtitle: { type: String, default: '' },
  noScroll: { type: Boolean, default: false },
  noFooter: { type: Boolean, default: false },
  class: { type: [String, Array, Object], default: '' },
  sectionClass: { type: [String, Array, Object], default: '' }
})

const rootClass = computed(() =>
  cn('flex min-h-0 w-full flex-1 flex-col bg-ivory overflow-hidden', props.class)
)

const sectionClass = computed(() =>
  cn('flex flex-1 flex-col min-h-0 overflow-y-auto', props.sectionClass)
)
</script>
