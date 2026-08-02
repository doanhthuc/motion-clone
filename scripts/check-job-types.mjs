#!/usr/bin/env node
// check-job-types.mjs — giữ các danh sách job type trong repo khớp nhau.
//
//   node scripts/check-job-types.mjs      (hoặc: make check-job-types)
//
// VÌ SAO CẦN: cùng một danh sách job type bị chép ở năm chỗ, và lệch nhau thì hỏng IM LẶNG —
// job nằm 'queued' mãi mãi vì không worker nào khai nhận type đó. Không lỗi, không log, không
// ai biết cho tới khi khách hỏi. Đo ngày 02/08/2026, ba danh sách có sẵn trong repo đều lệch:
//   linux.py:64 _DEFAULT_JOB_TYPES thiếu text-to-video/ss/wan-dancer dù comment ngay trên nó
//   ghi "bao trùm MỌI node type có handler"; setup-pm2.sh:520 thiếu edit-image/reveal;
//   .env.example:94 thiếu edit-image. Ba cái đó là di sản, script này không đụng vào (chúng chỉ
//   là mặc định dự phòng). Nó khoá bốn danh sách mà hệ serverless + profile full thực sự dùng.
//
// Kiểm tra 4 điều, cái thứ tư là cái đáng giá nhất: thêm handler mới vào PIPELINES sẽ làm script
// này ĐỎ, buộc phải quyết định type mới có vào profile full hay không — thay vì lặng lẽ bị bỏ sót.
import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..")
const read = (p) => readFileSync(join(ROOT, p), "utf8")
const split = (s) => s.split(",").map((t) => t.trim()).filter(Boolean)

// Type CỐ Ý không nằm trong profile full, kèm lý do. Thêm vào đây là một quyết định, không phải
// một lần quên — nên nó phải nằm trong code chứ không nằm trong đầu ai đó.
const EXCLUDED = {
  "wan-dancer":
    "run_wan_dancer (linux.py) tự raise khi GPU < 90GB VRAM; card khuyến nghị của dự án là " +
    "RTX 5090 32GB, nên bật type này chỉ đổi 'job nằm chờ' thành 'job fail sau khi đã claim'.",
}

function grab(file, re, label) {
  const m = re.exec(read(file))
  if (!m) throw new Error(`không tìm thấy ${label} trong ${file} — file đã đổi cấu trúc?`)
  return m[1]
}

const dockerfile = "motions-studio/worker/runpod/Dockerfile.selfhosted"
const sources = {
  registry: new Set(
    [...grab("motions-studio/worker/worker_runtime/linux.py", /^PIPELINES = \{([\s\S]*?)^\}/m, "PIPELINES")
      .matchAll(/^\s*"([^"]+)":/gm)].map((m) => m[1]),
  ),
  "image full": split(grab(dockerfile, /full\) printf '%s' "([^"]+)"/, "JOB_TYPES bản full")),
  "image motion": split(grab(dockerfile, /\*\)\s+printf '%s' "([^"]+)"/, "JOB_TYPES bản motion")),
  "setup-full.sh": split(grab("motions-studio/setup/setup-full.sh", /^JOB_TYPE="([^"]+)"/m, "JOB_TYPE")),
  "setup-motion-transfer.sh": split(
    grab("motions-studio/setup/setup-motion-transfer.sh", /^JOB_TYPE="([^"]+)"/m, "JOB_TYPE"),
  ),
  dispatcher: split(
    grab("motions-studio/api/src/mc-dispatcher.js", /DEFAULT_JOB_TYPES = "([^"]+)"/, "DEFAULT_JOB_TYPES"),
  ),
}

const errors = []
const eq = (a, b) => a.length === b.length && a.every((t, i) => t === b[i])
const diff = (a, b) => {
  const only = [...a.filter((t) => !b.includes(t)).map((t) => `+${t}`),
                ...b.filter((t) => !a.includes(t)).map((t) => `-${t}`)]
  // Cùng tập type nhưng khác thứ tự thì diff rỗng — phải nói ra, không thì thông báo lỗi trống trơn.
  return only.length ? [...new Set(only)].join(" ") : "cùng tập type nhưng KHÁC THỨ TỰ"
}

// 1+2. Các danh sách phải khớp nhau theo từng nhóm (so cả THỨ TỰ: khác thứ tự thì diff giữa hai
// lần sửa khó đọc, và người ta dễ tưởng đã đồng bộ trong khi chưa).
for (const [a, b] of [
  ["image full", "setup-full.sh"],
  ["image motion", "setup-motion-transfer.sh"],
  ["image motion", "dispatcher"],
]) {
  if (!eq(sources[a], sources[b])) errors.push(`${a} ≠ ${b}: ${diff(sources[a], sources[b])}`)
}

// 3. Không danh sách nào được chứa type KHÔNG có handler — worker sẽ claim rồi set error.
for (const [name, list] of Object.entries(sources)) {
  if (name === "registry") continue
  const ghost = list.filter((t) => !sources.registry.has(t))
  if (ghost.length) errors.push(`${name} có type không có handler trong PIPELINES: ${ghost.join(", ")}`)
}

// 4. Mọi handler phải hoặc nằm trong profile full, hoặc được loại trừ TƯỜNG MINH kèm lý do.
const missing = [...sources.registry].filter(
  (t) => !sources["image full"].includes(t) && !(t in EXCLUDED),
)
if (missing.length) {
  errors.push(
    `handler có trong PIPELINES nhưng KHÔNG có trong profile full: ${missing.join(", ")}\n` +
      `      → hoặc thêm vào JOB_TYPE của setup-full.sh + JOB_TYPES bản full của Dockerfile,\n` +
      `        hoặc khai vào EXCLUDED trong ${"scripts/check-job-types.mjs"} kèm lý do.`,
  )
}
const stale = Object.keys(EXCLUDED).filter((t) => !sources.registry.has(t))
if (stale.length) errors.push(`EXCLUDED liệt kê type đã không còn handler: ${stale.join(", ")}`)

if (errors.length) {
  console.error("✗ job type lệch nhau:\n" + errors.map((e) => `    - ${e}`).join("\n"))
  process.exit(1)
}
console.log(
  `✓ job type khớp — registry ${sources.registry.size} handler · full ${sources["image full"].length} · ` +
    `motion ${sources["image motion"].length} · loại trừ: ${Object.keys(EXCLUDED).join(", ") || "không"}`,
)
