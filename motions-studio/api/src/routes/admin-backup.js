// #region ALD 13/06/2026 - Backup & Restore toàn bộ motion-backend (admin).
// Bối cảnh: anh thuê VPS, hay set up lại từ đầu → cần 1-click backup ra MỘT file .zip (chứa CẢ nội dung
// Postgres LẪN toàn bộ file MinIO) tải về máy, và restore lại lên VPS mới.
//   GET  /admin/backup   (admin)  → tải motion-backup-<YYYYMMDD-HHmmss>.zip
//        Cấu trúc zip:
//          db.json                      = { version, exported_at, tables: { <table>: [rows...] } }
//          storage/<bucket>/<key>       = mọi object trong MinIO (kể cả output job)
//   POST /admin/restore  (admin)  multipart {file=.zip}  → { restored:{tables,files}, errors:[...] }
//        Restore là UPSERT (INSERT ... ON CONFLICT (id) DO UPDATE) — MERGE theo primary key, KHÔNG XÓA
//        dữ liệu sẵn có. File MinIO ghi đè lại bằng putObject.
//
// Lưu ý kỹ thuật:
//  - Mỗi router tự gắn auth nội bộ (giống admin-vps.js) vì server.js mount KHÔNG bọc middleware.
//  - Bảng ephemeral (workers / user_sessions / otp_codes / ocr_cache) CỐ TÌNH bỏ qua: tái tạo runtime.
//  - MinIO chỉ có 1 bucket vật lý (STORAGE_BUCKET, mặc định "motion"); cột `bucket` trong storage_files là
//    bucket LOGIC (prefix), không phải bucket S3 riêng → backup gói theo bucket vật lý để restore đúng key.
import { Router } from "express"
import multer from "multer"
import archiver from "archiver"
import unzipper from "unzipper"
import { query } from "../db.js"
import { putObject, getObjectStream, listAllObjects, getBucketName } from "../storage.js"
import { sessionAuth, requireAdmin } from "../auth/session.js"

// Restore zip có thể lớn (gồm cả file MinIO) → cho 2GB, lưu RAM (parse bằng unzipper.Open.buffer).
const upload = multer({ storage: multer.memoryStorage(), limits: { fileSize: 2 * 1024 * 1024 * 1024, files: 1 } })
const router = Router()

// Tất cả route ở đây cần đăng nhập + quyền admin (giống admin-vps.js).
router.use(["/admin/backup", "/admin/restore"], sessionAuth, requireAdmin)

// #region ALD 13/06/2026 - Bảng + cột thật (theo db/init/*.sql). PK = id cho TẤT CẢ bảng dump.
// Liệt kê cột tường minh để INSERT/UPSERT ổn định kể cả khi thứ tự cột DB đổi.
// Bỏ qua bảng ephemeral: workers, user_sessions, otp_codes, ocr_cache.
const TABLES = [
  { name: "users",            pk: "id", cols: ["id", "email", "full_name", "role", "is_active", "department", "title", "hr_code", "avatar_url", "last_login_at", "created_at"] },
  { name: "api_keys",         pk: "id", cols: ["id", "key", "user_id", "is_active", "expires_at", "revoked_at", "last_used_at", "created_at"] },
  { name: "ai_provider_keys", pk: "id", cols: ["id", "user_id", "provider", "api_key", "base_url", "default_model", "is_active", "created_at", "updated_at"] },
  { name: "workflows",        pk: "id", cols: ["id", "user_id", "slug", "name", "description", "definition", "is_public", "is_active", "async_enabled", "callback_url", "callback_headers", "created_at", "updated_at"] },
  { name: "workflow_runs",    pk: "id", cols: ["id", "workflow_id", "user_id", "auth_method", "status", "input", "output", "events", "definition", "error_msg", "started_at", "finished_at"] },
  { name: "model_refs",       pk: "id", cols: ["id", "gender", "age_group", "label", "storage_key", "orig_key", "mime", "size_bytes", "width", "height", "is_active", "user_id", "created_at"] },
  { name: "audio_files",      pk: "id", cols: ["id", "name", "storage_key", "size_bytes", "duration_sec", "mime", "client_id", "created_at"] },
  { name: "voices",           pk: "id", cols: ["id", "user_id", "name", "storage_key", "mime", "size_bytes", "duration_sec", "gender", "is_active", "created_at"] },
  { name: "storage_files",    pk: "id", cols: ["id", "bucket", "path", "storage_key", "name", "size_bytes", "mime", "user_id", "created_at"] },
  { name: "jobs",             pk: "id", cols: ["id", "type", "status", "progress", "current_step", "inputs", "params", "output_key", "error", "worker_id", "client_id", "created_at", "updated_at", "started_at", "finished_at", "logs", "previews", "outputs"] },
]
const TABLE_MAP = new Map(TABLES.map((t) => [t.name, t]))
// #endregion

// Tên file zip theo thời điểm: motion-backup-YYYYMMDD-HHmmss.zip
function backupFilename() {
  const d = new Date()
  const p = (n) => String(n).padStart(2, "0")
  const ts = `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`
  return `motion-backup-${ts}.zip`
}

// GET /admin/backup — gói db.json + toàn bộ MinIO → stream .zip về client.
router.get("/admin/backup", async (req, res) => {
  // 1) Dump DB trước (cần trọn vẹn trước khi mở stream — lỗi DB thì trả 500 sạch sẽ).
  let dbDump
  try {
    const tables = {}
    const warnings = []
    for (const t of TABLES) {
      // ALD 13/06/2026 - Chống DRIFT cột: DB trên VPS có thể THIẾU cột so với code (vd jobs.previews/outputs chưa
      // migrate) → SELECT cột cứng sẽ ném lỗi & hỏng CẢ backup. Thử cột khai báo trước; lỗi → fallback SELECT *
      // (lấy đúng cột đang có); vẫn lỗi (bảng chưa tồn tại) → bỏ qua bảng đó + ghi cảnh báo, KHÔNG hỏng backup.
      try {
        const { rows } = await query(`SELECT ${t.cols.join(", ")} FROM ${t.name}`)
        tables[t.name] = rows
      } catch (e1) {
        try {
          const { rows } = await query(`SELECT * FROM ${t.name}`)
          tables[t.name] = rows
          warnings.push(`${t.name}: thiếu cột so với code → đã dump bằng SELECT * (${String(e1?.message || e1).slice(0, 120)})`)
        } catch (e2) {
          tables[t.name] = []
          warnings.push(`${t.name}: BỎ QUA (không đọc được: ${String(e2?.message || e2).slice(0, 120)})`)
        }
      }
    }
    dbDump = { version: 1, exported_at: new Date().toISOString(), tables, warnings }
  } catch (e) {
    return res.status(500).json({ error: "Dump DB thất bại: " + String(e?.message || e) })
  }

  // 2) Liệt kê mọi object MinIO (kể cả output job ngoài storage_files).
  let objects
  try {
    objects = await listAllObjects()
  } catch (e) {
    return res.status(500).json({ error: "Liệt kê MinIO thất bại: " + String(e?.message || e) })
  }

  // 3) Mở zip stream về client.
  const bucket = getBucketName()
  res.setHeader("Content-Type", "application/zip")
  res.setHeader("Content-Disposition", `attachment; filename="${backupFilename()}"`)

  const archive = archiver("zip", { zlib: { level: 6 } })
  let failed = false
  archive.on("warning", (err) => { console.warn("[admin-backup] archiver warning:", err?.message || err) })
  archive.on("error", (err) => {
    failed = true
    console.error("[admin-backup] archiver error:", err)
    // Nếu chưa gửi byte nào thì còn kịp trả 500; nếu đã stream thì chỉ phá kết nối.
    if (!res.headersSent) res.status(500).json({ error: String(err?.message || err) })
    else res.destroy(err)
  })
  // Client hủy tải → bỏ dở archive cho gọn.
  req.on("close", () => { if (!res.writableEnded) archive.abort?.() })

  archive.pipe(res)

  // Hàm phụ: append 1 source (string hoặc stream) rồi CHỜ archiver tiêu thụ xong entry name tương ứng
  // mới trả về. Đợi đúng entry theo `name` (không nhầm với entry trước đó) → mở stream S3 TUẦN TỰ, giữ
  // bộ nhớ/socket thấp. archiver emit 'entry' kèm data.name cho mỗi entry đã xử lý.
  function appendAndWait(source, name) {
    return new Promise((resolve, reject) => {
      const onEntry = (data) => { if (data?.name === name) { cleanup(); resolve() } }
      const onError = (err) => { cleanup(); reject(err) }
      const cleanup = () => { archive.removeListener("entry", onEntry); archive.removeListener("error", onError) }
      archive.on("entry", onEntry)
      archive.on("error", onError)
      archive.append(source, { name })
    })
  }

  try {
    // db.json — toàn bộ nội dung Postgres.
    await appendAndWait(JSON.stringify(dbDump), "db.json")

    // Mỗi object MinIO → storage/<bucket>/<key>. Mở stream S3 LẦN LƯỢT (không mở đồng loạt).
    for (const o of objects) {
      if (failed) break
      if (!o.key) continue
      const name = `storage/${bucket}/${o.key}`
      try {
        const { stream } = await getObjectStream(o.key)
        await appendAndWait(stream, name)
      } catch (e) {
        // 1 object lỗi (đã xóa / hỏng) KHÔNG được làm vỡ cả backup → bỏ qua, ghi log.
        console.warn(`[admin-backup] bỏ qua object lỗi ${o.key}:`, e?.message || e)
      }
    }
    if (!failed) await archive.finalize()
  } catch (e) {
    console.error("[admin-backup] backup lỗi:", e)
    if (!res.headersSent) res.status(500).json({ error: String(e?.message || e) })
    else res.destroy(e)
  }
})

// Build câu UPSERT: INSERT (cols) VALUES ($1..) ON CONFLICT (pk) DO UPDATE SET <các cột != pk> = EXCLUDED.*
function buildUpsert(t) {
  const placeholders = t.cols.map((_, i) => `$${i + 1}`).join(", ")
  const updates = t.cols.filter((c) => c !== t.pk).map((c) => `${c} = EXCLUDED.${c}`).join(", ")
  return `INSERT INTO ${t.name} (${t.cols.join(", ")}) VALUES (${placeholders})
          ON CONFLICT (${t.pk}) DO UPDATE SET ${updates}`
}

// jsonb/array cần stringify; còn lại để pg tự bind. undefined → null.
function bindValue(v) {
  if (v === undefined) return null
  if (v !== null && typeof v === "object") return JSON.stringify(v)
  return v
}

// POST /admin/restore — nhận .zip (multipart field "file"), UPSERT DB + ghi lại file MinIO. KHÔNG XÓA gì.
router.post("/admin/restore", upload.single("file"), async (req, res) => {
  if (!req.file?.buffer?.length) return res.status(400).json({ error: "Thiếu file .zip (field 'file')" })

  const result = { restored: { tables: {}, files: 0 }, errors: [] }
  let directory
  try {
    directory = await unzipper.Open.buffer(req.file.buffer)
  } catch (e) {
    return res.status(400).json({ error: "File .zip không hợp lệ: " + String(e?.message || e) })
  }

  // Tách entry db.json vs storage/* .
  const dbEntry = directory.files.find((f) => f.path === "db.json" && f.type === "File")
  const fileEntries = directory.files.filter((f) => f.type === "File" && f.path.startsWith("storage/"))

  // 1) UPSERT DB từ db.json.
  if (dbEntry) {
    let dump
    try {
      dump = JSON.parse((await dbEntry.buffer()).toString("utf8"))
    } catch (e) {
      result.errors.push({ scope: "db.json", error: "Parse JSON lỗi: " + String(e?.message || e) })
    }
    const tables = dump?.tables || {}
    for (const tableName of Object.keys(tables)) {
      const t = TABLE_MAP.get(tableName)
      if (!t) { result.errors.push({ scope: tableName, error: "Bảng không nằm trong danh sách restore — bỏ qua" }); continue }
      const rows = Array.isArray(tables[tableName]) ? tables[tableName] : []
      const sql = buildUpsert(t)
      let ok = 0
      for (const row of rows) {
        try {
          const args = t.cols.map((c) => bindValue(row[c]))
          await query(sql, args)
          ok++
        } catch (e) {
          // 1 dòng hỏng KHÔNG được làm vỡ cả restore → gom vào errors[].
          result.errors.push({ scope: `${tableName}#${row?.[t.pk] ?? "?"}`, error: String(e?.message || e) })
        }
      }
      result.restored.tables[tableName] = ok
    }
  } else {
    result.errors.push({ scope: "db.json", error: "Không tìm thấy db.json trong zip" })
  }

  // 2) Ghi lại file MinIO. Entry name = storage/<bucket>/<key...> → bỏ "storage/<bucket>/" lấy key gốc,
  //    putObject ghi vào bucket vật lý hiện tại (key giữ nguyên nên khớp với storage_key trong DB).
  for (const f of fileEntries) {
    try {
      const rest = f.path.slice("storage/".length)       // "<bucket>/<key...>"
      const slash = rest.indexOf("/")
      if (slash < 0) continue
      const key = rest.slice(slash + 1)                  // "<key...>"
      if (!key) continue
      const buf = await f.buffer()
      await putObject(key, buf)
      result.restored.files++
    } catch (e) {
      result.errors.push({ scope: f.path, error: String(e?.message || e) })
    }
  }

  res.json(result)
})

export default router
// #endregion
