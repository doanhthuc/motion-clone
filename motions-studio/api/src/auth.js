// Auth đơn giản bằng API key (header). Client: X-API-Key = API_KEY. Worker: X-Worker-Token = WORKER_TOKEN.
const API_KEY = process.env.API_KEY || ""
const WORKER_TOKEN = process.env.WORKER_TOKEN || ""

export function clientAuth(req, res, next) {
  const k = req.get("x-api-key") || (req.get("authorization") || "").replace(/^Bearer\s+/i, "")
  if (!API_KEY || k !== API_KEY) return res.status(401).json({ error: "Sai hoặc thiếu X-API-Key" })
  next()
}

export function workerAuth(req, res, next) {
  const k = req.get("x-worker-token")
  if (!WORKER_TOKEN || k !== WORKER_TOKEN) return res.status(401).json({ error: "Sai hoặc thiếu X-Worker-Token" })
  next()
}
