// motions-studio/api/src/mc-dispatcher.js
// Poll bảng jobs, gọi RunPod Serverless /run khi có job queued mà chưa đủ worker.
//
// Worker tỉnh dậy TỰ gọi /worker/claim — dispatcher không giao job cho ai cả, chỉ đánh thức.
// Claim đã atomic trong Postgres nên thừa một worker chỉ tốn vài giây, không sai kết quả.
//
// File MỚI, không sửa file upstream nào: scripts/sync-upstream.sh không dùng --delete nên file
// mới sống sót qua sync, còn file upstream đã sửa thì bị ghi đè.
import { query } from "./db.js"

const ENDPOINT = process.env.RUNPOD_ENDPOINT_ID || ""
const API_KEY = process.env.RUNPOD_API_KEY || ""
const POLL_MS = Math.max(2000, Number(process.env.DISPATCH_POLL_SEC || 5) * 1000)
const MAX_INFLIGHT = Math.max(1, Number(process.env.DISPATCH_MAX_INFLIGHT || 3))

// PHẢI khớp JOB_TYPES mà endpoint RunPod thực sự claim (bake trong worker/runpod/Dockerfile.selfhosted
// ENV JOB_TYPES=..., xem thêm api/src/routes/jobs.js:229 — /worker/claim chỉ lấy job có
// `type = ANY($2)` theo danh sách worker gửi lên). Lệch danh sách này theo MỘT trong hai hướng đều hỏng:
//   - Dispatcher liệt kê type mà endpoint KHÔNG claim → job nằm 'queued' vĩnh viễn, dispatcher bắn /run
//     mỗi vòng poll cho một job không ai nhận được (17.280 request/ngày nếu poll 5s, worker không bao
//     giờ về 0 — đúng lỗi CRITICAL 3 đã review).
//   - Dispatcher KHÔNG liệt kê type mà endpoint claim được → job đó chờ dispatcher đánh thức nhưng
//     không bao giờ được bắn /run, chỉ chạy khi tình cờ có job type khác đánh thức worker.
// Đổi một bên (Dockerfile.selfhosted hoặc JOB_TYPES của Endpoint) thì PHẢI đổi DISPATCH_JOB_TYPES ở đây.
const DEFAULT_JOB_TYPES = "motion,teen-flycam,trend-tiktok,enhance"
const JOB_TYPES = (process.env.DISPATCH_JOB_TYPES || DEFAULT_JOB_TYPES)
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean)

// Cold start đo được 1-3 phút (RunPod Serverless kéo image + mount volume + boot ComfyUI). Cooldown
// PHẢI ít nhất bằng mức đó, nếu không dispatcher bắn /run lặp lại MỖI vòng poll trong suốt cold start —
// một job queued duy nhất kéo cả `max workers` lên chỉ vì dispatcher tưởng chưa ai lo cho nó.
const COOLDOWN_MS = Math.max(1000, Number(process.env.DISPATCH_COOLDOWN_SEC || 180) * 1000)

const log = (...a) => console.log("[mc-dispatcher]", ...a)

// Mốc thời gian (ms) của các lần /run đã bắn mà cold start có thể CHƯA xong. Đây là bộ nhớ TRONG
// TIẾN TRÌNH dispatcher — không có DB nào cho ta biết RunPod đã dựng xong container hay chưa, nên ta
// tự đếm "đã bắn, còn trong cửa sổ cooldown" thay vì tin cột `running` của bảng jobs (xem decideDispatch).
let fired = []

/** Giữ lại các mốc /run trong `list` chưa hết cooldown tại thời điểm `now`. Hàm thuần, test được
 * không cần timer thật: gọi với `now` giả lập để mô phỏng "đã hết hạn". */
export function pruneExpired(list, cooldownMs, now) {
  return list.filter((t) => now - t < cooldownMs)
}

/** Lọc các job 'queued' CHỈ còn type mà dispatcher này chịu trách nhiệm đánh thức. Hàm thuần,
 * test được bằng mảng {type} giả lập thay vì query Postgres thật. */
export function filterQueuedTypes(rows, allowedTypes) {
  const allowed = new Set(allowedTypes)
  return rows.filter((r) => allowed.has(r.type))
}

/** Số lần /run cần bắn. Hàm thuần để test được mà không cần DB lẫn mạng.
 *
 * `inflight` = số /run đã bắn gần đây mà cold start có thể chưa xong (xem `fired`/`pruneExpired`).
 * CỐ Ý không dùng count(*) WHERE status='running' của bảng jobs: job 'running' có thể đang chạy trên
 * worker LOCAL hay wf-worker — những tiến trình dispatcher này không hề điều khiển và không cần chờ —
 * dùng nó làm mẫu số sẽ serialize oan, tưởng "đã có người lo" trong khi container serverless còn
 * nguội, và job cứ nằm queued không ai đánh thức.
 *
 * `maxInflight` là trần TỔNG số /run còn trong cửa sổ cooldown, KHÔNG phải trần mỗi vòng poll. Trần
 * mỗi vòng (`Math.min(need, maxInflight)`) trông giống nhưng cộng dồn qua nhiều tick: hàng đợi 50 job,
 * maxInflight=3, poll 5s, cooldown 180s → 105 giây bắn đủ 50 lần /run, vì mỗi tick lại thấy "còn
 * need > 0, còn được bắn 3". Trừ `inflight` khỏi trần mới chặn đúng tổng. */
export function decideDispatch({ queued, inflight, maxInflight }) {
  const need = queued - inflight
  const room = maxInflight - inflight
  // maxInflight <= 0 là input sai (config lỗi) — không bao giờ trả số âm.
  if (need <= 0 || room <= 0) return 0
  return Math.min(need, room)
}

async function queuedRows() {
  const { rows } = await query(`SELECT id, type FROM jobs WHERE status='queued'`)
  return rows
}

// `input` PHẢI khác rỗng. SDK runpod kiểm bằng độ chân trị của Python, nên `{}` (falsy) bị coi là
// THIẾU: request chết với "Job has missing field(s): id or input.", RunPod thử lại một lần rồi trả
// "job timed out after 1 retries" — một thông báo không hề gợi ý rằng lỗi nằm ở payload. Đo thật
// trên endpoint fggbwsbhidwbdi ngày 02/08/2026. Handler KHÔNG đọc `input` (nó tự claim), nên nội
// dung không quan trọng, chỉ cần có một khoá.
const WAKE_BODY = JSON.stringify({ input: { wake: 1 } })

async function fireOne() {
  const res = await fetch(`https://api.runpod.ai/v2/${ENDPOINT}/run`, {
    method: "POST",
    headers: { Authorization: `Bearer ${API_KEY}`, "Content-Type": "application/json" },
    body: WAKE_BODY,
  })
  if (!res.ok) throw new Error(`/run ${res.status} ${(await res.text()).slice(0, 200)}`)
  return (await res.json())?.id || "?"
}

async function tick() {
  const rows = await queuedRows()
  const dispatchable = filterQueuedTypes(rows, JOB_TYPES)

  fired = pruneExpired(fired, COOLDOWN_MS, Date.now())
  const n = decideDispatch({ queued: dispatchable.length, inflight: fired.length, maxInflight: MAX_INFLIGHT })
  if (!n) return
  log(`queued(${JOB_TYPES.join(",")})=${dispatchable.length} inflight=${fired.length} → đánh thức ${n} worker`)
  for (let i = 0; i < n; i++) {
    try {
      const id = await fireOne()
      fired.push(Date.now())
      log("  /run →", id)
    } catch (e) {
      // Hết capacity hoặc endpoint lỗi: job nằm nguyên 'queued', vòng sau thử lại.
      log("  /run lỗi:", e.message)
      break
    }
  }
}

async function main() {
  if (!ENDPOINT || !API_KEY) {
    log("thiếu RUNPOD_ENDPOINT_ID hoặc RUNPOD_API_KEY → không chạy")
    process.exit(1)
  }
  // DATABASE_URL không có default: db.js gọi `new pg.Pool({ connectionString: process.env.DATABASE_URL })`
  // không dotenv, không fallback — thiếu biến này thì pg rơi về default connection (localhost, user hệ
  // điều hành, không mật khẩu), lỗi bị catch trong tick() nuốt, và pm2 status vẫn báo online mãi mãi
  // trong khi dispatcher không bao giờ query được. Chết ngay lúc khởi động thay vì chạy mãi trong lỗi.
  if (!process.env.DATABASE_URL) {
    log("thiếu DATABASE_URL → không chạy (xem ecosystem.config.cjs cách nó dẫn xuất biến này từ .env)")
    process.exit(1)
  }
  log(
    `bắt đầu · endpoint=${ENDPOINT} · poll=${POLL_MS}ms · maxInflight=${MAX_INFLIGHT} · ` +
      `cooldown=${COOLDOWN_MS}ms · jobTypes=${JOB_TYPES.join(",")}`,
  )
  for (;;) {
    try {
      await tick()
    } catch (e) {
      log("tick lỗi:", e.message)
    }
    await new Promise((r) => setTimeout(r, POLL_MS))
  }
}

/** Có phải file này đang được chạy như chương trình chính không (khác với being imported bởi test)?
 *
 * KHÔNG dùng được `process.argv[1]` một mình: pm2 fork mode chạy script qua một lớp bọc, nên
 * argv[1] là `/usr/lib/node_modules/pm2/lib/ProcessContainerFork.js` chứ không phải đường dẫn
 * script. Đo trên pod thật 02/08/2026. Hậu quả của phiên bản cũ đúng bằng thứ tệ nhất có thể:
 * `pm2 start api/src/mc-dispatcher.js` báo `online`, `restarts=0`, log RỖNG HOÀN TOÀN, và
 * main() không bao giờ chạy — dispatcher là tiến trình rỗng nằm đó mãi mãi. Cả cổng kiểm log
 * trong pod-bootstrap.sh (grep 'tick lỗi:') cũng xanh, vì không có log nào để mà lỗi.
 *
 * pm2 đặt `pm_exec_path` = đường dẫn script thật, nên xét nó trước, rồi mới tới argv[1] cho
 * trường hợp chạy tay `node api/src/mc-dispatcher.js`.
 *
 * Hàm thuần, nhận đường dẫn qua tham số để test được không cần pm2. */
export function isMainModule(execPath, argvPath) {
  return [execPath, argvPath].some((p) => typeof p === "string" && p.endsWith("mc-dispatcher.js"))
}

if (isMainModule(process.env.pm_exec_path, process.argv[1])) main()
