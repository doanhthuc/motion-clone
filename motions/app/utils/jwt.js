export function decodeJwtPayload(token) {
  const part = String(token || '').split('.')[1]
  if (!part) return null
  try {
    const normalized = part.replace(/-/g, '+').replace(/_/g, '/')
    const padded = normalized.padEnd(normalized.length + ((4 - normalized.length % 4) % 4), '=')
    let raw = ''
    if (typeof globalThis.atob === 'function') {
      raw = globalThis.atob(padded)
    } else if (typeof Buffer !== 'undefined') {
      raw = Buffer.from(padded, 'base64').toString('utf8')
    } else {
      return null
    }
    try {
      const utf8 = decodeURIComponent(Array.from(raw, (c) => `%${c.charCodeAt(0).toString(16).padStart(2, '0')}`).join(''))
      return JSON.parse(utf8)
    } catch {
      return JSON.parse(raw)
    }
  } catch {
    return null
  }
}
