// #region ALD 15/06/2026 - Lõi cài model on-demand cho tab "Models AI".
//   - loadCatalog()          : đọc catalog.json (curated) + model_catalog_custom (admin thêm qua UI).
//   - getInstalledComfy()    : quét đệ quy COMFY_MODELS_DIR + uploads → Map(basename → size) (check "đã cài").
//   - getInstalledOllama()   : GET {OLLAMA_URL}/api/tags → Map(name → size).
//   - enqueueInstall(...)    : tạo row model_downloads (queued) → scheduler chạy nền (aria2c / ollama pull).
//   - cancelDownload(id)     : kill tiến trình đang chạy.
//   Tải nền: ComfyUI dùng aria2c -x16 (song song, nhanh) → <type>/<file>.part → rename. Ollama stream /api/pull.
//   Progress ghi vào model_downloads (UI poll). Giới hạn MAX_CONCURRENT để khỏi nghẽn băng thông/đĩa.
import { spawn } from "node:child_process"
import fs from "node:fs"
import path from "node:path"
import { readFile } from "node:fs/promises"
import { query } from "./db.js"

const COMFY_MODELS_DIR = process.env.COMFY_MODELS_DIR || "/comfy-models"
const UPLOADS_DIR = process.env.MODEL_UPLOADS_DIR || "/model-uploads"
const CATALOG_PATH = process.env.MODEL_CATALOG_PATH || "/app/catalog.json"
const OLLAMA_URL = (process.env.OLLAMA_URL || "http://host.docker.internal:11434").replace(/\/$/, "")
const HF_TOKEN = process.env.HF_TOKEN || ""
const MAX_CONCURRENT = Math.max(1, Number(process.env.MODEL_DL_CONCURRENCY || 2))
const HIDE_QWEN_TRANSLATE_MODELS = String(process.env.HIDE_QWEN_TRANSLATE_MODELS || "").toLowerCase() === "1"
  || String(process.env.HIDE_QWEN_TRANSLATE_MODELS || "").toLowerCase() === "true"
const QWEN_TRANSLATE_MODEL_IDS = new Set(["ollama-qwen25-7b", "ollama-qwen25vl-7b"])
const MODEL_CATALOG_PLATFORM = String(process.env.MODEL_CATALOG_PLATFORM || "").toLowerCase()

export const COMFY_TYPES = new Set(["loras", "checkpoints", "unet", "vae", "text_encoders", "clip_vision", "diffusion_models", "upscale_models", "FlashVSR-v1.1"])

// downloadId -> { kill: () => void } cho cancel.
const active = new Map()

// ── Catalog ──────────────────────────────────────────────────────────────────
export async function loadCatalog() {
  let cat = { comfy: [], ollama: [] }
  try { cat = JSON.parse(await readFile(CATALOG_PATH, "utf8")) } catch (e) { console.warn("[models] không đọc được catalog.json:", e?.message || e) }
  let custom = []
  try { custom = (await query("SELECT * FROM model_catalog_custom ORDER BY created_at DESC")).rows } catch { /* bảng có thể chưa migrate */ }
  const comfyCustom = custom.filter((r) => r.kind !== "ollama").map((r) => ({
    id: `custom:${r.id}`, group: "Tùy biến (admin thêm)", label: r.label || r.filename, type: r.type,
    filename: r.filename, url: r.url, sizeBytes: r.size_bytes != null ? Number(r.size_bytes) : null,
    tier: "optional", gated: false, custom: true, note: r.note || "",
  }))
  const ollamaCustom = custom.filter((r) => r.kind === "ollama").map((r) => ({
    id: `custom:${r.id}`, group: "Tùy biến (admin thêm)", label: r.label || r.model, model: r.model,
    sizeBytes: r.size_bytes != null ? Number(r.size_bytes) : null, tier: "optional", custom: true, note: r.note || "",
  }))
  let curatedComfy = cat.comfy || []
  if (MODEL_CATALOG_PLATFORM) {
    curatedComfy = curatedComfy.filter((e) => !e.platform || e.platform === "all" || e.platform === MODEL_CATALOG_PLATFORM)
  }
  const curatedOllama = HIDE_QWEN_TRANSLATE_MODELS
    ? (cat.ollama || []).filter((e) => !QWEN_TRANSLATE_MODEL_IDS.has(e.id))
    : (cat.ollama || [])
  return { comfy: [...curatedComfy, ...comfyCustom], ollama: [...curatedOllama, ...ollamaCustom] }
}

// ── "Đã cài?" ────────────────────────────────────────────────────────────────
function scanDir(root, depth, out) {
  let ents
  try { ents = fs.readdirSync(root, { withFileTypes: true }) } catch { return }
  for (const e of ents) {
    const p = path.join(root, e.name)
    if (e.isDirectory()) { if (depth > 0) scanDir(p, depth - 1, out) }
    else if (e.isFile() && !e.name.endsWith(".part")) { try { out.set(e.name, fs.statSync(p).size) } catch { /* noop */ } }
  }
}
// Quét theo basename → không phụ thuộc model nằm ở unet/ hay diffusion_models/ (ComfyUI alias 2 thư mục này).
export function getInstalledComfy() {
  const out = new Map()
  scanDir(COMFY_MODELS_DIR, 3, out)
  scanDir(UPLOADS_DIR, 3, out)
  return out
}
export async function getInstalledOllama() {
  try {
    const r = await fetch(`${OLLAMA_URL}/api/tags`, { signal: AbortSignal.timeout(5000) })
    if (!r.ok) return new Map()
    const j = await r.json()
    const m = new Map()
    for (const x of (j.models || [])) m.set(x.name, x.size || null)
    return m
  } catch { return new Map() }
}

// ── Tải nền ──────────────────────────────────────────────────────────────────
async function setRow(id, fields) {
  const keys = Object.keys(fields)
  if (!keys.length) return
  const sets = keys.map((k, i) => `${k}=$${i + 2}`).join(", ")
  try { await query(`UPDATE model_downloads SET ${sets}, updated_at=now() WHERE id=$1`, [id, ...keys.map((k) => fields[k])]) } catch { /* noop */ }
}

function runComfy(row) {
  const dir = path.join(COMFY_MODELS_DIR, row.type)
  try { fs.mkdirSync(dir, { recursive: true }) } catch { /* noop */ }
  const dest = path.join(dir, row.ref)
  const tmp = `${dest}.part`
  // ALD 16/06/2026 - HuggingFace dùng backend Xet (URL ký theo byte-range CỐ ĐỊNH) → aria2 -x16 chia range bị
  // HTTP 403 ("Cài model" qua UI fail mọi model HF). HF → TẢI 1 LUỒNG (-x1 -s1); host khác (civitai…) giữ 16 luồng.
  const isHF = /huggingface\.co/.test(row.url || "")
  const args = [isHF ? "-x1" : "-x16", isHF ? "-s1" : "-s16", "-k1M", "--file-allocation=none", "--auto-file-renaming=false", "--allow-overwrite=true",
    "--summary-interval=0", "--console-log-level=warn", "-d", dir, "-o", `${row.ref}.part`]
  if (HF_TOKEN && isHF) args.push("--header", `Authorization: Bearer ${HF_TOKEN}`)
  args.push(row.url)

  let child
  try { child = spawn("aria2c", args, { stdio: ["ignore", "ignore", "pipe"] }) }
  catch (e) { setRow(row.id, { status: "error", error: `Không chạy được aria2c: ${e?.message || e}` }).then(pump); return }

  let stderr = ""
  child.stderr.on("data", (d) => { stderr = (stderr + d.toString()).slice(-2000) })
  const iv = setInterval(() => { try { setRow(row.id, { done_bytes: fs.statSync(tmp).size }) } catch { /* chưa có .part */ } }, 2000)
  active.set(row.id, { kill: () => { try { child.kill("SIGTERM") } catch { /* noop */ } } })

  // ALD 16/06/2026 - aria2c thiếu/không chạy (vd ENOENT) → 'error' bắn nhưng 'exit' KHÔNG → trước đây chỉ ghi
  // stderr → record kẹt "running 0%" MÃI (enduser thấy spinner treo). Phải set lỗi RÕ + dọn ở đây.
  let _errored = false
  child.on("error", (e) => {
    _errored = true
    clearInterval(iv); active.delete(row.id)
    const msg = /ENOENT/.test(String(e?.message || e)) ? "Thiếu aria2c trên server (apt install aria2)" : (e?.message || String(e))
    setRow(row.id, { status: "error", error: msg }).then(pump)
  })
  child.on("exit", async (code) => {
    if (_errored) return
    clearInterval(iv); active.delete(row.id)
    if (code === 0 && fs.existsSync(tmp)) {
      try { fs.renameSync(tmp, dest); const sz = fs.statSync(dest).size; await setRow(row.id, { status: "done", done_bytes: sz, total_bytes: sz, error: null }) }
      catch (e) { await setRow(row.id, { status: "error", error: String(e?.message || e) }) }
    } else {
      try { if (fs.existsSync(tmp)) fs.unlinkSync(tmp) } catch { /* noop */ }
      const cancelled = code === null
      await setRow(row.id, { status: cancelled ? "cancelled" : "error", error: cancelled ? null : (stderr.split("\n").filter(Boolean).pop() || `aria2c exit ${code}`) })
    }
    pump()
  })
}

async function runOllama(row) {
  const ac = new AbortController()
  active.set(row.id, { kill: () => ac.abort() })
  try {
    const r = await fetch(`${OLLAMA_URL}/api/pull`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: row.ref, stream: true }), signal: ac.signal,
    })
    if (!r.ok || !r.body) throw new Error(`ollama pull HTTP ${r.status}`)
    const reader = r.body.getReader(); const dec = new TextDecoder()
    let buf = "", total = 0, done = 0, last = 0
    for (;;) {
      const { value, done: fin } = await reader.read(); if (fin) break
      buf += dec.decode(value, { stream: true })
      let nl
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim(); buf = buf.slice(nl + 1)
        if (!line) continue
        let ev; try { ev = JSON.parse(line) } catch { continue }
        if (ev.error) throw new Error(ev.error)
        if (ev.total) total = ev.total
        if (ev.completed != null) done = ev.completed
        const now = Date.now()
        if (now - last > 1500) { last = now; await setRow(row.id, { total_bytes: total || null, done_bytes: done || 0 }) }
      }
    }
    active.delete(row.id)
    await setRow(row.id, { status: "done", total_bytes: total || null, done_bytes: total || done || 0, error: null })
  } catch (e) {
    active.delete(row.id)
    const cancelled = ac.signal.aborted
    await setRow(row.id, { status: cancelled ? "cancelled" : "error", error: cancelled ? null : String(e?.message || e) })
  }
  pump()
}

// Scheduler: chạy tối đa MAX_CONCURRENT row 'queued' (cũ nhất trước), còn lại đợi.
let pumping = false
async function pump() {
  if (pumping) return
  pumping = true
  try {
    while (active.size < MAX_CONCURRENT) {
      const { rows } = await query("SELECT * FROM model_downloads WHERE status='queued' ORDER BY created_at ASC LIMIT 1")
      const row = rows[0]
      if (!row) break
      await setRow(row.id, { status: "running" })
      if (row.kind === "ollama") runOllama(row); else runComfy(row) // active.set chạy đồng bộ trước await đầu tiên
    }
  } catch (e) { console.warn("[models] pump lỗi:", e?.message || e) }
  finally { pumping = false }
}

export async function enqueueInstall({ kind, ref, type, url, catalogId, sizeBytes, userId }) {
  const { rows } = await query(
    `INSERT INTO model_downloads (kind, ref, type, url, catalog_id, status, total_bytes, started_by)
     VALUES ($1,$2,$3,$4,$5,'queued',$6,$7) RETURNING *`,
    [kind, ref, type || null, url || null, catalogId || null, sizeBytes || null, userId || null])
  pump()
  return rows[0]
}

export function cancelDownload(id) {
  const a = active.get(id)
  if (a) { a.kill(); return true }
  return false
}

// Tìm file theo basename trong models dir + uploads → trả full path.
function scanPaths(root, depth, out) {
  let ents
  try { ents = fs.readdirSync(root, { withFileTypes: true }) } catch { return }
  for (const e of ents) {
    const p = path.join(root, e.name)
    if (e.isDirectory()) { if (depth > 0) scanPaths(p, depth - 1, out) }
    else if (e.isFile() && !out.has(e.name)) out.set(e.name, p)
  }
}
export function removeComfyFile(filename) {
  const base = path.basename(String(filename || ""))
  if (!base) return null
  const m = new Map()
  scanPaths(COMFY_MODELS_DIR, 3, m)
  scanPaths(UPLOADS_DIR, 3, m)
  const fp = m.get(base)
  if (!fp) return null
  try { fs.unlinkSync(fp); return fp } catch { return null }
}
export async function deleteOllama(model) {
  const r = await fetch(`${OLLAMA_URL}/api/delete`, {
    method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ model }),
  })
  return r.ok
}

// Sau khi api restart: download 'running' mồ côi (child đã chết) → đánh lỗi; 'queued' để pump chạy tiếp.
async function reconcileOnBoot() {
  try { await query("UPDATE model_downloads SET status='error', error='API khởi động lại khi đang tải', updated_at=now() WHERE status='running'") } catch { /* noop */ }
  pump()
}
setTimeout(() => { reconcileOnBoot() }, 4000)
// #endregion
