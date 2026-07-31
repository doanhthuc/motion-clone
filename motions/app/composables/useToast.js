// #region ALD 20/05/2026 - Toast notification system (singleton stack, dùng cho mọi feedback ngắn)
// Cách dùng:
//   const toast = useToast()
//   toast.success('Đã lưu')
//   toast.error('Có lỗi xảy ra', { duration: 6000 })
//   toast.info('Thông tin', { title: 'Tiêu đề' })
// Toast container mount 1 lần ở app.vue qua <UiToast />.
export function useToast() {
  const toasts = useState('toast.items', () => [])

  function dismiss(id) {
    toasts.value = toasts.value.filter((t) => t.id !== id)
  }

  function push(variant, message, opts = {}) {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 6)}`
    const item = {
      id,
      variant,        // 'success' | 'error' | 'info' | 'warning'
      message,
      title: opts.title || '',
      duration: opts.duration ?? 4000
    }
    toasts.value = [...toasts.value, item]
    if (item.duration > 0) {
      setTimeout(() => dismiss(id), item.duration)
    }
    return id
  }

  return {
    items: toasts,
    dismiss,
    success: (msg, opts) => push('success', msg, opts),
    error:   (msg, opts) => push('error', msg, opts),
    info:    (msg, opts) => push('info', msg, opts),
    warning: (msg, opts) => push('warning', msg, opts)
  }
}
// #endregion
