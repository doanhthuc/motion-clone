// #region ALD 31/05/2026 - Workflow worker: poll workflow_runs status=queued → chạy engine.
// Tái dùng image api (cùng deps pg). CMD: node src/wf-worker/worker.js.
import { query, waitForDb } from "../db.js"
import { runMigrations } from "../migrate.js"
import { runWorkflow } from "./engine.js"
import { tickContentPlans, tickSocialPosts, tickTikTokProcessing, onWorkflowRunFinished } from "./social-scheduler.js"

const POLL = Number(process.env.WF_POLL_INTERVAL_SEC || 2) * 1000
// ALD 12/06/2026 - chạy song song tối đa WF_CONCURRENCY run (trước đây 1 run/lần, blocking): node nặng vẫn
// được ComfyUI xếp hàng tuần tự nội bộ nên GPU an toàn; song song được lợi ở node ngoài GPU (LLM box khác,
// upload/download, gpu-free, output) và run thứ 2 không phải chờ run 1 xong toàn bộ workflow mới bắt đầu.
const CONC = Math.max(1, Number(process.env.WF_CONCURRENCY || 2))
const log = (...a) => console.log("[wf-worker]", ...a)

async function claim() {
  // Atomic: lấy 1 run queued (SKIP LOCKED chống đua nếu scale nhiều worker)
  const { rows } = await query(
    `UPDATE workflow_runs SET status='running', started_at=now()
     WHERE id = (SELECT id FROM workflow_runs WHERE status='queued'
                  ORDER BY CASE WHEN auth_method='task-cloud-auto' THEN 0 ELSE 1 END, started_at ASC
                  LIMIT 1 FOR UPDATE SKIP LOCKED)
     RETURNING *`)
  return rows[0] || null
}

async function loadDefinition(run) {
  if (run.definition && run.definition.nodes) return run.definition  // test-run (in-memory def)
  if (run.workflow_id) {
    const { rows } = await query("SELECT definition FROM workflows WHERE id=$1", [run.workflow_id])
    if (rows[0]?.definition) return rows[0].definition
  }
  return null
}

async function main() {
  await waitForDb()
  await runMigrations()   // ALD 04/06/2026 - tự áp db/init/*.sql idempotent (docker up volume cũ không thiếu cột)
  // #region ALD 04/06/2026 - Reclaim run mồ côi khi khởi động. Chỉ 1 wf-worker chạy (đơn luồng, blocking
  // 1 run/lần), nên mọi run 'running' lúc boot chắc chắn bị bỏ rơi do worker bị giết giữa chừng → fail
  // để KHÔNG kẹt 'running' mãi (FE nhận status cuối, hết treo "Running" vô hạn). User chạy lại nếu cần.
  const orphan = await query(
    `UPDATE workflow_runs SET status='error', error_msg='Worker khởi động lại — run bị gián đoạn, vui lòng chạy lại',
            finished_at=now()
     WHERE status='running' RETURNING id`).catch(() => ({ rows: [] }))
  if (orphan.rows?.length) log(`reclaim ${orphan.rows.length} run mồ côi (running → error)`)
  // #endregion
  log(`start · poll ${POLL}ms · concurrency ${CONC} · ollama=${process.env.OLLAMA_URL || "(default)"} · marker=${process.env.MARKER_URL || "(default)"}`)
  // ALD 12/06/2026 - song song hóa: giữ Set các run đang chạy, chỉ claim khi còn slot. Mỗi run chạy trong
  // promise riêng, lỗi của run nào set error run đó (không sập loop). Orphan-reclaim lúc boot vẫn đúng:
  // chỉ 1 container wf-worker nên mọi run 'running' lúc boot chắc chắn là tàn dư.
  const active = new Set()
  const runOne = async (run) => {
    try {
      log(`run ${String(run.id).slice(0, 8)} (wf=${run.workflow_id ? String(run.workflow_id).slice(0, 8) : "test"}) · ${active.size}/${CONC} slot`)
      const definition = await loadDefinition(run)
      if (!definition) {
        const msg = "Không tìm thấy definition (workflow xóa rồi?)"
        await query("UPDATE workflow_runs SET status='error', error_msg=$1, finished_at=now() WHERE id=$2", [msg, run.id])
        await onWorkflowRunFinished(run.id, { status: "error", errorMsg: msg }).catch(() => {})
        return
      }
      const res = await runWorkflow({
        runId: run.id, workflowId: run.workflow_id, userId: run.user_id,
        authMethod: run.auth_method || "session", definition, input: run.input || {},
      })
      log(`run ${String(run.id).slice(0, 8)} → ${res.status}`)
      // ALD 05/07/2026 - Social Management: nếu run này thuộc 1 content-plan slot (tickContentPlans tạo ra)
      // → cập nhật log + xếp hàng đăng MXH khi success. Run thường (không thuộc plan) → no-op tức thì.
      await onWorkflowRunFinished(run.id, res).catch((e) => log(`onWorkflowRunFinished lỗi run ${run.id}:`, e?.message || e))
    } catch (e) {
      log(`run ${String(run.id).slice(0, 8)} error:`, e?.message || e)
      await query("UPDATE workflow_runs SET status='error', error_msg=$1, finished_at=now() WHERE id=$2 AND status <> 'cancelled'",
        [String(e?.message || e), run.id]).catch(() => {})
      await onWorkflowRunFinished(run.id, { status: "error", errorMsg: String(e?.message || e) })
        .catch(() => {})
    }
  }
  // ALD 05/07/2026 - Scheduler tick Social Management: cùng process/singleton với run-claim loop nên không cần
  // lock riêng. Chu kỳ riêng (SCHEDULER_TICK_SEC, mặc định 30s) — thưa hơn POLL job vì content-plan/social-post
  // không cần độ trễ giây như job GPU.
  const SCHED_TICK = Number(process.env.SCHEDULER_TICK_SEC || 30) * 1000
  let lastSchedTick = 0
  for (;;) {
    try {
      if (Date.now() - lastSchedTick >= SCHED_TICK) {
        lastSchedTick = Date.now()
        await tickContentPlans().catch((e) => log("tickContentPlans lỗi:", e?.message || e))
        await tickSocialPosts().catch((e) => log("tickSocialPosts lỗi:", e?.message || e))
        // ALD 05/07/2026 - Content Sharing Guidelines mục 5e: xác nhận trạng thái TikTok THẬT (không coi upload
        // xong = đăng xong) — poll cùng chu kỳ với tickSocialPosts.
        await tickTikTokProcessing().catch((e) => log("tickTikTokProcessing lỗi:", e?.message || e))
      }
      if (active.size >= CONC) { await new Promise((r) => setTimeout(r, POLL)); continue }
      const run = await claim()
      if (!run) { await new Promise((r) => setTimeout(r, POLL)); continue }
      const task = runOne(run).finally(() => active.delete(task))
      active.add(task)
    } catch (e) {
      log("loop error:", e?.message || e)
      await new Promise((r) => setTimeout(r, POLL))
    }
  }
}
main().catch((e) => { console.error("[wf-worker] fatal:", e); process.exit(1) })
// #endregion
