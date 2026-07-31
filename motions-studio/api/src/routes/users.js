// #region ALD 01/06/2026 - Admin User CRUD (chỉ admin). Quản lý bảng users.
// Đăng nhập là OTP (không có password) → "tạo user" = chèn record; user login bằng OTP sau.
//   GET    /admin/users   ?q=&role=&active=&page=&limit=  → { items, total, page, limit, stats }
//   POST   /admin/users   { email, fullName, role, department, title, hrCode, isActive } → { item }
//   PUT    /admin/users/:id  (partial: bất kỳ field nào ở trên)                          → { item }
//   DELETE /admin/users/:id                                                              → { success }
// Tự bảo vệ: admin KHÔNG tự xoá / tự khoá / tự hạ quyền chính mình (tránh tự khoá ra ngoài).
import { Router } from "express"
import { query } from "../db.js"
import { sessionAuth, requireAdmin } from "../auth/session.js"

const router = Router()
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
const ROLES = ["admin", "staff"]
const norm = (e) => String(e || "").trim().toLowerCase()

const toItem = (r) => ({
  id: r.id,
  email: r.email,
  fullName: r.full_name,
  role: r.role,
  isActive: r.is_active,
  department: r.department,
  title: r.title,
  hrCode: r.hr_code,
  avatarUrl: r.avatar_url,
  lastLoginAt: r.last_login_at,
  createdAt: r.created_at,
})

// Mọi route quản lý user đều cần session HỢP LỆ + quyền admin.
router.use("/admin/users", sessionAuth, requireAdmin)

// ── LIST (search + filter + paginate) + stats tổng ───────────────────────────
router.get("/admin/users", async (req, res) => {
  try {
    const q = String(req.query.q || "").trim()
    const role = ROLES.includes(req.query.role) ? req.query.role : ""
    const active = req.query.active // 'true' | 'false' | undefined
    const limit = Math.min(200, Math.max(1, Number(req.query.limit) || 50))
    const page = Math.max(1, Number(req.query.page) || 1)
    const offset = (page - 1) * limit

    const where = [], args = []
    if (q) {
      args.push(`%${q}%`)
      where.push(`(email ILIKE $${args.length} OR full_name ILIKE $${args.length} OR hr_code ILIKE $${args.length})`)
    }
    if (role) { args.push(role); where.push(`role = $${args.length}`) }
    if (active === "true" || active === "false") { args.push(active === "true"); where.push(`is_active = $${args.length}`) }
    const whereSql = where.length ? `WHERE ${where.join(" AND ")}` : ""

    const totalRes = await query(`SELECT count(*)::int AS cnt FROM users ${whereSql}`, args)
    // Stats trên TOÀN bảng (không theo filter) — hiển thị bức tranh tổng.
    const statsRes = await query(
      `SELECT count(*)::int AS total,
              count(*) FILTER (WHERE role = 'admin')::int AS admins,
              count(*) FILTER (WHERE is_active)::int AS active
       FROM users`)

    args.push(limit, offset)
    const { rows } = await query(
      `SELECT * FROM users ${whereSql} ORDER BY created_at DESC LIMIT $${args.length - 1} OFFSET $${args.length}`, args)

    res.json({
      items: rows.map(toItem),
      total: totalRes.rows[0].cnt,
      page, limit,
      stats: statsRes.rows[0],
    })
  } catch (e) { res.status(500).json({ error: String(e?.message || e) }) }
})

// ── CREATE ───────────────────────────────────────────────────────────────────
router.post("/admin/users", async (req, res) => {
  try {
    const b = req.body || {}
    const email = norm(b.email)
    if (!email || !EMAIL_RE.test(email)) return res.status(400).json({ error: "Email không hợp lệ" })
    const role = ROLES.includes(b.role) ? b.role : "staff"
    const { rows } = await query(
      `INSERT INTO users (email, full_name, role, department, title, hr_code, is_active)
       VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING *`,
      [email, b.fullName || null, role, b.department || null, b.title || null, b.hrCode || null,
       b.isActive === false ? false : true])
    res.status(201).json({ item: toItem(rows[0]) })
  } catch (e) {
    if (/duplicate|unique/i.test(String(e.message))) return res.status(409).json({ error: "Email đã tồn tại" })
    res.status(500).json({ error: String(e?.message || e) })
  }
})

// ── UPDATE (partial) ──────────────────────────────────────────────────────────
router.put("/admin/users/:id", async (req, res) => {
  try {
    const cur = await query("SELECT * FROM users WHERE id = $1", [req.params.id])
    const old = cur.rows[0]
    if (!old) return res.status(404).json({ error: "Không tìm thấy user" })

    const b = req.body || {}
    const isSelf = req.params.id === req.session.userId
    const sets = [], args = []

    if (b.email !== undefined) {
      const email = norm(b.email)
      if (!email || !EMAIL_RE.test(email)) return res.status(400).json({ error: "Email không hợp lệ" })
      args.push(email); sets.push(`email = $${args.length}`)
    }
    if (b.fullName !== undefined) { args.push(b.fullName || null); sets.push(`full_name = $${args.length}`) }
    if (b.department !== undefined) { args.push(b.department || null); sets.push(`department = $${args.length}`) }
    if (b.title !== undefined) { args.push(b.title || null); sets.push(`title = $${args.length}`) }
    if (b.hrCode !== undefined) { args.push(b.hrCode || null); sets.push(`hr_code = $${args.length}`) }
    if (b.avatarUrl !== undefined) { args.push(b.avatarUrl || null); sets.push(`avatar_url = $${args.length}`) }
    if (b.role !== undefined) {
      if (!ROLES.includes(b.role)) return res.status(400).json({ error: "Vai trò không hợp lệ" })
      if (isSelf && b.role !== "admin") return res.status(400).json({ error: "Không thể tự hạ quyền admin của chính mình" })
      args.push(b.role); sets.push(`role = $${args.length}`)
    }
    if (b.isActive !== undefined) {
      if (isSelf && b.isActive === false) return res.status(400).json({ error: "Không thể tự khoá tài khoản của chính mình" })
      args.push(!!b.isActive); sets.push(`is_active = $${args.length}`)
    }

    if (!sets.length) return res.json({ item: toItem(old) })
    args.push(req.params.id)
    const { rows } = await query(`UPDATE users SET ${sets.join(", ")} WHERE id = $${args.length} RETURNING *`, args)
    res.json({ item: toItem(rows[0]) })
  } catch (e) {
    if (/duplicate|unique/i.test(String(e.message))) return res.status(409).json({ error: "Email đã tồn tại" })
    res.status(500).json({ error: String(e?.message || e) })
  }
})

// ── DELETE ───────────────────────────────────────────────────────────────────
// user_sessions / api_keys ON DELETE CASCADE → dọn sạch theo user.
router.delete("/admin/users/:id", async (req, res) => {
  try {
    if (req.params.id === req.session.userId)
      return res.status(400).json({ error: "Không thể tự xoá tài khoản của chính mình" })
    const { rows } = await query("DELETE FROM users WHERE id = $1 RETURNING id", [req.params.id])
    if (!rows[0]) return res.status(404).json({ error: "Không tìm thấy user" })
    res.json({ success: true })
  } catch (e) { res.status(500).json({ error: String(e?.message || e) }) }
})

export default router
// #endregion
