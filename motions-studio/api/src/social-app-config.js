// #region ALD 05/07/2026 - Credentials App Facebook/TikTok + Public Base URL: CHỈ đọc từ DB (social_app_config,
// nhập qua UI Social Management > Help). KHÔNG fallback .env — key/secret/domain luôn do user nhập tay qua UI,
// không được phép đặt sẵn trong file server (xem .env.example: 4 biến FACEBOOK_APP_*/TIKTOK_CLIENT_* đã bỏ hẳn
// khỏi đó, và process.env.PUBLIC_BASE_URL không còn dùng cho tính redirect URI OAuth nữa — chỉ còn phục vụ
// mục đích khác không liên quan Social Management, xem api/src/storage.js).
// Dùng chung cho routes/social-accounts.js (connect-url/callback) + social-publish.js (refresh TikTok token).
import { query } from "./db.js"

export async function getAppCredentials() {
  const row = (await query("SELECT * FROM social_app_config WHERE id=1")).rows[0] || {}
  return {
    facebookAppId: row.facebook_app_id || "",
    facebookAppSecret: row.facebook_app_secret || "",
    tiktokClientKey: row.tiktok_client_key || "",
    tiktokClientSecret: row.tiktok_client_secret || "",
    publicBaseUrl: String(row.public_base_url || "").replace(/\/$/, ""),
  }
}

// Hàm thuần (không tự query DB) — truyền publicBaseUrl đã lấy sẵn từ getAppCredentials() vào để tránh query lặp.
export function redirectUriFor(platform, publicBaseUrl) {
  const base = String(publicBaseUrl || "").replace(/\/$/, "")
  return base ? `${base}/social-accounts/${platform}/callback` : ""
}
// #endregion
