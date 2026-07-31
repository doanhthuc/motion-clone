// #region ALD 11/06/2026 - Thư viện "Model mẫu" cho create-image. Admin upload ảnh người mẫu → API tự XÓA NỀN
//   (bg-remover) + lưu MinIO + phân loại gender × age_group. Worker create-image bốc NGẪU NHIÊN 1 mẫu/output.
//   POST   /model-refs            multipart {image,gender,age_group,label}  (admin)        → { item }
//   GET    /model-refs?gender&age_group&active                              (session)      → { items }
//   PATCH  /model-refs/:id        { is_active,gender,age_group,label }      (admin)         → { item }
//   DELETE /model-refs/:id                                                  (admin)         → { ok }
//   GET    /worker/model-refs?gender&age_group                             (worker token)  → { items:[{id,key}] }
import { Router } from "express"
import multer from "multer"
import crypto from "node:crypto"
import { query } from "../db.js"
import { putObject, deleteObject, browserUrl } from "../storage.js"
import { sessionAuth, requireAdmin } from "../auth/session.js"
import { workerAuth } from "../auth.js"

const upload = multer({ storage: multer.memoryStorage(), limits: { fileSize: 30 * 1024 * 1024, files: 1 } })
const router = Router()

const GENDERS = new Set(["female", "male"])
const AGES = new Set(["young", "middle", "old"])
const BG_REMOVER_URL = (process.env.BG_REMOVER_URL || "http://bg-remover:8000").replace(/\/$/, "")

// Gọi service bg-remover (rembg) tách nền → trả buffer JPEG + kích thước. model=human (người).
async function bgRemove(buffer, mimetype, { model = "human", crop = false } = {}) {
  const url = `${BG_REMOVER_URL}/remove?model=${model}&crop=${crop ? 1 : 0}`
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": mimetype || "application/octet-stream" }, body: buffer })
  if (!r.ok) throw new Error(`bg-remover ${r.status}: ${(await r.text().catch(() => "")).slice(0, 200)}`)
  const buf = Buffer.from(await r.arrayBuffer())
  return { buf, width: Number(r.headers.get("x-image-width")) || null, height: Number(r.headers.get("x-image-height")) || null }
}

async function rowToItem(r) {
  return {
    id: r.id, gender: r.gender, ageGroup: r.age_group, label: r.label || "",
    isActive: r.is_active, width: r.width, height: r.height,
    sizeBytes: r.size_bytes, createdAt: r.created_at,
    url: await browserUrl(r.storage_key),
  }
}

// POST /model-refs — upload + xóa nền + phân loại (admin)
router.post("/model-refs", sessionAuth, requireAdmin, upload.single("image"), async (req, res) => {
  try {
    if (!req.file) return res.status(400).json({ error: "Thiếu ảnh (field 'image')" })
    const gender = String(req.body.gender || "").toLowerCase().trim()
    const ageGroup = String(req.body.age_group || req.body.ageGroup || "").toLowerCase().trim()
    if (!GENDERS.has(gender)) return res.status(400).json({ error: "gender phải là female|male" })
    if (!AGES.has(ageGroup)) return res.status(400).json({ error: "age_group phải là young|middle|old" })
    const label = String(req.body.label || "").trim().slice(0, 120) || null

    let cutout
    try {
      cutout = await bgRemove(req.file.buffer, req.file.mimetype, { model: "human" })
    } catch (e) {
      return res.status(502).json({ error: "Xóa nền thất bại: " + String(e?.message || e) })
    }

    const id = crypto.randomUUID()
    const key = `model-refs/${id}.jpg`
    await putObject(key, cutout.buf, "image/jpeg")
    const { rows } = await query(
      `INSERT INTO model_refs (id, gender, age_group, label, storage_key, mime, size_bytes, width, height, user_id)
       VALUES ($1,$2,$3,$4,$5,'image/jpeg',$6,$7,$8,$9) RETURNING *`,
      [id, gender, ageGroup, label, key, cutout.buf.length, cutout.width, cutout.height, req.session.userId])
    res.status(201).json({ item: await rowToItem(rows[0]) })
  } catch (e) { res.status(500).json({ error: String(e?.message || e) }) }
})

// GET /model-refs — gallery (session). Lọc tùy chọn theo gender/age_group/active.
router.get("/model-refs", sessionAuth, async (req, res) => {
  try {
    const where = [], args = []
    if (GENDERS.has(String(req.query.gender || "").toLowerCase())) { args.push(String(req.query.gender).toLowerCase()); where.push(`gender = $${args.length}`) }
    if (AGES.has(String(req.query.age_group || "").toLowerCase())) { args.push(String(req.query.age_group).toLowerCase()); where.push(`age_group = $${args.length}`) }
    if (req.query.active === "1" || req.query.active === "true") where.push("is_active = true")
    const sql = `SELECT * FROM model_refs ${where.length ? "WHERE " + where.join(" AND ") : ""} ORDER BY created_at DESC`
    const { rows } = await query(sql, args)
    res.json({ items: await Promise.all(rows.map(rowToItem)) })
  } catch (e) { res.status(500).json({ error: String(e?.message || e) }) }
})

// PATCH /model-refs/:id — đổi active / recategorize / label (admin)
router.patch("/model-refs/:id", sessionAuth, requireAdmin, async (req, res) => {
  try {
    const sets = [], args = []
    const b = req.body || {}
    if (typeof b.is_active === "boolean") { args.push(b.is_active); sets.push(`is_active = $${args.length}`) }
    if (GENDERS.has(String(b.gender || "").toLowerCase())) { args.push(String(b.gender).toLowerCase()); sets.push(`gender = $${args.length}`) }
    if (AGES.has(String(b.age_group || "").toLowerCase())) { args.push(String(b.age_group).toLowerCase()); sets.push(`age_group = $${args.length}`) }
    if (typeof b.label === "string") { args.push(b.label.trim().slice(0, 120) || null); sets.push(`label = $${args.length}`) }
    if (!sets.length) return res.status(400).json({ error: "Không có field hợp lệ để cập nhật" })
    args.push(req.params.id)
    const { rows } = await query(`UPDATE model_refs SET ${sets.join(", ")} WHERE id = $${args.length} RETURNING *`, args)
    if (!rows[0]) return res.status(404).json({ error: "Không tìm thấy" })
    res.json({ item: await rowToItem(rows[0]) })
  } catch (e) { res.status(500).json({ error: String(e?.message || e) }) }
})

// DELETE /model-refs/:id — xóa MinIO + row (admin)
router.delete("/model-refs/:id", sessionAuth, requireAdmin, async (req, res) => {
  try {
    const { rows } = await query("DELETE FROM model_refs WHERE id = $1 RETURNING storage_key", [req.params.id])
    if (!rows[0]) return res.status(404).json({ error: "Không tìm thấy" })
    await deleteObject(rows[0].storage_key).catch(() => {})
    res.json({ ok: true })
  } catch (e) { res.status(500).json({ error: String(e?.message || e) }) }
})

// GET /worker/model-refs — worker bốc mẫu active theo category (X-Worker-Token). Trả key để tải qua /files/<key>.
router.get("/worker/model-refs", workerAuth, async (req, res) => {
  try {
    const where = ["is_active = true"], args = []
    if (GENDERS.has(String(req.query.gender || "").toLowerCase())) { args.push(String(req.query.gender).toLowerCase()); where.push(`gender = $${args.length}`) }
    if (AGES.has(String(req.query.age_group || "").toLowerCase())) { args.push(String(req.query.age_group).toLowerCase()); where.push(`age_group = $${args.length}`) }
    const { rows } = await query(`SELECT id, storage_key FROM model_refs WHERE ${where.join(" AND ")}`, args)
    res.json({ items: rows.map((r) => ({ id: r.id, key: r.storage_key })) })
  } catch (e) { res.status(500).json({ error: String(e?.message || e) }) }
})

export default router
// #endregion
