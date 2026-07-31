// #region ALD 23/05/2026 - Composable marketing-jobs (video quảng cáo TTS + lip-sync).
// Endpoints:
//   POST   /marketing-jobs        multipart { product_image, model_image, script_image|script_text,
//                                                          category, voice_preference, duration_target_sec, resolution }
//   GET    /marketing-jobs        list paginated
//   GET    /marketing-jobs/:id    single + 4 signed URLs (product/model/script/output)
//   DELETE /marketing-jobs/:id    cancel
export function useMarketingJobs() {
  const auth = useAuth()
  const base = '/marketing-jobs'

  /**
   * Tạo job marketing.
   * @param {object} opts
   * @param {File[]} opts.productImages  - 1-5 ảnh sản phẩm (carousel). Backward compat productImage = wrap [single]
   * @param {File[]} opts.modelImages    - 1-3 ảnh người mẫu (first dùng cho lip-sync)
   * @param {File}   [opts.scriptImage]  - ảnh chứa kịch bản (OCR)
   * @param {string} [opts.scriptText]   - kịch bản plain text
   * @param {string} opts.category       - 7 enums
   * @param {string} opts.voicePreference - 'auto'|'male'|'female'
   * @param {number} opts.durationSec    - default 15
   * @param {string} opts.resolution     - default '720x1280'
   * @param {object} opts.params
   */
  async function create({
    productImage, modelImage,                     // backward compat (single)
    productImages, modelImages,                   // Phase D (arrays)
    scriptImage, scriptText,
    category = 'other', voicePreference = 'auto',
    durationSec = 15, resolution = '720x1280', params = {}
  } = {}) {
    // Normalize: prefer arrays, fallback single
    const products = Array.isArray(productImages) && productImages.length
      ? productImages
      : (productImage ? [productImage] : [])
    const models = Array.isArray(modelImages) && modelImages.length
      ? modelImages
      : (modelImage ? [modelImage] : [])

    if (products.length === 0) throw new Error('Cần ít nhất 1 ảnh sản phẩm')
    if (models.length === 0)   throw new Error('Cần ít nhất 1 ảnh người mẫu')
    if (!scriptImage && !scriptText) throw new Error('Cần scriptImage hoặc scriptText')

    const fd = new FormData()
    for (const f of products) fd.append('product_images', f)
    for (const f of models)   fd.append('model_images', f)
    if (scriptImage)     fd.append('script_image', scriptImage)
    if (scriptText)      fd.append('script_text', scriptText)
    fd.append('category', category)
    fd.append('voice_preference', voicePreference)
    fd.append('duration_target_sec', String(durationSec))
    fd.append('resolution', resolution)
    if (params && Object.keys(params).length) {
      fd.append('params', JSON.stringify(params))
    }
    return await auth.beFetch(base, { method: 'POST', body: fd })
  }

  async function get(id) {
    return await auth.beFetch(`${base}/${encodeURIComponent(id)}`)
  }

  async function cancel(id) {
    return await auth.beFetch(`${base}/${encodeURIComponent(id)}`, { method: 'DELETE' })
  }

  async function list({ page = 1, limit = 20, status = '', category = '', userId = '' } = {}) {
    const p = new URLSearchParams()
    p.set('page', String(page))
    p.set('limit', String(limit))
    if (status)   p.set('status', status)
    if (category) p.set('category', category)
    if (userId)   p.set('userId', userId)
    return await auth.beFetch(`${base}?${p.toString()}`)
  }

  function poll(id, { onUpdate, onDone, intervalMs = 2000 } = {}) {
    let stopped = false
    let timer = null
    async function tick() {
      if (stopped) return
      try {
        const snap = await get(id)
        if (stopped) return
        onUpdate?.(snap)
        if (snap?.status && snap.status !== 'queued' && snap.status !== 'running') {
          stopped = true
          onDone?.(snap)
          return
        }
      } catch (err) {
        if (stopped) return
        console.warn('[marketing-jobs] poll error:', err)
      }
      timer = setTimeout(tick, intervalMs)
    }
    tick()
    return {
      stop: () => {
        stopped = true
        if (timer) { clearTimeout(timer); timer = null }
      }
    }
  }

  // Category catalog với icon Bootstrap + label tiếng Việt
  const CATEGORIES = [
    { id: 'steel_building',   label: 'Nhà thép',           icon: 'bi-building',       hint: 'Pre-engineered building, nhà xưởng' },
    { id: 'steel_products',   label: 'Sản phẩm thép',      icon: 'bi-bricks',         hint: 'Tôn, dầm, cột, thép cuộn' },
    { id: 'steel_structure',  label: 'Kết cấu thép',       icon: 'bi-diagram-3',      hint: 'Kết cấu thép, mặt bằng kỹ thuật' },
    { id: 'real_estate',      label: 'Bất động sản',       icon: 'bi-houses',         hint: 'Nhà, đất, dự án' },
    { id: 'banking_sales',    label: 'Sales ngân hàng',    icon: 'bi-bank',           hint: 'Dịch vụ tài chính, vay vốn, thẻ' },
    { id: 'clothing',         label: 'Quần áo / Thời trang', icon: 'bi-bag-heart',    hint: 'Fashion, trang phục' },
    { id: 'other',            label: 'Khác',               icon: 'bi-three-dots',     hint: 'Lĩnh vực chưa liệt kê' }
  ]

  const VOICES = [
    { id: 'female', label: 'Nữ — HoaiMy',  icon: 'bi-gender-female',
      hint: 'Microsoft vi-VN-HoaiMyNeural · trẻ trung, tự nhiên · dùng cho thời trang/BĐS/sales' },
    { id: 'male',   label: 'Nam — NamMinh', icon: 'bi-gender-male',
      hint: 'Microsoft vi-VN-NamMinhNeural · trầm ấm, chuyên nghiệp · dùng cho thép/kết cấu/ngân hàng' },
    { id: 'auto',   label: 'Tự động',      icon: 'bi-stars',
      hint: 'Worker chọn giọng nữ mặc định. Nếu bật gender classifier sẽ phân tích ảnh người mẫu (Phase B+).' }
  ]

  // Rate variations để có nhiều giọng từ 2 base voice
  const VOICE_RATES = [
    { id: '-15%', label: 'Chậm',          hint: 'Phù hợp narrator BĐS, kể chuyện' },
    { id: '-5%',  label: 'Hơi chậm',      hint: 'Đọc rõ ràng, sales chuyên nghiệp' },
    { id: '+0%',  label: 'Bình thường',   hint: 'Mặc định, cân bằng' },
    { id: '+10%', label: 'Hơi nhanh',     hint: 'Năng động, marketing TikTok' },
    { id: '+20%', label: 'Nhanh',         hint: 'Reels ngắn, 15s pacing gấp' }
  ]

  const DURATIONS = [
    { id: 15, label: '15 giây', hint: 'Reels / TikTok ngắn' },
    { id: 30, label: '30 giây', hint: 'Quảng cáo chuẩn' },
    { id: 60, label: '60 giây', hint: 'Story telling dài' }
  ]

  const RESOLUTIONS = [
    { id: '720x1280', label: 'Dọc 720×1280', icon: 'bi-phone',           hint: 'Reels / TikTok / Story' },
    { id: '1280x720', label: 'Ngang 1280×720', icon: 'bi-aspect-ratio',  hint: 'YouTube / Facebook feed' }
  ]

  return { create, get, cancel, list, poll, CATEGORIES, VOICES, VOICE_RATES, DURATIONS, RESOLUTIONS }
}
// #endregion
