// #region ALD 20/05/2026 - Modal confirm chung (singleton). Trả Promise<boolean>.
// Cách dùng:
//   const confirm = useConfirm()
//   const ok = await confirm.ask({
//     title: 'Xoá hội thoại?',
//     message: 'Hành động không thể hoàn tác.',
//     confirmText: 'Xoá',
//     variant: 'danger'   // 'primary' | 'danger'
//   })
//   if (ok) { ... }
// Modal mount 1 lần ở app.vue qua <UiConfirm />.
export function useConfirm() {
  const state = useState('confirm.state', () => ({
    open: false,
    title: '',
    message: '',
    confirmText: 'Xác nhận',
    cancelText: 'Huỷ',
    variant: 'primary',  // 'primary' | 'danger'
    loading: false,
    loadingText: '',
    error: '',
    onConfirm: null,
    resolver: null
  }))

  function ask(opts = {}) {
    return new Promise((resolve) => {
      state.value = {
        open: true,
        title: opts.title || 'Xác nhận',
        message: opts.message || '',
        confirmText: opts.confirmText || 'Xác nhận',
        cancelText: opts.cancelText || 'Huỷ',
        variant: opts.variant || 'primary',
        loading: false,
        loadingText: opts.loadingText || '',
        error: '',
        onConfirm: typeof opts.onConfirm === 'function' ? opts.onConfirm : null,
        resolver: resolve
      }
    })
  }

  function _resolve(value) {
    state.value.resolver?.(value)
    state.value = { ...state.value, open: false, resolver: null }
  }

  async function accept() {
    if (state.value.loading) return
    if (!state.value.onConfirm) {
      _resolve(true)
      return
    }
    state.value = { ...state.value, loading: true, error: '' }
    try {
      const result = await state.value.onConfirm()
      _resolve(result ?? true)
    } catch (err) {
      state.value = {
        ...state.value,
        loading: false,
        error: err?.data?.error || err?.message || 'Thao tác thất bại'
      }
    }
  }
  function reject() {
    if (state.value.loading) return
    _resolve(false)
  }

  return { state, ask, accept, reject }
}
// #endregion
