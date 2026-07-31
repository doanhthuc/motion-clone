<!-- ALD 13/06/2026 - Backup & Restore (admin): 1-click tải toàn bộ dữ liệu (Postgres + MinIO) ra MỘT file
     .zip về máy, và khôi phục file .zip đó lên VPS mới. Restore là UPSERT (merge theo khóa chính) — KHÔNG
     xóa dữ liệu đang có. Dùng cho tình huống thuê VPS, hay set up lại từ đầu. -->
<template>
  <div class="space-y-4">
    <!-- Header -->
    <div class="flex items-center gap-3 flex-wrap">
      <span class="inline-flex h-11 w-11 items-center justify-center rounded-2xl bg-gradient-to-br from-primary to-primary-dark text-white shadow-pill flex-shrink-0">
        <i class="bi bi-box-arrow-in-down text-lg" />
      </span>
      <div class="min-w-0">
        <h3 class="text-sm font-bold text-gray-900 uppercase tracking-wide">Sao lưu & khôi phục</h3>
        <p class="text-[0.72rem] text-gray-500 mt-0.5">
          Toàn bộ database (Postgres) và file lưu trữ (MinIO) gói trong 1 file .zip — tải về máy hoặc nạp lên VPS mới.
        </p>
      </div>
    </div>

    <!-- ── Khối BACKUP ─────────────────────────────────────────────────── -->
    <div class="rounded-3xl border border-gray-200/70 bg-gray-50 shadow-card p-5">
      <div class="flex items-start gap-3">
        <span class="inline-flex h-10 w-10 items-center justify-center rounded-2xl bg-emerald-50 text-emerald-600 flex-shrink-0">
          <i class="bi bi-cloud-arrow-down-fill text-lg" />
        </span>
        <div class="min-w-0 flex-1">
          <h4 class="text-sm font-bold text-gray-900">Tải bản backup</h4>
          <p class="text-[0.72rem] text-gray-500 mt-0.5 leading-relaxed">
            Tạo 1 file <code class="font-mono text-gray-700">motion-backup-&lt;ngày-giờ&gt;.zip</code> gồm
            <b>db.json</b> (workflows, users, jobs, model mẫu, audio, API keys…) và toàn bộ file MinIO. Tùy dung
            lượng kho lưu trữ, quá trình có thể mất một lúc — vui lòng giữ tab mở.
          </p>
          <div class="mt-3">
            <UiButton variant="primary" size="md" :loading="downloading" :disabled="downloading || restoring" @click="downloadBackup">
              <i class="bi bi-download" />
              {{ downloading ? 'Đang đóng gói & tải…' : 'Tải bản backup (.zip)' }}
            </UiButton>
          </div>
        </div>
      </div>
    </div>

    <!-- ── Khối RESTORE ────────────────────────────────────────────────── -->
    <div class="rounded-3xl border border-gray-200/70 bg-gray-50 shadow-card p-5">
      <div class="flex items-start gap-3">
        <span class="inline-flex h-10 w-10 items-center justify-center rounded-2xl bg-sky-50 text-sky-600 flex-shrink-0">
          <i class="bi bi-cloud-arrow-up-fill text-lg" />
        </span>
        <div class="min-w-0 flex-1">
          <h4 class="text-sm font-bold text-gray-900">Khôi phục từ file .zip</h4>
          <p class="text-[0.72rem] text-gray-500 mt-0.5 leading-relaxed">
            Nạp lại file backup lên VPS hiện tại. Dữ liệu được <b>gộp (upsert) theo khóa chính</b> — bản ghi
            trùng ID sẽ được cập nhật, bản ghi mới được thêm vào, <b>không xóa</b> dữ liệu đang có.
          </p>

          <!-- Cảnh báo upsert -->
          <div class="mt-3 flex items-start gap-2 rounded-2xl bg-amber-50 border border-amber-200/70 px-3 py-2.5">
            <i class="bi bi-exclamation-triangle-fill text-amber-500 mt-0.5 flex-shrink-0" />
            <p class="text-[0.72rem] text-amber-800 leading-relaxed">
              Khôi phục sẽ <b>ghi đè</b> các bản ghi/ file trùng khóa bằng nội dung trong file backup. Hãy chắc
              bạn đang chọn đúng file. Thao tác này không thể hoàn tác.
            </p>
          </div>

          <!-- Chọn file + nút khôi phục -->
          <div class="mt-3 flex items-center gap-2 flex-wrap">
            <label class="press inline-flex items-center gap-2 h-10 px-4 rounded-full glass shadow-card text-sm font-semibold text-gray-700 hover:bg-gray-100 transition-colors cursor-pointer">
              <i class="bi bi-folder2-open" />
              {{ file ? 'Đổi file' : 'Chọn file .zip' }}
              <input ref="fileInput" type="file" accept=".zip,application/zip" class="hidden" @change="onFile">
            </label>
            <span v-if="file" class="text-xs font-mono text-gray-600 truncate max-w-[260px]" :title="file.name">
              {{ file.name }} · {{ formatBytes(file.size) }}
            </span>
            <UiButton variant="primary" size="md" :loading="restoring" :disabled="!file || restoring || downloading" @click="restoreBackup">
              <i class="bi bi-arrow-counterclockwise" />
              {{ restoring ? 'Đang khôi phục…' : 'Khôi phục' }}
            </UiButton>
          </div>

          <!-- Kết quả restore -->
          <div v-if="result" class="mt-4 rounded-2xl border border-gray-200/70 bg-gray-50 p-3.5">
            <div class="flex items-center gap-2 text-sm font-semibold text-gray-800">
              <i :class="result.errors?.length ? 'bi bi-check-circle text-amber-500' : 'bi bi-check-circle-fill text-emerald-500'" />
              Đã khôi phục
              <span class="ml-auto text-xs font-mono text-gray-500">{{ totalRows }} bản ghi · {{ result.restored?.files ?? 0 }} file</span>
            </div>
            <div class="mt-2 grid grid-cols-2 sm:grid-cols-3 gap-1.5">
              <div v-for="(n, t) in (result.restored?.tables || {})" :key="t" class="flex items-center justify-between gap-2 text-[0.72rem] font-mono bg-gray-50 rounded-lg px-2 py-1.5 border border-gray-200/60">
                <span class="text-gray-500 truncate">{{ t }}</span>
                <b class="text-gray-800">{{ n }}</b>
              </div>
            </div>
            <details v-if="result.errors?.length" class="mt-3">
              <summary class="text-xs font-semibold text-amber-700 cursor-pointer">
                {{ result.errors.length }} lỗi (đã bỏ qua, không làm gián đoạn khôi phục)
              </summary>
              <ul class="mt-1.5 max-h-48 overflow-auto space-y-1">
                <li v-for="(err, i) in result.errors" :key="i" class="text-[0.68rem] font-mono text-gray-600 bg-gray-50 rounded-lg px-2 py-1 border border-gray-200/60">
                  <b class="text-gray-800">{{ err.scope }}</b>: {{ err.error }}
                </li>
              </ul>
            </details>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
const auth = useAuth()
const toast = useToast()
const config = useRuntimeConfig()

const downloading = ref(false)
const restoring = ref(false)
const file = ref(null)
const fileInput = ref(null)
const result = ref(null)

const totalRows = computed(() => {
  const t = result.value?.restored?.tables || {}
  return Object.values(t).reduce((a, b) => a + (Number(b) || 0), 0)
})

function formatBytes(bytes) {
  const n = Number(bytes)
  if (!Number.isFinite(n) || n <= 0) return '0 B'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`
}

function onFile(e) {
  const f = e.target.files?.[0] || null
  file.value = f
  result.value = null
}

// Tải backup: GET /admin/backup → blob → download qua thẻ <a>. Dùng fetch thô (kèm Bearer) vì beFetch
// trả JSON đã parse, không lấy được binary.
async function downloadBackup() {
  if (downloading.value) return
  downloading.value = true
  try {
    const base = (config.public.motionBackendUrl || '').replace(/\/$/, '')
    const res = await fetch(`${base}/admin/backup`, { headers: { ...auth.authHeader() } })
    if (!res.ok) {
      let msg = `Lỗi ${res.status}`
      try { msg = (await res.json())?.error || msg } catch { /* body không phải JSON */ }
      throw new Error(msg)
    }
    // Tên file lấy từ Content-Disposition (BE đặt motion-backup-<ngày-giờ>.zip), fallback nếu không có.
    const cd = res.headers.get('content-disposition') || ''
    const m = cd.match(/filename="?([^"]+)"?/i)
    const filename = m?.[1] || `motion-backup-${new Date().toISOString().slice(0, 10)}.zip`
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
    toast.success('Đã tải bản backup về máy')
  } catch (e) {
    toast.error(e?.message || 'Tải backup thất bại')
  } finally {
    downloading.value = false
  }
}

// Khôi phục: POST /admin/restore (multipart field "file"). beFetch tự gắn Bearer + trả JSON summary.
async function restoreBackup() {
  if (restoring.value || !file.value) return
  restoring.value = true
  result.value = null
  try {
    const fd = new FormData()
    fd.append('file', file.value)
    const res = await auth.beFetch('/admin/restore', { method: 'POST', body: fd })
    result.value = res
    const files = res?.restored?.files ?? 0
    if (res?.errors?.length) {
      toast.warning(`Khôi phục xong với ${res.errors.length} lỗi — ${totalRows.value} bản ghi, ${files} file (xem chi tiết bên dưới)`)
    } else {
      toast.success(`Khôi phục xong — ${totalRows.value} bản ghi, ${files} file`)
    }
    // Cho phép chọn lại cùng file lần nữa nếu cần.
    if (fileInput.value) fileInput.value.value = ''
    file.value = null
  } catch (e) {
    toast.error(e?.data?.error || e?.message || 'Khôi phục thất bại')
  } finally {
    restoring.value = false
  }
}
</script>
