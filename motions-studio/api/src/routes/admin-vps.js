// #region Admin VPS monitor: realtime health + safe queue controls.
import { Router } from "express"
import os from "node:os"
import { readFileSync, statfsSync } from "node:fs"
import { query } from "../db.js"
import { sessionAuth, requireAdmin } from "../auth/session.js"
import { inspectResources, readMem, runResourceActions, restartComfy } from "../admin-resource-actions.js"
import { streamComfyPm2Logs } from "../pm2-comfy-logs.js"

const router = Router()
const COMFY_URL = (process.env.ADMIN_COMFY_URL || process.env.COMFY_URL || "").replace(/\/$/, "")
const RESOURCE_HELPER_URL = (process.env.ADMIN_RESOURCE_HELPER_URL || "").replace(/\/$/, "")
const RESOURCE_HELPER_TOKEN = process.env.ADMIN_RESOURCE_HELPER_TOKEN || ""
const RESOURCE_HELPER_TIMEOUT_MS = Number(process.env.ADMIN_RESOURCE_HELPER_TIMEOUT_MS || 125000)
let lastCpu = null

router.use(["/admin/vps", "/admin/server-monitor"], sessionAuth, requireAdmin)

async function safeQuery(sql, params, fallback, label) {
  try {
    const r = await query(sql, params)
    return r.rows
  } catch (e) {
    console.warn(`[admin-vps] ${label} unavailable:`, e?.message || e)
    return fallback
  }
}

function readCpu() {
  let fields
  try {
    fields = readFileSync("/proc/stat", "utf8").split("\n")[0].trim().split(/\s+/).slice(1).map(Number)
  } catch {
    return { cores: os.cpus().length, load_avg: os.loadavg(), used_pct: null }
  }
  const idle = fields[3] + (fields[4] || 0)
  const total = fields.reduce((a, b) => a + b, 0)
  let usedPct = null
  if (lastCpu) {
    const totalDelta = total - lastCpu.total
    const idleDelta = idle - lastCpu.idle
    usedPct = totalDelta > 0 ? Math.max(0, Math.min(1, (totalDelta - idleDelta) / totalDelta)) : null
  }
  lastCpu = { idle, total }
  return { cores: os.cpus().length, load_avg: os.loadavg(), used_pct: usedPct }
}

function readDisk(path = "/") {
  try {
    const st = statfsSync(path)
    const total = Number(st.blocks) * Number(st.bsize)
    const free = Number(st.bavail) * Number(st.bsize)
    const used = Math.max(0, total - free)
    return { path, total_bytes: total, used_bytes: used, free_bytes: free, used_pct: total ? used / total : 0 }
  } catch {
    return null
  }
}

function needsHostResource(mode) {
  return ["all", "ram", "cache", "memory", "swap"].includes(String(mode || "").toLowerCase())
}

async function readResourceInspect() {
  if (!RESOURCE_HELPER_URL) return inspectResources({ comfyUrl: COMFY_URL })

  const ac = new AbortController()
  const t = setTimeout(() => ac.abort(), Math.min(RESOURCE_HELPER_TIMEOUT_MS, 10000))
  try {
    const r = await fetch(`${RESOURCE_HELPER_URL}/inspect`, {
      headers: { "X-Resource-Token": RESOURCE_HELPER_TOKEN },
      signal: ac.signal,
    })
    clearTimeout(t)
    if (r.ok) {
      const payload = await r.json()
      return { ...payload, inspect_helper: { enabled: true, status: r.status } }
    }
    return {
      ...(await inspectResources({ comfyUrl: COMFY_URL })),
      inspect_helper: { enabled: true, status: r.status, error: await r.text().catch(() => r.statusText) },
    }
  } catch (e) {
    clearTimeout(t)
    return {
      ...(await inspectResources({ comfyUrl: COMFY_URL })),
      inspect_helper: { enabled: true, status: null, error: String(e?.message || e).slice(0, 800) },
    }
  }
}

async function runResourceRequest(mode) {
  if (!RESOURCE_HELPER_URL || !needsHostResource(mode)) {
    return runResourceActions(mode, { comfyUrl: COMFY_URL })
  }

  const ac = new AbortController()
  const t = setTimeout(() => ac.abort(), RESOURCE_HELPER_TIMEOUT_MS)
  try {
    const r = await fetch(`${RESOURCE_HELPER_URL}/free-resources`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Resource-Token": RESOURCE_HELPER_TOKEN,
      },
      body: JSON.stringify({ mode }),
      signal: ac.signal,
    })
    clearTimeout(t)
    const text = await r.text().catch(() => "")
    let payload = null
    try {
      payload = text ? JSON.parse(text) : null
    } catch {
      payload = null
    }
    if (payload && typeof payload === "object") {
      return {
        ...payload,
        helper: { enabled: true, status: r.status },
        success: r.ok && payload.success !== false,
      }
    }
    return {
      success: false,
      mode,
      helper: { enabled: true, status: r.status },
      memory_before: readMem(),
      memory_after: readMem(),
      steps: [{ name: "resource_helper", ok: false, status: r.status, error: text.slice(0, 1200) || r.statusText }],
    }
  } catch (e) {
    clearTimeout(t)
    return {
      success: false,
      mode,
      helper: { enabled: true, status: null },
      memory_before: readMem(),
      memory_after: readMem(),
      steps: [{ name: "resource_helper", ok: false, error: String(e?.message || e).slice(0, 1200) }],
    }
  }
}

async function runComfyRestartRequest() {
  // Ưu tiên helper (pid:host → kill được process ComfyUI của host). Helper unreachable → thử trực tiếp (Manager reboot HTTP).
  if (RESOURCE_HELPER_URL) {
    const ac = new AbortController()
    const t = setTimeout(() => ac.abort(), RESOURCE_HELPER_TIMEOUT_MS)
    try {
      const r = await fetch(`${RESOURCE_HELPER_URL}/restart-comfy`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Resource-Token": RESOURCE_HELPER_TOKEN },
        body: "{}",
        signal: ac.signal,
      })
      clearTimeout(t)
      const text = await r.text().catch(() => "")
      let payload = null
      try {
        payload = text ? JSON.parse(text) : null
      } catch {
        payload = null
      }
      if (payload && typeof payload === "object") {
        return { ...payload, helper: { enabled: true, status: r.status }, success: r.ok && payload.success !== false }
      }
      return {
        success: false,
        helper: { enabled: true, status: r.status },
        steps: [{ name: "resource_helper", ok: false, status: r.status, error: text.slice(0, 1200) || r.statusText }],
      }
    } catch (e) {
      clearTimeout(t)
      // Helper không tới được → fallback trực tiếp (chỉ Manager reboot HTTP hoạt động vì API không có pid:host).
      const direct = await restartComfy({ comfyUrl: COMFY_URL })
      return { ...direct, helper: { enabled: true, status: null, error: String(e?.message || e).slice(0, 400) } }
    }
  }
  return restartComfy({ comfyUrl: COMFY_URL })
}

function computeHealth({ cpu, mem, disk, gpu, workers, jobs }) {
  const penalties = []
  if (!workers.some((w) => w.fresh)) penalties.push(35)
  if (cpu.used_pct != null && cpu.used_pct > 0.85) penalties.push(18)
  if (mem?.used_pct > 0.85) penalties.push(24)
  if (mem?.swap_used_pct > 0.7 && mem?.available_bytes < 24 * 1024 ** 3) penalties.push(18)
  if (disk?.used_pct > 0.9) penalties.push(18)
  if (gpu?.used_pct != null && gpu.used_pct > 0.9) penalties.push(20)
  if (jobs.running > 0) penalties.push(Math.min(10, jobs.running * 3))
  if (jobs.queued > 20) penalties.push(10)
  const score = Math.max(0, Math.round(100 - penalties.reduce((a, b) => a + b, 0)))
  const status = score >= 80 ? "healthy" : score >= 55 ? "warning" : "critical"
  return { score, status }
}

// #region ALD 15/07/2026 - Tách thời gian chờ worker và thời gian xử lý
function epochMs(value) {
  const parsed = value ? new Date(value).getTime() : NaN
  return Number.isFinite(parsed) ? parsed : null
}

function withJobTimings(row, now = Date.now()) {
  const created = epochMs(row?.created_at)
  const started = epochMs(row?.started_at)
  const finished = epochMs(row?.finished_at)
  const queueEnd = started ?? (row?.status === "queued" ? now : epochMs(row?.updated_at) ?? now)
  const processingEnd = finished ?? (row?.status === "running" ? now : started)
  return {
    ...row,
    queue_duration_ms: created == null ? null : Math.max(0, queueEnd - created),
    processing_duration_ms: started == null || processingEnd == null ? 0 : Math.max(0, processingEnd - started),
  }
}
// #endregion

// #region ALD 15/06/2026 - Chẩn đoán "issue" cho Admin VPS: chỉ đích danh nguyên nhân + hành động nên làm.
// Dùng dữ liệu sẵn có (mem/ram_processes/gpu/disk/workers/jobs). Mỗi issue: { level, title, detail, action }.
// action map ở FE: "restart-comfy" -> restartComfy(); "free-swap"/"free-ram" -> freeResources(mode).
function fmtGiB(bytes) {
  return `${((Number(bytes) || 0) / 1024 ** 3).toFixed(1)}GB`
}
function isComfyProc(p) {
  const c = String(p?.command || "").toLowerCase()
  return c.includes("comfy") || c.includes("main.py") || c.includes("python")
}
function computeIssues({ mem, ramProcesses = [], gpu, disk, workers = [], jobs = {} }) {
  const issues = []

  if (mem?.used_pct != null && mem.used_pct > 0.85) {
    const top = [...ramProcesses].sort((a, b) => (b.rss_bytes || 0) - (a.rss_bytes || 0))[0]
    const level = mem.used_pct > 0.92 ? "critical" : "warning"
    const pctTxt = `${Math.round(mem.used_pct * 100)}%`
    if (top && isComfyProc(top) && (top.rss_bytes || 0) > 6 * 1024 ** 3) {
      issues.push({
        level,
        title: `RAM cao ${pctTxt} — ComfyUI giữ ${fmtGiB(top.rss_bytes)}`,
        detail: `Tiến trình "${top.command}" (pid ${top.pid}) rò RAM khi idle. Khởi động lại ComfyUI để giải phóng (supervisor tự dựng lại).`,
        action: "restart-comfy",
      })
    } else if (top) {
      issues.push({
        level,
        title: `RAM cao ${pctTxt}`,
        detail: `Top RAM: "${top.command}" (pid ${top.pid}) ${fmtGiB(top.rss_bytes)}. "Drop cache" chỉ xả page-cache, không hạ RSS của tiến trình.`,
        action: null,
      })
    } else {
      issues.push({ level, title: `RAM cao ${pctTxt}`, detail: "Cân nhắc giải phóng tài nguyên.", action: null })
    }
  }

  if (mem?.swap_used_pct != null && mem.swap_used_pct > 0.7) {
    issues.push({
      level: "warning",
      title: `Swap cao ${Math.round(mem.swap_used_pct * 100)}%`,
      detail: `Đang dùng ${fmtGiB(mem.swap_used_bytes)} swap — hệ thống chậm. Reset swap sau khi đã hạ RAM.`,
      action: "free-swap",
    })
  }

  if (gpu?.used_pct != null && gpu.used_pct > 0.92) {
    issues.push({
      level: "warning",
      title: `VRAM gần đầy ${Math.round(gpu.used_pct * 100)}%`,
      detail: "GPU sắp hết VRAM. Khởi động lại ComfyUI nếu nghẽn để giải phóng.",
      action: "restart-comfy",
    })
  }

  if (disk?.used_pct != null && disk.used_pct > 0.9) {
    issues.push({
      level: disk.used_pct > 0.95 ? "critical" : "warning",
      title: `Ổ đĩa gần đầy ${Math.round(disk.used_pct * 100)}%`,
      detail: "Dọn output/log cũ để tránh job lỗi do hết dung lượng.",
      action: null,
    })
  }

  if (workers.length && !workers.some((w) => w.fresh)) {
    issues.push({
      level: "critical",
      title: "Không có worker còn heartbeat",
      detail: "Worker có thể đã chết — job sẽ không được xử lý. Kiểm tra container worker.",
      action: null,
    })
  } else if (!workers.length) {
    issues.push({ level: "warning", title: "Chưa thấy worker nào", detail: "Bảng workers rỗng.", action: null })
  }

  if ((jobs.error || 0) > 10) {
    issues.push({
      level: "warning",
      title: `${jobs.error} job lỗi tồn đọng`,
      detail: "Cân nhắc Clear error để dọn hàng đợi.",
      action: null,
    })
  }

  return issues
}
// #endregion

async function buildStatus() {
  const [workerRows, jobRows, recentRows, activeRows, inspect] = await Promise.all([
    safeQuery(
      `SELECT worker_id, last_seen_at, active_job_id, mode, gpu_name, gpu_vram_total_mb,
              (last_seen_at > now() - interval '60 seconds') AS fresh
       FROM workers ORDER BY last_seen_at DESC`,
      [],
      [],
      "workers",
    ),
    safeQuery(
      `SELECT
         count(*) FILTER (WHERE status='queued')::int AS queued,
         count(*) FILTER (WHERE status='running')::int AS running,
         count(*) FILTER (WHERE status='error')::int AS error,
         count(*) FILTER (WHERE status='done')::int AS done,
         count(*) FILTER (WHERE status='cancelled')::int AS cancelled
       FROM jobs`,
      [],
      [{ queued: 0, running: 0, error: 0, done: 0, cancelled: 0 }],
      "jobs",
    ),
    safeQuery(
      `SELECT id, type, status, progress, current_step, worker_id, created_at, started_at, finished_at, updated_at
       FROM jobs WHERE status IN ('queued','running','error')
       ORDER BY updated_at DESC LIMIT 12`,
      [],
      [],
      "recent jobs",
    ),
    safeQuery(
      `SELECT id, type, status, progress, current_step, worker_id, created_at, started_at, finished_at, updated_at
       FROM jobs WHERE status IN ('queued','running')
       ORDER BY updated_at DESC LIMIT 20`,
      [],
      [],
      "active jobs",
    ),
    readResourceInspect(),
  ])

  const workers = workerRows.map((w) => ({
    worker_id: w.worker_id,
    last_seen_at: w.last_seen_at,
    active_job_id: w.active_job_id,
    mode: w.mode,
    gpu_name: w.gpu_name,
    gpu_vram_total_mb: w.gpu_vram_total_mb,
    fresh: w.fresh,
  }))
  const jobs = jobRows[0] || { queued: 0, running: 0, error: 0, done: 0, cancelled: 0 }
  const cpu = readCpu()
  const mem = readMem()
  const disk = readDisk("/")
  const gpu = inspect.gpu
  const health = computeHealth({ cpu, mem, disk, gpu, workers, jobs })
  const issues = computeIssues({ mem, ramProcesses: inspect.ram_processes || [], gpu, disk, workers, jobs })

  return {
    ts: new Date().toISOString(),
    host: { hostname: os.hostname(), platform: os.platform(), uptime_sec: os.uptime() },
    health,
    issues,
    cpu,
    memory: mem,
    disk,
    gpu,
    gpu_card: inspect.gpu_card,
    gpu_processes: inspect.gpu_processes,
    gpu_health: inspect.gpu_health,
    ram_processes: inspect.ram_processes,
    ram_process_source: inspect.ram_process_source,
    ram_process_error: inspect.ram_process_error,
    inspect_source: inspect.inspect_source,
    inspect_helper: inspect.inspect_helper || null,
    workers,
    jobs,
    active_jobs: activeRows.map((row) => withJobTimings(row)),
    recent_jobs: recentRows.map((row) => withJobTimings(row)),
  }
}

async function statusHandler(_req, res) {
  try {
    res.json(await buildStatus())
  } catch (e) {
    console.error("[admin-vps] status failed:", e)
    res.status(500).json({ error: e?.message || "Admin VPS status failed" })
  }
}

router.get(["/admin/vps/status", "/admin/server-monitor/status"], statusHandler)

// ALD 24/06/2026 - Lightweight ComfyUI health check: gọi thẳng /system_stats (3s timeout).
// Không cần buildStatus() nặng; dùng riêng cho status widget frontend.
router.get(["/admin/vps/comfy-health", "/admin/server-monitor/comfy-health"], async (_req, res) => {
  if (!COMFY_URL) return res.json({ up: false, error: "COMFY_URL chưa cấu hình" })
  const ac = new AbortController()
  const t = setTimeout(() => ac.abort(), 3000)
  try {
    const r = await fetch(`${COMFY_URL}/system_stats`, { signal: ac.signal })
    clearTimeout(t)
    if (!r.ok) return res.json({ up: false, error: `HTTP ${r.status}` })
    const body = await r.json()
    return res.json({
      up: true,
      version: body?.system?.comfyui_version || null,
      ram_free_mb: body?.system?.ram_free ? Math.round(body.system.ram_free / 1024 / 1024) : null,
    })
  } catch (e) {
    clearTimeout(t)
    return res.json({ up: false, error: String(e?.message || e).slice(0, 200) })
  }
})

router.get(["/admin/vps/status/stream", "/admin/server-monitor/status/stream"], async (req, res) => {
  try {
    res.set({
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      "Connection": "keep-alive",
    })
    res.flushHeaders?.()
  } catch (e) {
    console.error("[admin-vps] stream init failed:", e)
    return res.status(500).json({ error: e?.message || "Admin VPS stream failed" })
  }

  let closed = false
  req.on("close", () => { closed = true })

  const send = async () => {
    if (closed) return
    try {
      res.write(`event: status\n`)
      res.write(`data: ${JSON.stringify(await buildStatus())}\n\n`)
    } catch (e) {
      res.write(`event: error\n`)
      res.write(`data: ${JSON.stringify({ error: String(e?.message || e) })}\n\n`)
    }
  }
  await send()
  const timer = setInterval(send, 3000)
  req.on("close", () => clearInterval(timer))
})

// #region ALD 13/07/2026 - Terminal log ComfyUI dùng chung cho canvas + /admin/vps.
// Auth admin đã áp ở router.use; EventSource gửi session JWT qua ?token= như status stream.
router.get(["/admin/vps/comfy-logs/stream", "/admin/server-monitor/comfy-logs/stream"], async (req, res) => {
  try {
    await streamComfyPm2Logs(req, res)
  } catch (e) {
    console.error("[admin-vps] comfy log stream failed:", e)
    if (!res.headersSent) return res.status(500).json({ error: e?.message || "ComfyUI log stream failed" })
    res.end()
  }
})
// #endregion

router.post(["/admin/vps/clear-jobs", "/admin/server-monitor/clear-jobs"], async (req, res) => {
  const mode = String(req.body?.mode || "all")
  const rawJobIds = Array.isArray(req.body?.jobIds) ? req.body.jobIds : []
  const jobIds = rawJobIds
    .map((id) => String(id || "").trim())
    .filter((id) => /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(id))
  const statusMap = {
    queued: ["queued"],
    running: ["running"],
    error: ["error"],
    failed: ["error"],
    selected: ["queued", "running", "error"],
    all: ["queued", "running"],
    active: ["queued", "running"],
    all_with_error: ["queued", "running", "error"],
  }
  const statuses = statusMap[mode] || statusMap.all

  if (mode === "selected") {
    if (!jobIds.length) return res.status(400).json({ error: "Chưa chọn job nào" })
    const jobs = await safeQuery(
      `UPDATE jobs SET status='cancelled',
              current_step=CASE WHEN status='error' THEN COALESCE(current_step, 'admin cleared selected error') ELSE current_step END,
              error=COALESCE(error, 'Admin cancel selected jobs'),
              finished_at=COALESCE(finished_at, now())
       WHERE id = ANY($1::uuid[]) AND status = ANY($2::text[])
       RETURNING id, status`,
      [jobIds, statuses],
      [],
      "clear selected jobs",
    )
    return res.json({
      success: true,
      mode,
      statuses,
      job_ids: jobIds,
      jobs_cancelled: jobs.length,
      workflow_runs_cancelled: 0,
    })
  }

  const jobs = await safeQuery(
    `UPDATE jobs SET status='cancelled',
            current_step=CASE WHEN status='error' THEN COALESCE(current_step, 'admin cleared error') ELSE current_step END,
            error=COALESCE(error, 'Admin clear tài nguyên'),
            finished_at=COALESCE(finished_at, now())
     WHERE status = ANY($1::text[]) RETURNING id, status`,
    [statuses],
    [],
    "clear jobs",
  )
  const runs = await safeQuery(
    `UPDATE workflow_runs SET status='cancelled', error_msg=COALESCE(error_msg, 'Admin clear tài nguyên'), finished_at=COALESCE(finished_at, now())
     WHERE status = ANY($1::text[]) RETURNING id, status`,
    [statuses],
    [],
    "clear workflow runs",
  )
  res.json({ success: true, mode, statuses, jobs_cancelled: jobs.length, workflow_runs_cancelled: runs.length })
})

router.post(["/admin/vps/free-resources", "/admin/server-monitor/free-resources"], async (req, res) => {
  const mode = String(req.body?.mode || "all").toLowerCase()
  const result = await runResourceRequest(mode)
  const hardFailures = (result.steps || []).filter((s) => !s.ok && !s.skipped)
  res.status(hardFailures.length ? 207 : 200).json(result)
})

router.post(["/admin/vps/restart-comfy", "/admin/server-monitor/restart-comfy"], async (_req, res) => {
  try {
    const result = await runComfyRestartRequest()
    res.status(result.success ? 200 : 207).json(result)
  } catch (e) {
    res.status(500).json({ error: e?.message || "Restart ComfyUI failed" })
  }
})

export default router
// #endregion
