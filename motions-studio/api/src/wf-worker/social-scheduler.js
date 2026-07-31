// #region ALD 05/07/2026 - Scheduler cho "Social Management": Content Plan (chạy workflow theo giờ hẹn) + hàng
// đợi đăng bài (social_posts). Gọi từ wf-worker/worker.js (cùng process poll workflow_runs — 1 worker singleton
// nên KHÔNG cần lock phân tán, chỉ cần UNIQUE constraint DB chống bắn trùng khi restart/tick chồng nhau).
//
//   tickContentPlans()      — mỗi tick: slot nào ĐẾN GIỜ hôm nay (giờ Asia/Ho_Chi_Minh) và CHƯA chạy hôm nay
//                              → tạo workflow_runs (queued, để claim() bình thường trong worker.js nhặt chạy)
//                              + content_plan_runs (đánh dấu đã bắn, chống trùng).
//   onWorkflowRunFinished()  — gọi SAU khi 1 workflow_runs chạy xong (worker.js). Nếu run đó gắn với 1
//                              content_plan_runs → cập nhật success/error; nếu success → tạo social_posts theo
//                              cấu hình facebook/tiktok của slot (scheduled_for=now, tickSocialPosts đăng liền).
//   tickSocialPosts()        — mỗi tick: social_posts status='scheduled' & scheduled_for<=now → gọi Graph/TikTok
//                              API publish thật, cập nhật posted/error. Dùng CHUNG hàm publish với node "output"
//                              (social-publish.js) — Recommend thủ công VÀ Plan tự động đi qua đúng 1 code path.
import { query } from "../db.js"
import { publishToFacebook, publishToTikTok, ensureFreshTikTokToken, loadSocialAccounts, checkTikTokPublishStatus } from "./social-publish.js"

const TZ = process.env.SCHEDULER_TZ || "Asia/Ho_Chi_Minh"
const log = (...a) => console.log("[social-scheduler]", ...a)

// ── Content Plan: tạo workflow_runs cho slot đến giờ ───────────────────────────────────────────────
export async function tickContentPlans() {
  let due
  try {
    due = (await query(
      `SELECT s.*, p.user_id AS plan_user_id
       FROM content_plan_slots s
       JOIN content_plans p ON p.id = s.plan_id
       WHERE s.is_active = true AND p.is_active = true
         AND EXTRACT(DOW FROM (now() AT TIME ZONE $1))::int = ANY (s.weekdays)
         AND s.time_of_day <= (now() AT TIME ZONE $1)::time
         AND NOT EXISTS (
           SELECT 1 FROM content_plan_runs r
           WHERE r.slot_id = s.id AND r.scheduled_date = (now() AT TIME ZONE $1)::date
         )`, [TZ])).rows
  } catch (e) {
    log("tickContentPlans query lỗi:", e?.message || e)
    return
  }
  for (const slot of due) {
    try {
      const wf = (await query("SELECT definition FROM workflows WHERE id=$1 AND is_active=true", [slot.workflow_id])).rows[0]
      if (!wf) { log(`slot ${slot.id} bỏ qua — workflow ${slot.workflow_id} không tồn tại/đã tắt`); continue }
      const run = (await query(
        `INSERT INTO workflow_runs (workflow_id, user_id, auth_method, status, input)
         VALUES ($1,$2,'session','queued',$3) RETURNING id`,
        [slot.workflow_id, slot.plan_user_id, JSON.stringify(slot.input || {})])).rows[0]
      // scheduled_date dùng NGAY giá trị vừa lọc (giờ VN) — insert với UNIQUE(slot_id,scheduled_date) làm
      // "khoá" chống 2 tick chạy trùng (tick sau sẽ vi phạm UNIQUE → catch → bỏ qua, không tạo run đôi).
      await query(
        `INSERT INTO content_plan_runs (slot_id, scheduled_date, workflow_run_id, status)
         VALUES ($1, (now() AT TIME ZONE $2)::date, $3, 'queued')`,
        [slot.id, TZ, run.id])
      log(`slot ${String(slot.id).slice(0, 8)} (${slot.label || ""}) → tạo run ${String(run.id).slice(0, 8)}`)
    } catch (e) {
      // Vi phạm UNIQUE(slot_id,scheduled_date) = tick khác vừa tạo trước đó → im lặng bỏ qua (không phải lỗi thật).
      if (String(e?.message || "").includes("duplicate key")) continue
      log(`slot ${slot.id} lỗi:`, e?.message || e)
    }
  }
}

// ── Gọi sau khi 1 workflow_runs chạy xong (worker.js runOne) ───────────────────────────────────────
export async function onWorkflowRunFinished(runId, res) {
  let planRun
  try {
    planRun = (await query("SELECT * FROM content_plan_runs WHERE workflow_run_id=$1", [runId])).rows[0]
  } catch { return }
  if (!planRun) return // run thường (không thuộc content plan) — bỏ qua

  const ok = res?.status === "success"
  await query("UPDATE content_plan_runs SET status=$1, error_msg=$2, finished_at=now() WHERE id=$3",
    [ok ? "success" : "error", ok ? null : (res?.errorMsg || "lỗi không rõ"), planRun.id]).catch(() => {})
  if (!ok) { log(`plan run ${String(planRun.id).slice(0, 8)} lỗi — không đăng MXH`); return }

  const slot = (await query(
    `SELECT s.*, p.user_id AS plan_user_id FROM content_plan_slots s
     JOIN content_plans p ON p.id = s.plan_id WHERE s.id=$1`, [planRun.slot_id])).rows[0]
  if (!slot) return
  try {
    await scheduleSocialPosts({
      userId: slot.plan_user_id, workflowRunId: runId, planSlotId: slot.id,
      caption: slot.caption, facebook: slot.facebook, tiktok: slot.tiktok,
    })
    log(`plan run ${String(planRun.id).slice(0, 8)} success → xếp hàng đăng MXH`)
  } catch (e) {
    log(`plan run ${String(planRun.id).slice(0, 8)} lỗi xếp hàng đăng MXH:`, e?.message || e)
  }
}

// ── Xếp hàng social_posts (dùng chung cho Plan tự động + Recommend thủ công + toggle Output node) ──
// cfg: { userId, workflowRunId, planSlotId?, caption,
//        facebook:{enabled,accountIds}, tiktok:{enabled,accountIds,privacyLevel,disableDuet,disableComment,disableStitch,brandContentToggle,brandOrganicToggle},
//        scheduledFor? }
// ALD 05/07/2026 - accountIds LUÔN được xác thực qua loadSocialAccounts (lọc theo userId+platform thật) trước
// khi insert — chặn user A gán bài đăng vào social_accounts của user B (id đoán được thì query cũng rớt ra rỗng).
// tiktok.* (privacy/duet/comment/stitch/brand) là LỰA CHỌN THẬT của user ở FE (Content Sharing Guidelines mục 4) —
// KHÔNG hardcode ở publishToTikTok nữa, lưu thẳng vào social_posts để tickSocialPosts đọc lại khi đăng.
export async function scheduleSocialPosts(cfg) {
  const scheduledFor = cfg.scheduledFor ? new Date(cfg.scheduledFor) : new Date()
  const rows = []
  for (const platform of ["facebook", "tiktok"]) {
    const p = cfg[platform]
    if (!p?.enabled) continue
    const owned = await loadSocialAccounts(cfg.userId, platform, p.accountIds || [])
    const isTikTok = platform === "tiktok"
    for (const acc of owned) {
      const r = (await query(
        `INSERT INTO social_posts
           (user_id, workflow_run_id, content_plan_slot_id, platform, account_id, caption, status, scheduled_for,
            privacy_level, disable_duet, disable_comment, disable_stitch, brand_content_toggle, brand_organic_toggle)
         VALUES ($1,$2,$3,$4,$5,$6,'scheduled',$7,$8,$9,$10,$11,$12,$13) RETURNING *`,
        [cfg.userId, cfg.workflowRunId, cfg.planSlotId || null, platform, acc.id, cfg.caption || null, scheduledFor.toISOString(),
          isTikTok ? (p.privacyLevel || null) : null,
          isTikTok ? p.disableDuet !== false : true,
          isTikTok ? p.disableComment !== false : true,
          isTikTok ? p.disableStitch !== false : true,
          isTikTok ? !!p.brandContentToggle : false,
          isTikTok ? !!p.brandOrganicToggle : false])).rows[0]
      rows.push(r)
    }
  }
  return rows
}

// ── Publish social_posts đã đến giờ ─────────────────────────────────────────────────────────────────
// ALD 05/07/2026 - JOIN workflow_runs.status: chỉ nhặt post khi run ĐÃ XONG (success/error/cancelled). Tránh
// race hiếm gặp khi node "output" xếp hàng NGAY LÚC ĐANG chạy (trước khi workflow_runs.output được ghi ở cuối
// runWorkflow) — nếu publish sớm sẽ đọc output=null → đăng rỗng/lỗi oan trong lúc run thật ra sắp success.
export async function tickSocialPosts() {
  let due
  try {
    due = (await query(
      `SELECT sp.*, wr.status AS run_status, wr.output AS run_output
       FROM social_posts sp
       JOIN workflow_runs wr ON wr.id = sp.workflow_run_id
       WHERE sp.status = 'scheduled' AND sp.scheduled_for <= now()
         AND wr.status IN ('success', 'error', 'cancelled')
       ORDER BY sp.scheduled_for ASC LIMIT 20`)).rows
  } catch (e) {
    log("tickSocialPosts query lỗi:", e?.message || e)
    return
  }
  for (const post of due) {
    // Claim nguyên tử (tránh 2 tick song song publish trùng nếu sau này scale worker>1).
    const claimed = (await query(
      "UPDATE social_posts SET status='posting' WHERE id=$1 AND status='scheduled' RETURNING *", [post.id])).rows[0]
    if (!claimed) continue
    try {
      if (post.run_status !== "success") {
        throw new Error(post.run_status === "cancelled" ? "Workflow run đã bị huỷ — không có output để đăng" : "Workflow run lỗi — không có output để đăng")
      }
      const account = (await query("SELECT * FROM social_accounts WHERE id=$1 AND is_active=true", [claimed.account_id])).rows[0]
      if (!account) throw new Error("Tài khoản MXH đã bị ngắt kết nối")
      const output = post.run_output || {}
      const caption = String(claimed.caption || "").trim() || String(output?.text || "").trim()
      const videoUrl = output?.metadata?.video || null
      const imageUrl = output?.metadata?.image || null
      const fileBuf = output?.file?.data ? Buffer.from(output.file.data, "base64") : null

      let result
      if (claimed.platform === "facebook") {
        result = await publishToFacebook(account, { videoUrl, imageUrl, caption })
        // Graph API xác nhận đồng bộ (video/ảnh/text) → coi là đã đăng ngay, không cần poll thêm.
        await query("UPDATE social_posts SET status='posted', external_post_id=$1, posted_at=now() WHERE id=$2",
          [result.id || null, claimed.id])
      } else if (claimed.platform === "tiktok") {
        const isVideo = !!videoUrl || String(output?.file?.mimeType || "").startsWith("video/")
        if (!isVideo) throw new Error("TikTok chỉ hỗ trợ đăng VIDEO")
        if (!videoUrl && !fileBuf) throw new Error("TikTok: không có URL lẫn file video để đăng")
        if (!claimed.privacy_level) throw new Error("TikTok: bài đăng chưa chọn chế độ hiển thị (privacy) — huỷ và tạo lại từ form")
        const fresh = await ensureFreshTikTokToken(account)
        result = await publishToTikTok(fresh, {
          videoUrl, videoBuffer: videoUrl ? null : fileBuf, caption,
          privacyLevel: claimed.privacy_level,
          disableDuet: claimed.disable_duet, disableComment: claimed.disable_comment, disableStitch: claimed.disable_stitch,
          brandContentToggle: claimed.brand_content_toggle, brandOrganicToggle: claimed.brand_organic_toggle,
        })
        // ALD 05/07/2026 - CHƯA coi là "đã đăng" — TikTok xử lý bất đồng bộ (mục 5d/5e guidelines). Đánh dấu
        // 'processing', tickTikTokProcessing() bên dưới poll publish/status/fetch để xác nhận thật.
        await query("UPDATE social_posts SET status='processing', external_post_id=$1 WHERE id=$2",
          [result.id || null, claimed.id])
      } else {
        throw new Error(`Platform "${claimed.platform}" chưa hỗ trợ`)
      }
      log(`đăng ${claimed.platform} · post ${String(claimed.id).slice(0, 8)} · account ${account.name || account.external_id} · ${result?.status || "posted"}`)
    } catch (e) {
      await query("UPDATE social_posts SET status='error', error_msg=$1 WHERE id=$2",
        [String(e?.message || e), claimed.id]).catch(() => {})
      log(`đăng ${claimed.platform} lỗi · post ${String(claimed.id).slice(0, 8)}:`, e?.message || e)
    }
  }
}

// ── Xác nhận trạng thái THẬT của bài TikTok đang xử lý ('processing') ─────────────────────────────
// Content Sharing Guidelines mục 5e: phải theo dõi publish/status/fetch (hoặc webhook) thay vì coi upload
// xong = đăng xong. Poll mỗi tick (worker.js) — publish_id lưu ở social_posts.external_post_id.
export async function tickTikTokProcessing() {
  let pending
  try {
    pending = (await query(
      "SELECT * FROM social_posts WHERE platform='tiktok' AND status='processing' AND external_post_id IS NOT NULL LIMIT 20")).rows
  } catch (e) {
    log("tickTikTokProcessing query lỗi:", e?.message || e)
    return
  }
  for (const post of pending) {
    try {
      const account = (await query("SELECT * FROM social_accounts WHERE id=$1", [post.account_id])).rows[0]
      if (!account) { await query("UPDATE social_posts SET status='error', error_msg='Tài khoản đã bị ngắt kết nối' WHERE id=$1", [post.id]); continue }
      const st = await checkTikTokPublishStatus(account, post.external_post_id)
      if (!st.done) continue // vẫn đang xử lý (PROCESSING_DOWNLOAD/UPLOAD…) — tick sau kiểm tra lại
      if (st.ok) {
        await query("UPDATE social_posts SET status='posted', posted_at=now() WHERE id=$1", [post.id])
        log(`TikTok xử lý xong · post ${String(post.id).slice(0, 8)} → posted`)
      } else {
        await query("UPDATE social_posts SET status='error', error_msg=$1 WHERE id=$2", [st.message || `TikTok status=${st.status}`, post.id])
        log(`TikTok xử lý lỗi · post ${String(post.id).slice(0, 8)}: ${st.message || st.status}`)
      }
    } catch (e) {
      log(`tickTikTokProcessing post ${String(post.id).slice(0, 8)} lỗi:`, e?.message || e)
      // Không set 'error' ngay — có thể chỉ lỗi mạng tạm thời, tick sau thử lại. Nếu treo mãi, xem log để can thiệp tay.
    }
  }
}
// #endregion
