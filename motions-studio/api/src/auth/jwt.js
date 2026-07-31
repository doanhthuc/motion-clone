// #region ALD 31/05/2026 - JWT HS256 + token hash + refresh (port từ _shared/jwt.ts).
import crypto from "node:crypto"

const SECRET = process.env.SESSION_JWT_SECRET || ""
const ISS = "pebsteel-ai"

function b64url(buf) {
  return Buffer.from(buf).toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "")
}

// Sign HS256 JWT (tự ký, không cần lib) — payload: { sub, email, role, exp }
export function signSession({ sub, email, role, exp }) {
  if (!SECRET) throw new Error("Missing SESSION_JWT_SECRET")
  const header = { alg: "HS256", typ: "JWT" }
  const iat = Math.floor(Date.now() / 1000)
  const payload = { iat, iss: ISS, sub, email, role, exp }
  const data = `${b64url(JSON.stringify(header))}.${b64url(JSON.stringify(payload))}`
  const sig = crypto.createHmac("sha256", SECRET).update(data).digest("base64")
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "")
  return `${data}.${sig}`
}

export function verifySession(token) {
  try {
    if (!SECRET || !token) return null
    const [h, p, sig] = token.split(".")
    if (!h || !p || !sig) return null
    const expected = crypto.createHmac("sha256", SECRET).update(`${h}.${p}`).digest("base64")
      .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "")
    if (!crypto.timingSafeEqual(Buffer.from(sig), Buffer.from(expected))) return null
    const payload = JSON.parse(Buffer.from(p.replace(/-/g, "+").replace(/_/g, "/"), "base64").toString())
    if (payload.exp && payload.exp < Math.floor(Date.now() / 1000)) return null
    return payload
  } catch {
    return null
  }
}

export function hashToken(token) {
  return crypto.createHash("sha256").update(token).digest("hex")
}

export function generateRefreshToken() {
  return crypto.randomBytes(32).toString("hex")
}
// #endregion
