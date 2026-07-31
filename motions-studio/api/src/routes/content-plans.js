// #region ALD 05/07/2026 - "Social Management" — Content Plan: đặt lịch N bài/ngày, mỗi bài (slot) chạy 1
// workflow ở 1 giờ cố định (giờ Asia/Ho_Chi_Minh) rồi tự đăng. Scheduler thật nằm ở wf-worker/social-scheduler.js
// (tickContentPlans, chạy nền trong process wf-worker) — route này chỉ CRUD cấu hình + xem lịch sử chạy.
//   GET    /content-plans                     → { items } (kèm slots[])
//   POST   /content-plans                     { name } → { item }
//   PUT    /content-plans/:id                 { name?, is_active? } → { item }
//   DELETE /content-plans/:id                 (cascade slots + runs)
//   POST   /content-plans/:id/slots           { label,workflow_id,input,time_of_day,weekdays,caption,facebook,tiktok,is_active } → { item }
//   PUT    /content-plans/slots/:slotId        (cùng field) → { item }
//   DELETE /content-plans/slots/:slotId
//   GET    /content-plans/:id/runs            → { items } (lịch sử chạy, join slot label)
import { Router } from "express"
import { query } from "../db.js"
import { sessionAuth } from "../auth/session.js"
import { loadSocialAccounts } from "../wf-worker/social-publish.js"

const router = Router()
router.use("/content-plans", sessionAuth)

const toSlot = (r) => ({
  id: r.id, plan_id: r.plan_id, label: r.label, workflow_id: r.workflow_id, input: r.input,
  time_of_day: r.time_of_day, weekdays: r.weekdays, caption: r.caption,
  facebook: r.facebook, tiktok: r.tiktok, is_active: r.is_active,
})

async function loadOwnPlan(planId, userId) {
  return (await query("SELECT * FROM content_plans WHERE id=$1 AND user_id=$2", [planId, userId])).rows[0]
}
async function loadOwnSlot(slotId, userId) {
  return (await query(
    `SELECT s.* FROM content_plan_slots s JOIN content_plans p ON p.id = s.plan_id
     WHERE s.id=$1 AND p.user_id=$2`, [slotId, userId])).rows[0]
}

router.get("/content-plans", async (req, res) => {
  const plans = (await query("SELECT * FROM content_plans WHERE user_id=$1 ORDER BY created_at", [req.session.userId])).rows
  const ids = plans.map((p) => p.id)
  const slots = ids.length
    ? (await query("SELECT * FROM content_plan_slots WHERE plan_id = ANY($1::uuid[]) ORDER BY time_of_day", [ids])).rows
    : []
  res.json({
    items: plans.map((p) => ({
      id: p.id, name: p.name, is_active: p.is_active, created_at: p.created_at,
      slots: slots.filter((s) => s.plan_id === p.id).map(toSlot),
    })),
  })
})

router.post("/content-plans", async (req, res) => {
  const name = String(req.body?.name || "").trim()
  if (!name) return res.status(400).json({ error: "Thiếu tên plan" })
  const { rows } = await query(
    "INSERT INTO content_plans (user_id, name) VALUES ($1,$2) RETURNING *",
    [req.session.userId, name])
  res.status(201).json({ item: { ...rows[0], slots: [] } })
})

router.put("/content-plans/:id", async (req, res) => {
  const plan = await loadOwnPlan(req.params.id, req.session.userId)
  if (!plan) return res.status(404).json({ error: "Không tìm thấy plan" })
  const name = req.body?.name != null ? String(req.body.name).trim() : plan.name
  const isActive = req.body?.is_active != null ? !!req.body.is_active : plan.is_active
  const { rows } = await query(
    "UPDATE content_plans SET name=$1, is_active=$2, updated_at=now() WHERE id=$3 RETURNING *",
    [name, isActive, plan.id])
  res.json({ item: rows[0] })
})

router.delete("/content-plans/:id", async (req, res) => {
  await query("DELETE FROM content_plans WHERE id=$1 AND user_id=$2", [req.params.id, req.session.userId])
  res.json({ success: true })
})

function parseSlotBody(b, fallback = {}) {
  const weekdays = Array.isArray(b.weekdays) && b.weekdays.length
    ? b.weekdays.map((n) => Number(n)).filter((n) => Number.isInteger(n) && n >= 0 && n <= 6)
    : (fallback.weekdays || [0, 1, 2, 3, 4, 5, 6])
  return {
    label: b.label != null ? String(b.label) : (fallback.label || null),
    workflow_id: b.workflow_id || fallback.workflow_id,
    input: b.input != null ? b.input : (fallback.input || {}),
    time_of_day: b.time_of_day || fallback.time_of_day,
    weekdays,
    caption: b.caption != null ? String(b.caption) : (fallback.caption || null),
    facebook: b.facebook || fallback.facebook || { enabled: false, accountIds: [] },
    tiktok: b.tiktok || fallback.tiktok || { enabled: false, accountIds: [] },
    is_active: b.is_active != null ? !!b.is_active : (fallback.is_active ?? true),
  }
}

// ALD 05/07/2026 - accountIds phải KHÔNG RỖNG khi platform bật, và phải THẬT SỰ thuộc user (loadSocialAccounts
// lọc theo userId+platform) — tránh slot "bật nhưng không đăng đâu cả" hoặc gán nhầm account người khác.
async function validateSlotAccounts(s, userId) {
  for (const platform of ["facebook", "tiktok"]) {
    const p = s[platform]
    if (!p?.enabled) continue
    if (!p.accountIds?.length) return `Đã bật ${platform === "facebook" ? "Facebook" : "TikTok"} nhưng chưa chọn tài khoản`
    const owned = await loadSocialAccounts(userId, platform, p.accountIds)
    if (owned.length !== p.accountIds.length) return `Tài khoản ${platform === "facebook" ? "Facebook" : "TikTok"} đã chọn không hợp lệ/không thuộc quyền của bạn`
  }
  // ALD 05/07/2026 - Content Sharing Guidelines mục 4: privacy KHÔNG được để mặc định/rỗng — FE phải cho user tự
  // chọn (xem TikTokFields.vue). Branded Content không được để riêng tư (mục 5b).
  if (s.tiktok?.enabled) {
    if (!s.tiktok.privacyLevel) return "TikTok: chưa chọn chế độ hiển thị (privacy) cho bài đăng"
    if (s.tiktok.brandContentToggle && s.tiktok.privacyLevel === "SELF_ONLY") return "TikTok: nội dung 'Branded Content' không được để chế độ riêng tư (Chỉ mình tôi)"
  }
  return null
}

router.post("/content-plans/:id/slots", async (req, res) => {
  const plan = await loadOwnPlan(req.params.id, req.session.userId)
  if (!plan) return res.status(404).json({ error: "Không tìm thấy plan" })
  const s = parseSlotBody(req.body || {})
  if (!s.workflow_id) return res.status(400).json({ error: "Thiếu workflow_id" })
  if (!s.time_of_day) return res.status(400).json({ error: "Thiếu time_of_day (giờ chạy, vd '08:00')" })
  // ALD 23/07/2026 - Mọi workflow active đều là template dùng chung cho user đã đăng nhập.
  const wf = (await query("SELECT id FROM workflows WHERE id=$1 AND is_active=true", [s.workflow_id])).rows[0]
  if (!wf) return res.status(400).json({ error: "Workflow không tồn tại/không có quyền" })
  const accountsErr = await validateSlotAccounts(s, req.session.userId)
  if (accountsErr) return res.status(400).json({ error: accountsErr })
  const { rows } = await query(
    `INSERT INTO content_plan_slots (plan_id, label, workflow_id, input, time_of_day, weekdays, caption, facebook, tiktok, is_active)
     VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) RETURNING *`,
    [plan.id, s.label, s.workflow_id, JSON.stringify(s.input), s.time_of_day, s.weekdays, s.caption,
      JSON.stringify(s.facebook), JSON.stringify(s.tiktok), s.is_active])
  res.status(201).json({ item: toSlot(rows[0]) })
})

router.put("/content-plans/slots/:slotId", async (req, res) => {
  const cur = await loadOwnSlot(req.params.slotId, req.session.userId)
  if (!cur) return res.status(404).json({ error: "Không tìm thấy slot" })
  const s = parseSlotBody(req.body || {}, cur)
  const accountsErr = await validateSlotAccounts(s, req.session.userId)
  if (accountsErr) return res.status(400).json({ error: accountsErr })
  const { rows } = await query(
    `UPDATE content_plan_slots SET label=$1, workflow_id=$2, input=$3, time_of_day=$4, weekdays=$5,
       caption=$6, facebook=$7, tiktok=$8, is_active=$9, updated_at=now() WHERE id=$10 RETURNING *`,
    [s.label, s.workflow_id, JSON.stringify(s.input), s.time_of_day, s.weekdays, s.caption,
      JSON.stringify(s.facebook), JSON.stringify(s.tiktok), s.is_active, cur.id])
  res.json({ item: toSlot(rows[0]) })
})

router.delete("/content-plans/slots/:slotId", async (req, res) => {
  const cur = await loadOwnSlot(req.params.slotId, req.session.userId)
  if (!cur) return res.status(404).json({ error: "Không tìm thấy slot" })
  await query("DELETE FROM content_plan_slots WHERE id=$1", [cur.id])
  res.json({ success: true })
})

router.get("/content-plans/:id/runs", async (req, res) => {
  const plan = await loadOwnPlan(req.params.id, req.session.userId)
  if (!plan) return res.status(404).json({ error: "Không tìm thấy plan" })
  const { rows } = await query(
    `SELECT r.*, s.label AS slot_label FROM content_plan_runs r
     JOIN content_plan_slots s ON s.id = r.slot_id
     WHERE s.plan_id = $1 ORDER BY r.created_at DESC LIMIT 100`, [plan.id])
  res.json({ items: rows })
})

export default router
// #endregion
