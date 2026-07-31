// #region ALD 23/05/2026 - Composable audio library (upload/list/delete + signed URL preview).
// ALD 31/05/2026 - Chuyển sang motion-backend qua proxy same-origin /api/motion/audio
// (không còn /functions/v1/audio-files của Supabase Storage). Interface giữ nguyên.
//   POST   /api/motion/audio              multipart {file} → { item }
//   GET    /api/motion/audio              query: { page, limit, q, sortBy, sortDir } → { items, total }
//   GET    /api/motion/audio/:id          → { item } (kèm signedUrl preview same-origin)
//   DELETE /api/motion/audio/:id          → { ok }
//   POST   /api/motion/audio/bulk-delete  body { ids: [...] }
//
// FE pattern:
//   const audio = useAudioFiles()
//   await audio.load()              → items.value populated
//   await audio.upload(file)        → tự gọi load() sau
//   const url = await audio.getSignedUrl(id) → stream preview
export function useAudioFiles() {
  const items = useState('audioFiles.items', () => [])
  const total = useState('audioFiles.total', () => 0)
  const loading = useState('audioFiles.loading', () => false)
  const uploading = useState('audioFiles.uploading', () => false)
  const base = '/api/motion/audio'

  const ALLOWED_MIMES = ['audio/mpeg', 'audio/wav', 'audio/x-wav', 'audio/mp4', 'audio/x-m4a', 'audio/ogg', 'audio/webm', 'audio/flac']
  const ALLOWED_EXTS = ['mp3', 'wav', 'm4a', 'ogg', 'webm', 'flac']
  const MAX_SIZE_BYTES = 50 * 1024 * 1024  // 50MB — đủ cho 5-10 phút audio chất lượng cao

  function isValidAudio(file) {
    if (!file) return false
    if (file.size > MAX_SIZE_BYTES) return false
    const ext = (file.name.split('.').pop() || '').toLowerCase()
    return ALLOWED_MIMES.includes(file.type) || ALLOWED_EXTS.includes(ext)
  }

  async function load({ page = 1, limit = 50, q = '', sortBy = 'createdAt', sortDir = 'desc' } = {}) {
    loading.value = true
    try {
      const params = new URLSearchParams()
      params.set('page', String(page))
      params.set('limit', String(limit))
      if (q) params.set('q', q)
      params.set('sortBy', sortBy)
      params.set('sortDir', sortDir)
      const res = await $fetch(`${base}?${params.toString()}`)
      items.value = res?.items ?? []
      total.value = res?.total ?? items.value.length
    } finally {
      loading.value = false
    }
  }

  async function upload(file, { onProgress } = {}) {
    if (!isValidAudio(file)) {
      const err = new Error(`File không hợp lệ. Chỉ chấp nhận ${ALLOWED_EXTS.join('/')} ≤ ${MAX_SIZE_BYTES / 1024 / 1024}MB.`)
      err.code = 'INVALID_FILE'
      throw err
    }
    uploading.value = true
    try {
      const fd = new FormData()
      fd.append('file', file)
      // BE đọc duration via ffprobe sau upload. FE gửi tối đa metadata để index nhanh:
      fd.append('client_name', file.name)
      fd.append('client_mime', file.type || '')
      fd.append('client_size', String(file.size))

      const res = await $fetch(base, {
        method: 'POST',
        body: fd
        // Note: $fetch không support onProgress native. Nếu cần progress thật thì
        // dùng XMLHttpRequest. Hiện FE chỉ show spinner — đủ cho file ≤50MB.
      })
      await load()
      return res?.item ?? res
    } finally {
      uploading.value = false
    }
  }

  async function remove(id) {
    await $fetch(`${base}/${encodeURIComponent(id)}`, { method: 'DELETE' })
    items.value = items.value.filter((x) => x.id !== id)
    total.value = Math.max(0, total.value - 1)
  }

  async function bulkDelete(ids) {
    if (!ids?.length) return
    await $fetch(`${base}/bulk-delete`, {
      method: 'POST',
      body: { ids },
      headers: { 'Content-Type': 'application/json' }
    })
    const set = new Set(ids)
    items.value = items.value.filter((x) => !set.has(x.id))
    total.value = Math.max(0, total.value - ids.length)
  }

  async function getSignedUrl(id) {
    const res = await $fetch(`${base}/${encodeURIComponent(id)}`)
    return res?.item?.signedUrl || res?.signedUrl || null
  }

  function formatDuration(seconds) {
    if (!seconds || !isFinite(seconds)) return '--:--'
    const m = Math.floor(seconds / 60)
    const s = Math.floor(seconds % 60)
    return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
  }

  function formatSize(bytes) {
    if (!bytes) return '0 B'
    const u = ['B', 'KB', 'MB', 'GB']
    let i = 0
    let v = bytes
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i++ }
    return `${v.toFixed(v >= 100 ? 0 : 1)} ${u[i]}`
  }

  return {
    items, total, loading, uploading,
    ALLOWED_MIMES, ALLOWED_EXTS, MAX_SIZE_BYTES,
    isValidAudio,
    load, upload, remove, bulkDelete, getSignedUrl,
    formatDuration, formatSize
  }
}
// #endregion
