// motions-studio/api/src/test-mc-dispatcher.mjs
// Chạy: node motions-studio/api/src/test-mc-dispatcher.mjs
import assert from "node:assert/strict"
import { decideDispatch, pruneExpired, filterQueuedTypes, isMainModule } from "./mc-dispatcher.js"

// ── isMainModule (pm2 fork mode giấu đường dẫn script khỏi argv[1]) ───────────

// Chạy tay: node api/src/mc-dispatcher.js
assert.equal(isMainModule(undefined, "/root/motion-backend/api/src/mc-dispatcher.js"), true)

// pm2 fork mode: argv[1] là lớp bọc của pm2, đường dẫn thật nằm ở pm_exec_path. Đây là ca đã làm
// dispatcher thành tiến trình rỗng mà pm2 vẫn báo online — đo trên pod thật 02/08/2026.
assert.equal(
  isMainModule("/root/motion-backend/api/src/mc-dispatcher.js", "/usr/lib/node_modules/pm2/lib/ProcessContainerFork.js"),
  true,
)

// Bị import bởi test: không có đường dẫn nào trỏ tới file này → KHÔNG chạy main().
assert.equal(isMainModule(undefined, "/usr/local/bin/node_modules/.bin/mocha"), false)
assert.equal(isMainModule(undefined, undefined), false)

// ── decideDispatch ────────────────────────────────────────────────────────────

// Không có job queued → không bắn gì. Bắn thừa là đốt tiền cold start vô ích.
assert.equal(decideDispatch({ queued: 0, inflight: 0, maxInflight: 3 }), 0)

// Một job, không có /run nào đang in-flight → bắn đúng một lần.
assert.equal(decideDispatch({ queued: 1, inflight: 0, maxInflight: 3 }), 1)

// Đã có đúng số /run in-flight bằng số job queued → không cần thêm.
assert.equal(decideDispatch({ queued: 2, inflight: 2, maxInflight: 5 }), 0)

// Trần maxInflight chặn scale vô hạn khi hàng đợi dài.
assert.equal(decideDispatch({ queued: 50, inflight: 0, maxInflight: 3 }), 3)

// In-flight nhiều hơn queued (vừa bắn xong, job vừa được nhận) → không âm.
assert.equal(decideDispatch({ queued: 1, inflight: 4, maxInflight: 3 }), 0)

// maxInflight âm (config lỗi) → không bao giờ trả số âm, kể cả khi need dương.
assert.equal(decideDispatch({ queued: 5, inflight: 0, maxInflight: -1 }), 0)

// CRITICAL 3 — có in-flight (cold start có thể chưa xong) thì KHÔNG bắn thêm cho CÙNG job đó.
assert.equal(decideDispatch({ queued: 1, inflight: 1, maxInflight: 3 }), 0)

// maxInflight là trần TỔNG in-flight, không phải trần mỗi lượt: đã có 2 /run trong cửa sổ cooldown
// thì trần 3 chỉ còn chỗ cho 1, dù hàng đợi dài bao nhiêu.
assert.equal(decideDispatch({ queued: 50, inflight: 2, maxInflight: 3 }), 1)
assert.equal(decideDispatch({ queued: 50, inflight: 3, maxInflight: 3 }), 0)

// ── Mô phỏng NHIỀU tick (lỗi cộng dồn chỉ lộ ra qua thời gian, không qua một lệnh gọi đơn lẻ) ──
//
// Chạy đúng vòng lặp của tick(): prune theo cooldown → decideDispatch → push mốc đã bắn.
// Trả về tổng số /run đã bắn và số in-flight cao nhất từng đạt.
function simulate({ queued, maxInflight, cooldownMs, pollMs, ticks }) {
  let fired = []
  let total = 0
  let peak = 0
  for (let i = 0; i < ticks; i++) {
    const now = i * pollMs
    fired = pruneExpired(fired, cooldownMs, now)
    const n = decideDispatch({ queued, inflight: fired.length, maxInflight })
    for (let k = 0; k < n; k++) fired.push(now)
    total += n
    peak = Math.max(peak, fired.length)
  }
  return { total, peak }
}

{
  // Kịch bản re-reviewer tái hiện: hàng đợi 50 job đứng yên (cold start chưa xong, chưa ai claim),
  // poll 5s, cooldown 180s. Bản cũ `Math.min(need, maxInflight)` bắn đủ 50 lần trong ~105 giây.
  // Đúng ra: trần 3 giữ nguyên suốt cả cửa sổ cooldown, hết cooldown mới được bắn tiếp.
  const oneWindow = simulate({ queued: 50, maxInflight: 3, cooldownMs: 180_000, pollMs: 5000, ticks: 36 })
  assert.equal(oneWindow.total, 3, "trong một cửa sổ cooldown chỉ được bắn tối đa maxInflight lần")
  assert.equal(oneWindow.peak, 3)

  // 21 tick nữa (tổng 57 tick = 285s) vượt qua mốc cooldown của loạt đầu → được bắn thêm đúng một loạt.
  const twoWindows = simulate({ queued: 50, maxInflight: 3, cooldownMs: 180_000, pollMs: 5000, ticks: 57 })
  assert.equal(twoWindows.total, 6)
  assert.equal(twoWindows.peak, 3, "in-flight không bao giờ vượt maxInflight")

  // Hàng đợi ngắn hơn trần: `need` mới là ràng buộc, không phải trần.
  const shortQueue = simulate({ queued: 2, maxInflight: 3, cooldownMs: 180_000, pollMs: 5000, ticks: 36 })
  assert.equal(shortQueue.total, 2)
}

// ── pruneExpired (theo dõi in-flight bằng cooldown, không phải DB) ────────────

{
  const now = 1_000_000
  const cooldownMs = 180_000 // mặc định DISPATCH_COOLDOWN_SEC=180

  // /run bắn 60s trước, cooldown 180s → vẫn còn in-flight, chưa hết cold start.
  const stillFresh = pruneExpired([now - 60_000], cooldownMs, now)
  assert.equal(stillFresh.length, 1)
  assert.equal(decideDispatch({ queued: 1, inflight: stillFresh.length, maxInflight: 3 }), 0)

  // /run bắn 200s trước, cooldown 180s → đã hết hạn (cold start coi như xong hoặc đã fail) → bắn lại được.
  const expired = pruneExpired([now - 200_000], cooldownMs, now)
  assert.equal(expired.length, 0)
  assert.equal(decideDispatch({ queued: 1, inflight: expired.length, maxInflight: 3 }), 1)

  // Trộn lẫn: một mốc còn hạn, một mốc đã hết hạn → chỉ giữ lại mốc còn hạn.
  const mixed = pruneExpired([now - 200_000, now - 10_000], cooldownMs, now)
  assert.equal(mixed.length, 1)
}

// ── filterQueuedTypes (JOB_TYPES của dispatcher phải khớp JOB_TYPES của endpoint) ─

{
  const allowed = ["motion", "teen-flycam", "trend-tiktok", "enhance"]

  // Toàn bộ job queued thuộc type NGOÀI danh sách endpoint claim được (vd 'tryon', 'talk') →
  // không lọc ra job nào → decideDispatch nhận queued=0 → không bắn gì. Đây chính là ca
  // "job type ngoài JOB_TYPES nằm queued vĩnh viễn, dispatcher bắn mãi" mà CRITICAL 3 mô tả —
  // sau fix, dispatcher không còn thấy các job này là "cần đánh thức" nữa.
  const outOfScope = filterQueuedTypes(
    [
      { id: "a", type: "tryon" },
      { id: "b", type: "talk" },
    ],
    allowed,
  )
  assert.equal(outOfScope.length, 0)
  assert.equal(decideDispatch({ queued: outOfScope.length, inflight: 0, maxInflight: 3 }), 0)

  // Trộn lẫn: chỉ giữ lại job thuộc danh sách dispatcher quản lý.
  const mixedTypes = filterQueuedTypes(
    [
      { id: "a", type: "tryon" },
      { id: "b", type: "motion" },
      { id: "c", type: "enhance" },
    ],
    allowed,
  )
  assert.equal(mixedTypes.length, 2)
}

console.log("mc-dispatcher: all assertions passed")
