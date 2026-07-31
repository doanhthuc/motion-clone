// #region ALD 13/06/2026 - Thư viện "Giọng nói" (voice library). Admin upload file mẫu MP3/WAV →
//   lưu MinIO (voices/<id>.<ext>) + 1 dòng DB. viXTTS clone giọng TỪ file mẫu lúc TTS (không clone trước),
//   nên "đăng ký giọng" = lưu audio + row. Mọi node có giọng (talk/teaser/story-film) chọn từ list này.
//   GET    /voices                                          (session — MỌI user, để picker dùng được) → { items }
//   POST   /voices            multipart {file,name,gender}  (admin)                                    → { item }
//   DELETE /voices/:id                                      (admin)                                    → { ok }
//   GET    /voices/:id/audio                                (session) → stream ref audio (nút ▶ nghe thử)
//   GET    /worker/voices/:id                               (worker token) → { id, key } (worker tải qua /files/<key>)
import { Router } from "express"
import multer from "multer"
import crypto from "node:crypto"
import { spawn } from "node:child_process"
import os from "node:os"
import fs from "node:fs"
import path from "node:path"
import { query } from "../db.js"
import { putObject, getObjectStream, deleteObject, browserUrl } from "../storage.js"
import { sessionAuth, requireAdmin } from "../auth/session.js"
import { workerAuth } from "../auth.js"

const upload = multer({ storage: multer.memoryStorage(), limits: { fileSize: 50 * 1024 * 1024, files: 1 } })
const router = Router()

const GENDERS = new Set(["male", "female", "unknown"])
const extOf = (n, fb) => { const i = (n || "").lastIndexOf("."); return i >= 0 ? n.slice(i + 1).toLowerCase() : fb }

// ffprobe duration (giây) — best effort, lỗi thì trả null (không chặn upload). Giống audio.js.
function probeDuration(buffer, ext) {
  return new Promise((resolve) => {
    let tmp
    try {
      tmp = path.join(os.tmpdir(), `voice-${crypto.randomUUID()}.${ext || "bin"}`)
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

async function rowToItem(r) {
  return {
    id: r.id, name: r.name, gender: r.gender || "unknown",
    durationSec: r.duration_sec != null ? Number(r.duration_sec) : null,
    sizeBytes: r.size_bytes != null ? Number(r.size_bytes) : null,
    mime: r.mime, isActive: r.is_active, createdAt: r.created_at,
    audioUrl: await browserUrl(r.storage_key),   // <audio> nút ▶ nghe thử (HMAC qua /media, không cần header)
  }
}

// GET /voices — list giọng ACTIVE. MỞ cho MỌI user authed (picker talk/teaser/story-film cần).
router.get("/voices", sessionAuth, async (_req, res) => {
  try {
    const { rows } = await query("SELECT * FROM voices WHERE is_active = true ORDER BY created_at DESC")
    res.json({ items: await Promise.all(rows.map(rowToItem)) })
  } catch (e) { res.status(500).json({ error: String(e?.message || e) }) }
})

// POST /voices — upload file mẫu MP3/WAV (admin). KHÔNG gọi viXTTS — audio CHÍNH LÀ ref để clone sau.
router.post("/voices", sessionAuth, requireAdmin, upload.single("file"), async (req, res) => {
  try {
    if (!req.file) return res.status(400).json({ error: "Thiếu file âm thanh (field 'file')" })
    const name = String(req.body.name || "").trim().slice(0, 120)
    if (!name) return res.status(400).json({ error: "Thiếu tên giọng (field 'name')" })
    let gender = String(req.body.gender || "unknown").toLowerCase().trim()
    if (!GENDERS.has(gender)) gender = "unknown"

    const id = crypto.randomUUID()
    const ext = extOf(req.file.originalname, "mp3")
    const key = `voices/${id}.${ext}`
    await putObject(key, req.file.buffer, req.file.mimetype || "audio/mpeg")
    const duration = await probeDuration(req.file.buffer, ext)
    const { rows } = await query(
      `INSERT INTO voices (id, user_id, name, storage_key, mime, size_bytes, duration_sec, gender)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING *`,
      [id, req.session.userId, name, key, req.file.mimetype || null, req.file.size, duration, gender])
    res.status(201).json({ item: await rowToItem(rows[0]) })
  } catch (e) { res.status(500).json({ error: String(e?.message || e) }) }
})

// DELETE /voices/:id — hard-delete row + xóa object MinIO (giống model-refs) (admin).
router.delete("/voices/:id", sessionAuth, requireAdmin, async (req, res) => {
  try {
    const { rows } = await query("DELETE FROM voices WHERE id = $1 RETURNING storage_key", [req.params.id])
    if (!rows[0]) return res.status(404).json({ error: "Không tìm thấy" })
    await deleteObject(rows[0].storage_key).catch(() => {})
    res.json({ ok: true })
  } catch (e) { res.status(500).json({ error: String(e?.message || e) }) }
})

// GET /voices/:id/audio — stream ref audio (nút ▶) (session). Content-Type theo mime đã lưu.
router.get("/voices/:id/audio", sessionAuth, async (req, res) => {
  try {
    const { rows } = await query("SELECT storage_key, mime FROM voices WHERE id = $1", [req.params.id])
    if (!rows[0]) return res.status(404).json({ error: "Không tìm thấy" })
    const { stream, contentType, length } = await getObjectStream(rows[0].storage_key)
    res.set("content-type", contentType || rows[0].mime || "audio/mpeg")
    if (length) res.set("content-length", String(length))
    stream.pipe(res)
  } catch (e) { res.status(404).json({ error: "Không tải được: " + String(e?.message || e) }) }
})

// GET /worker/voices/:id — worker resolve id → storage_key (rồi tải qua /files/<key>) cho TTS viXTTS.
router.get("/worker/voices/:id", workerAuth, async (req, res) => {
  try {
    const { rows } = await query("SELECT id, storage_key FROM voices WHERE id = $1 AND is_active = true", [req.params.id])
    if (!rows[0]) return res.status(404).json({ error: "Không tìm thấy" })
    res.json({ id: rows[0].id, key: rows[0].storage_key })
  } catch (e) { res.status(500).json({ error: String(e?.message || e) }) }
})

export default router
// #endregion
