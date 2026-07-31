// #region ALD 06/07/2026 - Theme switcher: 'dark' (Graphite, mặc định) ↔ 'light'.
// Dark là theme gốc bake trong @theme (main.css); light = override qua class
// `theme-light` trên <html> (block html.theme-light trong main.css + editor).
// Persist localStorage, share state qua useState (mọi component cùng 1 nguồn).
import { useStorage } from '@vueuse/core'

export function useTheme() {
  const theme = useStorage('motions_theme', 'dark')

  function apply(val) {
    if (import.meta.client) {
      document.documentElement.classList.toggle('theme-light', val === 'light')
    }
  }

  function toggle() {
    theme.value = theme.value === 'light' ? 'dark' : 'light'
  }

  // Áp ngay khi client mount + mỗi lần đổi
  if (import.meta.client) {
    watch(theme, apply, { immediate: true })
  }

  const isLight = computed(() => theme.value === 'light')

  return { theme, isLight, toggle }
}
// #endregion
