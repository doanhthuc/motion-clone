// #region ALD 04/06/2026 - Tự chạy migration idempotent lúc khởi động (api + wf-worker).
// VẤN ĐỀ: Postgres CHỈ chạy /docker-entrypoint-initdb.d (mount từ db/init) khi data dir TRỐNG. Container
// chạy lại trên volume CŨ → KHÔNG re-run init → cột/bảng mới (vd jobs.previews) bị thiếu → app vỡ
// "column ... does not exist". Trước đây phải apply tay qua psql hoặc dựa vào CI deploy.
// GIẢI PHÁP: đọc db/init/*.sql (đã idempotent: IF NOT EXISTS / ON CONFLICT) chạy tuần tự mỗi lần boot →
// mọi `docker compose up` (volume mới HAY cũ) đều có schema mới nhất. Lỗi 1 file chỉ log, KHÔNG chặn boot.
// Seed workflow KHÔNG được auto chạy lúc boot/deploy. File seed nằm ở db/seeds và chỉ chạy thủ công bằng script.
import { readdir, readFile } from "node:fs/promises"
import { fileURLToPath } from "node:url"
import { dirname, join } from "node:path"
import { withDbClient } from "./db.js"

// migrate.js ở api/src. Docker image COPY db → api/db/init, còn bản native PM2 giữ
// db/init ở repository root. Hỗ trợ cả hai layout để deploy VPS không âm thầm bỏ migration.
const SRC_DIR = dirname(fileURLToPath(import.meta.url))
const INIT_DIR_CANDIDATES = [
  join(SRC_DIR, "..", "db", "init"),
  join(SRC_DIR, "..", "..", "db", "init"),
]

export async function runMigrations() {
  let files = null
  let initDir = null
  let lastError = null
  for (const candidate of INIT_DIR_CANDIDATES) {
    try {
      files = (await readdir(candidate)).filter((f) => f.endsWith(".sql")).sort()
      initDir = candidate
      break
    } catch (error) {
      lastError = error
    }
  }
  if (!files || !initDir) {
    console.warn("[migrate] bỏ qua (không đọc được db/init):", lastError?.message || lastError)
    return
  }
  files = files.filter((f) => !f.toLowerCase().includes("seed"))
  await withDbClient(async (client) => {
    // API, wf-worker và task-cloud-auto có thể boot cùng lúc. Serialize migration
    // theo PostgreSQL session để CREATE TABLE IF NOT EXISTS không race.
    await client.query("SELECT pg_advisory_lock($1)", [20260716])
    try {
      for (const f of files) {
        try {
          const sql = await readFile(join(initDir, f), "utf8")
          await client.query(sql)  // pg simple-query: chạy được nhiều statement/file (không tham số)
          console.log(`[migrate] ${f} ✓`)
        } catch (e) {
          console.warn(`[migrate] ${f} lỗi (bỏ qua): ${e?.message || e}`)
        }
      }
    } finally {
      await client.query("SELECT pg_advisory_unlock($1)", [20260716])
    }
  })
}
// #endregion
