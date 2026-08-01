// motions-studio/api/src/test-mc-dispatcher.mjs
// Chạy: node motions-studio/api/src/test-mc-dispatcher.mjs
import assert from "node:assert/strict"
import { decideDispatch } from "./mc-dispatcher.js"

// Không có job queued → không bắn gì. Bắn thừa là đốt tiền cold start vô ích.
assert.equal(decideDispatch({ queued: 0, running: 0, maxInflight: 3 }), 0)

// Một job, không có worker nào chạy → bắn đúng một lần.
assert.equal(decideDispatch({ queued: 1, running: 0, maxInflight: 3 }), 1)

// Đã có worker đang chạy đúng bằng số job queued → không cần thêm.
assert.equal(decideDispatch({ queued: 2, running: 2, maxInflight: 5 }), 0)

// Trần maxInflight chặn scale vô hạn khi hàng đợi dài.
assert.equal(decideDispatch({ queued: 50, running: 0, maxInflight: 3 }), 3)

// Đang chạy nhiều hơn queued (job vừa được nhận) → không âm.
assert.equal(decideDispatch({ queued: 1, running: 4, maxInflight: 3 }), 0)

console.log("mc-dispatcher: 5 assertions passed")
