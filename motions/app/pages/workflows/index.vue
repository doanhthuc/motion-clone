<template>
  <!-- #region ALD 23/05/2026 - Workflows page: list + tạo mới. Click 1 wf → /workflows/[id] (editor)
       ALD 06/07/2026 - Liquid Glass light redesign (đồng bộ toàn app, bỏ dark theme cục bộ). -->
  <div class="flex-1 min-h-0 flex flex-col px-3 sm:px-6 pt-1 pb-3">
    <div class="lq-panel flex-1 min-h-0 overflow-y-auto p-4 sm:p-6">
      <div class="max-w-6xl mx-auto">
        <SettingsWorkflowsTab />
      </div>
    </div>
  </div>
  <!-- #endregion -->
</template>

<script setup>
definePageMeta({ middleware: 'auth' })

useHead({ title: 'Workflows — Motions' })

const route = useRoute()
const wf = useWorkflows()

onMounted(async () => {
  await wf.load()
  // Sidebar "Workflow mới" pass ?new=1 → mở dialog tạo workflow trong WorkflowsTab
  // (WorkflowsTab có showCreate state riêng; mở sẵn nếu có query)
  if (route.query.new) {
    // Để WorkflowsTab tự handle — đơn giản nhất: emit event qua window
    nextTick(() => window.dispatchEvent(new CustomEvent('motions:wf:new')))
  }
})
</script>
