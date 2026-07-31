// #region ALD 31/05/2026 - Audio library (thay bucket audio Supabase Storage).
//   POST   /audio                multipart {file} → { item }
//   GET    /audio?page&limit&q&sortBy&sortDir → { items, total }
//   GET    /audio/:id            → { item } (kèm signedUrl preview)
//   GET    /audio/:id/download   → stream (cho proxy FE preview)
//   DELETE /audio/:id            → { ok }
//   POST   /audio/bulk-delete    { ids:[] } → { ok, deleted }
// File lưu MinIO prefix audio/. Duration đọc bằng ffprobe (ffmpeg trong image API).
import { Router } from "express"
import multer from "multer"
import crypto from "node:crypto"
import { spawn } from "node:child_process"
import os from "node:os"
import fs from "node:fs"
import path from "node:path"
import { query } from "../db.js"
import { putObject, getObjectStream, deleteObject, presignGet, browserUrl } from "../storage.js"
import { clientAuth, workerAuth } from "../auth.js"

const upload = multer({ storage: multer.memoryStorage(), limits: { fileSize: 60 * 1024 * 1024, files: 1 } })
const router = Router()
const extOf = (n, fb) => { const i = (n || "").lastIndexOf("."); return i >= 0 ? n.slice(i + 1).toLowerCase() : fb }

// ffprobe duration (giây) — best effort, lỗi thì trả null (không chặn upload).
function probeDuration(buffer, ext) {
  return new Promise((resolve) => {
    let tmp
    try {
      tmp = path.join(os.tmpdir(), `aud-${crypto.randomUUID()}.${ext || "bin"}`)
      fs.writeFileSync(tmp, buffer)
      const ff = spawn("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", tmp])
      let out = ""
      ff.stdout.on("data", (d) => { out += d })
      ff.on("error", () => { cleanup(); resolve(null) })
      ff.on("close", () => { cleanup(); const v = parseFloat(out.trim()); resolve(isFinite(v) ? v : null) })
    } catch { cleanup(); resolve(null) }
    function cleanup() { try { if (tmp) fs.unlinkSync(tmp) } catch { /* noop */ } }
  })
}

function rowToItem(row, signedUrl = null) {
  return {
    id: row.id, name: row.name, storage_key: row.storage_key,
    size_bytes: row.size_bytes != null ? Number(row.size_bytes) : null,
    duration_sec: row.duration_sec != null ? Number(row.duration_sec) : null,
    mime: row.mime, createdAt: row.created_at, created_at: row.created_at,
    ...(signedUrl ? { signedUrl } : {}),
  }
}

router.post("/audio", clientAuth, upload.single("file"), async (req, res) => {
  try {
    if (!req.file) return res.status(400).json({ error: "Thiếu file" })
    const id = crypto.randomUUID()
    const ext = extOf(req.file.originalname, "mp3")
    const key = `audio/${id}.${ext}`
    await putObject(key, req.file.buffer, req.file.mimetype || "audio/mpeg")
    const duration = await probeDuration(req.file.buffer, ext)
    const { rows } = await query(
      `INSERT INTO audio_files (id, name, storage_key, size_bytes, duration_sec, mime, client_id)
       VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING *`,
      [id, req.body.client_name || req.file.originalname || "audio", key, req.file.size, duration,
       req.file.mimetype || req.body.client_mime || null, req.get("x-client-id") || null],
    )
    res.status(201).json({ item: rowToItem(rows[0]) })
  } catch (e) {
    res.status(500).json({ error: String(e?.message || e) })
  }
})

router.get("/audio", clientAuth, async (req, res) => {
  const limit = Math.min(200, Math.max(1, Number(req.query.limit || 50)))
  const page = Math.max(1, Number(req.query.page || 1))
  const offset = (page - 1) * limit
  const dir = String(req.query.sortDir || "desc").toLowerCase() === "asc" ? "ASC" : "DESC"
  const where = [], args = []
  if (req.query.q) { args.push(`%${req.query.q}%`); where.push(`name ILIKE $${args.length}`) }
  const whereSql = where.length ? "WHERE " + where.join(" AND ") : ""
  const totalQ = await query(`SELECT count(*)::int AS n FROM audio_files ${whereSql}`, args)
  args.push(limit); args.push(offset)
  const { rows } = await query(
    `SELECT * FROM audio_files ${whereSql} ORDER BY created_at ${dir} LIMIT $${args.length - 1} OFFSET $${args.length}`,
    args,
  )
  res.json({ items: rows.map((r) => rowToItem(r)), total: totalQ.rows[0].n })
})

router.get("/audio/:id", clientAuth, async (req, res) => {
  const { rows } = await query("SELECT * FROM audio_files WHERE id=$1", [req.params.id])
  if (!rows[0]) return res.status(404).json({ error: "Không tìm thấy" })
  const signedUrl = await browserUrl(rows[0].storage_key)  // /media/<key>?sig (browser <audio>)
  res.json({ item: rowToItem(rows[0], signedUrl) })
})

router.get("/audio/:id/download", clientAuth, async (req, res) => {
  const { rows } = await query("SELECT storage_key, mime FROM audio_files WHERE id=$1", [req.params.id])
  if (!rows[0]) return res.status(404).json({ error: "Không tìm thấy" })
  try {
    const { stream, contentType, length } = await getObjectStream(rows[0].storage_key)
    res.set("content-type", contentType || rows[0].mime || "audio/mpeg")
    if (length) res.set("content-length", String(length))
    stream.pipe(res)
  } catch (e) {
    res.status(404).json({ error: "Không tải được: " + String(e?.message || e) })
  }
})

// GET /audio/:id/key — worker resolve id → storage_key (rồi tải qua /files/<key>) cho audio-replace
router.get("/audio/:id/key", workerAuth, async (req, res) => {
  const { rows } = await query("SELECT storage_key FROM audio_files WHERE id=$1", [req.params.id])
  if (!rows[0]) return res.status(404).json({ error: "Không tìm thấy" })
  res.json({ key: rows[0].storage_key })
})

router.delete("/audio/:id", clientAuth, async (req, res) => {
  const { rows } = await query("DELETE FROM audio_files WHERE id=$1 RETURNING storage_key", [req.params.id])
  if (rows[0]) await deleteObject(rows[0].storage_key)
  res.json({ ok: !!rows[0] })
})

router.post("/audio/bulk-delete", clientAuth, async (req, res) => {
  const ids = Array.isArray(req.body?.ids) ? req.body.ids : []
  if (!ids.length) return res.json({ ok: true, deleted: 0 })
  const { rows } = await query("DELETE FROM audio_files WHERE id = ANY($1::uuid[]) RETURNING storage_key", [ids])
  for (const r of rows) await deleteObject(r.storage_key)
  res.json({ ok: true, deleted: rows.length })
})

export default router
// #endregion
