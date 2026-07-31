// #region ALD 31/05/2026 - Storage files (thay /functions/v1/storage-files).
//   GET    /storage-files?page&limit&q&ext&sortBy&sortDir&userId&bucket  → { items, total, page, limit }
//   GET    /storage-files/stats?all&bucket                              → { totalFiles, totalSize, byBucket }
//   GET    /storage-files/users                                         → { users:[{userId,email,count,size}] }
//   GET    /storage-files/file/:path/signed-url?bucket                  → { signedUrl }
//   POST   /storage-files/upload   multipart {file,bucket,prefix}       → { path, bucket, signedUrl }
//   DELETE /storage-files          { paths,bucket } | { items:[{path,bucket}] } → { ok, deleted }
//   POST   /storage-files/bulk-delete { olderThanDays, userId }          → { ok, deleted }
import { Router } from "express"
import multer from "multer"
import crypto from "node:crypto"
import { query } from "../db.js"
import { putObject, getObjectStream, deleteObject, presignGet, browserUrl } from "../storage.js"
import { sessionAuth, requireAdmin } from "../auth/session.js"

const upload = multer({ storage: multer.memoryStorage(), limits: { fileSize: 200 * 1024 * 1024, files: 1 } })
const router = Router()
const extOf = (n, fb) => { const i = (n || "").lastIndexOf("."); return i >= 0 ? n.slice(i + 1).toLowerCase() : fb }
const DEFAULT_BUCKET = "chat-attachments"

function signedFor(row) {
  const base = process.env.PUBLIC_BASE_URL
  return base
    ? `${base.replace(/\/$/, "")}/storage-files/file/${encodeURIComponent(row.path)}/download?bucket=${encodeURIComponent(row.bucket)}`
    : null // presign điền sau (async)
}
function rowToItem(row, signedUrl) {
  return {
    // FE (StorageManager) dùng `name` làm ĐỊNH DANH path (Supabase convention) + hiển thị basename.
    // → trả name = path. filename giữ tên gốc nếu cần.
    id: row.id, bucket: row.bucket, path: row.path, name: row.path, filename: row.name,
    size_bytes: row.size_bytes != null ? Number(row.size_bytes) : null,
    size: row.size_bytes != null ? Number(row.size_bytes) : null,
    mime: row.mime, userId: row.user_id, createdAt: row.created_at, created_at: row.created_at,
    signedUrl: signedUrl ?? null,
  }
}

// Tất cả route storage cần đăng nhập
router.use("/storage-files", sessionAuth)

router.get("/storage-files", async (req, res) => {
  const limit = Math.min(200, Math.max(1, Number(req.query.limit || 20)))
  const page = Math.max(1, Number(req.query.page || 1))
  const dir = String(req.query.sortDir || "desc").toLowerCase() === "asc" ? "ASC" : "DESC"
  const where = [], args = []
  // staff chỉ thấy file của mình; admin thấy hết (hoặc lọc userId)
  if (req.session.role !== "admin") { args.push(req.session.userId); where.push(`user_id = $${args.length}`) }
  else if (req.query.userId) { args.push(req.query.userId); where.push(`user_id = $${args.length}`) }
  if (req.query.bucket) { args.push(req.query.bucket); where.push(`bucket = $${args.length}`) }
  if (req.query.q) { args.push(`%${req.query.q}%`); where.push(`name ILIKE $${args.length}`) }
  if (req.query.ext) { args.push(`%.${String(req.query.ext).replace(/^\./, "")}`); where.push(`path ILIKE $${args.length}`) }
  // ALD 26/06/2026 - mime prefix filter (video → mime ILIKE 'video/%') để total chỉ đếm đúng loại
  if (req.query.mime) { args.push(`${String(req.query.mime).split("/")[0]}/%`); where.push(`mime ILIKE $${args.length}`) }
  const w = where.length ? "WHERE " + where.join(" AND ") : ""
  const tot = await query(`SELECT count(*)::int n FROM storage_files ${w}`, args)
  args.push(limit, (page - 1) * limit)
  const { rows } = await query(
    `SELECT * FROM storage_files ${w} ORDER BY created_at ${dir} LIMIT $${args.length - 1} OFFSET $${args.length}`, args)
  const items = await Promise.all(rows.map(async (r) => rowToItem(r, await browserUrl(r.storage_key))))
  res.json({ items, total: tot.rows[0].n, page, limit })
})

router.get("/storage-files/stats", async (req, res) => {
  const where = [], args = []
  if (!req.query.all || req.session.role !== "admin") { args.push(req.session.userId); where.push(`user_id = $${args.length}`) }
  if (req.query.bucket) { args.push(req.query.bucket); where.push(`bucket = $${args.length}`) }
  const w = where.length ? "WHERE " + where.join(" AND ") : ""
  const { rows } = await query(
    `SELECT bucket, count(*)::int n, coalesce(sum(size_bytes),0)::bigint sz FROM storage_files ${w} GROUP BY bucket`, args)
  const byBucket = {}; let totalFiles = 0, totalSize = 0
  for (const r of rows) { byBucket[r.bucket] = { count: r.n, size: Number(r.sz) }; totalFiles += r.n; totalSize += Number(r.sz) }
  res.json({ totalFiles, totalSize, byBucket })
})

router.get("/storage-files/users", requireAdmin, async (_req, res) => {
  const { rows } = await query(
    `SELECT s.user_id, u.email, count(*)::int n, coalesce(sum(s.size_bytes),0)::bigint sz
     FROM storage_files s LEFT JOIN users u ON u.id = s.user_id GROUP BY s.user_id, u.email ORDER BY sz DESC`)
  res.json({ users: rows.map((r) => ({ userId: r.user_id, email: r.email, count: r.n, size: Number(r.sz) })) })
})

// signed-url: path có thể chứa '/', dùng regex
router.get(/^\/storage-files\/file\/(.+)\/signed-url$/, async (req, res) => {
  const path = decodeURIComponent(req.params[0])
  const bucket = req.query.bucket || DEFAULT_BUCKET
  const { rows } = await query("SELECT * FROM storage_files WHERE bucket=$1 AND path=$2", [bucket, path])
  if (!rows[0]) return res.status(404).json({ error: "Không tìm thấy" })
  res.json({ signedUrl: await browserUrl(rows[0].storage_key) })
})

router.get(/^\/storage-files\/file\/(.+)\/download$/, async (req, res) => {
  const path = decodeURIComponent(req.params[0])
  const bucket = req.query.bucket || DEFAULT_BUCKET
  const { rows } = await query("SELECT storage_key, mime FROM storage_files WHERE bucket=$1 AND path=$2", [bucket, path])
  if (!rows[0]) return res.status(404).json({ error: "Không tìm thấy" })
  try {
    const { stream, contentType, length } = await getObjectStream(rows[0].storage_key)
    if (contentType || rows[0].mime) res.set("content-type", contentType || rows[0].mime)
    if (length) res.set("content-length", String(length))
    stream.pipe(res)
  } catch (e) { res.status(404).json({ error: String(e?.message || e) }) }
})

router.post("/storage-files/upload", upload.single("file"), async (req, res) => {
  try {
    if (!req.file) return res.status(400).json({ error: "Thiếu file" })
    const bucket = req.body.bucket || DEFAULT_BUCKET
    const prefix = (req.body.prefix || "upload").replace(/[^a-zA-Z0-9_-]/g, "")
    const ext = extOf(req.file.originalname, "bin")
    // path = <prefix>/<uuid>/<tên gốc> → basename (FE hiển thị) là tên gốc, vẫn duy nhất nhờ uuid folder.
    const safe = (req.file.originalname || `file.${ext}`).replace(/[^a-zA-Z0-9._-]/g, "_").slice(0, 80)
    const path = `${prefix}/${crypto.randomUUID()}/${safe}`
    const key = `${bucket}/${path}`
    await putObject(key, req.file.buffer, req.file.mimetype || "application/octet-stream")
    const { rows } = await query(
      `INSERT INTO storage_files (bucket, path, storage_key, name, size_bytes, mime, user_id)
       VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING *`,
      [bucket, path, key, req.file.originalname || path, req.file.size, req.file.mimetype || null, req.session.userId])
    const signedUrl = await browserUrl(key)
    res.status(201).json({ path, bucket, signedUrl, item: rowToItem(rows[0], signedUrl) })
  } catch (e) { res.status(500).json({ error: String(e?.message || e) }) }
})

async function delOne(bucket, path) {
  const { rows } = await query("DELETE FROM storage_files WHERE bucket=$1 AND path=$2 RETURNING storage_key", [bucket, path])
  if (rows[0]) await deleteObject(rows[0].storage_key)
  return !!rows[0]
}

router.delete("/storage-files", async (req, res) => {
  let n = 0
  const b = req.body || {}
  if (Array.isArray(b.items)) { for (const it of b.items) if (await delOne(it.bucket || DEFAULT_BUCKET, it.path)) n++ }
  else if (Array.isArray(b.paths)) { for (const p of b.paths) if (await delOne(b.bucket || DEFAULT_BUCKET, p)) n++ }
  res.json({ ok: true, deleted: n })
})

router.post("/storage-files/bulk-delete", requireAdmin, async (req, res) => {
  const { olderThanDays, userId } = req.body || {}
  const where = [], args = []
  if (olderThanDays) { args.push(Number(olderThanDays)); where.push(`created_at < now() - ($${args.length} || ' days')::interval`) }
  if (userId) { args.push(userId); where.push(`user_id = $${args.length}`) }
  if (!where.length) return res.status(400).json({ error: "Cần olderThanDays hoặc userId" })
  const { rows } = await query(`DELETE FROM storage_files WHERE ${where.join(" AND ")} RETURNING storage_key`, args)
  for (const r of rows) await deleteObject(r.storage_key)
  res.json({ ok: true, deleted: rows.length })
})

export default router
// #endregion
