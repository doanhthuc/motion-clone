// #region ALD 31/05/2026 - extractSession + middleware (port từ _shared/session.ts).
// Ưu tiên: x-api-key → Authorization Bearer (session JWT) → ?token= (cho SSE/EventSource).
import { query } from "../db.js"
import { verifySession, hashToken } from "./jwt.js"

export async function extractSession(req) {
  // Path 1: API key
  const apiKey = (req.get("x-api-key") || "").trim()
  if (apiKey) {
    const { rows } = await query(
      `SELECT k.id, k.is_active, k.expires_at, k.revoked_at, u.id AS uid, u.email, u.role, u.is_active AS u_active
       FROM api_keys k JOIN users u ON u.id = k.user_id WHERE k.key = $1`, [apiKey])
    const r = rows[0]
    if (!r || !r.is_active || r.revoked_at || !r.u_active) return null
    if (r.expires_at && new Date(r.expires_at).getTime() < Date.now()) return null
    query("UPDATE api_keys SET last_used_at = now() WHERE id = $1", [r.id]).catch(() => {})
    return { userId: r.uid, email: r.email, role: r.role || "staff", authMethod: "apikey" }
  }

  // Path 2: session JWT (header Bearer / x-session-token / ?token=)
  const auth = (req.get("authorization") || "").replace(/^Bearer\s+/i, "")
  const token = req.get("x-session-token") || auth || req.query?.token || ""
  if (!token) return null
  const payload = verifySession(token)
  if (!payload) return null
  const { rows } = await query(
    "SELECT id, revoked_at, expires_at FROM user_sessions WHERE token_hash = $1", [hashToken(token)])
  const s = rows[0]
  if (s) {
    // Token do motion-backend phát: honor revoke/expire trong DB
    if (s.revoked_at || new Date(s.expires_at).getTime() < Date.now()) return null
    query("UPDATE user_sessions SET last_seen_at = now() WHERE id = $1", [s.id]).catch(() => {})
  } else {
    // Bridge: token ký bằng CÙNG secret (vd Supabase phát trong giai đoạn cutover) — chấp nhận
    // nếu chữ ký + exp hợp lệ VÀ user còn active. (JWT đã verify exp ở verifySession.)
    const u = await query("SELECT is_active FROM users WHERE id = $1", [payload.sub])
    if (!u.rows[0] || u.rows[0].is_active === false) return null
  }
  return { userId: payload.sub, email: payload.email, role: payload.role || "staff", authMethod: "session" }
}

// Middleware: gắn req.session, 401 nếu không hợp lệ.
export async function sessionAuth(req, res, next) {
  const s = await extractSession(req)
  if (!s) return res.status(401).json({ error: "Unauthorized", code: "NO_SESSION" })
  req.session = s
  next()
}

export function requireAdmin(req, res, next) {
  if (req.session?.role !== "admin") return res.status(403).json({ error: "Cần quyền admin" })
  next()
}
// #endregion
