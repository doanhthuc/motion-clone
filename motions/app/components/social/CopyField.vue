<template>
  <!-- #region ALD 05/07/2026 - Ô hiển thị 1 giá trị (URL redirect...) kèm nút copy — dùng trong Help drawer
       (dark theme, đồng bộ với Social Management redesign). -->
  <div class="mt-1.5 flex items-center gap-1.5 rounded-lg border border-white/[0.07] bg-white/[0.03] px-2 py-1.5">
    <code class="flex-1 min-w-0 truncate text-[11.5px] font-mono text-gray-700">{{ value }}</code>
    <button type="button" class="flex-shrink-0 text-gray-400 hover:text-primary" @click="copy">
      <i :class="['bi', copied ? 'bi-check2' : 'bi-clipboard']" />
    </button>
  </div>
  <!-- #endregion -->
</template>

<script setup>
const props = defineProps({ value: { type: String, default: '' } })
const toast = useToast()
const copied = ref(false)

function copy() {
  if (!props.value) return
  copyText(props.value).then(
    () => {
      copied.value = true
      toast.success('Đã copy')
      setTimeout(() => { copied.value = false }, 1500)
    },
    () => toast.error('Copy thất bại')
  )
}
</script>
