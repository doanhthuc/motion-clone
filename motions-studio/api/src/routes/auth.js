// #region ALD 31/05/2026 - Auth routes (port send-otp/verify-otp/refresh/logout).
//   POST /auth/send-otp    { email }                 → { success }
//   POST /auth/verify-otp  { email, code }           → { success, token, refreshToken, user }
//   POST /auth/refresh     { refreshToken }          → { success, token }
//   POST /auth/logout      { refreshToken }          → { success }
// OTP lưu Postgres (otp_codes). Email qua nodemailer SMTP.
import { Router } from "express"
import { query } from "../db.js"
import { signSession, verifySession, hashToken, generateRefreshToken } from "../auth/jwt.js"
import { sendOtpEmail } from "../auth/mailer.js"

const router = Router()
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
const norm = (e) => String(e || "").trim().toLowerCase()
const OTP_TTL = 300, RATE_WINDOW = 3600, MAX_PER_HOUR = 5, DEDUPE = 3
const genOtp = () => Math.floor(100000 + Math.random() * 900000).toString()

router.post("/auth/send-otp", async (req, res) => {
  try {
    const email = norm(req.body?.email)
    if (!email || !EMAIL_RE.test(email)) return res.status(400).json({ error: "Email không hợp lệ" })

    const u = await query("SELECT id, is_active FROM users WHERE email = $1", [email])
    if (!u.rows[0]) return res.status(404).json({ error: "Email chưa được đăng ký", code: "USER_NOT_FOUND" })
    if (u.rows[0].is_active === false) return res.status(403).json({ error: "Tài khoản đã bị khóa", code: "ACCOUNT_INACTIVE" })

    const cur = await query("SELECT code, expires_at, sent_count, window_start, created_at FROM otp_codes WHERE email = $1", [email])
    const row = cur.rows[0]
    const now = Date.now()
    // Dedupe 3s
    if (row && now - new Date(row.created_at).getTime() < DEDUPE * 1000) {
      return res.json({ success: true, message: "OTP đã được gửi" })
    }
    const isResend = row && new Date(row.expires_at).getTime() > now
    // Rate limit (chỉ tính fresh send)
    let sentCount = 1, windowStart = new Date()
    if (row) {
      const inWindow = now - new Date(row.window_start).getTime() < RATE_WINDOW * 1000
      if (!isResend && inWindow && row.sent_count >= MAX_PER_HOUR) {
        return res.status(429).json({ error: "Quá nhiều yêu cầu OTP. Thử lại sau 1 giờ.", code: "RATE_LIMITED" })
      }
      if (inWindow) { windowStart = new Date(row.window_start); sentCount = row.sent_count + (isResend ? 0 : 1) }
    }

    const otp = genOtp()
    const expires = new Date(now + OTP_TTL * 1000)
    await query(
      `INSERT INTO otp_codes (email, code, expires_at, sent_count, window_start, created_at)
       VALUES ($1,$2,$3,$4,$5, now())
       ON CONFLICT (email) DO UPDATE SET code=$2, expires_at=$3, sent_count=$4, window_start=$5, created_at=now()`,
      [email, otp, expires, sentCount, windowStart])

    try { await sendOtpEmail({ to: email, otp }) }
    catch (e) { console.error("[auth] SMTP fail:", e?.message || e); return res.status(503).json({ error: "Không gửi được email.", code: "SMTP_ERROR" }) }
    res.json({ success: true, message: "OTP đã được gửi" })
  } catch (e) { res.status(500).json({ error: String(e?.message || e) }) }
})

router.post("/auth/verify-otp", async (req, res) => {
  try {
    const email = norm(req.body?.email)
    const code = String(req.body?.code || "").trim()
    if (!email || !code) return res.status(400).json({ error: "Email và mã OTP là bắt buộc" })
    if (!/^\d{6}$/.test(code)) return res.status(400).json({ error: "Mã OTP phải là 6 chữ số" })

    const o = await query("SELECT code, expires_at FROM otp_codes WHERE email = $1", [email])
    const row = o.rows[0]
    if (!row || new Date(row.expires_at).getTime() < Date.now())
      return res.status(400).json({ error: "Mã OTP đã hết hạn.", code: "OTP_EXPIRED" })
    if (String(row.code).trim() !== code) return res.status(400).json({ error: "Mã OTP không đúng", code: "OTP_INVALID" })

    const u = await query(
      "SELECT id, email, full_name, role, is_active, department, title, hr_code, avatar_url FROM users WHERE email = $1", [email])
    const user = u.rows[0]
    if (!user) return res.status(404).json({ error: "Tài khoản không tồn tại", code: "USER_NOT_FOUND" })
    if (user.is_active === false) return res.status(403).json({ error: "Tài khoản đã bị khóa", code: "ACCOUNT_INACTIVE" })

    await query("DELETE FROM otp_codes WHERE email = $1", [email])

    const accessTtl = Number(process.env.ACCESS_TTL_SECONDS || 3600)
    const refreshTtl = Number(process.env.REFRESH_TTL_SECONDS || 2592000)
    const exp = Math.floor(Date.now() / 1000) + accessTtl
    const refreshExp = Math.floor(Date.now() / 1000) + refreshTtl
    const token = signSession({ sub: user.id, email: user.email, role: user.role, exp })
    const refreshToken = generateRefreshToken()
    const ip = (req.get("x-forwarded-for") || "").split(",")[0].trim() || null

    await query(
      `INSERT INTO user_sessions (user_id, token_hash, refresh_token_hash, user_agent, ip, expires_at, refresh_expires_at)
       VALUES ($1,$2,$3,$4,$5,$6,$7)`,
      [user.id, hashToken(token), hashToken(refreshToken), req.get("user-agent") || null, ip,
       new Date(exp * 1000), new Date(refreshExp * 1000)])
    query("UPDATE users SET last_login_at = now() WHERE id = $1", [user.id]).catch(() => {})

    res.json({
      success: true, token, refreshToken,
      expiresAt: new Date(exp * 1000).toISOString(),
      refreshExpiresAt: new Date(refreshExp * 1000).toISOString(),
      user: { id: user.id, email: user.email, fullName: user.full_name, role: user.role,
              department: user.department, title: user.title, hrCode: user.hr_code, avatarUrl: user.avatar_url },
    })
  } catch (e) { res.status(500).json({ error: String(e?.message || e) }) }
})

router.post("/auth/refresh", async (req, res) => {
  try {
    const rt = String(req.body?.refreshToken || "")
    if (!rt) return res.status(400).json({ error: "Thiếu refreshToken" })
    const { rows } = await query(
      `SELECT s.id, s.user_id, s.revoked_at, s.refresh_expires_at, u.email, u.role
       FROM user_sessions s JOIN users u ON u.id = s.user_id WHERE s.refresh_token_hash = $1`, [hashToken(rt)])
    const s = rows[0]
    if (!s || s.revoked_at || (s.refresh_expires_at && new Date(s.refresh_expires_at).getTime() < Date.now()))
      return res.status(401).json({ error: "Refresh token hết hạn", code: "REFRESH_EXPIRED" })

    const accessTtl = Number(process.env.ACCESS_TTL_SECONDS || 3600)
    const exp = Math.floor(Date.now() / 1000) + accessTtl
    const token = signSession({ sub: s.user_id, email: s.email, role: s.role, exp })
    await query("UPDATE user_sessions SET token_hash = $1, expires_at = $2, last_seen_at = now() WHERE id = $3",
      [hashToken(token), new Date(exp * 1000), s.id])
    res.json({ success: true, token, expiresAt: new Date(exp * 1000).toISOString() })
  } catch (e) { res.status(500).json({ error: String(e?.message || e) }) }
})

router.post("/auth/logout", async (req, res) => {
  try {
    const rt = String(req.body?.refreshToken || "")
    if (rt) await query("UPDATE user_sessions SET revoked_at = now() WHERE refresh_token_hash = $1 AND revoked_at IS NULL", [hashToken(rt)])
    res.json({ success: true })
  } catch (e) { res.status(500).json({ error: String(e?.message || e) }) }
})

export default router
// #endregion
