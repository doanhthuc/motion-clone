import express from "express"
import { inspectResources, runResourceActions, restartComfy } from "./admin-resource-actions.js"

const app = express()
const PORT = Number(process.env.PORT || 8099)
const TOKEN = process.env.ADMIN_RESOURCE_HELPER_TOKEN || "changeme-resource-helper-token"
const COMFY_URL = process.env.ADMIN_COMFY_URL || process.env.COMFY_URL || ""

app.use(express.json({ limit: "64kb" }))

function requireToken(req, res, next) {
  if (req.get("X-Resource-Token") !== TOKEN) {
    return res.status(403).json({ error: "Invalid resource helper token" })
  }
  next()
}

app.get("/health", (_req, res) => {
  res.json({ status: "ok", service: "admin-resource-helper" })
})

app.get("/inspect", requireToken, async (_req, res) => {
  try {
    res.json(await inspectResources({ comfyUrl: COMFY_URL }))
  } catch (e) {
    res.status(500).json({ error: String(e?.message || e) })
  }
})

app.post("/free-resources", requireToken, async (req, res) => {
  try {
    const result = await runResourceActions(req.body?.mode || "all", { comfyUrl: COMFY_URL })
    const hardFailures = (result.steps || []).filter((s) => !s.ok && !s.skipped)
    res.status(hardFailures.length ? 207 : 200).json(result)
  } catch (e) {
    res.status(500).json({ error: String(e?.message || e) })
  }
})

// Khởi động lại ComfyUI: helper có pid:host nên kill được process ComfyUI của host → supervisor dựng lại.
app.post("/restart-comfy", requireToken, async (_req, res) => {
  try {
    const result = await restartComfy({ comfyUrl: COMFY_URL })
    res.status(result.success ? 200 : 207).json(result)
  } catch (e) {
    res.status(500).json({ error: String(e?.message || e) })
  }
})

app.listen(PORT, () => {
  console.log(`[admin-resource-helper] listening on :${PORT}`)
})
