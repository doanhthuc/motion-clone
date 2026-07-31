<template>
  <NuxtRouteAnnouncer />
  <NuxtLoadingIndicator color="#2563eb" />
  <NuxtLayout>
    <NuxtPage />
  </NuxtLayout>

  <!-- #region ALD 23/05/2026 - Singleton overlays: toast + confirm modal -->
  <UiToast />
  <UiConfirm />
  <!-- #endregion -->
</template>
