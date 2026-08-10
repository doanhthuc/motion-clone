// #region ALD 31/05/2026 - Workflows CRUD + runs (port /functions/v1/workflows, phần CRUD).
// Engine chạy graph (test/invoke) → tạo workflow_runs status=queued, worker engine (Phase 4) sẽ chạy.
//   GET    /workflows                  → { items }   (của user + public)
//   GET    /workflows/:id              → { item }    (strip secrets node)
//   POST   /workflows                  → { item }    ({ slug,name,description,definition,is_public })
//   PUT    /workflows/:id              → { item }    (merge secrets cũ nếu FE gửi rỗng)
//   DELETE /workflows/:id
//   POST   /workflows/:id/test         { definition, input } → { run_id, status, poll_url }
//   POST   /workflows/:slug/invoke     { ...input }          → { run_id, status, poll_url }
//   GET    /workflows/:id/runs         → { items }
//   GET    /workflows/runs/:runId      → { run }
//   POST   /workflows/runs/:runId/cancel
//   DELETE /workflows/runs/:runId
//   GET    /workflows/:wfId/asset/:libId?kind=audio|storage → { item:{signedUrl,...} }
import { Router } from "express"
import { query } from "../db.js"
import { sessionAuth } from "../auth/session.js"
import { presignGet, browserUrl } from "../storage.js"

const router = Router()
const SLUG_RE = /^[a-z0-9][a-z0-9-]{1,49}$/
// ALD 11/06/2026 - thêm hfToken (HuggingFace token per-node). Field RIÊNG thay vì tái dùng apiKey để node cũ
// có apiKey=AIza… (gemini) khi flip provider sang HF không bị gửi nhầm key.
const SECRET_KEYS = ["apiKey", "geminiApiKey", "hfToken"]

function stripSecrets(def) {
  if (!def || typeof def !== "object" || !Array.isArray(def.nodes)) return def
  return {
    ...def,
    nodes: def.nodes.map((n) => {
      const cfg = n?.data?.config
      if (!cfg || typeof cfg !== "object") return n
      const newCfg = { ...cfg }
      // ALD 05/06/2026 - chỉ báo _isSet khi THỰC SỰ có key (rỗng = chưa có/đã xoá → FE hiện input để nhập lại).
      for (const k of SECRET_KEYS) if (k in newCfg) { const had = !!newCfg[k]; newCfg[k] = ""; if (had) newCfg[`__${k}_isSet`] = true }
      return { ...n, data: { ...n.data, config: newCfg } }
    }),
  }
}
// Merge secrets: nếu PUT gửi key rỗng nhưng def cũ có → giữ giá trị cũ (FE không re-nhập key).
function mergeSecrets(newDef, oldDef) {
  if (!newDef?.nodes || !oldDef?.nodes) return newDef
  const oldById = new Map(oldDef.nodes.map((n) => [n.id, n]))
  newDef.nodes = newDef.nodes.map((n) => {
    const cfg = n?.data?.config
    if (!cfg) return n
    const oldCfg = oldById.get(n.id)?.data?.config || {}
    const merged = { ...cfg }
    for (const k of SECRET_KEYS) {
      // ALD 05/06/2026 - cờ __<k>_clear = user CHỦ ĐỘNG xoá key → để rỗng (KHÔNG khôi phục key cũ).
      if (merged[`__${k}_clear`]) merged[k] = ""
      else if ((merged[k] === "" || merged[k] == null) && oldCfg[k]) merged[k] = oldCfg[k]
      delete merged[`__${k}_isSet`]
      delete merged[`__${k}_clear`]
    }
    return { ...n, data: { ...n.data, config: merged } }
  })
  return newDef
}

// #region ALD 13/07/2026 - Node video pose-driven duy nhất là Motion Transfer.
function definitionUsesRemovedNode(def) {
  return (def?.nodes || []).some((n) => String(n?.type || n?.data?.type) === "fashion-motion")
}
const REMOVED_NODE_ERROR = "Node gộp Try-on + Wan đã được gỡ. Hãy dùng Try-on → Motion Transfer."
// #endregion
const toItem = (r, strip = false) => ({
  id: r.id, user_id: r.user_id, slug: r.slug, name: r.name, description: r.description,
  definition: strip ? stripSecrets(r.definition) : r.definition,
  is_public: r.is_public, is_active: r.is_active, async_enabled: r.async_enabled,
  callback_url: r.callback_url, created_at: r.created_at, updated_at: r.updated_at,
})

router.use("/workflows", sessionAuth)

function includeInactive(req) {
  return req.session.role === "admin" && String(req.query.include_inactive || "").toLowerCase() === "true"
}

// #region ALD 29/06/2026 - Admin toàn quyền quản lý workflow.
//   Admin: thấy TẤT CẢ workflow của MỌI user (kể cả đã tắt) để xoá/quản lý.
//   ALD 23/07/2026 - Mọi user đăng nhập thấy toàn bộ workflow đang active để dùng chung như template.
//   Quyền sửa/xoá vẫn chỉ dành cho owner hoặc admin.
router.get("/workflows", async (req, res) => {
  const admin = req.session.role === "admin"
  const where = admin ? "TRUE" : "w.is_active = true"
  // JOIN users để admin biết workflow của AI (owner_email) khi quyết định xoá.
  const { rows } = await query(
    `SELECT w.*, u.email AS owner_email FROM workflows w
       LEFT JOIN users u ON u.id = w.user_id
      WHERE ${where} ORDER BY w.updated_at DESC`)
  // owned = đúng chủ sở hữu (FE dùng để ẩn UI Lưu / auto-save cho non-owner trên workflow public).
  res.json({ items: rows.map((r) => ({
    ...toItem(r, true),
    owned: r.user_id === req.session.userId,
    owner_email: r.owner_email || null,
  })) })
})
// #endregion

// #region ALD 09/07/2026 - Capabilities của BOX (API + worker chạy CÙNG máy): FE dùng để ẨN preset nặng
// (vd motion 20s·30fps = 601f cần RAM ≥128GB — minRamGb trong MOTION_PRESETS). Đặt TRƯỚC /workflows/:id kẻo bị nuốt.
// ALD 20/07/2026 - Thêm gpuVramGb + ngưỡng Wan-Dancer để FE GATE node "Vũ đạo theo nhạc" (khóa mờ khi < 90GB).
// ALD 10/08/2026 - totalRamGb lấy TRẦN CGROUP (box-ram.js) chứ không os.totalmem(): trên pod RunPod
// os.totalmem() trả RAM host 123 GiB trong khi container chỉ 55,9 GiB, nên gate preset đang mở nhầm
// những preset mà box không gánh nổi. Cùng con bug pod-fe.sh + sitecustomize.py đã trị.
router.get("/workflows/capabilities", sessionAuth, async (_req, res) => {
  const { boxRamGb } = await import("../box-ram.js")
  const { detectGpuVramGb, WAN_DANCER_MIN_VRAM_GB } = await import("../gpu-vram.js")
  const gpuVramGb = await detectGpuVramGb().catch(() => 0)
  res.json({
    totalRamGb: boxRamGb(),
    gpuVramGb,
    wanDancerMinVramGb: WAN_DANCER_MIN_VRAM_GB,
  })
})
// #endregion

router.get("/workflows/:id", async (req, res) => {
  const { rows } = await query("SELECT * FROM workflows WHERE id = $1", [req.params.id])
  const r = rows[0]
  if (!r) return res.status(404).json({ error: "Không tìm thấy" })
  // ALD 29/06/2026 - admin mở được cả workflow đã tắt (để quản lý/xoá), không cần ?include_inactive.
  if (r.is_active === false && !includeInactive(req) && req.session.role !== "admin")
    return res.status(404).json({ error: "Workflow đã bị tắt" })
  res.json({ item: { ...toItem(r, true), owned: r.user_id === req.session.userId } })
})

router.post("/workflows", async (req, res) => {
  const { slug, name, description, definition, is_public } = req.body || {}
  if (!slug || !SLUG_RE.test(slug)) return res.status(400).json({ error: "slug không hợp lệ (a-z0-9-)" })
  if (!name) return res.status(400).json({ error: "Thiếu name" })
  if (definitionUsesRemovedNode(definition)) return res.status(400).json({ error: REMOVED_NODE_ERROR })
  try {
    const { rows } = await query(
      `INSERT INTO workflows (user_id, slug, name, description, definition, is_public)
       VALUES ($1,$2,$3,$4,$5,$6) RETURNING *`,
      [req.session.userId, slug, name, description || null,
       JSON.stringify(definition || { nodes: [], edges: [] }), !!is_public])
    res.status(201).json({ item: toItem(rows[0]) })
  } catch (e) {
    if (String(e.message).includes("unique")) return res.status(409).json({ error: "slug đã tồn tại" })
    res.status(500).json({ error: String(e?.message || e) })
  }
})

router.put("/workflows/:id", async (req, res) => {
  const cur = await query("SELECT * FROM workflows WHERE id = $1", [req.params.id])
  const old = cur.rows[0]
  if (!old) return res.status(404).json({ error: "Không tìm thấy" })
  if (old.user_id !== req.session.userId && req.session.role !== "admin")
    return res.status(403).json({ error: "Không có quyền" })
  const b = req.body || {}
  if (definitionUsesRemovedNode(b.definition)) return res.status(400).json({ error: REMOVED_NODE_ERROR })
  const sets = [], args = []
  if (b.name != null) { args.push(b.name); sets.push(`name = $${args.length}`) }
  if (b.description != null) { args.push(b.description); sets.push(`description = $${args.length}`) }
  if (b.definition != null) { args.push(JSON.stringify(mergeSecrets(b.definition, old.definition))); sets.push(`definition = $${args.length}`) }
  if (b.is_public != null) { args.push(!!b.is_public); sets.push(`is_public = $${args.length}`) }
  if (b.is_active != null) { args.push(!!b.is_active); sets.push(`is_active = $${args.length}`) }
  if (b.async_enabled != null) { args.push(!!b.async_enabled); sets.push(`async_enabled = $${args.length}`) }
  if (b.callback_url !== undefined) { args.push(b.callback_url || null); sets.push(`callback_url = $${args.length}`) }
  if (!sets.length) return res.json({ item: toItem(old, true) })
  args.push(req.params.id)
  const { rows } = await query(`UPDATE workflows SET ${sets.join(", ")} WHERE id = $${args.length} RETURNING *`, args)
  res.json({ item: toItem(rows[0], true) })
})

router.delete("/workflows/:id", async (req, res) => {
  const { rows } = await query(
    "DELETE FROM workflows WHERE id = $1 AND (user_id = $2 OR $3 = 'admin') RETURNING id",
    [req.params.id, req.session.userId, req.session.role])
  res.json({ success: !!rows[0] })
})

// ALD 14/06/2026 - Xoá TOÀN BỘ lịch sử run của 1 workflow (FE "Clear history" gọi DELETE /workflows/:id/runs).
// Trước đây THIẾU endpoint này → FE báo 404. Chỉ xoá run của workflow user sở hữu (hoặc admin). Trả { deleted }.
// ALD 15/06/2026 - "Clear history" xoá run CỦA MÌNH cho workflow này (admin: xoá tất cả). Không chặn theo
// chủ workflow nữa (workflow public dùng chung → user vẫn được dọn lịch sử của mình, hết lỗi 404).
router.delete("/workflows/:id/runs", async (req, res) => {
  try {
    const admin = req.session.role === "admin"
    const args = admin ? [req.params.id] : [req.params.id, req.session.userId]
    const { rowCount } = await query(
      `DELETE FROM workflow_runs WHERE workflow_id = $1 ${admin ? "" : "AND user_id = $2"}`, args)
    res.json({ success: true, deleted: rowCount || 0 })
  } catch (e) { res.status(500).json({ error: String(e?.message || e) }) }
})

// ALD 05/06/2026 - Gemini (image provider + giọng TTS gemini:) CHỈ dành cho ADMIN → chặn ngay khi tạo run.
function definitionUsesGemini(def) {
  for (const n of def?.nodes || []) {
    const c = n?.data?.config || {}
    if (String(c.provider || "").toLowerCase() === "gemini") return true
    if (String(c.voice || "").toLowerCase().startsWith("gemini:")) return true
  }
  return false
}
const GEMINI_ADMIN_ONLY = "Gemini chỉ dành cho admin. Đổi provider sang Qwen (hoặc giọng khác) để chạy."

// ── Runs (engine thật chạy ở worker — Phase 4; ở đây tạo run queued + trả poll_url) ──
async function createRun({ workflowId, userId, authMethod, input, definition }) {
  const { rows } = await query(
    `INSERT INTO workflow_runs (workflow_id, user_id, auth_method, status, input, definition)
     VALUES ($1,$2,$3,'queued',$4,$5) RETURNING id, status`,
    [workflowId || null, userId, authMethod, JSON.stringify(input || {}),
     definition ? JSON.stringify(definition) : null])
  return rows[0]
}

router.post("/workflows/:id/test", async (req, res) => {
  const wf = await query("SELECT id, user_id, definition, is_active FROM workflows WHERE id = $1", [req.params.id])
  const row = wf.rows[0]
  if (!row) return res.status(404).json({ error: "Không tìm thấy workflow" })
  if (row.is_active === false)
    return res.status(404).json({ error: "Workflow đã bị tắt" })
  // ALD 03/06/2026 - FE strip secret (apiKey…) khi GET → def test-run gửi lên có key RỖNG. Merge lại từ
  // workflow ĐÃ LƯU (giống nhánh PUT) → test-run/Re-run KHÔNG đòi dán key mỗi lần (key đã lưu server-side).
  const sent = req.body?.definition
  const definition = sent && row.definition ? mergeSecrets(sent, row.definition) : sent
  if (definitionUsesRemovedNode(definition || row.definition)) return res.status(400).json({ error: REMOVED_NODE_ERROR })
  if (req.session.role !== "admin" && definitionUsesGemini(definition))
    return res.status(403).json({ error: GEMINI_ADMIN_ONLY })
  const run = await createRun({
    workflowId: row.id, userId: req.session.userId, authMethod: req.session.authMethod,
    input: req.body?.input, definition,
  })
  res.status(202).json({ run_id: run.id, status: run.status, poll_url: `/workflows/runs/${run.id}` })
})

router.post("/workflows/:slug/invoke", async (req, res) => {
  // ALD 23/07/2026 - Workflow active là template dùng chung. Nếu trùng slug, ưu tiên bản của chính user,
  // sau đó bản public, rồi bản cập nhật mới nhất để hành vi invoke ổn định.
  const wf = await query(`SELECT id, definition FROM workflows
    WHERE slug = $1 AND is_active = true
    ORDER BY (user_id = $2) DESC, is_public DESC, updated_at DESC
    LIMIT 1`,
    [req.params.slug, req.session.userId])
  if (!wf.rows[0]) return res.status(404).json({ error: "Không tìm thấy workflow" })
  if (definitionUsesRemovedNode(wf.rows[0].definition)) return res.status(400).json({ error: REMOVED_NODE_ERROR })
  if (req.session.role !== "admin" && definitionUsesGemini(wf.rows[0].definition))
    return res.status(403).json({ error: GEMINI_ADMIN_ONLY })
  const run = await createRun({
    workflowId: wf.rows[0].id, userId: req.session.userId, authMethod: req.session.authMethod, input: req.body,
  })
  res.status(202).json({ run_id: run.id, status: run.status, poll_url: `/workflows/runs/${run.id}` })
})

// ALD 15/06/2026 - CÔ LẬP THEO USER: workflow có thể is_public (vd 'create-image' dùng chung) nên run
// PHẢI lọc theo user_id — user chỉ thấy/sửa/xoá run CỦA MÌNH; admin thấy/sửa hết. (Trước đây thiếu lọc →
// log hiện run của mọi user, và xoá/cancel được run của người khác.)
// ALD 11/07/2026 - Phân trang runs (FE runs.vue "10 job/trang"). Trước đây cứng LIMIT 50 không offset →
// list dài + chậm. Giờ nhận ?limit=&offset=, trả kèm total để FE dựng pager. Backward-compat: không truyền
// param → limit=50 như cũ. SELECT vẫn slim (không lấy events/output/definition) nên nhẹ.
router.get("/workflows/:id/runs", async (req, res) => {
  const own = req.session.role === "admin"
  const limit = Math.min(100, Math.max(1, Number(req.query.limit) || 50))
  const offset = Math.max(0, Number(req.query.offset) || 0)
  const args = own ? [req.params.id] : [req.params.id, req.session.userId]
  const whereUser = own ? "" : "AND user_id = $2"
  const { rows } = await query(
    `SELECT id, status, error_msg, started_at, finished_at FROM workflow_runs
     WHERE workflow_id = $1 ${whereUser} ORDER BY started_at DESC
     LIMIT $${args.length + 1} OFFSET $${args.length + 2}`, [...args, limit, offset])
  const cnt = await query(
    `SELECT count(*)::int AS total FROM workflow_runs WHERE workflow_id = $1 ${whereUser}`, args)
  res.json({ items: rows, total: cnt.rows[0]?.total ?? rows.length, limit, offset })
})

router.get("/workflows/runs/:runId", async (req, res) => {
  const { rows } = await query("SELECT * FROM workflow_runs WHERE id = $1", [req.params.runId])
  const r = rows[0]
  if (!r) return res.status(404).json({ error: "Không tìm thấy run" })
  if (r.user_id !== req.session.userId && req.session.role !== "admin")
    return res.status(404).json({ error: "Không tìm thấy run" })
  res.json({ run: r })
})

router.post("/workflows/runs/:runId/cancel", async (req, res) => {
  await query(
    `UPDATE workflow_runs SET status='cancelled', finished_at=now()
     WHERE id=$1 AND status IN ('queued','running') AND (user_id=$2 OR $3='admin')`,
    [req.params.runId, req.session.userId, req.session.role])
  res.json({ success: true })
})

router.delete("/workflows/runs/:runId", async (req, res) => {
  await query("DELETE FROM workflow_runs WHERE id = $1 AND (user_id=$2 OR $3='admin')",
    [req.params.runId, req.session.userId, req.session.role])
  res.json({ success: true })
})

// Asset resolver: audio_files / storage_files referenced trong workflow def → signed URL
router.get("/workflows/:wfId/asset/:libId", async (req, res) => {
  const kind = req.query.kind === "storage" ? "storage" : "audio"
  try {
    if (kind === "audio") {
      const { rows } = await query("SELECT id, name, mime, storage_key FROM audio_files WHERE id = $1", [req.params.libId])
      if (!rows[0]) return res.status(404).json({ item: null })
      return res.json({ item: { signedUrl: await browserUrl(rows[0].storage_key), name: rows[0].name, mime: rows[0].mime } })
    }
    const { rows } = await query("SELECT bucket, path, name, mime, storage_key FROM storage_files WHERE id = $1", [req.params.libId])
    if (!rows[0]) return res.status(404).json({ item: null })
    res.json({ item: { signedUrl: await browserUrl(rows[0].storage_key), name: rows[0].name, mime: rows[0].mime } })
  } catch (e) { res.status(500).json({ error: String(e?.message || e) }) }
})

export default router
// #endregion
