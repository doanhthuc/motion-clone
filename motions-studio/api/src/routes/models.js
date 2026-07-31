// #region ALD 14/06/2026 - "Model AI (custom)": admin upload file model tự train (LoRA/checkpoint/…) →
//   lưu THẲNG xuống đĩa trong search-path ComfyUI: ${MODEL_UPLOADS_DIR}/<type>/<filename>
//   (host ~/ai/ComfyUI/models/uploads/<type>/, đăng ký qua extra_model_paths.yaml). KHÔNG vào MinIO —
//   ComfyUI chỉ nạp model theo TÊN FILE trên đĩa. Bảng model_files chỉ giữ metadata để list/xoá (dọn dẹp).
//   Gom 1 nơi (folder uploads/) → muốn dọn sạch chỉ cần xoá folder hoặc xoá từng file qua UI.
//   GET    /models[?type=loras]   (session — để node picker dùng)                 → { items }
//   POST   /models   multipart {file,type,note}   (admin — stream xuống đĩa)      → { item }
//   DELETE /models/:id                              (admin — unlink file + row)   → { ok }
import { Router } from "express"
import multer from "multer"
import crypto from "node:crypto"
import fs from "node:fs"
import path from "node:path"
import { query } from "../db.js"
import { sessionAuth, requireAdmin } from "../auth/session.js"
import {
  loadCatalog, getInstalledComfy, getInstalledOllama, enqueueInstall, cancelDownload,
  removeComfyFile, deleteOllama, COMFY_TYPES,
} from "../models-install.js"

const router = Router()

// Thư mục gốc 1 nơi (bind-mount từ host ~/ai/ComfyUI/models/uploads vào container). 1 subdir / 1 loại model.
const UPLOADS_DIR = process.env.MODEL_UPLOADS_DIR || "/model-uploads"
// type = subdir ComfyUI hợp lệ (KHỚP key trong extra_model_paths.yaml). Whitelist chặt để tránh path lạ.
const TYPES = new Set(["loras", "checkpoints", "unet", "vae", "text_encoders", "clip_vision"])
const MAX_BYTES = Number(process.env.MODEL_MAX_BYTES || 30 * 1024 * 1024 * 1024) // 30GB
const ALLOWED_EXT = new Set([".safetensors", ".gguf", ".pt", ".pth", ".ckpt", ".bin"])

const tmpDir = () => {
  const d = path.join(UPLOADS_DIR, ".tmp")
  fs.mkdirSync(d, { recursive: true })
  return d
}
// Sanitize TÊN FILE (chỉ basename, ký tự an toàn). Giữ tên gốc để dễ nhận diện ở loader ComfyUI.
function safeName(name) {
  const base = path.basename(String(name || "")).trim()
  const cleaned = base.replace(/[^A-Za-z0-9._-]+/g, "_").replace(/^\.+/, "")
  return cleaned.slice(0, 200)
}
const dirFor = (type) => {
  const d = path.join(UPLOADS_DIR, type)
  fs.mkdirSync(d, { recursive: true })
  return d
}

// Stream xuống đĩa (KHÔNG memoryStorage — file GB không buffer vào RAM). Tạm vào .tmp cùng filesystem → rename.
const upload = multer({
  storage: multer.diskStorage({
    destination: (_req, _file, cb) => { try { cb(null, tmpDir()) } catch (e) { cb(e) } },
    filename: (_req, _file, cb) => cb(null, `up-${crypto.randomUUID()}.part`),
  }),
  limits: { fileSize: MAX_BYTES, files: 1 },
})

const rowToItem = (r) => ({
  id: r.id, type: r.type, filename: r.filename, originalName: r.original_name,
  sizeBytes: r.size_bytes != null ? Number(r.size_bytes) : null, note: r.note, createdAt: r.created_at,
})

// GET /models — list model ACTIVE (mở cho mọi user authed: node picker LoRA cần). ?type=loras để lọc.
router.get("/models", sessionAuth, async (req, res) => {
  try {
    const type = String(req.query.type || "").trim()
    const where = ["is_active = true"]
    const args = []
    if (type) { args.push(type); where.push(`type = $${args.length}`) }
    const { rows } = await query(
      `SELECT * FROM model_files WHERE ${where.join(" AND ")} ORDER BY created_at DESC`, args)
    res.json({ items: rows.map(rowToItem) })
  } catch (e) { res.status(500).json({ error: String(e?.message || e) }) }
})

// POST /models — upload 1 file model (admin). multer đã stream xuống .tmp → validate → rename vào <type>/.
router.post("/models", sessionAuth, requireAdmin, upload.single("file"), async (req, res) => {
  const tmpPath = req.file?.path
  const cleanup = () => { try { if (tmpPath && fs.existsSync(tmpPath)) fs.unlinkSync(tmpPath) } catch { /* noop */ } }
  try {
    if (!req.file) return res.status(400).json({ error: "Thiếu file (field 'file')" })
    const type = String(req.body.type || "").trim()
    if (!TYPES.has(type)) { cleanup(); return res.status(400).json({ error: `Loại không hợp lệ: '${type}'. Cho phép: ${[...TYPES].join(", ")}` }) }
    const filename = safeName(req.file.originalname)
    if (!filename) { cleanup(); return res.status(400).json({ error: "Tên file không hợp lệ" }) }
    const ext = path.extname(filename).toLowerCase()
    if (!ALLOWED_EXT.has(ext)) { cleanup(); return res.status(400).json({ error: `Đuôi file không hỗ trợ: ${ext || "(không có)"}. Cho phép: ${[...ALLOWED_EXT].join(", ")}` }) }

    const dest = path.join(dirFor(type), filename)
    // Trùng (type, filename) → từ chối để KHÔNG đè model đang dùng. Check cả DB lẫn đĩa.
    const dup = await query("SELECT id FROM model_files WHERE type = $1 AND filename = $2 AND is_active = true", [type, filename])
    if (dup.rows[0] || fs.existsSync(dest)) { cleanup(); return res.status(409).json({ error: `File '${filename}' (${type}) đã tồn tại — đổi tên hoặc xoá bản cũ trước` }) }

    fs.renameSync(tmpPath, dest)   // cùng filesystem (cùng UPLOADS_DIR) → rename nguyên tử, không copy GB
    const size = (() => { try { return fs.statSync(dest).size } catch { return req.file.size || null } })()
    const note = String(req.body.note || "").trim().slice(0, 300) || null

    const { rows } = await query(
      `INSERT INTO model_files (type, filename, original_name, size_bytes, note, uploaded_by)
       VALUES ($1,$2,$3,$4,$5,$6) RETURNING *`,
      [type, filename, req.file.originalname || filename, size, note, req.session.userId])
    res.status(201).json({ item: rowToItem(rows[0]) })
  } catch (e) { cleanup(); res.status(500).json({ error: String(e?.message || e) }) }
})

// DELETE /models/:id — xoá model_files (custom upload) theo UUID. Admin.
// ALD 16/06/2026 - RÀNG BUỘC :id = uuid (36 ký tự hex/dash). Nếu để ":id" trần, nó NUỐT luôn route tĩnh
// "/models/installed" (xoá file đĩa) → :id="installed" → query WHERE id='installed' → lỗi "invalid uuid: installed".
router.delete("/models/:id([0-9a-fA-F-]{36})", sessionAuth, requireAdmin, async (req, res) => {
  try {
    const { rows } = await query("DELETE FROM model_files WHERE id = $1 RETURNING type, filename", [req.params.id])
    if (!rows[0]) return res.status(404).json({ error: "Không tìm thấy" })
    const fp = path.join(UPLOADS_DIR, rows[0].type, rows[0].filename)
    try { if (fs.existsSync(fp)) fs.unlinkSync(fp) } catch (e) { /* file mất rồi cũng ok */ }
    res.json({ ok: true })
  } catch (e) { res.status(500).json({ error: String(e?.message || e) }) }
})

// #region ALD 15/06/2026 - Catalog cài model on-demand (Settings → Models AI). Admin-only.
const rowToDl = (r) => ({
  id: r.id, kind: r.kind, ref: r.ref, type: r.type, status: r.status,
  totalBytes: r.total_bytes != null ? Number(r.total_bytes) : null,
  doneBytes: Number(r.done_bytes || 0),
  pct: r.total_bytes ? Math.min(100, Math.round((Number(r.done_bytes || 0) / Number(r.total_bytes)) * 100)) : null,
  error: r.error, createdAt: r.created_at, updatedAt: r.updated_at,
})

// GET /models/catalog — catalog (curated + custom) kèm trạng thái đã-cài (quét đĩa / ollama tags) + download đang chạy.
router.get("/models/catalog", sessionAuth, requireAdmin, async (_req, res) => {
  try {
    const cat = await loadCatalog()
    const comfyInstalled = getInstalledComfy()
    const ollamaInstalled = await getInstalledOllama()
    const { rows: dls } = await query("SELECT * FROM model_downloads WHERE status IN ('queued','running','error') ORDER BY created_at DESC")
    const byRef = (kind) => { const m = new Map(); for (const d of dls) if (d.kind === kind && !m.has(d.ref)) m.set(d.ref, d); return m }
    const cD = byRef("comfy"), oD = byRef("ollama")
    const comfy = cat.comfy.map((e) => {
      const sz = comfyInstalled.get(e.filename)
      const d = cD.get(e.filename)
      return { ...e, kind: "comfy", installed: sz != null, installedBytes: sz != null ? sz : null, download: d ? rowToDl(d) : null }
    })
    const ollama = cat.ollama.map((e) => {
      const sz = ollamaInstalled.get(e.model)
      const d = oD.get(e.model)
      return { ...e, kind: "ollama", installed: sz != null, installedBytes: sz != null ? sz : null, download: d ? rowToDl(d) : null }
    })
    res.json({ comfy, ollama })
  } catch (e) { res.status(500).json({ error: String(e?.message || e) }) }
})

// GET /models/downloads — danh sách tiến trình tải (UI poll khi có cái đang chạy).
router.get("/models/downloads", sessionAuth, requireAdmin, async (_req, res) => {
  try {
    const { rows } = await query("SELECT * FROM model_downloads ORDER BY created_at DESC LIMIT 100")
    res.json({ items: rows.map(rowToDl) })
  } catch (e) { res.status(500).json({ error: String(e?.message || e) }) }
})

// POST /models/catalog/install { id } — tải model theo id trong catalog.
router.post("/models/catalog/install", sessionAuth, requireAdmin, async (req, res) => {
  try {
    const id = String(req.body?.id || "").trim()
    if (!id) return res.status(400).json({ error: "Thiếu id model" })
    const cat = await loadCatalog()
    const entry = [...cat.comfy, ...cat.ollama].find((e) => e.id === id)
    if (!entry) return res.status(404).json({ error: "Không thấy model trong catalog" })
    const kind = entry.model ? "ollama" : "comfy"
    if (kind === "comfy") {
      if (!entry.url) return res.status(400).json({ error: "Model này là prebuilt (không có URL tải trực tiếp) — xem ghi chú." })
      if (!COMFY_TYPES.has(entry.type)) return res.status(400).json({ error: `Loại không hợp lệ: ${entry.type}` })
    }
    const ref = kind === "ollama" ? entry.model : entry.filename
    try {
      const row = await enqueueInstall({ kind, ref, type: entry.type, url: entry.url, catalogId: entry.id, sizeBytes: entry.sizeBytes, userId: req.session.userId })
      res.status(201).json({ download: rowToDl(row) })
    } catch (e) {
      if (/duplicate key|unique/i.test(String(e?.message))) return res.status(409).json({ error: "Model này đang được tải rồi" })
      throw e
    }
  } catch (e) { res.status(500).json({ error: String(e?.message || e) }) }
})

// POST /models/catalog/custom — admin thêm model bằng URL/tên (lưu DB) rồi tải luôn.
router.post("/models/catalog/custom", sessionAuth, requireAdmin, async (req, res) => {
  try {
    const kind = String(req.body?.kind || "comfy").toLowerCase() === "ollama" ? "ollama" : "comfy"
    const label = String(req.body?.label || "").trim().slice(0, 200) || null
    const note = String(req.body?.note || "").trim().slice(0, 300) || null
    const sizeBytes = req.body?.sizeBytes != null ? Number(req.body.sizeBytes) : null
    if (kind === "ollama") {
      const model = String(req.body?.model || "").trim()
      if (!model) return res.status(400).json({ error: "Thiếu tên model Ollama" })
      await query("INSERT INTO model_catalog_custom (kind, model, label, note, size_bytes, added_by) VALUES ('ollama',$1,$2,$3,$4,$5) ON CONFLICT DO NOTHING", [model, label, note, sizeBytes, req.session.userId])
      const row = await enqueueInstall({ kind, ref: model, sizeBytes, userId: req.session.userId })
      return res.status(201).json({ download: rowToDl(row) })
    }
    const type = String(req.body?.type || "").trim()
    const url = String(req.body?.url || "").trim()
    if (!/^https?:\/\//.test(url)) return res.status(400).json({ error: "URL không hợp lệ (phải http/https)" })
    let filename = safeName(req.body?.filename || "")
    if (!filename) { try { filename = safeName(path.basename(new URL(url).pathname)) } catch { /* noop */ } }
    if (!COMFY_TYPES.has(type)) return res.status(400).json({ error: `Loại không hợp lệ: '${type}'` })
    if (!filename) return res.status(400).json({ error: "Không xác định được tên file (nhập 'filename')" })
    const ext = path.extname(filename).toLowerCase()
    if (!ALLOWED_EXT.has(ext)) return res.status(400).json({ error: `Đuôi file không hỗ trợ: ${ext || "(không có)"}` })
    await query("INSERT INTO model_catalog_custom (kind, type, filename, url, label, note, size_bytes, added_by) VALUES ('comfy',$1,$2,$3,$4,$5,$6,$7) ON CONFLICT DO NOTHING", [type, filename, url, label, note, sizeBytes, req.session.userId])
    try {
      const row = await enqueueInstall({ kind: "comfy", ref: filename, type, url, sizeBytes, userId: req.session.userId })
      res.status(201).json({ download: rowToDl(row) })
    } catch (e) {
      if (/duplicate key|unique/i.test(String(e?.message))) return res.status(409).json({ error: "Model này đang được tải rồi" })
      throw e
    }
  } catch (e) { res.status(500).json({ error: String(e?.message || e) }) }
})

// DELETE /models/downloads/:id — cancel nếu đang chạy; nếu đã xong/lỗi thì xoá khỏi lịch sử.
router.delete("/models/downloads/:id", sessionAuth, requireAdmin, async (req, res) => {
  try {
    const id = req.params.id
    const killed = cancelDownload(id)
    if (!killed) {
      await query("UPDATE model_downloads SET status='cancelled', updated_at=now() WHERE id=$1 AND status='queued'", [id])
      await query("DELETE FROM model_downloads WHERE id=$1 AND status IN ('done','error','cancelled')", [id])
    }
    res.json({ ok: true, cancelled: killed })
  } catch (e) { res.status(500).json({ error: String(e?.message || e) }) }
})

// DELETE /models/installed { kind, filename | model } — xoá file ComfyUI trên đĩa / xoá model Ollama.
router.delete("/models/installed", sessionAuth, requireAdmin, async (req, res) => {
  try {
    const kind = String(req.body?.kind || "comfy").toLowerCase()
    if (kind === "ollama") {
      const model = String(req.body?.model || "").trim()
      if (!model) return res.status(400).json({ error: "Thiếu tên model" })
      const ok = await deleteOllama(model)
      return ok ? res.json({ ok: true }) : res.status(502).json({ error: "Ollama delete thất bại" })
    }
    const filename = safeName(req.body?.filename || "")
    if (!filename) return res.status(400).json({ error: "Thiếu filename" })
    const removed = removeComfyFile(filename)
    if (!removed) return res.status(404).json({ error: "Không thấy file trên đĩa" })
    res.json({ ok: true, path: removed })
  } catch (e) { res.status(500).json({ error: String(e?.message || e) }) }
})
// #endregion

export default router
// #endregion
