<template>
  <!-- #region ALD 20/05/2026 - Modal confirm: backdrop blur + dialog Apple-style -->
  <Teleport to="body">
    <Transition
      enter-active-class="transition duration-200"
      leave-active-class="transition duration-150"
      enter-from-class="opacity-0"
      leave-to-class="opacity-0"
    >
      <div
        v-if="confirm.state.value.open"
        class="fixed inset-0 z-[1500] bg-black/30 backdrop-blur-sm flex items-end sm:items-center justify-center p-4"
        @click.self="state.loading ? null : onCancel()"
      >
        <Transition
          appear
          enter-active-class="transition duration-300"
          enter-from-class="opacity-0 translate-y-4 sm:translate-y-0 sm:scale-95"
        >
          <div class="glass-pop rounded-3xl shadow-modal w-full max-w-sm overflow-hidden">
            <div class="p-6">
              <div class="flex items-start gap-3">
                <span :class="cn(
                  'inline-flex h-11 w-11 items-center justify-center rounded-2xl flex-shrink-0',
                  state.variant === 'danger' ? 'bg-rose-100 text-rose-600' : 'bg-blue-100 text-primary'
                )">
                  <i :class="['bi text-xl', state.variant === 'danger' ? 'bi-exclamation-triangle-fill' : 'bi-question-circle-fill']" />
                </span>
                <div class="flex-1 min-w-0">
                  <h3 class="text-base font-bold text-gray-900 tracking-tight">
                    {{ state.title }}
                  </h3>
                  <p v-if="state.message" class="text-sm text-gray-600 mt-1.5 leading-relaxed">
                    {{ state.message }}
                  </p>
                  <p v-if="state.error" class="text-sm text-rose-600 mt-2 leading-relaxed">
                    {{ state.error }}
                  </p>
                </div>
              </div>
            </div>

            <div class="flex items-center gap-2 p-3 border-t border-gray-100/60 bg-white/[0.03]">
              <button
                ref="cancelBtnRef"
                type="button"
                class="press flex-1 h-11 rounded-full bg-gray-50 shadow-card text-sm font-semibold text-gray-700 hover:bg-gray-50 transition-colors"
                :disabled="state.loading"
                :class="state.loading && 'opacity-60 cursor-not-allowed'"
                @click="onCancel"
              >
                {{ state.cancelText }}
              </button>
              <button
                type="button"
                :class="cn(
                  'press flex-1 h-11 rounded-full text-sm font-semibold shadow-pill transition-colors inline-flex items-center justify-center gap-2',
                  state.variant === 'danger'
                    ? 'bg-rose-500 text-white hover:bg-rose-600'
                    : 'bg-primary text-white hover:bg-primary-dark',
                  state.loading && 'opacity-80 cursor-wait'
                )"
                :disabled="state.loading"
                @click="onAccept"
              >
                <i v-if="state.loading" class="bi bi-arrow-repeat animate-spin" />
                <span>{{ state.loading ? (state.loadingText || 'Đang xử lý...') : state.confirmText }}</span>
              </button>
            </div>
          </div>
        </Transition>
      </div>
    </Transition>
  </Teleport>
  <!-- #endregion -->
</template>

<script setup>
const confirm = useConfirm()
const state = computed(() => confirm.state.value)
const cancelBtnRef = ref(null)

function onAccept() { confirm.accept() }
function onCancel() { confirm.reject() }

// #region ALD 20/05/2026 - ESC = cancel, focus default cancel button khi open
function onKeydown(e) {
  if (!state.value.open) return
  if (state.value.loading) return
  if (e.key === 'Escape') onCancel()
  if (e.key === 'Enter') onAccept()
}
onMounted(() => document.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => document.removeEventListener('keydown', onKeydown))

watch(() => state.value.open, async (open) => {
  if (open) {
    await nextTick()
    cancelBtnRef.value?.focus()
  }
})
// #endregion
</script>
