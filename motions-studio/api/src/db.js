import pg from "pg"

const pool = new pg.Pool({
  connectionString: process.env.DATABASE_URL,
  max: 10,
  idleTimeoutMillis: 30000,
})

pool.on("error", (e) => console.error("[db] pool error:", e.message))

export const query = (text, params) => pool.query(text, params)

// Chạy một nhóm truy vấn trên cùng một PostgreSQL session. Cần cho advisory lock
// của migration khi nhiều process PM2 khởi động đồng thời.
export async function withDbClient(fn) {
  const client = await pool.connect()
  try { return await fn(client) }
  finally { client.release() }
}

// Chờ Postgres sẵn sàng (compose có healthcheck nhưng retry cho chắc khi cold start).
export async function waitForDb(retries = 30) {
  for (let i = 0; i < retries; i++) {
    try { await pool.query("SELECT 1"); return }
    catch { await new Promise((r) => setTimeout(r, 2000)) }
  }
  throw new Error("Postgres không sẵn sàng sau nhiều lần thử")
}
