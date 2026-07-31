// #region ALD 31/05/2026 - Node handlers (port _shared/node-registry.ts + ai-providers.ts).
// Path text/AI/control đầy đủ (chat Ollama, image vision, ocr Chandra, http, condition, ...).
// Media nodes (motion/tryon/fashion/ss/create-image) → tạo job motion-backend (Phase tiếp).
import crypto from "node:crypto"
import { query } from "../db.js"
import { putObject, browserUrl, getObjectStream } from "../storage.js"
import path from "node:path"
import { scheduleSocialPosts } from "./social-scheduler.js"
import { enforceMotionResolution } from "../motion-resolution.js"
import { detectGpuVramGb, WAN_DANCER_MIN_VRAM_GB } from "../gpu-vram.js"
import { enforceTaskCloudEnhancePolicy } from "../task-cloud/enhance-policy.js"

const OLLAMA_URL = (process.env.OLLAMA_URL || "http://172.17.0.1:11434").replace(/\/$/, "")
const MARKER_URL = (process.env.MARKER_URL || "http://supabase-marker:8001").replace(/\/$/, "")
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

// ── ai-providers: callChat (ollama | custom OpenAI-compat) ──
async function loadProviderConfig(userId, provider) {
  const { rows } = await query(
    "SELECT api_key, base_url, default_model, is_active FROM ai_provider_keys WHERE user_id=$1 AND provider=$2",
    [userId, provider])
  if (!rows[0] || !rows[0].is_active) return null
  return rows[0]
}
async function callChat(userId, provider, opts) {
  const cfg = await loadProviderConfig(userId, provider)
  const apiKey = opts.customApiKey || cfg?.api_key || null
  const baseUrl = opts.customBaseUrl || cfg?.base_url || null
  if (provider === "custom") {
    if (!apiKey && !baseUrl) throw new Error("Custom provider cần base_url + api_key")
    const url = (baseUrl || "https://api.openai.com/v1").replace(/\/$/, "") + "/chat/completions"
    const headers = { "Content-Type": "application/json" }
    if (apiKey) headers.Authorization = `Bearer ${apiKey}`
    const res = await fetch(url, { method: "POST", headers, body: JSON.stringify({
      model: opts.model, messages: opts.messages, temperature: opts.temperature ?? 0.3, max_tokens: opts.maxTokens ?? 131072 }) })
    if (!res.ok) throw new Error(`Custom API ${res.status}: ${(await res.text()).slice(0, 300)}`)
    const d = await res.json()
    return { text: d.choices?.[0]?.message?.content ?? "", model: d.model ?? opts.model, provider: "custom",
      usage: { input: d.usage?.prompt_tokens, output: d.usage?.completion_tokens } }
  }
  // ollama
  const url = (baseUrl || OLLAMA_URL).replace(/\/$/, "") + "/api/chat"
  const body = { model: opts.model, messages: opts.messages, stream: false,
    options: { temperature: opts.temperature ?? 0.3, num_predict: opts.maxTokens ?? 131072, num_ctx: opts.numCtx ?? 131072 } }
  if (opts.keepAlive != null) body.keep_alive = opts.keepAlive
  if (opts.format != null) body.format = opts.format           // 'json' → Ollama ép output JSON hợp lệ
  if (opts.think != null) body.think = opts.think              // false → tắt suy luận qwen3 (khỏi ăn token + kèm <think>)
  const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
  if (!res.ok) throw new Error(`Ollama ${res.status}: ${(await res.text()).slice(0, 300)}`)
  const d = await res.json()
  return { text: d.message?.content ?? "", model: d.model ?? opts.model, provider: "ollama",
    usage: { input: d.prompt_eval_count, output: d.eval_count } }
}

// Model dùng cho tất cả AI director (story-film, face-motion, …). Env key giữ nguyên để không phải cập nhật VPS.
const SCRIPT_MODEL = process.env.SCRIPT_MODEL || ""
const PROMPT_TRANSLATE_MODEL = (process.env.PROMPT_TRANSLATE_MODEL || SCRIPT_MODEL || "qwen2.5:7b-instruct").trim()

function looksVietnamese(text) {
  const s = String(text || "").trim()
  if (!s) return false
  if (/[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]/i.test(s)) return true
  return /\b(kich ban|phân cảnh|canh|hình ảnh|hanh dong|lời thoại|thoại|sản phẩm|người mẫu|bao bì|ưu đãi|nhắn tin|tư vấn|thương hiệu)\b/i.test(s)
}

async function translatePromptToEnglish(text, userId, ctx, label) {
  const raw = String(text || "").trim()
  if (!raw || !looksVietnamese(raw)) return raw
  const sys = `You are a cinematic prompt translator for video generation models.
Translate Vietnamese prompt text into concise, natural English prompt text suitable for Wan image/video generation.
Rules:
- Output only the translated English prompt.
- Preserve product/ad meaning, camera intent, motion, lighting, and scene details.
- Do not explain anything.
- Keep it compact and production-friendly.`
  try {
    ctx?.emit?.("info", `Dịch prompt ${label || "Wan"}: VI -> EN`)
    const { text: out } = await callChat(userId, "ollama", {
      model: PROMPT_TRANSLATE_MODEL,
      messages: [{ role: "system", content: sys }, { role: "user", content: raw }],
      temperature: 0.2,
      maxTokens: 512,
      numCtx: 4096,
      keepAlive: 0,
      think: false,
    })
    const clean = String(out || "").replace(/<think>[\s\S]*?<\/think>/gi, "").replace(/```/g, "").trim()
    if (!clean) return raw
    return clean
  } catch (e) {
    ctx?.emit?.("warn", `Dịch prompt lỗi (${label || "Wan"}) → dùng prompt gốc: ${e?.message || e}`)
    return raw
  }
}

async function maybeTranslateWanPrompt(type, params, ctx) {
  const isWanText = type === "text-to-video" || type === "wan-i2v"
  const isWanSs = type === "ss" && ["wan2.1", "wan2.2", "wan-v2v"].includes(String(params.model || "").toLowerCase())
  if (!isWanText && !isWanSs) return params
  const out = { ...params }
  if (String(out.prompt || "").trim()) {
    const original = String(out.prompt).trim()
    const translated = await translatePromptToEnglish(original, ctx.userId, ctx, type)
    if (translated && translated !== original) {
      out.promptVi = original
      out.prompt = translated
    }
  }
  if (String(out.negativePrompt || "").trim() && looksVietnamese(out.negativePrompt)) {
    const originalNeg = String(out.negativePrompt).trim()
    const translatedNeg = await translatePromptToEnglish(originalNeg, ctx.userId, ctx, `${type} negative`)
    if (translatedNeg && translatedNeg !== originalNeg) {
      out.negativePromptVi = originalNeg
      out.negativePrompt = translatedNeg
    }
  }
  return out
}

function mediaJobTimeoutSec(type, params) {
  const explicitMin = Number(params.renderTimeoutMin || params.timeoutMin || 0)
  const explicitSec = Number(params.renderTimeoutSec || params.timeoutSec || params.jobTimeoutSec || 0)
  if (Number.isFinite(explicitMin) && explicitMin > 0) return Math.max(60, Math.round(explicitMin * 60))
  if (Number.isFinite(explicitSec) && explicitSec > 0) return Math.max(60, Math.round(explicitSec))

  const shotlistCount = Array.isArray(params.shotlist) ? params.shotlist.length : 0
  const requestedShots = Number(params.numShots || params.shotCount || params.shot_count || 0)
  const shotCount = Math.max(1, shotlistCount || (Number.isFinite(requestedShots) && requestedShots > 0 ? requestedShots : 0))
  const heavyTypes = new Set(["story-film", "face-motion", "text-to-video", "wan-i2v", "teen-flycam", "trend-tiktok", "ss", "voiceover", "enhance", "wan-dancer"])
  if (heavyTypes.has(type)) {
    // Wan/Qwen can take 4-8 minutes per shot on a busy GPU.
    // Keep the workflow run alive longer than the child worker job instead of failing at 30 minutes.
    return Math.min(4 * 60 * 60, Math.max(45 * 60, (15 * 60) + shotCount * (12 * 60)))
  }
  if (type === "create-image") {
    const outCount = Math.max(1, Number(params.outputCount || params.output_count || 1) || 1)
    return Math.min(90 * 60, Math.max(30 * 60, 10 * 60 + outCount * 5 * 60))
  }
  // ALD 01/07/2026 - edit-image: N ảnh × outputCount version → scale timeout theo tổng số kết quả.
  if (type === "edit-image") {
    const outCount = Math.max(1, Number(params.outputCount || params.output_count || 1) || 1)
    const imgCount = Math.max(1, Number(params.inputCount || params.input_count || 1) || 1)
    return Math.min(90 * 60, Math.max(30 * 60, 10 * 60 + imgCount * outCount * 3 * 60))
  }
  return 30 * 60
}

function normalizeMotionDriverSegment(type, params) {
  if (type !== "motion" || !params || typeof params !== "object") return params
  const out = { ...params }
  out.deliveryPreset = "source"
  out.delivery_preset = "source"
  out.fitDriver = false
  out.fit_driver = false
  delete out.bgAnchor
  delete out.bg_anchor
  delete out.bgAnchorMaskExpand
  delete out.bg_anchor_mask_expand
  delete out.bgAnchorMaskBlur
  delete out.bg_anchor_mask_blur
  delete out.motionHandFix
  delete out.motion_hand_fix
  delete out.hand_fix_steps
  delete out.hand_fix_pose_strength
  delete out.hand_fix_clip_strength
  const start = Number(out.driverStartSec ?? out.driver_start_sec ?? 0)
  const dur = Number(out.driverDurSec ?? out.driver_dur_sec ?? 0)
  if (!Number.isFinite(start) || !Number.isFinite(dur) || dur <= 0) return out

  // ALD 28/06/2026 - Multi-outfit legacy compatibility: some saved/run snapshots
  // used driverDurSec as endSec (5/10, 10/15). Worker expects duration.
  if (start > 0 && Math.abs(dur - (start + 5)) < 0.001) {
    out.driverDurSec = 5
    out.driver_dur_sec = 5
  }
  const fixedDur = Number(out.driverDurSec ?? out.driver_dur_sec ?? dur)
  if (Math.abs(fixedDur - 5) < 0.001 && start % 5 === 0) {
    out.preset = "5s-720p"
    out.frames = 81
    out.steps = 4
    out.skipFirstFrames = 0
    out.skip_first_frames = 0
  }
  return out
}
// ALD 03/06/2026 - AI ĐẠO DIỄN phim: KỊCH BẢN → JSON {characters, shots}. Tự nhận diện nhân vật chính/phụ/
// quần chúng + giọng (gender), tách cảnh (bối cảnh + khung hình + lời thoại từng nhân vật). Cho node story-film.
async function parseStoryScript(raw, userId, model, maxShots) {
  const cap = Number(maxShots) > 0 ? `TỐI ĐA ${maxShots} cảnh (gộp lại cho vừa nếu kịch bản dài)` : "3-8 cảnh"
  const sys = `Bạn là ĐẠO DIỄN phim. Người dùng dán 1 KỊCH BẢN (có thể lộn xộn). Phân tích và trả JSON cho pipeline dựng phim nhân vật.
CHỈ trả JSON thuần (KHÔNG markdown, KHÔNG suy luận, KHÔNG giải thích), schema:
{
  "characters": [
    { "id": "<slug ngắn không dấu, vd 'mai'>", "name": "<tên hiển thị>", "role": "main|supporting|extra", "gender": "male|female",
      "appearance": "<MÔ TẢ NGOẠI HÌNH chi tiết bằng TIẾNG ANH cho model sinh ảnh: tuổi, khuôn mặt, kiểu tóc, trang phục>" }
  ],
  "shots": [
    { "id": <số>, "setting": "<MÔ TẢ BỐI CẢNH bằng TIẾNG ANH: địa điểm, ánh sáng, tông màu, không có người thừa>",
      "framing": "<close-up | medium shot | wide shot>",
      "speakerId": "<id nhân vật ĐANG NÓI ở cảnh này; rỗng nếu cảnh KHÔNG thoại>",
      "line": "<LỜI THOẠI nhân vật nói — GIỮ NGUYÊN tiếng Việt của kịch bản; rỗng nếu cảnh hành động>",
      "action": "<chuyển động/biểu cảm ngắn bằng TIẾNG ANH>",
      "durationSec": <số giây nếu cảnh KHÔNG thoại> }
  ]
}
Quy tắc:
- Tự NHẬN DIỆN diễn viên chính/phụ (main/supporting) và quần chúng (extra). gender đúng (→ giọng nam/nữ).
- ${cap}. Cảnh có thoại: speakerId trỏ ĐÚNG 1 nhân vật trong "characters", line là lời người đó nói.
- appearance + setting + framing + action viết TIẾNG ANH. line + name GIỮ tiếng Việt gốc.
- Mỗi nhân vật có thoại PHẢI có trong "characters" với id khớp speakerId.`
  let { text } = await callChat(userId, "ollama", {
    model: (model || "").trim() || SCRIPT_MODEL,
    messages: [{ role: "system", content: sys }, { role: "user", content: String(raw || "").slice(0, 8000) }],
    temperature: 0.3, maxTokens: 6144, numCtx: 12288, keepAlive: 0, format: "json", think: false,
  })
  text = String(text || "").replace(/<think>[\s\S]*?<\/think>/gi, "").replace(/```json|```/gi, "").trim()
  const m = text.match(/\{[\s\S]*\}/)
  if (!m) throw new Error("LLM không trả JSON")
  const obj = JSON.parse(m[0])
  const slug = (s, f) => String(s || f).toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 24) || f
  const characters = (Array.isArray(obj.characters) ? obj.characters : []).filter((c) => c && typeof c === "object").map((c, i) => ({
    id: slug(c.id || c.name, `c${i}`),
    name: String(c.name || c.id || `Nhân vật ${i + 1}`).slice(0, 40),
    role: ["main", "supporting", "extra"].includes(String(c.role || "").toLowerCase()) ? String(c.role).toLowerCase() : (i === 0 ? "main" : "supporting"),
    gender: String(c.gender || "").toLowerCase().startsWith("m") ? "male" : "female",
    appearance: String(c.appearance || c.desc || c.name || "a Vietnamese person").slice(0, 400),
  }))
  let shots = (Array.isArray(obj.shots) ? obj.shots : []).filter((s) => s && typeof s === "object").map((s, i) => ({
    id: i + 1,
    setting: String(s.setting || s.scene || s.scenePrompt || "a simple interior").slice(0, 500),
    framing: String(s.framing || s.shot || "medium shot").slice(0, 60),
    speakerId: slug(s.speakerId || s.speaker || s.characterId || "", ""),
    line: String(s.line || (s.dialogue && s.dialogue.line) || (typeof s.dialogue === "string" ? s.dialogue : "") || "").slice(0, 300),
    action: String(s.action || s.motion || "").slice(0, 200),
    durationSec: Math.max(2, Math.min(8, Number(s.durationSec || s.duration) || 3)),
  }))
  if (Number(maxShots) > 0 && shots.length > Number(maxShots)) shots = shots.slice(0, Number(maxShots))
  if (!shots.length) throw new Error("LLM không tách được cảnh nào")
  return { characters, shots }
}

// #region ALD 06/06/2026 - face-motion ("Người mẫu đọc kịch bản"): AI đọc KỊCH BẢN + PHONG THÁI → 1 câu
// mô tả HÀNH ĐỘNG/biểu cảm (tiếng Anh) cho MultiTalk, để người mẫu KHÔNG chỉ nhép miệng mà còn cử động
// tự nhiên bám nội dung. Lỗi → caller bỏ qua, worker run_face_motion tự fill từ preset phong thái.
const DEMEANOR_LABELS = {
  "hon-nhien": "Hồn nhiên (vui tươi, trong trẻo, năng lượng nhẹ nhàng)",
  "trong-sang": "Trong sáng (dịu dàng, chân thành, điềm tĩnh duyên dáng)",
  "manh-me": "Mạnh mẽ (tự tin, quả quyết, ánh mắt thẳng, dứt khoát)",
  "ca-tinh": "Cá tính (biểu cảm, phá cách, có thần thái, cử chỉ táo bạo)",
}
async function parseFaceMotionSegments(script, demeanor, userId, model) {
  const dem = DEMEANOR_LABELS[String(demeanor || "")] || DEMEANOR_LABELS["hon-nhien"]
  const sys = `Bạn là đạo diễn diễn xuất cho video người mẫu NÓI (lip-sync). Người mẫu đọc KỊCH BẢN, phải có HÀNH ĐỘNG ĐA DẠNG bám nội dung — MỖI đoạn MỘT động tác KHÁC nhau, KHÔNG lặp lại 1 cử chỉ suốt clip.
PHONG THÁI yêu cầu: ${dem}.
Chia kịch bản thành 2-6 đoạn theo câu / cụm ý tự nhiên (kịch bản ngắn thì 2-3 đoạn). Mỗi đoạn: GIỮ NGUYÊN lời tiếng Việt + 1 hành động/biểu cảm TIẾNG ANH RIÊNG cho đoạn đó, KHÁC các đoạn khác (đổi cử chỉ tay, tư thế/đổi trọng tâm, hướng nhìn, biểu cảm khuôn mặt cho hợp nội dung đoạn & phong thái).
CHỈ trả JSON thuần (KHÔNG markdown, KHÔNG giải thích), schema:
{ "segments": [ { "line": "<lời TIẾNG VIỆT nguyên văn của đoạn>", "action": "<câu TIẾNG ANH ngắn ≤30 từ — hành động RIÊNG cho đoạn này, kèm 'talking to camera, lip sync'>" } ] }`
  let { text } = await callChat(userId, "ollama", {
    model: (model || "").trim() || SCRIPT_MODEL,
    messages: [{ role: "system", content: sys }, { role: "user", content: String(script || "").slice(0, 4000) }],
    temperature: 0.6, maxTokens: 1024, numCtx: 8192, keepAlive: 0, format: "json", think: false,
  })
  text = String(text || "").replace(/<think>[\s\S]*?<\/think>/gi, "").replace(/```json|```/gi, "").trim()
  const m = text.match(/\{[\s\S]*\}/)
  if (!m) throw new Error("LLM không trả JSON")
  let segs = JSON.parse(m[0]).segments
  if (!Array.isArray(segs)) throw new Error("LLM không trả segments[]")
  segs = segs.map((s) => ({ line: String(s?.line || "").trim(), action: String(s?.action || "").trim().slice(0, 300) }))
             .filter((s) => s.line).slice(0, 8)   // cap 8 đoạn → giới hạn tổng thời gian render (FE poll 30')
  if (!segs.length) throw new Error("LLM không tách được đoạn")
  return segs
}
// #endregion

// ── input ──
// #region ALD 15/07/2026 - Chuẩn hóa tên file từ signed URL Task Cloud
// Signed URL có dạng .../inputs/0?expires=...&signature=.... Trước đây query string bị
// coi là phần mở rộng, tạo object key `ref.0?expires=...`; worker gọi /files/<key>
// thì HTTP tách dấu ? thành query và storage chỉ còn tìm `ref.0` → 404.
const MIME_EXTENSION = new Map([
  ["image/jpeg", "jpg"], ["image/png", "png"], ["image/webp", "webp"], ["image/avif", "avif"],
  ["video/mp4", "mp4"], ["video/quicktime", "mov"], ["video/webm", "webm"], ["video/x-matroska", "mkv"],
  ["audio/mpeg", "mp3"], ["audio/mp4", "m4a"], ["audio/wav", "wav"], ["application/pdf", "pdf"],
])

function cleanMime(value) {
  return String(value || "").split(";", 1)[0].trim().toLowerCase()
}

function filenameFromDisposition(value) {
  const header = String(value || "")
  const utf8 = header.match(/filename\*\s*=\s*UTF-8''([^;]+)/i)
  if (utf8?.[1]) {
    try { return decodeURIComponent(utf8[1].trim().replace(/^"|"$/g, "")) } catch { /* fallback below */ }
  }
  return header.match(/filename\s*=\s*"([^"]+)"/i)?.[1]
    || header.match(/filename\s*=\s*([^;]+)/i)?.[1]?.trim()
    || ""
}

function safeFileName(rawName, mimeType, fallback = "file") {
  let name = String(rawName || "").split(/[?#]/, 1)[0]
  try { name = decodeURIComponent(name) } catch { /* keep original */ }
  name = path.basename(name).replace(/[\u0000-\u001f\u007f]/g, "").replace(/[^\p{L}\p{N}._ -]+/gu, "-").trim()
  if (!name || name === "." || name === "..") name = fallback
  let ext = path.extname(name).slice(1).toLowerCase()
  if (!/^[a-z0-9]{1,10}$/.test(ext)) ext = ""
  if (!ext) {
    ext = MIME_EXTENSION.get(cleanMime(mimeType)) || "bin"
    name = `${name.replace(/\.+$/, "")}.${ext}`
  }
  return name.slice(-180)
}

function safeFileNameFromResponse(url, response, contentType, fallback) {
  let pathname = ""
  try { pathname = new URL(url).pathname } catch { pathname = String(url || "") }
  const dispositionName = filenameFromDisposition(response?.headers?.get?.("content-disposition"))
  return safeFileName(dispositionName || pathname, contentType, fallback)
}

function safeStorageExtension(file) {
  const name = safeFileName(file?.name, file?.mimeType, "input")
  const ext = path.extname(name).slice(1).toLowerCase()
  return /^[a-z0-9]{1,10}$/.test(ext) ? ext : (MIME_EXTENSION.get(cleanMime(file?.mimeType)) || "bin")
}
// #endregion

const handleInput = async (data, _prev, ctx) => {
  const c = data.config || {}
  const inferred = { inputText: "text", inputImage: "image", inputFile: "file", inputHistory: "history" }[data.type] || "text"
  const contentType = String(c.contentType || inferred)
  const source = String(c.source || "session")
  const subst = (s) => { let o = s; for (const [k, v] of Object.entries(ctx.workflowInput || {})) o = o.replace(new RegExp(`\\{\\{\\s*session\\.${k}\\s*\\}\\}`, "g"), v == null ? "" : String(v)); return o }

  if (source === "session") {
    const field = String(c.field || contentType)
    const value = (ctx.workflowInput || {})[field]
    if (contentType === "text") { const text = value == null ? "" : typeof value === "string" ? value : JSON.stringify(value); ctx.emit("info", `Session.${field} → text (${text.length}c)`); return { text } }
    if (contentType === "image" || contentType === "file") {
      const f = value
      if (!f) throw new Error(`Session không có ${contentType} ở field "${field}"`)
      if (typeof f === "string" && /^https?:\/\//i.test(f)) {
        ctx.emit("info", `Session.${field} → tải URL ${f.slice(0, 80)}`)
        const res = await fetch(f)
        if (!res.ok) throw new Error(`Tải session.${field} ${res.status}`)
        const buf = Buffer.from(await res.arrayBuffer())
        const mimeType = res.headers.get("content-type") || "application/octet-stream"
        const name = safeFileNameFromResponse(f, res, mimeType, contentType)
        return { text: name, file: { name, mimeType, data: buf.toString("base64") } }
      }
      if (f && typeof f === "object" && typeof f.url === "string" && /^https?:\/\//i.test(f.url)) {
        ctx.emit("info", `Session.${field} → tải URL ${f.url.slice(0, 80)}`)
        const res = await fetch(f.url)
        if (!res.ok) throw new Error(`Tải session.${field}.url ${res.status}`)
        const buf = Buffer.from(await res.arrayBuffer())
        const mimeType = f.mimeType || f.type || res.headers.get("content-type") || "application/octet-stream"
        const name = safeFileName(f.name || f.filename || safeFileNameFromResponse(f.url, res, mimeType, contentType), mimeType, contentType)
        return { text: name, file: { name, mimeType, data: buf.toString("base64") } }
      }
      if (!f.data) throw new Error(`Session.${field} cần upload object hoặc URL hợp lệ cho ${contentType}`)
      ctx.emit("info", `Session.${field} → ${contentType} ${f.name}`)
      return { text: f.name || "", file: { name: f.name || contentType, mimeType: f.mimeType || "application/octet-stream", data: f.data } }
    }
    if (contentType === "history") { const m = Array.isArray(value) ? value : []; ctx.emit("info", `Session.${field} → history (${m.length})`); return { text: JSON.stringify(m), metadata: { _isHistory: true, messageCount: m.length } } }
  }
  if (source === "static") {
    if (contentType === "text") { const t = subst(String(c.staticText || "")); ctx.emit("info", `Static text (${t.length}c)`); return { text: t } }
    // image/file: FE upload lên storage → staticPath/staticBucket (base64 đã clear). Ưu tiên:
    //   staticData (base64) → staticPath (tải từ MinIO) → staticUrl (fetch).
    let dataB64 = c.staticData ? String(c.staticData) : ""
    let mime = c.staticMime || ""
    if (!dataB64 && c.staticPath) {
      const key = `${c.staticBucket || "chat-attachments"}/${c.staticPath}`
      const { stream, contentType: ct } = await getObjectStream(key)
      const chunks = []; for await (const ch of stream) chunks.push(ch)
      dataB64 = Buffer.concat(chunks).toString("base64"); mime = mime || ct || ""
    } else if (!dataB64 && c.staticUrl) {
      const r = await fetch(c.staticUrl)
      if (!r.ok) throw new Error(`Tải staticUrl ${r.status}`)
      dataB64 = Buffer.from(await r.arrayBuffer()).toString("base64"); mime = mime || r.headers.get("content-type") || ""
    }
    if (!dataB64) throw new Error("Static thiếu dữ liệu (staticData/staticPath/staticUrl)")
    ctx.emit("info", `Static ${contentType} ${c.staticName || ""}`)
    return { text: String(c.staticName || ""), file: { name: String(c.staticName || contentType), mimeType: mime || "application/octet-stream", data: dataB64 } }
  }
  if (source === "url") {
    const url = subst(String(c.url || ""))
    if (!url) throw new Error("Input source=url thiếu url")
    ctx.emit("info", `Tải URL ${url.slice(0, 80)}`)
    const res = await fetch(url)
    if (!res.ok) throw new Error(`Tải URL ${res.status}`)
    if (contentType === "text") return { text: await res.text() }
    const buf = Buffer.from(await res.arrayBuffer())
    const mimeType = res.headers.get("content-type") || "application/octet-stream"
    const name = safeFileNameFromResponse(url, res, mimeType, contentType || "file")
    return { text: name, file: { name, mimeType, data: buf.toString("base64") } }
  }
  if (source === "library") {
    // libraryId → audio_files/storage_files; tải bytes về base64
    const libId = String(c.libraryId || "")
    if (!libId) throw new Error("Library source thiếu libraryId")
    let row = (await query("SELECT name, mime, storage_key FROM audio_files WHERE id=$1", [libId])).rows[0]
    if (!row) row = (await query("SELECT name, mime, storage_key FROM storage_files WHERE id=$1", [libId])).rows[0]
    if (!row) throw new Error(`Library item ${libId} không tồn tại`)
    const { getObjectStream } = await import("../storage.js")
    const { stream } = await getObjectStream(row.storage_key)
    const chunks = []; for await (const ch of stream) chunks.push(ch)
    const buf = Buffer.concat(chunks)
    ctx.emit("info", `Library ${row.name} (${buf.length}b)`)
    return { text: row.name || "", file: { name: row.name || "file", mimeType: row.mime || "application/octet-stream", data: buf.toString("base64") } }
  }
  throw new Error(`Input source "${source}" chưa hỗ trợ`)
}

// #region ALD 30/06/2026 - cast-model (Trụ D): TUYỂN người mẫu từ KHO model_refs theo gender+age_group khi user
// CHỈ có ảnh sản phẩm (không có ảnh người mẫu). Bốc 1 mẫu DETERMINISTIC (theo seed, KHÔNG random) → trả ảnh làm
// output → mọi cảnh dùng CÙNG 1 người mẫu (continuity). Xử lý tại API như node input, KHÔNG qua worker/JOB_TYPES.
const handleCastModel = async (data, _prev, ctx) => {
  const c = data.config || {}
  const gender = ["male", "female"].includes(String(c.gender || "").toLowerCase()) ? String(c.gender).toLowerCase() : "female"
  const ageGroup = ["young", "middle", "old"].includes(String(c.ageGroup || c.age_group || "").toLowerCase()) ? String(c.ageGroup || c.age_group).toLowerCase() : "young"
  // Lọc dần: đúng gender+age → nới gender → bất kỳ active. Sắp xếp ổn định để seed pick reproducible.
  let pool = (await query("SELECT id, storage_key FROM model_refs WHERE is_active = true AND gender = $1 AND age_group = $2 ORDER BY created_at, id", [gender, ageGroup])).rows
  if (!pool.length) pool = (await query("SELECT id, storage_key FROM model_refs WHERE is_active = true AND gender = $1 ORDER BY created_at, id", [gender])).rows
  if (!pool.length) pool = (await query("SELECT id, storage_key FROM model_refs WHERE is_active = true ORDER BY created_at, id")).rows
  if (!pool.length) throw new Error("Kho người mẫu (model_refs) trống — hãy upload ảnh mẫu ở Settings, hoặc đưa ảnh người mẫu vào input")
  const seed = Math.max(0, Number(c.seed) || 0)
  const pick = pool[seed % pool.length]
  const { stream, contentType } = await getObjectStream(pick.storage_key)
  const chunks = []; for await (const ch of stream) chunks.push(ch)
  const buf = Buffer.concat(chunks)
  ctx.emit("info", `cast-model: tuyển ${gender}/${ageGroup} từ kho (${pool.length} mẫu) → ${String(pick.id).slice(0, 8)} (${buf.length}b)`)
  return { text: `model-${pick.id}`, file: { name: `cast-model-${pick.id}.jpg`, mimeType: contentType || "image/jpeg", data: buf.toString("base64") } }
}
// #endregion

const handleOutput = async (data, prev, ctx) => {
  const format = String(data.config?.format || "markdown")
  let result
  if (!prev) {
    ctx.emit("warn", "Output không có data")
    result = { text: "", metadata: { format } }
  } else {
    ctx.emit("info", `Output format=${format}`)
    if (format === "json") {
      try { result = { text: prev.text, metadata: { ...(prev.metadata || {}), format, parsed: JSON.parse(prev.text) } } }
      catch { /* not json */ }
    }
    if (!result) result = { ...prev, metadata: { ...(prev.metadata || {}), format } }
  }
  // ALD 15/06/2026 - Có kết quả → xả RAM/VRAM (opt-in config.cleanup): Chandra stop + Ollama unload + ComfyUI /free.
  // Dọn 1 lần ở CUỐI workflow, tránh VRAM/RAM dồn cho lần chạy sau. Chỉ chạy khi có data thật (prev).
  if (data.config?.cleanup && prev) {
    ctx.emit("info", "Output xong → xả RAM/VRAM")
    try { await freeGpuRam(ctx, { comfy: true }) } catch (e) { ctx.emit("warn", `Xả lỗi: ${e.message}`) }
  }
  // #region ALD 05/07/2026 - Toggle "Đăng tự động lên MXH" (config.social) → XẾP HÀNG vào social_posts (KHÔNG
  // gọi Graph/TikTok API trực tiếp tại đây) — social-scheduler.js tickSocialPosts() (chạy nền trong wf-worker,
  // ~30s/lần) mới thật sự publish. Lý do đổi từ gọi thẳng (bản đầu) sang xếp hàng:
  //   1. social_posts là NGUỒN SỰ THẬT DUY NHẤT cho "output đã đăng chưa" — Recommend (Social Management) dựa
  //      vào bảng này để ẩn output đã có bài. Gọi thẳng ở đây (không ghi social_posts) sẽ khiến Recommend vẫn
  //      đề xuất lại output đã tự đăng qua toggle này → user tạo bài đăng thủ công thêm lần nữa = ĐĂNG TRÙNG.
  //   2. Dùng chung 1 code path (tickSocialPosts) cho MỌI nguồn đăng (toggle Output node, Recommend thủ công,
  //      Content Plan tự động) → lịch sử/trạng thái/retry nhất quán ở 1 chỗ (tab Lịch sử).
  // Không throw ra ngoài — xếp hàng lỗi (vd DB down) chỉ warn, không fail cả workflow.
  if (data.config?.social?.enabled && prev) {
    try {
      const rows = await scheduleSocialPosts({
        userId: ctx.userId, workflowRunId: ctx.workflowRunId,
        caption: data.config.social.caption, facebook: data.config.social.facebook, tiktok: data.config.social.tiktok,
      })
      if (rows.length) ctx.emit("info", `Đã xếp hàng đăng MXH (${rows.length}) — worker sẽ đăng trong giây lát`)
    } catch (e) { ctx.emit("warn", `Xếp hàng đăng MXH lỗi: ${e?.message || e}`) }
  }
  // #endregion
  return result
}

const handleOcr = async (data, prev, ctx) => {
  if (!prev?.file) throw new Error("Node 'ocr' cần file từ node trước")
  const cacheEnabled = data.config?.cache_enabled !== false
  const bytes = Buffer.from(prev.file.data, "base64")
  let fileHash = ""
  if (cacheEnabled) {
    fileHash = crypto.createHash("sha256").update(bytes).digest("hex")
    ctx.emit("info", `OCR ${prev.file.name} · hash ${fileHash.slice(0, 12)}…`)
    const cached = (await query("SELECT text, metadata, hit_count FROM ocr_cache WHERE file_hash=$1", [fileHash])).rows[0]
    if (cached?.text) {
      query("UPDATE ocr_cache SET accessed_at=now(), hit_count=$1 WHERE file_hash=$2", [(cached.hit_count || 1) + 1, fileHash]).catch(() => {})
      ctx.emit("success", `OCR CACHE HIT · ${cached.text.length}c`)
      return { text: cached.text, metadata: { ...(cached.metadata || {}), _cache: { hit: true } } }
    }
    ctx.emit("info", "OCR cache miss → Marker")
  }
  const form = new FormData()
  form.append("file", new Blob([bytes], { type: prev.file.mimeType }), prev.file.name)
  const submit = await fetch(`${MARKER_URL}/ocr/async`, { method: "POST", body: form })
  if (!submit.ok) throw new Error(`Marker /ocr/async ${submit.status}`)
  const { job_id } = await submit.json()
  ctx.emit("info", `Marker job ${job_id} — chờ`)
  for (let i = 0; i < 1200; i++) {
    await sleep(i < 38 ? 800 : 2500)
    const poll = await fetch(`${MARKER_URL}/ocr/${job_id}`)
    if (!poll.ok) continue
    const r = await poll.json()
    if (r.status === "complete") {
      const text = r.text ?? "", metadata = r.metadata ?? {}
      ctx.emit("success", `OCR xong: ${text.length}c`)
      if (cacheEnabled && fileHash && text) query(
        `INSERT INTO ocr_cache(file_hash,text,metadata,file_name,file_size_bytes,mime_type) VALUES($1,$2,$3,$4,$5,$6)
         ON CONFLICT(file_hash) DO UPDATE SET text=$2, metadata=$3, accessed_at=now()`,
        [fileHash, text, JSON.stringify(metadata), prev.file.name, bytes.length, prev.file.mimeType]).catch(() => {})
      return { text, metadata }
    }
    if (r.status === "error") throw new Error(`OCR lỗi: ${r.error}`)
  }
  throw new Error("OCR timeout")
}

const handleChat = async (data, prev, ctx) => {
  const c = data.config || {}
  const provider = String(c.provider || "ollama")
  const model = String(c.model || "")
  if (!model) throw new Error("Node 'chat' thiếu config.model")
  const userText = prev?.text || ""
  ctx.emit("info", `Chat ${provider}/${model} · in=${userText.length}c`)
  const messages = [...(c.systemPrompt ? [{ role: "system", content: String(c.systemPrompt) }] : []), { role: "user", content: userText }]
  const r = await callChat(ctx.userId, provider, {
    model, messages, temperature: Number(c.temperature ?? 0.3),
    maxTokens: Number(c.maxTokens) > 0 ? Number(c.maxTokens) : 131072,
    numCtx: Number(c.numCtx) > 0 ? Number(c.numCtx) : 131072,
    customBaseUrl: c.customBaseUrl || null, customApiKey: c.customApiKey || null,
    keepAlive: c.keepAlive ?? "10m",
  })
  ctx.emit("success", `Chat trả ${r.text.length}c`, { usage: r.usage })
  return { text: r.text, metadata: { ...(prev?.metadata || {}), provider: r.provider, model: r.model, usage: r.usage } }
}

const handleImage = async (data, prev, ctx) => {
  if (!prev?.file) throw new Error("Node 'image' cần ảnh từ node trước")
  const model = String(data.config?.model || "")
  if (!model) throw new Error("Node 'image' thiếu config.model")
  const prompt = String(data.config?.prompt || "Mô tả chi tiết ảnh này bằng tiếng Việt.")
  ctx.emit("info", `Vision Ollama/${model}: ${prev.file.name}`)
  const res = await fetch(`${OLLAMA_URL}/api/chat`, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model, messages: [{ role: "user", content: prompt, images: [prev.file.data] }], stream: false, keep_alive: 0 }) })
  if (!res.ok) throw new Error(`Ollama vision ${res.status}: ${(await res.text()).slice(0, 200)}`)
  const j = await res.json()
  const text = j.message?.content ?? ""
  ctx.emit("success", `Vision trả ${text.length}c`)
  return { text, metadata: { model: j.model } }
}

const handleCondition = async (data, prev, ctx) => {
  const expr = String(data.config?.expression || "").trim()
  if (!expr) throw new Error("Node 'condition' thiếu config.expression")
  const text = prev?.text ?? "", metadata = prev?.metadata ?? {}
  let result
  try { result = Boolean(new Function("text", "metadata", `return (${expr})`)(text, metadata)) }
  catch (e) { throw new Error(`Expression lỗi: ${e.message}`) }
  const branch = result ? "true" : "false"
  ctx.emit("info", `Condition "${expr.slice(0, 60)}" → ${branch}`)
  return { text, metadata: { ...metadata, _branch: branch, _conditionResult: result } }
}

const handleHttp = async (data, prev, ctx) => {
  const c = data.config || {}
  const method = String(c.method || "GET").toUpperCase()
  let urlTpl = String(c.url || "").trim()
  if (!urlTpl) throw new Error("Node 'http' thiếu config.url")
  const subst = (s) => String(s).replace(/\{\{\s*text\s*\}\}/g, prev?.text ?? "").replace(/\{\{\s*metadata\.([\w.]+)\s*\}\}/g, (_, k) => {
    let v = prev?.metadata; for (const p of String(k).split(".")) v = v && typeof v === "object" ? v[p] : undefined; return v == null ? "" : String(v) })
  const url = subst(urlTpl)
  ctx.emit("info", `HTTP ${method} ${url.slice(0, 80)}`)
  let body
  if (c.body != null && method !== "GET" && method !== "HEAD") body = typeof c.body === "string" ? subst(c.body) : JSON.stringify(c.body)
  const res = await fetch(url, { method, headers: { "Content-Type": "application/json", ...(c.headers || {}) }, body,
    signal: AbortSignal.timeout(Number(c.timeout || 30000)) })
  const ct = res.headers.get("content-type") || ""
  let respBody, respJson
  if (ct.includes("application/json")) { respJson = await res.json().catch(() => null); respBody = JSON.stringify(respJson, null, 2) }
  else respBody = await res.text()
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${respBody.slice(0, 300)}`)
  ctx.emit("success", `HTTP ${res.status} (${respBody.length}c)`)
  return { text: respBody, metadata: { status: res.status, contentType: ct, ...(respJson != null ? { json: respJson } : {}) } }
}

const handleDebug = async (_data, prev, ctx) => {
  const meta = prev?.metadata || {}
  ctx.emit("info", `Debug: text=${(prev?.text || "").length}c`, { outputMeta: meta })
  return prev || { text: "" }
}

const handleTransform = async (data, prev, ctx) => {
  // Transform text cơ bản: trim/uppercase/lowercase/json-extract/regex-replace/template.
  const c = data.config || {}, op = String(c.op || c.operation || "passthrough")
  let text = prev?.text ?? ""
  if (op === "trim") text = text.trim()
  else if (op === "uppercase") text = text.toUpperCase()
  else if (op === "lowercase") text = text.toLowerCase()
  else if (op === "template" && c.template) text = String(c.template).replace(/\{\{\s*text\s*\}\}/g, text)
  else if (op === "regex-replace" && c.pattern != null) text = text.replace(new RegExp(c.pattern, c.flags || "g"), c.replacement ?? "")
  else if (op === "json-extract" && c.path) { try { let v = JSON.parse(text); for (const p of String(c.path).split(".")) v = v?.[p]; text = typeof v === "string" ? v : JSON.stringify(v) } catch { /* keep */ } }
  ctx.emit("info", `Transform ${op} → ${text.length}c`)
  return { text, metadata: prev?.metadata || {} }
}

const handleValidate = async (data, prev, ctx) => {
  // Kiểm tra prev.text theo rule; fail → throw (onError quyết định). Rules: notEmpty, minLength, isJson, contains.
  const c = data.config || {}, text = prev?.text ?? ""
  const fails = []
  if (c.notEmpty && !text.trim()) fails.push("text rỗng")
  if (c.minLength && text.length < Number(c.minLength)) fails.push(`< ${c.minLength} ký tự`)
  if (c.isJson) { try { JSON.parse(text) } catch { fails.push("không phải JSON hợp lệ") } }
  if (c.contains && !text.includes(String(c.contains))) fails.push(`thiếu "${c.contains}"`)
  if (fails.length) throw new Error(`Validate fail: ${fails.join("; ")}`)
  ctx.emit("success", "Validate OK")
  return prev || { text: "" }
}

const handleGpuWarmup = async (_data, prev, ctx) => {
  try { await fetch(`${MARKER_URL}/chandra/warmup`, { method: "POST" }); ctx.emit("info", "GPU warmup (Chandra) kicked") }
  catch (e) { ctx.emit("warn", `Warmup fail: ${e.message}`) }
  return prev || { text: "" }
}
async function unloadOllamaModels(ctx, models = []) {
  const base = OLLAMA_URL
  let loaded = []
  try {
    const res = await fetch(`${base}/api/ps`)
    if (res.ok) {
      const body = await res.json()
      loaded = Array.isArray(body.models) ? body.models.map((m) => m.name || m.model).filter(Boolean) : []
    }
  } catch (e) {
    ctx.emit("warn", `Không đọc được Ollama ps: ${e.message}`)
  }
  const targets = models.length ? models : loaded
  if (!targets.length) {
    ctx.emit("info", "Ollama không có model nào đang giữ VRAM")
    return
  }
  for (const model of targets) {
    try {
      const res = await fetch(`${base}/api/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model, prompt: "", stream: false, keep_alive: 0 }),
      })
      if (!res.ok) throw new Error(`${res.status}: ${(await res.text()).slice(0, 160)}`)
      ctx.emit("info", `Ollama unload: ${model}`)
    } catch (e) {
      ctx.emit("warn", `Ollama unload ${model} lỗi: ${e.message}`)
    }
  }
}
// ALD 15/06/2026 - Dọn GPU/RAM dùng chung (cho node gpu-free VÀ Output "xả khi xong"). wf-worker host-net →
// ComfyUI 127.0.0.1:8188 reachable. Chandra stop + Ollama unload + ComfyUI /free (unload models + free RAM/VRAM).
async function freeGpuRam(ctx, { chandra = true, ollama = true, comfy = true, ollamaModels = [] } = {}) {
  if (chandra) {
    try { await fetch(`${MARKER_URL}/chandra/stop`, { method: "POST" }); ctx.emit("info", "Xả: Chandra unload") }
    catch (e) { ctx.emit("warn", `Chandra unload fail: ${e.message}`) }
  }
  if (ollama) {
    try { await unloadOllamaModels(ctx, ollamaModels) } catch (e) { ctx.emit("warn", `Ollama unload fail: ${e.message}`) }
  }
  if (comfy) {
    const comfyUrl = (process.env.COMFY_URL || "http://127.0.0.1:8188").replace(/\/$/, "")
    try {
      const res = await fetch(`${comfyUrl}/free`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ unload_models: true, free_memory: true }),
      })
      if (!res.ok) throw new Error(`${res.status}: ${(await res.text()).slice(0, 160)}`)
      ctx.emit("info", "Xả: ComfyUI unload_models + free_memory (VRAM+RAM)")
    } catch (e) {
      ctx.emit("warn", `ComfyUI free fail: ${e.message}`)
    }
  }
}

const handleGpuFree = async (data, prev, ctx) => {
  const c = data.config || {}
  await freeGpuRam(ctx, {
    chandra: c.free_chandra !== false,
    ollama: c.free_ollama !== false,
    comfy: c.free_comfy === true,
    ollamaModels: String(c.ollama_models || "").split(",").map((s) => s.trim()).filter(Boolean),
  })
  return prev || { text: "" }
}

const handleNestedWorkflow = async (data, prev, ctx) => {
  const slug = String(data.config?.slug || "").trim()
  if (!slug) throw new Error("Node 'workflow' thiếu config.slug")
  const stack = ctx.workflowStack || []
  if (stack.includes(slug)) throw new Error(`Cycle: "${slug}" đã trong stack [${stack.join(" → ")}]`)
  if (stack.length >= 5) throw new Error(`Quá sâu (>5 nesting)`)
  // ALD 23/07/2026 - Workflow active dùng chung như template cho mọi user; ưu tiên bản do chính user sở hữu.
  const wf = (await query(`SELECT id, definition, is_active FROM workflows
    WHERE slug=$1 AND is_active=true
    ORDER BY (user_id=$2) DESC, is_public DESC, updated_at DESC
    LIMIT 1`, [slug, ctx.userId])).rows[0]
  if (!wf) throw new Error(`Workflow "/${slug}" không tồn tại/không quyền`)
  if (!wf.is_active) throw new Error(`Workflow "/${slug}" disabled`)
  ctx.emit("info", `Gọi nested /${slug}`)
  // Tạo run con rồi chạy engine (lazy import tránh circular)
  const { runWorkflow } = await import("./engine.js")
  const child = (await query(
    "INSERT INTO workflow_runs(workflow_id,user_id,auth_method,status,input) VALUES($1,$2,'session','queued',$3) RETURNING id",
    [wf.id, ctx.userId, JSON.stringify({ ...ctx.workflowInput, text: prev?.text ?? "" })])).rows[0]
  const result = await runWorkflow({ runId: child.id, workflowId: wf.id, userId: ctx.userId, authMethod: "session",
    definition: wf.definition, input: { ...ctx.workflowInput, text: prev?.text ?? "" }, _stack: [...stack, slug] })
  for (const ev of result.events) ctx.emit(ev.level, `[/${slug}] ${ev.msg}`, ev.extra)
  if (result.status === "error") throw new Error(`Workflow /${slug} lỗi: ${result.errorMsg}`)
  return result.output ?? { text: "" }
}

// Media node → tạo job motion-backend (bảng jobs) cho worker Python (ComfyUI) chạy, rồi poll.
// Upload file input (base64 từ upstream NodeOutput) lên MinIO; trả output URL (image/video).
function mediaViaJob(type) {
  return async (data, prev, ctx) => {
    // ALD 20/07/2026 - HARD-BLOCK Wan-Dancer: node "Vũ đạo theo nhạc" (Wan-Dancer-14B ~2×34.5GB bf16) CHỈ chạy
    // trên VPS GPU ≥ 90GB. Chặn Ở ĐÂY (không chỉ ẩn UI) để workflow đã lưu / gọi API thẳng cũng không lách được.
    // "Không đo được VRAM" ⇒ 0 ⇒ chặn (block mặc định). Kiểm TRƯỚC khi upload input/INSERT để khỏi phí việc.
    if (type === "wan-dancer") {
      const vramGb = await detectGpuVramGb().catch(() => 0)
      if (vramGb < WAN_DANCER_MIN_VRAM_GB) {
        throw new Error(`Node Wan-Dancer cần GPU ≥ ${WAN_DANCER_MIN_VRAM_GB}GB VRAM — box hiện có ${vramGb || "?"}GB. Node bị khóa.`)
      }
      ctx.emit("info", `Wan-Dancer: GPU box ${vramGb}GB ≥ ${WAN_DANCER_MIN_VRAM_GB}GB — mở khóa`)
    }
    const jobId = crypto.randomUUID()
    const inputs = {}
    const sources = { ...(ctx.inputsByHandle || {}) }
    // ALD 14/07/2026 - Node multi-input chỉ được đọc output đúng theo edge/handle. Không fallback sang
    // `lastOutput` toàn workflow: với graph nhiều root, fallback cũ làm node root không có ảnh vẫn dùng ké ảnh
    // của root trước, che giấu dây nối sai rồi thất bại ở node kế tiếp.
    if (!ctx.usesHandleInputs && !Object.keys(sources).length && prev?.file) sources.input = prev
    for (const [handle, out] of Object.entries(sources)) {
      const f = out?.file
      if (!f?.data) continue
      const ext = safeStorageExtension(f)
      const mappedHandle = (type === "motion" && handle === "image") ? "ref" : handle
      const key = `${jobId}/${mappedHandle}.${ext}`
      await putObject(key, Buffer.from(f.data, "base64"), f.mimeType || "application/octet-stream")
      inputs[mappedHandle] = key
    }
    let params = normalizeMotionDriverSegment(type, { ...(ctx.workflowInput || {}), ...(data.config || {}) })
    delete params.text; delete params.image; delete params.file
    params = await maybeTranslateWanPrompt(type, params, ctx)

    // Polyfill params từ preset cho node motion (match với FE motions-transfer)
    // ALD 09/07/2026 - FIX "tay ảo" gián tiếp: list cũ chỉ có 5 preset + fallback ÉP về 15s-720p → mọi preset mới
    // (20s/30s/30fps/sharp/max) chạy qua WORKFLOW bị ÂM THẦM nén về 15s·16fps·steps4 (user chọn 30fps mà render 16fps).
    // Nay: sync đủ list (kèm render_fps) + preset LẠ thì KHÔNG ép — để worker tự tra bảng preset đầy đủ của nó.
    if (type === "motion" && params.preset) {
      const MOTION_PRESETS = [
        { id: '2s-720p',   resolution: '544x960', frames: 33,  steps: 4 },
        { id: '4s-720p',   resolution: '544x960', frames: 65,  steps: 4 },
        { id: '5s-720p',   resolution: '544x960', frames: 81,  steps: 4 },
        { id: '10s-720p',  resolution: '544x960', frames: 161, steps: 4 },
        { id: '15s-720p',  resolution: '544x960', frames: 241, steps: 4 },
        { id: '20s-720p',  resolution: '544x960', frames: 321, steps: 4 },
        { id: '30s-720p',  resolution: '544x960', frames: 481, steps: 4 },
        { id: '10s-720p-sharp', resolution: '544x960', frames: 161, steps: 4 },
        { id: '10s-30fps', resolution: '544x960', frames: 301, steps: 4, renderFps: 30 },
        { id: '15s-30fps', resolution: '544x960', frames: 451, steps: 4, renderFps: 30 },
        { id: '20s-30fps', resolution: '544x960', frames: 601, steps: 4, renderFps: 30 },
        // Alias legacy: Natural/MAX đã gỡ, giữ ID nhưng chạy Fast 4 bước.
        { id: '8s-720p-max', resolution: '720x1280', frames: 241, steps: 4, renderFps: 30 }
      ]
      const p = MOTION_PRESETS.find((x) => x.id === params.preset)
      if (p) {
        const [w, h] = String(p.resolution || '544x960').split('x').map((n) => parseInt(n, 10) || 0)
        params.width = w || 544
        params.height = h || 960
        params.frames = p.frames
        params.steps = p.steps
        if (p.renderFps) params.render_fps = p.renderFps
      }
    }

    // ALD 13/07/2026 - Backend chỉ còn Fast LightX2V 4 bước. Ghi đè sau preset/config để workflow
    // legacy đã lưu natural/HQ/20 steps không thể kích hoạt lại đường non-distill đã gỡ.
    if (type === "motion") {
      params.renderProfile = "fast"
      params.render_profile = "fast"
      params.hq = false
      delete params.hq_steps
      params.steps = 4
      params.cfg = 1
      params.scheduler = "dpm++_sde"
      params.loraLightx2v = 1
      params.lora_lightx2v = 1
      // 1 ref + 80 frame mới/window: chia đều các clip 5s*n @16fps, không sinh tail window ngắn.
      params.frame_window_size = 81
    }

    // #region ALD 11/06/2026 - Inject key từ node api-key: NỐI CỔNG (ctx.wiredKey) ưu tiên nhất → tự phân bổ
    // theo providerType (ctx.providerKeys) → field node-level (đã nằm sẵn trong params, không ghi đè).
    // HF dùng field hfToken riêng (tránh apiKey=AIza… cũ lạc sang HF). Gemini TTS: CHỈ inject khi voice gemini:*
    // — không inject vô điều kiện vì _tts_any tự nâng mọi voice lên Gemini khi thấy key (lách admin gate).
    {
      const wk = ctx.wiredKey || null
      const pk = ctx.providerKeys || {}
      const prov = String(params.provider || "").toLowerCase().trim()
      const needKey = prov && !["qwen", "self-host", "selfhost", "local"].includes(prov)
      if (wk && !needKey) ctx.emit("warn", "Node API Key được nối vào node này nhưng provider là Self-host (không cần key) — bỏ qua")
      else if (wk && needKey && wk.providerType !== prov) ctx.emit("warn", `Node API Key nối vào có Type "${wk.providerType}" ≠ provider "${prov}" của node — bỏ qua, dùng key tự phân bổ nếu có`)
      if (needKey) {
        const key = (wk && wk.providerType === prov) ? wk.apiKey : pk[prov]
        const field = prov === "huggingface" ? "hfToken" : "apiKey"
        if (key && !String(params[field] || "").trim()) {
          params[field] = key
          ctx.emit("info", `Dùng key từ node API Key (${prov}${wk && wk.providerType === prov ? " · nối cổng" : " · tự phân bổ"})`)
        }
      }
      const gk = (wk && wk.providerType === "gemini") ? wk.apiKey : pk.gemini
      if (String(params.voice || "").toLowerCase().startsWith("gemini:") && gk && !String(params.geminiApiKey || "").trim() && !String(params.apiKey || "").trim()) {
        params.geminiApiKey = gk
        ctx.emit("info", "Dùng Gemini key từ node API Key cho TTS")
      }
    }
    // #endregion
    // ALD 03/06/2026 - story-film: AI ĐẠO DIỄN đọc KỊCH BẢN TỔNG → tự tách cảnh + nhận diện nhân vật
    // (chính/phụ/quần chúng) + lời thoại + khung hình. KHÔNG dựng tay từng node — worker run_story_film dựng.
    if (type === "story-film") {
      const raw = String(data.config?.script || data.config?.scriptText || params.script || prev?.text || "").trim()
      if (!raw) throw new Error("story-film: cần nhập KỊCH BẢN (config.script)")
      delete params.script; delete params.scriptText
      try {
        ctx.emit("info", "AI đạo diễn đang phân tích kịch bản (nhân vật + cảnh + thoại)…")
        const { characters, shots } = await parseStoryScript(raw, ctx.userId, params.scriptModel, Number(params.maxShots) || 0)
        params.characters = characters; params.shots = shots
        ctx.emit("info", `AI đạo diễn: ${characters.length} nhân vật (${characters.filter(c => c.role !== "extra").length} chính/phụ) · ${shots.length} cảnh`)
      } catch (e) {
        throw new Error(`AI đạo diễn lỗi (không phân tích được kịch bản): ${e?.message || e}`)
      }
    }
    // ALD 06/06/2026 - face-motion: AI CHIA ĐOẠN kịch bản → mỗi đoạn 1 hành động RIÊNG (params.segments) → worker
    // render từng đoạn rồi ghép → động tác đa dạng, hết lặp. Kịch bản gốc giữ ở params.line làm fallback (AI lỗi →
    // worker đọc full 1 clip). InfiniteTalk 1-clip-1-prompt vốn lặp động tác nên phải chia đoạn để đa dạng.
    if (type === "face-motion") {
      const raw = String(data.config?.script || params.script || prev?.text || "").trim()
      if (!raw) throw new Error("face-motion: cần nhập KỊCH BẢN (config.script)")
      delete params.script
      params.line = raw
      try {
        ctx.emit("info", "AI đang chia đoạn + dựng hành động ĐA DẠNG theo kịch bản…")
        const segs = await parseFaceMotionSegments(raw, params.demeanor, ctx.userId, params.scriptModel)
        params.segments = segs
        ctx.emit("info", `Chia ${segs.length} đoạn — mỗi đoạn 1 hành động riêng (phong thái ${params.demeanor || "hon-nhien"})`)
      } catch (e) {
        ctx.emit("warn", `AI chia đoạn lỗi → 1 clip theo phong thái: ${e?.message || e}`)
        delete params.prompt   // worker run_face_motion fallback: 1 clip, fill prompt từ DEMEANOR_PROMPTS
      }
    }
    if (new Set(["motion", "tryon", "create-image", "edit-image", "product-overlay", "text-to-video", "wan-i2v", "ss", "teen-flycam", "trend-tiktok", "enhance"]).has(type)) {
      await freeGpuRam(ctx, { chandra: false, ollama: true, comfy: false })
    }
    // ALD 17/07/2026 - gắn run id vào params (convention _prefix như _gen) để Task Cloud auto-worker truy ngược
    // mọi job của run mà dọn object MinIO sau khi đã giao kết quả về 103.142.24.69 (không lưu bền trên .165).
    if (ctx.workflowRunId) params._wfRunId = String(ctx.workflowRunId)
    // ALD 19/07/2026 - Chốt resolution trước INSERT; không phụ thuộc phiên bản Python GPU worker đang giữ RAM.
    params = enforceMotionResolution(type, params)
    // ALD 24/07/2026 - Chốt lần cuối tại nơi tạo job: workflow Task Cloud cũ có lưu
    // engine=flashvsr cũng không thể kích hoạt FlashVSR sau khi backend đã được cập nhật.
    const taskCloudEngineBefore = params?.engine
    params = enforceTaskCloudEnhancePolicy(type, params)
    if (type === "enhance" && taskCloudEngineBefore !== params?.engine && params?.engine === "lanczos") {
      ctx.emit("info", `Task Cloud: khóa Lanczos ${params?.targetRes || "1080p"} · FPS gốc`)
    }
    await query("INSERT INTO jobs (id, type, status, inputs, params, client_id) VALUES ($1,$2,'queued',$3,$4,$5)",
      [jobId, type, JSON.stringify(inputs), JSON.stringify(params), ctx.userId])
    const timeoutSec = mediaJobTimeoutSec(type, params)
    const maxPolls = Math.max(30, Math.ceil(timeoutSec / 2))
    // ALD 11/06/2026 - message theo provider (HF/Gemini không liên quan ComfyUI)
    const backend = { huggingface: "HuggingFace (fal-ai)", gemini: "Gemini API" }[String(params.provider || "").toLowerCase()] || "ComfyUI"
    ctx.emit("info", `Tạo job ${type} ${jobId.slice(0, 8)} — chờ worker ${backend} (timeout ${Math.round(timeoutSec / 60)} phút)`)
    let lastStep = "", lastPrev = 0, lastEmitProg = 0
    for (let i = 0; i < maxPolls; i++) {
      await sleep(2000)
      const { rows } = await query("SELECT status, progress, current_step, output_key, outputs, previews, error FROM jobs WHERE id=$1", [jobId])
      const r = rows[0]
      if (!r) throw new Error("job biến mất")
      // ALD 05/06/2026 - Honor cancel ở tầng RUN: FE Cancel chỉ set workflow_runs.status='cancelled'. Check ~mỗi 6s
      // → cascade xuống bảng jobs (worker.py thấy 'cancelled' sẽ /interrupt ComfyUI nhả GPU) + dừng engine ngay.
      if (i % 3 === 0) {
        const wf = await query("SELECT status FROM workflow_runs WHERE id=$1", [ctx.workflowRunId]).catch(() => ({ rows: [] }))
        if (wf.rows[0]?.status === "cancelled") {
          await query("UPDATE jobs SET status='cancelled', finished_at=now() WHERE id=$1 AND status IN ('queued','running')", [jobId]).catch(() => {})
          throw new Error(`${type} bị hủy (workflow đã cancel)`)
        }
      }
      // ALD 03/06/2026 - preview TỪNG BƯỚC (story-film: chân dung nhân vật chính/phụ + từng cảnh) → emit event
      // có thumbnail (previewUrl) → FE hiện step-by-step trên canvas. Mỗi preview mới = 1 dòng có ảnh.
      const prevs = Array.isArray(r.previews) ? r.previews : []
      if (prevs.length > lastPrev) {
        for (const p of prevs.slice(lastPrev)) {
          try { ctx.emit("info", `🖼 ${p.label || "preview"}`, { previewUrl: await browserUrl(p.key), previewKind: "image", previewLabel: p.label || null }) } catch { /* ignore */ }
        }
        lastPrev = prevs.length
      }
      // ALD 05/06/2026 - emit khi ĐỔI bước HOẶC progress đổi ≥5% (worker bơm % sampling realtime, current_step
      // giữ nguyên) → log chạy mượt. DÙNG abs(): clip 15s/30s chạy NHIỀU animate-window → sang window mới progress
      // RESET về thấp (vd 86%→33%); guard cũ chỉ bắt nhích LÊN nên log đứng im suốt window 2+ ("không thấy log").
      const progNow = r.progress || 0
      if ((r.current_step && r.current_step !== lastStep) || Math.abs(progNow - lastEmitProg) >= 0.05) {
        if (r.current_step) lastStep = r.current_step
        lastEmitProg = progNow
        ctx.emit("info", `${type}: ${lastStep || "đang xử lý"} (${Math.round(progNow * 100)}%)`)
      }
      if (r.status === "done") {
        // ALD 03/06/2026 - đa output (3 preset teaser): outputs[] → videos[]; fallback output_key (single).
        // ALD 08/06/2026 - create-image có thể trả nhiều ảnh → tách thêm images[] nhưng vẫn giữ image
        // đầu tiên để chain sang node sau tương thích như cũ.
        const outs = Array.isArray(r.outputs) && r.outputs.length ? r.outputs : (r.output_key ? [{ key: r.output_key }] : [])
        const videos = [], images = []
        for (const o of outs) {
          if (!o?.key) continue
          const asset = { url: await browserUrl(o.key), label: o.label || null, key: o.key }
          if (String(o.key).toLowerCase().endsWith(".mp4")) videos.push(asset)
          else images.push(asset)
        }
        const primaryKey = outs[0]?.key || r.output_key
        const url = (String(primaryKey || "").toLowerCase().endsWith(".mp4") ? videos[0]?.url : images[0]?.url) || videos[0]?.url || images[0]?.url || ""
        const isVid = String(primaryKey || "").endsWith(".mp4")
        // Tải output đầu → base64 'file' để node SAU (vd motion nhận ảnh tryon) dùng làm input.
        let file = null
        try {
          const { stream, contentType } = await getObjectStream(primaryKey)
          const chunks = []; for await (const ch of stream) chunks.push(ch)
          file = { name: String(primaryKey).split("/").pop(), mimeType: contentType || (isVid ? "video/mp4" : "image/png"), data: Buffer.concat(chunks).toString("base64") }
        } catch (e) { ctx.emit("warn", `Không tải lại output ${type} để chain: ${e?.message || e}`) }
        const count = isVid ? videos.length : images.length
        ctx.emit("success", `${type} xong${count > 1 ? ` (${count} kết quả)` : ""}`)
        return { text: url, file, metadata: { ...(isVid ? { video: url } : { image: url }), url, videos, images } }
      }
      if (r.status === "error") throw new Error(r.error || `${type} lỗi`)
      if (r.status === "cancelled") throw new Error(`${type} bị hủy`)
    }
    await query("UPDATE jobs SET status='cancelled', error=$2, finished_at=now() WHERE id=$1 AND status IN ('queued','running')",
      [jobId, `${type} timeout after ${timeoutSec}s`]).catch(() => {})
    throw new Error(`${type} timeout sau ${Math.round(timeoutSec / 60)} phút`)
  }
}
const mediaStub = (label) => async (_d, _p, ctx) => { ctx.emit("warn", `Node "${label}" chưa hỗ trợ standalone`); throw new Error(`Node "${label}" chưa hỗ trợ`) }

export const NODE_HANDLERS = {
  input: handleInput, inputText: handleInput, inputImage: handleInput, inputFile: handleInput, inputHistory: handleInput,
  // ALD 30/06/2026 - cast-model: tuyển người mẫu từ kho model_refs theo gender/age (Trụ D). Xử lý tại API, không qua worker.
  "cast-model": handleCastModel,
  output: handleOutput, ocr: handleOcr, chat: handleChat, image: handleImage,
  condition: handleCondition, http: handleHttp, debug: handleDebug, transform: handleTransform, validate: handleValidate,
  "gpu-warmup": handleGpuWarmup, "gpu-free": handleGpuFree, workflow: handleNestedWorkflow,
  // Media nodes → delegate job cho worker Python (ComfyUI).
  motion: mediaViaJob("motion"), tryon: mediaViaJob("tryon"),
  "create-image": mediaViaJob("create-image"), "cloudflare-ocr": mediaStub("cloudflare-ocr"),
  // ALD 01/07/2026 - edit-image: SỬA ảnh CÓ SẴN theo mô tả. Nhận LIST ảnh (image1..imageN qua cổng),
  // mỗi ảnh sinh 1-5 version; worker upload + preview TỪNG version (progressive). Qwen-Edit/Gemini.
  "edit-image": mediaViaJob("edit-image"),
  "product-overlay": mediaViaJob("product-overlay"),
  // ALD 03/06/2026 - talk: nhân vật NÓI + nhép miệng (MultiTalk). 1-input ảnh (qua prev → inputs.input), thoại+giọng ở config.
  talk: mediaViaJob("talk"),
  // ALD 06/06/2026 - face-motion: "Người mẫu đọc kịch bản" = talk có HÀNH ĐỘNG theo phong thái. 1-input ảnh người mẫu;
  // kịch bản+phong thái+giọng ở config. Nhánh director trong mediaViaJob sinh motion-prompt từ kịch bản+phong thái.
  "face-motion": mediaViaJob("face-motion"),
  // ALD 14/06/2026 - text-to-video: CHỈ prompt (không ảnh) → video ngắn (Wan2.x T2V). Model chọn ở config (dropdown).
  "text-to-video": mediaViaJob("text-to-video"),
  // ALD 26/06/2026 - wan-i2v: 1 ảnh đầu (+ ảnh cuối tùy chọn) + prompt → Wan I2V/FLF. Đọc wanModel từ UI.
  "wan-i2v": mediaViaJob("wan-i2v"),
  // ALD 20/07/2026 - wan-dancer: "Vũ đạo theo nhạc" (Wan-Dancer-14B, music-to-dance). Ảnh nhân vật + nhạc + style
  // → video nhảy dài. HARD-BLOCK VRAM ≥ 90GB ngay đầu mediaViaJob (chỉ chạy trên VPS thuê GPU lớn, KHÔNG phải .165).
  "wan-dancer": mediaViaJob("wan-dancer"),
  "teen-flycam": mediaViaJob("teen-flycam"),
  "trend-tiktok": mediaViaJob("trend-tiktok"),
  // ALD 22/06/2026 - enhance: upscale video HẬU Wan (ESRGAN ×4 → 1080p/2K/4K) + RIFE fps tùy chọn. 1-input video
  // (prev → inputs.input); targetRes/fpsInterp ở config. Worker comfy_recycle xả GPU/RAM của Wan trước khi upscale.
  enhance: mediaViaJob("enhance"),
  // ALD 14/06/2026 - ss: node "SS" = Ảnh→Video (LTX-2.3 + LoRA custom user tự train, upload qua Settings). 1-input ảnh
  // (prev → inputs.input); prompt + loraName + loraStrength + thời lượng ở config. worker run_ss dựng graph LTX-2.3 I2V.
  ss: mediaViaJob("ss"),
  // ALD 03/06/2026 - concat: ghép ≥2 phân cảnh (clip1, clip2, …) → 1 video, giữ tiếng từng cảnh.
  concat: mediaViaJob("concat"),
  // ALD 08/07/2026 - reveal (Đè lộ): 2 video (cổng base + reveal) cùng người khác đồ → wipe/scan/vortex lộ dần
  // "Đồ mới" lên "Nền". Pure ffmpeg trong worker run_reveal. Config (revealMode/direction/loop/bandPct/…) → params.
  reveal: mediaViaJob("reveal"),
  // ALD 26/06/2026 - voiceover: 1 video + script + voice → TTS rồi thay/trộn audio, giữ nguyên hình.
  voiceover: mediaViaJob("voiceover"),
  // ALD 03/06/2026 - story-film: 1 ô KỊCH BẢN → AI đạo diễn tách cảnh/nhân vật/thoại → phim nhân vật (lip-sync) ghép sẵn.
  "story-film": mediaViaJob("story-film"),
  // ALD 15/06/2026 - subtitle: 1 input VIDEO (qua prev → inputs.input) → worker run_subtitle: ASR (OmniVoice /asr)
  // + dịch (Ollama) → CHÁY phụ đề (hardsub), giữ tiếng gốc. Tiến trình từng câu stream realtime qua run SSE.
  subtitle: mediaViaJob("subtitle"),

  // "Ghép vào mẫu" — tái dùng pipeline create-image (Qwen-Edit đa ảnh): image1=ảnh mẫu (base
  // latent) + image2..N=người; prompt ghép-giữ-mặt do FE auto-build (config.prompt).
  compose: mediaViaJob("create-image"),
  // ALD 11/06/2026 - api-key: node khai báo key (providerType+apiKey), KHÔNG chạy gì — engine pre-scan definition
  // và phân phối key qua ctx.providerKeys khi tạo job. Node 0-in 0-out là orphan (không execute); handler này chỉ
  // là bảo hiểm khi bị nối dây tay vào graph.
  "api-key": async (_d, prev, ctx) => { ctx.emit("info", "Node API Key — không chạy gì (key được phân phối khi tạo job)"); return prev || { text: "" } },
}
// #endregion
