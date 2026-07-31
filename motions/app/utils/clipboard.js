// ALD 08/07/2026 - Copy an toàn cho context KHÔNG bảo mật (app mở qua HTTP + IP nội bộ, không phải HTTPS/localhost).
// navigator.clipboard.writeText CHỈ chạy trong secure context → ngoài đó ném "not allowed by the user agent...".
// Fallback: document.execCommand('copy') qua textarea ẩn (chạy được trên HTTP). Ném lỗi nếu CẢ HAI đều fail
// → giữ nguyên try/catch & .then(ok, err) ở mọi call-site cũ (chỉ cần đổi navigator.clipboard.writeText → copyText).
export async function copyText(text) {
  const s = String(text ?? '')
  // 1) Clipboard API (chỉ khi secure context — HTTPS hoặc localhost)
  try {
    if (typeof navigator !== 'undefined' && navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(s)
      return true
    }
  } catch (_) { /* rơi xuống fallback */ }
  // 2) Fallback execCommand (chạy trên HTTP nội bộ)
  try {
    const ta = document.createElement('textarea')
    ta.value = s
    ta.setAttribute('readonly', '')
    ta.style.position = 'fixed'
    ta.style.top = '-9999px'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.focus()
    ta.select()
    ta.setSelectionRange(0, s.length)
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    if (ok) return true
  } catch (_) { /* ném lỗi bên dưới */ }
  throw new Error('Clipboard không khả dụng (thử HTTPS hoặc copy tay)')
}
