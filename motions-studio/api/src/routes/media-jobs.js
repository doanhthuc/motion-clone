// #region ALD 31/05/2026 - Media jobs session-authed cho marketing + stream media.
// Dùng chung bảng jobs. Output/input trả signed URL (qua API nếu PUBLIC_BASE_URL).
//   POST   /marketing-jobs       multipart {product_image,model_image,script_image|script_text, ...params}
//   GET    /<base>?page&limit&status  · GET /<base>/:id (+signed URLs) · DELETE /<base>/:id
import { Router } from "express"
import multer from "multer"
import crypto from "node:crypto"
import { query } from "../db.js"
import { putObject, presignGet, getObjectStream, getObjectRange, browserUrl, pipeObjectStream } from "../storage.js"
import { sessionAuth, extractSession } from "../auth/session.js"

const upload = multer({ storage: multer.memoryStorage(), limits: { fileSize: 200 * 1024 * 1024, files: 20 } })
const router = Router()
const extOf = (n, fb) => { const i = (n || "").lastIndexOf("."); return i >= 0 ? n.slice(i + 1).toLowerCase() : fb }

async function signed(key) {
  return await browserUrl(key)  // /media/<key>?sig (HTTPS, browser-loadable) hoặc presigned MinIO
}
async function rowToDto(r, withUrls = false) {
  const dto = {
    id: r.id, type: r.type, status: r.status, progress: Number(r.progress), current_step: r.current_step,
    inputs: r.inputs, params: r.params, output_key: r.output_key, error: r.error,
    created_at: r.created_at, updated_at: r.updated_at, started_at: r.started_at, finished_at: r.finished_at,
  }
  if (withUrls) {
    dto.output_url = await signed(r.output_key)
    const urls = {}
    for (const [field, v] of Object.entries(r.inputs || {})) urls[field] = Array.isArray(v) ? await Promise.all(v.map(signed)) : await signed(v)
    dto.input_urls = urls
    // marketing FE đọc product/model/script/output signed riêng
    dto.product_url = urls.product || null; dto.model_url = urls.model || null; dto.script_url = urls.script || null
  }
  return dto
}

// Tạo job từ multipart (mapField: tên field FE → tên handle lưu)
function makeCreate(type, mapField) {
  return async (req, res) => {
    try {
      let params = {}
      if (req.body.params) { try { params = JSON.parse(req.body.params) } catch { return res.status(400).json({ error: "params không phải JSON" }) } }
      // gom scalar fields (preset/garmentType/script_text/category/...) vào params
      for (const [k, v] of Object.entries(req.body)) if (k !== "params" && typeof v === "string") params[k] = v
      const jobId = crypto.randomUUID()
      const byField = {}
      for (const f of (req.files || [])) (byField[f.fieldname] ||= []).push(f)
      const inputs = {}
      for (const [field, files] of Object.entries(byField)) {
        const handle = mapField[field] || field
        const keys = []
        for (let i = 0; i < files.length; i++) {
          const f = files[i]
          const key = `${jobId}/${handle}${files.length > 1 ? "_" + i : ""}.${extOf(f.originalname, "bin")}`
          await putObject(key, f.buffer, f.mimetype || "application/octet-stream")
          keys.push(key)
        }
        inputs[handle] = keys.length > 1 ? keys : keys[0]
      }
      const { rows } = await query(
        `INSERT INTO jobs (id, type, status, inputs, params, client_id) VALUES ($1,$2,'queued',$3,$4,$5) RETURNING *`,
        [jobId, type, JSON.stringify(inputs), JSON.stringify(params), req.session.userId])
      res.status(202).json(await rowToDto(rows[0]))
    } catch (e) { res.status(500).json({ error: String(e?.message || e) }) }
  }
}

function mountType(base, type, mapField) {
  router.post(base, sessionAuth, upload.any(), makeCreate(type, mapField))
  router.get(base, sessionAuth, async (req, res) => {
    const limit = Math.min(100, Math.max(1, Number(req.query.limit || 20)))
    const page = Math.max(1, Number(req.query.page || 1))
    const where = ["type = $1", "client_id = $2"], args = [type, req.session.userId]
    if (req.query.status) { args.push(req.query.status); where.push(`status = $${args.length}`) }
    args.push(limit, (page - 1) * limit)
    const { rows } = await query(
      `SELECT * FROM jobs WHERE ${where.join(" AND ")} ORDER BY created_at DESC LIMIT $${args.length - 1} OFFSET $${args.length}`, args)
    res.json({ items: await Promise.all(rows.map((r) => rowToDto(r))), page, limit })
  })
  router.get(`${base}/:id`, sessionAuth, async (req, res) => {
    const { rows } = await query("SELECT * FROM jobs WHERE id=$1 AND type=$2", [req.params.id, type])
    if (!rows[0]) return res.status(404).json({ error: "Không tìm thấy" })
    res.json(await rowToDto(rows[0], true))
  })
  router.delete(`${base}/:id`, sessionAuth, async (req, res) => {
    const { rows } = await query(
      `UPDATE jobs SET status='cancelled', finished_at=now() WHERE id=$1 AND type=$2 AND status IN ('queued','running') RETURNING id`,
      [req.params.id, type])
    res.json({ success: !!rows[0], status: "cancelled" })
  })
}

mountType("/marketing-jobs", "teaser", { product_image: "product", model_image: "model", script_image: "script" })

// Stream input/output media qua API (1 domain, kèm session). key chứa '/'.
router.get(/^\/media-file\/(.+)/, async (req, res) => {
  const session = await extractSession(req)
  if (!session) return res.status(401).json({ error: "Unauthorized" })
  try {
    res.set("accept-ranges", "bytes")
    const range = req.headers.range
    if (range) {
      const { stream, contentType, length, contentRange } = await getObjectRange(req.params[0], range)
      if (contentType) res.set("content-type", contentType)
      if (contentRange) res.set("content-range", contentRange)
      if (length != null) res.set("content-length", String(length))
      res.status(206)
      pipeObjectStream(stream, res)
    } else {
      const { stream, contentType, length } = await getObjectStream(req.params[0])
      if (contentType) res.set("content-type", contentType)
      if (length != null) res.set("content-length", String(length))
      pipeObjectStream(stream, res)
    }
  } catch (e) { res.status(404).json({ error: String(e?.message || e) }) }
})

export default router
// #endregion
