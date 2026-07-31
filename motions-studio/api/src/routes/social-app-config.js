// #region ALD 05/07/2026 - Cấu hình App Facebook/TikTok + Public Base URL nhập trực tiếp qua UI (Social
// Management > Help — step by step). Ai đăng nhập cũng XEM được trạng thái (đã set chưa + redirect URI cần
// khai trên Facebook/TikTok), nhưng chỉ ADMIN được sửa (dùng chung cả team). publicBaseUrl KHÔNG còn đọc
// process.env.PUBLIC_BASE_URL nữa — 100% DB (cột social_app_config.public_base_url), theo đúng yêu cầu
// "không đưa gì vào .env": mọi giá trị cần cho luồng connect (kể cả domain redirect) đều do người dùng tự
// nhập tay trong UI, không có biến nào phải sửa file server.
//   GET /social-app-config → { facebook:{appId,hasSecret,redirectUri}, tiktok:{clientKey,hasSecret,redirectUri}, publicBaseUrl }
//   PUT /social-app-config (admin) { facebook:{appId,appSecret}, tiktok:{clientKey,clientSecret}, publicBaseUrl } → item trên
import { Router } from "express"
import { query } from "../db.js"
import { sessionAuth, requireAdmin } from "../auth/session.js"
import { redirectUriFor } from "../social-app-config.js"

const router = Router()
router.use("/social-app-config", sessionAuth)

router.get("/social-app-config", async (_req, res) => {
  const row = (await query("SELECT * FROM social_app_config WHERE id=1")).rows[0] || {}
  const publicBaseUrl = String(row.public_base_url || "").replace(/\/$/, "")
  res.json({
    publicBaseUrl,
    facebook: {
      appId: row.facebook_app_id || "",
      hasSecret: !!row.facebook_app_secret,
      redirectUri: redirectUriFor("facebook", publicBaseUrl),
    },
    tiktok: {
      clientKey: row.tiktok_client_key || "",
      hasSecret: !!row.tiktok_client_secret,
      redirectUri: redirectUriFor("tiktok", publicBaseUrl),
    },
  })
})

router.put("/social-app-config", requireAdmin, async (req, res) => {
  const b = req.body || {}
  const cur = (await query("SELECT * FROM social_app_config WHERE id=1")).rows[0] || {}
  // Secret rỗng trong request = giữ nguyên secret cũ (không bắt nhập lại mỗi lần sửa App ID).
  const facebookAppId = b.facebook?.appId != null ? String(b.facebook.appId).trim() : (cur.facebook_app_id || "")
  const facebookAppSecret = b.facebook?.appSecret ? String(b.facebook.appSecret).trim() : (cur.facebook_app_secret || "")
  const tiktokClientKey = b.tiktok?.clientKey != null ? String(b.tiktok.clientKey).trim() : (cur.tiktok_client_key || "")
  const tiktokClientSecret = b.tiktok?.clientSecret ? String(b.tiktok.clientSecret).trim() : (cur.tiktok_client_secret || "")
  const publicBaseUrl = b.publicBaseUrl != null ? String(b.publicBaseUrl).trim().replace(/\/$/, "") : String(cur.public_base_url || "")
  await query(
    `INSERT INTO social_app_config (id, facebook_app_id, facebook_app_secret, tiktok_client_key, tiktok_client_secret, public_base_url, updated_at, updated_by)
     VALUES (1,$1,$2,$3,$4,$5,now(),$6)
     ON CONFLICT (id) DO UPDATE SET
       facebook_app_id=$1, facebook_app_secret=$2, tiktok_client_key=$3, tiktok_client_secret=$4, public_base_url=$5, updated_at=now(), updated_by=$6`,
    [facebookAppId || null, facebookAppSecret || null, tiktokClientKey || null, tiktokClientSecret || null, publicBaseUrl || null, req.session.userId])
  res.json({
    publicBaseUrl,
    facebook: { appId: facebookAppId, hasSecret: !!facebookAppSecret, redirectUri: redirectUriFor("facebook", publicBaseUrl) },
    tiktok: { clientKey: tiktokClientKey, hasSecret: !!tiktokClientSecret, redirectUri: redirectUriFor("tiktok", publicBaseUrl) },
  })
})

export default router
// #endregion
