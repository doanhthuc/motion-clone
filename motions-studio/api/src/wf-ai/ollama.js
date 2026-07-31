// #region ALD 09/07/2026 - wf-ai/ollama.js: lớp LLM (Ollama) dùng chung cho AI đạo diễn.
// Tách từ routes/workflow-ai.js cũ (viết lại toàn bộ theo kiến trúc PROFILE → STORYBOARD → COMPILER).
// Giữ nguyên hành vi đã kiểm chứng: check vision-capability (chống model mù "bịa sản phẩm" — sự cố thật 04/07),
// unload model khỏi VRAM sau khi dùng (nhường GPU cho Wan), fallback model khi model chính chưa pull (404).
export const OLLAMA_URL = (process.env.OLLAMA_URL || "http://172.17.0.1:11434").replace(/\/$/, "")
export const DEFAULT_MODEL = (process.env.WORKFLOW_AI_MODEL || "qwen3.6:35b").trim()
export const FALLBACK_MODEL = (process.env.WORKFLOW_AI_FALLBACK_MODEL || "qwen3:30b-a3b").trim()
// Vision mặc định = model đạo diễn (qwen3.6:35b multimodal); env WORKFLOW_AI_VISION_MODEL tách model VL riêng (vd qwen2.5vl:7b).
export const VISION_MODEL = (process.env.WORKFLOW_AI_VISION_MODEL || DEFAULT_MODEL).trim()
export const AI_NUM_PREDICT = Math.max(2048, Number(process.env.WORKFLOW_AI_NUM_PREDICT) || 4096)
const AI_TIMEOUT_MS = Math.max(30000, Number(process.env.WORKFLOW_AI_TIMEOUT_MS) || 180000)

export async function ollamaChat({ model, messages, tools, format, think }) {
  const body = { model, messages, stream: false, think: think !== false, keep_alive: "30s", options: { temperature: 0.3, num_ctx: 16384, num_predict: AI_NUM_PREDICT } }
  if (tools) body.tools = tools
  if (format) body.format = format
  const res = await fetch(`${OLLAMA_URL}/api/chat`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: AbortSignal.timeout(AI_TIMEOUT_MS) })
  if (!res.ok) {
    const txt = (await res.text()).slice(0, 300)
    const err = new Error(`Ollama ${res.status}: ${txt}`)
    err.ollamaStatus = res.status
    err.modelMissing = res.status === 404 || /not found|no such model|pull/i.test(txt)
    throw err
  }
  return await res.json()
}

// Model có "vision" không? Ollama gặp model KHÔNG hỗ trợ ảnh sẽ LẶNG LẼ BỎ ẢNH → model bịa sản phẩm
// (sự cố thật 04/07: giày → phim nước hoa). PHẢI check capability trước khi gửi ảnh.
export async function ollamaHasVision(model, log) {
  try {
    const r = await fetch(`${OLLAMA_URL}/api/show`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model }), signal: AbortSignal.timeout(10000),
    })
    if (r.status === 404) { const e = new Error(`model ${model} chưa pull`); e.modelMissing = true; throw e }
    if (!r.ok) throw new Error(`ollama show ${r.status}`)
    const body = await r.json()
    if (Array.isArray(body.capabilities)) return body.capabilities.includes("vision")
    // Ollama cũ không trả capabilities → đoán theo tên; không chắc coi như KHÔNG (thà bỏ còn hơn để model mù bịa).
    return /(^|[^a-z])(vl|vision|llava|moondream|minicpm-v|gemma3|omni)([^a-z]|$)/i.test(String(model))
  } catch (e) {
    if (e?.modelMissing) throw e
    log?.push?.(`vision: không kiểm tra được capability (${e?.message || e}) → coi như KHÔNG hỗ trợ ảnh`)
    return false
  }
}

// "ollama stop": nhả model khỏi VRAM (keep_alive:0) + poll /api/ps tới khi sạch (nhường GPU cho Wan/ComfyUI).
export async function ollamaUnload(model) {
  const names = new Set()
  if (model) names.add(model)
  try {
    const ps = await fetch(`${OLLAMA_URL}/api/ps`, { signal: AbortSignal.timeout(5000) })
    if (ps.ok) {
      const body = await ps.json()
      for (const m of Array.isArray(body.models) ? body.models : []) {
        const name = m.name || m.model
        if (name) names.add(name)
      }
    }
  } catch { /* best-effort */ }
  for (const name of names) {
    try {
      await fetch(`${OLLAMA_URL}/api/generate`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: name, prompt: "", stream: false, keep_alive: 0 }),
        signal: AbortSignal.timeout(15000),
      })
    } catch { /* best-effort per model */ }
  }
  const deadline = Date.now() + 15000
  while (Date.now() < deadline) {
    try {
      const ps = await fetch(`${OLLAMA_URL}/api/ps`, { signal: AbortSignal.timeout(3000) })
      if (!ps.ok) break
      const body = await ps.json()
      const loaded = (Array.isArray(body.models) ? body.models : []).map((m) => m.name || m.model).filter(Boolean)
      if (!loaded.some((name) => names.has(name))) break
    } catch { break }
    await new Promise((r) => setTimeout(r, 700))
  }
}

// Gom 1 chỗ việc "bóc JSON từ output LLM" (file cũ lặp 5 nơi): strip <think>/code-fence → match {...} → parse.
export function jsonFromLLM(text) {
  const t = String(text || "").replace(/<think>[\s\S]*?<\/think>/gi, "").replace(/```json|```/gi, "").trim()
  const m = t.match(/\{[\s\S]*\}/)
  if (!m) return null
  try { return JSON.parse(m[0]) } catch { return null }
}

// Wrapper "gọi LLM lấy JSON": retry 1 lần khi parse fail (nudge "CHỈ trả JSON"), tự fallback FALLBACK_MODEL
// khi model chính chưa pull. Trả { obj, model } (model = model THẬT đã dùng, để log/unload đúng).
export async function chatJson({ model = DEFAULT_MODEL, sys, user, think = true, images, log }) {
  let useModel = model
  const mkMessages = (nudge) => [
    { role: "system", content: nudge ? `${sys}\n\nOutput trước KHÔNG phải JSON hợp lệ. CHỈ trả JSON đúng schema, không markdown, không giải thích.` : sys },
    { role: "user", content: String(user || ""), ...(images?.length ? { images } : {}) },
  ]
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const r = await ollamaChat({ model: useModel, messages: mkMessages(attempt === 1), format: "json", think })
      const obj = jsonFromLLM(r.message?.content)
      if (obj) return { obj, model: useModel }
      if (attempt === 0) { log?.push?.(`LLM trả JSON xấu → retry (${useModel})`); continue }
      return { obj: null, model: useModel }
    } catch (e) {
      if (e?.modelMissing && useModel !== FALLBACK_MODEL) {
        log?.push?.(`model "${useModel}" chưa pull → fallback ${FALLBACK_MODEL}`)
        useModel = FALLBACK_MODEL
        continue
      }
      throw e
    }
  }
  return { obj: null, model: useModel }
}
// #endregion
