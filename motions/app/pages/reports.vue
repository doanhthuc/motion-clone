<template>
  <!-- #region ALD 31/05/2026 - Reports tách khỏi Settings → trang riêng ở sidebar trái. -->
  <div class="flex-1 min-h-0 flex flex-col px-3 sm:px-6 pt-2 pb-3">
    <div class="w-full flex-1 min-h-0 flex flex-col">
      <SettingsReportsManager />
    </div>
  </div>
  <!-- #endregion -->
</template>

<script setup>
definePageMeta({ middleware: 'auth' })
useHead({ title: 'Reports — Motions' })
</script>
