// #region ALD 05/07/2026 - Connect Facebook Page / TikTok account (OAuth) cho node "output" đăng tự động.
//   GET    /social-accounts                     (sessionAuth) → { items } (token không trả về FE)
//   DELETE /social-accounts/:id                  (sessionAuth) → soft-disconnect (is_active=false)
//   GET    /social-accounts/:platform/connect-url (sessionAuth) → { url } mở popup OAuth dialog
//   GET    /social-accounts/facebook/callback     (PUBLIC — FB redirect thẳng trình duyệt, không header)
//   GET    /social-accounts/tiktok/callback       (PUBLIC — TikTok redirect thẳng trình duyệt)
// Callback KHÔNG đi qua sessionAuth (đăng ký route callback TRƯỚC router.use(sessionAuth) bên dưới) — nhận
// diện user qua `state` đã ký HMAC (signState/verifyState, xem wf-worker/social-oauth.js).
// Domain redirect (publicBaseUrl) lấy từ getAppCredentials() (DB, social_app_config.public_base_url) —
// KHÔNG đọc process.env.PUBLIC_BASE_URL ở đây nữa, nhập qua UI Social Management > Help.
import { Router } from "express"
import { query } from "../db.js"
import { sessionAuth } from "../auth/session.js"
import { signState, verifyState, pkcePair, oauthResultHtml } from "../wf-worker/social-oauth.js"
import { getAppCredentials, redirectUriFor } from "../social-app-config.js"
import { queryTikTokCreatorInfo } from "../wf-worker/social-publish.js"

const router = Router()

const toItem = (r) => ({
  id: r.id, platform: r.platform, name: r.name, avatar_url: r.avatar_url,
  external_id: r.external_id, is_active: r.is_active, connected_at: r.created_at,
})

// ── Callbacks (PUBLIC — đăng ký TRƯỚC sessionAuth) ──────────────────────────────────────────────

router.get("/social-accounts/facebook/callback", async (req, res) => {
  const { code, state, error, error_description: errDesc } = req.query
  const payload = verifyState(state)
  if (error || !payload || payload.platform !== "facebook" || !code) {
    return res.status(400).send(oauthResultHtml({ ok: false, platform: "facebook", message: errDesc || "Thiếu/sai state hoặc bạn đã huỷ kết nối" }))
  }
  try {
    const { facebookAppId: appId, facebookAppSecret: appSecret, publicBaseUrl } = await getAppCredentials()
    if (!appId || !appSecret) throw new Error("Chưa cấu hình App Facebook — vào Social Management > Help để nhập App ID/Secret")
    const redirectUri = redirectUriFor("facebook", publicBaseUrl)
    // 1) code → short-lived user token
    const tokRes = await fetch(`https://graph.facebook.com/v21.0/oauth/access_token?client_id=${encodeURIComponent(appId)}&redirect_uri=${encodeURIComponent(redirectUri)}&client_secret=${encodeURIComponent(appSecret)}&code=${encodeURIComponent(code)}`)
    const tokD = await tokRes.json().catch(() => ({}))
    if (!tokRes.ok || !tokD.access_token) throw new Error(tokD.error?.message || "Không đổi được code lấy token")
    // 2) short-lived → long-lived user token (~60 ngày; Page token lấy từ đây coi như không hết hạn)
    const llRes = await fetch(`https://graph.facebook.com/v21.0/oauth/access_token?grant_type=fb_exchange_token&client_id=${encodeURIComponent(appId)}&client_secret=${encodeURIComponent(appSecret)}&fb_exchange_token=${encodeURIComponent(tokD.access_token)}`)
    const llD = await llRes.json().catch(() => ({}))
    const userToken = llD.access_token || tokD.access_token
    // 3) danh sách Page user quản lý (mỗi Page có access_token riêng, dùng để đăng bài)
    const pagesRes = await fetch(`https://graph.facebook.com/v21.0/me/accounts?fields=id,name,access_token,picture&access_token=${encodeURIComponent(userToken)}`)
    const pagesD = await pagesRes.json().catch(() => ({}))
    if (!pagesRes.ok || !Array.isArray(pagesD.data)) throw new Error(pagesD.error?.message || "Không lấy được danh sách Page")
    if (!pagesD.data.length) throw new Error("Tài khoản Facebook này chưa quản trị Page nào (cần quyền Admin/Editor trên ít nhất 1 Page)")
    for (const p of pagesD.data) {
      await query(
        `INSERT INTO social_accounts (user_id, platform, external_id, name, avatar_url, access_token, meta, is_active)
         VALUES ($1,'facebook',$2,$3,$4,$5,$6,true)
         ON CONFLICT (user_id, platform, external_id) DO UPDATE SET
           name=$3, avatar_url=$4, access_token=$5, meta=$6, is_active=true, updated_at=now()`,
        [payload.uid, p.id, p.name, p.picture?.data?.url || null, p.access_token, JSON.stringify({ user_access_token: userToken })])
    }
    return res.send(oauthResultHtml({ ok: true, platform: "facebook", message: `Đã kết nối ${pagesD.data.length} Page: ${pagesD.data.map((p) => p.name).join(", ")}` }))
  } catch (e) {
    return res.status(500).send(oauthResultHtml({ ok: false, platform: "facebook", message: String(e?.message || e) }))
  }
})

router.get("/social-accounts/tiktok/callback", async (req, res) => {
  const { code, state, error, error_description: errDesc } = req.query
  const payload = verifyState(state)
  if (error || !payload || payload.platform !== "tiktok" || !code) {
    return res.status(400).send(oauthResultHtml({ ok: false, platform: "tiktok", message: errDesc || "Thiếu/sai state hoặc bạn đã huỷ kết nối" }))
  }
  try {
    const { tiktokClientKey: clientKey, tiktokClientSecret: clientSecret, publicBaseUrl } = await getAppCredentials()
    if (!clientKey || !clientSecret) throw new Error("Chưa cấu hình App TikTok — vào Social Management > Help để nhập Client Key/Secret")
    const body = new URLSearchParams({
      client_key: clientKey, client_secret: clientSecret, code: String(code), grant_type: "authorization_code",
      redirect_uri: redirectUriFor("tiktok", publicBaseUrl), code_verifier: payload.cv || "",
    })
    const tokRes = await fetch("https://open.tiktokapis.com/v2/oauth/token/", {
      method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body,
    })
    const tokD = await tokRes.json().catch(() => ({}))
    if (!tokRes.ok || !tokD.access_token) throw new Error(tokD.error_description || tokD.error || "Không đổi được code lấy token")
    const infoRes = await fetch("https://open.tiktokapis.com/v2/user/info/?fields=open_id,display_name,avatar_url", {
      headers: { Authorization: `Bearer ${tokD.access_token}` },
    })
    const infoD = await infoRes.json().catch(() => ({}))
    const info = infoD?.data?.user || {}
    const openId = info.open_id || tokD.open_id
    if (!openId) throw new Error("TikTok không trả về open_id")
    const expiresAt = new Date(Date.now() + (Number(tokD.expires_in) || 86400) * 1000)
    await query(
      `INSERT INTO social_accounts (user_id, platform, external_id, name, avatar_url, access_token, refresh_token, token_expires_at, meta, is_active)
       VALUES ($1,'tiktok',$2,$3,$4,$5,$6,$7,$8,true)
       ON CONFLICT (user_id, platform, external_id) DO UPDATE SET
         name=$3, avatar_url=$4, access_token=$5, refresh_token=$6, token_expires_at=$7, meta=$8, is_active=true, updated_at=now()`,
      [payload.uid, openId, info.display_name || "TikTok", info.avatar_url || null,
        tokD.access_token, tokD.refresh_token || null, expiresAt, JSON.stringify({ privacy_level: "SELF_ONLY" })])
    return res.send(oauthResultHtml({ ok: true, platform: "tiktok", message: `Đã kết nối @${info.display_name || openId} (đăng ở chế độ riêng tư cho tới khi app được TikTok duyệt công khai)` }))
  } catch (e) {
    return res.status(500).send(oauthResultHtml({ ok: false, platform: "tiktok", message: String(e?.message || e) }))
  }
})

// ── Authenticated routes ────────────────────────────────────────────────────────────────────────
router.use("/social-accounts", sessionAuth)

router.get("/social-accounts", async (req, res) => {
  const { rows } = await query(
    "SELECT * FROM social_accounts WHERE user_id=$1 AND is_active=true ORDER BY platform, created_at",
    [req.session.userId])
  res.json({ items: rows.map(toItem) })
})

router.delete("/social-accounts/:id", async (req, res) => {
  await query("UPDATE social_accounts SET is_active=false, updated_at=now() WHERE id=$1 AND user_id=$2",
    [req.params.id, req.session.userId])
  res.json({ success: true })
})

// ALD 05/07/2026 - Content Sharing Guidelines mục 4 (Required UX): FE PHẢI gọi creator_info trước khi render
// form đăng bài TikTok — hiển thị nickname tài khoản, giới hạn thời lượng video, danh sách privacy_level được
// phép chọn (app chưa audit → TikTok tự chỉ trả SELF_ONLY), và tương tác nào bị tài khoản tắt sẵn (để làm mờ
// checkbox tương ứng, không cho user bật lại).
router.get("/social-accounts/:id/tiktok-creator-info", async (req, res) => {
  const account = (await query(
    "SELECT * FROM social_accounts WHERE id=$1 AND user_id=$2 AND platform='tiktok' AND is_active=true",
    [req.params.id, req.session.userId])).rows[0]
  if (!account) return res.status(404).json({ error: "Không tìm thấy tài khoản TikTok này" })
  try {
    const info = await queryTikTokCreatorInfo(account)
    res.json(info)
  } catch (e) {
    res.status(500).json({ error: String(e?.message || e) })
  }
})

router.get("/social-accounts/facebook/connect-url", async (req, res) => {
  const { facebookAppId: appId, publicBaseUrl } = await getAppCredentials()
  if (!appId) return res.status(400).json({ error: "Chưa cấu hình App Facebook — vào Social Management > Help để nhập App ID/Secret" })
  if (!publicBaseUrl) return res.status(400).json({ error: "Chưa cấu hình Public Base URL — vào Social Management > Help để nhập (domain HTTPS để Facebook redirect về)" })
  const state = signState({ uid: req.session.userId, platform: "facebook" })
  const scope = "pages_show_list,pages_read_engagement,pages_manage_posts,business_management"
  const url = `https://www.facebook.com/v21.0/dialog/oauth?client_id=${encodeURIComponent(appId)}&redirect_uri=${encodeURIComponent(redirectUriFor("facebook", publicBaseUrl))}&state=${encodeURIComponent(state)}&scope=${encodeURIComponent(scope)}&response_type=code`
  res.json({ url })
})

router.get("/social-accounts/tiktok/connect-url", async (req, res) => {
  const { tiktokClientKey: clientKey, publicBaseUrl } = await getAppCredentials()
  if (!clientKey) return res.status(400).json({ error: "Chưa cấu hình App TikTok — vào Social Management > Help để nhập Client Key/Secret" })
  if (!publicBaseUrl) return res.status(400).json({ error: "Chưa cấu hình Public Base URL — vào Social Management > Help để nhập (domain HTTPS để TikTok redirect về)" })
  const { verifier, challenge } = pkcePair()
  const state = signState({ uid: req.session.userId, platform: "tiktok", cv: verifier })
  const scope = "user.info.basic,video.publish,video.upload"
  const url = `https://www.tiktok.com/v2/auth/authorize/?client_key=${encodeURIComponent(clientKey)}&scope=${encodeURIComponent(scope)}&response_type=code&redirect_uri=${encodeURIComponent(redirectUriFor("tiktok", publicBaseUrl))}&state=${encodeURIComponent(state)}&code_challenge=${challenge}&code_challenge_method=S256`
  res.json({ url })
})

export default router
// #endregion
