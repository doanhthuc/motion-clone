// #region ALD 05/07/2026 - Helper OAuth cho Connect Facebook Page / TikTok (node "output" đăng tự động).
// signState/verifyState: HMAC-SHA256 stateless (giống pattern jwt.js) — nhúng userId+platform (+codeVerifier
// PKCE cho TikTok) vào query `state` gửi qua OAuth dialog, KHÔNG cần bảng DB tạm lưu state. Callback không đi
// qua sessionAuth (Facebook/TikTok redirect thẳng trình duyệt, không có header Authorization) nên phải tự
// verify state để biết request thuộc user nào.
import crypto from "node:crypto"

const SECRET = process.env.SESSION_JWT_SECRET || "social-oauth-fallback-secret"

function b64url(buf) {
  return Buffer.from(buf).toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "")
}
function b64urlToBuf(s) {
  return Buffer.from(String(s).replace(/-/g, "+").replace(/_/g, "/"), "base64")
}

/** Ký state (mặc định sống 10 phút — đủ cho luồng OAuth redirect). */
export function signState(payload, ttlSec = 600) {
  const body = { ...payload, exp: Math.floor(Date.now() / 1000) + ttlSec }
  const data = b64url(JSON.stringify(body))
  const sig = b64url(crypto.createHmac("sha256", SECRET).update(data).digest())
  return `${data}.${sig}`
}

/** Verify + decode state. Trả null nếu sai chữ ký / hết hạn / malformed. */
export function verifyState(state) {
  try {
    const [data, sig] = String(state || "").split(".")
    if (!data || !sig) return null
    const expected = b64url(crypto.createHmac("sha256", SECRET).update(data).digest())
    const a = Buffer.from(sig), b = Buffer.from(expected)
    if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) return null
    const payload = JSON.parse(b64urlToBuf(data).toString())
    if (payload.exp && payload.exp < Math.floor(Date.now() / 1000)) return null
    return payload
  } catch {
    return null
  }
}

/** PKCE code_verifier/code_challenge (S256) — bắt buộc cho TikTok OAuth v2. */
export function pkcePair() {
  const verifier = b64url(crypto.randomBytes(32))
  const challenge = b64url(crypto.createHash("sha256").update(verifier).digest())
  return { verifier, challenge }
}

/** Trang HTML nhỏ trả về sau callback: báo kết quả cho popup opener rồi tự đóng. */
export function oauthResultHtml({ ok, platform, message }) {
  const title = ok ? "Kết nối thành công" : "Kết nối lỗi"
  const esc = (s) => String(s || "").replace(/[<>&]/g, (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" }[c]))
  return `<!doctype html><html><head><meta charset="utf-8"><title>${esc(title)}</title></head>
<body style="font-family:-apple-system,Segoe UI,sans-serif;text-align:center;padding-top:72px;color:#1c1c1e">
  <div style="font-size:32px">${ok ? "✅" : "❌"}</div>
  <p style="font-weight:600;margin-top:10px">${esc(title)}${platform ? ` — ${esc(platform)}` : ""}</p>
  <p style="color:#666;font-size:13px;max-width:420px;margin:8px auto 0">${esc(message)}</p>
  <script>
    try {
      window.opener && window.opener.postMessage({ source: "social-oauth", ok: ${ok ? "true" : "false"}, platform: ${JSON.stringify(platform || "")}, message: ${JSON.stringify(message || "")} }, "*");
    } catch (e) {}
    setTimeout(function () { window.close() }, ${ok ? 1200 : 5000});
  </script>
</body></html>`
}
// #endregion
