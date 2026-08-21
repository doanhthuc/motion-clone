// motions-studio/api/src/mc-dispatcher.js
// Poll bảng jobs, gọi RunPod Serverless /run khi có job queued mà chưa đủ worker.
//
// Worker tỉnh dậy TỰ gọi /worker/claim — dispatcher không giao job cho ai cả, chỉ đánh thức.
// Claim đã atomic trong Postgres nên thừa một worker chỉ tốn vài giây, không sai kết quả.
//
// File riêng, không nhồi vào ecosystem.config.cjs: giữ dispatcher tách khỏi cấu hình PM2 chung để
// bật/tắt được bằng một biến môi trường mà không phải sửa file khai báo mọi tiến trình khác.
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
const DEFAULT_JOB_TYPES = "motion,teen-flycam,trend-tiktok,enhance,character-swap"
const JOB_TYPES = (process.env.DISPATCH_JOB_TYPES || DEFAULT_JOB_TYPES)
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean)

// Cold start đo được 1-3 phút (RunPod Serverless kéo image + mount volume + boot ComfyUI). Cooldown
// PHẢI ít nhất bằng mức đó, nếu không dispatcher bắn /run lặp lại MỖI vòng poll trong suốt cold start —
// một job queued duy nhất kéo cả `max workers` lên chỉ vì dispatcher tưởng chưa ai lo cho nó.
const COOLDOWN_MS = Math.max(1000, Number(process.env.DISPATCH_COOLDOWN_SEC || 180) * 1000)

// Sàn cứng cho ngưỡng reclaim. Số đo 02/08/2026 trên chính worker đang giữ job: khoảng IM LẶNG
// heartbeat dài nhất là 79 giây — heartbeat nhịp 15s trong vòng chờ ComfyUI, nhưng các pha tải
// input, nạp model và upload output thì không nhịp gì cả. Job dài hơn (1080p, nhiều frame) upload
// lâu hơn nên im lặng còn dài hơn. Ai đặt 60 giây "cho nhạy" sẽ GIẾT job đang chạy thật, và mất
// luôn tiền GPU đã trả cho nó. Sàn này tồn tại để cấu hình sai không phá được dữ liệu.
const MIN_ORPHAN_SEC = 300
const DEFAULT_ORPHAN_SEC = 900

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

/** Ngưỡng (giây) coi một job `running` là mồ côi. Hàm thuần để test được cấu hình rác.
 *
 * `0` = TẮT hẳn, có chủ ý. Giá trị dương dưới `MIN_ORPHAN_SEC` bị kéo lên sàn thay vì tôn trọng —
 * xem lý do ở chỗ khai báo sàn. Giá trị không phải số hoặc âm là cấu hình lỗi: về mặc định, KHÔNG
 * tắt tính năng, vì tắt âm thầm thì người dùng chỉ thấy thanh tiến trình treo mà không hiểu vì sao. */
export function orphanThresholdSec(raw) {
  if (raw === undefined || raw === null || String(raw).trim() === "") return DEFAULT_ORPHAN_SEC
  const n = Number(raw)
  if (!Number.isFinite(n) || n < 0) return DEFAULT_ORPHAN_SEC
  if (n === 0) return 0
  return Math.max(MIN_ORPHAN_SEC, Math.round(n))
}

const ORPHAN_SEC = orphanThresholdSec(process.env.DISPATCH_ORPHAN_SEC)

/** Chuyển job `running` mồ côi của worker serverless sang `error`.
 *
 * Vì sao cần: `api/src/routes/jobs.js:219` reclaim job theo `worker_id` — nó chỉ chạy khi CHÍNH
 * worker đó gọi `/worker/claim` lần nữa. Mỗi container serverless lại có `WORKER_ID` riêng
 * (`serverless-<pod-id>`), nên nếu container chết giữa job thì không container nào mang đúng id đó
 * để kích hoạt reclaim → job nằm `running` vĩnh viễn, người dùng nhìn một thanh tiến trình không
 * bao giờ dừng. Đây là đường reclaim theo THỜI GIAN, không theo worker.
 *
 * Hai điều kiện phải cùng đúng, không phải một:
 *   - `jobs.updated_at` cũ hơn ngưỡng — trigger `set_updated_at` chạm cột này mỗi lần PATCH tiến độ
 *   - và bảng `workers` không có nhịp heartbeat nào mới hơn ngưỡng cho đúng worker đó
 * Chỉ xét một trong hai là bắn oan: có pha job chạy mà không PATCH tiến độ (nạp model), và có pha
 * heartbeat im (upload output). Cả hai cùng im mới là container đã chết thật.
 *
 * `worker_id LIKE 'serverless-%'` KHÔNG phải trang trí: nó ngăn dispatcher đụng vào job của worker
 * local hay wf-worker — những tiến trình nó không điều khiển và đã có cơ chế reclaim riêng. */
async function reclaimOrphans() {
  if (!ORPHAN_SEC) return
  const { rows } = await query(
    `UPDATE jobs SET status='error',
            error = 'Worker serverless mất tích — không có heartbeat nào trong ' || $1 || ' giây. Hãy chạy lại.',
            finished_at = now()
      WHERE status='running'
        AND worker_id LIKE 'serverless-%'
        AND updated_at < now() - ($1 || ' seconds')::interval
        AND NOT EXISTS (
              SELECT 1 FROM workers w
               WHERE w.worker_id = jobs.worker_id
                 AND w.last_seen_at > now() - ($1 || ' seconds')::interval)
      RETURNING id, worker_id`,
    [String(ORPHAN_SEC)],
  )
  for (const r of rows) log(`job mồ côi ${r.id} của ${r.worker_id} → error (im lặng > ${ORPHAN_SEC}s)`)
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
  await reclaimOrphans()
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
      `cooldown=${COOLDOWN_MS}ms · jobTypes=${JOB_TYPES.join(",")} · ` +
      `reclaimMồCôi=${ORPHAN_SEC ? ORPHAN_SEC + "s" : "TẮT"}`,
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
