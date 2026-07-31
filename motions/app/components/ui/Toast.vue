<template>
  <!-- #region ALD 20/05/2026 - Toast stack ở góc dưới-phải, Teleport ra body để escape stacking -->
  <Teleport to="body">
    <div class="fixed z-[2000] bottom-4 right-4 left-4 sm:left-auto flex flex-col gap-2 max-w-sm pointer-events-none">
      <TransitionGroup
        enter-active-class="transition duration-300 ease-out"
        leave-active-class="transition duration-200 ease-in"
        enter-from-class="opacity-0 translate-y-2 scale-95"
        leave-to-class="opacity-0 translate-x-4"
        move-class="transition duration-300"
      >
        <div
          v-for="t in toast.items.value"
          :key="t.id"
          :class="cn(
            'pointer-events-auto glass-pop rounded-2xl shadow-island-lg px-4 py-3 flex items-start gap-3',
            'border-l-4',
            BORDER[t.variant]
          )"
        >
          <span :class="cn('inline-flex h-6 w-6 items-center justify-center rounded-full flex-shrink-0 mt-0.5', BG[t.variant])">
            <i :class="['bi text-sm', ICON[t.variant]]" />
          </span>
          <div class="flex-1 min-w-0">
            <div v-if="t.title" class="text-sm font-bold text-gray-900">{{ t.title }}</div>
            <div :class="cn('text-sm leading-relaxed text-gray-700', t.title && 'mt-0.5')">
              {{ t.message }}
            </div>
          </div>
          <button
            type="button"
            class="flex-shrink-0 h-6 w-6 flex items-center justify-center rounded-full text-gray-400 hover:bg-gray-100 hover:text-gray-700 press"
            @click="toast.dismiss(t.id)"
          >
            <i class="bi bi-x text-base" />
          </button>
        </div>
      </TransitionGroup>
    </div>
  </Teleport>
  <!-- #endregion -->
</template>

<script setup>
const toast = useToast()

// #region ALD 20/05/2026 - Variant style maps
const ICON = {
  success: 'bi-check2',
  error: 'bi-exclamation-triangle-fill',
  warning: 'bi-exclamation-circle-fill',
  info: 'bi-info-circle-fill'
}
const BG = {
  success: 'bg-emerald-100 text-emerald-700',
  error: 'bg-rose-100 text-rose-700',
  warning: 'bg-amber-100 text-amber-700',
  info: 'bg-blue-100 text-primary'
}
const BORDER = {
  success: 'border-emerald-500',
  error: 'border-rose-500',
  warning: 'border-amber-500',
  info: 'border-primary'
}
// #endregion
</script>
