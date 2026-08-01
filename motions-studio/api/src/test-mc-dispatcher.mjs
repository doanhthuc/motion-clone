// motions-studio/api/src/test-mc-dispatcher.mjs
// Chạy: node motions-studio/api/src/test-mc-dispatcher.mjs
import assert from "node:assert/strict"
import { decideDispatch, pruneExpired, filterQueuedTypes } from "./mc-dispatcher.js"

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
