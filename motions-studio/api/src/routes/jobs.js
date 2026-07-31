import { Router } from "express"
import multer from "multer"
import crypto from "node:crypto"
import { query } from "../db.js"
import { putObject, getObjectStream, getObjectRange, presignGet, browserUrl, verifyMediaSig, pipeObjectStream } from "../storage.js"
import { clientAuth, workerAuth } from "../auth.js"
import { enforceMotionResolution } from "../motion-resolution.js"
import { enforceTaskCloudEnhancePolicy } from "../task-cloud/enhance-policy.js"

const upload = multer({ storage: multer.memoryStorage(), limits: { fileSize: 200 * 1024 * 1024, files: 20 } })
const router = Router()

const extOf = (n, fb) => { const i = (n || "").lastIndexOf("."); return i >= 0 ? n.slice(i + 1).toLowerCase() : fb }

function normalizeMotionDriverSegment(type, params) {
  if (type !== "motion" || !params || typeof params !== "object") return params
  const out = { ...params }
  out.deliveryPreset = "source"
  out.delivery_preset = "source"
  out.fitDriver = false
  out.fit_driver = false
  delete out.bgAnchor
  delete out.bg_anchor
  delete out.bgAnchorMaskExpand
  delete out.bg_anchor_mask_expand
  delete out.bgAnchorMaskBlur
  delete out.bg_anchor_mask_blur
  delete out.motionHandFix
  delete out.motion_hand_fix
  delete out.hand_fix_steps
  delete out.hand_fix_pose_strength
  delete out.hand_fix_clip_strength

  // ALD 13/07/2026 - Natural/HQ đã gỡ khỏi backend. Chuẩn hóa trước mọi early-return để payload
  // gọi thẳng /jobs cũng được lưu đúng cấu hình Fast thực tế, không chỉ được sửa tạm trong worker.
  out.renderProfile = "fast"
  out.render_profile = "fast"
  out.hq = false
  delete out.hq_steps
  out.steps = 4
  out.cfg = 1
  out.scheduler = "dpm++_sde"
  out.loraLightx2v = 1
  out.lora_lightx2v = 1
  // Motion Transfer quay về đường window ban đầu: không Context Options/overlap/blend. Chuẩn hóa ngay lúc
  // nhận job để DB/log phản ánh đúng cấu hình worker thực chạy, kể cả payload cũ còn gửi anchored-context.
  out.windowMode = "autoregressive"
  out.window_mode = "autoregressive"
  out.frame_window_size = 81

  const start = Number(out.driverStartSec ?? out.driver_start_sec ?? 0)
  const dur = Number(out.driverDurSec ?? out.driver_dur_sec ?? 0)
  if (!Number.isFinite(start) || !Number.isFinite(dur) || dur <= 0) return out

  // ALD 28/06/2026 - Legacy multi-outfit bug: some callers stored driverDurSec
  // as endSec (0/5, 5/10, 10/15). Worker expects duration, so node 2 would
  // render 5..15s instead of 5..10s. Normalize the exact 5s slice pattern.
  if (start > 0 && Math.abs(dur - (start + 5)) < 0.001) {
    out.driverDurSec = 5
    out.driver_dur_sec = 5
  }
  const fixedDur = Number(out.driverDurSec ?? out.driver_dur_sec ?? dur)
  if (Math.abs(fixedDur - 5) < 0.001 && start % 5 === 0) {
    out.preset = "5s-720p"
    out.frames = 81
    out.steps = 4
    out.skipFirstFrames = 0
    out.skip_first_frames = 0
  }
  return out
}

async function rowToDto(row, withUrls = false) {
  const dto = {
    id: row.id, type: row.type, status: row.status, progress: Number(row.progress),
    current_step: row.current_step, inputs: row.inputs, params: row.params,
    output_key: row.output_key, error: row.error, worker_id: row.worker_id,
    outputs: Array.isArray(row.outputs) ? row.outputs : [],   // ALD 03/06/2026 - đa preset (3 style teaser)
    created_at: row.created_at, updated_at: row.updated_at,
    started_at: row.started_at, finished_at: row.finished_at,
  }
  // output_url cho browser: /media/<key>?sig (HTTPS qua API, không cần header) khi có PUBLIC_BASE_URL.
  if (withUrls && row.output_key) dto.output_url = await browserUrl(row.output_key)
  if (withUrls && dto.outputs.length)
    dto.outputs = await Promise.all(dto.outputs.map(async (o) => ({ ...o, url: o?.key ? await browserUrl(o.key) : null })))
  return dto
}

// ──────────── CLIENT API (X-API-Key) ────────────

// POST /jobs — multipart: type, params(JSON), + files (fieldname = handle: ref/motion/model/product…)
router.post("/jobs", clientAuth, upload.any(), async (req, res) => {
  try {
    const type = String(req.body.type || "").trim()
    if (!type) return res.status(400).json({ error: "Thiếu 'type'" })
    // #region ALD 13/07/2026 - Gỡ node gộp Try-on + Wan; chỉ giữ Motion Transfer.
    if (type === "fashion-motion") {
      return res.status(410).json({ error: "Job type này đã được gỡ. Hãy dùng Try-on → Motion Transfer." })
    }
    // #endregion
    let params = {}
    if (req.body.params) { try { params = JSON.parse(req.body.params) } catch { return res.status(400).json({ error: "params không phải JSON" }) } }
    params = enforceTaskCloudEnhancePolicy(
      type,
      enforceMotionResolution(type, normalizeMotionDriverSegment(type, params)),
    )
    // #region ALD 17/07/2026 - CHẶN detailUpscale ở API cho job motion: ESRGAN không biết mặt → răng bể pixel.
    // FE đã ẩn toggle nhưng node/client cũ vẫn có thể gửi true → ép false tại đây là chốt chặn cuối.
    // Mở lại khi CodeFormer vào delivery (xem feature/face-restore-motion-delivery.md).
    if (type === "motion" && params && typeof params === "object") {
      params.detailUpscale = false
      params.detail_upscale = false
    }
    // #endregion

    const jobId = crypto.randomUUID()
    // Gom file theo fieldname → 1 file = string key, nhiều file = array keys
    const byField = {}
    for (const f of (req.files || [])) (byField[f.fieldname] ||= []).push(f)
    const inputs = {}
    for (const [field, files] of Object.entries(byField)) {
      const keys = []
      for (let i = 0; i < files.length; i++) {
        const f = files[i]
        const key = `${jobId}/${field}${files.length > 1 ? "_" + i : ""}.${extOf(f.originalname, "bin")}`
        await putObject(key, f.buffer, f.mimetype || "application/octet-stream")
        keys.push(key)
      }
      inputs[field] = keys.length > 1 ? keys : keys[0]
    }

    const { rows } = await query(
      `INSERT INTO jobs (id, type, status, inputs, params, client_id)
       VALUES ($1, $2, 'queued', $3, $4, $5) RETURNING *`,
      [jobId, type, JSON.stringify(inputs), JSON.stringify(params), req.get("x-client-id") || null],
    )
    res.status(202).json(await rowToDto(rows[0]))
  } catch (e) {
    res.status(500).json({ error: String(e?.message || e) })
  }
})

// GET /jobs — list gần đây (filter type/status)
router.get("/jobs", clientAuth, async (req, res) => {
  const limit = Math.min(100, Math.max(1, Number(req.query.limit || 20)))
  const where = [], args = []
  if (req.query.type) { args.push(req.query.type); where.push(`type = $${args.length}`) }
  if (req.query.status) { args.push(req.query.status); where.push(`status = $${args.length}`) }
  args.push(limit)
  const { rows } = await query(
    `SELECT * FROM jobs ${where.length ? "WHERE " + where.join(" AND ") : ""} ORDER BY created_at DESC LIMIT $${args.length}`,
    args,
  )
  res.json({ items: await Promise.all(rows.map((r) => rowToDto(r))) })
})

// GET /jobs/:id — chi tiết + presigned output URL
router.get("/jobs/:id", clientAuth, async (req, res) => {
  const { rows } = await query("SELECT * FROM jobs WHERE id = $1", [req.params.id])
  if (!rows[0]) return res.status(404).json({ error: "Không tìm thấy" })
  res.json(await rowToDto(rows[0], true))
})

// GET /jobs/:id/download — stream output qua API (1 domain HTTPS, khỏi expose MinIO ra ngoài)
router.get("/jobs/:id/download", clientAuth, async (req, res) => {
  const { rows } = await query("SELECT output_key FROM jobs WHERE id = $1", [req.params.id])
  if (!rows[0]?.output_key) return res.status(404).json({ error: "Chưa có output" })
  try {
    res.set("accept-ranges", "bytes")
    res.set("content-disposition", `inline; filename="${req.params.id}.mp4"`)
    const range = req.headers.range
    if (range) {
      const { stream, contentType, length, contentRange } = await getObjectRange(rows[0].output_key, range)
      if (contentType) res.set("content-type", contentType)
      if (contentRange) res.set("content-range", contentRange)
      if (length != null) res.set("content-length", String(length))
      res.status(206)
      pipeObjectStream(stream, res)
    } else {
      const { stream, contentType, length } = await getObjectStream(rows[0].output_key)
      if (contentType) res.set("content-type", contentType)
      if (length != null) res.set("content-length", String(length))
      pipeObjectStream(stream, res)
    }
  } catch (e) {
    res.status(404).json({ error: "Không tải được output: " + String(e?.message || e) })
  }
})

// DELETE /jobs/:id — cancel
router.delete("/jobs/:id", clientAuth, async (req, res) => {
  const { rows } = await query(
    `UPDATE jobs SET status='cancelled', finished_at=now() WHERE id=$1 AND status IN ('queued','running') RETURNING id`,
    [req.params.id],
  )
  res.json({ success: !!rows[0], status: "cancelled" })
})

// ──────────── WORKER API (X-Worker-Token) ────────────

// ALD 05/06/2026 - GET /jobs/:id/cancelled — worker hỏi nhanh job có bị HỦY không (giữa lúc poll ComfyUI)
// → nếu có thì worker /interrupt ComfyUI ngay để nhả GPU. Honor cancel ở tầng worker (xem worker.py).
router.get("/jobs/:id/cancelled", workerAuth, async (req, res) => {
  const { rows } = await query("SELECT status FROM jobs WHERE id=$1", [req.params.id])
  res.json({ cancelled: rows[0]?.status === "cancelled", status: rows[0]?.status || null })
})

// POST /worker/claim — atomic lấy 1 job queued thuộc types[] (SKIP LOCKED chống đua)
router.post("/worker/claim", workerAuth, async (req, res) => {
  const types = Array.isArray(req.body.types) && req.body.types.length ? req.body.types : ["motion"]
  const workerId = String(req.body.worker_id || "worker")
  // #region ALD 04/06/2026 - Reclaim job mồ côi: job 'running' mang worker_id này mà worker KHÔNG còn nhớ
  // (không nằm trong active_job_ids gửi kèm) = tàn dư do worker bị giết giữa chừng (deploy/restart/crash/OOM)
  // → fail ngay, KHÔNG để kẹt 'running' vĩnh viễn. Chủ động fail (không requeue) tránh re-render tốn GPU.
  // ALD 12/06/2026 - worker chạy SONG SONG N job (WORKER_CONCURRENCY) nên claim lúc job khác đang chạy là
  // bình thường → chỉ reclaim job NGOÀI active_job_ids. Worker cũ không gửi field này ([]) = hành vi cũ.
  const actives = (Array.isArray(req.body.active_job_ids) ? req.body.active_job_ids : [])
    .map((x) => String(x)).filter(Boolean)
  const reclaimed = await query(
    `UPDATE jobs SET status='error', error='Worker khởi động lại giữa chừng — vui lòng chạy lại',
            finished_at=now()
     WHERE status='running' AND worker_id=$1 AND NOT (id::text = ANY($2::text[])) RETURNING id`,
    [workerId, actives],
  ).catch(() => ({ rows: [] }))
  if (reclaimed.rows?.length) console.log(`[claim] reclaim ${reclaimed.rows.length} job mồ côi của ${workerId} (running → error)`)
  // #endregion
  const { rows } = await query(
    `UPDATE jobs SET status='running', worker_id=$1, started_at=now()
     WHERE id = (SELECT id FROM jobs WHERE status='queued' AND type = ANY($2::text[])
                 ORDER BY created_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED)
     RETURNING *`,
    [workerId, types],
  )
  if (!rows[0]) return res.status(204).end()
  res.json(await rowToDto(rows[0]))
})

// PATCH /jobs/:id — worker cập nhật tiến trình/status
router.patch("/jobs/:id", workerAuth, async (req, res) => {
  const { status, progress, current_step, error } = req.body
  const sets = [], args = []
  if (status != null) { args.push(status); sets.push(`status = $${args.length}`) }
  if (progress != null) { args.push(Math.max(0, Math.min(1, Number(progress)))); sets.push(`progress = $${args.length}`) }
  if (current_step != null) { args.push(String(current_step).slice(0, 300)); sets.push(`current_step = $${args.length}`) }
  if (error != null) { args.push(String(error).slice(0, 2000)); sets.push(`error = $${args.length}`) }
  if (status === "error" || status === "done" || status === "cancelled") sets.push(`finished_at = now()`)
  if (!sets.length) return res.json({ ok: true })
  args.push(req.params.id)
  await query(`UPDATE jobs SET ${sets.join(", ")} WHERE id = $${args.length}`, args)
  res.json({ ok: true })
})

// POST /jobs/:id/output — worker upload kết quả. ALD 03/06/2026: hỗ trợ ĐA OUTPUT (3 preset teaser):
// body.variant (số) → key output-<variant>; body.label → nhãn style; body.final='false' → CHƯA done
// (append vào outputs[]); final mặc định true → set done (single-output cũ giữ nguyên).
router.post("/jobs/:id/output", workerAuth, upload.single("output"), async (req, res) => {
  if (!req.file) return res.status(400).json({ error: "Thiếu file 'output'" })
  const variant = req.body?.variant
  const label = req.body?.label || null
  const isFinal = String(req.body?.final ?? "true") !== "false"
  const suffix = variant !== undefined && variant !== null && variant !== "" ? `-${variant}` : ""
  const key = `${req.params.id}/output${suffix}.${extOf(req.file.originalname, "mp4")}`
  const mime = req.file.mimetype || "video/mp4"
  await putObject(key, req.file.buffer, mime)
  const sets = ["outputs = COALESCE(outputs, '[]'::jsonb) || $1::jsonb", "output_key = COALESCE(output_key, $2)"]
  const args = [JSON.stringify([{ key, label, mime }]), key]
  if (isFinal) sets.push("status='done'", "progress=1", "current_step='done'", "finished_at=now()")
  args.push(req.params.id)
  const { rows } = await query(
    `UPDATE jobs SET ${sets.join(", ")} WHERE id=$${args.length} RETURNING type, client_id`, args)
  // Lưu vào Storage library (bucket 'outputs'), mỗi style 1 entry (tên kèm nhãn) để user xem/dùng lại.
  try {
    const job = rows[0] || {}
    const lbl = label ? `-${String(label).replace(/[^\p{L}\p{N}]+/gu, "_")}` : ""
    const name = `${job.type || "output"}${lbl}-${req.params.id.slice(0, 8)}.${extOf(req.file.originalname, "mp4")}`
    await query(
      `INSERT INTO storage_files (bucket, path, storage_key, name, size_bytes, mime, user_id)
       VALUES ('outputs', $1, $1, $2, $3, $4, $5) ON CONFLICT (bucket, path) DO NOTHING`,
      [key, name, req.file.size, mime, job.client_id || null],
    )
  } catch (e) { /* không chặn output nếu ghi storage lỗi */ }
  res.json({ ok: true, output_key: key })
})

// ALD 03/06/2026 - POST /jobs/:id/preview — worker đẩy ảnh PREVIEW từng bước (chân dung nhân vật / cảnh
// của story-film) → jobs.previews[]. KHÔNG đụng output_key/status. mediaViaJob poll previews → emit event
// có thumbnail (previewUrl) → FE hiện step-by-step trên canvas/timeline.
router.post("/jobs/:id/preview", workerAuth, upload.single("preview"), async (req, res) => {
  if (!req.file) return res.status(400).json({ error: "Thiếu file 'preview'" })
  const key = `${req.params.id}/preview-${Date.now().toString(36)}${Math.floor(Math.random() * 1e4)}.${extOf(req.file.originalname, "png")}`
  await putObject(key, req.file.buffer, req.file.mimetype || "image/png")
  await query("UPDATE jobs SET previews = COALESCE(previews, '[]'::jsonb) || $1::jsonb WHERE id=$2",
    [JSON.stringify([{ key, label: req.body?.label || "" }]), req.params.id])
  res.json({ ok: true, key })
})

// GET /jobs/:id/logs?since=<ts> — client đọc log (thay SSE log Supabase; FE poll mỗi 2s)
router.get("/jobs/:id/logs", clientAuth, async (req, res) => {
  const { rows } = await query("SELECT logs FROM jobs WHERE id=$1", [req.params.id])
  if (!rows[0]) return res.status(404).json({ error: "Không tìm thấy" })
  let logs = Array.isArray(rows[0].logs) ? rows[0].logs : []
  if (req.query.since) logs = logs.filter((l) => l.ts && l.ts > req.query.since)
  res.json({ logs })
})

// POST /jobs/:id/log — worker append 1 dòng log
router.post("/jobs/:id/log", workerAuth, async (req, res) => {
  const entry = {
    id: crypto.randomUUID(),
    ts: new Date().toISOString(),
    level: String(req.body.level || "info"),
    message: String(req.body.message || "").slice(0, 1000),
    progress: req.body.progress != null ? Math.max(0, Math.min(1, Number(req.body.progress))) : null,
  }
  await query("UPDATE jobs SET logs = logs || $1::jsonb WHERE id=$2", [JSON.stringify([entry]), req.params.id])
  res.json({ ok: true })
})

// ──────────── WORKER STATUS (badge "GPU Ready") ────────────

// POST /worker/heartbeat — worker báo còn sống + GPU info (upsert)
router.post("/worker/heartbeat", workerAuth, async (req, res) => {
  const b = req.body || {}
  await query(
    `INSERT INTO workers (worker_id, last_seen_at, active_job_id, mode, gpu_name, gpu_vram_total_mb)
     VALUES ($1, now(), $2, $3, $4, $5)
     ON CONFLICT (worker_id) DO UPDATE SET
       last_seen_at=now(), active_job_id=$2, mode=$3, gpu_name=$4, gpu_vram_total_mb=$5`,
    [String(b.worker_id || "worker"), b.active_job_id || null, b.mode || null,
     b.gpu_name || null, b.gpu_vram_total_mb != null ? Math.round(Number(b.gpu_vram_total_mb)) : null],
  )
  res.json({ ok: true })
})

// GET /worker-status — FE badge: { healthy, workers[], queued, running, total_workers }
router.get("/worker-status", clientAuth, async (_req, res) => {
  const w = await query(
    `SELECT worker_id, last_seen_at, active_job_id, mode, gpu_name, gpu_vram_total_mb,
            (last_seen_at > now() - interval '60 seconds') AS fresh
     FROM workers ORDER BY last_seen_at DESC`,
  )
  const c = await query(
    `SELECT count(*) FILTER (WHERE status='queued')  AS queued,
            count(*) FILTER (WHERE status='running') AS running FROM jobs`,
  )
  const workers = w.rows.map((r) => ({
    worker_id: r.worker_id, last_seen_at: r.last_seen_at, active_job_id: r.active_job_id,
    mode: r.mode, gpu_name: r.gpu_name, gpu_vram_total_mb: r.gpu_vram_total_mb,
  }))
  res.json({
    healthy: w.rows.some((r) => r.fresh),
    workers,
    queued: Number(c.rows[0].queued), running: Number(c.rows[0].running),
    total_workers: workers.length,
  })
})

// GET /files/<key> — stream object (worker tải input; key có thể chứa '/')
router.get(/^\/files\/(.+)/, workerAuth, async (req, res) => {
  try {
    const key = String(req.params[0] || "").split("?")[0]
    const { stream, contentType, length } = await getObjectStream(key)
    if (contentType) res.set("content-type", contentType)
    if (length) res.set("content-length", String(length))
    pipeObjectStream(stream, res)
  } catch (e) {
    res.status(404).json({ error: "Không tìm thấy file: " + String(e?.message || e) })
  }
})

// ALD 10/07/2026 - POST /worker/upload-temp — worker đẩy file TRUNG GIAN lên storage (vd driver đã transcode
// H.264 cho DashScope: codec AV1/HEVC bị cloud từ chối "InvalidVideo.OpenError"). Body multipart: file + key.
router.post("/worker/upload-temp", workerAuth, upload.single("file"), async (req, res) => {
  try {
    const key = String(req.body?.key || "").trim()
    if (!key || key.includes("..")) return res.status(400).json({ error: "key không hợp lệ" })
    if (!req.file) return res.status(400).json({ error: "thiếu file" })
    await putObject(key, req.file.buffer, req.file.mimetype || "application/octet-stream")
    res.json({ key })
  } catch (e) { res.status(500).json({ error: String(e?.message || e) }) }
})

// ALD 10/07/2026 - GET /worker/public-url?key=<storage_key> — worker xin URL PUBLIC (HMAC /media, không cần
// header) cho provider cloud phải TỰ fetch file (DashScope wan2.7 driving_audio CHỈ nhận URL, không nhận base64).
router.get("/worker/public-url", workerAuth, async (req, res) => {
  try {
    const key = String(req.query.key || "").trim()
    if (!key) return res.status(400).json({ error: "thiếu ?key=" })
    const url = await browserUrl(key)
    if (!url) return res.status(404).json({ error: "không tạo được URL cho key này" })
    res.json({ url })
  } catch (e) { res.status(500).json({ error: String(e?.message || e) }) }
})

// GET /media/<key>?exp&sig — PUBLIC (HMAC-verified), cho browser <img>/<video> tải qua HTTPS.
router.get(/^\/media\/(.+)/, async (req, res) => {
  const key = req.params[0]
  if (!verifyMediaSig(key, req.query.exp, req.query.sig)) return res.status(403).json({ error: "Chữ ký không hợp lệ / hết hạn" })
  try {
    res.set("accept-ranges", "bytes")           // ALD 03/06/2026 - báo client server hỗ trợ Range
    res.set("cache-control", "private, max-age=86400")
    const range = req.headers.range
    if (range) {                                 // <video> seek/Safari: trả 206 Partial Content
      const { stream, contentType, length, contentRange } = await getObjectRange(key, range)
      if (contentType) res.set("content-type", contentType)
      if (contentRange) res.set("content-range", contentRange)
      if (length != null) res.set("content-length", String(length))
      res.status(206)
      pipeObjectStream(stream, res)
    } else {
      const { stream, contentType, length } = await getObjectStream(key)
      if (contentType) res.set("content-type", contentType)
      if (length != null) res.set("content-length", String(length))
      pipeObjectStream(stream, res)
    }
  } catch (e) {
    res.status(404).json({ error: "Không tìm thấy media: " + String(e?.message || e) })
  }
})

export default router
