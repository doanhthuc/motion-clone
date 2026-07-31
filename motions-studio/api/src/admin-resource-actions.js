import { execFile } from "node:child_process"
import { readFileSync, writeFileSync, readdirSync } from "node:fs"
import { promisify } from "node:util"

const RESOURCE_COMMAND_TIMEOUT_MS = Number(process.env.ADMIN_RESOURCE_COMMAND_TIMEOUT_MS || 120000)
const INSPECT_COMMAND_TIMEOUT_MS = Number(process.env.ADMIN_INSPECT_COMMAND_TIMEOUT_MS || 3000)
const NVIDIA_SMI = process.env.ADMIN_NVIDIA_SMI_PATH || "nvidia-smi"
const execFileAsync = promisify(execFile)

export function readMem() {
  const info = {}
  try {
    for (const line of readFileSync("/proc/meminfo", "utf8").split("\n")) {
      const m = line.match(/^(\w+):\s+(\d+)/)
      if (m) info[m[1]] = Number(m[2]) * 1024
    }
  } catch {
    return null
  }
  const total = info.MemTotal || 0
  const available = info.MemAvailable || 0
  const used = Math.max(0, total - available)
  const swapTotal = info.SwapTotal || 0
  const swapFree = info.SwapFree || 0
  return {
    total_bytes: total,
    used_bytes: used,
    available_bytes: available,
    used_pct: total ? used / total : 0,
    swap_total_bytes: swapTotal,
    swap_used_bytes: Math.max(0, swapTotal - swapFree),
    swap_used_pct: swapTotal ? (swapTotal - swapFree) / swapTotal : 0,
  }
}

export async function comfyPost(comfyUrl, path, body, name) {
  const baseUrl = String(comfyUrl || "").replace(/\/$/, "")
  if (!baseUrl) return { name, ok: false, skipped: true, error: "COMFY_URL chưa cấu hình" }
  try {
    const ac = new AbortController()
    const t = setTimeout(() => ac.abort(), 5000)
    const r = await fetch(`${baseUrl}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
      signal: ac.signal,
    })
    clearTimeout(t)
    const text = await r.text().catch(() => "")
    return {
      name,
      ok: r.ok,
      status: r.status,
      message: text ? text.slice(0, 500) : (r.ok ? "ok" : r.statusText),
    }
  } catch (e) {
    const msg = String(e?.message || e)
    // Timeout/connection-refused = ComfyUI không chạy → skip (không phải lỗi hệ thống)
    const unreachable = /aborted|ECONNREFUSED|ENOTFOUND|ECONNRESET/i.test(msg)
    return unreachable
      ? { name, ok: false, skipped: true, error: msg.slice(0, 800) }
      : { name, ok: false, error: msg.slice(0, 800) }
  }
}

export async function runCommand(name, cmd, args = []) {
  try {
    const { stdout, stderr } = await execFileAsync(cmd, args, {
      timeout: RESOURCE_COMMAND_TIMEOUT_MS,
      maxBuffer: 256 * 1024,
    })
    return {
      name,
      ok: true,
      stdout: String(stdout || "").slice(0, 1000),
      stderr: String(stderr || "").slice(0, 1000),
    }
  } catch (e) {
    return {
      name,
      ok: false,
      code: e?.code || null,
      error: String(e?.stderr || e?.message || e).slice(0, 1200),
    }
  }
}

async function runInspectCommand(name, cmd, args = []) {
  try {
    const { stdout, stderr } = await execFileAsync(cmd, args, {
      timeout: INSPECT_COMMAND_TIMEOUT_MS,
      maxBuffer: 1024 * 1024,
    })
    return { name, ok: true, stdout: String(stdout || ""), stderr: String(stderr || "") }
  } catch (e) {
    return {
      name,
      ok: false,
      code: e?.code || null,
      error: String(e?.stderr || e?.message || e).slice(0, 1200),
    }
  }
}

function asNumber(v) {
  const n = Number(String(v ?? "").trim())
  return Number.isFinite(n) ? n : null
}

function mbToBytes(v) {
  const n = asNumber(v)
  return n == null ? null : n * 1024 * 1024
}

function pctValue(v) {
  const n = asNumber(v)
  return n == null ? null : Math.max(0, Math.min(1, n / 100))
}

function splitCsvLine(line) {
  return String(line || "").split(",").map((s) => s.trim())
}

export async function readComfyGpu(comfyUrl) {
  const baseUrl = String(comfyUrl || "").replace(/\/$/, "")
  if (!baseUrl) return null
  try {
    const ac = new AbortController()
    const t = setTimeout(() => ac.abort(), 2500)
    const r = await fetch(`${baseUrl}/system_stats`, { signal: ac.signal })
    clearTimeout(t)
    if (!r.ok) return null
    const d = await r.json()
    const dev = (d.devices || [])[0]
    if (!dev) return null
    const total = Number(dev.vram_total || 0)
    const free = Number(dev.vram_free || 0)
    const used = total && free ? Math.max(0, total - free) : null
    return {
      source: "comfyui",
      endpoint: baseUrl,
      name: dev.name || null,
      total_bytes: total || null,
      used_bytes: used,
      free_bytes: free || null,
      used_pct: total && used != null ? used / total : null,
    }
  } catch {
    return null
  }
}

export async function readRamProcesses(limit = 12) {
  const r = await runInspectCommand("ps_ram", "ps", [
    "-eo",
    "pid,ppid,rss,vsz,pcpu,pmem,stat,comm",
    "--sort=-rss",
  ])
  if (!r.ok) return { source: "ps", error: r.error, processes: [] }
  const processes = r.stdout
    .trim()
    .split("\n")
    .slice(1, limit + 1)
    .map((line) => {
      const parts = line.trim().split(/\s+/)
      const [pid, ppid, rssKb, vszKb, pcpu, pmem, stat, ...commParts] = parts
      return {
        pid: asNumber(pid),
        ppid: asNumber(ppid),
        rss_bytes: (asNumber(rssKb) || 0) * 1024,
        vsz_bytes: (asNumber(vszKb) || 0) * 1024,
        cpu_pct: asNumber(pcpu),
        mem_pct: asNumber(pmem),
        stat: stat || "",
        command: commParts.join(" ") || "",
      }
    })
    .filter((p) => p.pid)
  return { source: "ps", processes }
}

export async function readNvidiaSmi() {
  const gpuFields = [
    "index",
    "uuid",
    "name",
    "driver_version",
    "memory.total",
    "memory.used",
    "memory.free",
    "utilization.gpu",
    "utilization.memory",
    "temperature.gpu",
    "power.draw",
    "power.limit",
    "fan.speed",
    "pstate",
    "pcie.link.gen.current",
    "pcie.link.width.current",
  ]
  const gpuResult = await runInspectCommand("nvidia_smi_gpu", NVIDIA_SMI, [
    `--query-gpu=${gpuFields.join(",")}`,
    "--format=csv,noheader,nounits",
  ])
  if (!gpuResult.ok) {
    return { source: "nvidia-smi", available: false, error: gpuResult.error, gpus: [], processes: [] }
  }

  const gpus = gpuResult.stdout
    .trim()
    .split("\n")
    .filter(Boolean)
    .map((line) => {
      const v = splitCsvLine(line)
      const total = mbToBytes(v[4])
      const used = mbToBytes(v[5])
      const free = mbToBytes(v[6])
      return {
        index: asNumber(v[0]),
        uuid: v[1] || null,
        name: v[2] || null,
        driver_version: v[3] || null,
        total_bytes: total,
        used_bytes: used,
        free_bytes: free,
        used_pct: total && used != null ? used / total : null,
        gpu_util_pct: pctValue(v[7]),
        memory_util_pct: pctValue(v[8]),
        temperature_c: asNumber(v[9]),
        power_draw_w: asNumber(v[10]),
        power_limit_w: asNumber(v[11]),
        fan_pct: pctValue(v[12]),
        pstate: v[13] || null,
        pcie_gen: asNumber(v[14]),
        pcie_width: asNumber(v[15]),
      }
    })

  const appResult = await runInspectCommand("nvidia_smi_apps", NVIDIA_SMI, [
    "--query-compute-apps=pid,process_name,used_memory",
    "--format=csv,noheader,nounits",
  ])
  const processes = appResult.ok
    ? appResult.stdout
        .trim()
        .split("\n")
        .filter(Boolean)
        .map((line) => {
          const v = splitCsvLine(line)
          return {
            gpu_uuid: null,
            pid: asNumber(v[0]),
            process_name: v[1] || null,
            used_bytes: mbToBytes(v[2]),
          }
        })
        .filter((p) => p.pid)
    : []

  return {
    source: "nvidia-smi",
    available: true,
    gpus,
    processes,
    process_error: appResult.ok ? null : appResult.error,
  }
}

export function computeGpuHealth(gpu, gpuProcesses = []) {
  if (!gpu) return { score: 0, status: "critical", reasons: ["Không đọc được GPU"] }
  const reasons = []
  let penalty = 0
  if (gpu.used_pct != null && gpu.used_pct > 0.92) {
    penalty += 30
    reasons.push("VRAM gần đầy")
  } else if (gpu.used_pct != null && gpu.used_pct > 0.8) {
    penalty += 16
    reasons.push("VRAM cao")
  }
  if (gpu.temperature_c != null && gpu.temperature_c >= 84) {
    penalty += 28
    reasons.push("GPU nóng")
  } else if (gpu.temperature_c != null && gpu.temperature_c >= 78) {
    penalty += 14
    reasons.push("Nhiệt GPU cao")
  }
  if (gpu.power_draw_w != null && gpu.power_limit_w && gpu.power_draw_w / gpu.power_limit_w > 0.95) {
    penalty += 10
    reasons.push("Power sát giới hạn")
  }
  if (gpu.gpu_util_pct != null && gpu.gpu_util_pct > 0.95 && gpu.used_pct != null && gpu.used_pct > 0.75) {
    penalty += 8
    reasons.push("GPU đang tải nặng")
  }
  if (gpuProcesses.length > 3) {
    penalty += 8
    reasons.push("Nhiều process giữ VRAM")
  }
  const score = Math.max(0, Math.round(100 - penalty))
  const status = score >= 80 ? "healthy" : score >= 55 ? "warning" : "critical"
  return { score, status, reasons }
}

export async function inspectResources({ comfyUrl = "" } = {}) {
  const [comfyGpu, nvidia, ram] = await Promise.all([
    readComfyGpu(comfyUrl),
    readNvidiaSmi(),
    readRamProcesses(),
  ])
  const primaryGpu = nvidia.gpus?.[0] || null
  const gpu = primaryGpu
    ? { ...primaryGpu, source: "nvidia-smi", comfy: comfyGpu }
    : comfyGpu
  return {
    inspect_source: nvidia.available ? "nvidia-smi" : (comfyGpu ? "comfyui" : "local"),
    gpu,
    gpu_card: nvidia,
    gpu_processes: nvidia.processes || [],
    gpu_health: computeGpuHealth(gpu, nvidia.processes || []),
    ram_processes: ram.processes || [],
    ram_process_source: ram.source,
    ram_process_error: ram.error || null,
  }
}

export async function runCommandWithSudoFallback(name, cmd, args = []) {
  const direct = await runCommand(name, cmd, args)
  if (direct.ok) return direct
  const sudo = await runCommand(`${name}_sudo`, "/usr/bin/sudo", ["-n", cmd, ...args])
  if (sudo.ok) return { ...sudo, name, sudo: true }
  return { ...direct, sudo_error: sudo.error || sudo.stderr || null }
}

export async function dropRamCaches() {
  const sync = await runCommand("sync", "sync")
  try {
    writeFileSync("/proc/sys/vm/drop_caches", "3\n")
    return [
      sync,
      { name: "drop_caches", ok: true, message: "wrote 3 to /proc/sys/vm/drop_caches" },
    ]
  } catch (e) {
    const fallback = await runCommand(
      "drop_caches_sudo",
      "/usr/bin/sudo",
      ["-n", "/bin/sh", "-lc", "sync; echo 3 > /proc/sys/vm/drop_caches"],
    )
    return [
      sync,
      fallback.ok
        ? { ...fallback, name: "drop_caches", sudo: true }
        : {
            name: "drop_caches",
            ok: false,
            error: String(e?.message || e).slice(0, 800),
            sudo_error: fallback.error || fallback.stderr || null,
          },
    ]
  }
}

export async function resetSwap() {
  // nsenter vào host mount namespace vì container có mount ns riêng (không thấy /swap.img của host)
  const off = await runCommand("swapoff", "nsenter", ["--mount=/proc/1/ns/mnt", "--", "swapoff", "-a"])
  if (!off.ok) return [off]
  const on = await runCommand("swapon", "nsenter", ["--mount=/proc/1/ns/mnt", "--", "swapon", "-a"])
  return [off, on]
}

export async function runResourceActions(mode, { comfyUrl = "" } = {}) {
  const normalizedMode = String(mode || "all").toLowerCase()
  const memoryBefore = readMem()
  const startedAt = Date.now()
  const steps = []

  if (["all", "gpu", "comfy", "vram"].includes(normalizedMode)) {
    steps.push(await comfyPost(comfyUrl, "/interrupt", {}, "comfy_interrupt"))
    steps.push(await comfyPost(comfyUrl, "/free", { unload_models: true, free_memory: true }, "comfy_free"))
  }
  if (["all", "ram", "cache", "memory"].includes(normalizedMode)) {
    steps.push(...await dropRamCaches())
  }
  if (["all", "swap"].includes(normalizedMode)) {
    steps.push(...await resetSwap())
  }

  const hardFailures = steps.filter((s) => !s.ok && !s.skipped)
  return {
    success: hardFailures.length === 0,
    mode: normalizedMode,
    duration_ms: Date.now() - startedAt,
    memory_before: memoryBefore,
    memory_after: readMem(),
    steps,
  }
}

// #region ALD 14/06/2026 - Khởi động lại ComfyUI (giải phóng VRAM → reboot). Same-box (resource-helper pid:host): kill process, supervisor/pm2 tự dựng lại (theo bài học [[comfyui-ram-leak-defer]]). Cross-box: thử ComfyUI-Manager reboot qua HTTP.
function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms))
}

async function comfyAlive(base, timeoutMs = 3000) {
  if (!base) return false
  try {
    const ac = new AbortController()
    const t = setTimeout(() => ac.abort(), timeoutMs)
    const r = await fetch(`${base}/system_stats`, { signal: ac.signal })
    clearTimeout(t)
    return r.ok
  } catch {
    return false
  }
}

// Chờ ComfyUI đạt trạng thái mong muốn (up=true để chờ sống lại, false để xác nhận đã rớt).
async function waitComfyState(base, wantUp, maxMs) {
  const deadline = Date.now() + maxMs
  // eslint-disable-next-line no-constant-condition
  while (Date.now() < deadline) {
    if ((await comfyAlive(base)) === wantUp) return true
    await sleep(1500)
  }
  return false
}

// #region ALD 15/06/2026 - Chờ ComfyUI sống lại sau khi kill, ƯU TIÊN nhận biết qua PID mới.
// Lý do: helper có pid:host nên thấy /proc host => phát hiện process ComfyUI mới mà KHÔNG cần HTTP.
// HTTP /system_stats qua host.docker.internal:8188 có thể bị ufw chặn (container→host) => nếu chỉ dựa
// HTTP thì restart thành công vẫn bị báo fail + treo chờ vô ích. PID mới cũng xuất hiện sớm hơn HTTP
// (HTTP phải import xong model). Trả { up, via: "pid" | "http" | null }.
async function waitComfyBack(base, oldPids, maxMs) {
  const old = new Set(oldPids || [])
  const deadline = Date.now() + maxMs
  while (Date.now() < deadline) {
    const cur = findComfyPids()
    if (cur.some((p) => !old.has(p))) return { up: true, via: "pid" }
    if (await comfyAlive(base)) return { up: true, via: "http" }
    await sleep(1500)
  }
  return { up: false, via: null }
}
// #endregion

// pid:host => /proc thấy process của host. Khớp tiến trình ComfyUI: python main.py --listen ... 8188.
function findComfyPids() {
  const pids = []
  let entries = []
  try {
    entries = readdirSync("/proc")
  } catch {
    return pids
  }
  for (const e of entries) {
    if (!/^\d+$/.test(e)) continue
    let cmd = ""
    try {
      cmd = readFileSync(`/proc/${e}/cmdline`, "utf8").replace(/\0/g, " ").trim().toLowerCase()
    } catch {
      continue
    }
    if (!cmd) continue
    const isComfy =
      cmd.includes("main.py") &&
      cmd.includes("python") &&
      (cmd.includes("comfyui") || cmd.includes("8188") || cmd.includes("--listen"))
    if (isComfy) pids.push(Number(e))
  }
  return pids
}

// ComfyUI-Manager: GET /api/manager/reboot khiến server tự thoát rồi launcher dựng lại. Hoạt động cross-box (qua HTTP).
async function comfyManagerReboot(base) {
  if (!base) return { name: "comfy_manager_reboot", ok: false, skipped: true, error: "COMFY_URL chưa cấu hình" }
  for (const ep of ["/api/manager/reboot", "/manager/reboot"]) {
    try {
      const ac = new AbortController()
      const t = setTimeout(() => ac.abort(), 8000)
      await fetch(`${base}${ep}`, { method: "GET", signal: ac.signal }).catch(() => null)
      clearTimeout(t)
    } catch {
      // reboot làm rớt kết nối là bình thường
    }
  }
  // Xác nhận: server phải RỚT trong ~8s. Nếu endpoint không tồn tại, server vẫn sống => Manager không có.
  const wentDown = await waitComfyState(base, false, 8000)
  return wentDown
    ? { name: "comfy_manager_reboot", ok: true, message: "ComfyUI-Manager reboot: server đã rớt để khởi động lại" }
    : { name: "comfy_manager_reboot", ok: false, skipped: true, error: "ComfyUI-Manager reboot không khả dụng (server không rớt)" }
}

export async function restartComfy({ comfyUrl = "" } = {}) {
  const base = String(comfyUrl || "").replace(/\/$/, "")
  const startedAt = Date.now()
  const steps = []
  let method = null

  // 1) Giải phóng VRAM trước (graceful) để lần khởi động lại sạch.
  steps.push(await comfyPost(comfyUrl, "/interrupt", {}, "comfy_interrupt"))
  steps.push(await comfyPost(comfyUrl, "/free", { unload_models: true, free_memory: true }, "comfy_free"))

  const pids = findComfyPids()
  if (pids.length) {
    // Same-box: kill process => supervisor/pm2 tự dựng lại (đường đã được kiểm chứng trên box GPU).
    for (const pid of pids) {
      try {
        process.kill(pid, "SIGTERM")
        steps.push({ name: `comfy_sigterm_${pid}`, ok: true })
      } catch (e) {
        steps.push({ name: `comfy_sigterm_${pid}`, ok: false, error: String(e?.message || e) })
      }
    }
    await sleep(4000)
    for (const pid of pids) {
      try {
        process.kill(pid, 0) // còn sống?
        process.kill(pid, "SIGKILL")
        steps.push({ name: `comfy_sigkill_${pid}`, ok: true })
      } catch {
        // đã thoát = tốt
      }
    }
    method = "kill_supervisor"
  } else {
    // Không thấy process (khác box hoặc không có pid:host) => thử Manager reboot qua HTTP.
    const reboot = await comfyManagerReboot(base)
    steps.push(reboot)
    if (reboot.ok) method = "manager_reboot"
    else
      steps.push({
        name: "comfy_restart",
        ok: false,
        skipped: !base,
        error:
          "Không thấy process ComfyUI và Manager reboot không khả dụng — bật resource-helper (pid:host) hoặc cài ComfyUI-Manager.",
      })
  }

  // 2) Chờ ComfyUI sống lại.
  // Same-box (kill_supervisor): ưu tiên PID mới (không phụ thuộc HTTP có thể bị ufw chặn) → báo đúng & nhanh.
  // Cross-box (manager_reboot) hoặc không có method: chỉ còn tín hiệu HTTP.
  let up = false
  let upVia = null
  if (method === "kill_supervisor") {
    const back = await waitComfyBack(base, pids, 90000)
    up = back.up
    upVia = back.via
  } else if (method) {
    up = await waitComfyState(base, true, 60000)
    upVia = up ? "http" : null
  } else {
    up = await comfyAlive(base)
    upVia = up ? "http" : null
  }
  steps.push({
    name: "comfy_up",
    ok: up,
    message: up ? (upVia === "pid" ? "ComfyUI đã dựng lại (phát hiện PID mới)" : "ComfyUI đã trả /system_stats") : undefined,
    error: up ? undefined : "ComfyUI chưa dựng lại (supervisor có thể đang khởi động, thử Làm mới sau ~30s)",
  })

  const hardFailures = steps.filter((s) => !s.ok && !s.skipped)
  return {
    success: Boolean(method) && (up || hardFailures.length === 0),
    method,
    comfy_up: up,
    duration_ms: Date.now() - startedAt,
    steps,
  }
}
// #endregion
