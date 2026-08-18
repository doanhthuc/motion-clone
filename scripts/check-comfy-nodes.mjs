#!/usr/bin/env node
// check-comfy-nodes.mjs — giữ BỐN danh sách custom node của ComfyUI khớp nhau.
//
//   node scripts/check-comfy-nodes.mjs      (hoặc: make check-comfy-nodes)
//
// VÌ SAO CẦN — đây không phải rủi ro lý thuyết, nó đã xảy ra và tốn một phiên nghiệm thu:
//
//   21/07/2026  fix "chu mỏ" làm workflow motion PHỤ THUỘC ComfyUI-WanAnimatePreprocess
//               (worker_runtime/linux.py:1657 dựng node 26 PoseAndFaceDetection).
//   17/08/2026  phát hiện MỌI job motion trên RunPod âm thầm fallback DWPose pad128, và
//               "sửa" bằng cách thêm node vào motions-studio/comfyui/Dockerfile —
//               một file KHÔNG WORKFLOW NÀO BUILD. Bản sửa vô hiệu 100% từ đầu.
//   18/08/2026  nghiệm thu trên pod thật: node vẫn vắng, phải clone tay rồi restart comfyui.
//
// Cái làm nó lọt được cả tháng: bốn danh sách chép tay ở bốn chỗ, comment nói chúng "phải
// khớp", và KHÔNG GÌ ép. Thêm node vào ba chỗ rồi quên chỗ thứ tư thì mọi thứ vẫn xanh —
// đúng loại hỏng im lặng mà check-job-types.mjs tồn tại để chặn, chỉ khác đối tượng.
//
// Bốn nguồn, và quan hệ giữa chúng (theo chính comment trong worker-image/Dockerfile):
//   worker-image/Dockerfile            image prebuilt — clone list + CỔNG TỰ KIỂM của nó
//   worker/runpod/Dockerfile.selfhosted image serverless — base (PROFILE=motion) + nhánh full
//   setup/setup-full.sh                COMFY_NODES đường không-prebuilt, profile full
//   setup/setup-motion-transfer.sh     COMFY_NODES đường không-prebuilt, profile motion
//
// Năm điều được kiểm, cái đầu là cái đã hỏng thật:
//   1. clone list của worker-image == danh sách trong cổng tự kiểm của CHÍNH NÓ.
//      Cổng liệt kê ít hơn clone list thì một node biến mất khỏi image mà build vẫn xanh.
//   2. worker-image == setup-full.sh          (cùng profile full, hai đường cài)
//   3. worker-image == selfhosted base+full   (hai image, cùng profile full)
//   4. setup-motion-transfer.sh == selfhosted base   (cùng profile motion)
//   5. sha ghim phải TRÙNG giữa hai image. Comment gốc: "Hai image ghim CÙNG commit là cố
//      ý: lệch nhau thì một bên chạy được mà bên kia không, và không có gì để so."
import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..")
const read = (p) => readFileSync(join(ROOT, p), "utf8")
const nameOf = (url) => url.split("/").pop()

const WORKER_IMAGE = "motions-studio/worker-image/Dockerfile"
const SELFHOSTED = "motions-studio/worker/runpod/Dockerfile.selfhosted"
const SETUP_FULL = "motions-studio/setup/setup-full.sh"
const SETUP_MOTION = "motions-studio/setup/setup-motion-transfer.sh"

// "https://github.com/kijai/ComfyUI-KJNodes.git 4d46ac1" → { url: …, sha: … }
function pinnedList(text, label) {
  const found = [...text.matchAll(/"(https:\/\/github\.com\/[^ "]+?)\.git\s+([0-9a-f]{7,40})"/g)]
  if (!found.length) throw new Error(`không tìm thấy dòng "<url>.git <sha>" nào trong ${label} — file đã đổi cấu trúc?`)
  return new Map(found.map((m) => [m[1], m[2]]))
}

// COMFY_NODES="https://… https://…" (không ghim sha ở đường setup)
function comfyNodes(file) {
  const m = /^COMFY_NODES="([^"]+)"/m.exec(read(file))
  if (!m) throw new Error(`không tìm thấy COMFY_NODES trong ${file} — file đã đổi cấu trúc?`)
  return new Set(m[1].trim().split(/\s+/).map((u) => u.replace(/\.git$/, "").replace(/\/$/, "")))
}

const wiText = read(WORKER_IMAGE)
const wiClone = pinnedList(wiText, WORKER_IMAGE)

// Cổng tự kiểm của worker-image: `for d in A B C \n D; do test -d …`
function verifyGateNames() {
  // `for d in A B C \` + newline + `   D; do \` + newline + `   test -d "…"`.
  // Dấu \ nối dòng nằm CẢ giữa danh sách LẪN giữa `; do` và `test -d` — bắt cả hai bằng
  // [\s\\]* thay vì đoán đúng thứ tự \ và \n.
  const m = /for d in ([A-Za-z0-9_\-\s\\]+?); do[\s\\]*test -d/.exec(wiText)
  if (!m) throw new Error(`không tìm thấy cổng tự kiểm (\`for d in … test -d\`) trong ${WORKER_IMAGE} — file đã đổi cấu trúc?`)
  return new Set(m[1].split(/[\s\\]+/).filter(Boolean))
}

const shText = read(SELFHOSTED)
const shBase = pinnedList(shText.split('case "$PROFILE"')[0], `${SELFHOSTED} (base)`)
const fullBranch = shText.includes('full) set -- "$@"')
  ? shText.split('full) set -- "$@"')[1].split(";;")[0]
  : (() => { throw new Error(`không tìm thấy nhánh \`full) set -- "$@"\` trong ${SELFHOSTED}`) })()
const shFull = pinnedList(fullBranch, `${SELFHOSTED} (full)`)

const errors = []
const diff = (a, b, labelA, labelB) => {
  const onlyA = [...a].filter((x) => !b.has(x)).sort()
  const onlyB = [...b].filter((x) => !a.has(x)).sort()
  if (onlyA.length || onlyB.length) {
    errors.push(
      `${labelA} ≠ ${labelB}\n` +
        (onlyA.length ? `    chỉ có ở ${labelA}: ${onlyA.join(", ")}\n` : "") +
        (onlyB.length ? `    chỉ có ở ${labelB}: ${onlyB.join(", ")}\n` : ""),
    )
  }
}

// 1. Cổng tự kiểm của worker-image phải phủ ĐÚNG clone list của chính nó.
diff(
  new Set([...wiClone.keys()].map(nameOf)),
  verifyGateNames(),
  "worker-image clone list",
  "cổng tự kiểm của worker-image",
)

// 2. worker-image (full) == setup-full.sh
diff(new Set(wiClone.keys()), comfyNodes(SETUP_FULL), "worker-image", "setup-full.sh COMFY_NODES")

// 3. worker-image (full) == selfhosted base+full
const shAll = new Set([...shBase.keys(), ...shFull.keys()])
diff(new Set(wiClone.keys()), shAll, "worker-image", "selfhosted PROFILE=full (base+full)")

// 4. setup-motion-transfer.sh == selfhosted base (PROFILE=motion)
diff(comfyNodes(SETUP_MOTION), new Set(shBase.keys()), "setup-motion-transfer.sh COMFY_NODES", "selfhosted base (PROFILE=motion)")

// 5. sha ghim phải trùng giữa hai image.
for (const [url, sha] of new Map([...shBase, ...shFull])) {
  const other = wiClone.get(url)
  if (other && other !== sha) {
    errors.push(
      `${nameOf(url)}: sha LỆCH giữa hai image — worker-image ghim ${other}, selfhosted ghim ${sha}.\n` +
        `    Hai image ghim cùng commit là cố ý: lệch nhau thì một bên chạy được mà bên kia không.\n`,
    )
  }
}

if (errors.length) {
  console.error("✗ danh sách custom node ComfyUI đã trôi:\n")
  for (const e of errors) console.error(`  ${e}`)
  console.error(
    "  Thêm/bớt node thì phải sửa CẢ BỐN chỗ (và cổng tự kiểm của worker-image):\n" +
      `    ${WORKER_IMAGE}\n    ${SELFHOSTED}\n    ${SETUP_FULL}\n    ${SETUP_MOTION}\n\n` +
      "  KHÔNG sửa motions-studio/comfyui/Dockerfile — không workflow nào build nó (xem\n" +
      "  cảnh báo ở đầu file đó; nó đã lừa được một lần, tốn cả một phiên nghiệm thu).\n",
  )
  process.exit(1)
}

console.log(
  `✓ custom node khớp — ${wiClone.size} node (full) · ${shBase.size} (motion) · ` +
    `cổng tự kiểm phủ đủ · sha trùng giữa hai image`,
)
