// #region ALD 23/05/2026 - Storage composable: gộp 2 bucket chat-attachments + motion-jobs.
// Wrapper /functions/v1/storage-files:
//   - listFiles({page, limit, q, ext, sortBy, sortDir, userId, bucket})
//                                    bucket='chat-attachments'|'motion-jobs'|'' (cả 2)
//   - getStats({all, bucket})        admin set all=true → toàn cục
//   - listUsers()                    admin: aggregate per-user (folder view) across all buckets
//   - getSignedUrl(path, {bucket})   preview TTL 1h, mặc định bucket='chat-attachments'
//   - deleteFiles(items)             items: [{path, bucket}] (hỗn hợp) hoặc { paths, bucket }
//   - bulkDelete({olderThanDays, userId})  quét cả 2 bucket
export function useStorageFiles() {
  const auth = useAuth()
  const base = '/storage-files'

  async function listFiles({
    page = 1, limit = 20, q = '', ext = '', mime = '',
    sortBy = 'createdAt', sortDir = 'desc', userId = '', bucket = ''
  } = {}) {
    const params = new URLSearchParams()
    params.set('page', String(page))
    params.set('limit', String(limit))
    if (q) params.set('q', q)
    if (ext) params.set('ext', ext)
    if (mime) params.set('mime', mime)
    if (sortBy) params.set('sortBy', sortBy)
    if (sortDir) params.set('sortDir', sortDir)
    if (userId) params.set('userId', userId)
    if (bucket) params.set('bucket', bucket)
    return await auth.beFetch(`${base}?${params.toString()}`)
  }

  async function getStats({ all = false, bucket = '' } = {}) {
    const params = new URLSearchParams()
    if (all) params.set('all', '1')
    if (bucket) params.set('bucket', bucket)
    const qs = params.toString()
    return await auth.beFetch(`${base}/stats${qs ? '?' + qs : ''}`)
  }

  async function listUsers() {
    return await auth.beFetch(`${base}/users`)
  }

  async function getSignedUrl(path, { bucket = 'chat-attachments' } = {}) {
    const enc = encodeURIComponent(path)
    const qs = bucket ? `?bucket=${encodeURIComponent(bucket)}` : ''
    return await auth.beFetch(`${base}/file/${enc}/signed-url${qs}`)
  }

  /**
   * Xoá file. Hỗ trợ 2 dạng input:
   *   deleteFiles(['a/b', 'c/d'])                       → mặc định bucket='chat-attachments'
   *   deleteFiles(['a/b'], { bucket: 'motion-jobs' })   → all paths cùng 1 bucket
   *   deleteFiles([{path:'x', bucket:'motion-jobs'}, {path:'y', bucket:'chat-attachments'}])
   *                                                     → hỗn hợp nhiều bucket
   */
  async function deleteFiles(input, { bucket = 'chat-attachments' } = {}) {
    let body
    if (Array.isArray(input) && input.length && typeof input[0] === 'object' && input[0].path) {
      body = { items: input }
    } else {
      body = { paths: input, bucket }
    }
    return await auth.beFetch(base, { method: 'DELETE', body })
  }

  async function bulkDelete({ olderThanDays, userId } = {}) {
    return await auth.beFetch(`${base}/bulk-delete`, {
      method: 'POST',
      body: { olderThanDays, userId }
    })
  }

  // ALD 27/05/2026 - Upload file lên bucket cho FE static input. Service role (BE)
  // upload nên không vướng RLS App JWT. Trả { path, bucket, signedUrl }. Dùng để
  // workflow Inspector chuyển source='static' base64 sang lưu URL (DB nhẹ, persist).
  async function uploadFile(file, { bucket = 'chat-attachments', prefix = 'wf-upload' } = {}) {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('bucket', bucket)
    fd.append('prefix', prefix)
    return await auth.beFetch(`${base}/upload`, { method: 'POST', body: fd })
  }

  return { listFiles, getStats, listUsers, getSignedUrl, deleteFiles, bulkDelete, uploadFile }
}
// #endregion
