<template>
  <!-- #region ALD 24/05/2026 - Storage page full-screen, table scrolls inside (no page scroll).
       ALD 05/07/2026 - Padding đồng bộ với Audio/Social (dark card không dính sát viền layout). -->
  <div class="flex-1 min-h-0 flex flex-col px-3 sm:px-6 pt-2 pb-3">
    <div class="w-full flex-1 min-h-0 flex flex-col">
      <SettingsStorageManager :is-admin="isAdmin" />
    </div>
  </div>
  <!-- #endregion -->
</template>

<script setup>
definePageMeta({ middleware: 'auth' })

useHead({ title: 'Storage — Motions' })

const auth = useAuth()
const isAdmin = computed(() => {
  const token = auth.token.value || ''
  try {
    const payload = JSON.parse(atob(token.split('.')[1] ?? ''))
    return payload?.role === 'admin'
  } catch {
    return false
  }
})
</script>
