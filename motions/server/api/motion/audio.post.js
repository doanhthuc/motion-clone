// #region ALD 31/05/2026 - Proxy upload audio (multipart) → motion-backend POST /audio.
export default defineEventHandler(async (event) => {
  const { motionApiUrl, motionApiKey } = useRuntimeConfig(event)
  const parts = await readMultipartFormData(event)
  if (!parts?.length) throw createError({ statusCode: 400, statusMessage: 'Thiếu file' })

  const fd = new FormData()
  for (const part of parts) {
    if (part.name === 'file') {
      fd.append('file', new Blob([part.data], { type: part.type || 'audio/mpeg' }), part.filename || 'audio.mp3')
    } else if (part.data) {
      fd.append(part.name, part.data.toString('utf8'))
    }
  }
  try {
    return await $fetch(`${motionApiUrl.replace(/\/$/, '')}/audio`, {
      method: 'POST', body: fd, headers: { 'X-API-Key': motionApiKey }
    })
  } catch (err) {
    throw createError({ statusCode: err?.response?.status || 502, statusMessage: err?.data?.error || 'Upload audio lỗi' })
  }
})
// #endregion
