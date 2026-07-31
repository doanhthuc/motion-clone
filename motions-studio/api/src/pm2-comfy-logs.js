// #region ALD 13/07/2026 - Stream file log PM2 của ComfyUI qua SSE cho Admin VPS.
// Đọc trực tiếp comfyui-out.log + comfyui-error.log thay vì spawn `pm2 logs` cho mỗi browser:
// ít process nền hơn, không mở shell tùy ý, vẫn trả đúng nội dung PM2 đang hiển thị.
import os from "node:os"
import path from "node:path"
import { existsSync } from "node:fs"
import { open, stat } from "node:fs/promises"

const INITIAL_BYTES = 256 * 1024
const CHUNK_BYTES = 512 * 1024
const INITIAL_LINES = 180

function unique(items) {
  return [...new Set(items.filter(Boolean).map((v) => path.resolve(v)))]
}

function candidateDirs() {
  return unique([
    process.env.COMFY_PM2_LOG_DIR,
    "/host-pm2-logs",
    path.join(process.env.PM2_HOME || path.join(os.homedir(), ".pm2"), "logs"),
  ])
}

function findLogFiles() {
  for (const dir of candidateDirs()) {
    const out = path.join(dir, "comfyui-out.log")
    const error = path.join(dir, "comfyui-error.log")
    if (existsSync(out) || existsSync(error)) {
      return [
        existsSync(out) ? { path: out, stream: "out" } : null,
        existsSync(error) ? { path: error, stream: "error" } : null,
      ].filter(Boolean)
    }
  }
  return []
}

function cleanLine(value) {
  return String(value || "")
    .replace(/\u001b\[[0-?]*[ -/]*[@-~]/g, "")
    .replace(/\r$/, "")
}

// PM2 gọi file stderr là "error.log", nhưng Python logging mặc định ghi cả INFO/DEBUG vào stderr.
// Phân loại theo NỘI DUNG để drawer không tô đỏ toàn bộ log khởi động bình thường của ComfyUI.
export function classifyLogLevel(value, stream = "out") {
  const line = cleanLine(value)
  if (/\[(?:INFO|DONE|DEBUG|SUCCESS)\]|(?:^|\s)(?:INFO|DEBUG):/i.test(line)) return "info"
  if (/\[(?:WARN|WARNING)\]|\bwarning\b|\bdeprecated\b/i.test(line)) return "warning"
  if (
    /\[(?:ERR|ERROR|FATAL|CRITICAL)\]/i.test(line)
    || /\bTraceback\b|\bException(?:\s+ignored)?\b/i.test(line)
    || /\b(?:ImportError|RuntimeError|ValueError|TypeError|KeyError|AssertionError|MemoryError|OSError|CUDAError)\b/i.test(line)
    || /\b[A-Za-z]*Error\s*:/i.test(line)
    || /\bfailed\b|\bfailure\b/i.test(line)
    || /\b(?:CUDA|cuDNN|NCCL)[^\n]*(?:error|failed|failure)\b/i.test(line)
    || /\bout of memory\b|\bOOM\b|\bsegmentation fault\b/i.test(line)
    || /\bFile "[^"]+", line \d+/i.test(line)
  ) return "error"
  return stream === "error" ? "info" : "out"
}

async function readBytes(file, start, length) {
  if (length <= 0) return ""
  const handle = await open(file, "r")
  try {
    const buffer = Buffer.alloc(length)
    const { bytesRead } = await handle.read(buffer, 0, length, start)
    return buffer.subarray(0, bytesRead).toString("utf8")
  } finally {
    await handle.close()
  }
}

async function initialState(file) {
  const info = await stat(file.path)
  const start = Math.max(0, info.size - INITIAL_BYTES)
  let text = await readBytes(file.path, start, info.size - start)
  if (start > 0) text = text.slice(Math.max(0, text.indexOf("\n") + 1))
  const lines = text.split("\n").filter(Boolean).slice(-INITIAL_LINES).map(cleanLine)
  return { ...file, offset: info.size, inode: info.ino, remainder: "", lines }
}

async function pollState(state) {
  const info = await stat(state.path)
  if (info.ino !== state.inode || info.size < state.offset) {
    state.offset = 0
    state.inode = info.ino
    state.remainder = ""
  }
  if (info.size <= state.offset) return []

  let start = state.offset
  if (info.size - start > CHUNK_BYTES) start = info.size - CHUNK_BYTES
  const text = state.remainder + await readBytes(state.path, start, info.size - start)
  state.offset = info.size
  const parts = text.split("\n")
  state.remainder = parts.pop() || ""
  return parts.map(cleanLine).filter(Boolean)
}

function send(res, event, payload) {
  if (res.destroyed || res.writableEnded) return
  res.write(`event: ${event}\n`)
  res.write(`data: ${JSON.stringify(payload)}\n\n`)
}

export async function streamComfyPm2Logs(req, res) {
  res.set({
    "Content-Type": "text/event-stream; charset=utf-8",
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
  })
  res.flushHeaders?.()

  let closed = false
  let states = []
  let polling = false
  let nextDiscoveryAt = 0
  req.on("close", () => { closed = true })

  async function loadSnapshot() {
    const files = findLogFiles()
    send(res, "meta", {
      type: "snapshot",
      process: "comfyui",
      files: files.map((f) => f.path),
      found: files.length > 0,
    })
    if (!files.length) {
      send(res, "meta", {
        type: "waiting",
        message: "Chưa tìm thấy log PM2 của ComfyUI. Kiểm tra COMFY_PM2_LOG_DIR hoặc PM2_HOME.",
      })
      states = []
      nextDiscoveryAt = Date.now() + 5000
      return
    }
    states = []
    for (const file of files) {
      try {
        const state = await initialState(file)
        states.push(state)
        for (const line of state.lines) {
          send(res, "log", { stream: state.stream, level: classifyLogLevel(line, state.stream), line })
        }
      } catch (e) {
        send(res, "meta", { type: "error", message: `Không đọc được ${file.path}: ${String(e?.message || e)}` })
      }
    }
    nextDiscoveryAt = states.length ? Number.POSITIVE_INFINITY : Date.now() + 5000
    send(res, "meta", states.length
      ? { type: "live", message: "Đang theo dõi pm2 logs comfyui" }
      : { type: "waiting", message: "Đã thấy file log nhưng chưa thể mở, sẽ tự thử lại." })
  }

  await loadSnapshot()

  const pollTimer = setInterval(async () => {
    if (closed || polling) return
    polling = true
    try {
      if (!states.length) {
        if (Date.now() < nextDiscoveryAt) return
        await loadSnapshot()
        return
      }
      for (const state of states) {
        try {
          for (const line of await pollState(state)) {
            send(res, "log", { stream: state.stream, level: classifyLogLevel(line, state.stream), line })
          }
        } catch (e) {
          send(res, "meta", { type: "error", message: `Mất file log ${state.path}: ${String(e?.message || e)}` })
          states = []
          nextDiscoveryAt = Date.now() + 3000
          break
        }
      }
    } finally {
      polling = false
    }
  }, 900)
  const keepAlive = setInterval(() => {
    if (!closed && !res.destroyed && !res.writableEnded) res.write(": keep-alive\n\n")
  }, 15000)

  req.on("close", () => {
    clearInterval(pollTimer)
    clearInterval(keepAlive)
  })
}
// #endregion
