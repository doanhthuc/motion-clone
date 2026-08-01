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
const log = (...a) => console.log("[mc-dispatcher]", ...a)

/** Số lần /run cần bắn. Hàm thuần để test được mà không cần DB lẫn mạng. */
export function decideDispatch({ queued, running, maxInflight }) {
  const need = queued - running
  if (need <= 0) return 0
  return Math.min(need, maxInflight)
}

async function counts() {
  const { rows } = await query(
    `SELECT count(*) FILTER (WHERE status='queued')  AS queued,
            count(*) FILTER (WHERE status='running') AS running
       FROM jobs`,
  )
  return { queued: Number(rows[0].queued), running: Number(rows[0].running) }
}

async function fireOne() {
  const res = await fetch(`https://api.runpod.ai/v2/${ENDPOINT}/run`, {
    method: "POST",
    headers: { Authorization: `Bearer ${API_KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify({ input: {} }),
  })
  if (!res.ok) throw new Error(`/run ${res.status} ${(await res.text()).slice(0, 200)}`)
  return (await res.json())?.id || "?"
}

async function tick() {
  const { queued, running } = await counts()
  const n = decideDispatch({ queued, running, maxInflight: MAX_INFLIGHT })
  if (!n) return
  log(`queued=${queued} running=${running} → đánh thức ${n} worker`)
  for (let i = 0; i < n; i++) {
    try {
      log("  /run →", await fireOne())
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
  log(`bắt đầu · endpoint=${ENDPOINT} · poll=${POLL_MS}ms · maxInflight=${MAX_INFLIGHT}`)
  for (;;) {
    try {
      await tick()
    } catch (e) {
      log("tick lỗi:", e.message)
    }
    await new Promise((r) => setTimeout(r, POLL_MS))
  }
}

if (process.argv[1] && process.argv[1].endsWith("mc-dispatcher.js")) main()
