// #region ALD 05/07/2026 - Đăng lên Facebook Page / TikTok — hàm publish "trần" (gọi Graph/TikTok API thật),
// dùng bởi wf-worker/social-scheduler.js (tickSocialPosts, publish nền theo hàng đợi social_posts). KHÔNG còn
// hàm nào ở đây được gọi trực tiếp từ node "output" nữa (xem handlers.js handleOutput — đã đổi sang xếp hàng
// qua scheduleSocialPosts để mọi đường đăng bài đi qua CHUNG 1 nguồn sự thật là bảng social_posts).
//   - Facebook: Graph API — gửi thẳng file_url/url (browserUrl HTTPS đã ký) cho Graph tự tải, không cần
//     upload tay. Page access token lấy lúc connect coi như KHÔNG hết hạn (miễn Page còn tồn tại + user còn
//     quyền quản trị) nên không cần refresh.
//   - TikTok: Content Posting API v2. Tuân thủ Content Sharing Guidelines
//     (https://developers.tiktok.com/doc/content-sharing-guidelines/):
//       · source = PULL_FROM_URL khi đã có URL trên server (domain đã verify) — CHỈ fallback FILE_UPLOAD khi
//         không có URL. Trước đây hardcode FILE_UPLOAD dù có URL sẵn → SAI guideline, đã sửa 05/07/2026.
//       · privacy_level / disable_duet/comment/stitch / brand_*_toggle: nhận từ LỰA CHỌN THẬT của user (cột
//         social_posts.*, xem routes/social-posts.js + FE composer) — KHÔNG hardcode true/false ở đây nữa.
//       · App CHƯA được audit Content Posting API → TikTok tự chối privacy_level khác SELF_ONLY, không cần
//         chặn tay ở code (để creator_info/privacy_level_options TikTok trả về tự quyết định UI cho phép chọn gì).
//       · publish() chỉ trả publish_id (status "processing" tạm) — KHÔNG tự coi là "đã đăng". Xác nhận thật qua
//         checkTikTokPublishStatus() (tickTikTokProcessing trong social-scheduler.js poll định kỳ).
import { query } from "../db.js"
import { getAppCredentials } from "../social-app-config.js"

const FB_API = "https://graph.facebook.com/v21.0"
const FB_VIDEO_API = "https://graph-video.facebook.com/v21.0"
const TT_API = "https://open.tiktokapis.com/v2"

/** Load + XÁC THỰC QUYỀN SỞ HỮU: chỉ trả về social_accounts thuộc ĐÚNG user+platform trong ids yêu cầu. Dùng ở
 * routes/social-posts.js + routes/content-plans.js để chặn user A gán bài đăng vào account của user B (id đoán
 * được thì cũng không published được vì query luôn lọc theo userId). */
export async function loadSocialAccounts(userId, platform, ids) {
  const list = (ids || []).filter(Boolean)
  if (!list.length) return []
  const { rows } = await query(
    "SELECT * FROM social_accounts WHERE user_id=$1 AND platform=$2 AND id = ANY($3::uuid[]) AND is_active=true",
    [userId, platform, list])
  return rows
}

export async function publishToFacebook(account, { videoUrl, imageUrl, caption }) {
  const pageId = account.external_id
  const token = account.access_token
  if (videoUrl) {
    const body = new URLSearchParams({ file_url: videoUrl, description: caption || "", access_token: token })
    const r = await fetch(`${FB_VIDEO_API}/${encodeURIComponent(pageId)}/videos`, { method: "POST", body })
    const d = await r.json().catch(() => ({}))
    if (!r.ok || d.error) throw new Error(d.error?.message || `Facebook video lỗi HTTP ${r.status}`)
    return { platform: "facebook", accountId: account.id, id: d.id, kind: "video" }
  }
  if (imageUrl) {
    const body = new URLSearchParams({ url: imageUrl, caption: caption || "", access_token: token })
    const r = await fetch(`${FB_API}/${encodeURIComponent(pageId)}/photos`, { method: "POST", body })
    const d = await r.json().catch(() => ({}))
    if (!r.ok || d.error) throw new Error(d.error?.message || `Facebook ảnh lỗi HTTP ${r.status}`)
    return { platform: "facebook", accountId: account.id, id: d.post_id || d.id, kind: "image" }
  }
  if (caption) {
    const body = new URLSearchParams({ message: caption, access_token: token })
    const r = await fetch(`${FB_API}/${encodeURIComponent(pageId)}/feed`, { method: "POST", body })
    const d = await r.json().catch(() => ({}))
    if (!r.ok || d.error) throw new Error(d.error?.message || `Facebook đăng bài lỗi HTTP ${r.status}`)
    return { platform: "facebook", accountId: account.id, id: d.id, kind: "text" }
  }
  throw new Error("Facebook: node Output chưa có video/ảnh/text để đăng")
}

/** Refresh access_token TikTok nếu còn hạn < 5 phút (access token TikTok chỉ sống ~24h). */
export async function ensureFreshTikTokToken(account) {
  const expMs = account.token_expires_at ? new Date(account.token_expires_at).getTime() : 0
  if (expMs - Date.now() > 5 * 60 * 1000) return account
  if (!account.refresh_token) throw new Error("TikTok token hết hạn và không có refresh_token — cần kết nối lại")
  const { tiktokClientKey, tiktokClientSecret } = await getAppCredentials()
  const body = new URLSearchParams({
    client_key: tiktokClientKey,
    client_secret: tiktokClientSecret,
    grant_type: "refresh_token",
    refresh_token: account.refresh_token,
  })
  const r = await fetch(`${TT_API}/oauth/token/`, { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body })
  const d = await r.json().catch(() => ({}))
  if (!r.ok || !d.access_token) throw new Error(d.error_description || d.error || `TikTok refresh token lỗi HTTP ${r.status} — cần kết nối lại`)
  const newExp = new Date(Date.now() + (Number(d.expires_in) || 86400) * 1000)
  await query("UPDATE social_accounts SET access_token=$1, refresh_token=$2, token_expires_at=$3, updated_at=now() WHERE id=$4",
    [d.access_token, d.refresh_token || account.refresh_token, newExp, account.id])
  return { ...account, access_token: d.access_token, refresh_token: d.refresh_token || account.refresh_token }
}

/** Query creator_info — BẮT BUỘC gọi trước khi render UI đăng bài (Required UX Implementation, mục 1). Trả về
 * nickname (hiển thị cho user biết đang đăng vào tài khoản nào), giới hạn thời lượng video, danh sách
 * privacy_level hợp lệ (app chưa audit → TikTok tự chỉ trả về SELF_ONLY), và cờ tương tác nào bị TẮT sẵn ở
 * phía tài khoản (dùng để làm mờ/disable checkbox tương ứng trên UI, không cho user bật lại). */
export async function queryTikTokCreatorInfo(account) {
  const r = await fetch(`${TT_API}/post/publish/creator_info/query/`, {
    method: "POST",
    headers: { Authorization: `Bearer ${account.access_token}`, "Content-Type": "application/json; charset=UTF-8" },
  })
  const d = await r.json().catch(() => ({}))
  if (!r.ok || d.error?.code && d.error.code !== "ok") throw new Error(d.error?.message || `TikTok creator_info lỗi HTTP ${r.status}`)
  const data = d.data || {}
  return {
    nickname: data.creator_nickname || account.name || "",
    avatarUrl: data.creator_avatar_url || account.avatar_url || null,
    privacyLevelOptions: data.privacy_level_options || ["SELF_ONLY"],
    maxVideoPostDurationSec: data.max_video_post_duration_sec ?? null,
    commentDisabled: !!data.comment_disabled,
    duetDisabled: !!data.duet_disabled,
    stitchDisabled: !!data.stitch_disabled,
  }
}

/**
 * post: { videoUrl?, videoBuffer?, caption, privacyLevel, disableDuet, disableComment, disableStitch,
 *         brandContentToggle, brandOrganicToggle }
 * ALD 05/07/2026 - privacyLevel/disable-duet-comment-stitch/brand toggle giờ đến từ lựa chọn THẬT của user (không hardcode nữa) —
 * xem social-scheduler.js tickSocialPosts truyền vào từ cột social_posts.*. source ưu tiên PULL_FROM_URL khi có
 * videoUrl (đúng Content Sharing Guidelines mục 6b/6d), chỉ FILE_UPLOAD khi không có URL nào khác.
 */
export async function publishToTikTok(account, post) {
  const { videoUrl, videoBuffer, caption, privacyLevel, disableDuet, disableComment, disableStitch, brandContentToggle, brandOrganicToggle } = post
  if (!videoUrl && !videoBuffer?.length) throw new Error("TikTok: node Output chỉ hỗ trợ đăng VIDEO (chưa có ảnh/text)")
  if (!privacyLevel) throw new Error("TikTok: chưa chọn chế độ hiển thị (privacy) cho bài đăng")
  // Branded Content không được phép ở chế độ riêng tư (Content Sharing Guidelines mục 5b).
  if (brandContentToggle && privacyLevel === "SELF_ONLY") throw new Error("TikTok: nội dung 'Branded Content' không được để chế độ riêng tư (Chỉ mình tôi)")
  const token = account.access_token
  const post_info = {
    title: caption || "",
    privacy_level: privacyLevel,
    disable_duet: disableDuet !== false,     // mặc định TẮT (true) trừ khi user tự bật (false rõ ràng)
    disable_comment: disableComment !== false,
    disable_stitch: disableStitch !== false,
    brand_content_toggle: !!brandContentToggle,
    brand_organic_toggle: !!brandOrganicToggle,
    video_cover_timestamp_ms: 1000,
  }
  const source_info = videoUrl
    ? { source: "PULL_FROM_URL", video_url: videoUrl }
    : { source: "FILE_UPLOAD", video_size: videoBuffer.length, chunk_size: videoBuffer.length, total_chunk_count: 1 }
  const initRes = await fetch(`${TT_API}/post/publish/video/init/`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ post_info, source_info }),
  })
  const initD = await initRes.json().catch(() => ({}))
  // ALD 05/07/2026 - Message "Please review guidelines" lặp lại y hệt bất kể nội dung/scope/sandbox key — đã loại
  // trừ nhiều giả thuyết qua message suông. error.code + log_id là dữ liệu THẬT TikTok trả về đằng sau message
  // chung chung đó (vd spam_risk_too_many_pending_share, reached_active_user_cap, scope_not_authorized,
  // unaudited_client_user_cap…) — log ra để tra cứu chính xác thay vì đoán tiếp.
  if (!initRes.ok || (initD.error?.code && initD.error.code !== "ok")) {
    const code = initD.error?.code || `http_${initRes.status}`
    const logId = initD.error?.log_id || ""
    throw new Error(`[${code}]${logId ? ` (log_id=${logId})` : ""} ${initD.error?.message || `TikTok init lỗi HTTP ${initRes.status}`}`)
  }
  const { publish_id, upload_url } = initD.data || {}
  if (!publish_id) throw new Error("TikTok: không nhận được publish_id từ init")
  // FILE_UPLOAD mới cần PUT bytes tay — PULL_FROM_URL để TikTok tự tải từ video_url, không cần bước này.
  if (source_info.source === "FILE_UPLOAD") {
    if (!upload_url) throw new Error("TikTok: không nhận được upload_url từ init")
    const putRes = await fetch(upload_url, {
      method: "PUT",
      headers: { "Content-Type": "video/mp4", "Content-Range": `bytes 0-${videoBuffer.length - 1}/${videoBuffer.length}` },
      body: videoBuffer,
    })
    if (!putRes.ok) throw new Error(`TikTok upload video lỗi HTTP ${putRes.status}`)
  }
  // ALD 05/07/2026 - KHÔNG coi là "đã đăng" ở đây nữa — chỉ trả publish_id, trạng thái thật do
  // checkTikTokPublishStatus() xác nhận (poll ở tickTikTokProcessing, social-scheduler.js).
  return { platform: "tiktok", accountId: account.id, id: publish_id, kind: "video", status: "processing" }
}

/** Poll trạng thái xử lý thật của 1 publish_id (Required UX Implementation mục 5e — phải theo dõi status thay
 * vì coi upload xong = đăng xong). Trả { done, ok, message }. */
export async function checkTikTokPublishStatus(account, publishId) {
  const r = await fetch(`${TT_API}/post/publish/status/fetch/`, {
    method: "POST",
    headers: { Authorization: `Bearer ${account.access_token}`, "Content-Type": "application/json; charset=UTF-8" },
    body: JSON.stringify({ publish_id: publishId }),
  })
  const d = await r.json().catch(() => ({}))
  if (!r.ok || d.error?.code && d.error.code !== "ok") throw new Error(d.error?.message || `TikTok status/fetch lỗi HTTP ${r.status}`)
  const status = d.data?.status || ""
  if (status === "PUBLISH_COMPLETE" || status === "SEND_TO_USER_INBOX") return { done: true, ok: true, status }
  if (status === "FAILED") return { done: true, ok: false, status, message: d.data?.fail_reason || "TikTok xử lý thất bại" }
  return { done: false, ok: false, status } // PROCESSING_DOWNLOAD | PROCESSING_UPLOAD | ... chưa xong
}
// #endregion
