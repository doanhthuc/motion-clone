// #region ALD 10/08/2026 - RAM THẬT của box = trần cgroup, KHÔNG phải os.totalmem().
//
// RunPod cho container thấy /proc/meminfo của HOST chứ không thấy trần cgroup, nên os.totalmem()
// trên pod motion trả 123 GiB trong khi container chỉ được cấp 55,9 GiB:
//
//     /sys/fs/cgroup/memory.max   59999997952   trần THẬT
//     /proc/meminfo MemTotal      129429860 kB  RAM host — os.totalmem() đọc cái này
//
// Đây là con bug đã cắn hai lần: heap Node lúc build FE (trị ở scripts/pod-fe.sh #cgroup) và
// ComfyUI bị cgroup OOM-kill giữa job motion 453 frame (trị ở scripts/pysite/sitecustomize.py,
// commit 82c9e58). Lần này nó cắn GET /workflows/capabilities: FE gate preset nặng theo
// totalRamGb, thấy 123 GiB nên mở những preset mà container không gánh nổi.
//
// Cùng thuật toán với sitecustomize.py `_detect_cgroup` — giữ ĐỒNG BỘ khi sửa một trong hai.
import { readFileSync } from "node:fs"
import os from "node:os"

// cgroup v1 ghi "không giới hạn" bằng một số khổng lồ (~2^63) thay vì chuỗi "max".
const UNLIMITED = 2n ** 62n

const LIMIT_PATHS = [
  "/sys/fs/cgroup/memory.max",                        // cgroup v2
  "/sys/fs/cgroup/memory/memory.limit_in_bytes",      // cgroup v1
]

function readLimitBytes(path) {
  try {
    const raw = readFileSync(path, "utf8").trim()
    if (!raw || raw === "max") return 0
    const v = BigInt(raw)
    return v <= 0n || v >= UNLIMITED ? 0 : Number(v)
  } catch {
    return 0
  }
}

/**
 * Trần RAM thật của box (bytes). Trả trần cgroup khi nó CHẶT HƠN RAM host; ngược lại
 * (máy thật, pod không giới hạn, macOS dev) trả os.totalmem() như cũ.
 *
 * `opts` chỉ để test — production luôn gọi không tham số.
 */
export function boxRamBytes({ hostBytes = os.totalmem(), paths = LIMIT_PATHS } = {}) {
  for (const path of paths) {
    const limit = readLimitBytes(path)
    if (limit > 0 && limit < hostBytes) return limit
  }
  return hostBytes
}

export function boxRamGb(opts) {
  return Math.round(boxRamBytes(opts) / 1024 ** 3)
}
// #endregion
