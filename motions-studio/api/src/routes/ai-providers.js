// #region ALD 31/05/2026 - AI providers CRUD (thay /functions/v1/ai-providers).
//   GET    /ai-providers              → { items } (api_key masked)
//   PUT    /ai-providers/:provider    { api_key, base_url?, default_model?, is_active? }
//   DELETE /ai-providers/:provider
//   GET    /ai-providers/test/:provider → { ok, model?, reply? } | { ok:false, error }
import { Router } from "express"
import { query } from "../db.js"
import { sessionAuth } from "../auth/session.js"

const router = Router()
const mask = (k) => (!k ? null : k.length <= 8 ? "••••" : `${k.slice(0, 4)}…${k.slice(-4)}`)
const toItem = (r) => ({
  provider: r.provider, api_key: mask(r.api_key), has_key: !!r.api_key,
  base_url: r.base_url, default_model: r.default_model, is_active: r.is_active, updated_at: r.updated_at,
})

router.use("/ai-providers", sessionAuth)

router.get("/ai-providers", async (req, res) => {
  const { rows } = await query("SELECT * FROM ai_provider_keys WHERE user_id=$1 ORDER BY provider", [req.session.userId])
  res.json({ items: rows.map(toItem) })
})

router.put("/ai-providers/:provider", async (req, res) => {
  const provider = String(req.params.provider)
  const b = req.body || {}
  // Nếu api_key rỗng nhưng đã có key cũ → giữ key cũ (FE re-save không nhập lại)
  const cur = (await query("SELECT api_key FROM ai_provider_keys WHERE user_id=$1 AND provider=$2", [req.session.userId, provider])).rows[0]
  const apiKey = (b.api_key === "" || b.api_key == null) ? (cur?.api_key ?? null) : b.api_key
  const { rows } = await query(
    `INSERT INTO ai_provider_keys (user_id, provider, api_key, base_url, default_model, is_active, updated_at)
     VALUES ($1,$2,$3,$4,$5,$6, now())
     ON CONFLICT (user_id, provider) DO UPDATE SET
       api_key=$3, base_url=$4, default_model=$5, is_active=$6, updated_at=now() RETURNING *`,
    [req.session.userId, provider, apiKey, b.base_url || null, b.default_model || null, b.is_active !== false])
  res.json({ item: toItem(rows[0]) })
})

router.delete("/ai-providers/:provider", async (req, res) => {
  await query("DELETE FROM ai_provider_keys WHERE user_id=$1 AND provider=$2", [req.session.userId, req.params.provider])
  res.json({ success: true })
})

router.get("/ai-providers/test/:provider", async (req, res) => {
  const provider = String(req.params.provider)
  const { rows } = await query("SELECT api_key, base_url, default_model FROM ai_provider_keys WHERE user_id=$1 AND provider=$2",
    [req.session.userId, provider])
  const cfg = rows[0]
  if (!cfg) return res.json({ ok: false, error: "Chưa cấu hình provider" })
  try {
    if (provider === "ollama") {
      const url = (cfg.base_url || process.env.OLLAMA_URL || "http://172.17.0.1:11434").replace(/\/$/, "") + "/api/tags"
      const r = await fetch(url, { signal: AbortSignal.timeout(8000) })
      if (!r.ok) return res.json({ ok: false, error: `Ollama ${r.status}` })
      const d = await r.json()
      return res.json({ ok: true, model: cfg.default_model, reply: `${(d.models || []).length} models` })
    }
    // custom/cloud OpenAI-compatible: GET /models
    const base = (cfg.base_url || "https://api.openai.com/v1").replace(/\/$/, "")
    const r = await fetch(`${base}/models`, {
      headers: cfg.api_key ? { Authorization: `Bearer ${cfg.api_key}` } : {}, signal: AbortSignal.timeout(8000) })
    if (!r.ok) return res.json({ ok: false, error: `HTTP ${r.status}` })
    return res.json({ ok: true, model: cfg.default_model, reply: "OK" })
  } catch (e) {
    return res.json({ ok: false, error: String(e?.message || e) })
  }
})

// ALD 02/06/2026 - Liệt kê model của provider (cho FE picker, vd node Teaser chọn model AI dàn cảnh).
//   ollama → /api/tags ; custom/openai-compat → GET /models. Lỗi → { models: [], error }.
router.get("/ai-providers/:provider/models", async (req, res) => {
  const provider = String(req.params.provider)
  const { rows } = await query("SELECT api_key, base_url FROM ai_provider_keys WHERE user_id=$1 AND provider=$2",
    [req.session.userId, provider])
  const cfg = rows[0] || {}
  try {
    if (provider === "ollama") {
      // base_url có thể là 127.0.0.1 (góc nhìn wf-worker host-net) → api ở bridge KHÔNG tới được.
      // Nếu là localhost VÀ api có OLLAMA_URL (host.docker.internal) → ưu tiên env. Giữ base_url remote khác.
      let base = cfg.base_url || process.env.OLLAMA_URL || "http://host.docker.internal:11434"
      if (/\/\/(127\.0\.0\.1|localhost)(:|\/|$)/.test(base) && process.env.OLLAMA_URL) base = process.env.OLLAMA_URL
      try {
        const r = await fetch(base.replace(/\/$/, "") + "/api/tags", { signal: AbortSignal.timeout(6000) })
        if (r.ok) {
          const d = await r.json()
          const models = [...new Set((d.models || []).map((m) => m.name || m.model).filter(Boolean))].sort()
          if (models.length) return res.json({ models })
        }
      } catch { /* api ở bridge thường bị firewall chặn tới host Ollama → rơi xuống fallback */ }
      // Fallback: api không tới được Ollama (firewall bridge→host) → trả list dự phòng (env override)
      // để picker vẫn dùng được. Khi mở firewall / OLLAMA_URL tới được thì tự dùng list live ở trên.
      const fb = (process.env.OLLAMA_FALLBACK_MODELS || "qwen3.6:35b,qwen2.5:14b,deepseek-r1:14b,qwen2.5:7b-instruct,qwen2.5vl:7b")
        .split(",").map((s) => s.trim()).filter(Boolean)
      return res.json({ models: fb, fallback: true })
    }
    const base = (cfg.base_url || "https://api.openai.com/v1").replace(/\/$/, "")
    const r = await fetch(`${base}/models`, {
      headers: cfg.api_key ? { Authorization: `Bearer ${cfg.api_key}` } : {}, signal: AbortSignal.timeout(8000) })
    if (!r.ok) return res.json({ models: [], error: `HTTP ${r.status}` })
    const d = await r.json()
    const models = [...new Set((d.data || d.models || []).map((m) => m.id || m.name).filter(Boolean))].sort()
    return res.json({ models })
  } catch (e) {
    return res.json({ models: [], error: String(e?.message || e) })
  }
})

export default router
// #endregion
