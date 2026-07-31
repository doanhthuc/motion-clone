// #region ALD 05/07/2026 - "Social Management" — Recommend (output workflow chưa đăng → gợi ý tạo bài) +
// lịch sử/hàng đợi đăng bài (social_posts). Đăng THẬT xảy ra ở wf-worker/social-scheduler.js (tickSocialPosts,
// chạy nền theo scheduled_for) — route này chỉ tạo/huỷ hàng đợi, KHÔNG gọi Graph/TikTok API trực tiếp.
//   GET    /social-posts/recommend        → { items } (workflow_runs success, có video/ảnh, CHƯA có trong social_posts)
//   GET    /social-posts                  → { items } (lịch sử/hàng đợi của user)
//   POST   /social-posts                  { workflow_run_id, caption?, facebook?, tiktok?, scheduled_for? } → { items }
//   POST   /social-posts/upload-test      multipart(file, caption?, facebook?, tiktok?) → { items } — TỰ TEST đăng
//                                            1 file bất kỳ (không qua workflow), dùng cho tab Đề xuất khi muốn thử
//                                            nhanh mà chưa có output workflow phù hợp. Tạo 1 workflow_runs "giả"
//                                            (workflow_id=NULL, status=success, output=file vừa upload) rồi đi
//                                            CHUNG 1 đường ống scheduleSocialPosts/tickSocialPosts như bình thường
//                                            — không tạo code path publish riêng, không lệch với luồng thật.
//   DELETE /social-posts/:id              → huỷ nếu còn 'scheduled' (chưa đăng)
import { Router } from "express"
import multer from "multer"
import crypto from "node:crypto"
import { query } from "../db.js"
import { sessionAuth } from "../auth/session.js"
import { scheduleSocialPosts } from "../wf-worker/social-scheduler.js"
import { putObject, browserUrl } from "../storage.js"

const upload = multer({ storage: multer.memoryStorage(), limits: { fileSize: 200 * 1024 * 1024, files: 1 } })
const router = Router()
router.use("/social-posts", sessionAuth)

// facebook/tiktok gửi kèm dạng JSON string trong multipart field (vd '{"enabled":true,"accountIds":["..."]}').
// tiktok còn kèm privacyLevel/disableDuet/disableComment/disableStitch/brandContentToggle/brandOrganicToggle —
// LỰA CHỌN THẬT của user qua TikTokFields.vue (Content Sharing Guidelines mục 4), không hardcode ở server.
function parsePlatformField(raw, isTikTok = false) {
  try {
    const p = JSON.parse(raw || "{}")
    const base = { enabled: !!p.enabled, accountIds: Array.isArray(p.accountIds) ? p.accountIds : [] }
    if (!isTikTok) return base
    return {
      ...base,
      privacyLevel: p.privacyLevel || null,
      disableDuet: p.disableDuet !== false,
      disableComment: p.disableComment !== false,
      disableStitch: p.disableStitch !== false,
      brandContentToggle: !!p.brandContentToggle,
      brandOrganicToggle: !!p.brandOrganicToggle,
    }
  } catch { return isTikTok ? { enabled: false, accountIds: [] } : { enabled: false, accountIds: [] } }
}

// Kiểm tra chung cho TikTok (dùng ở cả POST /social-posts và /social-posts/upload-test).
function validateTiktokFields(tiktok) {
  if (!tiktok.enabled) return null
  if (!tiktok.privacyLevel) return "TikTok: chưa chọn chế độ hiển thị (privacy) cho bài đăng"
  if (tiktok.brandContentToggle && tiktok.privacyLevel === "SELF_ONLY") return "TikTok: nội dung 'Branded Content' không được để chế độ riêng tư (Chỉ mình tôi)"
  return null
}

function previewOf(output) {
  const meta = output?.metadata || {}
  const url = meta.video || meta.image || null
  return { url, kind: meta.video ? "video" : (meta.image ? "image" : null), text: output?.text || "" }
}

router.get("/social-posts/recommend", async (req, res) => {
  const limit = Math.min(100, Math.max(1, Number(req.query.limit) || 30))
  const { rows } = await query(
    `SELECT wr.id, wr.workflow_id, w.name AS workflow_name, wr.output, wr.finished_at
     FROM workflow_runs wr
     JOIN workflows w ON w.id = wr.workflow_id
     WHERE wr.user_id = $1 AND wr.status = 'success'
       AND (wr.output->'metadata'->>'video' IS NOT NULL OR wr.output->'metadata'->>'image' IS NOT NULL)
       AND NOT EXISTS (SELECT 1 FROM social_posts sp WHERE sp.workflow_run_id = wr.id)
     ORDER BY wr.finished_at DESC NULLS LAST
     LIMIT $2`,
    [req.session.userId, limit])
  res.json({
    items: rows.map((r) => ({
      workflow_run_id: r.id, workflow_id: r.workflow_id, workflow_name: r.workflow_name,
      finished_at: r.finished_at, preview: previewOf(r.output),
    })),
  })
})

// ALD 05/07/2026 - Phân trang thật (page/limit) cho "Hoạt động gần đây" — trước đây chỉ LIMIT không OFFSET/total,
// không đủ khi dữ liệu nhiều. Giữ tương thích ngược: nếu FE không gửi page thì mặc định page=1.
router.get("/social-posts", async (req, res) => {
  const limit = Math.min(200, Math.max(1, Number(req.query.limit) || 20))
  const page = Math.max(1, Number(req.query.page) || 1)
  const offset = (page - 1) * limit
  const [{ rows }, countRes] = await Promise.all([
    query(
      `SELECT sp.*, sa.name AS account_name, COALESCE(w.name, 'Test upload') AS workflow_name, wr.output AS run_output
       FROM social_posts sp
       JOIN social_accounts sa ON sa.id = sp.account_id
       LEFT JOIN workflow_runs wr ON wr.id = sp.workflow_run_id
       LEFT JOIN workflows w ON w.id = wr.workflow_id
       WHERE sp.user_id = $1
       ORDER BY sp.created_at DESC
       LIMIT $2 OFFSET $3`,
      [req.session.userId, limit, offset]).then((r) => ({ rows: r.rows })),
    query(`SELECT COUNT(*)::int AS total FROM social_posts WHERE user_id = $1`, [req.session.userId]),
  ])
  res.json({
    items: rows.map((r) => ({
      id: r.id, platform: r.platform, account_name: r.account_name, workflow_name: r.workflow_name,
      caption: r.caption, status: r.status, scheduled_for: r.scheduled_for, posted_at: r.posted_at,
      external_post_id: r.external_post_id, error_msg: r.error_msg, created_at: r.created_at,
      preview: previewOf(r.run_output), content_plan_slot_id: r.content_plan_slot_id,
    })),
    total: countRes.rows[0]?.total || 0,
    page, limit,
  })
})

router.post("/social-posts", async (req, res) => {
  const b = req.body || {}
  const runId = String(b.workflow_run_id || "")
  if (!runId) return res.status(400).json({ error: "Thiếu workflow_run_id" })
  const run = (await query("SELECT id, status FROM workflow_runs WHERE id=$1 AND user_id=$2", [runId, req.session.userId])).rows[0]
  if (!run) return res.status(404).json({ error: "Không tìm thấy kết quả workflow này" })
  if (run.status !== "success") return res.status(400).json({ error: "Run chưa xong (chỉ đăng được run success)" })
  const facebook = { enabled: !!b.facebook?.enabled, accountIds: Array.isArray(b.facebook?.accountIds) ? b.facebook.accountIds : [] }
  const tiktok = {
    enabled: !!b.tiktok?.enabled, accountIds: Array.isArray(b.tiktok?.accountIds) ? b.tiktok.accountIds : [],
    privacyLevel: b.tiktok?.privacyLevel || null,
    disableDuet: b.tiktok?.disableDuet !== false, disableComment: b.tiktok?.disableComment !== false, disableStitch: b.tiktok?.disableStitch !== false,
    brandContentToggle: !!b.tiktok?.brandContentToggle, brandOrganicToggle: !!b.tiktok?.brandOrganicToggle,
  }
  if (!facebook.enabled && !tiktok.enabled) return res.status(400).json({ error: "Chưa chọn nền tảng/tài khoản nào để đăng" })
  // ALD 05/07/2026 - FIX: điều kiện cũ dùng && giữa 2 vế nên chỉ báo lỗi khi CẢ HAI nền tảng cùng bật-mà-rỗng
  // accountIds — bật 1 nền tảng (vd chỉ Facebook) mà quên chọn Page thì lọt qua, tạo 0 dòng social_posts nhưng
  // vẫn trả 201 (im lặng không đăng gì, rất khó hiểu cho user). Đổi sang kiểm tra ĐỘC LẬP từng nền tảng.
  if (facebook.enabled && !facebook.accountIds.length) return res.status(400).json({ error: "Đã bật Facebook nhưng chưa chọn Page" })
  if (tiktok.enabled && !tiktok.accountIds.length) return res.status(400).json({ error: "Đã bật TikTok nhưng chưa chọn tài khoản" })
  const ttErr = validateTiktokFields(tiktok)
  if (ttErr) return res.status(400).json({ error: ttErr })
  try {
    const items = await scheduleSocialPosts({
      userId: req.session.userId, workflowRunId: runId, caption: b.caption || "",
      facebook, tiktok, scheduledFor: b.scheduled_for || null,
    })
    if (!items.length) return res.status(400).json({ error: "Tài khoản đã chọn không hợp lệ/không thuộc quyền của bạn" })
    res.status(201).json({ items })
  } catch (e) {
    res.status(500).json({ error: String(e?.message || e) })
  }
})

router.post("/social-posts/upload-test", upload.single("file"), async (req, res) => {
  try {
    if (!req.file) return res.status(400).json({ error: "Thiếu file" })
    const mime = req.file.mimetype || "application/octet-stream"
    const isVideo = mime.startsWith("video/")
    const isImage = mime.startsWith("image/")
    if (!isVideo && !isImage) return res.status(400).json({ error: `File "${mime}" không hỗ trợ — chỉ nhận video hoặc ảnh` })

    const facebook = parsePlatformField(req.body.facebook)
    const tiktok = parsePlatformField(req.body.tiktok, true)
    if (!facebook.enabled && !tiktok.enabled) return res.status(400).json({ error: "Chưa chọn nền tảng/tài khoản nào để đăng" })
    if (facebook.enabled && !facebook.accountIds.length) return res.status(400).json({ error: "Đã bật Facebook nhưng chưa chọn Page" })
    if (tiktok.enabled && !tiktok.accountIds.length) return res.status(400).json({ error: "Đã bật TikTok nhưng chưa chọn tài khoản" })
    if (tiktok.enabled && !isVideo) return res.status(400).json({ error: "TikTok chỉ hỗ trợ đăng VIDEO — bỏ chọn TikTok hoặc đổi sang file video" })
    const ttErr = validateTiktokFields(tiktok)
    if (ttErr) return res.status(400).json({ error: ttErr })

    // Lưu file lên storage (MinIO) → lấy URL public (Facebook cần URL để tự tải; TikTok đọc trực tiếp base64
    // bên dưới nên không bắt buộc, nhưng lưu luôn để xem lại/tái dùng, giống mọi output khác trong hệ thống).
    const safe = (req.file.originalname || `upload.${isVideo ? "mp4" : "jpg"}`).replace(/[^a-zA-Z0-9._-]/g, "_").slice(0, 80)
    const key = `social-test/${req.session.userId}/${crypto.randomUUID()}/${safe}`
    await putObject(key, req.file.buffer, mime)
    const url = await browserUrl(key)
    const caption = String(req.body.caption || "").trim()

    // "workflow_runs giả" — social_posts.workflow_run_id NOT NULL nên cần 1 run success để gắn vào; workflow_id
    // để NULL (không thuộc workflow nào), output đúng format mà tickSocialPosts/publishTo* đang đọc.
    const run = (await query(
      `INSERT INTO workflow_runs (workflow_id, user_id, auth_method, status, input, output, finished_at)
       VALUES (NULL,$1,'session','success','{}'::jsonb,$2,now()) RETURNING id`,
      [req.session.userId, JSON.stringify({
        file: { data: req.file.buffer.toString("base64"), mimeType: mime, name: safe },
        metadata: isVideo ? { video: url } : { image: url },
        text: caption,
      })])).rows[0]

    const items = await scheduleSocialPosts({
      userId: req.session.userId, workflowRunId: run.id, caption, facebook, tiktok,
    })
    if (!items.length) return res.status(400).json({ error: "Tài khoản đã chọn không hợp lệ/không thuộc quyền của bạn" })
    res.status(201).json({ items })
  } catch (e) {
    res.status(500).json({ error: String(e?.message || e) })
  }
})

router.delete("/social-posts/:id", async (req, res) => {
  const { rowCount } = await query(
    "UPDATE social_posts SET status='cancelled' WHERE id=$1 AND user_id=$2 AND status='scheduled'",
    [req.params.id, req.session.userId])
  if (!rowCount) return res.status(400).json({ error: "Chỉ huỷ được bài đang chờ đăng (scheduled)" })
  res.json({ success: true })
})

export default router
// #endregion
