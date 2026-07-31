<template>
  <!-- #region ALD 05/07/2026 - Dropdown tự vẽ (KHÔNG dùng <select> gốc). Dùng chung cho
       "Chế độ hiển thị" (TikTokFields), "Workflow" (slot editor) và filter Storage.
       ALD 06/07/2026 - Liquid Glass light restyle (đồng bộ design system). -->
  <div ref="wrapRef" class="dd-wrap relative">
    <button
      type="button" class="dd-trigger" :class="{ 'is-open': open }" :disabled="disabled"
      @click="open = !open"
    >
      <span :class="selected ? 'text-[color:var(--ink)]' : 'text-[color:var(--ink-3)]'" class="truncate">{{ selected ? selected.label : placeholder }}</span>
      <i class="bi bi-chevron-down dd-chevron flex-shrink-0" :class="{ 'is-open': open }" />
    </button>
    <Transition enter-active-class="transition duration-120" leave-active-class="transition duration-100" enter-from-class="opacity-0 -translate-y-1" leave-to-class="opacity-0 -translate-y-1">
      <div v-if="open" class="dd-panel">
        <p v-if="!options.length" class="px-3 py-2 text-[11px] text-[color:var(--ink-3)]">Không có lựa chọn nào.</p>
        <button
          v-for="opt in options" :key="opt.value" type="button" class="dd-option"
          :class="{ 'is-selected': opt.value === modelValue, 'is-disabled': opt.disabled }"
          :disabled="opt.disabled"
          @click="select(opt)"
        >
          <span class="truncate">{{ opt.label }}</span>
          <i v-if="opt.value === modelValue" class="bi bi-check2 flex-shrink-0" />
        </button>
      </div>
    </Transition>
  </div>
  <!-- #endregion -->
</template>

<script setup>
const props = defineProps({
  modelValue: { type: String, default: '' },
  options: { type: Array, default: () => [] }, // [{ value, label, disabled? }]
  placeholder: { type: String, default: '-- Chọn --' },
  disabled: { type: Boolean, default: false }
})
const emit = defineEmits(['update:modelValue'])

const open = ref(false)
const wrapRef = ref(null)

const selected = computed(() => props.options.find((o) => o.value === props.modelValue) || null)

function select(opt) {
  if (opt.disabled) return
  emit('update:modelValue', opt.value)
  open.value = false
}

function onOutside(e) {
  if (open.value && wrapRef.value && !wrapRef.value.contains(e.target)) open.value = false
}
onMounted(() => document.addEventListener('mousedown', onOutside))
onBeforeUnmount(() => document.removeEventListener('mousedown', onOutside))
</script>

<style scoped>
.dd-trigger {
  width: 100%; height: 36px; border-radius: 10px; padding: 0 10px;
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  background: var(--surface, #fff); border: 1px solid var(--line-2, rgba(0,0,0,.14));
  font-size: 13px; cursor: pointer; text-align: left;
  transition: border-color .15s ease, box-shadow .15s ease;
}
.dd-trigger:hover { background: rgba(255,255,255,0.08); }
.dd-trigger.is-open { border-color: var(--primary); box-shadow: 0 0 0 3.5px rgba(94,106,210,.14); }
.dd-trigger:disabled { opacity: .5; cursor: not-allowed; }
.dd-chevron { font-size: 11px; color: var(--ink-3); transition: transform .15s; }
.dd-chevron.is-open { transform: rotate(180deg); }
.dd-panel {
  position: absolute; z-index: 60; top: calc(100% + 4px); left: 0; right: 0;
  max-height: 220px; overflow-y: auto; padding: 4px;
  background: var(--glass-bg-solid, rgba(255,255,255,.95));
  backdrop-filter: blur(24px) saturate(180%);
  -webkit-backdrop-filter: blur(24px) saturate(180%);
  border: 1px solid var(--line); border-radius: 12px;
  box-shadow: var(--shadow-dropdown, 0 8px 32px rgba(0,0,0,.1));
}
.dd-option {
  width: 100%; display: flex; align-items: center; justify-content: space-between; gap: 8px;
  padding: 7px 9px; border-radius: 8px; border: none; background: transparent;
  color: var(--ink-2); font-size: 13px; cursor: pointer; text-align: left;
}
.dd-option:hover:not(.is-disabled) { background: rgba(0,0,0,.05); color: var(--ink); }
.dd-option.is-selected { color: var(--primary); background: rgba(94,106,210,.08); }
.dd-option.is-disabled { opacity: .35; cursor: not-allowed; }
</style>
