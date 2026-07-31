// #region ALD 21/05/2026 - GPU status poller (chandra-vllm running ↔ ollama chat available).
// Khi chandra chiếm VRAM, nemotron3:33b CPU offload → chat chậm/treo.
// FE poll endpoint /functions/v1/gpu-status để biết khi nào enable chat input.
//
// Surface:
//   - status:        reactive state { chat_ready, running, healthy, eta_stop_sec, ... }
//   - chatReady:     computed boolean — true nếu chandra-vllm KHÔNG running
//   - start()/stop():control polling lifecycle
//
// Auto polling: 5s/lần khi chandra running, 30s/lần khi idle (đỡ tốn request).
export function useGpuStatus() {
  const config = useRuntimeConfig()
  const url = `${config.public.motionBackendUrl}/functions/v1/gpu-status`

  const status = useState('gpu.status', () => ({
    chat_ready: true,    // default optimistic (đỡ block input nếu poll chưa kịp)
    running: false,
    healthy: false,
    eta_stop_sec: null,
    active_jobs: 0,
    marker_error: null
  }))
  const polling = useState('gpu.polling', () => false)
  // FE-side suppression: khi user vừa submit OCR, marker chưa kịp register job →
  // status poll trả active_jobs=0 (stale) → banner "OCR vừa xong" hiện sai.
  // Set flag 30s để mọi consumer (banner) bỏ qua state cũ trong giai đoạn race.
  const recentOcrSubmitMs = useState('gpu.recentOcrSubmitMs', () => 0)
  let timer = null

  const chatReady = computed(() => status.value.chat_ready !== false)
  // ETA hiển thị "Đang giải phóng GPU... (Xs)" cho user thấy progress.
  // Null trong 30s sau khi submit OCR — tránh hiện banner khi job mới chưa register.
  const etaStopSec = computed(() => {
    if (Date.now() - recentOcrSubmitMs.value < 30000) return null
    return status.value.eta_stop_sec
  })

  async function fetchOnce() {
    try {
      // KHÔNG kèm auth — endpoint public, đỡ overhead JWT
      const res = await fetch(url)
      if (!res.ok) return
      const data = await res.json()
      status.value = { ...status.value, ...data }
    } catch (err) {
      // Network blip — giữ state cũ, đợi tick sau
      console.warn('[gpu-status] poll failed:', err)
    }
  }

  function scheduleNext() {
    if (!polling.value) return
    const delay = status.value.running ? 5000 : 30000
    timer = setTimeout(async () => {
      await fetchOnce()
      scheduleNext()
    }, delay)
  }

  function start() {
    if (polling.value) return
    polling.value = true
    fetchOnce().then(scheduleNext)
  }

  function stop() {
    polling.value = false
    if (timer) { clearTimeout(timer); timer = null }
  }

  /**
   * Force re-fetch ngay (dùng khi user vừa submit OCR → muốn chuyển state ngay).
   */
  function refresh() { return fetchOnce() }

  /**
   * Pre-warm chandra-vllm — gọi khi user attach file PDF/image. Marker bắt đầu start
   * container trong BG. By time user click submit, chandra đã ready → instant OCR.
   * Fire-and-forget, không throw.
   */
  async function warmup() {
    try {
      await fetch(url + '/warmup', { method: 'POST' })
      fetchOnce()  // refresh status sau warmup
    } catch (err) {
      console.warn('[gpu-status] warmup failed:', err)
    }
  }

  /**
   * Đánh dấu vừa submit OCR — suppress banner 30s + force refresh status sau 2s/5s
   * để bắt kịp khi marker register job (edge function call có thể trễ vài giây).
   */
  function markOcrSubmitted() {
    recentOcrSubmitMs.value = Date.now()
    setTimeout(() => fetchOnce(), 2000)
    setTimeout(() => fetchOnce(), 5000)
  }

  return { status, chatReady, etaStopSec, start, stop, refresh, warmup, markOcrSubmitted }
}
// #endregion
